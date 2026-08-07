"""Rooms — autopilot grant ISSUER (the ⚡ auto-accept session chip).

The rooms review gate (``pending.py::_gate_server_actions``) already CONSUMES
grants via ``autopilot.decide()`` — a matching grant lets an eligible ``[DO:]``
verb auto-apply instead of landing in the pending queue. What was missing is the
in-app ISSUER: a way for the user to mint those grants from the rooms UI. This is
``.claude/rules/autopilot-grants.md`` "piece 3".

Mirrors the voice-assistant grant-issuing vertical
(``apps/public/standard/voice-assistant/pending.py``) with two rooms-specific
differences:
  • rooms has two actor types — ``agent`` and ``cli`` — so one toggle issues
    grants for both (``actor.id=""`` = any participant of that type, the
    "auto-accept everyone in this room" pattern ``match()`` supports).
  • scope is per-room: ``session:<room_id>`` (matches the gate's
    ``scope_candidates`` at pending.py:479), bounded by a TTL (default 1h) so a
    forgotten toggle expires on its own.

The eligibility floor is enforced at save time — a ``gated``/``never`` verb can
never be granted, no matter what. Cross-module callers reach these via ``self.X``
after re-binding in app.py. Do not import from ``.app`` (it imports us).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import autopilot as _autopilot
from emptyos.sdk import web_route
from emptyos.sdk.utils import config_flag

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ────────────────────────────────
#   api_autopilot_status   = _autopilot_mod.api_autopilot_status
#   api_autopilot_session  = _autopilot_mod.api_autopilot_session
#   api_autopilot_revoke   = _autopilot_mod.api_autopilot_revoke
#   api_autopilot_hold     = _autopilot_mod.api_autopilot_hold
# Adding a route here? Add a matching binding line in app.py.
# ──────────────────────────────────────────────────────────────

# The two actor types a room's [DO:] actions carry (source_actor.type).
_ROOM_ACTOR_TYPES = ("agent", "cli")


def _autopilot_root(self) -> Path:
    """The shared autopilot store root — the SAME path the gate passes to
    ``decide()`` (``kernel.config.data_dir``), so issued grants are found at
    fire time."""
    return Path(self.kernel.config.data_dir)


def _room_scope(room_id: str) -> str:
    """Per-room session scope. Matches the gate's ``session:<room_id>``
    candidate; TTL is what bounds its lifetime."""
    return f"session:{room_id}"


def _eligible_set(self):
    """Registry-derived effective floor (None → legacy policy.json). Same source
    the gate uses, so save-time and fire-time eligibility agree."""
    fn = getattr(self.kernel, "autopilot_eligible_set", None)
    elig = fn() if callable(fn) else None
    if elig is None:
        elig = set(_autopilot.load_policy(_autopilot_root(self)).get("eligible_verbs") or [])
    return elig


@web_route("GET", "/api/autopilot")
async def api_autopilot_status(self, request):
    """Active auto-accept grants for a room + the eligible-verb list.

    Query: ``?room_id=<id>``. Returns session/room/global grants that target a
    room actor type, so the chip can render an active/countdown state.
    """
    room_id = (request.query_params.get("room_id") or "").strip()
    scope = _room_scope(room_id)
    root = _autopilot_root(self)
    grants = [
        g
        for g in _autopilot.load_grants(root)
        if g.get("scope") in (scope, f"room:{room_id}", "global")
        and (g.get("actor") or {}).get("type") in _ROOM_ACTOR_TYPES
    ]
    # Which control is meaningful depends on the global stable-default:
    #   • ON  → reversible verbs already auto-run; the useful control is a HOLD
    #           (pause auto for this room). Report whether a hold is active.
    #   • OFF → a grant is what makes an eligible verb auto-run (the ⚡ chip).
    auto_stable = config_flag(self.kernel.config, "autopilot.auto_stable_default")
    holds = [
        h
        for h in _autopilot.load_holds(root)
        if h.get("scope") == scope and (h.get("actor") or {}).get("type") in _ROOM_ACTOR_TYPES
    ]
    return {
        "scope": scope,
        "grants": grants,
        "held": bool(holds),
        "eligible_verbs": sorted(_eligible_set(self)),
        "auto_stable": auto_stable,
    }


@web_route("POST", "/api/autopilot/session")
async def api_autopilot_session(self, request):
    """Toggle session-scoped auto-accept for one room.

    Body: ``{"room_id": "...", "enabled": true, "ttl_s": 3600}`` to grant;
    ``{"room_id": "...", "enabled": false}`` to revoke. On enable, issues one
    ``<ns>.*`` grant per eligible namespace × {agent, cli} with ``actor.id=""``
    (any participant), so a single toggle covers "auto-accept every eligible
    verb from anyone in this room, this session." Non-eligible namespaces are
    skipped (the floor holds).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    room_id = (body.get("room_id") or "").strip()
    if not room_id:
        return {"ok": False, "error": "room_id required"}
    scope = _room_scope(room_id)
    root = _autopilot_root(self)

    if not bool(body.get("enabled")):
        n = _autopilot.revoke_scope(root, scope)
        return {"ok": True, "revoked": n, "active": False}

    ttl = int(body.get("ttl_s") or 3600)
    issued = _autopilot.replace_namespace_grants(
        root,
        scope=scope,
        actors=[(atype, "") for atype in _ROOM_ACTOR_TYPES],  # "" = any participant
        eligible=_eligible_set(self),
        ttl_seconds=ttl,
        rationale="rooms auto-accept toggle",
    )
    return {"ok": True, "active": True, "scope": scope, "grants": issued, "expires_in_s": ttl}


