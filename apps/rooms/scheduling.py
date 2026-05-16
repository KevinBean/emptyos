"""Rooms — time-based features (reminders + room schedules + boot migrations).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: reminders cron + per-room scheduled messages + boot-time data migrations.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._chat`` (chat.py) when firing schedules; ``self._load_history`` (agents.py) on migrations.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from apscheduler.triggers.cron import CronTrigger

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────
#   _migrate_legacy_gpts_data        = _scheduling._migrate_legacy_gpts_data
#   _sync_general_assistant_actions  = _scheduling._sync_general_assistant_actions
#   _reminders_path                  = _scheduling._reminders_path
#   _load_reminders                  = _scheduling._load_reminders
#   _save_reminders                  = _scheduling._save_reminders
#   add_reminder                     = _scheduling.add_reminder
#   list_reminders                   = _scheduling.list_reminders
#   remove_reminder                  = _scheduling.remove_reminder
#   _fire_due_reminders              = _scheduling._fire_due_reminders
#   _register_reminder_cron          = _scheduling._register_reminder_cron
#   _schedule_job_id                 = _scheduling._schedule_job_id
#   _register_room_schedules         = _scheduling._register_room_schedules
#   SCHEDULE_SYSTEM_SUFFIX           = _scheduling.SCHEDULE_SYSTEM_SUFFIX
#   _fire_room_schedule              = _scheduling._fire_room_schedule
#   set_schedule                     = _scheduling.set_schedule
#   clear_schedule                   = _scheduling.clear_schedule
#   api_set_schedule                 = _scheduling.api_set_schedule
#   api_clear_schedule               = _scheduling.api_clear_schedule
#   api_fire_schedule_now            = _scheduling.api_fire_schedule_now
#   api_add_reminder                 = _scheduling.api_add_reminder
#   api_list_reminders               = _scheduling.api_list_reminders
#   api_remove_reminder              = _scheduling.api_remove_reminder
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _migrate_legacy_gpts_data(self):
    """Move per-machine state from `data/apps/gpts/` to `data/apps/rooms/`
    on first boot after the rename. Lossless one-shot — leaves the source
    directory renamed to `gpts.migrated-<ts>/` for verification, doesn't
    delete it. No-op if there's nothing to migrate.

    Lives inline (not in a separate _migrate_gpts.py) so it runs deterministically
    before any data_dir read from setup() / chat() / list_agents().
    """
    legacy_dir = self.kernel.config.data_dir / "apps" / "gpts"
    target_dir = self.data_dir
    if not legacy_dir.exists() or not legacy_dir.is_dir():
        return
    marker = target_dir / ".migrated-from-gpts"
    if marker.exists():
        return
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for child in legacy_dir.iterdir():
            dest = target_dir / child.name
            if dest.exists():
                continue  # never overwrite live data
            child.rename(dest)
        marker.write_text(
            datetime.now(timezone.utc).isoformat(), encoding="utf-8"
        )
        # Park the legacy dir under a timestamped name so the user can
        # verify and remove it manually.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        try:
            legacy_dir.rename(legacy_dir.with_name(f"gpts.migrated-{stamp}"))
        except OSError:
            # Cross-volume / locked — non-fatal; data is already moved.
            pass
    except Exception as e:
        # Best-effort. If migration fails, the app boots empty and the
        # user will see no agents until they investigate.
        try:
            self.kernel.syslog.error(
                "rooms", f"legacy gpts data migration failed: {e}"
            )
        except Exception:
            pass


def _sync_general_assistant_actions(self):
    """Regenerate general-assistant's server_actions from every app manifest's
    [provides.assistant].commands — so declaring a slash command once in a
    manifest is enough to let the page-assistant drawer call it.
    """
    actions: dict[str, list[str]] = {}
    for app_id, manifest in self.kernel.apps.manifests.items():
        commands = manifest.provides.get("assistant", {}).get("commands", [])
        methods = sorted({c["method"] for c in commands if c.get("method")})
        if methods:
            actions[app_id] = methods

    agent = self._load_agent("general-assistant")
    if not agent:
        return
    if agent.get("server_actions") == actions:
        return
    agent["server_actions"] = actions
    self._save_agent(agent)


def _reminders_path(self) -> Path:
    return self.data_dir / "reminders.json"


def _load_reminders(self) -> list[dict]:
    p = self._reminders_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_reminders(self, reminders: list[dict]) -> None:
    try:
        self._reminders_path().write_text(
            json.dumps(reminders, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


async def add_reminder(self, room_id: str, due_ts: str, note: str = "") -> dict:
    """Schedule a reminder for `room_id` at ISO `due_ts`. The reminder fires
    once the cron job sees due_ts ≤ now (1-minute resolution)."""
    if not room_id or not due_ts:
        return {"error": "room_id and due_ts required"}
    if not self._load_agent(room_id):
        return {"error": "room not found"}
    reminder = {
        "id": f"rem-{uuid.uuid4().hex[:10]}",
        "room_id": room_id,
        "due_ts": due_ts,
        "note": (note or "").strip(),
        "created_ts": datetime.now(timezone.utc).isoformat(),
        "fired": False,
    }
    rems = self._load_reminders()
    rems.append(reminder)
    self._save_reminders(rems)
    return reminder


def list_reminders(self, room_id: str = "", include_fired: bool = False) -> list[dict]:
    rems = self._load_reminders()
    out = []
    for r in rems:
        if room_id and r.get("room_id") != room_id:
            continue
        if not include_fired and r.get("fired"):
            continue
        out.append(r)
    out.sort(key=lambda r: r.get("due_ts", ""))
    return out


async def remove_reminder(self, reminder_id: str) -> dict:
    rems = self._load_reminders()
    before = len(rems)
    rems = [r for r in rems if r.get("id") != reminder_id]
    if len(rems) == before:
        return {"error": "reminder not found"}
    self._save_reminders(rems)
    return {"ok": True, "id": reminder_id}


async def _fire_due_reminders(self) -> None:
    """Cron tick — scan for reminders whose due_ts has passed and mark
    them fired. Each fire emits `rooms:reminder_fired` so the reactor /
    UI can surface the nudge. Idempotent — fired reminders never re-fire."""
    rems = self._load_reminders()
    now = datetime.now(timezone.utc).isoformat()
    changed = False
    for r in rems:
        if r.get("fired"):
            continue
        if (r.get("due_ts") or "") > now:
            continue
        r["fired"] = True
        r["fired_ts"] = now
        changed = True
        try:
            await self.emit("rooms:reminder_fired", {
                "id": r["id"], "room_id": r["room_id"],
                "note": r.get("note", ""),
            })
        except Exception:
            pass
    if changed:
        self._save_reminders(rems)


def _register_reminder_cron(self) -> None:
    """Per-minute job that fires due reminders. Fail-soft via BaseApp."""
    self.add_cron_job(
        "rooms:reminders-tick",
        self._fire_due_reminders,
        interval_seconds=60,
    )


def _schedule_job_id(self, room_id: str) -> str:
    return f"rooms:schedule:{room_id}"


def _register_room_schedules(self) -> None:
    """Walk every room, drop any prior schedule job, re-add for rooms
    that have an enabled cron. Idempotent — safe to re-run after a
    schedule edit so the new trigger takes effect now."""
    for room in self._list_agents():
        jid = self._schedule_job_id(room["id"])
        self.remove_cron_job(jid)  # benign no-op if not registered
        schedule = room.get("schedule") or {}
        if not schedule.get("enabled") or not schedule.get("cron"):
            continue
        rid = room["id"]
        async def _wrapper(rid=rid):
            try:
                await self._fire_room_schedule(rid)
            except Exception:
                pass
        self.add_cron_job(jid, _wrapper, cron=schedule["cron"])


SCHEDULE_SYSTEM_SUFFIX = (
    "\n\nYou are running a scheduled check-in. The user hasn't said "
    "anything yet — open the conversation. Keep it to one short paragraph "
    "(≤3 sentences). No greeting like 'Hi there' — get straight to the "
    "substance of what was asked. Don't pad. Don't sign off."
)


async def _fire_room_schedule(self, room_id: str) -> None:
    """Cron tick — generate a self-initiated message and append to the
    room's history. Bumps unread state for the user."""
    room = self._load_agent(room_id)
    if not room:
        return
    schedule = room.get("schedule") or {}
    prompt = (schedule.get("prompt") or "").strip()
    if not prompt:
        return
    # Resolve responder: 1:1 = the room itself, group = first agent.
    parts = self._normalize_participants(room)
    agent_parts = [p for p in parts if p.get("type") == "agent"]
    if agent_parts and agent_parts[0]["id"] != room["id"]:
        responder = self._load_agent(agent_parts[0]["id"]) or room
    else:
        responder = room
    kind = self._room_kind(room)
    system = self._build_system(responder) + self.SCHEDULE_SYSTEM_SUFFIX
    kwargs: dict = {"system": system, "domain": "text"}
    if responder.get("model"):
        kwargs["model"] = responder["model"]
    if responder.get("temperature") is not None:
        kwargs["temperature"] = responder["temperature"]
    if responder.get("effort"):
        kwargs["effort"] = responder["effort"]
    try:
        text = await self.think(prompt, **kwargs)
    except Exception as e:
        text = f"(scheduled check-in failed: {e!s:.200s})"
    # Route [DO:] tokens through the action pipeline so gate_mode applies.
    # In "gate" mode they land as pending cards; in "auto" they execute
    # against the agent's allowlist; absent both, the call is a no-op
    # that just returns the cleaned text.
    server_results: list[dict] = []
    try:
        text, server_results = await self._execute_server_actions(
            text or "", responder, room_id=room_id,
        )
    except Exception:
        # Never let an action-processing failure swallow the scheduled
        # fire — the message itself still goes to history.
        pass
    history = self._load_history(room_id)
    now = datetime.now(timezone.utc).isoformat()
    msg: dict = {
        "role": "assistant",
        "text": (text or "").strip(),
        "ts": now,
        "scheduled": True,
    }
    if server_results:
        msg["server_results"] = server_results
    if kind == "group":
        msg["actor"] = {"type": "agent", "id": responder["id"]}
    history.append(msg)
    self._save_history(room_id, history)
    # Stamp last_fired so the UI can show "next scheduled fire" math.
    schedule["last_fired"] = now
    room["schedule"] = schedule
    self._save_agent(room)
    await self.emit("rooms:scheduled_fired", {
        "room_id": room_id, "responder_id": responder["id"],
    })


