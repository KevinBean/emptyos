"""Portal — claude.ai-shape entry surface.

A thin UI app: hero composer + sidebar (Rooms + ungrouped Threads) + mode chips.
Most backend wiring goes through apps/rooms/ — portal doesn't own conversations.

The one piece portal DOES own is **Rooms** (the claude.ai "Projects" analogue):
a thin organisational folder layered over apps/rooms/ agents. A folder has a
name, a default mode, and a list of `thread_ids` pointing at rooms-app agents.
Folders don't store conversation content — they're pure organisation.

  data/apps/portal/folders.json
  {
    "schema_version": 1,
    "folders": {
      "<folder_id>": {
        "id": "<folder_id>",
        "name": "Work",
        "default_mode": "think",
        "thread_ids": ["agent-abc123", ...],
        "created": "<iso>",
        "updated": "<iso>"
      }
    }
  }

A thread can belong to at most one folder (enforced on attach). Deleting a
folder leaves its threads intact and ungrouped — apps/rooms/ remains the
source of truth for conversation content.

Out of V2.6 (defer): folder-level system_prompt that overrides per-thread
prompts (claude.ai's "custom instructions"), knowledge-file attachments,
drag-drop reordering, sub-folders. The schema reserves room for these
fields without committing UI for them yet.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk import now_iso as _now

_SCHEMA_VERSION = 1

#: The agent app's project-name cap (apps/public/standard/agent/projects.py
#: NAME_MAX). A longer folder name is clamped rather than failing every run.
MIGRATE_NAME_MAX = 120


def _new_id() -> str:
    return "fld-" + uuid.uuid4().hex[:10]


class PortalApp(BaseApp):
    async def setup(self) -> None:
        await super().setup()
        self._folders_path: Path = self.data_dir / "folders.json"

    # ── Storage ──────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if not self._folders_path.exists():
            return {"schema_version": _SCHEMA_VERSION, "folders": {}}
        try:
            data = json.loads(self._folders_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "folders" not in data:
                raise ValueError("malformed")
            data.setdefault("schema_version", _SCHEMA_VERSION)
            if not isinstance(data["folders"], dict):
                data["folders"] = {}
            return data
        except (json.JSONDecodeError, ValueError, OSError):
            return {"schema_version": _SCHEMA_VERSION, "folders": {}}

    def _save(self, state: dict[str, Any]) -> None:
        self._folders_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._folders_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self._folders_path)

    def _folder_of_thread(self, state: dict, thread_id: str) -> str | None:
        for fid, f in state.get("folders", {}).items():
            if thread_id in (f.get("thread_ids") or []):
                return fid
        return None

    # ── Page config (dark flags the page reads at boot) ─────────────────

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        """Flags the page needs before it renders. ``chat_first`` turns the home
        into the chat-first surface (pages/portal-chat.js): new conversations are
        agent sessions with profile "chat", and the other modes move under More.
        ``[apps.portal] feature.chat-first.enabled`` — off by default.

        ``artifacts`` opens the side panel when a chat produces one. It is the
        agent app's flag, not portal's (``[apps.agent] feature.artifacts.enabled``),
        because that is what decides whether the tool exists at all — a panel
        portal enabled on its own would be a door onto a room with no floor.
        """
        return {
            "chat_first": bool(self.app_config("feature.chat-first.enabled", False)),
            "artifacts": bool(self.kernel.config.get("apps.agent.feature.artifacts.enabled", False)),
            # Same reasoning for connectors: the agent app's flag is what
            # decides whether an MCP tool can exist, so a portal-only toggle
            # would offer a picker over nothing. Public mode is refused
            # server-side as well (connectors.py).
            "connectors": bool(
                self.kernel.config.get("apps.agent.feature.mcp-inbound.enabled", False)
            ) and str(self.kernel.config.get("network.mode", "local")) != "public",
        }

    # ── Folder CRUD ──────────────────────────────────────────────────

    @web_route("GET", "/api/folders")
    async def api_list_folders(self, request):
        state = self._load()
        # Return as a list (UI iterates), sorted by updated desc → falls back to created.
        folders = list(state.get("folders", {}).values())
        folders.sort(key=lambda f: (f.get("updated") or f.get("created") or ""), reverse=True)
        return {"folders": folders}

    @web_route("POST", "/api/folders")
    async def api_create_folder(self, request):
        data = await self.read_json(request)
        name = (data.get("name") or "").strip()
        if not name:
            return {"error": "name required"}
        mode = (data.get("default_mode") or "think").strip().lower()
        if mode not in {"think", "learn", "code", "life"}:
            mode = "think"
        state = self._load()
        fid = _new_id()
        folder = {
            "id": fid,
            "name": name,
            "default_mode": mode,
            "thread_ids": [],
            "system_prompt": "",
            "model": "",            # folder-level model override; empty = default chain
            "knowledge_files": [],  # reserved — V2.7+
            "created": _now(),
            "updated": _now(),
        }
        state["folders"][fid] = folder
        self._save(state)
        await self.emit("portal:folder_created", {"id": fid, "name": name})
        return folder

    @web_route("PATCH", "/api/folders/{folder_id}")
    async def api_update_folder(self, request):
        fid = request.path_params["folder_id"]
        data = await self.read_json(request)
        state = self._load()
        f = state.get("folders", {}).get(fid)
        if not f:
            return {"error": "not found"}
        if "name" in data:
            n = (data.get("name") or "").strip()
            if n:
                f["name"] = n
        if "default_mode" in data:
            m = (data.get("default_mode") or "").strip().lower()
            if m in {"think", "learn", "code", "life"}:
                f["default_mode"] = m
        if "system_prompt" in data:
            # Folder-level instructions are the claude.ai-Project analogue.
            # Empty string is valid (clears instructions); only `None` is
            # treated as "field not present".
            sp = data.get("system_prompt")
            if sp is None:
                pass
            else:
                f["system_prompt"] = str(sp)
        if "model" in data:
            # Folder-level model override. Empty string clears (new threads
            # fall back to the kernel's default think-provider chain).
            mv = data.get("model")
            if mv is None:
                pass
            else:
                f["model"] = str(mv).strip()
        f["updated"] = _now()
        self._save(state)
        return f

    @web_route("DELETE", "/api/folders/{folder_id}")
    async def api_delete_folder(self, request):
        fid = request.path_params["folder_id"]
        state = self._load()
        if fid not in state.get("folders", {}):
            return {"error": "not found"}
        # Threads stay (apps/rooms/ owns them); we just drop the folder shell.
        f = state["folders"].pop(fid)
        self._save(state)
        await self.emit("portal:folder_deleted", {"id": fid, "name": f.get("name", "")})
        return {"deleted": fid, "freed_threads": list(f.get("thread_ids") or [])}

    # ── Folders → chat projects (chat-first, desktop GUI plan B2) ────

    @web_route("POST", "/api/folders/migrate")
    async def api_migrate_folders(self, request):
        """Copy every folder into an agent chat project: its name, and its
        instructions (``system_prompt``) as the project's instructions. Agent
        threads in the folder (``agent:<sid>``) move into the project; Rooms
        and Assistant threads stay in the folder, which is kept.

        Re-runnable: a folder records ``chat_project_id`` as soon as its
        project exists, and is skipped after that — so a run cut short by an
        error resumes instead of duplicating. ``folders.json`` (only that file:
        the agent's projects are not in it) is backed up before the first
        change of each run. A folder's model override is not copied — a chat's
        model is chosen per chat. A chat already in a project stays there.
        Chat-first only (``feature.chat-first.enabled``).
        """
        if not self.app_config("feature.chat-first.enabled", False):
            return {"error": "chat-first is off"}
        # Serialised: two overlapping runs (two tabs, a double click) would
        # both read the same to-do list and create every project twice.
        async with self.write_lock("folders-migrate"):
            todo = [f for f in self._load().get("folders", {}).values() if not f.get("chat_project_id")]
            if not todo:
                return {"ok": True, "migrated": [], "failed": [], "backup": ""}
            backup = self._backup_folders()
            migrated, failed = [], []
            for f in todo:
                try:
                    project = await self.call_app(
                        "agent", "create_chat_project",
                        name=(f.get("name") or "Folder")[:MIGRATE_NAME_MAX],
                        instructions=f.get("system_prompt") or "",
                    )
                except Exception as e:  # one folder must not strand the rest
                    project = {"error": f"{type(e).__name__}: {e}"}
                if not isinstance(project, dict) or not project.get("id"):
                    failed.append({"folder": f["id"], "name": f.get("name", ""),
                                   "error": (project or {}).get("error", "create failed")})
                    continue
                self._mark_migrated(f["id"], project["id"])
                moved = 0
                for tid in f.get("thread_ids") or []:
                    if not tid.startswith("agent:"):
                        continue
                    try:
                        r = await self.call_app(
                            "agent", "assign_chat_project",
                            session_id=tid[len("agent:"):], project_id=project["id"], keep_existing=True,
                        )
                    except Exception:
                        r = None
                    moved += 1 if isinstance(r, dict) and r.get("ok") and not r.get("kept") else 0
                migrated.append({"folder": f["id"], "project": project["id"], "name": project["name"], "chats_moved": moved})
            return {"ok": not failed, "migrated": migrated, "failed": failed, "backup": backup.name}

    def _backup_folders(self) -> Path:
        """Copy folders.json aside under a name no earlier backup has."""
        stamp = _now().replace(":", "").replace("-", "")[:15]
        backup = self._folders_path.with_name(f"folders.backup-{stamp}.json")
        n = 1
        while backup.exists():
            backup = self._folders_path.with_name(f"folders.backup-{stamp}-{n}.json")
            n += 1
        backup.write_bytes(self._folders_path.read_bytes())
        return backup

    def _mark_migrated(self, fid: str, project_id: str) -> None:
        """Record a folder's project at once. Read-modify-write with no await
        between, so a folder request landing during the run is not lost."""
        state = self._load()
        folder = state.get("folders", {}).get(fid)
        if folder is not None:
            folder["chat_project_id"] = project_id
            folder["updated"] = _now()
            self._save(state)

    # ── Thread membership ────────────────────────────────────────────

    @web_route("POST", "/api/folders/{folder_id}/threads")
    async def api_attach_thread(self, request):
        fid = request.path_params["folder_id"]
        data = await self.read_json(request)
        thread_id = (data.get("thread_id") or "").strip()
        if not thread_id:
            return {"error": "thread_id required"}
        state = self._load()
        f = state.get("folders", {}).get(fid)
        if not f:
            return {"error": "folder not found"}
        # One folder per thread — pull from prior folder if any.
        prior = self._folder_of_thread(state, thread_id)
        if prior and prior != fid:
            prior_f = state["folders"].get(prior)
            if prior_f:
                prior_f["thread_ids"] = [t for t in (prior_f.get("thread_ids") or []) if t != thread_id]
                prior_f["updated"] = _now()
        # Idempotent: insert at top if not already present.
        ids = [t for t in (f.get("thread_ids") or []) if t != thread_id]
        ids.insert(0, thread_id)
        f["thread_ids"] = ids
        f["updated"] = _now()
        self._save(state)
        return f

    @web_route("DELETE", "/api/folders/{folder_id}/threads/{thread_id}")
    async def api_detach_thread(self, request):
        fid = request.path_params["folder_id"]
        thread_id = request.path_params["thread_id"]
        state = self._load()
        f = state.get("folders", {}).get(fid)
        if not f:
            return {"error": "folder not found"}
        f["thread_ids"] = [t for t in (f.get("thread_ids") or []) if t != thread_id]
        f["updated"] = _now()
        self._save(state)
        return f

    # ── Discovery helper: which folder owns each thread ──────────────
    #
    # The UI fetches /api/agents (from rooms) AND /api/folders (from portal),
    # then assembles the sidebar. This endpoint is a convenience: given a
    # thread id, return the folder id (or null). Avoids the UI having to do
    # the inverse lookup itself when only one thread is in scope.

    @web_route("GET", "/api/threads/{thread_id}/folder")
    async def api_folder_of_thread(self, request):
        thread_id = request.path_params["thread_id"]
        state = self._load()
        return {"thread_id": thread_id, "folder_id": self._folder_of_thread(state, thread_id)}

    # ── Pinned threads ──────────────────────────────────────────────
    #
    # Cross-device pin sync. Stored at data/apps/portal/pins.json — a
    # separate file from folders.json so the two don't fight at write
    # time and so the schema can evolve independently. The shape is a
    # flat ordered list of thread ids; newest pin floats to the top so
    # the UI shows recent intent first.

    def _pins_path(self) -> Path:
        return self.data_dir / "pins.json"

    def _load_pins(self) -> list[str]:
        p = self._pins_path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                threads = data.get("threads", [])
            elif isinstance(data, list):
                threads = data
            else:
                return []
            return [str(t) for t in threads if t]
        except (json.JSONDecodeError, ValueError, OSError):
            return []

    def _save_pins(self, threads: list[str]) -> None:
        p = self._pins_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "threads": threads, "updated": _now()}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(p)

    @web_route("GET", "/api/pins")
    async def api_list_pins(self, request):
        return {"threads": self._load_pins()}

    @web_route("POST", "/api/pins/{thread_id}")
    async def api_pin_thread(self, request):
        thread_id = (request.path_params.get("thread_id") or "").strip()
        if not thread_id:
            return {"error": "thread_id required"}
        pins = [t for t in self._load_pins() if t != thread_id]
        pins.insert(0, thread_id)  # newest pin floats to top
        self._save_pins(pins)
        return {"threads": pins}

    @web_route("DELETE", "/api/pins/{thread_id}")
    async def api_unpin_thread(self, request):
        thread_id = (request.path_params.get("thread_id") or "").strip()
        pins = [t for t in self._load_pins() if t != thread_id]
        self._save_pins(pins)
        return {"threads": pins}
