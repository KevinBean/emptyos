"""Aura — pending action gate (risk-shaped intents await Apply/Reject).

Companion to the rooms review-gate (`apps/rooms/pending.py`) but minimal:
no `[DO:]` token parsing (Aura uses `[INTENT:]` and dispatches inline),
no sandboxed writes (free-form vault writes are NEVER autopilot-eligible
so they always land here as pending). Just: persist, list, apply via
call_app, reject.

Extracted from `app.py` to keep the spine focused on lifecycle + chat
orchestration. Module-level functions are bound onto `VoiceAssistantApp`
in `app.py` per `.claude/rules/multi-module-apps.md`.

Reaches into other modules: `self.call_app` (kernel), `self.emit` (kernel).
Reads grant state via `emptyos.sdk.autopilot`.
Do not import from `.app` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk import autopilot as _autopilot

if TYPE_CHECKING:
    from .app import VoiceAssistantApp  # noqa: F401 — for type hints only


# ─── Bind to VoiceAssistantApp class as ─────────────────────────────────
#   _pending_dir            = _pending._pending_dir
#   _pending_path           = _pending._pending_path
#   _actions_log_path       = _pending._actions_log_path
#   _save_pending           = _pending._save_pending
#   _load_pending           = _pending._load_pending
#   _autopilot_data_root    = _pending._autopilot_data_root
#   save_pending_action     = _pending.save_pending_action
#   list_pending            = _pending.list_pending
#   find_pending_duplicate  = _pending.find_pending_duplicate
#   apply_pending           = _pending.apply_pending
#   reject_pending          = _pending.reject_pending
#   _resolve_last_pending   = _pending._resolve_last_pending
#   voice_confirm_pending   = _pending.voice_confirm_pending
#   voice_reject_pending    = _pending.voice_reject_pending
#   _is_grant_active        = _pending._is_grant_active
#   _session_scope          = _pending._session_scope          # @staticmethod
#   api_pending_list        = _pending.api_pending_list
#   api_pending_apply       = _pending.api_pending_apply
#   api_pending_reject      = _pending.api_pending_reject
#   api_undo                = _pending.api_undo
#   api_autopilot_status    = _pending.api_autopilot_status
#   api_autopilot_grant     = _pending.api_autopilot_grant
#   api_autopilot_revoke    = _pending.api_autopilot_revoke
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


# Voice grants are scoped to a single Aura "session" — defined as the
# lifetime of one daemon boot, because Aura has only one conversation
# context per machine. Daemon restart implicitly revokes all session
# grants (the scope string changes via _session_scope()).
_SESSION_BOOT_TOKEN: str | None = None


def _session_scope(self) -> str:
    """Stable per-daemon-boot scope string. Re-issued on daemon restart so
    every session grant is implicitly reaped on reboot."""
    global _SESSION_BOOT_TOKEN
    if _SESSION_BOOT_TOKEN is None:
        _SESSION_BOOT_TOKEN = "session:voice-" + uuid.uuid4().hex[:8]
    return _SESSION_BOOT_TOKEN


def _autopilot_data_root(self) -> Path:
    """The autopilot store is shared across grant consumers (rooms +
    voice-assistant), so it lives under the kernel data root, not Aura's
    own data_dir. ``self.data_dir`` is ``data/apps/voice-assistant`` —
    its parent's parent is ``data/``."""
    return Path(self.data_dir).parent.parent


def _pending_dir(self) -> Path:
    return self.data_subdir("pending")


def _pending_path(self, action_id: str) -> Path:
    return _pending_dir(self) / f"{action_id}.json"


def _actions_log_path(self) -> Path:
    """The undo actions-log for applied voice intents. Same JSONL shape the
    rooms path writes (both read via emptyos.sdk.actions_log)."""
    return Path(self.data_dir) / "actions_log.jsonl"


def _save_pending(self, action: dict) -> None:
    _pending_path(self, action["id"]).write_text(
        json.dumps(action, indent=2), encoding="utf-8",
    )


