"""Rooms — room shape, persona system, CLI participant dispatch.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: participant normalization, responder resolution, register/unregister persona contributed by other apps, agent-runtime CLI dispatch (claude-cli + codex + gemini).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self.service('agent-runtime')`` for CLI dispatch; ``self._gate_server_actions`` (pending.py).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.do_token import extract_do_tokens
from emptyos.sdk.utils import parse_llm_json

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────
#   _normalize_participants  = _participants._normalize_participants
#   _room_kind               = _participants._room_kind
#   _resolve_responder_id    = _participants._resolve_responder_id
#   _resolve_responder       = _participants._resolve_responder
#   _new_room_id             = _participants._new_room_id
#   _build_cli_prompt        = _participants._build_cli_prompt
#   _build_cli_system        = _participants._build_cli_system
#   _dispatch_cli_turn       = _participants._dispatch_cli_turn
#   register_persona         = _participants.register_persona
#   unregister_persona       = _participants.unregister_persona
#   add_participant          = _participants.add_participant
#   remove_participant       = _participants.remove_participant
#   api_add_participant      = _participants.api_add_participant
#   api_remove_participant   = _participants.api_remove_participant
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


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
    """1on1 vs group, derived from participants. A room with 2+ responders
    (agent or cli, in any combination) is a group — matches create_room's
    validation rule. Caching on the record is the caller's choice."""
    parts = self._normalize_participants(room)
    responders = [p for p in parts if p.get("type") in ("agent", "cli")]
    return "group" if len(responders) > 1 else "1on1"


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
        f"note.create({{title, body}}). "
        f"To WRITE OR EDIT a vault markdown file, emit "
        f'[DO:rooms.write_note({{"path":"<vault-relative path>","content":"<full file content>"}})]. '
        f"The user sees a line-by-line diff and approves before any file "
        f"is touched. Always use this verb for vault edits — do not use "
        f"Edit/Write tools or other apps' write methods for vault files. "
        f"When unsure of the exact app or method, still emit a best guess "
        f"— failed applies surface in the UI and the user can tell you "
        f"the right one."
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


async def register_persona(
    self,
    *,
    id: str,
    name: str,
    system_prompt: str,
    model: str = "",
    source: str = "",
    emoji: str = "",
) -> dict:
    """Idempotent: create-or-update a 1:1 rooms agent from another
    app's persona definition. Preserves any existing knowledge_files,
    tools, server_actions, etc. — only the fields the caller owns are
    overwritten."""
    if not id or not name:
        return {"error": "id and name required"}
    existing = self._load_agent(id) or {}
    # Reject a stomp: if a record exists with a different source, the
    # caller doesn't own it. Empty existing source = legacy or
    # user-created; allow the upgrade.
    if existing and existing.get("source") and existing.get("source") != source:
        return {"error": f"agent '{id}' belongs to '{existing.get('source')}'"}
    agent = {
        "id": id,
        "name": name,
        "tier": existing.get("tier", "1:1"),
        "system_prompt": (system_prompt or "").strip() or "You are a helpful assistant.",
        "knowledge_files": existing.get("knowledge_files", []),
        "knowledge_dir": existing.get("knowledge_dir", ""),
        "knowledge_char_limit": existing.get("knowledge_char_limit", 2000),
        "model": model,
        "effort": existing.get("effort", ""),
        "tools": existing.get("tools", []),
        "server_actions": existing.get("server_actions", {}),
        "temperature": existing.get("temperature"),
        "builtin": False,
        "source": source,
        "emoji": emoji,
        "created": existing.get("created") or datetime.now(timezone.utc).isoformat(),
    }
    self._save_agent(agent)
    return agent


async def unregister_persona(self, *, id: str, source: str = "") -> dict:
    """Remove a persona record. Only succeeds if the existing record
    carries the same source — prevents one app from deleting another
    app's mirror."""
    existing = self._load_agent(id)
    if not existing:
        return {"ok": True, "removed": False}
    existing_source = existing.get("source") or ""
    if existing_source != source:
        return {"error": f"agent '{id}' belongs to '{existing_source or 'user'}'"}
    path = self._agent_path(id)
    try:
        path.unlink()
        self.kernel.agents.invalidate(id)
    except FileNotFoundError:
        pass
    return {"ok": True, "removed": True}


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


@web_route("POST", "/api/rooms/{room_id}/participants")
async def api_add_participant(self, request):
    room_id = request.path_params["room_id"]
    data = await request.json()
    return await self.add_participant(room_id, data.get("participant") or data)


@web_route("DELETE", "/api/rooms/{room_id}/participants/{participant_id}")
async def api_remove_participant(self, request):
    room_id = request.path_params["room_id"]
    participant_id = request.path_params["participant_id"]
    return self.remove_participant(room_id, participant_id)
