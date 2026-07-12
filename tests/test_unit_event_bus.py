"""Unit tests for EventBus — offline, no daemon.

Pins the async-wedge fix: emit()/history() persist + read via asyncio.to_thread
on a check_same_thread=False connection serialized by a lock. The cross-thread
path is the regression target — a naive to_thread on the original
check_same_thread=True connection would raise sqlite3.ProgrammingError.
"""

from __future__ import annotations

import asyncio

import pytest

from emptyos.kernel.event_bus import EventBus


@pytest.fixture
def bus(tmp_path):
    return EventBus(db_path=tmp_path / "events.db")


def test_emit_persists_and_history_reads(bus):
    async def run():
        await bus.emit("test:one", {"n": 1}, source="unit")
        await bus.emit("test:two", {"n": 2}, source="unit")
        rows = await bus.history(limit=10)
        return rows

    rows = asyncio.run(run())
    types = {r["type"] for r in rows}
    assert {"test:one", "test:two"} <= types
    one = next(r for r in rows if r["type"] == "test:one")
    assert one["data"] == {"n": 1}
    assert one["source"] == "unit"


def test_history_filters_by_type(bus):
    async def run():
        await bus.emit("a:x", {})
        await bus.emit("b:y", {})
        return await bus.history(event_type="a:x", limit=10)

    rows = asyncio.run(run())
    assert rows and all(r["type"] == "a:x" for r in rows)


def test_handlers_still_fire_after_async_persist(bus):
    seen = []
    bus.on("hook:fire", lambda ev: seen.append(ev.data["v"]))

    async def run():
        await bus.emit("hook:fire", {"v": 42})

    asyncio.run(run())
    assert seen == [42]


def test_concurrent_emits_all_persist(bus):
    """Cross-thread write path under concurrency — the regression guard."""

    async def run():
        await asyncio.gather(*[bus.emit("burst:e", {"i": i}) for i in range(25)])
        return await bus.history(event_type="burst:e", limit=50)

    rows = asyncio.run(run())
    assert len(rows) == 25
    assert {r["data"]["i"] for r in rows} == set(range(25))


def test_no_db_history_returns_empty():
    b = EventBus(db_path=None)

    async def run():
        await b.emit("x:y", {})  # must not raise without a db
        return await b.history()

    assert asyncio.run(run()) == []


# ---------------------------------------------------------------------------
# Retention — the events table gains a row per emit and had no prune at all,
# so a long-lived daemon grew it without bound.
# ---------------------------------------------------------------------------

def _backdate(bus, event_id: str, days_ago: int):
    """Rewrite one row's timestamp. Timestamps are UTC ISO-8601 strings, so
    lexical ordering is chronological — the same assumption prune() makes."""
    from datetime import UTC, datetime, timedelta
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    with bus._db_lock:
        bus._db.execute("UPDATE events SET timestamp = ? WHERE id = ?", (ts, event_id))
        bus._db.commit()


def _all_ids(bus):
    with bus._db_lock:
        return [r[0] for r in bus._db.execute("SELECT id FROM events").fetchall()]


def test_prune_removes_only_old_events(bus):
    async def run():
        await bus.emit("test:old", {})
        await bus.emit("test:new", {})
    asyncio.run(run())

    old_id, new_id = _all_ids(bus)
    _backdate(bus, old_id, days_ago=40)

    assert bus.prune(days=30) == 1
    assert _all_ids(bus) == [new_id]


def test_prune_is_a_noop_when_nothing_is_old(bus):
    async def run():
        await bus.emit("test:fresh", {})
    asyncio.run(run())
    assert bus.prune(days=30) == 0
    assert len(_all_ids(bus)) == 1


def test_prune_disabled_for_nonpositive_days(bus):
    async def run():
        await bus.emit("test:one", {})
    asyncio.run(run())
    (eid,) = _all_ids(bus)
    _backdate(bus, eid, days_ago=999)
    assert bus.prune(days=0) == 0
    assert len(_all_ids(bus)) == 1