def _load_pending(self, action_id: str) -> dict | None:
    p = _pending_path(self, action_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_grant_active(self, verb: str, *, companion_id: str | None = None) -> bool:
    """Does the current session have a grant covering this verb?

    Voice grants always use actor.type=``voice`` and actor.id=``aura``
    today (Aura is a single-user surface). Scope candidates: the current
    session scope + global.
    """
    # Registry-derived floor (None = legacy). getattr-guarded so a minimal
    # kernel without the method degrades to the legacy floor, not a crash.
    _elig_fn = getattr(self.kernel, "autopilot_eligible_set", None)
    grant = _autopilot.match(
        _autopilot_data_root(self),
        actor_type="voice",
        actor_id="aura",
        verb=verb,
        scope_candidates=[_session_scope(self), "global"],
        eligible=_elig_fn() if callable(_elig_fn) else None,
    )
    return grant is not None


async def save_pending_action(
    self,
    *,
    verb: str,
    app: str,
    method: str,
    args: dict | None = None,
    description: str = "",
    say: str = "",
    companion_id: str | None = None,
) -> dict:
    """File a pending Aura intent. Returns the saved action dict."""
    action = {
        "id": "act-" + uuid.uuid4().hex[:10],
        "ts": datetime.now(timezone.utc).isoformat(),
        "source_actor": {
            "type": "voice",
            "id": "aura",
            "companion": companion_id or "",
        },
        "verb": verb,
        "app": app,
        "method": method,
        "args": args or {},
        "description": description,
        "say": say,
        "status": "pending",
    }
    _save_pending(self, action)
    asyncio.create_task(
        self.emit(
            "voice-assistant:action_proposed",
            {
                "action_id": action["id"],
                "verb": verb,
                "app": app,
                "method": method,
            },
        )
    )
    return action


def list_pending(self, status: str = "pending") -> list[dict]:
    """List pending Aura actions. status='' returns every record."""
    out: list[dict] = []
    for f in _pending_dir(self).glob("act-*.json"):
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if status and a.get("status") != status:
            continue
        out.append(a)
    out.sort(key=lambda x: x.get("ts", ""))
    return out


def find_pending_duplicate(self, verb: str, args: dict | None) -> dict | None:
    """Return an already-pending action with the same verb + args, if any.

    Lets the gate re-reference the existing card instead of filing a new
    file every time the user repeats the request (the 2026-06-29 log shows
    the same recipe-add gated three times → three orphan pending files).
    """
    try:
        args_key = json.dumps(args or {}, sort_keys=True)
    except Exception:
        return None
    for a in list_pending(self, "pending"):
        try:
            if a.get("verb") == verb and json.dumps(a.get("args") or {}, sort_keys=True) == args_key:
                return a
        except Exception:
            continue
    return None


async def apply_pending(self, action_id: str) -> dict:
    """Execute a pending Aura action via call_app; mark applied."""
    action = _load_pending(self, action_id)
    if not action:
        return {"error": "action not found"}
    if action.get("status") != "pending":
        return {"error": f"already {action.get('status')}"}
    try:
        result = await self.call_app(
            action["app"], action["method"], **(action.get("args") or {}),
        )
    except Exception as e:
        action["status"] = "failed"
        action["error"] = str(e)[:200]
        action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
        _save_pending(self, action)
        return {"error": str(e)[:200], "action": action}
    action["status"] = "applied"
    action["result"] = str(result)[:500] if not isinstance(result, dict) else "ok"
    if isinstance(result, dict) and result.get("say"):
        # Keep the applied verb's own spoken outcome so a voice confirm
        # ("yes, apply it") can speak it back instead of a generic "done".
        action["result_say"] = str(result["say"])[:300]
    action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
    # Record to the shared undo log (registry-aware inverse) so /api/undo can
    # reverse an applied intent — parity with the rooms path.
    from emptyos.sdk.actions_log import lookup_inverse, record_action
    inverse = lookup_inverse(self.kernel, action["app"], action["method"])
    action["reversible"] = bool(inverse)
    record_action(_actions_log_path(self), app=action["app"], method=action["method"],
                  args=action.get("args") or {}, result=result, inverse=inverse)
    _save_pending(self, action)
    await self.emit("voice-assistant:action_applied", {
        "action_id": action_id, "verb": action.get("verb"),
        "app": action["app"], "method": action["method"],
    })
    return action


async def reject_pending(self, action_id: str) -> dict:
    """Mark a pending Aura action rejected without executing."""
    action = _load_pending(self, action_id)
    if not action:
        return {"error": "action not found"}
    if action.get("status") != "pending":
        return {"error": f"already {action.get('status')}"}
    action["status"] = "rejected"
    action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
    _save_pending(self, action)
    await self.emit("voice-assistant:action_rejected", {
        "action_id": action_id, "verb": action.get("verb"),
    })
    return action


# ── Voice confirm / dismiss (aura.confirm / aura.dismiss) ──────────────
# The Apply/Reject card is a click surface; these verbs are the VOICE path
# to the same resolution, so "Want me to apply that?" is no longer a dead
# end for a hands-free (or phone) user. The model emits
# [INTENT:aura.confirm({})] on "yes / apply it / 好 / 确认".

def _resolve_last_pending(self) -> str | None:
    """The action a bare 'yes' refers to: this companion's last gate stash,
    else the newest still-pending action (covers daemon restarts)."""
    stash = getattr(self, "_last_pending", None) or {}
    aid = stash.get(getattr(self, "_active_companion", "") or "")
    if aid:
        a = _load_pending(self, aid)
        if a and a.get("status") == "pending":
            return aid
    all_pending = list_pending(self, "pending")
    return all_pending[-1]["id"] if all_pending else None


async def voice_confirm_pending(self) -> dict:
    aid = _resolve_last_pending(self)
    if not aid:
        return {"say": "Nothing pending to confirm."}
    action = await apply_pending(self, aid)
    if action.get("error"):
        return {"say": f"Couldn't apply that — {action['error']}."}
    say = action.get("result_say") or f"Applied — {action.get('description') or action.get('verb') or 'done'}."
    return {"say": say}


async def voice_reject_pending(self) -> dict:
    aid = _resolve_last_pending(self)
    if not aid:
        return {"say": "Nothing pending to dismiss."}
    action = await reject_pending(self, aid)
    if action.get("error"):
        return {"say": f"Couldn't dismiss that — {action['error']}."}
    return {"say": "Okay, dropped it."}


# ── HTTP routes ────────────────────────────────────────────────────────

@web_route("GET", "/api/pending")
async def api_pending_list(self, request):
    status = request.query_params.get("status", "pending")
    return {"pending": list_pending(self, status=status)}


@web_route("POST", "/api/pending/{action_id}/apply")
async def api_pending_apply(self, request):
    action_id = request.path_params["action_id"]
    return await apply_pending(self, action_id)


@web_route("POST", "/api/pending/{action_id}/reject")
async def api_pending_reject(self, request):
    action_id = request.path_params["action_id"]
    return await reject_pending(self, action_id)


@web_route("POST", "/api/undo")
async def api_undo(self, request):
    """Undo the last reversible applied intent by calling its declared inverse.

    Parity with the rooms Undo path — shared logic in emptyos.sdk.actions_log;
    this route wraps it with the voice-assistant emit + response shape.
    """
    from emptyos.sdk.actions_log import undo_last
    out = await undo_last(_actions_log_path(self), self.call_app)
    if out.get("ok"):
        undid = out.get("undid", {})
        await self.emit("voice-assistant:undo", {
            "app": undid.get("app"), "method": undid.get("method"),
            "inverse": undid.get("inverse"),
        })
    return out


@web_route("GET", "/api/autopilot")
async def api_autopilot_status(self, request):
    """Return active session-scoped grants + countdown info."""
    scope = _session_scope(self)
    grants = [
        g for g in _autopilot.load_grants(_autopilot_data_root(self))
        if g.get("scope") in (scope, "global")
        and (g.get("actor") or {}).get("type") == "voice"
    ]
    pol = _autopilot.load_policy(_autopilot_data_root(self))
    return {
        "scope": scope,
        "grants": grants,
        "eligible_verbs": pol.get("eligible_verbs") or [],
    }


@web_route("POST", "/api/autopilot/session")
async def api_autopilot_grant(self, request):
    """Toggle session-scoped auto-accept for the current voice session.

    Body: `{"enabled": true, "ttl_s": 3600}` to grant; `{"enabled": false}` to revoke.
    Issues a single grant covering every eligible verb (`<ns>.*` pattern on
    every namespace in the eligibility list) so the user gets "auto-accept
    everything I'm allowed to" in one toggle.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = bool(body.get("enabled"))
    scope = _session_scope(self)
    root = _autopilot_data_root(self)

    if not enabled:
        n = _autopilot.revoke_scope(root, scope)
        return {"ok": True, "revoked": n, "active": False}

    ttl = int(body.get("ttl_s") or 3600)
    # Issue one grant per app namespace appearing in the eligibility list,
    # so a single toggle covers "every eligible verb in this session."
    # Use the registry-derived floor when the flag is on; else the legacy
    # policy.json list. The same set both names the eligible namespaces and is
    # passed to save_grant so the save-time check agrees with the fire-time one.
    _elig_fn = getattr(self.kernel, "autopilot_eligible_set", None)
    eligible = _elig_fn() if callable(_elig_fn) else None
    if eligible is None:
        eligible = set(_autopilot.load_policy(root).get("eligible_verbs") or [])
    namespaces = sorted({v.split(".", 1)[0] for v in eligible})
    # Replace any existing session grants so the toggle is clean.
    _autopilot.revoke_scope(root, scope)
    issued = []
    for ns in namespaces:
        try:
            g = _autopilot.save_grant(
                root,
                actor_type="voice",
                actor_id="aura",
                verb_pattern=f"{ns}.*",
                scope=scope,
                ttl_seconds=ttl,
                rationale="voice toolbar toggle",
                eligible=eligible,
            )
            issued.append(g)
        except ValueError:
            # Namespace wildcard hit a non-eligible verb somehow. Skip.
            continue
    return {"ok": True, "active": True, "scope": scope, "grants": issued, "expires_in_s": ttl}


@web_route("POST", "/api/autopilot/revoke")
async def api_autopilot_revoke(self, request):
    """Revoke a specific grant by id (Settings → Autopilot row revoke)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    gid = (body.get("grant_id") or "").strip()
    if not gid:
        return {"ok": False, "error": "grant_id required"}
    ok = _autopilot.revoke_grant(_autopilot_data_root(self), gid)
    return {"ok": ok}
