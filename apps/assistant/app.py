"""Assistant — multi-session AI chat with vault integration.

Absorbed from AI Phone Agent. Features:
- Multi-session management (create/rename/delete)
- WebSocket streaming with real-time token display
- Smart auto-routing (vault queries → local AI, else → cloud AI)
- 13+ slash commands routing to EmptyOS apps via call_app()
- Vault context injection (search + read top results)
- Custom personas + persistent memories
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime
from pathlib import Path

from emptyos.runtime import wheel as _wheel
from emptyos.sdk import BaseApp, cli_command, web_route, ws_route

from .prompts import (
    BUILTIN_OVERRIDES,
    FALLBACK_THINK_TIMEOUT,
    MAX_CHAT_TURNS,
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_FILES,
    SYSTEM_PROMPT,
    TOOLS_MAX_ITERS,
    TOOLS_READONLY,
    TOOLS_SYSTEM_PROMPT,
)
from .sessions import SessionsMixin


class AssistantApp(SessionsMixin, BaseApp):
    _sessions_lock: asyncio.Lock
    _cancel_flags: dict[str, bool]

    async def setup(self):
        await super().setup()
        self._sessions_lock = asyncio.Lock()
        self._cancel_flags = {}
        self._slash_commands = self._discover_slash_commands()
        self._init_db()
        # User-state dossier is injected into every chat turn so generic questions
        # ("what chant for the interview?") still see current context (which interview).
        # Cached 60s because the underlying facts change slowly.
        self._state_cache: str | None = None
        self._state_cache_ts: float = 0.0

    def _discover_slash_commands(self) -> dict:
        """Build slash command table from all app manifests' [provides.assistant] sections."""
        commands = {}
        for app_id, manifest in self.kernel.apps.manifests.items():
            assistant_section = manifest.provides.get("assistant", {})
            for cmd_def in assistant_section.get("commands", []):
                slash = cmd_def.get("slash", "")
                if slash:
                    commands[slash] = (app_id, cmd_def["method"], cmd_def.get("arg"))
        return commands

    # ── Provider Routing ──────────────────────────────────────

    def _get_provider(self, message: str, session: dict) -> str:
        label, _ = self._pick_provider_label(session)
        # Legacy callers expect a concrete name; "auto" becomes "openai" for
        # back-compat with the /ask endpoint which needs a specific provider
        # to try first before falling back to the default chain.
        return label if label != "auto" else "openai"

    def _pick_provider_label(self, session: dict) -> tuple[str, bool]:
        """Return (label, is_explicit).

        is_explicit=True → the user or an app-level setting picked a specific
        provider; the UI should announce it and show a 'switched' badge if the
        actual answerer differs.

        is_explicit=False → pure auto mode with no override; label is 'auto'
        and the real answerer is only known when the capability layer emits
        a `provider_used` chunk with the first streamed chunk.
        """
        backend = session.get("backend", "auto")
        if backend != "auto":
            return backend, True
        settings = self.kernel.services.get_optional("settings")
        if settings:
            app_prov = settings.get(f"think.app.{self.manifest.id}")
            if app_prov:
                return app_prov, True
        return "auto", False

    # ── Slash Commands ─────────────────────────────────────────

    async def _handle_slash(self, text: str) -> str | None:
        parts = text.strip().split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            lines = ["**Available commands:**"]
            for c, (app, method, _) in sorted(self._slash_commands.items()):
                lines.append(f"  `{c}` — {app}.{method}")
            lines.append("  `/export` — export this session to vault")
            lines.append("  `/archive` — archive old sessions to vault")
            lines.append("  `/new` — new conversation")
            lines.append("  `/help` — this list")
            return "\n".join(lines)

        if cmd == "/new":
            return None  # handled by frontend

        if cmd == "/export":
            return "__EXPORT__"  # sentinel — handled by ws_chat with session context

        if cmd == "/archive":
            archived = await self._auto_archive()
            if archived:
                names = ", ".join(a["name"] for a in archived)
                return f"Archived {len(archived)} sessions to vault: {names}"
            return "No sessions to archive (need >5 messages and >7 days idle)."

        # Prefix matching: /ima → /image, /tas → /tasks
        if cmd not in self._slash_commands:
            matches = [c for c in self._slash_commands if c.startswith(cmd)]
            if len(matches) == 1:
                cmd = matches[0]

        if cmd in self._slash_commands:
            app_id, method, arg_name = self._slash_commands[cmd]
            override = BUILTIN_OVERRIDES.get(cmd, {})

            # Usage hint check (e.g. /image without argument)
            if override.get("usage_hint") and not arg:
                return override["usage_hint"]

            try:
                kwargs = {arg_name: arg} if arg_name and arg else {}
                kwargs.update(override.get("extra_kwargs", {}))

                result = await self.call_app(app_id, method, **kwargs)
                # Format result
                if isinstance(result, list):
                    items = result[:10]
                    formatted = json.dumps(items, indent=2, ensure_ascii=False, default=str)
                    return f"**{cmd}** ({len(result)} items):\n```json\n{formatted[:2000]}\n```"
                elif isinstance(result, dict):
                    return f"**{cmd}**:\n```json\n{json.dumps(result, indent=2, ensure_ascii=False, default=str)[:2000]}\n```"
                else:
                    return f"**{cmd}**: {str(result)[:2000]}"
            except Exception as e:
                return f"**{cmd}** failed: {e}"

        return None

    # ── Chat Messages Builder ──────────────────────────────────

    def _build_chat_messages(
        self, session: dict, vault_context: str = "", max_turns: int = MAX_CHAT_TURNS
    ) -> list[dict]:
        """Build an OpenAI-style messages list from session history.

        - Keeps the last `max_turns` entries from DB.
        - Drops slash-command meta replies (stored with agent='system').
        - Injects vault_context into the final user turn so the model sees it
          clearly tied to the current question without polluting prior turns.
        """
        raw = session.get("messages", [])[-max_turns:]
        msgs: list[dict] = []
        for m in raw:
            role = m.get("role", "")
            if role not in ("user", "assistant"):
                continue
            if role == "assistant" and m.get("agent") == "system":
                continue
            content = m.get("text", "")
            if not content:
                continue
            msgs.append({"role": role, "content": content})

        if vault_context and msgs and msgs[-1]["role"] == "user":
            msgs[-1] = {
                "role": "user",
                "content": (
                    "[Possibly-relevant vault snippets — reference only. "
                    "Ignore them unless they directly answer the user's question below. "
                    "Do NOT assume the user is asking about these topics.]\n"
                    f"{vault_context}\n\n"
                    f"[User's actual message]\n{msgs[-1]['content']}"
                ),
            }
        return msgs

    # ── User State Dossier ─────────────────────────────────────

    async def _build_user_state(self) -> str:
        """Compact 'who you are right now' snapshot injected into every chat turn.

        Pulls active projects, active job applications, today's journal summary.
        Each source is fail-soft — an uninstalled or erroring app drops its
        section silently. Cached 60s because the facts change slowly and this
        runs on every message.
        """
        now = datetime.now().timestamp()
        if self._state_cache is not None and (now - self._state_cache_ts) < 60:
            return self._state_cache

        lines: list[str] = []

        try:
            projects = await self.call_app("projects", "list_projects", status_filter="active")
            if projects:
                lines.append("Active projects:")
                for p in projects[:6]:
                    name = p.get("name") or p.get("id", "")
                    dl = f" due {p['deadline']}" if p.get("deadline") else ""
                    tasks = ""
                    if p.get("total_tasks"):
                        tasks = f" [{p.get('done_tasks', 0)}/{p['total_tasks']}]"
                    lines.append(f"  - {name}{tasks}{dl}")
        except Exception:
            pass

        try:
            summary = await self.call_app("jobs", "get_summary")
            by_status = (summary or {}).get("applications", {}).get("by_status", {})
            closed = {"rejected", "withdrawn", "not_pursuing", "accepted"}
            active = {k: v for k, v in by_status.items() if k not in closed}
            if active:
                total = sum(active.values())
                parts = ", ".join(f"{v} {k}" for k, v in active.items())
                lines.append(f"Job applications in flight: {total} ({parts})")
        except Exception:
            pass

        try:
            j = await self.call_app("journal", "get_summary")
            if j:
                bits = []
                if j.get("today_entries"):
                    bits.append(f"{j['today_entries']} entries today")
                if j.get("streak"):
                    bits.append(f"{j['streak']}-day streak")
                if j.get("mood"):
                    bits.append(f"mood={j['mood']}")
                if bits:
                    lines.append("Journal: " + ", ".join(bits))
        except Exception:
            pass

        state = "\n".join(lines)
        self._state_cache = state
        self._state_cache_ts = now
        return state

    async def _build_system(self, session: dict | None = None) -> str:
        """Full system prompt: base + wheel + user-state dossier + session-custom."""
        system = SYSTEM_PROMPT.format(date=date.today().isoformat())
        system += _wheel.planner_context(self.kernel)
        state = await self._build_user_state()
        if state:
            system += f"\n\nCurrent user state:\n{state}"
        if session:
            custom = session.get("system_prompt", "")
            if custom:
                system += f"\n\nCustom instructions: {custom}"
        return system

    # ── Vault Context ──────────────────────────────────────────

    def _should_skip_retrieval(self, question: str) -> bool:
        """Skip vault retrieval for greetings / very short / stopword-only queries.

        Short generic queries ("hello", "hi", "thanks", "ok") match thousands of
        lines across the vault and produce noisy context that hijacks the reply.
        Heuristic: under 12 chars or only stopwords → skip.
        """
        q = (question or "").strip().lower().rstrip("?.!,")
        if len(q) < 12:
            return True
        tokens = [t for t in q.replace("?", " ").replace("!", " ").split() if t]
        stop = {
            "hi",
            "hello",
            "hey",
            "yo",
            "sup",
            "hola",
            "thanks",
            "thank",
            "you",
            "ok",
            "okay",
            "cool",
            "nice",
            "great",
            "good",
            "morning",
            "evening",
            "afternoon",
            "night",
            "bye",
            "gm",
            "gn",
            "lol",
            "haha",
            "?",
            "test",
            "ping",
            "yes",
            "no",
            "sure",
            "wassup",
            "how",
            "are",
            "doing",
        }
        if tokens and all(t in stop for t in tokens):
            return True
        return False

    async def _build_context(self, question: str, session: dict | None = None) -> str:
        if self._should_skip_retrieval(question):
            return ""
        # Multi-turn retrieval query: include recent prior turns so a
        # follow-up like "how does that work?" doesn't lose topic. Session
        # messages use {role, text}; map to {role, content} for the helper.
        retrieval_query = question
        if session and session.get("messages"):
            from emptyos.sdk.embeddings import build_retrieval_query

            history = [
                {"role": m.get("role", ""), "content": m.get("text", "")}
                for m in session["messages"]
                if m.get("text")
            ]
            # Drop the trailing turn if it equals the current question (we
            # add it via the `current` arg instead).
            if history and history[-1].get("content") == question:
                history = history[:-1]
            retrieval_query = build_retrieval_query(history, question)

        # Prefer embedding-based search when available — much higher recall
        # on paraphrase queries. Falls back to grep on any failure or when
        # embeddings unavailable. Routed through apps/search so a single
        # retrieval pipeline serves both /search and the assistant.
        results: list = []
        try:
            if self.embeddings_available:
                resp = await self.call_app(
                    "search", "_embed_search",
                    query=retrieval_query, top=MAX_CONTEXT_FILES,
                )
                if isinstance(resp, tuple) and len(resp) >= 1:
                    paths = resp[0] or []
                    results = [{"path": p} for p in paths]
        except Exception:
            results = []
        if not results:
            try:
                # Grep fallback uses the bare question (it can't usefully grep
                # for "x | y | z" multi-turn concatenations).
                results = await self.search(question, path=str(self.kernel.config.notes_path))
            except Exception:
                return ""
        context_parts = []
        for r in results[:MAX_CONTEXT_FILES]:
            path = r if isinstance(r, str) else r.get("path", "")
            try:
                content = await self.read(path)
                name = path.replace("\\", "/").split("/")[-1]
                context_parts.append(f"[{name}]\n{content[:MAX_CONTEXT_CHARS]}")
            except Exception:
                continue
        return "\n\n".join(context_parts) if context_parts else ""

    # ── Read-Only Tool Loop (opt-in retrieval upgrade) ─────────

    def _use_tools_default(self) -> bool:
        """Default for use_tools when the request/UI doesn't set it explicitly."""
        settings = self.service("settings")
        if not settings:
            return False
        try:
            return bool(settings.get("assistant.use_tools"))
        except Exception:
            return False

    def _resolve_tool_provider(self, preferred: str = ""):
        """Find a ToolCapableProvider for the read-only retrieval path.

        Mirrors `AgentApp._resolve_provider` but skips NativelyAgenticProvider
        (claude-cli runs its own loop — we want to drive the loop ourselves so
        the tool set stays read-only and auto-approved). Returns None if no
        tool-capable provider is registered.
        """
        from emptyos.capabilities.providers._tool_capable import ToolCapableProvider

        think = self.kernel.capability("think")
        candidates: list = list(think.providers)
        for chain in getattr(think, "_domains", {}).values():
            candidates.extend(chain)
        for chain in getattr(think, "_buckets", {}).values():
            candidates.extend(chain)

        if preferred:
            for p in candidates:
                if getattr(p, "name", "") == preferred and isinstance(p, ToolCapableProvider):
                    return p
        for p in candidates:
            if isinstance(p, ToolCapableProvider):
                return p
        return None

    async def _chat_with_tools(
        self,
        message: str,
        session: dict | None,
        on_event=None,  # optional async callable(event_type: str, data: dict)
    ) -> tuple[str, str, list[dict]]:
        """Run a chat turn with read-only vault tools (Read/Grep/Glob/WebSearch).

        Returns (response_text, provider_name, tool_call_log). Falls back to
        a plain think() call if no tool-capable provider is available.

        If on_event is provided, agent:text and agent:tool_call events are
        forwarded to it in real time so callers can stream to a WebSocket.
        """
        provider = self._resolve_tool_provider()
        if provider is None:
            # No tool-capable provider registered — fall through to a regular
            # think() with keyword-grep context. Caller handles persistence.
            context = await self._build_context(message, session=session)
            system = await self._build_system(session)
            user_content = f"[Vault context]\n{context}\n\n{message}" if context else message
            text = await self.think(
                messages=[{"role": "user", "content": user_content}],
                system=system,
                domain="text",
                temperature=0.4,
            )
            return text, "no-tool-provider:fallback", []

        from emptyos.sdk.agent_loop import AgentSession, run_turn
        from emptyos.sdk.agent_tools import build_registry

        tools = build_registry(enabled=list(TOOLS_READONLY))
        if not tools:
            return "No read-only tools available.", "error", []

        # Seed session messages from history (provider-native shape expected
        # by run_turn). We already persist in a chat-friendly shape, so rebuild.
        history_msgs: list[dict] = []
        if session:
            raw = session.get("messages", [])[-MAX_CHAT_TURNS:]
            for m in raw:
                role = m.get("role")
                if role == "assistant" and m.get("agent") == "system":
                    continue  # drop slash-command meta
                if role in ("user", "assistant"):
                    history_msgs.append({"role": role, "content": m.get("text", "")})

        sess = AgentSession(
            id=(session or {}).get("id", f"tmp-{uuid.uuid4().hex[:8]}"),
            messages=history_msgs,
            provider_kind=provider.kind,
        )

        vault_root = str(self.kernel.config.notes_path)
        system = TOOLS_SYSTEM_PROMPT.format(
            date=date.today().isoformat(),
            vault=vault_root,
        )
        # Append user-state dossier so tool-use retrieval has the same awareness
        # the classic chat path gets.
        state = await self._build_user_state()
        if state:
            system += f"\n\nCurrent user state:\n{state}"
        if session and (custom := session.get("system_prompt", "")):
            system += f"\n\nCustom instructions: {custom}"

        pre_len = len(sess.messages)

        # Temporary subscriptions to forward live events to the caller (e.g. WebSocket).
        _unsubs: list = []
        if on_event and self.kernel.events:
            _sid = sess.id

            async def _fwd_text(event):
                if event.data.get("session_id") == _sid:
                    await on_event("agent:text", event.data)

            async def _fwd_tool(event):
                if event.data.get("session_id") == _sid:
                    await on_event("agent:tool_call", event.data)

            _unsubs.append(self.kernel.events.on("agent:text", _fwd_text))
            _unsubs.append(self.kernel.events.on("agent:tool_call", _fwd_tool))

        try:
            await run_turn(
                session=sess,
                user_text=message,
                provider=provider,
                tools=tools,
                tool_consent=None,  # read-only — auto-approve all
                events=self.kernel.events,
                app_ref=self,
                system=system,
                max_iters=TOOLS_MAX_ITERS,
            )
        except Exception as e:
            return f"Tool-use error: {type(e).__name__}: {e}", provider.name, []
        finally:
            for u in _unsubs:
                try:
                    u()
                except Exception:
                    pass

        # Extract final assistant text from the new messages.
        final_text = ""
        tool_log: list[dict] = []
        for m in sess.messages[pre_len:]:
            role = m.get("role")
            content = m.get("content", "")
            if role == "assistant":
                # Anthropic shape: list of blocks. OpenAI shape: string + tool_calls.
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "text":
                            final_text += blk.get("text", "")
                        elif isinstance(blk, dict) and blk.get("type") == "tool_use":
                            tool_log.append({"name": blk.get("name"), "input": blk.get("input")})
                elif isinstance(content, str):
                    final_text += content
                # OpenAI-native tool calls live on the message dict
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    tool_log.append({"name": fn.get("name"), "input": fn.get("arguments")})

        return final_text.strip(), provider.name, tool_log

    # ── WebSocket Chat ─────────────────────────────────────────

    @ws_route("/ws/{session_id}")
    async def ws_chat(self, websocket):
        """Main chat WebSocket — streaming AI responses."""
        session_id = websocket.path_params.get("session_id", "")
        session = self._get_session(session_id)
        if not session:
            await websocket.send_json({"type": "error", "message": "Session not found"})
            return

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type", "message")

                if msg_type == "message":
                    text = data.get("text", "").strip()
                    if not text:
                        continue

                    # Save user message
                    async with self._sessions_lock:
                        self._add_message(session_id, "user", text)

                    # Check slash commands first
                    if text.startswith("/"):
                        result = await self._handle_slash(text)
                        if result == "__EXPORT__":
                            export = await self._export_session(session_id)
                            if export.get("ok"):
                                result = f"Session exported to `{export['filename']}`"
                            else:
                                result = f"Export failed: {export.get('error', 'unknown')}"
                        if result is not None:
                            await websocket.send_json(
                                {"type": "agent-reply", "agent": "system", "text": result}
                            )
                            async with self._sessions_lock:
                                self._add_message(session_id, "assistant", result, agent="system")
                            continue

                    # Reload session for latest messages
                    session = self._get_session(session_id) or session

                    # Tool-use retrieval path — non-streaming, replaces the
                    # standard stream below. Per-message flag wins; otherwise
                    # falls back to the assistant.use_tools setting default.
                    use_tools = bool(data.get("use_tools", self._use_tools_default()))
                    if use_tools:
                        await websocket.send_json({"type": "agent-thinking", "agent": "tools"})
                        _stream_text = ""

                        async def _stream_to_ws(ev_type: str, ev_data: dict):
                            nonlocal _stream_text
                            try:
                                if ev_type == "agent:text":
                                    _stream_text += ev_data.get("delta", "")
                                    await websocket.send_json(
                                        {
                                            "type": "agent-stream",
                                            "agent": "tools",
                                            "text": _stream_text,
                                        }
                                    )
                                elif ev_type == "agent:tool_call":
                                    await websocket.send_json(
                                        {
                                            "type": "agent-status",
                                            "agent": "tools",
                                            "status": f"using {ev_data.get('name', 'tool')}",
                                            "tool": ev_data.get("name", ""),
                                        }
                                    )
                            except Exception:
                                pass

                        try:
                            tool_text, tool_prov, tool_log = await self._chat_with_tools(
                                text, session, on_event=_stream_to_ws
                            )
                        except Exception as e:
                            tool_text, tool_prov, tool_log = f"Tool-use error: {e}", "error", []
                        label = f"tools:{tool_prov}"
                        await websocket.send_json(
                            {
                                "type": "agent-reply",
                                "agent": label,
                                "text": tool_text,
                                "tool_calls": tool_log,
                            }
                        )
                        await websocket.send_json({"type": "agent-done"})
                        async with self._sessions_lock:
                            self._add_message(session_id, "assistant", tool_text, agent=label)
                        await self.emit(
                            "assistant:message", {"session": session_id, "provider": label}
                        )
                        if session.get("name", "").startswith("New chat"):
                            asyncio.create_task(self._auto_name(session_id, text, websocket))
                        continue

                    provider, explicit_backend = self._pick_provider_label(session)

                    # Build prompt with context
                    await websocket.send_json({"type": "agent-thinking", "agent": provider})

                    context = await self._build_context(text, session=session)

                    system = await self._build_system(session)

                    chat_messages = self._build_chat_messages(session, vault_context=context)

                    # Stream response with cancel support
                    full_text = ""
                    original_provider = provider
                    self._cancel_flags[session_id] = False
                    try:
                        async for chunk in self.think_stream(
                            messages=chat_messages, system=system, domain="text"
                        ):
                            # Check cancel flag
                            if self._cancel_flags.get(session_id):
                                self._cancel_flags.pop(session_id, None)
                                break

                            # Provider-used marker from capability layer — carries
                            # the name of the provider that will answer. Arrives
                            # with the first chunk, so UI can update labels early.
                            if "provider_used" in chunk:
                                used = chunk["provider_used"]
                                if used and used != provider:
                                    await websocket.send_json(
                                        {
                                            "type": "provider-resolved",
                                            "from": original_provider,
                                            "to": used,
                                            "was_switch": explicit_backend,
                                        }
                                    )
                                    provider = used
                                continue

                            # Tool status events (from claude-cli stream-json)
                            if "tool_status" in chunk:
                                await websocket.send_json(
                                    {
                                        "type": "agent-status",
                                        "agent": provider,
                                        "status": chunk["tool_status"],
                                        "tool": chunk.get("tool", ""),
                                    }
                                )
                                continue

                            # Token usage events (from openai)
                            if "usage" in chunk:
                                await websocket.send_json(
                                    {
                                        "type": "agent-usage",
                                        "agent": provider,
                                        **chunk["usage"],
                                    }
                                )
                                continue

                            delta = chunk.get("text", "")
                            if delta:
                                full_text += delta
                                await websocket.send_json(
                                    {
                                        "type": "agent-stream",
                                        "agent": provider,
                                        "text": full_text,
                                    }
                                )
                    except Exception as stream_err:
                        # Streaming chain exhausted — fall back to the blocking
                        # think() which walks the default chain. Skip retrying
                        # the same provider that just failed.
                        self.log_warn(f"stream failed: {type(stream_err).__name__}: {stream_err}")
                        if not full_text:
                            try:
                                await websocket.send_json(
                                    {
                                        "type": "agent-status",
                                        "agent": provider,
                                        "status": "Streaming failed — retrying without streaming…",
                                    }
                                )
                            except Exception:
                                pass
                            try:
                                full_text = await asyncio.wait_for(
                                    self.think(
                                        messages=chat_messages, system=system, domain="text"
                                    ),
                                    timeout=FALLBACK_THINK_TIMEOUT,
                                )
                            except TimeoutError:
                                full_text = (
                                    f"Error: provider did not respond within {FALLBACK_THINK_TIMEOUT:.0f}s. "
                                    "Try again or switch backend in settings."
                                )
                                self.log_error(
                                    f"think fallback timed out after {FALLBACK_THINK_TIMEOUT:.0f}s"
                                )
                            except Exception as e:
                                full_text = f"Error: {e}"
                                self.log_error(f"think fallback failed: {type(e).__name__}: {e}")
                    finally:
                        self._cancel_flags.pop(session_id, None)
                        # Persist FIRST, before the final WS sends. If the WS
                        # has dropped mid-stream, the send_json calls below
                        # raise and the assistant text would otherwise vanish
                        # on refresh — user msg saved at line 500, reply lost.
                        if full_text:
                            try:
                                async with self._sessions_lock:
                                    self._add_message(
                                        session_id, "assistant", full_text, agent=provider
                                    )
                            except Exception as _persist_err:
                                self.log_warn(f"persist on stream-end failed: {_persist_err}")

                    try:
                        await websocket.send_json(
                            {"type": "agent-reply", "agent": provider, "text": full_text}
                        )
                        await websocket.send_json({"type": "agent-done"})
                    except Exception:
                        pass  # WS already gone — message is already persisted above

                    await self.emit(
                        "assistant:message", {"session": session_id, "provider": provider}
                    )

                    # Auto-name session after first exchange
                    if session.get("name", "").startswith("New chat"):
                        asyncio.create_task(self._auto_name(session_id, text, websocket))

                elif msg_type == "cancel":
                    self._cancel_flags[session_id] = True

                elif msg_type == "set-backend":
                    self.db.execute(
                        "UPDATE sessions SET backend = ? WHERE id = ?",
                        (data.get("backend", "auto"), session_id),
                    )
                    self.db.commit()
                    session = self._get_session(session_id) or session

                elif msg_type == "set-system-prompt":
                    self.db.execute(
                        "UPDATE sessions SET system_prompt = ? WHERE id = ?",
                        (data.get("system_prompt", ""), session_id),
                    )
                    self.db.commit()
                    session = self._get_session(session_id) or session

        except Exception as e:
            # Client disconnected or unexpected WS error — log so silent hangs
            # on the UI have a trace in syslog.
            if not isinstance(e, (asyncio.CancelledError,)):
                try:
                    self.log_warn(f"assistant ws loop ended: {type(e).__name__}: {e}")
                except Exception:
                    pass

    # ── REST API: Attachments ──────────────────────────────────

    @web_route("GET", "/api/vault-files")
    async def api_vault_files(self, request):
        """Search vault notes for the attachment picker.

        Query params:
          q     — substring match on note name (case-insensitive)
          tag   — tag filter (hierarchical via VaultIndex)
          limit — cap results (default 50)
        """
        q = (request.query_params.get("q") or "").strip().lower()
        tag = (request.query_params.get("tag") or "").strip()
        try:
            limit = max(1, min(200, int(request.query_params.get("limit") or 50)))
        except ValueError:
            limit = 50

        vi = self.kernel.services.get_optional("vault_index")
        if not vi:
            return {"files": []}

        entries = vi.find(tags=[tag] if tag else None)
        if q:
            entries = [
                e
                for e in entries
                if q in e.get("name", "").lower() or q in e.get("path", "").lower()
            ]
        # Sort newest-first by modified mtime
        entries.sort(key=lambda e: e.get("modified", 0), reverse=True)
        files = [
            {
                "path": e.get("path", ""),
                "name": e.get("name", ""),
                "folder": e.get("folder", ""),
                "tags": list(e.get("tags", []))[:6],
                "modified": e.get("modified", 0),
            }
            for e in entries[:limit]
        ]
        return {"files": files, "total": len(entries)}

    @web_route("POST", "/api/upload")
    async def api_upload(self, request):
        """Accept a file upload, save to vault inbox attachments, return its vault path.

        Pattern mirrors apps/reports/app.py api_upload_figure: starlette form parsing.
        """
        import re

        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return {"error": "no file in 'file' form field"}

        max_mb = int(self.app_config("upload_max_mb", 50))
        data = await upload.read()
        if not data:
            return {"error": "empty file"}
        if len(data) > max_mb * 1024 * 1024:
            return {"error": f"file too large ({len(data) // (1024 * 1024)}MB > {max_mb}MB cap)"}

        vault_root = self.kernel.config.notes_path
        if not vault_root:
            return {"error": "no vault configured"}

        rel_dir = self.vault_config("attachments", "00_Inbox/_attachments")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        raw_name = upload.filename or "upload.bin"
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-") or "upload.bin"
        rel_path = f"{rel_dir}/{ts}-{safe_name}"

        abs_path = Path(vault_root) / rel_path
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(data)
        except Exception as e:
            return {"error": f"write failed: {e}"}

        # Re-index if it's markdown so VaultIndex picks it up immediately.
        if rel_path.endswith(".md"):
            vi = self.kernel.services.get_optional("vault_index")
            if vi:
                try:
                    vi.index_file(rel_path)
                except Exception:
                    pass

        return {
            "path": rel_path,
            "name": raw_name,
            "size": len(data),
            "mime": getattr(upload, "content_type", "") or "",
        }

    # ── REST API: Sessions ─────────────────────────────────────

    @web_route("GET", "/api/sessions")
    async def api_list_sessions(self, request):
        rows = self.db.execute("""
            SELECT s.id, s.name, s.backend, s.created,
                   COUNT(m.id) as message_count,
                   MAX(m.ts) as last_message
            FROM sessions s LEFT JOIN messages m ON s.id = m.session_id
            GROUP BY s.id ORDER BY COALESCE(MAX(m.ts), s.created) DESC
        """).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "backend": r["backend"],
                "message_count": r["message_count"],
                "created": r["created"],
                "last_message": r["last_message"] or "",
            }
            for r in rows
        ]

    @web_route("POST", "/api/sessions")
    async def api_create_session(self, request):
        data = await request.json()
        async with self._sessions_lock:
            session = self._create_session(data.get("name", ""), data.get("backend", "auto"))
        return session

    @web_route("GET", "/api/sessions/{sid}")
    async def api_get_session(self, request):
        sid = request.path_params["sid"]
        session = self._get_session(sid)
        if not session:
            return {"error": "not found"}
        return session

    @web_route("PUT", "/api/sessions/{sid}")
    async def api_update_session(self, request):
        sid = request.path_params["sid"]
        data = await request.json()
        async with self._sessions_lock:
            row = self.db.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
            if not row:
                return {"error": "not found"}
            _ALLOWED_COLS = {"name", "backend", "system_prompt"}
            for key in _ALLOWED_COLS:
                if key in data:
                    self.db.execute(
                        "UPDATE sessions SET " + key + " = ? WHERE id = ?", (data[key], sid)
                    )
            self.db.commit()
        return self._get_session(sid)

    @web_route("DELETE", "/api/sessions/{sid}")
    async def api_delete_session(self, request):
        sid = request.path_params["sid"]
        async with self._sessions_lock:
            self.db.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            self.db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            self.db.commit()
        return {"ok": True}

    @web_route("POST", "/api/sessions/{sid}/export")
    async def api_export_session(self, request):
        """Export session to vault as markdown."""
        sid = request.path_params["sid"]
        return await self._export_session(sid)

    @web_route("POST", "/api/archive")
    async def api_archive(self, request):
        """Archive old idle sessions to vault."""
        archived = await self._auto_archive()
        return {"archived": len(archived), "sessions": archived}

    # ── REST API: Legacy chat (backward compat) ────────────────

    @web_route("POST", "/api/chat")
    async def api_chat(self, request):
        data = await request.json()
        message = data.get("message", "")
        session_id = data.get("session_id", "")
        if not message:
            return {"error": "message required"}

        # Slash command check
        if message.startswith("/"):
            result = await self._handle_slash(message)
            if result:
                if session_id:
                    async with self._sessions_lock:
                        self._add_message(session_id, "user", message)
                        self._add_message(session_id, "assistant", result, agent="system")
                return {"response": result, "message": message, "provider": "system"}

        # Tool-use retrieval path — opt-in per request, or via setting default.
        use_tools = bool(data.get("use_tools", self._use_tools_default()))

        session = None
        if session_id:
            async with self._sessions_lock:
                self._add_message(session_id, "user", message)
            session = self._get_session(session_id)

        if use_tools:
            text, prov_name, tool_log = await self._chat_with_tools(message, session)
            if session_id and text:
                async with self._sessions_lock:
                    self._add_message(session_id, "assistant", text, agent=f"tools:{prov_name}")
            return {
                "response": text,
                "message": message,
                "provider": f"tools:{prov_name}",
                "tool_calls": tool_log,
            }

        # Classic path — keyword-grep context + single LLM call.
        context = ""
        if data.get("context", True):
            context = await self._build_context(message, session=session)

        system = await self._build_system()

        if session:
            chat_messages = self._build_chat_messages(session, vault_context=context)
        else:
            user_content = f"[Vault context]\n{context}\n\n{message}" if context else message
            chat_messages = [{"role": "user", "content": user_content}]

        provider = self._get_provider(message, session) if session else "openai"
        try:
            result = await self._think_with_provider(
                provider,
                "",
                "text",
                {"system": system, "messages": chat_messages},
            )
            if not result:
                result = await self.think(
                    messages=chat_messages,
                    system=system,
                    domain="text",
                    temperature=0.4,
                )
                provider = "default"
        except RuntimeError as e:
            # AI-offline → let the server.py middleware turn it into a 503; a 200 OK
            # with "Error: ..." in the body looks like a real reply to the UI.
            if "No available provider for capability" in str(e):
                raise
            result = f"Error: {e}"
            provider = "error"
        except Exception as e:
            result = f"Error: {e}"
            provider = "error"

        if session_id:
            async with self._sessions_lock:
                self._add_message(session_id, "assistant", result, agent=provider)

        return {"response": result, "message": message, "provider": provider}

    @web_route("GET", "/api/providers")
    async def api_providers(self, request):
        """Available LLM providers."""
        cap = self.kernel.capability("think")
        providers = []
        seen = set()
        for p in cap.providers:
            if p.name not in seen:
                try:
                    avail = await p.available()
                except Exception:
                    avail = False
                providers.append({"name": p.name, "available": avail})
                seen.add(p.name)
        return providers

    @web_route("GET", "/api/slash-commands")
    async def api_slash_commands(self, request):
        """List available slash commands."""
        cmds = [
            {"command": c, "app": a, "method": m}
            for c, (a, m, _) in sorted(self._slash_commands.items())
        ]
        cmds.append({"command": "/help", "app": "assistant", "method": "help"})
        cmds.append({"command": "/new", "app": "assistant", "method": "new session"})
        return cmds

    # ── Compare Mode ──────────────────────────────────────────

    @web_route("POST", "/api/compare")
    async def api_compare(self, request):
        """Send same prompt to ALL providers. Returns [{provider, response, latency_ms}].

        Persists into the session if `session_id` is supplied — user message +
        one combined assistant message containing every provider's reply, so
        the conversation survives refresh.
        """
        data = await request.json()
        message = data.get("message", "")
        session_id = data.get("session_id", "")
        if not message:
            return {"error": "message required"}

        if session_id:
            async with self._sessions_lock:
                self._add_message(session_id, "user", message)

        session = self._get_session(session_id) if session_id else None
        context = await self._build_context(message, session=session)
        system = await self._build_system()
        prompt = f"Vault context:\n{context}\n\nQuestion: {message}" if context else message

        results = await self.think_compare(prompt, system=system, domain="text")
        # Normalize — extract text from CapabilityResult objects
        normalized = []
        for r in results:
            resp = r.get("response", "")
            if hasattr(resp, "value"):
                resp = resp.value
            normalized.append(
                {
                    "provider": r.get("provider", "unknown"),
                    "text": str(resp)[:4000],
                    "latency_ms": r.get("latency_ms", 0),
                    "error": r.get("error"),
                }
            )

        if session_id and normalized:
            # Combine into one assistant turn so the next chat turn sees one
            # coherent prior reply, not N parallel "assistant" rows.
            blocks = []
            for r in normalized:
                if r.get("error"):
                    blocks.append(f"**{r['provider']}** — *error:* {r['error']}")
                else:
                    blocks.append(f"**{r['provider']}** ({r['latency_ms']}ms):\n{r['text']}")
            combined = "\n\n---\n\n".join(blocks)
            async with self._sessions_lock:
                self._add_message(session_id, "assistant", combined, agent="compare")

        return {"question": message, "results": normalized}

    # ── Voice I/O ─────────────────────────────────────────────

    @web_route("POST", "/api/tts")
    async def api_tts(self, request):
        """Text-to-speech via platform speak capability."""
        data = await request.json()
        text = data.get("text", "")
        if not text:
            return {"error": "text required"}
        try:
            result = await self.speak(text)
            # Result is a file path or audio bytes
            if isinstance(result, (str, Path)):
                return {
                    "audio_url": f"/assistant/api/audio/{Path(result).name}",
                    "path": str(result),
                }
            return {"error": "unexpected TTS result type"}
        except Exception as e:
            return {"error": f"TTS failed: {e}"}

    @web_route("POST", "/api/stt")
    async def api_stt(self, request):
        """Speech-to-text via platform listen capability."""
        data = await request.json()
        audio_path = data.get("audio_path", "")
        if not audio_path:
            return {"error": "audio_path required"}
        try:
            text = await self.listen(audio_path)
            return {"text": text}
        except Exception as e:
            return {"error": f"STT failed: {e}"}

    # ── PWA ───────────────────────────────────────────────────

    @web_route("GET", "/manifest.json")
    async def api_manifest(self, request):
        from starlette.responses import JSONResponse

        return JSONResponse(
            {
                "name": "EmptyOS Assistant",
                "short_name": "Assistant",
                "description": "AI chat with vault integration",
                "start_url": "/assistant/",
                "display": "standalone",
                "background_color": "#1a1a2e",
                "theme_color": "#1a1a2e",
                "icons": [
                    {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                ],
            }
        )

    # ── CLI ────────────────────────────────────────────────────

    @cli_command("ask", help="Ask EmptyOS anything")
    async def cmd_ask(self, question: str = ""):
        if not question:
            print("  Usage: eos ask 'your question here'")
            return
        context = await self._build_context(question)
        system = await self._build_system()
        prompt = f"Vault context:\n{context}\n\n{question}" if context else question
        response = await self.think(prompt, system=system, domain="text", temperature=0.4)
        print(f"\n  {response}\n")
