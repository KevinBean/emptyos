"""Unit tests for `@on_event(..., background=True)` (eos-insights, 2026-08-05).

`EventBus.emit` awaits every handler serially in one task, so a slow handler
pins the whole bus for its duration. Measured on the live daemon:
`reactor.on_learn_lesson_completed` held it for 16.7s on one lesson completion.

These tests pin the three properties that make detaching safe, and each FAILS
without the opt-in:

* the bus returns before a slow background handler finishes;
* a background handler that raises is still logged in the bus's own
  `Handler error for <event>` shape — a detached handler must not become a new
  silent-failure surface;
* the spawned task is strongly referenced, so asyncio cannot collect it
  mid-flight.

Pure SDK + kernel bus. No daemon, no vault, no app loading.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from emptyos.kernel.event_bus import EventBus
from emptyos.sdk.decorators import on_event


class _FakeSyslog:
    def __init__(self):
        self.errors: list[tuple[str, str, dict]] = []

    def error(self, source, message, data=None):
        self.errors.append((source, message, data or {}))

    def warn(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


def _Harness(syslog):
    """A real BaseApp with only the two attributes these paths touch.

    Was a hand-built stand-in that bound `_detached_handler` onto itself. That
    is a partial double, and it bit: when `_detached_handler` was refactored to
    delegate tracking to `spawn_background`, three tests failed on the DOUBLE
    lacking that method while the shipped code was fine. `object.__new__` skips
    `__init__` without faking the class, so any future internal collaboration
    between these methods just works.
    """
    from emptyos.sdk.base_app import BaseApp

    app = object.__new__(BaseApp)
    app.kernel = type("K", (), {"syslog": syslog})()
    app.manifest = type("M", (), {"id": "test-app"})()
    app._bg_tasks = set()
    app._event_unsubs = []      # teardown() walks it
    return app


# ── decorator metadata ───────────────────────────────────────────────────────

def test_background_flag_is_recorded_on_the_handler():
    @on_event("x:y", background=True)
    async def h(self, event):
        pass

    assert h._eos_event == {"type": "x:y", "background": True}


def test_plain_on_event_stays_foreground_by_default():
    """The opt-in must be opt-in — every existing handler keeps bus ordering."""

    @on_event("x:y")
    async def h(self, event):
        pass

    assert h._eos_event["background"] is False


# ── the actual point: the bus is freed ───────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_returns_before_a_slow_background_handler_finishes():
    syslog = _FakeSyslog()
    app = _Harness(syslog)
    bus = EventBus()
    done = asyncio.Event()

    async def slow(event):
        await asyncio.sleep(0.30)
        done.set()

    bus.on("slow:evt", app._detached_handler(slow, "slow:evt"))

    started = time.monotonic()
    await bus.emit("slow:evt", {})
    elapsed = time.monotonic() - started

    assert elapsed < 0.10, f"bus blocked for {elapsed:.3f}s — handler was not detached"
    assert not done.is_set(), "handler somehow completed inline"
    await asyncio.wait_for(done.wait(), timeout=2.0)  # …but it does still run


@pytest.mark.asyncio
async def test_a_foreground_handler_still_blocks():
    """The contrast case — proves the test above is measuring the opt-in and
    not just a fast machine."""
    bus = EventBus()

    async def slow(event):
        await asyncio.sleep(0.30)

    bus.on("slow:evt", slow)

    started = time.monotonic()
    await bus.emit("slow:evt", {})
    assert time.monotonic() - started >= 0.25


# ── errors must stay visible ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failing_background_handler_is_still_logged():
    syslog = _FakeSyslog()
    app = _Harness(syslog)
    bus = EventBus()

    async def boom(event):
        raise AttributeError("'Event' object has no attribute 'get'")

    bus.on("publish:deployed", app._detached_handler(boom, "publish:deployed"))
    await bus.emit("publish:deployed", {})
    for _ in range(50):
        if syslog.errors:
            break
        await asyncio.sleep(0.01)

    assert syslog.errors, "a detached handler failed silently"
    source, message, data = syslog.errors[0]
    assert source == "event_bus"
    # Same shape the bus itself logs, so trace-miner's `handler error for
    # <app>:` attribution and its code-bug classifier both still fire.
    assert message.startswith("Handler error for publish:deployed:")
    assert data.get("app") == "test-app"
    assert data.get("background") is True


@pytest.mark.asyncio
async def test_the_spawned_task_is_strongly_referenced():
    """asyncio only weak-refs running tasks; without the set, a background
    handler can be garbage-collected mid-flight."""
    syslog = _FakeSyslog()
    app = _Harness(syslog)
    bus = EventBus()

    async def slow(event):
        await asyncio.sleep(0.20)

    bus.on("slow:evt", app._detached_handler(slow, "slow:evt"))
    await bus.emit("slow:evt", {})

    assert len(app._bg_tasks) == 1
    await asyncio.sleep(0.35)
    assert len(app._bg_tasks) == 0, "done-callback did not discard the task"


@pytest.mark.asyncio
async def test_the_shim_returns_a_non_awaitable():
    """This is the mechanism — `emit` only awaits an awaitable result, so
    returning None is what actually frees the bus."""
    syslog = _FakeSyslog()
    app = _Harness(syslog)

    async def noop(event):
        pass

    shim = app._detached_handler(noop, "x:y")
    result = shim(object())
    assert result is None
    await asyncio.sleep(0.05)


# ── setup() wiring — the link between the decorator and the shim ─────────────

@pytest.mark.asyncio
async def test_setup_registers_background_handlers_detached_and_others_direct():
    """The decorator sets a flag and `_detached_handler` builds the shim, but
    `BaseApp.setup()` is what joins them. Without this, both halves pass their
    own tests while the daemon still registers every handler inline."""
    from emptyos.sdk.base_app import BaseApp

    registered: dict[str, object] = {}

    class _Events:
        def on(self, event_type, handler):
            registered[event_type] = handler
            return lambda: None

    class _App(BaseApp):
        @on_event("fast:evt")
        async def on_fast(self, event):
            pass

        @on_event("slow:evt", background=True)
        async def on_slow(self, event):
            await asyncio.sleep(0.30)

    kernel = type("K", (), {"events": _Events(), "syslog": _FakeSyslog()})()
    app = _App(kernel, type("M", (), {"id": "test-app"})())
    await app.setup()

    assert set(registered) == {"fast:evt", "slow:evt"}
    # The foreground one is the bound method itself…
    assert registered["fast:evt"].__name__ == "on_fast"
    # …the background one is the shim, which returns non-awaitable.
    assert registered["slow:evt"] is not app.on_slow
    started = time.monotonic()
    assert registered["slow:evt"](object()) is None
    assert time.monotonic() - started < 0.10

    await app.teardown()
    assert not app._bg_tasks, "teardown left background tasks running"


# ── spawn_background — setup-time warm-ups ───────────────────────────────────

@pytest.mark.asyncio
async def test_spawn_background_returns_immediately_and_still_runs():
    """A warm-up awaited in setup() blocks the app loader. garden awaited a full
    plot recompute there: 2.5s median, 31.7s worst, the slowest load on the
    daemon."""
    from emptyos.sdk.base_app import BaseApp

    app = object.__new__(BaseApp)
    app.kernel = type("K", (), {"syslog": _FakeSyslog()})()
    app.manifest = type("M", (), {"id": "test-app"})()
    app._bg_tasks = set()

    done = asyncio.Event()

    async def warm():
        await asyncio.sleep(0.25)
        done.set()

    started = time.monotonic()
    app.spawn_background(warm(), label="warm")
    assert time.monotonic() - started < 0.05
    assert not done.is_set()
    await asyncio.wait_for(done.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_spawn_background_holds_a_strong_reference():
    """asyncio weak-refs running tasks; a bare create_task warm-up can be
    collected mid-flight — silently, and most likely under memory pressure."""
    from emptyos.sdk.base_app import BaseApp

    app = object.__new__(BaseApp)
    app.kernel = type("K", (), {"syslog": _FakeSyslog()})()
    app.manifest = type("M", (), {"id": "test-app"})()
    app._bg_tasks = set()

    async def warm():
        await asyncio.sleep(0.15)

    app.spawn_background(warm())
    assert len(app._bg_tasks) == 1
    await asyncio.sleep(0.30)
    assert len(app._bg_tasks) == 0


@pytest.mark.asyncio
async def test_a_failing_warmup_is_logged_not_swallowed():
    from emptyos.sdk.base_app import BaseApp

    syslog = _FakeSyslog()
    app = object.__new__(BaseApp)
    app.kernel = type("K", (), {"syslog": syslog})()
    app.manifest = type("M", (), {"id": "garden"})()
    app._bg_tasks = set()

    class _Warn(_FakeSyslog):
        pass

    warned: list = []
    app.kernel.syslog.warn = lambda src, msg, **k: warned.append((src, msg))

    async def boom():
        raise RuntimeError("plot source unreadable")

    app.spawn_background(boom(), label="initial tick")
    for _ in range(50):
        if warned:
            break
        await asyncio.sleep(0.01)
    assert warned, "a failed warm-up vanished into an unretrieved exception"
    assert "garden" in warned[0][1] and "initial tick" in warned[0][1]


@pytest.mark.asyncio
async def test_teardown_racing_a_fresh_spawn_stays_silent(recwarn):
    """Cancel before the loop runs the task body: the wrapped coroutine was
    created and never awaited, which Python reports as a RuntimeWarning at GC
    time — from a shutdown path where nobody is watching. It must not add noise
    to the log it is racing."""
    import gc

    app = _Harness(_FakeSyslog())

    async def warm():
        await asyncio.sleep(1.0)

    app.spawn_background(warm(), label="warm")
    await app.teardown()          # cancels before the body ever ran
    await asyncio.sleep(0)
    gc.collect()

    never_awaited = [w for w in recwarn
                     if issubclass(w.category, RuntimeWarning)
                     and "never awaited" in str(w.message)]
    assert not never_awaited, f"teardown leaked a coroutine warning: {never_awaited}"
