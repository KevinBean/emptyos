"""Atomic claim for approval queues — persist the claim before the side effect.

Three consumers (CLAUDE.md rule 9 — extracted on the third, and per
``feedback_platform_fix_for_n_app_bugs``: the same bug in >=3 apps is a platform
fix) shared one broken shape:

    action = load(id)
    if action["status"] != "pending": return already
    result = await call_app(...)        # <-- yields the event loop
    action["status"] = "applied"; save(action)

The ``await`` yields, so two concurrent approvals both passed the status check
and both ran the side effect. Measured, not inferred: two concurrent
``rooms.apply_pending`` calls executed ``task.add`` twice
(``tests/test_unit_rooms_logic.py::TestApplyPendingConcurrency``). Reach paths
that make this live rather than theoretical: rooms is approvable from both the
HTTP route and the Telegram callback; voice-assistant from a mis-heard repeated
voice confirm.

``staff/approvals.py`` already had the correct shape — claim under the lock,
release, then execute — and this is that shape made reusable.

**Why a lock is not enough on its own.** ``asyncio.Lock`` serialises tasks
inside one process, but the claim must also survive a crash and be visible to
anything else reading the queue directory. So the claim is *persisted* inside
the lock. An action left at ``CLAIMED`` after a restart is the honest
"we started it and don't know if it landed" state — never silently replayed.

Pure module: no ``self``, no I/O beyond the injected ``load``/``save``. The two
callables exist because the three consumers keep three different conventions
(bound method, module-fn-taking-self, module-fn-taking-app) over identical
JSON-file semantics; normalising those is a bigger refactor than this fix.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

# The claim marker. Matches staff/approvals.py's vocabulary so one word means
# the same thing across every approval queue.
CLAIMED = "approving"
# Status a queue entry may be claimed FROM.
PENDING = "pending"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def claim_pending(
    lock: asyncio.Lock,
    load: Callable[[str], dict | None],
    save: Callable[[dict], None],
    action_id: str,
    *,
    claim_status: str = CLAIMED,
    from_status: str = PENDING,
    now: Callable[[], str] = _iso_now,
) -> tuple[dict | None, dict | None]:
    """Atomically move one queue entry ``from_status`` -> ``claim_status``.

    Returns ``(action, None)`` for the single winner and ``(None, error)`` for
    every other caller, where ``error`` is the ``{"error": ...}`` dict the
    existing endpoints already return — so call sites keep their contract.

    The winner holds no lock on return: the side effect runs *outside*, so one
    slow ``call_app`` never blocks approvals of unrelated actions (the reason
    staff drops its lock before executing).

    ``claim_status="rejected"`` makes this the reject path too — a reject is a
    claim that needs no execution, which is what stops Apply and Reject from
    both winning and the later write silently discarding the user's decision.
    """
    async with lock:
        action = load(action_id)
        if not action:
            return None, {"error": "action not found"}
        current = action.get("status")
        if current != from_status:
            return None, {"error": f"already {current}"}
        action["status"] = claim_status
        action["claimed_ts"] = now()
        save(action)
        return action, None


def is_unknown_effect(action: dict, *, claim_status: str = CLAIMED) -> bool:
    """True when an entry is stuck at its claim marker.

    Means: we claimed it, started the side effect, and never recorded an
    outcome — the process died mid-execution. The effect may or may not have
    landed, so it must be surfaced for a human decision and never auto-retried.
    A retry requires a fresh approval.
    """
    return action.get("status") == claim_status
