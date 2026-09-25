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
from emptyos.sdk.claude_run_stream import transform_stream_json_obj
from emptyos.sdk.do_token import extract_do_tokens
from emptyos.sdk.utils import path_segment_error

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────
#   _normalize_participants  = _participants._normalize_participants
#   _room_kind               = _participants._room_kind
#   _resolve_responder_id    = _participants._resolve_responder_id
#   _resolve_responder       = _participants._resolve_responder
#   _new_room_id             = _participants._new_room_id
#   _strip_room_mentions     = _participants._strip_room_mentions  # rm @id tokens before CLI dispatch
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
            if isinstance(p, dict):
                if p.get("type") == "user" and not p.get("id"):
                    p = {**p, "id": "me"}
                # Team mode: every participant carries a role. Default "peer"
                # (no team behaviour) so legacy rooms read unchanged. A room
                # becomes a "team" only once a participant is given "lead".
                if "role" not in p:
                    p = {**p, "role": "peer"}
            out.append(p)
        return out
    return [
        {"type": "user", "id": "me", "role": "peer"},
        {"type": "agent", "id": room["id"], "role": "peer"},
    ]


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


async def _resolve_responder(
    self, text: str, parts: list[dict], room: dict | None = None,
) -> "tuple[dict | None, dict | None]":
    """Pick a responder participant (agent OR cli). Returns
    ``(participant, auto_meta)`` so the caller can branch on `type` and
    knows whether the pick was an explicit @mention/single-responder
    resolution (``auto_meta is None``) or an LLM auto-route (``auto_meta``
    carries a short reason — never silent, per CLAUDE.md's north star).

    Same @mention rules as `_resolve_responder_id` but also matches
    cli participants by id. When 2+ responders exist, no @mention
    matches, and `room["auto_route"]` is truthy, classifies the message
    via `self.select()` against each responder's `specialty`/`role`
    instead of blindly defaulting to the first participant.
    """
    responders = [p for p in parts if p.get("type") in ("agent", "cli")]
    if not responders:
        return None, None
    if len(responders) == 1:
        return responders[0], None
    for m in re.finditer(r"@([A-Za-z0-9_\-]+)", text or ""):
        mention = m.group(1).strip().lower()
        for p in responders:
            pid = (p.get("id") or "").lower()
            if pid == mention or pid.replace("-", "") == mention.replace("-", ""):
                return p, None
            if p.get("type") == "agent":
                a = self._load_agent(p["id"])
                if a:
                    name = (a.get("name") or "").lower()
                    if name == mention or name.replace(" ", "-") == mention:
                        return p, None
    if room and room.get("auto_route"):
        choices = {
            p["id"]: p.get("specialty") or p.get("role") or f"{p['type']} participant"
            for p in responders
        }
        chosen_id = await self.select(
            text, choices, default=responders[0]["id"], min_ability="weak",
        )
        chosen = next((p for p in responders if p["id"] == chosen_id), responders[0])
        return chosen, {"reason": f"auto-routed to {chosen_id}"}
    return responders[0], None


