"""Rooms — single-agent and (in v2) multi-participant conversation rooms.

Each room has a system prompt, optional knowledge files, and persistent
history. Renamed from `gpts` 2026-05-09; the v1 surface is structurally
identical (one agent per room = a 1:1 chat), with multi-participant +
CLI-participant support landing in subsequent phases.

Today's surface: named agents with custom system prompts, attached vault
knowledge files for context, and chat with persistent per-agent history.

Rooms is the universal AI service layer: page-assistant, assistant UI,
and other apps all consume room agents. Each agent has:
  - Persona (system_prompt)
  - Knowledge (files + dirs, cached 5min)
  - Dynamic context (page metrics, live data — injected per request)
  - Client actions (described to LLM, output as [BUTTON:] for browser)
  - Server actions (described to LLM, output as [DO:] → call_app() on backend)
  - History (per-agent JSON, last 200 msgs)
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, ndjson_response, web_route
from emptyos.sdk.utils import parse_llm_json


class RoomsApp(BaseApp):

    async def setup(self):
        await super().setup()
        self._migrate_legacy_gpts_data()
        self._sync_general_assistant_actions()
        self._register_reminder_cron()
        self._register_room_schedules()

    def _migrate_legacy_gpts_data(self):
        """Move per-machine state from `data/apps/gpts/` to `data/apps/rooms/`
        on first boot after the rename. Lossless one-shot — leaves the source
        directory renamed to `gpts.migrated-<ts>/` for verification, doesn't
        delete it. No-op if there's nothing to migrate.

        Lives inline (not in a separate _migrate_gpts.py) so it runs deterministically
        before any data_dir read from setup() / chat() / list_agents().
        """
        from datetime import datetime, timezone
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

    # --- paths ---

    def _agents_dir(self) -> Path:
        d = self.data_dir / "agents"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _history_dir(self) -> Path:
        d = self.data_dir / "history"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _actions_log_path(self) -> Path:
        return self.data_dir / "actions.jsonl"

    def _pending_dir(self) -> Path:
        d = self.data_dir / "pending"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- Snippet library (Phase 21) ---
    #
    # Reusable prompt fragments. Single-user system → flat dict keyed by
    # name. Stored as `{name: {body, created, used_count}}` so we can sort
    # by usage frequency in the modal listing.

    def _snippets_path(self) -> Path:
        return self.data_dir / "snippets.json"

    def _load_snippets(self) -> dict:
        p = self._snippets_path()
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_snippets(self, snippets: dict) -> None:
        try:
            self._snippets_path().write_text(
                json.dumps(snippets, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    async def add_snippet(self, name: str, body: str) -> dict:
        name = (name or "").strip().lower()
        body = (body or "").strip()
        if not name or not body:
            return {"error": "name and body required"}
        # Lowercase, alphanumeric+dash only — keeps slash-recall predictable.
        if not re.match(r"^[a-z0-9][a-z0-9_\-]*$", name):
            return {"error": "name must be lowercase alphanumeric (with - or _)"}
        snippets = self._load_snippets()
        existed = name in snippets
        snippets[name] = {
            "body": body,
            "created": (snippets.get(name, {}).get("created")
                        or datetime.now(timezone.utc).isoformat()),
            "updated": datetime.now(timezone.utc).isoformat(),
            "used_count": snippets.get(name, {}).get("used_count", 0),
        }
        self._save_snippets(snippets)
        return {"ok": True, "name": name, "updated": existed}

    async def remove_snippet(self, name: str) -> dict:
        snippets = self._load_snippets()
        if name not in snippets:
            return {"error": "snippet not found"}
        del snippets[name]
        self._save_snippets(snippets)
        return {"ok": True, "name": name}

    def list_snippets(self) -> list[dict]:
        snippets = self._load_snippets()
        out = []
        for name, s in snippets.items():
            out.append({
                "name": name,
                "body": s.get("body", ""),
                "created": s.get("created", ""),
                "updated": s.get("updated", ""),
                "used_count": s.get("used_count", 0),
            })
        # Most-used first, then most-recently-updated.
        out.sort(key=lambda s: (-s["used_count"], s["updated"]), reverse=False)
        return out

    async def get_snippet(self, name: str) -> dict:
        snippets = self._load_snippets()
        s = snippets.get((name or "").strip().lower())
        if not s:
            return {"error": "not found"}
        # Bump usage counter on retrieval so the listing reflects what's hot.
        s["used_count"] = int(s.get("used_count", 0)) + 1
        self._save_snippets(snippets)
        return {"name": name, "body": s.get("body", ""),
                "used_count": s["used_count"]}

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
        """Register a per-minute job that fires due reminders. No-op if the
        scheduler service is missing."""
        sched = getattr(self.kernel, "scheduler", None)
        if not sched or not getattr(sched, "_scheduler", None):
            return
        try:
            from apscheduler.triggers.interval import IntervalTrigger
            sched._scheduler.add_job(
                self._fire_due_reminders,
                trigger=IntervalTrigger(seconds=60),
                id="rooms:reminders-tick",
                replace_existing=True,
            )
        except Exception:
            pass

    # --- Scheduled room check-ins (Phase 22) ---
    #
    # Each room can carry a `schedule = {cron, prompt, enabled, last_fired}`.
    # On boot, _register_room_schedules walks every room and adds an
    # APScheduler job per enabled schedule. On fire, the agent generates a
    # self-initiated message — appended to history as a normal assistant
    # turn so the unread/catch-up plumbing already handles surfacing it.

    def _schedule_job_id(self, room_id: str) -> str:
        return f"rooms:schedule:{room_id}"

    def _register_room_schedules(self) -> None:
        sched = getattr(self.kernel, "scheduler", None)
        if not sched or not getattr(sched, "_scheduler", None):
            return
        from apscheduler.triggers.cron import CronTrigger
        # Wipe any previously-registered job ids before re-registering — so
        # cron edits don't leave dangling old triggers in the scheduler.
        for room in self._list_agents():
            jid = self._schedule_job_id(room["id"])
            try: sched._scheduler.remove_job(jid)
            except Exception: pass
            schedule = room.get("schedule") or {}
            if not schedule.get("enabled") or not schedule.get("cron"):
                continue
            try:
                trigger = CronTrigger.from_crontab(schedule["cron"])
            except Exception:
                continue
            rid = room["id"]
            async def _wrapper(rid=rid):
                try:
                    await self._fire_room_schedule(rid)
                except Exception:
                    pass
            try:
                sched._scheduler.add_job(
                    _wrapper, trigger=trigger, id=jid, replace_existing=True,
                )
            except Exception:
                pass

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
        history = self._load_history(room_id)
        now = datetime.now(timezone.utc).isoformat()
        msg: dict = {
            "role": "assistant",
            "text": (text or "").strip(),
            "ts": now,
            "scheduled": True,
        }
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
            from apscheduler.triggers.cron import CronTrigger
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

    def _visits_path(self) -> Path:
        return self.data_dir / "visits.json"

    def _load_visits(self) -> dict:
        """{room_id → ISO timestamp of last visit}. Single-user system, so
        no per-user split — keeps the file small + the read trivial."""
        p = self._visits_path()
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_visits(self, visits: dict) -> None:
        try:
            self._visits_path().write_text(
                json.dumps(visits, indent=2), encoding="utf-8",
            )
        except Exception:
            pass

    def mark_visited(self, room_id: str) -> dict:
        """Stamp room_id with the current timestamp. Idempotent — fine to
        call from every openChat."""
        if not room_id:
            return {"error": "room_id required"}
        visits = self._load_visits()
        visits[room_id] = datetime.now(timezone.utc).isoformat()
        self._save_visits(visits)
        return {"ok": True, "room_id": room_id, "visited": visits[room_id]}

    def get_visits(self) -> dict:
        """Return the full {room_id → last_visited_ts} map. Used by the
        sidebar to compute unread state in one fetch."""
        return self._load_visits()

    def get_unread(self) -> dict:
        """Return {room_id: {count, last_ts}} for rooms with new messages
        since their last visit. Skips rooms whose latest message was from
        the user (they sent it — they know it's there).

        Single-pass scan of history JSONs; cheap because each file is
        capped at 200 messages.
        """
        visits = self._load_visits()
        out: dict = {}
        history_dir = self.data_dir / "history"
        if not history_dir.exists():
            return out
        for f in history_dir.glob("*.json"):
            room_id = f.stem
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            messages = data.get("messages", []) if isinstance(data, dict) else []
            if not isinstance(messages, list) or not messages:
                continue
            last_visited = visits.get(room_id, "")
            # Last message must be a non-user one and newer than last_visited.
            last = messages[-1]
            if last.get("role") == "user":
                continue
            last_ts = last.get("ts", "")
            if not last_ts or (last_visited and last_ts <= last_visited):
                continue
            # Count non-user messages newer than last_visited.
            count = 0
            for m in messages:
                if m.get("role") == "user":
                    continue
                ts = m.get("ts", "")
                if not last_visited or ts > last_visited:
                    count += 1
            if count > 0:
                out[room_id] = {"count": count, "last_ts": last_ts}
        return out

    def _pending_path(self, action_id: str) -> Path:
        return self._pending_dir() / f"{action_id}.json"

    def _save_pending(self, action: dict) -> None:
        self._pending_path(action["id"]).write_text(
            json.dumps(action, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    def _load_pending(self, action_id: str) -> dict | None:
        p = self._pending_path(action_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _lookup_inverse(self, app_id: str, method: str) -> str:
        """Return the declared inverse method for app_id.method, or '' if none."""
        manifest = self.kernel.apps.manifests.get(app_id)
        if not manifest:
            return ""
        for cmd in manifest.provides.get("assistant", {}).get("commands", []):
            if cmd.get("method") == method:
                return cmd.get("inverse", "") or ""
        return ""

    def _method_signature(self, app_id: str, method: str) -> str:
        """Introspect the real method signature so the LLM doesn't invent params."""
        import inspect
        try:
            inst = self.kernel.apps.instances.get(app_id)
            if not inst:
                return "()"
            fn = getattr(inst, method, None)
            if not callable(fn):
                return "()"
            params: list[str] = []
            for name, p in inspect.signature(fn).parameters.items():
                if name in ("self", "request", "cls"):
                    continue
                if p.default is inspect.Parameter.empty:
                    params.append(name)
                else:
                    params.append(f"{name}={p.default!r}")
            return "(" + ", ".join(params) + ")"
        except Exception:
            return "()"

    # --- agent CRUD helpers ---

    def _agent_path(self, agent_id: str) -> Path:
        return self._agents_dir() / f"{agent_id}.json"

    def _history_path(self, agent_id: str) -> Path:
        return self._history_dir() / f"{agent_id}.json"

    def _load_agent(self, agent_id: str) -> dict | None:
        p = self._agent_path(agent_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_agent(self, agent: dict):
        self._agent_path(agent["id"]).write_text(
            json.dumps(agent, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.kernel.agents.invalidate(agent["id"])

    def _list_agents(self) -> list[dict]:
        agents = []
        for f in sorted(self._agents_dir().glob("*.json")):
            try:
                agents.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return agents

    def _find_agent_by_name(self, name: str) -> dict | None:
        q = name.lower()
        for a in self._list_agents():
            if a["name"].lower() == q:
                return a
        return None

    # --- history helpers ---

    def _load_history(self, agent_id: str) -> list[dict]:
        p = self._history_path(agent_id)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("messages", [])
        except Exception:
            return []

    def _save_history(self, agent_id: str, messages: list[dict]):
        # Cap history to last 200 messages to prevent unbounded growth
        if len(messages) > 200:
            messages = messages[-200:]
        self._history_path(agent_id).write_text(
            json.dumps({"agent_id": agent_id, "messages": messages},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # Stamp the room with the latest message timestamp so the sidebar can
        # sort by recency without scanning every history file. Cheap because
        # save_history is the only write path that ever changes activity.
        if messages:
            try:
                room = self._load_agent(agent_id)
                if room and not room.get("builtin_skip_stamp"):
                    last_ts = messages[-1].get("ts") or ""
                    if last_ts and room.get("last_msg_ts") != last_ts:
                        room["last_msg_ts"] = last_ts
                        self._save_agent(room)
            except Exception:
                pass

    # --- room/participant helpers (Phase 2 multi-participant) ---
    #
    # A "room" is the surface concept; storage stays at agents/<id>.json.
    # Legacy 1:1 records (one persona = one room) get participants synthesised
    # on read. Group rooms are stored as records with `participants` listing
    # multiple agent ids and `tier="group"` so the agent picker can hide them.

    def _normalize_participants(self, room: dict) -> list[dict]:
        """Return the room's participant list, deriving 1:1 shape for legacy
        records that don't carry one yet.

        Every user participant gets a stable `id` ("me" by default) so the UI
        can display the user as a peer in the member list and so prompts can
        attribute user turns by id rather than the bare role string.
        """
        parts = room.get("participants")
        if isinstance(parts, list) and parts:
            out = []
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "user" and not p.get("id"):
                    p = {**p, "id": "me"}
                out.append(p)
            return out
        return [{"type": "user", "id": "me"}, {"type": "agent", "id": room["id"]}]

    def _room_kind(self, room: dict) -> str:
        """1on1 vs group, derived from participants. Cached on the record
        only when caller chooses to persist; reads are pure."""
        parts = self._normalize_participants(room)
        agents = [p for p in parts if p.get("type") == "agent"]
        return "group" if len(agents) > 1 else "1on1"

    def _resolve_responder_id(self, text: str, agent_parts: list[dict]) -> str | None:
        """Pick which participant agent should respond to *text*.

        - 0 agents → None.
        - 1 agent → that one (no @mention parsing needed).
        - >1 agents → scan `text` for `@<id>` or `@<name>` (case-insensitive,
          dashes/spaces interchangeable). Falls back to the first agent in
          the participant list if no match.
        """
        if not agent_parts:
            return None
        if len(agent_parts) == 1:
            return agent_parts[0].get("id")
        for m in re.finditer(r"@([A-Za-z0-9_\-]+)", text or ""):
            mention = m.group(1).strip().lower()
            for p in agent_parts:
                pid = (p.get("id") or "").lower()
                if pid == mention or pid.replace("-", "") == mention.replace("-", ""):
                    return p.get("id")
                # Also match by display name.
                a = self._load_agent(p.get("id", ""))
                if a:
                    name = (a.get("name") or "").lower()
                    if name == mention or name.replace(" ", "-") == mention:
                        return p.get("id")
        return agent_parts[0].get("id")

    def _resolve_responder(self, text: str, parts: list[dict]) -> dict | None:
        """Pick a responder participant (agent OR cli). Returns the
        participant dict so the caller can branch on `type`.

        Same @mention rules as `_resolve_responder_id` but also matches
        cli participants by id.
        """
        responders = [p for p in parts if p.get("type") in ("agent", "cli")]
        if not responders:
            return None
        if len(responders) == 1:
            return responders[0]
        for m in re.finditer(r"@([A-Za-z0-9_\-]+)", text or ""):
            mention = m.group(1).strip().lower()
            for p in responders:
                pid = (p.get("id") or "").lower()
                if pid == mention or pid.replace("-", "") == mention.replace("-", ""):
                    return p
                if p.get("type") == "agent":
                    a = self._load_agent(p["id"])
                    if a:
                        name = (a.get("name") or "").lower()
                        if name == mention or name.replace(" ", "-") == mention:
                            return p
        return responders[0]

    def _new_room_id(self, prefix: str = "room") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    # --- CLI participant dispatch (Phase 3) ---

    def _build_cli_prompt(self, room: dict, text: str, history: list[dict]) -> str:
        """Format recent room turns + current message as the CLI's `-p` arg.

        Each line is `<speaker>: <text>` so the CLI sees who said what.
        We cap to the last ~10 turns (room history is also capped to 200).
        """
        # Resolve the user's display id once (default "me"). Lets the CLI
        # see "me: ..." instead of the bare role and matches what the UI
        # shows in the member strip.
        user_id = "me"
        for p in self._normalize_participants(room):
            if p.get("type") == "user" and p.get("id"):
                user_id = p["id"]
                break

        lines = []
        for m in (history or [])[-10:]:
            actor = m.get("actor") or {}
            if actor.get("id"):
                speaker = actor["id"]
            elif m.get("role") == "user":
                speaker = user_id
            else:
                speaker = m.get("role", "assistant")
            t = (m.get("text") or "").strip()
            if t:
                lines.append(f"{speaker}: {t}")
        lines.append(f"{user_id}: {text}")
        return "\n".join(lines)

    def _build_cli_system(self, room: dict, cli_part: dict) -> str:
        """Persona/discipline framing for the CLI participant.

        The "actions go through review gate" framing is load-bearing: claude-cli
        runs with read-only tools, so any state-changing action MUST be emitted
        as a `[DO:app.method({json})]` token in the reply text. The rooms
        backend parses these tokens post-stream and surfaces them as
        Apply/Reject cards. If the model uses Edit/Write directly, the call
        will fail (tools not in --allowedTools) and the user sees nothing.
        """
        others = []
        for p in self._normalize_participants(room):
            if p.get("type") == "agent":
                others.append(p["id"])
            elif p.get("type") == "cli" and p["id"] != cli_part["id"]:
                others.append(f"{p['id']} (cli)")
        room_title = room.get("name", "this room")
        co = ", ".join(others) if others else "(none)"
        return (
            f"You are a CLI participant in a chat room titled '{room_title}'. "
            f"Other participants you can address by @id: {co}. "
            f"Reply naturally as one voice in the conversation. Be concise. "
            f"You have read-only tools (Read, Grep, Glob, WebFetch) for "
            f"investigation. For any action that would MODIFY state — adding "
            f"a task, editing a note, sending a message — emit a "
            f'[DO:app.method({{"arg":"value"}})] token inline in your reply. '
            f"The user reviews each [DO:] as a card and clicks Apply or Reject. "
            f"Never describe an action you would take and skip the token; the "
            f"user only sees what you emit. Common verbs: task.add({{text}}), "
            f"journal.add_entry({{text, mood}}), capture.add({{text}}), "
            f"note.create({{title, body}}). When unsure of the exact app or "
            f"method, still emit a best guess — failed applies surface in the "
            f"UI and the user can tell you the right one."
        )

    async def _dispatch_cli_turn(
        self, room: dict, cli_part: dict, text: str, history: list[dict]
    ) -> AsyncIterator[dict]:
        """Stream one turn from a CLI participant (e.g. claude-cli).

        Yields the same chunk shapes as the agent streaming path
        (`{text, done}`) plus optional `tool_use` / `tool_result` chunks the
        UI can render as cards. The final chunk carries `responder_id` and
        `actor_type='cli'` so the UI labels the bubble correctly.
        """
        runtime = self.service("agent-runtime")
        if runtime is None:
            yield {
                "text": "[agent-runtime plugin not loaded]",
                "done": True, "error": True,
                "responder_id": cli_part["id"], "actor_type": "cli",
            }
            return

        prompt = self._build_cli_prompt(room, text, history)
        system_prompt = self._build_cli_system(room, cli_part)
        cli_id = cli_part["id"]
        cwd = cli_part.get("cwd") or str(self.kernel.config.notes_path or Path.cwd())
        timeout_s = float(cli_part.get("timeout_s") or 600)

        # Non-claude CLIs (codex, gemini, etc.) — buffered text adapter, no
        # tool events, no streaming. The whole reply lands as one text chunk.
        if cli_id != "claude-cli":
            try:
                result = await runtime.text_cli_run(
                    cli_id=cli_id,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    cwd=cwd,
                    timeout_s=timeout_s,
                    extra_args=cli_part.get("extra_args") or None,
                )
            except Exception as e:
                result = {"error": f"{cli_id} dispatch failed: {e!s:.200s}"}
            if "error" in result:
                yield {
                    "text": f"[{result['error']}]",
                    "done": True, "error": True,
                    "responder_id": cli_id, "actor_type": "cli",
                }
                return
            text_out = result.get("text") or ""
            if text_out:
                yield {"text": text_out, "done": False}
            yield {
                "text": "", "done": True, "full": text_out,
                "responder_id": cli_id, "actor_type": "cli",
            }
            return

        # Claude-CLI path — streaming with tool events + review gate.
        allowed_tools = cli_part.get("allowed_tools") or "Read,Grep,Glob,WebFetch"
        cli_model = cli_part.get("model") or None
        cli_effort = cli_part.get("effort") or None

        # Bridge sync stdout-line callback → async generator via a queue.
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_line(raw: bytes) -> None:
            try:
                evt = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                return
            try:
                loop.call_soon_threadsafe(queue.put_nowait, evt)
            except RuntimeError:
                pass

        async def driver():
            try:
                result = await runtime.claude_cli_run(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    allowed_tools=allowed_tools,
                    cwd=cwd,
                    model=cli_model,
                    effort=cli_effort,
                    on_stdout_line=on_line,
                    timeout_s=timeout_s,
                )
                if isinstance(result, dict) and "error" in result:
                    await queue.put({"_error": result["error"]})
                else:
                    await queue.put({"_done": True})
            except Exception as e:
                await queue.put({"_error": str(e)[:200]})
            finally:
                await queue.put(None)

        task = asyncio.create_task(driver())
        full_text = ""

        try:
            while True:
                evt = await queue.get()
                if evt is None:
                    break
                if "_error" in evt:
                    msg = f"[cli error: {evt['_error']}]"
                    full_text += msg
                    yield {"text": msg, "done": False}
                    continue
                if "_done" in evt:
                    continue
                # Parse claude-cli stream-json events into normalized chunks.
                etype = evt.get("type")
                if etype == "assistant":
                    for block in evt.get("message", {}).get("content", []) or []:
                        btype = block.get("type")
                        if btype == "text":
                            t = block.get("text", "")
                            if t:
                                full_text += t
                                yield {"text": t, "done": False}
                        elif btype == "tool_use":
                            yield {
                                "tool_use": {
                                    "name": block.get("name"),
                                    "input": block.get("input"),
                                    "id": block.get("id"),
                                },
                                "done": False,
                            }
                elif etype == "user":
                    for block in evt.get("message", {}).get("content", []) or []:
                        if block.get("type") == "tool_result":
                            content = block.get("content")
                            if isinstance(content, list):
                                content = " ".join(
                                    str(c.get("text", c)) for c in content if c
                                )
                            yield {
                                "tool_result": {
                                    "tool_use_id": block.get("tool_use_id"),
                                    "content": str(content)[:500],
                                },
                                "done": False,
                            }
        finally:
            try:
                await task
            except Exception:
                pass

        yield {
            "text": "", "done": True, "full": full_text,
            "responder_id": cli_part["id"], "actor_type": "cli",
        }

    # --- Vault file references (Phase 11) ---
    #
    # Users can drop vault notes into a message via [[path]] wikilinks. The
    # picker (`:` trigger in the input) populates real paths; backend reads
    # each file and prepends its content to the LLM prompt as Knowledge.

    _WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
    _MAX_REF_BYTES = 8000  # cap per-file to keep the prompt bounded

    def _extract_wikilinks(self, text: str) -> list[str]:
        """Return distinct wikilink targets in the order they appear."""
        seen: set = set()
        out: list[str] = []
        for m in self._WIKILINK_RE.finditer(text or ""):
            ref = m.group(1).strip()
            if not ref or ref in seen:
                continue
            seen.add(ref)
            out.append(ref)
        return out

    def _resolve_wikilinks(self, text: str) -> str:
        """For each [[path]] in `text`, read the file (capped) and return a
        context block ready to prepend to the LLM prompt. Empty when there
        are no links or none resolve."""
        refs = self._extract_wikilinks(text)
        if not refs:
            return ""
        blocks: list[str] = []
        for ref in refs:
            content = ""
            try:
                content = self.vault_read(ref) or ""
            except Exception:
                content = ""
            # Try with .md suffix if the bare path wasn't found.
            if not content and not ref.endswith(".md"):
                try:
                    content = self.vault_read(ref + ".md") or ""
                except Exception:
                    pass
            if not content:
                blocks.append(f"### {ref}\n*(file not found in vault)*")
                continue
            if len(content) > self._MAX_REF_BYTES:
                content = content[: self._MAX_REF_BYTES] + "\n…(truncated)"
            blocks.append(f"### {ref}\n{content}")
        if not blocks:
            return ""
        return "User-attached vault notes:\n\n" + "\n\n".join(blocks)

    @web_route("GET", "/api/vault-search")
    async def api_vault_search(self, request):
        """Substring search over vault file names + paths via the kernel
        vault_index service. Mirrors apps/assistant/api/vault-files so the
        rooms input picker doesn't depend on the assistant app being loaded.
        """
        q = (request.query_params.get("q") or "").strip().lower()
        try:
            limit = max(1, min(50, int(request.query_params.get("limit") or 20)))
        except ValueError:
            limit = 20
        vi = self.kernel.services.get_optional("vault_index")
        if not vi:
            return {"files": []}
        entries = vi.find()
        if q:
            entries = [
                e for e in entries
                if q in e.get("name", "").lower() or q in e.get("path", "").lower()
            ]
        entries.sort(key=lambda e: e.get("modified", 0), reverse=True)
        files = [
            {"path": e.get("path", ""), "name": e.get("name", ""),
             "folder": e.get("folder", "")}
            for e in entries[:limit]
        ]
        return {"files": files}

    # --- Smart agent suggestions (Phase 27) ---
    #
    # Cheap keyword-scoring against agent system_prompts. Given a query
    # (typically the room title the user is composing), score every non-
    # group agent by how many of the query's content words appear in their
    # name + system_prompt + tools. Returns top 3.

    _STOPWORDS = {
        "the", "a", "an", "of", "to", "for", "and", "or", "with", "in",
        "on", "at", "by", "from", "is", "are", "be", "this", "that",
        "i", "me", "my", "you", "your", "we", "our", "it", "its",
        "do", "does", "did", "have", "has", "had", "will", "would",
        "can", "could", "should", "if", "but", "as", "than",
    }

    def suggest_agents(self, query: str, limit: int = 3) -> list[dict]:
        q = (query or "").strip().lower()
        if not q:
            return []
        # Tokenize: lowercase, alphanumeric chunks, drop stopwords + 1-char.
        tokens = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 1 and t not in self._STOPWORDS]
        if not tokens:
            return []
        candidates = [a for a in self._list_agents()
                      if (a.get("tier") or "user") != "group"
                      and a.get("status") != "archived"]
        scored: list[tuple[int, dict]] = []
        for a in candidates:
            haystack = " ".join([
                (a.get("name") or "").lower(),
                (a.get("system_prompt") or "").lower(),
                " ".join(a.get("tools") or []).lower(),
            ])
            score = 0
            for t in tokens:
                if t in haystack:
                    score += 1
            if score:
                scored.append((score, a))
        scored.sort(key=lambda x: (-x[0], (x[1].get("name") or "")))
        out = []
        for s, a in scored[:limit]:
            out.append({
                "id": a["id"], "name": a.get("name", a["id"]),
                "tier": a.get("tier", "user"),
                "system_prompt": (a.get("system_prompt") or "")[:140],
                "score": s,
            })
        return out

    @web_route("GET", "/api/suggest-agents")
    async def api_suggest_agents(self, request):
        q = request.query_params.get("q", "")
        try:
            limit = max(1, min(10, int(request.query_params.get("limit") or 3)))
        except ValueError:
            limit = 3
        return self.suggest_agents(q, limit=limit)

    # --- Cross-room message search (Phase 7b) ---

    def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        """Scan every saved thread for `query` (case-insensitive substring),
        return matching message hits with a snippet + room context. Order:
        most recent first by message timestamp.

        Cheap O(n_messages) scan against the JSON files; fine for the 200-msg
        cap per room. If it ever grows, swap to an index — but keep this as
        the fallback so it works against fresh installs.
        """
        q = (query or "").strip().lower()
        if len(q) < 2:
            return []
        history_dir = self.data_dir / "history"
        if not history_dir.exists():
            return []
        out: list[dict] = []
        for f in history_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            messages = data.get("messages", []) if isinstance(data, dict) else []
            if not isinstance(messages, list):
                continue
            room_id = f.stem
            room = self._load_agent(room_id)
            room_name = room.get("name", room_id) if room else room_id
            kind = self._room_kind(room) if room else "1on1"
            for m in messages:
                text = (m.get("text") or "")
                if not text or q not in text.lower():
                    continue
                # Build a 140-char snippet centred on the match.
                idx = text.lower().find(q)
                start = max(0, idx - 40)
                end = min(len(text), idx + len(q) + 100)
                snippet = ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
                actor = m.get("actor") or {}
                speaker = actor.get("id") or m.get("role", "user")
                out.append({
                    "room_id": room_id,
                    "room_name": room_name,
                    "kind": kind,
                    "speaker": speaker,
                    "ts": m.get("ts", ""),
                    "snippet": snippet,
                })
        out.sort(key=lambda r: r.get("ts", ""), reverse=True)
        return out[:limit]

    # --- Hub panel contributions (Phase 7a) ---

    async def panel_pending_count(self) -> dict | None:
        """Stat-tile: total pending [DO:] actions across every room. Drops
        silently when there are zero so the hub stays uncluttered."""
        try:
            pending = self.list_pending(room_id="", status="pending")
        except Exception:
            return None
        if not pending:
            return None
        return {
            "label": "Pending",
            "value": str(len(pending)),
            "href": "/rooms/",
        }

    async def panel_recent_rooms(self) -> list[dict] | None:
        """Plain-list: rooms sorted by most-recent activity (history mtime).
        Each row links into the room. Limited to ~5 by the manifest cap.
        """
        history_dir = self.data_dir / "history"
        if not history_dir.exists():
            return None
        rows = []
        for f in history_dir.glob("*.json"):
            try:
                mtime = f.stat().st_mtime
            except Exception:
                continue
            room_id = f.stem
            room = self._load_agent(room_id)
            if not room:
                continue
            rows.append({"id": room_id, "name": room.get("name", room_id), "mtime": mtime,
                         "kind": self._room_kind(room)})
        if not rows:
            return None
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        out = []
        for r in rows[:5]:
            icon = "👥 " if r["kind"] == "group" else ""
            out.append({
                "text": icon + r["name"],
                "href": "/rooms/#" + r["id"],
            })
        return out

    # --- Voice intents (Phase 7c) ---
    #
    # Verbs Aura can fire. Returned shapes follow voice-intents.md:
    # `say` is the spoken reply; `card` (renderer + data) is shown alongside.

    async def voice_list_rooms(self) -> dict:
        """List recently-active rooms, top 5. Card: plain-list."""
        history_dir = self.data_dir / "history"
        rows: list[tuple[float, dict]] = []
        if history_dir.exists():
            for f in history_dir.glob("*.json"):
                try:
                    mtime = f.stat().st_mtime
                except Exception:
                    continue
                room = self._load_agent(f.stem)
                if not room:
                    continue
                rows.append((mtime, room))
        rows.sort(key=lambda x: x[0], reverse=True)
        top = rows[:5]
        if not top:
            return {"say": "No rooms with activity yet."}
        names = [r["name"] for _, r in top]
        say = "Recent rooms: " + ", ".join(names[:3]) + (
            f", and {len(top) - 3} more." if len(top) > 3 else "."
        )
        card_data = [
            {"text": ("👥 " if self._room_kind(r) == "group" else "") + r["name"]}
            for _, r in top
        ]
        return {"say": say, "card": {"renderer": "plain-list", "data": card_data}}

    async def voice_open_room(self, name: str = "") -> dict:
        """Find a room whose name contains *name* (case-insensitive). When
        the match is unambiguous, the card carries the room URL the UI can
        navigate to. Otherwise, list candidates so the user can be specific."""
        q = (name or "").strip().lower()
        if not q:
            return {"say": "Which room?"}
        matches = [r for r in self._list_agents()
                   if q in (r.get("name") or "").lower() or q in (r.get("id") or "").lower()]
        if not matches:
            return {"say": f"No room matching '{name}'."}
        if len(matches) == 1:
            r = matches[0]
            return {
                "say": f"Opening {r.get('name', r['id'])}.",
                "card": {
                    "renderer": "entity-card",
                    "data": {
                        "title": r.get("name", r["id"]),
                        "subtitle": self._room_kind(r),
                        "fields": [{"label": "Open", "value": "/rooms/#" + r["id"]}],
                    },
                },
            }
        # Disambiguate.
        names = [r.get("name", r["id"]) for r in matches[:5]]
        return {
            "say": f"Found {len(matches)} rooms matching '{name}': {', '.join(names[:3])}. Be more specific.",
            "card": {
                "renderer": "plain-list",
                "data": [{"text": n} for n in names],
            },
        }

    # --- public API for cross-app calls (use via self.call_app("rooms", ...)) ---

    async def chat(self, agent_id: str, text: str, context: str = "",
                   client_actions: list[dict] | None = None) -> dict:
        """Send a chat turn to an agent and return the response dict.

        Public wrapper around the internal chat pipeline so other apps can
        call `await self.call_app("rooms", "chat", agent_id=..., text=...)`.
        """
        return await self._chat(agent_id, text, context=context, client_actions=client_actions)

    def get_agent(self, agent_id: str) -> dict | None:
        """Load and return an agent record, or None if it doesn't exist."""
        return self._load_agent(agent_id)

    def list_agents(self, tier: str | None = None) -> list[dict]:
        """List all agents, optionally filtered by tier."""
        agents = self._list_agents()
        if tier is not None:
            agents = [a for a in agents if a.get("tier") == tier]
        return agents

    def save_agent(self, agent: dict) -> dict:
        """Persist an agent record. Returns the saved agent."""
        self._save_agent(agent)
        return agent

    def has_agent(self, agent_id: str) -> bool:
        return self._load_agent(agent_id) is not None

    # --- chat logic ---

    def _build_system(self, agent: dict, client_actions: list[dict] | None = None) -> str:
        """Build system prompt: persona + action instructions."""
        system = agent.get("system_prompt", "You are a helpful assistant.")

        action_lines = []
        # Client actions — LLM outputs [BUTTON:label|action(param)] for browser execution
        if client_actions:
            action_lines.append("Available client actions (output [BUTTON:label|action(param)] for user to click, or [ACTION:name(param)] to auto-execute):")
            for a in client_actions:
                params = ", ".join(a.get("params", []))
                action_lines.append(f"- {a['name']}({params}): {a.get('description', '')}")

        # Server actions — LLM outputs [DO:app.method(json_args)] for backend execution
        server_actions = agent.get("server_actions", {})
        if server_actions:
            action_lines.append("Available server actions (output [DO:app.method({\"arg\":\"value\"})] to execute on the server). Use ONLY parameters listed in each signature — do not invent parameter names:")
            for app_id, methods in server_actions.items():
                for method in methods:
                    sig = self._method_signature(app_id, method)
                    action_lines.append(f"- {app_id}.{method}{sig}")

        if action_lines:
            system += "\n\n" + "\n".join(action_lines)
            system += (
                "\n\nBe concise. Answer in the user's language. "
                "When the user asks you to do something, act via [DO:] or [BUTTON:] — "
                "do not describe what you would do in prose. "
                "Emit each [DO:] tag only once per response; do not repeat the same "
                "call with the same args."
            )

        return system

    def _build_prompt(self, agent: dict, text: str, context: str = "",
                      history: list[dict] | None = None) -> str:
        """Build user prompt: knowledge + context + history + message.

        Sync path. Stuffs the whole knowledge blob (capped per-file by
        load_knowledge). Used when no query is available or embeddings
        aren't usable. The async _build_prompt_async path swaps to
        embedding retrieval when both conditions are met.
        """
        knowledge_text = self.kernel.agents.load_knowledge(agent)
        return self._assemble_prompt(text, knowledge_text, context, history)

    async def _build_prompt_async(self, agent: dict, text: str, context: str = "",
                                  history: list[dict] | None = None) -> str:
        """Embedding-aware prompt build. Falls back to the sync path when
        embeddings unavailable or the agent has no chunkable knowledge.

        Multi-turn aware: the embedding query is built from `history` + `text`
        so a follow-up like "what about the second one?" still retrieves the
        right knowledge chunks.
        """
        if not text or not text.strip() or not self.embeddings_available:
            return self._build_prompt(agent, text, context, history)
        try:
            chunks = self.kernel.agents.load_knowledge_chunks(agent)
            if not chunks:
                return self._build_prompt(agent, text, context, history)
            top_k = int(agent.get("knowledge_top_k", 6))
            from emptyos.sdk.embeddings import build_retrieval_query

            # Room history items are {role, text}; map to {role, content}.
            hist_dicts = [
                {"role": m.get("role", ""), "content": m.get("text", "")}
                for m in (history or [])
                if m.get("text")
            ]
            retrieval_query = build_retrieval_query(hist_dicts, text)
            index = await self.embedding_index(chunks, text_fn=lambda it: it["text"])
            hits = await index.search(retrieval_query, top_k=top_k, min_score=0.30)
            if not hits:
                # No confident match — show nothing rather than misleading context.
                knowledge_text = ""
            else:
                blocks = []
                for it, _score in hits:
                    blocks.append(f"### {it['source']}\n{it['text']}")
                knowledge_text = "\n\n".join(blocks)
            return self._assemble_prompt(text, knowledge_text, context, history)
        except Exception:
            return self._build_prompt(agent, text, context, history)

    @staticmethod
    def _assemble_prompt(text: str, knowledge_text: str, context: str,
                         history: list[dict] | None) -> str:
        parts = []
        if knowledge_text:
            parts.append("Knowledge context:\n" + knowledge_text)
        if context:
            parts.append("Live context:\n" + context)
        recent = (history or [])[-20:]
        if recent:
            convo = "\n".join(f"{m['role']}: {m['text']}" for m in recent)
            parts.append(f"Conversation so far:\n{convo}")
        parts.append(f"user: {text}")
        return "\n\n".join(parts)

    async def _execute_server_actions(self, response: str, agent: dict) -> tuple[str, list[dict]]:
        """Parse [DO:app.method(json_args)] from response, execute, return cleaned text + results."""
        server_actions = agent.get("server_actions", {})
        if not server_actions:
            return response, []

        pattern = re.compile(r'\[DO:(\w+)\.(\w+)\((\{.*?\})\)\]', re.DOTALL)
        results = []
        cleaned = response

        for match in pattern.finditer(response):
            app_id, method, args_str = match.group(1), match.group(2), match.group(3)
            # Validate against allowlist
            allowed_methods = server_actions.get(app_id, [])
            if method not in allowed_methods:
                results.append({"app": app_id, "method": method, "error": "not allowed", "ok": False})
                continue
            try:
                args = parse_llm_json(args_str, fallback={})
                res = await self.call_app(app_id, method, **args)
                inverse = self._lookup_inverse(app_id, method)
                entry = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "app": app_id,
                    "method": method,
                    "args": args,
                    "result": str(res)[:500],
                    "inverse": inverse,
                    "reversed": False,
                }
                try:
                    with self._actions_log_path().open("a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                results.append({
                    "app": app_id, "method": method, "result": str(res)[:300],
                    "ok": True, "reversible": bool(inverse),
                })
            except Exception as e:
                results.append({"app": app_id, "method": method, "error": str(e)[:200], "ok": False})

        # Strip [DO:] tags from response text sent to client
        cleaned = pattern.sub("", cleaned).strip()
        return cleaned, results

    @staticmethod
    def _summarize_server_actions(results: list[dict]) -> str:
        """Fallback text when the LLM emits only [DO:] tags and nothing else.

        For reads (no inverse), include the result payload so the user sees data.
        For writes, a checkmark is enough — the server_results block below shows it
        and the Undo button covers reversibility.
        """
        if not results:
            return ""
        lines = []
        for r in results:
            label = f"{r.get('app', '?')}.{r.get('method', '?')}"
            if r.get("ok"):
                result = (r.get("result") or "").strip()
                if result and result not in ("None", "null", "[]", "{}"):
                    lines.append(f"**{label}**\n{result}")
                else:
                    lines.append(f"✓ {label}")
            else:
                lines.append(f"✗ {label} — {r.get('error', 'failed')}")
        return "\n\n".join(lines)

    # --- Review gate (Phase 5) ---
    #
    # CLI participants emit [DO:app.method({json})] tokens for actions that
    # would modify state — same grammar agents use, but routed through the
    # gate instead of executing. The user reviews each as a card and clicks
    # Apply or Reject. Agent participants keep their auto-execute path with
    # the existing undo mechanism — no UX regression for page-assistant.

    async def _gate_server_actions(
        self, response: str, *, room_id: str, source_actor: dict,
    ) -> tuple[str, list[dict]]:
        """Parse [DO:] tokens from `response`, save each as a pending action,
        return cleaned text + the saved entries. No execution, no allowlist —
        the user is the gate, and apply-time errors surface in the UI.
        """
        pattern = re.compile(r'\[DO:(\w+)\.(\w+)\((\{.*?\})\)\]', re.DOTALL)
        pending: list[dict] = []
        for match in pattern.finditer(response):
            app_id, method, args_str = match.group(1), match.group(2), match.group(3)
            try:
                args = parse_llm_json(args_str, fallback={})
            except Exception:
                args = {}
            action = {
                "id": f"act-{uuid.uuid4().hex[:10]}",
                "room_id": room_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "source_actor": source_actor,
                "app": app_id,
                "method": method,
                "args": args,
                "status": "pending",
            }
            self._save_pending(action)
            pending.append(action)
        cleaned = pattern.sub("", response).strip()
        return cleaned, pending

    def list_pending(self, room_id: str = "", status: str = "pending") -> list[dict]:
        """List pending actions, optionally filtered by room and status.
        Sorted by timestamp ascending."""
        out: list[dict] = []
        for f in self._pending_dir().glob("act-*.json"):
            try:
                a = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if room_id and a.get("room_id") != room_id:
                continue
            if status and a.get("status") != status:
                continue
            out.append(a)
        out.sort(key=lambda x: x.get("ts", ""))
        return out

    async def apply_pending(self, action_id: str) -> dict:
        """Execute a pending action via call_app, mark it applied."""
        action = self._load_pending(action_id)
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
            self._save_pending(action)
            return {"error": str(e)[:200], "action": action}
        action["status"] = "applied"
        action["result"] = str(result)[:500]
        action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
        self._save_pending(action)
        await self.emit("rooms:action_applied", {
            "action_id": action_id, "room_id": action.get("room_id"),
            "app": action["app"], "method": action["method"],
        })
        return action

    async def reject_pending(self, action_id: str) -> dict:
        """Mark a pending action rejected without executing."""
        action = self._load_pending(action_id)
        if not action:
            return {"error": "action not found"}
        if action.get("status") != "pending":
            return {"error": f"already {action.get('status')}"}
        action["status"] = "rejected"
        action["resolved_ts"] = datetime.now(timezone.utc).isoformat()
        self._save_pending(action)
        await self.emit("rooms:action_rejected", {
            "action_id": action_id, "room_id": action.get("room_id"),
        })
        return action

    async def _chat(self, agent_id: str, text: str,
                    context: str = "", client_actions: list[dict] | None = None) -> dict:
        # `agent_id` is the room id (1:1 rooms are stored under their agent's
        # own id; group rooms have a generated id).
        room = self._load_agent(agent_id)
        if not room:
            return {"response": f"Room '{agent_id}' not found.", "agent_id": agent_id}

        parts = self._normalize_participants(room)
        agent_parts = [p for p in parts if p.get("type") == "agent"]
        kind = self._room_kind(room)

        # Resolve which participant agent responds this turn.
        responder_id = self._resolve_responder_id(text, agent_parts)
        if responder_id and responder_id != room["id"]:
            responder = self._load_agent(responder_id)
            if not responder:
                return {
                    "response": f"Participant '{responder_id}' not found.",
                    "agent_id": agent_id,
                }
        else:
            # 1:1 fallback — the room record IS the agent persona.
            responder = room

        history = self._load_history(agent_id)
        system = self._build_system(responder, client_actions)
        # Phase 11 — resolve [[wikilink]] vault refs in the user message.
        ref_block = self._resolve_wikilinks(text)
        # Phase 26 — prepend the room's memory block when present.
        mem_block = self._memory_block(room)
        merged_context = "\n\n".join(b for b in (mem_block, ref_block, context) if b).strip()
        prompt = await self._build_prompt_async(responder, text, merged_context, history)

        kwargs: dict = {"system": system, "domain": "text"}
        if responder.get("model"):
            kwargs["model"] = responder["model"]
        if responder.get("temperature") is not None:
            kwargs["temperature"] = responder["temperature"]
        # Forwarded to claude-cli as --effort when the resolved provider is
        # claude-cli. Other providers ignore unknown kwargs.
        if responder.get("effort"):
            kwargs["effort"] = responder["effort"]

        response = await self.think(prompt, **kwargs)

        # Execute server actions from LLM output
        response, server_results = await self._execute_server_actions(response, responder)
        if not response and server_results:
            response = self._summarize_server_actions(server_results)

        # Save to history. For group rooms, tag the assistant turn with the
        # responder's id so the UI can render which agent spoke. 1:1 rooms
        # keep the legacy {role, text, ts} shape — back-compat invariant.
        now = datetime.now(timezone.utc).isoformat()
        history.append({"role": "user", "text": text, "ts": now})
        assistant_msg: dict = {"role": "assistant", "text": response, "ts": now}
        if kind == "group":
            assistant_msg["actor"] = {"type": "agent", "id": responder["id"]}
        history.append(assistant_msg)
        self._save_history(agent_id, history)

        # Auto-save to vault — use responder's name so group rooms log who said what.
        self._vault_log_chat(responder, text, response)

        await self.emit("rooms:chat", {
            "agent_id": agent_id,
            "agent_name": responder.get("name", responder["id"]),
            "kind": kind,
            "responder_id": responder["id"],
        })
        result = {
            "response": response,
            "agent_id": agent_id,
            "responder_id": responder["id"],
        }
        if server_results:
            result["server_results"] = server_results
        return result

    def _vault_log_chat(self, agent: dict, user_text: str, ai_text: str):
        """Auto-save chat exchange to a per-agent vault note."""
        agent_id = agent["id"]
        agent_name = agent.get("name", agent_id)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc).strftime("%H:%M")
        filename = f"chat-{agent_id}-{today}.md"

        existing = self.vault_read(filename)
        if existing:
            # Append to existing file
            entry = f"\n**You** ({now}): {user_text}\n\n**{agent_name}**: {ai_text}\n"
            self.vault_write(filename, existing.rstrip() + "\n" + entry)
        else:
            # Create new file
            content = (
                f"# {agent_name} — {today}\n\n"
                f"**You** ({now}): {user_text}\n\n"
                f"**{agent_name}**: {ai_text}\n"
            )
            self.vault_write(filename, content)

    # --- CLI ---

    @cli_command("rooms", help="Conversation rooms — list or chat")
    async def cmd_rooms(self, action: str = "list", name: str = "", text: str = ""):
        if action == "list":
            agents = self._list_agents()
            if not agents:
                print("  No agents. Create one via the API.")
                return
            for a in agents:
                files = len(a.get("knowledge_files", []))
                model = a.get("model") or "default"
                print(f"  {a['name']:<24} ({model}, {files} knowledge files)")
        elif action == "chat":
            if not name:
                print("  Usage: eos rooms chat \"Agent Name\" \"your message\"")
                return
            agent = self._find_agent_by_name(name)
            if not agent:
                print(f"  Agent '{name}' not found.")
                return
            if not text:
                print("  Provide a message to send.")
                return
            result = await self._chat(agent["id"], text)
            print(f"\n  [{agent['name']}]: {result['response']}\n")
        else:
            print(f"  Unknown action '{action}'. Use: list, chat")

    # --- Web API ---

    @web_route("GET", "/api/agents")
    async def api_list_agents(self, request):
        agents = self._list_agents()
        tier = request.query_params.get("tier", "")
        # Archived rooms are excluded by default — pass ?status=archived to
        # see only archived, or ?status=all to see everything. Search hits
        # always include archived rooms (so you can find old conversations).
        status = request.query_params.get("status", "active")
        if tier:
            agents = [a for a in agents if a.get("tier", "user") == tier]
        if status == "active":
            agents = [a for a in agents if a.get("status") != "archived"]
        elif status == "archived":
            agents = [a for a in agents if a.get("status") == "archived"]
        # status == "all" → no filter
        return agents

    @web_route("GET", "/api/agents/{agent_id}")
    async def api_get_agent(self, request):
        agent_id = request.path_params["agent_id"]
        agent = self._load_agent(agent_id)
        if not agent:
            return {"error": "not found"}
        return agent

    @web_route("POST", "/api/agents")
    async def api_create_agent(self, request):
        data = await request.json()
        # `or ""` defends against JSON null in the request body — bare
        # `data.get(K, "")` returns None when the key is present-but-null,
        # and `.strip()` on None crashes the route.
        name = (data.get("name") or "").strip()
        if not name:
            return {"error": "name required"}
        agent = {
            "id": data.get("id") or uuid.uuid4().hex[:12],
            "name": name,
            "tier": data.get("tier", "user"),
            "system_prompt": data.get("system_prompt", "You are a helpful assistant."),
            "knowledge_files": data.get("knowledge_files", []),
            "knowledge_dir": data.get("knowledge_dir", ""),
            "knowledge_char_limit": data.get("knowledge_char_limit", 2000),
            "model": data.get("model", ""),
            "effort": data.get("effort", ""),
            "tools": data.get("tools", []),
            "server_actions": data.get("server_actions", {}),
            "temperature": data.get("temperature"),
            "builtin": data.get("builtin", False),
            "created": datetime.now(timezone.utc).isoformat(),
        }
        self._save_agent(agent)
        return agent

    @web_route("PUT", "/api/agents/{agent_id}")
    async def api_update_agent(self, request):
        agent_id = request.path_params["agent_id"]
        agent = self._load_agent(agent_id)
        if not agent:
            return {"error": "not found"}
        data = await request.json()
        updatable = ("name", "system_prompt", "knowledge_files", "knowledge_dir",
                     "knowledge_char_limit", "model", "effort", "tools",
                     "temperature", "tier", "server_actions")
        for key in updatable:
            if key in data:
                agent[key] = data[key]
        self._save_agent(agent)
        return agent

    @web_route("DELETE", "/api/agents/{agent_id}")
    async def api_delete_agent(self, request):
        agent_id = request.path_params["agent_id"]
        agent = self._load_agent(agent_id)
        if not agent:
            return {"error": "not found"}
        if agent.get("builtin"):
            return {"error": "Cannot delete builtin agent. You can edit it instead."}
        agent_path = self._agent_path(agent_id)
        history_path = self._history_path(agent_id)
        agent_path.unlink()
        if history_path.exists():
            history_path.unlink()
        self.kernel.agents.invalidate(agent_id)
        return {"deleted": agent_id}

    @web_route("POST", "/api/chat")
    async def api_chat(self, request):
        data = await request.json()
        agent_id = (data.get("agent_id") or "")
        text = (data.get("text") or "").strip()
        if not agent_id or not text:
            return {"error": "agent_id and text required"}
        context = data.get("context") or ""
        client_actions = data.get("client_actions")
        return await self._chat(agent_id, text, context=context, client_actions=client_actions)

    @web_route("POST", "/api/chat/stream")
    async def api_chat_stream(self, request):
        """Streaming chat — returns NDJSON chunks as they arrive.

        Branches on responder type:
        - agent → existing think_stream path with [DO:] action execution
        - cli   → spawn agent-runtime CLI subprocess, stream stream-json events

        Optional `reply_to: <ts>` carries through to the saved user message
        for thread rendering (Phase 25). The reply context isn't fed to the
        LLM as separate framing — the parent is already in scrollback.
        """
        data = await request.json()
        agent_id = (data.get("agent_id") or "")
        text = (data.get("text") or "").strip()
        reply_to = (data.get("reply_to") or "").strip()
        if not agent_id or not text:
            return {"error": "agent_id and text required"}

        room = self._load_agent(agent_id)
        if not room:
            return {"error": f"Room '{agent_id}' not found"}

        parts = self._normalize_participants(room)
        kind = self._room_kind(room)
        # Participant-aware resolution — picks an agent OR cli participant.
        responder_part = self._resolve_responder(text, parts)
        if not responder_part:
            return {"error": "room has no responder participants"}

        history = self._load_history(agent_id)
        app = self

        if responder_part.get("type") == "cli":
            # CLI participant — agent-runtime spawn, stream-json events.
            cli_id = responder_part["id"]

            async def generate_cli():
                full_text = ""
                async for chunk in app._dispatch_cli_turn(
                    room, responder_part, text, history,
                ):
                    if chunk.get("text"):
                        full_text += chunk["text"]
                    yield chunk
                # Phase 5 review gate: parse [DO:] tokens out of the CLI's
                # reply, save each as a pending action, yield a card chunk
                # so the UI can render Apply/Reject inline. Cleaned text
                # (without tokens) is what gets persisted to history.
                cleaned_text, pending = await app._gate_server_actions(
                    full_text, room_id=agent_id,
                    source_actor={"type": "cli", "id": cli_id},
                )
                for action in pending:
                    yield {"pending_action": action, "done": False}
                # Save the user turn + the CLI's accumulated text reply.
                now = datetime.now(timezone.utc).isoformat()
                user_msg: dict = {"role": "user", "text": text, "ts": now}
                if reply_to:
                    user_msg["reply_to"] = reply_to
                history.append(user_msg)
                assistant_msg: dict = {
                    "role": "assistant",
                    "text": cleaned_text or full_text,
                    "ts": now,
                    "actor": {"type": "cli", "id": cli_id},
                }
                if pending:
                    assistant_msg["pending"] = [a["id"] for a in pending]
                history.append(assistant_msg)
                app._save_history(agent_id, history)
                # Note: vault logging skipped for CLI — outputs are often
                # tool transcripts that aren't useful as journal entries.
                await app.emit("rooms:chat", {
                    "agent_id": agent_id,
                    "responder_id": cli_id,
                    "responder_type": "cli",
                    "kind": kind,
                    "pending_count": len(pending),
                })

            return ndjson_response(generate_cli())

        # Agent participant — existing path.
        responder_id = responder_part["id"]
        if responder_id != room["id"]:
            responder = self._load_agent(responder_id)
            if not responder:
                return {"error": f"Participant '{responder_id}' not found"}
        else:
            responder = room

        context = data.get("context", "")
        client_actions = data.get("client_actions")

        system = self._build_system(responder, client_actions)
        # Phase 11 — resolve [[wikilink]] vault refs in the user message.
        ref_block = self._resolve_wikilinks(text)
        # Phase 26 — prepend the room's memory block when present.
        mem_block = self._memory_block(room)
        merged_context = "\n\n".join(b for b in (mem_block, ref_block, context) if b).strip()
        prompt = await self._build_prompt_async(responder, text, merged_context, history)

        async def generate():
            full_text = ""
            try:
                stream_kwargs = {"system": system, "domain": "text"}
                if responder.get("model"):
                    stream_kwargs["model"] = responder["model"]
                if responder.get("temperature") is not None:
                    stream_kwargs["temperature"] = responder["temperature"]
                if responder.get("effort"):
                    stream_kwargs["effort"] = responder["effort"]
                async for chunk in app.think_stream(prompt, **stream_kwargs):
                    t = chunk.get("text", "")
                    if t:
                        full_text += t
                        yield {"text": t, "done": False}
                yield {
                    "text": "", "done": True, "full": full_text,
                    "responder_id": responder["id"],
                }
            except Exception as e:
                yield {"text": str(e), "done": True, "error": True}
                full_text = f"Error: {e}"

            # Execute server actions + save history
            cleaned, server_results = await app._execute_server_actions(full_text, responder)
            if not cleaned and server_results:
                cleaned = app._summarize_server_actions(server_results)
                yield {"text": cleaned, "done": False, "fallback": True}
            now = datetime.now(timezone.utc).isoformat()
            user_msg: dict = {"role": "user", "text": text, "ts": now}
            if reply_to:
                user_msg["reply_to"] = reply_to
            history.append(user_msg)
            assistant_msg: dict = {"role": "assistant", "text": cleaned, "ts": now}
            if kind == "group":
                assistant_msg["actor"] = {"type": "agent", "id": responder["id"]}
            history.append(assistant_msg)
            app._save_history(agent_id, history)
            app._vault_log_chat(responder, text, cleaned)
            await app.emit("rooms:chat", {
                "agent_id": agent_id,
                "responder_id": responder["id"],
                "kind": kind,
            })
            if server_results:
                yield {"server_results": server_results}

        return ndjson_response(generate())

    @web_route("GET", "/api/debug/system-prompt/{agent_id}")
    async def api_debug_system_prompt(self, request):
        """Return the exact system prompt an agent would build. For testing."""
        agent_id = request.path_params["agent_id"]
        agent = self._load_agent(agent_id)
        if not agent:
            return {"error": f"agent {agent_id!r} not found"}
        return {
            "agent_id": agent_id,
            "prompt": self._build_system(agent, client_actions=None),
            "server_actions": agent.get("server_actions", {}),
        }

    @web_route("POST", "/api/undo")
    async def api_undo(self, request):
        """Undo the last reversible [DO:] action by calling its declared inverse."""
        log_path = self._actions_log_path()
        if not log_path.exists():
            return {"ok": False, "message": "Nothing to undo."}

        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            return {"ok": False, "error": f"read log failed: {e}"}

        target_idx = None
        target = None
        for i in range(len(lines) - 1, -1, -1):
            raw = lines[i].strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except Exception:
                continue
            if entry.get("inverse") and not entry.get("reversed"):
                target_idx = i
                target = entry
                break

        if not target:
            return {"ok": False, "message": "Nothing to undo."}

        try:
            result = await self.call_app(
                target["app"], target["inverse"], **target.get("args", {})
            )
        except Exception as e:
            return {"ok": False, "error": f"inverse failed: {e}"}

        target["reversed"] = True
        target["reversed_ts"] = datetime.now(timezone.utc).isoformat()
        lines[target_idx] = json.dumps(target, ensure_ascii=False)
        try:
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

        await self.emit("rooms:undo", {
            "app": target["app"], "method": target["method"],
            "inverse": target["inverse"],
        })

        return {
            "ok": True,
            "undid": {
                "app": target["app"],
                "method": target["method"],
                "args": target.get("args", {}),
            },
            "result": str(result)[:300],
        }

    @web_route("GET", "/api/history/{agent_id}")
    async def api_get_history(self, request):
        agent_id = request.path_params["agent_id"]
        messages = self._load_history(agent_id)
        return {"agent_id": agent_id, "messages": messages}

    @web_route("DELETE", "/api/history/{agent_id}")
    async def api_clear_history(self, request):
        agent_id = request.path_params["agent_id"]
        p = self._history_path(agent_id)
        if p.exists():
            p.unlink()
        return {"cleared": agent_id}

    @web_route("POST", "/api/vault-export")
    async def api_vault_export(self, request):
        """Export all agents + recent chat history to vault."""
        agents = self._list_agents()
        lines = ["# Rooms — Conversation Agents", "", f"*{len(agents)} agents*", ""]
        for agent in agents:
            lines.append(f"## {agent.get('name', agent['id'])}")
            lines.append("")
            if agent.get("system_prompt"):
                lines.append(f"**System Prompt:** {agent['system_prompt'][:200]}")
            if agent.get("model"):
                lines.append(f"**Model:** {agent['model']}")
            if agent.get("knowledge_files"):
                lines.append(f"**Knowledge:** {', '.join(agent['knowledge_files'])}")
            lines.append("")
            # Recent history
            hp = self._history_path(agent["id"])
            if hp.exists():
                history = json.loads(hp.read_text(encoding="utf-8"))
                recent = history[-5:]
                if recent:
                    lines.append("### Recent Chat")
                    lines.append("")
                    for msg in recent:
                        role = "You" if msg.get("role") == "user" else "AI"
                        lines.append(f"**{role}:** {msg.get('text', '')[:200]}")
                    lines.append("")
            lines.append("---")
            lines.append("")
        self.vault_write("rooms-agents.md", "\n".join(lines))
        return {"exported": len(agents)}

    # --- Group room API (Phase 2) ---
    #
    # A "room" is the surface concept. 1:1 rooms remain stored as their
    # underlying agent record (legacy compatibility). Group rooms get a fresh
    # generated id, an explicit `participants` list, and `tier="group"` so the
    # 1:1 agent picker hides them.

    def list_rooms(self, kind: str | None = None,
                   include_archived: bool = False) -> list[dict]:
        """List rooms with computed `kind` and `participants`. Filter by kind
        ("1on1" | "group") when supplied. Archived rooms are excluded by
        default — pass include_archived=True to include them."""
        rooms = []
        for r in self._list_agents():
            if not include_archived and r.get("status") == "archived":
                continue
            view = dict(r)
            view["participants"] = self._normalize_participants(r)
            view["kind"] = self._room_kind(r)
            if kind is None or view["kind"] == kind:
                rooms.append(view)
        return rooms

    async def archive_room(self, room_id: str) -> dict:
        """Mark a room as archived (status='archived'). Reversible — see
        unarchive_room. Doesn't delete history or pending actions; the room
        just stops appearing in the default sidebar list."""
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        if room.get("builtin"):
            return {"error": "cannot archive a builtin agent"}
        room["status"] = "archived"
        room["archived_ts"] = datetime.now(timezone.utc).isoformat()
        self._save_agent(room)
        await self.emit("rooms:archived", {"room_id": room_id})
        return {"ok": True, "room_id": room_id, "status": "archived"}

    async def unarchive_room(self, room_id: str) -> dict:
        """Restore an archived room to active status."""
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        room["status"] = "active"
        room.pop("archived_ts", None)
        self._save_agent(room)
        await self.emit("rooms:unarchived", {"room_id": room_id})
        return {"ok": True, "room_id": room_id, "status": "active"}

    # --- Room export to vault (Phase 8b) ---

    async def export_room(self, room_id: str) -> dict:
        """Render the full thread to a vault markdown note. The note carries
        frontmatter (room id, kind, participants, exported timestamp) + a
        speaker-headed transcript. Useful for archival, sharing, or feeding
        the conversation to an external tool.
        """
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        history = self._load_history(room_id)
        parts = self._normalize_participants(room)
        kind = self._room_kind(room)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in room_id.lower())
        rel_path = f"30_Resources/EmptyOS/rooms/exports/{today}-{slug}.md"

        # Frontmatter — flat fields only (vault rule 14: no nested structures).
        # Participants get JSON-encoded into a single string field.
        fm_lines = [
            "---",
            f"room_id: {room_id}",
            f"name: {room.get('name', room_id)}",
            f"kind: {kind}",
            f"exported: {datetime.now(timezone.utc).isoformat()}",
            f"participants: {json.dumps(parts, ensure_ascii=False)}",
            "tags:",
            "  - room-export",
        ]
        if room.get("status") == "archived":
            fm_lines.append("  - archived")
        fm_lines.append("---")

        body_lines = [f"# {room.get('name', room_id)}", ""]
        if not history:
            body_lines.append("*Empty room — no messages yet.*")
        else:
            for m in history:
                role = m.get("role", "user")
                actor = m.get("actor") or {}
                speaker_id = actor.get("id") or (
                    "me" if role == "user" else "assistant"
                )
                actor_type = actor.get("type") or ("user" if role == "user" else "agent")
                icon = {"cli": "⚡", "user": "👤", "agent": "◆"}.get(actor_type, "◆")
                ts = m.get("ts") or ""
                ts_short = ts.split("T", 1)[0] + " " + ts.split("T", 1)[1][:5] if "T" in ts else ts
                body_lines.append(f"### {icon} {speaker_id}  ·  {ts_short}")
                body_lines.append("")
                body_lines.append(m.get("text") or "*(empty)*")
                body_lines.append("")

        content = "\n".join(fm_lines) + "\n\n" + "\n".join(body_lines) + "\n"
        try:
            self.vault_write(rel_path, content)
        except Exception as e:
            return {"error": f"vault write failed: {e}"}
        await self.emit("rooms:exported", {"room_id": room_id, "path": rel_path})
        return {"ok": True, "room_id": room_id, "path": rel_path,
                "message_count": len(history)}

    # --- Room distill (Phase 8c) ---

    # --- Context inspector (Phase 19) ---
    #
    # Reconstructs what the LLM would see on the next turn — system prompt,
    # attached knowledge, recent history transcript. Doesn't run think(),
    # doesn't pre-resolve the user's hypothetical message. The user's actual
    # message + any [[wikilinks]] in it would be appended at chat time.

    async def inspect_context(self, room_id: str) -> dict:
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        # Resolve responder the same way _chat would for a 1:1 room (no
        # @mention is given here; for groups we pick the first agent so the
        # inspector reflects the most-likely default-responder context).
        parts = self._normalize_participants(room)
        agent_parts = [p for p in parts if p.get("type") == "agent"]
        if agent_parts and agent_parts[0]["id"] != room["id"]:
            responder = self._load_agent(agent_parts[0]["id"]) or room
        else:
            responder = room

        history = self._load_history(room_id)
        system = self._build_system(responder, client_actions=None)

        # Knowledge — let the kernel agents resolver render it the same way
        # _build_prompt sync path does. Embedding-aware retrieval is per-turn
        # (depends on user query) so we show the static dump here.
        try:
            knowledge_text = self.kernel.agents.load_knowledge(responder) or ""
        except Exception:
            knowledge_text = ""

        # Recent transcript — last 20 turns, the same window _assemble_prompt
        # uses. Render with speaker ids so it matches what the model sees.
        recent = (history or [])[-20:]
        transcript_lines = []
        for m in recent:
            actor = m.get("actor") or {}
            speaker = actor.get("id") or m.get("role", "user")
            text = (m.get("text") or "").strip()
            if text:
                transcript_lines.append(f"{speaker}: {text}")
        transcript = "\n".join(transcript_lines)

        return {
            "room_id": room_id,
            "responder_id": responder["id"],
            "responder_name": responder.get("name", responder["id"]),
            "model": responder.get("model", "(provider default)"),
            "effort": responder.get("effort", "(provider default)"),
            "temperature": responder.get("temperature"),
            "system_prompt": system,
            "system_prompt_chars": len(system),
            "knowledge": knowledge_text,
            "knowledge_chars": len(knowledge_text),
            "knowledge_files": list(responder.get("knowledge_files") or []),
            "transcript": transcript,
            "transcript_chars": len(transcript),
            "history_count": len(history),
            "total_chars": len(system) + len(knowledge_text) + len(transcript),
        }

    # --- Agent memory (Phase 26) ---
    #
    # Per-room facts the agent should remember across turns. Stored as
    # `memory: [{id, ts, fact}]` on the room record. Surfaced in the LLM
    # prompt as a "Memory" block so the agent has consistent context beyond
    # the rolling 20-turn history window.

    MAX_MEMORY_ENTRIES = 30  # cap so the prompt doesn't bloat indefinitely

    async def add_memory(self, room_id: str, fact: str) -> dict:
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        fact = (fact or "").strip()
        if not fact:
            return {"error": "fact required"}
        memory = list(room.get("memory") or [])
        memory.append({
            "id": f"mem-{uuid.uuid4().hex[:10]}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "fact": fact,
        })
        if len(memory) > self.MAX_MEMORY_ENTRIES:
            memory = memory[-self.MAX_MEMORY_ENTRIES:]
        room["memory"] = memory
        self._save_agent(room)
        return {"ok": True, "memory": memory}

    async def remove_memory(self, room_id: str, memory_id: str) -> dict:
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        memory = [m for m in (room.get("memory") or []) if m.get("id") != memory_id]
        room["memory"] = memory
        self._save_agent(room)
        return {"ok": True, "memory": memory}

    def list_memory(self, room_id: str) -> list[dict]:
        room = self._load_agent(room_id)
        if not room:
            return []
        return list(room.get("memory") or [])

    def _memory_block(self, room: dict) -> str:
        """Render the room's memory list as a context block to prepend to
        the LLM prompt. Empty when no memories — `_chat` skips the merge
        in that case so unused rooms don't get a 'Memory:' header."""
        memory = room.get("memory") or []
        if not memory:
            return ""
        lines = ["Memory (things you've been asked to remember):"]
        for m in memory:
            lines.append(f"- {m.get('fact', '')}")
        return "\n".join(lines)

    # --- Per-room knowledge management (Phase 18) ---
    #
    # Operates on `knowledge_files` — the existing field already piped through
    # _build_prompt_async / load_knowledge / load_knowledge_chunks. Phase 18
    # just adds clean add/remove APIs so the UI doesn't have to re-stringify
    # comma-separated paths.

    async def add_knowledge(self, room_id: str, path: str) -> dict:
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        path = (path or "").strip()
        if not path:
            return {"error": "path required"}
        files = list(room.get("knowledge_files") or [])
        if path in files:
            return {"ok": True, "already_present": True, "knowledge_files": files}
        files.append(path)
        room["knowledge_files"] = files
        self._save_agent(room)
        return {"ok": True, "knowledge_files": files}

    async def remove_knowledge(self, room_id: str, path: str) -> dict:
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        files = [p for p in (room.get("knowledge_files") or []) if p != path]
        room["knowledge_files"] = files
        self._save_agent(room)
        return {"ok": True, "knowledge_files": files}

    # --- Pin messages (Phase 15) ---
    #
    # Pinned messages stick to the top of the thread regardless of scroll.
    # Identified by message timestamp (ISO string) — already stable on every
    # saved message. Stored as `pinned_ts: [iso, ...]` on the room record.

    async def pin_message(self, room_id: str, ts: str) -> dict:
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        if not ts:
            return {"error": "ts required"}
        # Validate the ts actually corresponds to a saved message.
        history = self._load_history(room_id)
        if not any(m.get("ts") == ts for m in history):
            return {"error": "no message with that timestamp"}
        pinned = list(room.get("pinned_ts") or [])
        if ts in pinned:
            return {"ok": True, "already_pinned": True, "pinned_ts": pinned}
        pinned.append(ts)
        # Cap to last 10 to keep the pinned panel manageable.
        if len(pinned) > 10:
            pinned = pinned[-10:]
        room["pinned_ts"] = pinned
        self._save_agent(room)
        await self.emit("rooms:pinned", {"room_id": room_id, "ts": ts})
        return {"ok": True, "pinned_ts": pinned}

    async def unpin_message(self, room_id: str, ts: str) -> dict:
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        pinned = [t for t in (room.get("pinned_ts") or []) if t != ts]
        room["pinned_ts"] = pinned
        self._save_agent(room)
        await self.emit("rooms:unpinned", {"room_id": room_id, "ts": ts})
        return {"ok": True, "pinned_ts": pinned}

    # --- Catch me up (Phase 9c) ---

    CATCH_UP_SYSTEM = (
        "You give one-paragraph catch-up summaries of recent chat messages. "
        "Output ONE plain paragraph — no headings, no bullets, no preamble, "
        "no closing remark. Lead with what changed, end with the most recent "
        "open thread or question. Skip pleasantries, skip 'in summary'. "
        "Quote a phrase only when wording matters."
    )

    async def catch_me_up(self, room_id: str, since_ts: str = "") -> dict:
        """Summarise messages newer than `since_ts` for a room. If since_ts
        is empty, defaults to the last visit timestamp; if there's no visit
        either, summarises the last 20 messages.
        """
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        history = self._load_history(room_id)
        if not history:
            return {"error": "room has no history"}
        if not since_ts:
            since_ts = self._load_visits().get(room_id, "")

        if since_ts:
            new_msgs = [m for m in history if m.get("ts", "") > since_ts]
        else:
            new_msgs = history[-20:]
        if not new_msgs:
            return {"summary": "Nothing new since you were last here.", "count": 0}

        lines = []
        for m in new_msgs:
            actor = m.get("actor") or {}
            speaker = actor.get("id") or m.get("role", "user")
            text = (m.get("text") or "").strip()
            if text:
                lines.append(f"{speaker}: {text}")
        transcript = "\n".join(lines)
        prompt = (
            f"Catch the user up on these {len(new_msgs)} new messages in "
            f"room '{room.get('name', room_id)}':\n\n{transcript}"
        )
        kwargs: dict = {"system": self.CATCH_UP_SYSTEM, "domain": "text"}
        if room.get("model"):
            kwargs["model"] = room["model"]
        if room.get("effort"):
            kwargs["effort"] = room["effort"]
        try:
            summary = (await self.think(prompt, **kwargs)).strip()
        except Exception as e:
            return {"error": f"think() failed: {e}"}
        return {"summary": summary, "count": len(new_msgs)}

    DISTILL_SYSTEM = (
        "You distill multi-turn conversations into structured KB notes. "
        "Output strict markdown with the sections named below — no preamble, "
        "no closing remark, no filler. Be terse: prefer bullet fragments to "
        "full sentences. Quote only when the wording matters. Skip empty "
        "sections rather than filling them.\n\n"
        "Sections, in order:\n"
        "## Decisions\n"
        "- Concrete decisions reached. Each as a bullet.\n\n"
        "## Open questions\n"
        "- Things that came up but weren't resolved.\n\n"
        "## Action items\n"
        "- Tasks the user (or others) should do. Use checkbox lines: `- [ ] ...`.\n\n"
        "## Insights\n"
        "- Non-obvious observations or framings worth remembering.\n\n"
        "## References\n"
        "- Files, URLs, or vault notes that came up. Each as a bullet.\n"
    )

    async def distill_room(self, room_id: str) -> dict:
        """Run the room's history through self.think() with a summarization
        prompt. Writes the result as a KB note tagged `kb` + `room-distill`.
        Returns the vault path on success.
        """
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        history = self._load_history(room_id)
        if not history:
            return {"error": "room has no history to distill"}

        # Build the transcript the model summarises. Use speaker ids so the
        # model can attribute decisions/quotes correctly.
        lines = []
        for m in history:
            actor = m.get("actor") or {}
            speaker = actor.get("id") or m.get("role", "user")
            text = (m.get("text") or "").strip()
            if text:
                lines.append(f"{speaker}: {text}")
        transcript = "\n".join(lines)

        prompt = (
            f"Conversation in room '{room.get('name', room_id)}':\n\n"
            f"{transcript}\n\n"
            f"Distill the above into the structured note format."
        )

        kwargs: dict = {"system": self.DISTILL_SYSTEM, "domain": "text"}
        if room.get("model"):
            kwargs["model"] = room["model"]
        if room.get("effort"):
            kwargs["effort"] = room["effort"]
        try:
            distilled = await self.think(prompt, **kwargs)
        except Exception as e:
            return {"error": f"think() failed: {e}"}

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in room_id.lower())
        rel_path = f"30_Resources/EmptyOS/rooms/distills/{today}-{slug}.md"
        fm = [
            "---",
            f"source_room: {room_id}",
            f"source_room_name: {room.get('name', room_id)}",
            f"distilled: {datetime.now(timezone.utc).isoformat()}",
            f"message_count: {len(history)}",
            "tags:",
            "  - kb",
            "  - room-distill",
            "---",
        ]
        body = [f"# {room.get('name', room_id)} — distilled", "",
                f"*From {len(history)} messages on {today}.*", "",
                distilled.strip()]
        content = "\n".join(fm) + "\n\n" + "\n".join(body) + "\n"
        try:
            self.vault_write(rel_path, content)
        except Exception as e:
            return {"error": f"vault write failed: {e}"}
        await self.emit("rooms:distilled", {
            "room_id": room_id, "path": rel_path, "message_count": len(history),
        })
        return {"ok": True, "room_id": room_id, "path": rel_path,
                "message_count": len(history)}

    async def create_room(
        self,
        title: str,
        participants: list[dict],
        *,
        system_prompt: str = "",
        model: str = "",
    ) -> dict:
        """Create a group room (≥2 agent participants).

        For 1:1 rooms, callers should use the existing POST /api/agents path
        — that record IS the 1:1 room.
        """
        if not title:
            return {"error": "title required"}
        # Coerce {agent_id, ...} into the canonical shape.
        norm: list[dict] = [{"type": "user", "id": "me"}]
        for p in participants or []:
            if isinstance(p, str):
                norm.append({"type": "agent", "id": p})
            elif isinstance(p, dict):
                t = p.get("type", "agent")
                pid = p.get("id")
                if pid:
                    entry: dict = {"type": t, "id": pid}
                    # Carry CLI-specific config through (cwd, allowed_tools,
                    # timeout_s, model, effort).
                    if t == "cli":
                        for k in ("cwd", "allowed_tools", "timeout_s",
                                  "model", "effort"):
                            if k in p:
                                entry[k] = p[k]
                    norm.append(entry)
        responder_count = sum(1 for p in norm if p.get("type") in ("agent", "cli"))
        if responder_count < 2:
            return {"error": "group rooms need at least 2 responder participants (agent or cli)"}
        # Validate every agent participant exists. CLIs are validated lazily
        # at dispatch time — the agent-runtime plugin reports if the binary
        # isn't on PATH.
        for p in norm:
            if p.get("type") == "agent" and not self._load_agent(p["id"]):
                return {"error": f"participant '{p['id']}' not found"}
        room = {
            "id": self._new_room_id("group"),
            "name": title,
            "tier": "group",
            "kind": "group",
            "participants": norm,
            "system_prompt": system_prompt,
            "model": model,
            "knowledge_files": [],
            "knowledge_dir": "",
            "knowledge_char_limit": 0,
            "tools": [],
            "server_actions": {},
            "temperature": None,
            "builtin": False,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        self._save_agent(room)
        await self.emit("rooms:created", {
            "room_id": room["id"], "title": room["name"],
            "participants": room["participants"],
        })
        return room

    async def add_participant(self, room_id: str, participant: dict | str) -> dict:
        """Add an agent or CLI participant to a room. Promotes a 1:1 room
        to a group when the second responder lands."""
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        if isinstance(participant, str):
            participant = {"type": "agent", "id": participant}
        if not isinstance(participant, dict) or not participant.get("id"):
            return {"error": "participant requires {type, id}"}
        if participant.get("type") == "agent" and not self._load_agent(participant["id"]):
            return {"error": f"agent '{participant['id']}' not found"}
        # Strip to canonical fields, carrying CLI-specific config through.
        clean: dict = {"type": participant.get("type", "agent"), "id": participant["id"]}
        if clean["type"] == "cli":
            for k in ("cwd", "allowed_tools", "timeout_s", "model", "effort"):
                if k in participant:
                    clean[k] = participant[k]
        parts = self._normalize_participants(room)
        # No-op if already present (matched by type + id).
        for p in parts:
            if p.get("type") == clean["type"] and p.get("id") == clean["id"]:
                room["participants"] = parts
                room["kind"] = self._room_kind(room)
                self._save_agent(room)
                return room
        parts.append(clean)
        room["participants"] = parts
        room["kind"] = self._room_kind(room)
        self._save_agent(room)
        await self.emit("rooms:participant_added", {
            "room_id": room_id, "participant": participant,
        })
        return room

    def remove_participant(self, room_id: str, participant_id: str) -> dict:
        """Remove a participant by id. Refuses to remove the last agent
        participant (a room with no agents has nothing to respond)."""
        room = self._load_agent(room_id)
        if not room:
            return {"error": "room not found"}
        parts = self._normalize_participants(room)
        kept = [p for p in parts if p.get("id") != participant_id]
        if len([p for p in kept if p.get("type") == "agent"]) < 1:
            return {"error": "cannot remove the last agent participant"}
        room["participants"] = kept
        room["kind"] = self._room_kind(room)
        self._save_agent(room)
        return room

    @web_route("GET", "/api/rooms")
    async def api_list_rooms(self, request):
        kind = request.query_params.get("kind") or None
        return self.list_rooms(kind=kind)

    @web_route("POST", "/api/rooms")
    async def api_create_room(self, request):
        data = await request.json()
        return await self.create_room(
            title=(data.get("title") or "").strip(),
            participants=data.get("participants") or [],
            system_prompt=data.get("system_prompt", ""),
            model=data.get("model", ""),
        )

    @web_route("POST", "/api/rooms/{room_id}/participants")
    async def api_add_participant(self, request):
        room_id = request.path_params["room_id"]
        data = await request.json()
        return await self.add_participant(room_id, data.get("participant") or data)

    # --- Snippet routes (Phase 21) ---

    @web_route("GET", "/api/snippets")
    async def api_list_snippets(self, request):
        return self.list_snippets()

    @web_route("GET", "/api/snippets/{name}")
    async def api_get_snippet(self, request):
        return await self.get_snippet(request.path_params["name"])

    @web_route("POST", "/api/snippets")
    async def api_add_snippet(self, request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        return await self.add_snippet(
            (data.get("name") or "").strip(),
            (data.get("body") or "").strip(),
        )

    @web_route("DELETE", "/api/snippets/{name}")
    async def api_remove_snippet(self, request):
        return await self.remove_snippet(request.path_params["name"])

    @web_route("POST", "/api/rooms/{room_id}/visit")
    async def api_visit_room(self, request):
        return self.mark_visited(request.path_params["room_id"])

    @web_route("POST", "/api/rooms/{room_id}/schedule")
    async def api_set_schedule(self, request):
        room_id = request.path_params["room_id"]
        try:
            data = await request.json()
        except Exception:
            data = {}
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
        try:
            data = await request.json()
        except Exception:
            data = {}
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

    @web_route("GET", "/api/visits")
    async def api_visits(self, request):
        return self.get_visits()

    @web_route("GET", "/api/unread")
    async def api_unread(self, request):
        return self.get_unread()

    @web_route("POST", "/api/rooms/{room_id}/archive")
    async def api_archive_room(self, request):
        return await self.archive_room(request.path_params["room_id"])

    @web_route("POST", "/api/rooms/{room_id}/unarchive")
    async def api_unarchive_room(self, request):
        return await self.unarchive_room(request.path_params["room_id"])

    @web_route("POST", "/api/rooms/{room_id}/export")
    async def api_export_room(self, request):
        return await self.export_room(request.path_params["room_id"])

    @web_route("POST", "/api/rooms/{room_id}/distill")
    async def api_distill_room(self, request):
        return await self.distill_room(request.path_params["room_id"])

    @web_route("GET", "/api/rooms/{room_id}/inspect")
    async def api_inspect_context(self, request):
        return await self.inspect_context(request.path_params["room_id"])

    @web_route("GET", "/api/rooms/{room_id}/memory")
    async def api_list_memory(self, request):
        return self.list_memory(request.path_params["room_id"])

    @web_route("POST", "/api/rooms/{room_id}/memory")
    async def api_add_memory(self, request):
        room_id = request.path_params["room_id"]
        try:
            data = await request.json()
        except Exception:
            data = {}
        return await self.add_memory(room_id, (data.get("fact") or "").strip())

    @web_route("DELETE", "/api/rooms/{room_id}/memory/{memory_id}")
    async def api_remove_memory(self, request):
        return await self.remove_memory(
            request.path_params["room_id"],
            request.path_params["memory_id"],
        )

    @web_route("POST", "/api/rooms/{room_id}/knowledge")
    async def api_add_knowledge(self, request):
        room_id = request.path_params["room_id"]
        try:
            data = await request.json()
        except Exception:
            data = {}
        return await self.add_knowledge(
            room_id, (data.get("path") or "").strip(),
        )

    @web_route("POST", "/api/rooms/{room_id}/knowledge/remove")
    async def api_remove_knowledge(self, request):
        # POST/remove instead of DELETE because vault paths often contain
        # slashes / special chars that break URL routing — body-payload is safer.
        room_id = request.path_params["room_id"]
        try:
            data = await request.json()
        except Exception:
            data = {}
        return await self.remove_knowledge(
            room_id, (data.get("path") or "").strip(),
        )

    @web_route("POST", "/api/rooms/{room_id}/pin")
    async def api_pin_message(self, request):
        data = await request.json()
        return await self.pin_message(
            request.path_params["room_id"], (data.get("ts") or "").strip(),
        )

    @web_route("POST", "/api/rooms/{room_id}/unpin")
    async def api_unpin_message(self, request):
        data = await request.json()
        return await self.unpin_message(
            request.path_params["room_id"], (data.get("ts") or "").strip(),
        )

    @web_route("POST", "/api/rooms/{room_id}/catch-up")
    async def api_catch_up(self, request):
        room_id = request.path_params["room_id"]
        try:
            data = await request.json()
        except Exception:
            data = {}
        since = (data.get("since") or "").strip() if isinstance(data, dict) else ""
        return await self.catch_me_up(room_id, since)

    @web_route("DELETE", "/api/rooms/{room_id}/participants/{participant_id}")
    async def api_remove_participant(self, request):
        room_id = request.path_params["room_id"]
        participant_id = request.path_params["participant_id"]
        return self.remove_participant(room_id, participant_id)

    # --- Room ↔ task pointer (Phase 4) ---
    #
    # Tasks are owned by `apps/projects/`; rooms only attach pointers. A task
    # references back to a room via the 🗨️ <room_id> marker on its line; the
    # rooms-side methods below are thin wrappers that go through the projects
    # app's authoritative API.

    async def attach_task(
        self, room_id: str, text: str, project_id: str = "inbox", due: str = "",
    ) -> dict:
        """Create a task in `project_id` (default: inbox) and attach it back
        to this room. The task line carries the 🗨️ marker so it surfaces in
        `tasks_for_room` and the room's attached-tasks panel."""
        if not room_id:
            return {"error": "room_id required"}
        if not self._load_agent(room_id):
            return {"error": f"room '{room_id}' not found"}
        text = (text or "").strip()
        if not text:
            return {"error": "task text required"}
        return await self.call_app(
            "projects", "add_task_to_project",
            project_id=project_id, text=text, due=due, room_id=room_id,
        )

    async def tasks_for_room(self, room_id: str, status_filter: str = "") -> list[dict]:
        """Tasks attached to `room_id` across all projects. Thin wrapper over
        projects.tasks_for_room — kept on rooms so the UI hits a single
        canonical URL (`/rooms/api/rooms/<id>/tasks`)."""
        if not room_id:
            return []
        try:
            return await self.call_app(
                "projects", "tasks_for_room",
                room_id=room_id, status_filter=status_filter,
            ) or []
        except Exception:
            return []

    @web_route("GET", "/api/rooms/{room_id}/tasks")
    async def api_room_tasks(self, request):
        room_id = request.path_params["room_id"]
        status_filter = request.query_params.get("status", "")
        return await self.tasks_for_room(room_id, status_filter)

    @web_route("POST", "/api/rooms/{room_id}/tasks")
    async def api_attach_task(self, request):
        room_id = request.path_params["room_id"]
        data = await request.json()
        return await self.attach_task(
            room_id=room_id,
            text=(data.get("text") or "").strip(),
            project_id=(data.get("project_id") or "inbox").strip() or "inbox",
            due=(data.get("due") or "").strip(),
        )

    # --- Pending actions (Phase 5 review gate) ---

    @web_route("GET", "/api/rooms/{room_id}/pending")
    async def api_room_pending(self, request):
        """Pending [DO:] actions for a room. Defaults to status=pending; pass
        ?status=all to include applied/rejected/failed for an audit view."""
        room_id = request.path_params["room_id"]
        status = request.query_params.get("status", "pending")
        if status == "all":
            status = ""
        return self.list_pending(room_id, status=status)

    @web_route("GET", "/api/search")
    async def api_search(self, request):
        """Cross-room message search. ?q=<query>&limit=<n>. Returns hit list
        with snippet + room context."""
        q = request.query_params.get("q", "")
        try:
            limit = int(request.query_params.get("limit", "20"))
        except ValueError:
            limit = 20
        return self.search_messages(q, limit=limit)

    @web_route("GET", "/api/pending")
    async def api_global_pending(self, request):
        """All pending actions across every room. Used by the sidebar's
        global pending dashboard. ?status=all includes resolved entries."""
        status = request.query_params.get("status", "pending")
        if status == "all":
            status = ""
        return self.list_pending(room_id="", status=status)

    @web_route("POST", "/api/pending/{action_id}/apply")
    async def api_apply_pending(self, request):
        return await self.apply_pending(request.path_params["action_id"])

    @web_route("POST", "/api/pending/{action_id}/reject")
    async def api_reject_pending(self, request):
        return await self.reject_pending(request.path_params["action_id"])
