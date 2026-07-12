"""Delegation — the shared autopilot decide→dispatch/gate→audit core.

One place for the sequence that ``rooms`` (`pending.py:_gate_server_actions`),
``replay``, ``runbook``, and the MCP foundry each inline today: take an actor +
verb, ask :func:`emptyos.sdk.autopilot.decide` whether it auto-runs, and either
dispatch it (``call_app`` + audit) or hand the gated record to the caller's
review surface (``on_gate``) + audit. Persistence, events, and transport stay
with the caller — only the decision/dispatch/audit core is shared.

Lives in its own module rather than ``autopilot.py`` because it takes ``kernel``
and calls into apps; ``autopilot.py`` stays pure (data-dir functions, unit-tested
without a daemon). First consumer: the ``/api/delegated-action`` daemon route
(`emptyos/web/server.py`). Documented convergence targets (rule 9): the rooms +
replay inline copies migrate onto this in a separate, tested change.

See ``.claude/rules/autopilot-grants.md`` and ``.claude/rules/proposed-action.md``.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from emptyos.sdk.autopilot import append_audit, decide
from emptyos.sdk.utils import config_flag


async def gate_or_dispatch(
    kernel,
    *,
    actor: dict,
    app: str,
    method: str,
    args: dict | None = None,
    scope_candidates: list[str],
    on_gate: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Run the autopilot decision for ``actor`` invoking ``app.method``.

    ``decide`` auto-runs the verb when a stable-default or matching grant
    applies (and the actor is within budget); otherwise it gates. On auto we
    dispatch via the kernel app registry (the same resolution
    ``BaseApp.call_app`` uses) and write one audit entry. On gate we hand the
    record to ``on_gate`` (the caller's review surface — e.g. the rooms pending
    queue) and audit the refusal. Audit failures never break the action path.

    Returns one of::

        {"action": "auto", "ok": bool, "grant_id": str|None,
         "result": str, "error": str|None}
        {"action": "gate", "reason": str, "grant_id": str|None}

    ``actor`` is ``{"type": ..., "id": ...}``. ``scope_candidates`` is passed
    straight to ``decide`` (the caller appends ``"global"`` if it wants it).
    """
    data_root = kernel.config.data_dir
    args = args or {}
    verb = f"{app}.{method}"
    actor_type = (actor or {}).get("type") or ""
    actor_id = (actor or {}).get("id") or ""

    eligible = kernel.autopilot_eligible_set()
    decision = decide(
        data_root,
        actor_type=actor_type,
        actor_id=actor_id,
        verb=verb,
        scope_candidates=scope_candidates,
        eligible=eligible,
        auto_stable=config_flag(kernel.config, "autopilot.auto_stable_default"),
        enforce_budget=config_flag(kernel.config, "autopilot.enforce_budget_caps"),
    )
    grant_id = decision.get("grant_id")

    if decision.get("action") != "auto":
        record = {
            "app": app, "method": method, "args": args, "actor": actor or {},
            "gate_reason": decision.get("reason"), "gate_grant_id": grant_id,
        }
        if on_gate is not None:
            # Not wrapped — a failed review-surface persist should surface to
            # the caller (it can retry), not be swallowed under the gate.
            await on_gate(record)
        _safe_audit(
            data_root, actor=actor or {}, app=app, method=method, args=args,
            grant_id=grant_id, ok=False,
            error=f"gated:{decision.get('reason')}",
        )
        return {"action": "gate", "reason": decision.get("reason"),
                "grant_id": grant_id}

    # Auto branch — dispatch the same way BaseApp.call_app resolves a target.
    result: Any = None
    ok = True
    err: str | None = None
    try:
        inst = kernel.apps.instances.get(app)
        if inst is None:
            inst = await kernel.apps.load(app)
        fn = getattr(inst, method, None)
        if fn is None:
            raise AttributeError(f"App '{app}' has no method '{method}'")
        result = fn(**args)
        if inspect.isawaitable(result):
            result = await result
    except Exception as e:  # noqa: BLE001 — outcome captured for audit + caller
        ok = False
        err = str(e)[:200] or e.__class__.__name__

    _safe_audit(
        data_root, actor=actor or {}, app=app, method=method, args=args,
        grant_id=grant_id, ok=ok, error=err,
    )
    return {"action": "auto", "ok": ok, "grant_id": grant_id,
            "result": str(result)[:500], "error": err}


def _safe_audit(data_root, **kw) -> None:
    """append_audit that never raises — audit must not break the action path."""
    try:
        append_audit(data_root, **kw)
    except Exception:
        pass
