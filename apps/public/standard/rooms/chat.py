"""Rooms — core chat execution (prompt building + streaming + sync).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: ``_build_system`` / ``_build_prompt`` / ``_assemble_prompt``, the ``_chat`` orchestrator, the ``api_chat`` + ``api_chat_stream`` HTTP surfaces, and the ``api_do`` direct-action endpoint.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._gate_server_actions`` (pending.py), ``self._dispatch_cli_turn`` (participants.py), ``self._load_agent`` / ``_save_history`` (agents.py).
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

from emptyos.sdk import ndjson_response, web_route
from emptyos.sdk.do_token import extract_do_tokens
from emptyos.sdk.utils import parse_llm_json

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────
#   chat                     = _chat.chat
#   _build_system            = _chat._build_system
#   _build_prompt            = _chat._build_prompt
#   _build_prompt_async      = _chat._build_prompt_async
#   _expand_at_paths         = _chat._expand_at_paths  # @<path> file-include resolver
#   _assemble_prompt         = _chat._assemble_prompt  # @staticmethod
#   _chat                    = _chat._chat
#   _vault_log_chat          = _chat._vault_log_chat
#   api_chat                 = _chat.api_chat
#   api_do                   = _chat.api_do
#   _action_result_links     = _chat._action_result_links  # @staticmethod
#   api_chat_stream          = _chat.api_chat_stream
#   api_debug_system_prompt  = _chat.api_debug_system_prompt
#   _run_participant_turn    = _chat._run_participant_turn
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


_SLASH_RE = re.compile(r"^(/[A-Za-z][A-Za-z0-9_-]*)\b(.*)$", re.DOTALL)


async def _maybe_apply_skill_overlay(app, text: str, responder: dict):
    """Detect `/<slash> ...` prefix, look up the skill via call_app, return
    (stripped_text, overlaid_responder). Falls through silently when:
      - text has no leading slash
      - skill app isn't loaded
      - slash doesn't match a registered skill

    The overlay layers the skill body BELOW the room's existing prompt
    (room persona stays primary) and unions server_actions per app.
    """
    if not text.startswith("/"):
        return text, responder
    m = _SLASH_RE.match(text)
    if not m:
        return text, responder
    slash, rest = m.group(1), m.group(2).lstrip()
    try:
        skill = await app.call_app("skill", "by_slash", slash=slash)
    except Exception:
        return text, responder
    if not skill or not isinstance(skill, dict):
        return text, responder

    # Layer: room prompt first (persona wins ties), skill body second.
    base_prompt = responder.get("system_prompt") or ""
    skill_body = (skill.get("body") or "").strip()
    merged_prompt = f"{base_prompt}\n\n---\n\n{skill_body}" if base_prompt else skill_body

    # Union server_actions: keep all of the room's, add any new ones from
    # the skill. Per-app methods deduped, order preserved (room-first so
    # the LLM sees room's verbs ranked higher when it picks tools).
    base_actions = dict(responder.get("server_actions") or {})
    for app_id, methods in (skill.get("server_actions") or {}).items():
        existing = list(base_actions.get(app_id) or [])
        for meth in methods:
            if meth not in existing:
                existing.append(meth)
        base_actions[app_id] = existing

    overlaid = dict(responder)
    overlaid["system_prompt"] = merged_prompt
    overlaid["server_actions"] = base_actions
    # Skills don't persist — this is a per-turn synthesized record.

    await app.emit(
        "skill:invoked",
        {"name": skill.get("name"), "slash": slash, "room_id": responder.get("id")},
    )
    return rest or text, overlaid


async def chat(
    self, agent_id: str, text: str, context: str = "", client_actions: list[dict] | None = None
) -> dict:
    """Send a chat turn to an agent and return the response dict.

    Public wrapper around the internal chat pipeline so other apps can
    call `await self.call_app("rooms", "chat", agent_id=..., text=...)`.
    """
    return await self._chat(agent_id, text, context=context, client_actions=client_actions)


def _build_system(
    self, agent: dict, client_actions: list[dict] | None = None, *, team_block: str = ""
) -> str:
    """Build system prompt: persona + action instructions.

    ``team_block`` (optional) is appended verbatim — the caller computes it
    via ``_team_prompt_block(room, participant)`` when the responder is a
    lead/worker in a team room. Empty for non-team rooms.

    Tool-less agents (no server_actions, no client_actions, no tools)
    get an explicit "you can't do, only suggest" clause prepended so
    they don't hallucinate writes/sends/saves that have no mechanism
    behind them. Seen in the wild 2026-05-15: Life Strategist replied
    "Written to `00_Inbox/test-sandbox.md`" with zero capability to
    write anything.
    """
    system = agent.get("system_prompt", "You are a helpful assistant.")

    server_actions = agent.get("server_actions") or {}
    tools = agent.get("tools") or {}
    if not server_actions and not client_actions and not tools:
        system += (
            "\n\nYou have no write tools and no server actions. "
            "Never claim to have written, saved, sent, scheduled, "
            "or otherwise modified anything — you have no mechanism "
            "to do so. Suggest, discuss, and explain instead. "
            "If the user asks you to perform an action you can't, "
            'say so plainly ("I can\'t do that from here — ") and '
            "either propose the steps they could take or recommend "
            "an agent / room that has the right tools."
        )

    action_lines = []
    # Client actions — page-registered JS handlers, browser-side execution.
    if client_actions:
        action_lines.append(
            "Available client actions — page-side, JS only. "
            "Emit `[BUTTON:label|action(param)]` for a button the user clicks, "
            "or `[ACTION:name(param)]` to run silently:"
        )
        for a in client_actions:
            params = ", ".join(a.get("params", []))
            action_lines.append(f"- {a['name']}({params}): {a.get('description', '')}")

    # Server actions — call_app on the backend.
    if server_actions:
        action_lines.append(
            "Available server actions — backend, call_app. Two emission shapes:\n"
            '  • `[DO:app.method({"arg":"value"})]` — auto-execute on send. '
            "Use for confirmed, low-risk reads/writes the user clearly asked for.\n"
            '  • `[BUTTON:label|DO:app.method({"arg":"value"})]` — render as a '
            "button the user clicks to confirm. Use when you're offering an action "
            "you think is right but aren't certain, or anything destructive/irreversible.\n"
            "Pick ONE shape per action — never wrap a [BUTTON:|DO:] inside another [DO:]. "
            "Use ONLY parameters listed in each signature — do not invent parameter names:"
        )
        for app_id, methods in server_actions.items():
            for method in methods:
                sig = self._method_signature(app_id, method)
                action_lines.append(f"- {app_id}.{method}{sig}")

    if action_lines:
        system += "\n\n" + "\n".join(action_lines)
        system += (
            "\n\nBe concise. Answer in the user's language. "
            "When the user asks you to do something, act via the tokens above — "
            "do not describe what you would do in prose. "
            "Emit each token only once per response; do not repeat the same "
            "call with the same args."
        )

    if team_block:
        system += "\n\n" + team_block
    return system


# `@<path>` file-include expansion — mirrors what pi / claude-cli do natively,
# but for EmptyOS's own agent path (where the LLM call goes through
# self.think(), not through a coding-CLI subprocess). The `@@` file picker in
# the rooms input inserts `@<path>` after selection — this resolver reads the
# file at think time so the model sees actual content, not the bare path.
_PATH_TOKEN_RE = re.compile(r"@([A-Za-z0-9_./\-]+)")
_PATH_MAX_BYTES = 50_000  # Per file; bigger files surface a stub
_PATH_MAX_TOTAL = 200_000  # All files combined per turn


def _expand_at_paths(self, text: str) -> str:
    """Expand `@<path>` tokens in *text* by appending fenced file blocks.

    Native-agent equivalent of pi's / claude-cli's `@file` syntax. Resolves
    each token against the vault first, then the repo root. Tokens that
    look like room mentions (no `/`, no `.<ext>`) are skipped — those are
    handled by the mention resolver. The original `@<path>` reference stays
    inline; file contents append at the end as fenced blocks.

    Hard caps prevent a single message from blowing the model's context:
      - per-file: 50KB → above, surface a size stub
      - per-turn: 200KB total → further matches surface a stub

    NOTE on direct `Path.read_text` vs `self.read` (CLAUDE.md rule 1):
    intentional. `self.read` resolves through the `read` capability chain,
    which (a) falls through to the human provider on a missing file,
    prompting the user to paste content mid-prompt-build (catastrophic UX),
    (b) raises on encoding errors instead of replacing, (c) is bound to the
    vault base_path and can't reach repo files. This helper is a scoped
    prompt-builder, not an app feature touching arbitrary user data —
    direct IO with explicit size caps and silent-skip-on-missing is the
    correct shape here.
    """
    if not text or "@" not in text:
        return text
    matches = list(dict.fromkeys(_PATH_TOKEN_RE.findall(text)))  # dedupe, keep order
    if not matches:
        return text

    repo_root = Path(__file__).resolve().parent.parent.parent
    vault_root = self.vault_root

    blocks: list[str] = []
    total = 0
    for tok in matches:
        # Must look path-shaped — has `/` or a `.<ext>` suffix.
        if "/" not in tok and not re.search(r"\.[a-zA-Z0-9]{1,8}$", tok):
            continue
        # Prefer vault when token is a markdown-shaped reference; otherwise
        # repo first since `@apps/...` is the most common coding-chat shape.
        order: list[Path] = []
        if tok.endswith(".md"):
            if vault_root:
                order.append(vault_root / tok)
            order.append(repo_root / tok)
        else:
            order.append(repo_root / tok)
            if vault_root:
                order.append(vault_root / tok)

        resolved = None
        for cp in order:
            try:
                if cp.is_file():
                    resolved = cp
                    break
            except OSError:
                continue
        if resolved is None:
            continue

        try:
            size = resolved.stat().st_size
        except OSError:
            continue
        if size > _PATH_MAX_BYTES:
            blocks.append(
                f"\n\n[@{tok} — {size} bytes, exceeds {_PATH_MAX_BYTES} byte include cap]"
            )
            continue
        if total + size > _PATH_MAX_TOTAL:
            blocks.append(f"\n\n[@{tok} — skipped, per-turn include budget reached]")
            break
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            blocks.append(f"\n\n[@{tok} — read failed: {type(e).__name__}]")
            continue
        ext = resolved.suffix.lstrip(".")
        blocks.append(f"\n\nFile `{tok}`:\n```{ext}\n{content}\n```")
        total += size

    return text + "".join(blocks)


def _build_prompt(
    self, agent: dict, text: str, context: str = "", history: list[dict] | None = None
) -> str:
    """Build user prompt: knowledge + context + history + message.

    Sync path. Stuffs the whole knowledge blob (capped per-file by
    load_knowledge). Used when no query is available or embeddings
    aren't usable. The async _build_prompt_async path swaps to
    embedding retrieval when both conditions are met.
    """
    knowledge_text = self.kernel.agents.load_knowledge(agent)
    text = self._expand_at_paths(text)
    return self._assemble_prompt(text, knowledge_text, context, history)


async def _build_prompt_async(
    self, agent: dict, text: str, context: str = "", history: list[dict] | None = None
) -> str:
    """Embedding-aware prompt build. Falls back to the sync path when
    embeddings unavailable or the agent has no chunkable knowledge.

    Multi-turn aware: the embedding query is built from `history` + `text`
    so a follow-up like "what about the second one?" still retrieves the
    right knowledge chunks.
    """
    if not text or not text.strip() or not self.embeddings_available:
        return self._build_prompt(agent, text, context, history)
    # Expand `@<path>` references once, here, BEFORE the embedding query —
    # file content often carries the semantic signal the retrieval should
    # hit. Downstream `_assemble_prompt` is dumb-paste; `_build_prompt`
    # below is only reached on fallback when expansion has already run, so
    # we pass the expanded text through and skip the second pass.
    expanded = self._expand_at_paths(text)
    try:
        chunks = self.kernel.agents.load_knowledge_chunks(agent)
        if not chunks:
            # Fallback: build with the expanded text + an empty knowledge
            # blob so the static path doesn't double-expand.
            return self._assemble_prompt(expanded, "", context, history)
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
        return self._assemble_prompt(expanded, knowledge_text, context, history)
    except Exception:
        return self._build_prompt(agent, text, context, history)


@staticmethod
def _assemble_prompt(
    text: str, knowledge_text: str, context: str, history: list[dict] | None
) -> str:
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


async def _chat(
    self, agent_id: str, text: str, context: str = "", client_actions: list[dict] | None = None
) -> dict:
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

    # Skill overlay — if the user typed `/<slash> ...` and `<slash>` is a
    # registered skill, layer the skill's system-prompt body onto the
    # responder and merge its server_actions allowlist for this turn only.
    # The room's base persona stays intact; the skill is additive scaffolding.
    text, responder = await _maybe_apply_skill_overlay(self, text, responder)

    # Tree-aware history: walk parent chain from the room's current head.
    # On legacy rooms (no head set) this returns the flat list unchanged.
    raw_history = self._load_history(agent_id)
    history = self._walk_to_head(raw_history, room.get("current_head_entry_id"))
    system = self._build_system(responder, client_actions)
    # Phase 11 — resolve [[wikilink]] vault refs in the user message.
    ref_block = self._resolve_wikilinks(text)
    # Phase 26 — prepend the room's memory block when present.
    mem_block = self._memory_block(room)
    merged_context = "\n\n".join(b for b in (mem_block, ref_block, context) if b).strip()
    prompt = await self._build_prompt_async(responder, text, merged_context, history)

    kwargs: dict = {"system": system, "domain": "text"}
    if responder.get("provider"):
        kwargs["provider"] = responder["provider"]
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
    response, server_results = await self._execute_server_actions(
        response,
        responder,
        room_id=agent_id,
    )
    if not response and server_results:
        response = self._summarize_server_actions(server_results)

    # Save to history. For group rooms, tag the assistant turn with the
    # responder's id so the UI can render which agent spoke. 1:1 rooms
    # keep the legacy {role, text, ts} shape — back-compat invariant.
    #
    # Tree-aware append: each new message carries entry_id + parent_entry_id.
    # The user message parents off the room's current head (or null on a
    # fresh room); the assistant parents off the just-appended user message.
    # We append to `raw_history` (all branches), not `history` (current head
    # chain), so existing branches aren't dropped on save.
    import uuid as _uuid

    now = datetime.now(timezone.utc).isoformat()
    head_id = room.get("current_head_entry_id")
    user_entry_id = _uuid.uuid4().hex[:12]
    user_msg = {
        "role": "user",
        "text": text,
        "ts": now,
        "entry_id": user_entry_id,
        "parent_entry_id": head_id,
    }
    raw_history.append(user_msg)
    assistant_entry_id = _uuid.uuid4().hex[:12]
    assistant_msg: dict = {
        "role": "assistant",
        "text": response,
        "ts": now,
        "entry_id": assistant_entry_id,
        "parent_entry_id": user_entry_id,
    }
    if kind == "group":
        assistant_msg["actor"] = {"type": "agent", "id": responder["id"]}
    if server_results:
        assistant_msg["server_results"] = server_results
    raw_history.append(assistant_msg)
    self._save_history(agent_id, raw_history)
    # Advance the room's head pointer to the new assistant message.
    room["current_head_entry_id"] = assistant_entry_id
    self._save_agent(room)

    # Auto-save to vault — use responder's name so group rooms log who said what.
    self._vault_log_chat(responder, text, response)

    await self.emit(
        "rooms:chat",
        {
            "agent_id": agent_id,
            "agent_name": responder.get("name", responder["id"]),
            "kind": kind,
            "responder_id": responder["id"],
        },
    )
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


@web_route("POST", "/api/chat")
async def api_chat(self, request):
    data = await request.json()
    agent_id = data.get("agent_id") or ""
    text = (data.get("text") or "").strip()
    if not agent_id or not text:
        return {"error": "agent_id and text required"}
    context = data.get("context") or ""
    client_actions = data.get("client_actions")
    return await self._chat(agent_id, text, context=context, client_actions=client_actions)


@web_route("POST", "/api/do")
async def api_do(self, request):
    """Execute a single [DO:] action proposed by an agent.

    Body: {agent_id, app, method, args}. Validates against the agent's
    server_actions allowlist, then call_app's it. Used by page-assistant
    when the user clicks a [BUTTON:label|DO:app.method({...})] button —
    the click-to-execute counterpart to bare [DO:] auto-execution.
    """
    data = await request.json()
    agent_id = (data.get("agent_id") or "").strip()
    app_id = (data.get("app") or "").strip()
    method = (data.get("method") or "").strip()
    args = data.get("args") or {}
    if not agent_id or not app_id or not method:
        return {"ok": False, "error": "agent_id, app, method required"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object"}
    agent = self._load_agent(agent_id)
    if not agent:
        return {"ok": False, "error": f"agent '{agent_id}' not found"}
    allowed = (agent.get("server_actions") or {}).get(app_id, [])
    if method not in allowed:
        return {"ok": False, "error": f"{app_id}.{method} not in agent allowlist"}
    try:
        res = await self.call_app(app_id, method, **args)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    inverse = self._lookup_inverse(app_id, method)
    from emptyos.sdk.actions_log import record_action
    record_action(self._actions_log_path(), app=app_id, method=method,
                  args=args, result=res, inverse=inverse)
    return {
        "ok": True,
        "app": app_id,
        "method": method,
        "result": str(res)[:300],
        "reversible": bool(inverse),
        "links": self._action_result_links(app_id, res),
    }


@staticmethod
def _action_result_links(app_id: str, res) -> list[dict]:
    """Build UI link hints from a server-action result.

    Inspects common result shapes (dict with `path`/`file`, dataclass
    with `.file`, dict with `routed_to`) and emits chip-ready entries.
    Always includes a link to the owning app's page so the user can
    see the broader list/state after the change.
    """
    links: list[dict] = []

    as_dict: dict | None = None
    if isinstance(res, dict):
        as_dict = res
    elif hasattr(res, "to_dict"):
        try:
            d = res.to_dict()
            if isinstance(d, dict):
                as_dict = d
        except Exception:
            pass

    note_path: str | None = None
    if as_dict:
        for key in ("path", "file"):
            v = as_dict.get(key)
            if isinstance(v, str) and v:
                note_path = v
                break
        if not note_path:
            v = getattr(res, "file", None)
            if isinstance(v, str) and v:
                note_path = v
    else:
        v = getattr(res, "file", None)
        if isinstance(v, str) and v:
            note_path = v

    if note_path:
        label = note_path.rsplit("/", 1)[-1] or note_path
        links.append({"type": "note", "path": note_path, "label": label})

    if as_dict and isinstance(as_dict.get("routed_to"), str) and as_dict["routed_to"]:
        target = as_dict["routed_to"]
        name = as_dict.get("project_name") or target
        links.append({"type": "app", "url": f"/projects/?id={target}", "label": f"→ {name}"})

    # Always offer an exit to the owning app's main page.
    links.append({"type": "app", "url": f"/{app_id}/", "label": f"→ {app_id}"})
    return links


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
    agent_id = data.get("agent_id") or ""
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
    # auto_meta is non-None only when room["auto_route"] fired the
    # LLM-classified fallback (2+ responders, no @mention match) — never
    # silent, surfaced to the client as the stream's first chunk below.
    responder_part, auto_meta = await self._resolve_responder(text, parts, room)
    if not responder_part:
        return {"error": "room has no responder participants"}

    raw_history = self._load_history(agent_id)
    history = self._walk_to_head(raw_history, room.get("current_head_entry_id"))
    app = self

    if responder_part.get("type") == "cli":
        # CLI participant — agent-runtime spawn, stream-json events.
        cli_id = responder_part["id"]

        async def generate_cli():
            if auto_meta:
                yield {"auto_routed": True, **auto_meta, "responder_id": responder_part["id"]}
            full_text = ""
            model_label = ""
            async for chunk in app._dispatch_cli_turn(
                room,
                responder_part,
                text,
                history,
            ):
                if chunk.get("text"):
                    full_text += chunk["text"]
                if chunk.get("done") and chunk.get("model"):
                    model_label = chunk["model"]
                yield chunk
            # Phase 5 review gate: parse [DO:] tokens out of the CLI's
            # reply, save each as a pending action, yield a card chunk
            # so the UI can render Apply/Reject inline. Cleaned text
            # (without tokens) is what gets persisted to history.
            cleaned_text, pending = await app._gate_server_actions(
                full_text,
                room_id=agent_id,
                source_actor={"type": "cli", "id": cli_id},
            )
            # If the gate stripped tokens, tell the UI to replace what it
            # streamed — otherwise the user keeps seeing the raw [DO:]
            # syntax under the pending card.
            if pending and cleaned_text != full_text:
                yield {"text_replace": cleaned_text, "done": False}
            for action in pending:
                yield {"pending_action": action, "done": False}
            # Save the user turn + the CLI's accumulated text reply.
            # Tree-aware: parent off the room's current head; advance head
            # to the new assistant message. Append onto raw_history (all
            # branches) so other branches survive the save.
            import uuid as _uuid

            now = datetime.now(timezone.utc).isoformat()
            head_id = room.get("current_head_entry_id")
            user_entry_id = _uuid.uuid4().hex[:12]
            user_msg: dict = {
                "role": "user",
                "text": text,
                "ts": now,
                "entry_id": user_entry_id,
                "parent_entry_id": head_id,
            }
            if reply_to:
                user_msg["reply_to"] = reply_to
            raw_history.append(user_msg)
            # Persist the cleaned text when the gate fired (even if it's
            # empty — a reply that was nothing but [DO:] tokens has no
            # prose worth keeping; the pending cards carry the meaning).
            saved_text = cleaned_text if pending else full_text
            assistant_entry_id = _uuid.uuid4().hex[:12]
            assistant_msg: dict = {
                "role": "assistant",
                "text": saved_text,
                "ts": now,
                "actor": {"type": "cli", "id": cli_id},
                "entry_id": assistant_entry_id,
                "parent_entry_id": user_entry_id,
            }
            if model_label:
                assistant_msg["model"] = model_label
            if pending:
                assistant_msg["pending"] = [a["id"] for a in pending]
            raw_history.append(assistant_msg)
            app._save_history(agent_id, raw_history)
            room["current_head_entry_id"] = assistant_entry_id
            app._save_agent(room)
            # Note: vault logging skipped for CLI — outputs are often
            # tool transcripts that aren't useful as journal entries.
            await app.emit(
                "rooms:chat",
                {
                    "agent_id": agent_id,
                    "responder_id": cli_id,
                    "responder_type": "cli",
                    "kind": kind,
                    "pending_count": len(pending),
                },
            )

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

    system = self._build_system(
        responder,
        client_actions,
        team_block=self._team_prompt_block(room, responder_part),
    )
    # Phase 11 — resolve [[wikilink]] vault refs in the user message.
    ref_block = self._resolve_wikilinks(text)
    # Phase 26 — prepend the room's memory block when present.
    mem_block = self._memory_block(room)
    merged_context = "\n\n".join(b for b in (mem_block, ref_block, context) if b).strip()
    prompt = await self._build_prompt_async(responder, text, merged_context, history)

    async def generate():
        if auto_meta:
            yield {"auto_routed": True, **auto_meta, "responder_id": responder_part["id"]}
        full_text = ""
        try:
            stream_kwargs = {"system": system, "domain": "text"}
            if responder.get("provider"):
                stream_kwargs["provider"] = responder["provider"]
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
                "text": "",
                "done": True,
                "full": full_text,
                "responder_id": responder["id"],
            }
        except Exception as e:
            yield {"text": str(e), "done": True, "error": True}
            full_text = f"Error: {e}"

        # Execute server actions + save history
        cleaned, server_results = await app._execute_server_actions(
            full_text,
            responder,
            room_id=agent_id,
        )
        if not cleaned and server_results:
            cleaned = app._summarize_server_actions(server_results)
            yield {"text": cleaned, "done": False, "fallback": True}
        # Tree-aware history append (same shape as the CLI branch above).
        import uuid as _uuid

        now = datetime.now(timezone.utc).isoformat()
        head_id = room.get("current_head_entry_id")
        user_entry_id = _uuid.uuid4().hex[:12]
        user_msg: dict = {
            "role": "user",
            "text": text,
            "ts": now,
            "entry_id": user_entry_id,
            "parent_entry_id": head_id,
        }
        if reply_to:
            user_msg["reply_to"] = reply_to
        raw_history.append(user_msg)
        assistant_entry_id = _uuid.uuid4().hex[:12]
        assistant_msg: dict = {
            "role": "assistant",
            "text": cleaned,
            "ts": now,
            "entry_id": assistant_entry_id,
            "parent_entry_id": user_entry_id,
        }
        if kind == "group":
            assistant_msg["actor"] = {"type": "agent", "id": responder["id"]}
        raw_history.append(assistant_msg)
        app._save_history(agent_id, raw_history)
        room["current_head_entry_id"] = assistant_entry_id
        app._save_agent(room)
        app._vault_log_chat(responder, text, cleaned)
        await app.emit(
            "rooms:chat",
            {
                "agent_id": agent_id,
                "responder_id": responder["id"],
                "kind": kind,
            },
        )
        if server_results:
            yield {"server_results": server_results}

    return ndjson_response(generate())


async def _run_participant_turn(
    self,
    room: dict,
    participant: dict,
    text: str,
    *,
    reply_to: str = "",
    source: str = "chat",
    apply_team_tokens: bool = False,
) -> dict:
    """Run ONE participant's turn headlessly (no streaming) and persist it.

    Shares the build→dispatch→gate→save path with ``api_chat_stream`` so the
    team auto-dispatch loop and interactive chat don't fork. Returns
    ``{"reply", "full", "pending", "actor"}``.

    ``apply_team_tokens``: when True (set by an armed team run — the arm is the
    grant), ``[DO:rooms.team_*]`` tokens are applied immediately and stripped
    before the normal gate runs on the remainder; non-team ``[DO:]`` still
    flows through the room's usual gate/allowlist.
    """
    import json as _json
    import uuid as _uuid

    from .team import TEAM_VERBS

    agent_id = room["id"]
    ptype = participant.get("type")
    raw_history = self._load_history(agent_id)
    history = self._walk_to_head(raw_history, room.get("current_head_entry_id"))

    full_text = ""
    model_label = ""
    actor = {"type": ptype, "id": participant.get("id")}

    if ptype == "cli":
        async for chunk in self._dispatch_cli_turn(room, participant, text, history):
            if chunk.get("text"):
                full_text += chunk["text"]
            if chunk.get("done") and chunk.get("model"):
                model_label = chunk["model"]
    elif ptype == "agent":
        responder_id = participant.get("id")
        responder = room if responder_id == agent_id else (self._load_agent(responder_id) or room)
        system = self._build_system(
            responder,
            team_block=self._team_prompt_block(room, participant),
        )
        prompt = await self._build_prompt_async(
            responder,
            text,
            self._memory_block(room),
            history,
        )
        stream_kwargs = {"system": system, "domain": "text"}
        if responder.get("provider"):
            stream_kwargs["provider"] = responder["provider"]
        if responder.get("model"):
            stream_kwargs["model"] = responder["model"]
        if responder.get("temperature") is not None:
            stream_kwargs["temperature"] = responder["temperature"]
        if responder.get("effort"):
            stream_kwargs["effort"] = responder["effort"]
        try:
            async for chunk in self.think_stream(prompt, **stream_kwargs):
                if chunk.get("text"):
                    full_text += chunk["text"]
        except Exception as e:
            full_text = f"[turn error: {e}]"
        actor = {"type": "agent", "id": responder_id}
    else:
        return {"reply": "", "full": "", "pending": [], "actor": actor}

    # Team-namespace tokens: apply immediately under an armed run, then strip
    # them so the normal gate below only sees the non-team remainder.
    if apply_team_tokens and full_text:
        remainder, toks = extract_do_tokens(
            full_text,
            source_actor={"type": ptype, "id": participant.get("id")},
        )
        kept = []
        applied_any = False
        failures: list[str] = []
        for a in toks:
            if a.get("app") == "rooms" and a.get("method") in TEAM_VERBS:
                try:
                    getattr(self, a["method"])(agent_id, **(a.get("args") or {}))
                except Exception as e:
                    # Never silently swallow. Before this, the exception was
                    # dropped, applied_any was set anyway, and the token was
                    # stripped below — so a failed team verb read to the user
                    # as success, which is exactly what the review gate exists
                    # to prevent. Surface it in the sibling paths' format
                    # (`_summarize_server_actions`: "✗ app.method — err").
                    failures.append(
                        f"✗ {a.get('app', 'rooms')}.{a.get('method', '?')} — {str(e)[:200]}"
                    )
                applied_any = True
            else:
                kept.append(a)
        if applied_any:
            full_text = remainder
            for a in kept:
                full_text += f"\n[DO:{a['app']}.{a['method']}({_json.dumps(a.get('args') or {}, ensure_ascii=False)})]"
            if failures:
                full_text += "\n\n" + "\n".join(failures)

    pending: list[dict] = []
    if ptype == "cli":
        cleaned, pending = await self._gate_server_actions(
            full_text,
            room_id=agent_id,
            source_actor={"type": "cli", "id": participant.get("id")},
        )
        saved_text = cleaned if pending else full_text
    else:
        responder = room if actor["id"] == agent_id else (self._load_agent(actor["id"]) or room)
        cleaned, server_results = await self._execute_server_actions(
            full_text,
            responder,
            room_id=agent_id,
        )
        if not cleaned and server_results:
            cleaned = self._summarize_server_actions(server_results)
        saved_text = cleaned

    # Tree-aware history append. Reload the room first — team-token applies
    # above call _save_agent, so the in-scope `room` is stale.
    now = datetime.now(timezone.utc).isoformat()
    fresh = self._load_agent(agent_id) or room
    head_id = fresh.get("current_head_entry_id")
    raw_history = self._load_history(agent_id)
    user_entry_id = _uuid.uuid4().hex[:12]
    user_msg: dict = {
        "role": "user",
        "text": text,
        "ts": now,
        "entry_id": user_entry_id,
        "parent_entry_id": head_id,
    }
    if reply_to:
        user_msg["reply_to"] = reply_to
    raw_history.append(user_msg)
    assistant_entry_id = _uuid.uuid4().hex[:12]
    assistant_msg: dict = {
        "role": "assistant",
        "text": saved_text,
        "ts": now,
        "actor": actor,
        "entry_id": assistant_entry_id,
        "parent_entry_id": user_entry_id,
    }
    if model_label:
        assistant_msg["model"] = model_label
    if pending:
        assistant_msg["pending"] = [a["id"] for a in pending]
    raw_history.append(assistant_msg)
    self._save_history(agent_id, raw_history)
    fresh["current_head_entry_id"] = assistant_entry_id
    self._save_agent(fresh)
    try:
        await self.emit(
            "rooms:chat",
            {
                "agent_id": agent_id,
                "responder_id": participant.get("id"),
                "responder_type": ptype,
                "source": source,
                "pending_count": len(pending),
            },
        )
    except Exception:
        pass
    return {"reply": saved_text, "full": full_text, "pending": pending, "actor": actor}


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
