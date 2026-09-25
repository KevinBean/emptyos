"""Rooms — agent CRUD + chat history I/O.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: per-agent JSON store (``data/apps/rooms/agents/<id>.json``), per-room chat history (``data/apps/rooms/history/<id>.json``), plus all agent CRUD + history endpoints.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import path_segment_error, require_path_segment, safe_path_segment

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────
#   _agents_dir          = _agents._agents_dir
#   _history_dir         = _agents._history_dir
#   _agent_path          = _agents._agent_path
#   _history_path        = _agents._history_path
#   _load_agent          = _agents._load_agent
#   _save_agent          = _agents._save_agent
#   _list_agents         = _agents._list_agents
#   _find_agent_by_name  = _agents._find_agent_by_name
#   _load_history        = _agents._load_history
#   _save_history        = _agents._save_history
#   _walk_to_head        = staticmethod(_agents._walk_to_head)        # tree branch traversal
#   _ensure_message_ids  = staticmethod(_agents._ensure_message_ids)  # lazy migration
#   get_agent            = _agents.get_agent
#   list_agents          = _agents.list_agents
#   save_agent           = _agents.save_agent
#   has_agent            = _agents.has_agent
#   api_list_agents      = _agents.api_list_agents
#   api_get_agent        = _agents.api_get_agent
#   api_create_agent     = _agents.api_create_agent
#   api_update_agent     = _agents.api_update_agent
#   api_delete_agent     = _agents.api_delete_agent
#   api_get_history      = _agents.api_get_history
#   clear_room_history   = _agents.clear_room_history
#   api_clear_history    = _agents.api_clear_history
#   _ensure_message_ids  = _agents._ensure_message_ids
#   _walk_to_head        = _agents._walk_to_head
#   api_verb_menu        = _agents.api_verb_menu
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _agents_dir(self) -> Path:
    return self.data_subdir("agents")


def _history_dir(self) -> Path:
    return self.data_subdir("history")


def _agent_path(self, agent_id: str) -> Path | None:
    """Agent record path, or None when agent_id isn't a plain slug.

    agent_id arrives from a DELETE path param and the result is unlink()ed
    after only an .exists() check, so a raw `..` deleted an arbitrary .json.
    """
    if not safe_path_segment(agent_id):
        return None
    return self._agents_dir() / f"{agent_id}.json"


def _history_path(self, agent_id: str) -> Path | None:
    """History path — same untrusted id, same unlink() sink."""
    if not safe_path_segment(agent_id):
        return None
    return self._history_dir() / f"{agent_id}.json"


def _load_agent(self, agent_id: str) -> dict | None:
    p = self._agent_path(agent_id)
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_agent(self, agent: dict):
    p = self._agent_path(agent["id"])
    if p is None:
        require_path_segment(agent["id"], "agent id")  # raises with the shared message
    p.write_text(
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


def _ensure_message_ids(messages: list[dict]) -> bool:
    """Backfill `entry_id` + `parent_entry_id` on legacy messages.

    Mutates *messages* in place. Returns True if anything changed, so the
    caller can persist the migration. Linear history before this change
    becomes a degenerate tree: every message's parent is the previous one;
    the first message has parent_entry_id == None.
    """
    import uuid as _uuid
    changed = False
    prev_id: str | None = None
    for m in messages:
        if not m.get("entry_id"):
            m["entry_id"] = _uuid.uuid4().hex[:12]
            changed = True
        if "parent_entry_id" not in m:
            m["parent_entry_id"] = prev_id
            changed = True
        prev_id = m["entry_id"]
    return changed


def _walk_to_head(messages: list[dict], head_entry_id: str | None) -> list[dict]:
    """Return the linear chain root → head from a (possibly branched) history.

    If head is unknown or null, returns the original list unchanged (so
    callers that don't yet know about branching still see all messages).
    Useful precondition for prompt-building and history rendering once a
    room actually has multiple branches.
    """
    if not head_entry_id:
        return messages
    by_id = {m.get("entry_id"): m for m in messages if m.get("entry_id")}
    if head_entry_id not in by_id:
        return messages
    chain: list[dict] = []
    cur: str | None = head_entry_id
    visited: set[str] = set()
    while cur and cur in by_id and cur not in visited:
        visited.add(cur)
        chain.append(by_id[cur])
        cur = by_id[cur].get("parent_entry_id")
    chain.reverse()
    return chain


def _load_history(self, agent_id: str) -> list[dict]:
    """Read flat history, backfill entry/parent ids if missing.

    Returns the raw flat list (all branches inclusive). Callers wanting a
    single chain (for prompt-building, rendering) should pass through
    `_walk_to_head` with the room's `current_head_entry_id`.
    """
    p = self._history_path(agent_id)
    if p is None or not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        messages = data.get("messages", [])
    except Exception:
        return []
    # One-time migration: if any message lacks ids, backfill + persist so
    # the room is tree-shaped from now on. Cheap — only writes when changed.
    if _ensure_message_ids(messages):
        try:
            p.write_text(
                json.dumps({"agent_id": agent_id, "messages": messages},
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass  # next save will retry
    return messages


def _save_history(self, agent_id: str, messages: list[dict]):
    # Cap history to last 200 messages to prevent unbounded growth.
    # NOTE: when branches accumulate, this cap drops the oldest entries
    # across ALL branches uniformly. Acceptable for v1 — branching is
    # rare enough that we'd hit it only after a power user really uses it.
    if len(messages) > 200:
        messages = messages[-200:]
    # Defensive: ensure ids stay populated on save (new messages get ids
    # before being appended, but a manual splice that drops them would
    # otherwise re-trigger migration on next load).
    _ensure_message_ids(messages)
    hp = self._history_path(agent_id)
    if hp is None:
        require_path_segment(agent_id, "agent id")  # raises with the shared message
    hp.write_text(
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


@web_route("GET", "/api/verb-menu")
async def api_verb_menu(self, request):
    """Verbs offered to the agent surface (unified registry, surfaces ∋ "agent"),
    grouped by app — the menu the agent-config picklist draws from.

    IMPORTANT: ``surfaces ∋ "agent"`` means a verb is *offered* (available to add
    to an agent), NOT granted. The per-agent ``server_actions`` allowlist remains
    the execution gate — adding a verb here still writes ``server_actions`` JSON.
    Read-only. See .claude/rules/verb-registry.md.
    """
    by_app: dict[str, list[dict]] = {}
    try:
        registry = self.kernel.apps.get_verbs()
    except Exception:
        registry = None
    if registry is not None:
        for e in registry.for_surface("agent"):
            by_app.setdefault(e.app_id, []).append({
                "verb": e.verb,
                "method": e.method,
                "summary": e.summary,
                "args": e.args or {},
                "eligibility": e.eligibility,
            })
    return {"apps": by_app, "count": sum(len(v) for v in by_app.values())}


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
        "provider": data.get("provider", ""),
        "model": data.get("model", ""),
        "effort": data.get("effort", ""),
        "tools": data.get("tools", []),
        "server_actions": data.get("server_actions", {}),
        "gate_mode": data.get("gate_mode", "auto"),
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
                 "knowledge_char_limit", "provider", "model", "effort", "tools",
                 "temperature", "tier", "server_actions", "gate_mode", "auto_route")
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
    if agent_path is None or history_path is None:
        return {"error": path_segment_error(agent_id, "agent id")}
    agent_path.unlink()
    if history_path.exists():
        history_path.unlink()
    self.kernel.agents.invalidate(agent_id)
    return {"deleted": agent_id}


@web_route("GET", "/api/history/{agent_id}")
async def api_get_history(self, request):
    agent_id = request.path_params["agent_id"]
    messages = self._load_history(agent_id)
    return {"agent_id": agent_id, "messages": messages}


def clear_room_history(self, agent_id: str) -> dict:
    """Start a fresh session for a room: drop its history file and reset the
    branch head pointer. Cross-app callable (the telegram bridge's /clear).
    Idempotent — clearing an empty room is a no-op that still returns ok.
    """
    p = self._history_path(agent_id)
    if p is None:
        return {"error": path_segment_error(agent_id, "agent id")}
    if p.exists():
        p.unlink()
    # Reset the branch head so the next turn doesn't dangle off a deleted entry.
    room = self._load_agent(agent_id)
    if room and room.get("current_head_entry_id"):
        room["current_head_entry_id"] = None
        self._save_agent(room)
    return {"cleared": agent_id, "ok": True}


@web_route("DELETE", "/api/history/{agent_id}")
async def api_clear_history(self, request):
    return self.clear_room_history(request.path_params["agent_id"])
