"""Rooms — room features (lifecycle / memory / distill / search / panels / voice / tasks).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: create/list/archive/export/inspect rooms, memory + knowledge + pin, catch-up + distill summarization, vault search + wikilinks, hub panels, voice intents, task linkage, agent suggestion, message search.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: most other modules — this is the surface that aggregates the rest.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import cli_command, web_route
from emptyos.sdk.utils import parse_llm_json

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────
#   _WIKILINK_RE          = _rooms_core._WIKILINK_RE
#   _MAX_REF_BYTES        = _rooms_core._MAX_REF_BYTES
#   _extract_wikilinks    = _rooms_core._extract_wikilinks
#   _resolve_wikilinks    = _rooms_core._resolve_wikilinks
#   api_vault_search      = _rooms_core.api_vault_search
#   _STOPWORDS            = _rooms_core._STOPWORDS
#   suggest_agents        = _rooms_core.suggest_agents
#   api_suggest_agents    = _rooms_core.api_suggest_agents
#   search_messages       = _rooms_core.search_messages
#   panel_pending_count   = _rooms_core.panel_pending_count
#   panel_recent_rooms    = _rooms_core.panel_recent_rooms
#   voice_list_rooms      = _rooms_core.voice_list_rooms
#   voice_open_room       = _rooms_core.voice_open_room
#   cmd_rooms             = _rooms_core.cmd_rooms
#   api_vault_export      = _rooms_core.api_vault_export
#   list_rooms            = _rooms_core.list_rooms
#   archive_room          = _rooms_core.archive_room
#   unarchive_room        = _rooms_core.unarchive_room
#   export_room           = _rooms_core.export_room
#   inspect_context       = _rooms_core.inspect_context
#   MAX_MEMORY_ENTRIES    = _rooms_core.MAX_MEMORY_ENTRIES
#   add_memory            = _rooms_core.add_memory
#   remove_memory         = _rooms_core.remove_memory
#   list_memory           = _rooms_core.list_memory
#   _memory_block         = _rooms_core._memory_block
#   add_knowledge         = _rooms_core.add_knowledge
#   remove_knowledge      = _rooms_core.remove_knowledge
#   pin_message           = _rooms_core.pin_message
#   unpin_message         = _rooms_core.unpin_message
#   CATCH_UP_SYSTEM       = _rooms_core.CATCH_UP_SYSTEM
#   catch_me_up           = _rooms_core.catch_me_up
#   DISTILL_SYSTEM        = _rooms_core.DISTILL_SYSTEM
#   distill_room          = _rooms_core.distill_room
#   create_room           = _rooms_core.create_room
#   api_list_rooms        = _rooms_core.api_list_rooms
#   api_create_room       = _rooms_core.api_create_room
#   api_archive_room      = _rooms_core.api_archive_room
#   api_unarchive_room    = _rooms_core.api_unarchive_room
#   api_export_room       = _rooms_core.api_export_room
#   api_distill_room      = _rooms_core.api_distill_room
#   api_inspect_context   = _rooms_core.api_inspect_context
#   api_list_memory       = _rooms_core.api_list_memory
#   api_add_memory        = _rooms_core.api_add_memory
#   api_remove_memory     = _rooms_core.api_remove_memory
#   api_add_knowledge     = _rooms_core.api_add_knowledge
#   api_remove_knowledge  = _rooms_core.api_remove_knowledge
#   api_pin_message       = _rooms_core.api_pin_message
#   api_unpin_message     = _rooms_core.api_unpin_message
#   api_catch_up          = _rooms_core.api_catch_up
#   attach_task           = _rooms_core.attach_task
#   tasks_for_room        = _rooms_core.tasks_for_room
#   api_room_tasks        = _rooms_core.api_room_tasks
#   api_attach_task       = _rooms_core.api_attach_task
#   api_search            = _rooms_core.api_search
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


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
            if q in (e.get("name") or "").lower() or q in (e.get("path") or "").lower()
        ]
    entries.sort(key=lambda e: e.get("modified", 0), reverse=True)
    files = [
        {"path": e.get("path", ""), "name": e.get("name", ""),
         "folder": e.get("folder", "")}
        for e in entries[:limit]
    ]
    return {"files": files}


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
    data = await self.safe_json(request)
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
    data = await self.safe_json(request)
    return await self.add_knowledge(
        room_id, (data.get("path") or "").strip(),
    )


@web_route("POST", "/api/rooms/{room_id}/knowledge/remove")
async def api_remove_knowledge(self, request):
    # POST/remove instead of DELETE because vault paths often contain
    # slashes / special chars that break URL routing — body-payload is safer.
    room_id = request.path_params["room_id"]
    data = await self.safe_json(request)
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
    data = await self.safe_json(request)
    since = (data.get("since") or "").strip() if isinstance(data, dict) else ""
    return await self.catch_me_up(room_id, since)


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