@web_route("POST", "/api/autopilot/revoke")
async def api_autopilot_revoke(self, request):
    """Revoke a single grant by id (row-level revoke)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    gid = (body.get("grant_id") or "").strip()
    if not gid:
        return {"ok": False, "error": "grant_id required"}
    ok = _autopilot.revoke_grant(_autopilot_root(self), gid)
    return {"ok": ok}


@web_route("POST", "/api/autopilot/hold")
async def api_autopilot_hold(self, request):
    """Toggle a HOLD on a room — pause auto-run for its actions so they route
    through the review gate instead (the "watch this room" control that's useful
    when global stable-default auto is ON).

    Body: ``{"room_id": "...", "enabled": true}`` to pause; ``{"enabled": false}``
    to resume. On enable, issues one ``<ns>.*`` hold per eligible namespace ×
    {agent, cli} with ``actor.id=""`` (any participant), scope
    ``session:<room_id>``. Holds have NO TTL (persist until the user resumes) —
    fail-safe: a forgotten hold means MORE review, never less. Holds win over
    grants, so a paused room gates even if a grant also names the verb.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    room_id = (body.get("room_id") or "").strip()
    if not room_id:
        return {"ok": False, "error": "room_id required"}
    scope = _room_scope(room_id)
    root = _autopilot_root(self)

    if not bool(body.get("enabled")):
        n = _autopilot.revoke_hold_scope(root, scope)
        return {"ok": True, "resumed": n, "held": False}

    # Hold every eligible namespace (the verbs that auto-run) for both actor
    # types. Holds have no eligibility floor, but scoping to the eligible
    # namespaces keeps the hold aligned with what would otherwise auto-run.
    namespaces = sorted({v.split(".", 1)[0] for v in _eligible_set(self)})
    _autopilot.revoke_hold_scope(root, scope)  # replace → clean toggle
    issued = []
    for atype in _ROOM_ACTOR_TYPES:
        for ns in namespaces:
            issued.append(
                _autopilot.save_hold(
                    root,
                    actor_type=atype,
                    actor_id="",
                    verb_pattern=f"{ns}.*",
                    scope=scope,
                    rationale="rooms pause-auto toggle",
                )
            )
    return {"ok": True, "held": True, "scope": scope, "holds": issued}
