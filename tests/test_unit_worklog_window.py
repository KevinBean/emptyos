"""Unit: worklog reporting window (?days= trailing vs ?from=&to= range).

The trailing window is capped at 92 days, which put 927 of this vault's 934
recorded hours out of reach of the timesheet PDF — an employment record is
asked for as "FY2024", a shape a trailing window cannot express. These pin the
range semantics and the guards that keep an uncapped range safe.
"""
import importlib.util
import pathlib
from datetime import date, timedelta

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "worklog_app_mod",
    pathlib.Path(__file__).resolve().parent.parent
    / "apps/public/standard/worklog/app.py",
)


class _Req:
    def __init__(self, **params):
        self.query_params = params


@pytest.fixture(scope="module")
def win():
    """Bind the unbound _window off the class — no kernel, no app instance."""
    import sys
    import types
    pkg = types.ModuleType("eos_worklog_pkg")
    pkg.__path__ = [str(pathlib.Path(_SPEC.origin).parent)]
    sys.modules.setdefault("eos_worklog_pkg", pkg)
    mod = importlib.util.module_from_spec(_SPEC)
    mod.__package__ = "eos_worklog_pkg"
    sys.modules["eos_worklog_pkg.app"] = mod
    _SPEC.loader.exec_module(mod)
    return mod.WorklogApp._window


def test_default_is_a_seven_day_trailing_window(win):
    got = win(None, _Req())
    assert got["days"] == 7
    assert got["end"] == date.today().isoformat()
    assert got["start"] == (date.today() - timedelta(days=7)).isoformat()
    assert "last 7 days" in got["label"]


@pytest.mark.parametrize("raw,expected", [("1", 1), ("92", 92), ("500", 92), ("0", 1), ("-5", 1)])
def test_trailing_window_is_clamped(win, raw, expected):
    assert win(None, _Req(days=raw))["days"] == expected


def test_garbage_days_falls_back_to_default(win):
    assert win(None, _Req(days="lots"))["days"] == 7


def test_explicit_range_is_uncapped_and_inclusive(win):
    got = win(None, _Req(**{"from": "2024-01-01", "to": "2024-12-31"}))
    assert got["start"] == "2024-01-01" and got["end"] == "2024-12-31"
    assert got["days"] == 366, "2024 is a leap year; both bounds inclusive"
    assert got["label"] == "2024-01-01 → 2024-12-31"


def test_open_ended_range_sides(win):
    assert win(None, _Req(**{"from": "2023-05-01"}))["end"] == date.today().isoformat()
    assert win(None, _Req(to="2023-05-01"))["start"] == "1970-01-01"


def test_reversed_range_is_swapped_not_emptied(win):
    got = win(None, _Req(**{"from": "2024-12-31", "to": "2024-01-01"}))
    assert (got["start"], got["end"]) == ("2024-01-01", "2024-12-31")


@pytest.mark.parametrize("bad", ["not-a-date", "2024-13-01", "2024/01/01", "2024-02-30"])
def test_bad_dates_error_rather_than_silently_widening(win, bad):
    """A malformed bound must not fall through to the trailing-window default —
    that would quietly export a different period than the one asked for."""
    assert "error" in win(None, _Req(**{"from": bad}))


def test_compact_iso_basic_form_is_accepted(win):
    """`date.fromisoformat` takes the ISO 8601 basic form on 3.11+, so
    `?from=20240101` is a correct parse rather than a bad date. Pinned because
    it looks like a typo and a future 'tighten the parser' change would break a
    working URL."""
    assert win(None, _Req(**{"from": "20240101"}))["start"] == "2024-01-01"


def test_range_wins_over_days_when_both_given(win):
    got = win(None, _Req(days="7", **{"from": "2024-01-01", "to": "2024-01-31"}))
    assert got["start"] == "2024-01-01" and got["days"] == 31


def test_employer_passes_through_both_forms(win):
    assert win(None, _Req(employer="Acme"))["employer"] == "Acme"
    assert win(None, _Req(employer="Acme", **{"from": "2024-01-01"}))["employer"] == "Acme"


def test_rollup_cap_is_a_real_ceiling(win):
    """An uncapped range must not push the whole corpus into one prompt.

    Read from `reporting`, its only consumer: constants travel with the code
    that uses them (.claude/rules/multi-module-apps.md, convention 5).
    """
    import sys
    mod = sys.modules["eos_worklog_pkg.reporting"]
    assert isinstance(mod.ROLLUP_MAX_ITEMS, int)
    assert 0 < mod.ROLLUP_MAX_ITEMS <= 1000