def _new_room_id(self, prefix: str = "room") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _strip_room_mentions(self, text: str, room: dict) -> str:
    """Strip @<id|name> tokens that match this room's participants.

    The @mention is rooms-routing syntax; once we've resolved the responder
    the token is dead weight inside the prompt. Worse, several text CLIs
    (pi, codex, aider) interpret bare `@<token>` as a file-include directive
    and fail with "File not found" when the token doesn't resolve. Other
    @ tokens (e.g. `@apps/rooms/app.py` — pi's genuine file feature) are
    left intact so power users can still leverage them.
    """
    if not text:
        return text
    ids: set[str] = set()
    for p in self._normalize_participants(room):
        pid = (p.get("id") or "").lower()
        if pid:
            ids.add(pid)
            ids.add(pid.replace("-", ""))
        if p.get("type") == "agent":
            a = self._load_agent(p.get("id", ""))
            if a:
                name = (a.get("name") or "").lower()
                if name:
                    ids.add(name)
                    ids.add(name.replace(" ", "-"))
                    ids.add(name.replace(" ", ""))
    def _repl(m: "re.Match[str]") -> str:
        token = m.group(1).lower()
        if token in ids or token.replace("-", "") in {i.replace("-", "") for i in ids}:
            return ""
        return m.group(0)
    return re.sub(r"@([A-Za-z0-9_\-]+)\s?", _repl, text).strip()


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
        t = self._strip_room_mentions((m.get("text") or "").strip(), room)
        if t:
            lines.append(f"{speaker}: {t}")
    lines.append(f"{user_id}: {self._strip_room_mentions(text, room)}")
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
    base = (
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
    # Team mode: append a lead/worker coordination block when this room is a
    # team and this participant has a role. Empty for non-team rooms / peers.
    team_block = self._team_prompt_block(room, cli_part)
    if team_block:
        base += "\n\n" + team_block
    return base


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

    adapter_info: dict = {}
    if cli_id != "claude-cli":
        # `text_cli_run` threads no tool restriction, so an adapter with a
        # measured filesystem escape must not silently inherit the vault as
        # its cwd (the default computed above). On Windows codex reports
        # `sandbox: read-only` and writes anyway, which leaves cwd as the only
        # real containment — see .claude/rules/multi-cli-participants.md. An
        # explicit cwd is the user's deliberate choice and passes through.
        try:
            adapter_info = runtime.cli_adapter_info(cli_id) or {}
        except Exception:
            adapter_info = {}
        if adapter_info.get("writes_unsandboxed") and not cli_part.get("cwd"):
            yield {
                "text": (
                    f"[{cli_id} can write the filesystem on this platform despite its "
                    f"read-only flag, so it was not run with the default vault working "
                    f"directory. Set an explicit `cwd` on this participant — a scratch "
                    f"or project directory — to use it here.]"
                ),
                "done": True, "error": True,
                "responder_id": cli_id, "actor_type": "cli",
            }
            return

    # Buffered path — CLIs that print a plain-text reply and emit no event
    # stream. The whole reply lands as one text chunk, with no tool cards.
    # Adapters that declare `stream_json` (codex, via `codex exec --json`)
    # skip this and take the streaming path below instead.
    if cli_id != "claude-cli" and not adapter_info.get("stream_json"):
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
        model_label = (cli_part.get("model") or result.get("model") or "").strip()
        if text_out:
            yield {"text": text_out, "done": False}
        yield {
            "text": "", "done": True, "full": text_out,
            "responder_id": cli_id, "actor_type": "cli",
            "model": model_label,
        }
        return

    # Streaming path — claude-cli natively, plus any adapter declaring
    # `stream_json`. Both emit line-delimited events that
    # `transform_stream_json_obj` normalizes into one canonical vocabulary, so
    # everything below this point is dialect-agnostic and codex reuses it
    # unchanged rather than duplicating the branch (three dialects share the
    # parser — emptyos/sdk/claude_run_stream.py).
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
            if cli_id == "claude-cli":
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
            else:
                # Same line callback, different spawner. `text_cli_run`
                # already forwards raw stdout lines, so a stream_json adapter
                # needs no bespoke runner — only its `--json` flag.
                result = await runtime.text_cli_run(
                    cli_id=cli_id,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    cwd=cwd,
                    timeout_s=timeout_s,
                    extra_args=cli_part.get("extra_args") or None,
                    on_stdout_line=on_line,
                )
            if isinstance(result, dict) and "error" in result:
                await queue.put({"_error": result["error"]})
            else:
                await queue.put({"_done": True})
        except Exception as e:
            await queue.put({"_error": str(e)[:200]})
        finally:
            # `on_line` hands events over with `call_soon_threadsafe`, which
            # defers the put by a loop iteration. Posting the sentinel without
            # yielding first can therefore close the consumer while the last
            # events are still queued — a CLI that dumps its output and exits
            # immediately loses its final tool cards. One turn is enough to
            # flush the deferred callbacks.
            await asyncio.sleep(0)
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
            # Parse claude-cli stream-json via the shared dialect parser
            # (emptyos/sdk/claude_run_stream.py) — single source of truth for
            # the Anthropic stream-json shape — then map its canonical events to
            # this room's {text/tool_use/tool_result, done} chunk shape.
            for canon in transform_stream_json_obj(evt, preview_chars=500):
                ctype = canon.get("type")
                if ctype == "chunk":
                    t = canon.get("text", "")
                    if t:
                        full_text += t
                        yield {"text": t, "done": False}
                elif ctype == "tool_use":
                    yield {
                        "tool_use": {
                            "name": canon.get("tool"),
                            "input": canon.get("input"),
                            "id": canon.get("id"),
                        },
                        "done": False,
                    }
                elif ctype == "tool_result":
                    yield {
                        "tool_result": {
                            "tool_use_id": canon.get("tool_use_id"),
                            "content": canon.get("preview", ""),
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
        # Same precedence the buffered path uses: the participant's own model
        # wins, then the adapter's declared label. Without the second term a
        # stream_json CLI shows an empty chip even when its adapter names a
        # model — the buffered path reads that off text_cli_run's return,
        # which the streaming path never sees.
        "model": (cli_part.get("model") or adapter_info.get("model") or "").strip(),
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
    gate_mode: str = "",
    server_actions: dict | None = None,
    keep_existing_prompt: bool = False,
) -> dict:
    """Idempotent: create-or-update a 1:1 rooms agent from another
    app's persona definition. Preserves any existing knowledge_files,
    tools, server_actions, etc. — only the fields the caller owns are
    overwritten. ``gate_mode`` / ``server_actions`` are set only when
    provided, so existing callers never clobber prior values (first
    consumer: the telegram two-way bridge, which needs gate_mode="gate"
    so every [DO:] lands as a review card).

    ``keep_existing_prompt=True`` makes ``system_prompt`` a seed used
    only on first create — an existing non-empty prompt (e.g. one the
    user tuned in /rooms/) survives re-registration on restart."""
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
        "system_prompt": (
            existing.get("system_prompt")
            if keep_existing_prompt and (existing.get("system_prompt") or "").strip()
            else (system_prompt or "").strip() or "You are a helpful assistant."
        ),
        "knowledge_files": existing.get("knowledge_files", []),
        "knowledge_dir": existing.get("knowledge_dir", ""),
        "knowledge_char_limit": existing.get("knowledge_char_limit", 2000),
        "model": model,
        "effort": existing.get("effort", ""),
        "tools": existing.get("tools", []),
        "server_actions": (
            server_actions if server_actions is not None
            else existing.get("server_actions", {})
        ),
        "gate_mode": gate_mode or existing.get("gate_mode", ""),
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
    if path is None:
        return {"error": path_segment_error(id, "agent id")}
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
    # Applies to agent AND cli participants — it's a routing hint for
    # room["auto_route"], not CLI config.
    if participant.get("specialty"):
        clean["specialty"] = participant["specialty"]
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
