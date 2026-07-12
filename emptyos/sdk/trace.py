"""Trace correlation — one id threading think → tool → result inside an agent task.

A *trace* is one full agent task (one ``run_turn`` / ``run_native_turn``
invocation, including any nested SubAgent turns). The trace id is carried in a
``contextvars.ContextVar``, so it flows across ``await`` boundaries within the
task that started the turn without threading a parameter through every layer:

- ``agent_loop._emit`` stamps it onto every ``agent:*`` event,
- ``BaseApp._emit_think_executed`` stamps it onto ``think:executed`` (so any
  ``self.think()`` made *inside* a traced turn — orient, sub-agents — is
  attributable),
- the agent app's tool-audit JSONL hook stamps it onto each audit line,
- the billing app (dark-flagged) retains per-call cost rows keyed by it.

Pure stdlib, no kernel — unit-testable without a daemon. Nothing here ever
raises into a caller's path.
"""

from __future__ import annotations

import contextvars
import functools
import uuid

_current_trace: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "eos_trace_id", default=None
)


def new_trace_id() -> str:
    """Mint a fresh trace id (``trace-<10 hex>``)."""
    return "trace-" + uuid.uuid4().hex[:10]


def current_trace_id() -> str | None:
    """The trace id active in this task's context, or None outside any trace."""
    return _current_trace.get()


def set_trace_id(trace_id: str) -> contextvars.Token:
    """Activate a trace id; returns the token for ``reset_trace_id``."""
    return _current_trace.set(trace_id)


def reset_trace_id(token: contextvars.Token) -> None:
    """Deactivate a trace set by ``set_trace_id``. Never raises."""
    try:
        _current_trace.reset(token)
    except Exception:
        pass


def stamp_trace(data: dict) -> dict:
    """Add ``trace_id`` to an event payload when a trace is active.

    Mutates and returns ``data``. No-op when no trace is active or the payload
    already carries a trace_id (a nested emitter wins over the ambient one).
    """
    tid = _current_trace.get()
    if tid and "trace_id" not in data:
        data["trace_id"] = tid
    return data


def trace_boundary(fn):
    """Decorator marking an async function as the root of one trace.

    Mints a trace id when none is active; an already-active trace is inherited
    unchanged (a nested SubAgent turn shares its parent's trace — the trace is
    the *task*, not the call). The context is reset on exit, so sequential
    turns in the same asyncio task each get a fresh id.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        token = None
        if _current_trace.get() is None:
            token = _current_trace.set(new_trace_id())
        try:
            return await fn(*args, **kwargs)
        finally:
            if token is not None:
                reset_trace_id(token)

    return wrapper
