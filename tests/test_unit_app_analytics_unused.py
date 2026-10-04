"""app-analytics must not query the usage table once per app on the event loop.

Pins the 2026-09-06 21:22 wedge (evidence `20260906T112242Z`): `api_unused`
called `usage.range(where={"app": id})` for every unused app — 233 sync SQLite
calls on the loop. In isolation they cost ~0.4 s; each one yields the GIL,
and with fifteen CPU-bound audit threads from another app running that became a
37 s wedge and a watchdog kill. The lever this app owns is the number of
round-trips: one grouped statement per question instead of one per app.
The same page (`pages/index.html`, one `Promise.all`) also called
`/api/streaks` (233 `range()` scans) and `/api/errors-vs-usage` (466
`total()` calls); all three are covered.

Claims, each red-provable on its own:

1. `last_bucket` / `buckets_by` / `sums_by` return the right answer, honour
   `where`, refuse an unknown field, and each is ONE statement that
   AGGREGATES IN SQL — pinned by the traced SQL containing `GROUP BY` (and
   `MAX(` for last_bucket) AND by the row count the cursor returns (one row
   per key, not one per table row: a single un-grouped `SELECT *` folded in
   Python passed a plain statement count).
2. `api_unused`, `_streaks_data` and `api_errors_vs_usage` each call their
   grouped helper exactly once, with `where` as intended, and never call
   `range()` or `total()` — asserted on the recorder's FULL call list, so a
   hidden extra query cannot slip past a count (`.claude/rules/audits.md`
   § Failure mode 3).
3. The result shapes and orders are the pre-existing ones.

Daemon-free: in-memory SQLite for the counter; the app is built with
`object.__new__` and stubs for `usage`, `kernel.apps.manifests`,
`_all_app_ids` and `_errors_by_app`.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from helpers import load_app_module

from emptyos.sdk.time_series import TimeSeriesCounter, days_ago_utc, today_utc


# ── 1. the three grouped helpers ──────────────────────────────────────


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)


@pytest.fixture
def counter():
    conn = sqlite3.connect(":memory:")
    c = TimeSeriesCounter(conn, "usage", dims=["app", "kind"])
    c.bump({"app": "task", "kind": "view"}, at=_d("2026-08-01"))
    c.bump({"app": "task", "kind": "view"}, at=_d("2026-09-03"), by=3)
    c.bump({"app": "task", "kind": "api"}, at=_d("2026-08-20"))
    c.bump({"app": "task", "kind": "api"}, at=_d("2026-09-03"))  # same day as the view: GROUP BY app,bucket must fold it
    c.bump({"app": "kb", "kind": "view"}, at=_d("2026-07-15"))
    c.bump({"app": "journal", "kind": "api"}, at=_d("2026-09-05"), by=2)
    yield c, conn
    conn.close()


class _Traced:
    """Capture every statement AND the number of rows each cursor yields.

    `sqlite3.Connection.execute` is read-only, so the counter's `conn` is
    swapped for a delegating proxy that counts fetched rows.
    """

    def __init__(self, counter: TimeSeriesCounter):
        self.sql: list[str] = []
        self.rows = 0
        real_conn = counter.conn
        real_conn.set_trace_callback(self.sql.append)
        outer = self

        class _Proxy:
            def execute(self_, sql, *a):
                fetched = real_conn.execute(sql, *a).fetchall()
                outer.rows += len(fetched)
                return fetched  # iterable, like the cursor the code loops over

            def __getattr__(self_, name):
                return getattr(real_conn, name)

        counter.conn = _Proxy()  # type: ignore[assignment]

    def selects(self):
        return [s for s in self.sql if s.lstrip().upper().startswith(("SELECT", "WITH"))]


def test_last_bucket_newest_per_key_in_one_grouped_statement(counter):
    c, _ = counter
    t = _Traced(c)
    out = c.last_bucket("app")
    assert out == {"task": "2026-09-03", "kb": "2026-07-15", "journal": "2026-09-05"}
    sel = t.selects()
    assert len(sel) == 1 and "GROUP BY" in sel[0].upper() and "MAX(" in sel[0].upper(), sel
    assert t.rows == 3, "one row per key — folding table rows in Python is the shape this refuses"


def test_last_bucket_honours_where_and_refuses_unknown_field(counter):
    c, _ = counter
    assert c.last_bucket("app", where={"kind": "api"}) == {"task": "2026-09-03", "journal": "2026-09-05"}
    with pytest.raises(ValueError):
        c.last_bucket("bucket")


def test_buckets_by_is_one_grouped_statement_with_ascending_buckets(counter):
    c, _ = counter
    t = _Traced(c)
    out = c.buckets_by("app")
    assert out == {"task": ["2026-08-01", "2026-08-20", "2026-09-03"], "kb": ["2026-07-15"], "journal": ["2026-09-05"]}
    sel = t.selects()
    assert len(sel) == 1 and "GROUP BY" in sel[0].upper(), sel
    assert t.rows == 5, "one row per (key, bucket) — GROUP BY app, bucket, kind would return 6"
    assert c.buckets_by("app", start="2026-08-15") == {"task": ["2026-08-20", "2026-09-03"], "journal": ["2026-09-05"]}
    assert c.buckets_by("app", end="2026-08-31") == {"task": ["2026-08-01", "2026-08-20"], "kb": ["2026-07-15"]}
    with pytest.raises(ValueError):
        c.buckets_by("nope")


def test_sums_by_is_one_grouped_statement(counter):
    c, _ = counter
    t = _Traced(c)
    out = c.sums_by("app", where={"kind": "view"})
    assert out == {"task": 4, "kb": 1}
    sel = t.selects()
    assert len(sel) == 1 and "GROUP BY" in sel[0].upper() and "SUM(" in sel[0].upper(), sel
    assert t.rows == 2
    assert c.sums_by("app", start="2026-09-01") == {"task": 4, "journal": 2}
    assert c.sums_by("app", end="2026-08-31") == {"task": 2, "kb": 1}
    with pytest.raises(ValueError):
        c.sums_by("nope")


# ── 2 + 3. the call sites ─────────────────────────────────────────────


def _load_app_class():
    # The app does `from .productivity import …` / `from .vault_mixin import …`,
    # so it is loaded as a package module the way its sibling unit test does.
    return load_app_module("app-analytics", "app", preload=("vault_mixin",)).AppAnalyticsApp


class _UsageRecorder:
    """Stands in for TimeSeriesCounter; records EVERY query method call with
    its arguments — including the time window, so dropping `start=`/`end=`
    (a 30-day view silently becoming all-time) is recorded and asserted against. `range` and `total` exist so a regression to a per-app loop
    is recorded and asserted against, not hidden behind an AttributeError."""

    def __init__(self, *, last=None, active=(), buckets=None, views=None, events=None):
        self.calls: list[tuple] = []
        self._last = dict(last or {})
        self._active = list(active)
        self._buckets = dict(buckets or {})
        self._views = dict(views or {})
        self._events = dict(events or {})

    def top(self, field, start=None, end=None, where=None, limit=10):
        self.calls.append(("top", field, start, end, where))
        return [{"key": k, "count": 1} for k in self._active]

    def last_bucket(self, field, where=None):
        self.calls.append(("last_bucket", field, None, None, where))
        return dict(self._last)

    def buckets_by(self, field, start=None, end=None, where=None):
        self.calls.append(("buckets_by", field, start, end, where))
        return {k: list(v) for k, v in self._buckets.items()}

    def sums_by(self, field, start=None, end=None, where=None):
        self.calls.append(("sums_by", field, start, end, where))
        kind = (where or {}).get("kind")
        return dict(self._views if kind == "view" else self._events)

    def range(self, *a, **kw):
        self.calls.append(("range", kw.get("start"), kw.get("end"), kw.get("where")))
        return []

    def total(self, *a, **kw):
        self.calls.append(("total", kw.get("start"), kw.get("end"), kw.get("where")))
        return 0


def _make_app(all_ids, usage, *, errors=None):
    cls = _load_app_class()

    class _App(cls):  # type: ignore[misc,valid-type]
        def _all_app_ids(self):
            return list(all_ids)

        def _errors_by_app(self, days):
            return dict(errors or {})

    app = object.__new__(_App)
    app.usage = usage
    app.kernel = SimpleNamespace(apps=SimpleNamespace(manifests={
        "kb": SimpleNamespace(name="Knowledge Base"),
        "old": SimpleNamespace(name="Old App"),
    }))
    return app


class _Req:
    def __init__(self, **q):
        self.query_params = q


def test_api_unused_one_grouped_query_no_per_app_scans():
    today = datetime.now(UTC).date()
    last = {"task": today.isoformat(), "kb": (today - timedelta(days=40)).isoformat(),
            "old": (today - timedelta(days=200)).isoformat()}
    usage = _UsageRecorder(last=last, active=["task"])
    app = _make_app(["task", "kb", "old", "never"], usage)

    rows = asyncio.run(app.api_unused(_Req(days="30")))

    assert usage.calls == [("top", "app", days_ago_utc(29), today_utc(), None),
                           ("last_bucket", "app", None, None, None)], usage.calls
    by = {r["app_id"]: r for r in rows}
    assert set(by) == {"kb", "old", "never"}  # task is active, so not unused
    assert by["kb"]["last_seen"] == last["kb"] and by["kb"]["days_ago"] in (40, 41)  # UTC-midnight tolerance
    assert by["kb"]["name"] == "Knowledge Base"
    assert by["never"]["last_seen"] is None and by["never"]["days_ago"] is None
    # Pre-existing order, preserved: never-seen counts as infinitely stale
    # (9999) and sorts first; then longest-unused first.
    assert [r["app_id"] for r in rows] == ["never", "old", "kb"]


def test_streaks_one_grouped_query_no_per_app_scans():
    # ISO weeks W34, W35, W36 (2026-09-06 is the Sunday of W36, same week as 08-31)
    usage = _UsageRecorder(buckets={"task": ["2026-08-17", "2026-08-24", "2026-08-31"], "kb": ["2026-06-01"]})
    app = _make_app(["task", "kb", "never"], usage)

    rows = app._streaks_data()

    assert usage.calls == [("buckets_by", "app", None, None, None)], usage.calls
    by = {r["app"]: r for r in rows}
    assert set(by) == {"task", "kb"}, "an app with no buckets yields no row (pre-existing)"
    assert by["task"]["longest_weeks"] == 3
    assert by["task"]["last_week"] == "2026-W36"
    assert by["kb"]["current_weeks"] == 0


def test_errors_vs_usage_two_grouped_queries_no_per_app_totals():
    usage = _UsageRecorder(views={"task": 10, "kb": 2}, events={"task": 5})
    app = _make_app(["task", "kb", "quiet"], usage, errors={"task": 3})

    rows = asyncio.run(app.api_errors_vs_usage(_Req(days="30")))

    s, e = days_ago_utc(29), today_utc()
    assert usage.calls == [("sums_by", "app", s, e, {"kind": "view"}), ("sums_by", "app", s, e, {"kind": "event"})], usage.calls
    by = {r["app"]: r for r in rows}
    assert set(by) == {"task", "kb"}, "an app with no activity and no errors is omitted (pre-existing)"
    assert by["task"]["views"] == 10 and by["task"]["events"] == 5 and by["task"]["errors"] == 3
    assert by["task"]["error_rate"] == round(3 / 15, 4)
    assert by["kb"]["views"] == 2 and by["kb"]["errors"] == 0
