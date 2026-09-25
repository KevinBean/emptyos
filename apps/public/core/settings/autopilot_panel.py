"""Settings — Autopilot console (the director's view over every standing
delegation to an AI actor).

Grants/holds/budgets already have working issuers scattered across surfaces —
rooms' per-room "auto-accept" chip, voice-assistant's session toggle, the
`eos autopilot` CLI for the outbound MCP foundry — but there was no single
place to see every active grant/hold/budget across every actor and scope at
once. `eos autopilot list-grants`/`review` answers that from a terminal; this
is the same read (plus revoke + manual issue) from the Settings UI, per
``.claude/rules/autopilot-grants.md`` build-order step 5.

This module owns NOTHING app-specific — it reads/writes the same
``data/autopilot/`` store every other consumer does (``emptyos/sdk/autopilot.py``).
Cross-module callers reach these via ``self.X`` after re-binding in app.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.autopilot import (
    all_budgets,
    eligible_set_for_kernel,
    load_holds,
    load_policy,
    revoke_grant,
    revoke_hold,
    review_grants,
    save_grant,
    set_budget,
)

if TYPE_CHECKING:
    from .app import SettingsApp  # noqa: F401


# ─── Bind to SettingsApp class as ──────────────────────────────
#   api_autopilot_console       = _autopilot_panel.api_autopilot_console
#   api_autopilot_grant         = _autopilot_panel.api_autopilot_grant
#   api_autopilot_revoke        = _autopilot_panel.api_autopilot_revoke
#   api_autopilot_hold_revoke   = _autopilot_panel.api_autopilot_hold_revoke
#   api_autopilot_budget        = _autopilot_panel.api_autopilot_budget
# Adding a route here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────


def _data_dir(self) -> Path:
    return Path(self.kernel.config.data_dir)


def _eligible_set(self):
    """Registry-derived effective floor (None -> legacy policy.json). Same
    source every other grant issuer uses, so a manual issue from this panel
    is refused on exactly the same floor as the room chip / CLI."""
    return eligible_set_for_kernel(self.kernel)


@web_route("GET", "/api/autopilot")
async def api_autopilot_console(self, request):
    """Every active grant + hold + budget + the eligibility policy, in one
    payload. Grants arrive pre-classified (stale/expiring/fresh/active) with
    fire counts via ``review_grants`` — the same aggregation ``eos autopilot
    review`` uses, so the UI and the CLI never disagree."""
    root = _data_dir(self)
    rep = review_grants(root)
    pol = load_policy(root)
    return {
        "grants": rep["grants"],
        "holds": load_holds(root),
        "budgets": all_budgets(root),
        "eligible_verbs": sorted(pol.get("eligible_verbs") or []),
        "week": rep["week"],
        "generated_at": rep["generated_at"],
    }


@web_route("POST", "/api/autopilot/grant")
async def api_autopilot_grant(self, request):
    """Manually issue a grant (the UI form behind the eligibility floor).

    Body: ``{actor_type, actor_id, verb_pattern, scope, ttl_s?, rationale?}``.
    Refused (same as the CLI / room chip) if ``verb_pattern`` names a verb
    that isn't autopilot-eligible — no surface, including this one, can
    override that floor.
    """
    body = await self.safe_json(request)
    actor_type = (body.get("actor_type") or "").strip()
    actor_id = (body.get("actor_id") or "").strip()
    verb_pattern = (body.get("verb_pattern") or "").strip()
    scope = (body.get("scope") or "").strip()
    if not actor_type or not verb_pattern or not scope:
        return {"ok": False, "error": "actor_type, verb_pattern, and scope are required"}
    ttl_s = body.get("ttl_s")
    try:
        ttl_s = int(ttl_s) if ttl_s not in (None, "") else None
    except (TypeError, ValueError):
        return {"ok": False, "error": "ttl_s must be a number of seconds"}
    try:
        grant = save_grant(
            _data_dir(self),
            actor_type=actor_type,
            actor_id=actor_id,
            verb_pattern=verb_pattern,
            scope=scope,
            ttl_seconds=ttl_s,
            rationale=(body.get("rationale") or "").strip(),
            eligible=_eligible_set(self),
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "grant": grant}


@web_route("POST", "/api/autopilot/revoke")
async def api_autopilot_revoke(self, request):
    """Revoke one grant by id. Does not undo past auto-applies — only stops
    future ones (they stay in the audit log)."""
    body = await self.safe_json(request)
    gid = (body.get("grant_id") or "").strip()
    if not gid:
        return {"ok": False, "error": "grant_id required"}
    return {"ok": revoke_grant(_data_dir(self), gid)}


@web_route("POST", "/api/autopilot/hold/revoke")
async def api_autopilot_hold_revoke(self, request):
    """Remove one hold by id — resumes auto-running the verb it paused."""
    body = await self.safe_json(request)
    hid = (body.get("hold_id") or "").strip()
    if not hid:
        return {"ok": False, "error": "hold_id required"}
    return {"ok": revoke_hold(_data_dir(self), hid)}


@web_route("POST", "/api/autopilot/budget")
async def api_autopilot_budget(self, request):
    """Set (or clear, with 0/negative) an actor's monthly USD autopilot cap.

    A budget only ever adds friction under the eligibility/grant floor — it
    never widens what an actor may do, only how much it may spend doing it.
    """
    body = await self.safe_json(request)
    actor_id = (body.get("actor_id") or "").strip()
    if not actor_id:
        return {"ok": False, "error": "actor_id required"}
    try:
        cap = float(body.get("monthly_usd"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "monthly_usd must be a number"}
    rec = set_budget(_data_dir(self), actor_id, cap)
    return {"ok": True, "budget": {"actor_id": actor_id, **rec}}
