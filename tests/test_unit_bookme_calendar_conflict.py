"""BookMe must not offer a slot the owner is already committed to.

BookMe contributes its confirmed bookings *to* the calendar agenda but, until
2026-08-05, never read back *from* it — so `_slots_for` could only ever see
conflicts bookme itself had created. A published booking page would happily
offer 14:00 Tuesday while the owner sat in a subscribed-calendar meeting.

Two halves are pinned here:

* `calendar/ics.py` learning to parse an event's **end** (DTEND / DURATION).
  The calendar app had no notion of when anything finished, so there was no
  interval to check against — only a start time.
* `bookme._calendar_busy` / `_slots_for` consuming those intervals, and
  degrading to the old behaviour when calendar is absent or answers badly.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BOOKME = _ROOT / "apps/public/standard/bookme/app.py"
_CAL_DIR = _ROOT / "apps/public/standard/calendar"


def _load_standalone(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        pytest.skip(f"{path.name} not loadable", allow_module_level=True)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_calendar():
    """Register the parent packages so `from . import ics` resolves."""
    for pkg_name, pkg_path in (
        ("cal_under_test", _CAL_DIR),
    ):
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(pkg_path)]
        sys.modules[pkg_name] = pkg
    ics = _load_standalone(_CAL_DIR / "ics.py", "cal_under_test.ics")
    sys.modules["cal_under_test.ics"] = ics
    app = _load_standalone(_CAL_DIR / "app.py", "cal_under_test.app")
    return app, ics


bookme = _load_standalone(_BOOKME, "bookme_conflict_under_test")
cal_app, cal_ics = _load_calendar()


# ── ICS end-time parsing ────────────────────────────────────────────────


def _vevent(**fields: str) -> str:
    body = "\r\n".join(f"{k}:{v}" for k, v in fields.items())
    return f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n{body}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


class TestParseDuration:
    @pytest.mark.parametrize(
        "raw,minutes",
        [
            ("PT1H", 60),
            ("PT30M", 30),
            ("PT1H30M", 90),
            ("P1D", 1440),
            ("P1W", 10080),
            ("PT90S", 1),
            ("", 0),
            ("garbage", 0),
            ("1H30M", 0),  # missing the leading P
        ],
    )
    def test_parses(self, raw, minutes):
        # The parser moved to emptyos/sdk/ics_parse.py when countdown became its
        # second consumer (37c28680); calendar/ics.py now imports only parse_ics.
        from emptyos.sdk.ics_parse import _parse_duration

        assert _parse_duration(raw) == minutes


class TestEventEnd:
    def test_dtend_same_day(self):
        events = cal_ics.parse_ics(
            _vevent(DTSTART="20260810T140000", DTEND="20260810T153000", SUMMARY="Design review")
        )
        assert events[0]["time"] == "14:00"
        assert events[0]["end"] == "15:30"

    def test_duration_when_no_dtend(self):
        events = cal_ics.parse_ics(
            _vevent(DTSTART="20260810T140000", DURATION="PT45M", SUMMARY="Standup")
        )
        assert events[0]["end"] == "14:45"

    def test_all_day_has_no_end(self):
        events = cal_ics.parse_ics(_vevent(DTSTART="20260810", SUMMARY="Public holiday"))
        assert events[0]["time"] == ""
        assert events[0]["end"] == ""

    def test_unknown_end_is_empty_not_guessed(self):
        events = cal_ics.parse_ics(_vevent(DTSTART="20260810T140000", SUMMARY="Mystery"))
        assert events[0]["end"] == ""

    def test_crossing_midnight_clamps(self):
        events = cal_ics.parse_ics(
            _vevent(DTSTART="20260810T230000", DTEND="20260811T010000", SUMMARY="Long call")
        )
        assert events[0]["end"] == "23:59"

    def test_end_before_start_is_rejected(self):
        events = cal_ics.parse_ics(
            _vevent(DTSTART="20260810T140000", DTEND="20260810T130000", SUMMARY="Broken")
        )
        assert events[0]["end"] == ""


# ── calendar.busy_intervals ─────────────────────────────────────────────


class _CalStub:
    """Minimal stand-in — busy_intervals only reaches _show + the ICS cache."""

    def __init__(self, events, *, show=True):
        self._events = events
        self._show_ics = show
        self.logged = []

    def _show(self, key):
        return self._show_ics

    def _cached_ics_events(self, start, end):
        return [e for e in self._events if start.isoformat() <= e["date"] < end.isoformat()]

    def log(self, message, level="info", data=None, job_id=""):
        self.logged.append({"message": message, "level": level, "data": data or {}})


def _busy(stub, date="2026-08-10", default_min=0):
    fn = cal_app.CalendarApp.busy_intervals
    return asyncio.run(fn(stub, date=date, default_min=default_min))


class TestBusyIntervals:
    def test_returns_timed_events_with_ends(self):
        rows = _busy(_CalStub([
            {"date": "2026-08-10", "time": "14:00", "end": "15:30", "title": "Review", "source": "Work"},
        ]))
        assert rows == [
            {"start": "14:00", "end": "15:30", "title": "Review", "source": "Work"}
        ]

    def test_all_day_events_block_nothing(self):
        rows = _busy(_CalStub([
            {"date": "2026-08-10", "time": "", "end": "", "title": "Holiday", "source": "Work"},
        ]))
        assert rows == []

    def test_unknown_end_skipped_by_default(self):
        """0 = skip rather than fabricate a block."""
        rows = _busy(_CalStub([
            {"date": "2026-08-10", "time": "14:00", "end": "", "title": "Mystery", "source": "Work"},
        ]))
        assert rows == []

    def test_skipping_is_never_silent(self):
        """A scheduling surface that quietly ignores commitments looks exactly
        like one with nothing to ignore — which is how the original
        double-booking bug stayed invisible."""
        stub = _CalStub([
            {"date": "2026-08-10", "time": "14:00", "end": "", "title": "A", "source": "W"},
            {"date": "2026-08-10", "time": "16:00", "end": "", "title": "B", "source": "W"},
        ])
        _busy(stub)
        assert len(stub.logged) == 1
        assert stub.logged[0]["level"] == "warning"
        assert stub.logged[0]["data"] == {"date": "2026-08-10", "skipped_no_end": 2}

    def test_no_warning_when_nothing_was_skipped(self):
        stub = _CalStub([
            {"date": "2026-08-10", "time": "14:00", "end": "15:00", "title": "A", "source": "W"},
        ])
        _busy(stub)
        assert stub.logged == []

    def test_no_warning_when_the_default_covers_them(self):
        stub = _CalStub([
            {"date": "2026-08-10", "time": "14:00", "end": "", "title": "A", "source": "W"},
        ])
        _busy(stub, default_min=60)
        assert stub.logged == []

    def test_unknown_end_uses_default_when_configured(self):
        rows = _busy(_CalStub([
            {"date": "2026-08-10", "time": "14:00", "end": "", "title": "Mystery", "source": "Work"},
        ]), default_min=60)
        assert rows[0]["end"] == "15:00"

    def test_disabled_ics_source_returns_nothing(self):
        rows = _busy(_CalStub([
            {"date": "2026-08-10", "time": "14:00", "end": "15:00", "title": "X", "source": "Work"},
        ], show=False))
        assert rows == []

    def test_bad_date_returns_nothing(self):
        assert _busy(_CalStub([]), date="not-a-date") == []

    def test_sorted_by_start(self):
        rows = _busy(_CalStub([
            {"date": "2026-08-10", "time": "16:00", "end": "16:30", "title": "B", "source": "W"},
            {"date": "2026-08-10", "time": "09:00", "end": "09:30", "title": "A", "source": "W"},
        ]))
        assert [r["start"] for r in rows] == ["09:00", "16:00"]


# ── bookme slot computation ─────────────────────────────────────────────

_CFG = {
    "owner_name": "Owner",
    "timezone": "UTC",
    "availability": {"mon": [["09:00", "12:00"]], "tue": [], "wed": [], "thu": [],
                     "fri": [], "sat": [], "sun": []},
    "external_busy_default_min": 0,
}
_ET = {"id": "intro-30", "name": "Intro", "duration_min": 30, "buffer_min": 0, "active": True}
_MONDAY = "2026-08-10"  # a Monday, comfortably in the future relative to _NOW


class _BookmeStub:
    """Real `_slots_for` / `_calendar_busy`, stubbed I/O around them."""

    def __init__(self, calendar_rows=None, calendar_error=None, own_bookings=None):
        self._calendar_rows = calendar_rows
        self._calendar_error = calendar_error
        self._own = own_bookings or []
        self.calls = []

    # stubbed collaborators
    def _now_local(self, cfg):
        return _dt.datetime(2026, 8, 1, 0, 0)

    def _taken_intervals(self, date_str):
        return self._own

    async def try_call_app(self, app_id, method, /, **kwargs):
        self.calls.append((app_id, method, kwargs))
        if self._calendar_error is not None:
            return None, self._calendar_error
        return self._calendar_rows, None

    # methods under test
    _calendar_busy = bookme.BookMeApp._calendar_busy
    _slots_for = bookme.BookMeApp._slots_for


def _slots(stub, cfg=None):
    return asyncio.run(stub._slots_for(cfg or _CFG, _ET, _MONDAY))


class TestSlotsAgainstCalendar:
    def test_baseline_full_morning(self):
        assert _slots(_BookmeStub(calendar_rows=[])) == [
            "09:00", "09:30", "10:00", "10:30", "11:00", "11:30"
        ]

    def test_calendar_commitment_removes_its_slots(self):
        """The regression this whole file exists for."""
        stub = _BookmeStub(calendar_rows=[
            {"start": "10:00", "end": "11:00", "title": "Existing meeting", "source": "Work"},
        ])
        slots = _slots(stub)
        assert "10:00" not in slots and "10:30" not in slots
        assert slots == ["09:00", "09:30", "11:00", "11:30"]

    def test_partial_overlap_still_blocks(self):
        stub = _BookmeStub(calendar_rows=[
            {"start": "10:15", "end": "10:45", "title": "Short call", "source": "Work"},
        ])
        assert "10:00" not in _slots(stub)
        assert "10:30" not in _slots(stub)

    def test_own_bookings_and_calendar_compose(self):
        stub = _BookmeStub(
            calendar_rows=[{"start": "11:00", "end": "11:30", "title": "Cal", "source": "W"}],
            own_bookings=[(
                _dt.datetime(2026, 8, 10, 9, 0), _dt.datetime(2026, 8, 10, 9, 30),
            )],
        )
        assert _slots(stub) == ["09:30", "10:00", "10:30", "11:30"]

    def test_buffer_applies_to_calendar_intervals(self):
        cfg = dict(_CFG)
        et = dict(_ET, buffer_min=15)
        stub = _BookmeStub(calendar_rows=[
            {"start": "10:00", "end": "10:30", "title": "Cal", "source": "W"},
        ])
        slots = asyncio.run(stub._slots_for(cfg, et, _MONDAY))
        # 09:30-10:00 now abuts the buffered 09:45-10:45 window.
        assert "09:30" not in slots and "10:30" not in slots

    def test_it_asks_calendar_for_the_right_date(self):
        stub = _BookmeStub(calendar_rows=[])
        _slots(stub)
        assert stub.calls == [("calendar", "busy_intervals",
                              {"date": _MONDAY, "default_min": 0})]

    def test_default_min_threaded_from_config(self):
        stub = _BookmeStub(calendar_rows=[])
        asyncio.run(stub._slots_for(dict(_CFG, external_busy_default_min=45), _ET, _MONDAY))
        assert stub.calls[0][2]["default_min"] == 45


class TestDegradesWhenCalendarUnavailable:
    """calendar is `optional_apps` — absence must be the old behaviour, not a 500."""

    def test_missing_app_leaves_slots_intact(self):
        stub = _BookmeStub(calendar_error="app 'calendar' not found")
        assert _slots(stub) == ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"]

    def test_none_response_is_tolerated(self):
        assert _slots(_BookmeStub(calendar_rows=None)) == [
            "09:00", "09:30", "10:00", "10:30", "11:00", "11:30"
        ]

    @pytest.mark.parametrize("row", [
        {"start": "bogus", "end": "11:00"},
        {"start": "10:00"},
        {},
        {"start": None, "end": None},
        "not-a-dict",
    ])
    def test_malformed_rows_are_skipped_not_fatal(self, row):
        stub = _BookmeStub(calendar_rows=[row])
        assert _slots(stub) == ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"]

    def test_one_bad_row_does_not_drop_a_good_one(self):
        stub = _BookmeStub(calendar_rows=[
            {"start": "bogus", "end": "x"},
            {"start": "10:00", "end": "10:30", "title": "Real", "source": "W"},
        ])
        assert "10:00" not in _slots(stub)