async def set_schedule(self, room_id: str, cron: str, prompt: str,
                       enabled: bool = True) -> dict:
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    cron = (cron or "").strip()
    prompt = (prompt or "").strip()
    if not cron or not prompt:
        return {"error": "cron and prompt required"}
    # Validate cron expression so we don't silently lose a typo.
    try:
        CronTrigger.from_crontab(cron)
    except Exception as e:
        return {"error": f"invalid cron: {e!s:.120s}"}
    room["schedule"] = {
        "cron": cron, "prompt": prompt, "enabled": bool(enabled),
        "last_fired": room.get("schedule", {}).get("last_fired"),
    }
    self._save_agent(room)
    # Re-register all schedules so the new/edited one takes effect now.
    self._register_room_schedules()
    return {"ok": True, "schedule": room["schedule"]}


async def clear_schedule(self, room_id: str) -> dict:
    room = self._load_agent(room_id)
    if not room:
        return {"error": "room not found"}
    room.pop("schedule", None)
    self._save_agent(room)
    self._register_room_schedules()
    return {"ok": True}


@web_route("POST", "/api/rooms/{room_id}/schedule")
async def api_set_schedule(self, request):
    room_id = request.path_params["room_id"]
    data = await self.safe_json(request)
    return await self.set_schedule(
        room_id,
        (data.get("cron") or "").strip(),
        (data.get("prompt") or "").strip(),
        bool(data.get("enabled", True)),
    )