def test_prune_chunks_across_a_large_backlog(bus):
    """The first prune on a long-lived daemon faces a big backlog; the chunk
    loop must delete everything, not just one chunk's worth."""
    async def run():
        for i in range(25):
            await bus.emit("test:bulk", {"i": i})
    asyncio.run(run())
    for eid in _all_ids(bus):
        _backdate(bus, eid, days_ago=40)

    assert bus.prune(days=30, chunk=10) == 25
    assert _all_ids(bus) == []


def test_prune_without_db_is_safe():
    assert EventBus().prune(days=30) == 0


# ---------------------------------------------------------------------------
# Slow-handler detection. emit() awaits handlers serially, so a slow one taxes
# everything behind it. We detect (never cancel — cancelling mid
# read-modify-write would manufacture vault data loss).
# ---------------------------------------------------------------------------

class _CapturingSyslog:
    def __init__(self):
        self.warnings: list[str] = []

    def warn(self, source, message, **kwargs):
        self.warnings.append(message)

    def error(self, source, message, **kwargs):
        pass


def test_slow_handler_is_reported(bus, monkeypatch):
    import emptyos.kernel.event_bus as eb
    monkeypatch.setattr(eb, "SLOW_HANDLER_S", 0.01)
    log = _CapturingSyslog()
    bus.set_syslog(log)

    async def sluggish(event):
        await asyncio.sleep(0.05)

    bus.on("test:slow", sluggish)
    asyncio.run(bus.emit("test:slow", {}))

    assert len(log.warnings) == 1
    assert "sluggish" in log.warnings[0]
    assert "test:slow" in log.warnings[0]


def test_fast_handler_is_silent(bus):
    log = _CapturingSyslog()
    bus.set_syslog(log)
    bus.on("test:fast", lambda event: None)
    asyncio.run(bus.emit("test:fast", {}))
    assert log.warnings == []


def test_slow_handler_warning_is_throttled(bus, monkeypatch):
    """A chronically slow handler on a chatty event must not flood syslog."""
    import emptyos.kernel.event_bus as eb
    monkeypatch.setattr(eb, "SLOW_HANDLER_S", 0.01)
    log = _CapturingSyslog()
    bus.set_syslog(log)

    async def sluggish(event):
        await asyncio.sleep(0.02)

    bus.on("test:chatty", sluggish)

    async def run():
        for _ in range(3):
            await bus.emit("test:chatty", {})
    asyncio.run(run())

    assert len(log.warnings) == 1  # throttled to one per 60s window


def test_slow_handler_throttle_is_shared_across_event_types(bus, monkeypatch):
    """One slow handler warns once per window, no matter how many event types.

    The throttle key is the handler name alone, deliberately. An on_any handler
    runs on every one of the ~540 emitted event names, so keying by
    (handler, event_type) would let a single wedged handler emit dozens of
    duplicate warnings per minute and drown the log exactly when someone is
    reading it. The remediation is per handler (wrap the slow call in
    asyncio.to_thread), and the emitted message names the event type that
    tripped the threshold.
    """
    import emptyos.kernel.event_bus as eb
    monkeypatch.setattr(eb, "SLOW_HANDLER_S", 0.01)
    log = _CapturingSyslog()
    bus.set_syslog(log)

    async def sluggish(event):
        await asyncio.sleep(0.02)

    bus.on("test:first", sluggish)
    bus.on("test:second", sluggish)
    asyncio.run(bus.emit("test:first", {}))
    asyncio.run(bus.emit("test:second", {}))
    assert len(log.warnings) == 1


def test_slow_detection_does_not_cancel_the_handler(bus, monkeypatch):
    """Detection only — the handler must still run to completion."""
    import emptyos.kernel.event_bus as eb
    monkeypatch.setattr(eb, "SLOW_HANDLER_S", 0.01)
    bus.set_syslog(_CapturingSyslog())
    done = []

    async def sluggish(event):
        await asyncio.sleep(0.03)
        done.append(True)

    bus.on("test:slow", sluggish)
    asyncio.run(bus.emit("test:slow", {}))
    assert done == [True]
