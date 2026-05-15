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
#   api_clear_history    = _agents.api_clear_history
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _agents_dir(self) -> Path:
    d = self.data_dir / "agents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _history_dir(self) -> Path:
    d = self.data_dir / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