@web_route("DELETE", "/api/rooms/{room_id}/schedule")
async def api_clear_schedule(self, request):
    return await self.clear_schedule(request.path_params["room_id"])


@web_route("POST", "/api/rooms/{room_id}/fire-schedule")
async def api_fire_schedule_now(self, request):
    """Manual fire — useful for testing without waiting for the cron."""
    room_id = request.path_params["room_id"]
    if not self._load_agent(room_id):
        return {"error": "room not found"}
    await self._fire_room_schedule(room_id)
    return {"ok": True}


@web_route("POST", "/api/rooms/{room_id}/remind")
async def api_add_reminder(self, request):
    room_id = request.path_params["room_id"]
    data = await self.safe_json(request)
    return await self.add_reminder(
        room_id=room_id,
        due_ts=(data.get("due_ts") or "").strip(),
        note=(data.get("note") or "").strip(),
    )


@web_route("GET", "/api/reminders")
async def api_list_reminders(self, request):
    room = request.query_params.get("room", "")
    include_fired = request.query_params.get("include_fired") == "1"
    return self.list_reminders(room_id=room, include_fired=include_fired)


@web_route("DELETE", "/api/reminders/{reminder_id}")
async def api_remove_reminder(self, request):
    return await self.remove_reminder(request.path_params["reminder_id"])
