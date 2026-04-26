"""Agent app — Claude-Code-like coding agent with tool-use loop.

Exposes three surfaces:
    /agent/             — web UI (streaming WS + permission modal)
    /agent/ws/{session} — WebSocket for live turn streaming
    eos chat            — interactive terminal REPL (see apps/agent/repl.py)

All three drive the same `run_turn()` loop in `emptyos/sdk/agent_loop.py`, with
the same tool registry (`emptyos/sdk/agent_tools/`) and permission manager
(`kernel.tool_consent`). The only difference is the transport surfacing events.

Module layout (kept atomic — P4 Atomic):
    app.py       — class body, setup, provider resolution, WS loop (hot path)
    sessions.py  — session CRUD / archive / revert / edit-stack helpers (mixin)
    orient.py    — pre-turn classify + plan pipeline
    routes.py    — @web_route HTTP handlers + tool-hook helpers
    repl.py      — `eos chat` terminal REPL + shared SLASH_COMMANDS
    prompts.py   — system/user prompt templates
    context.py   — runtime / app catalog / skill catalog blocks
    skills.py    — Claude-Code-compatible skill discovery
    tools/       — agent-loop tool registry
"""

from __future__ import annotations

import asyncio

from emptyos.sdk import BaseApp, ChatSessionStore, web_route, ws_route
from emptyos.sdk.agent_loop import (
    AgentSession, run_turn, run_native_turn,
    DEFAULT_SYSTEM_PROMPT, DEFAULT_MAX_ITERS, EDIT_PATH_LIMIT,
)
from emptyos.sdk.agent_tools import build_registry

DEFAULT_TURN_TIMEOUT = 180.0  # seconds — cap on a single turn's tool-loop; override via agent.turn_timeout


from apps.agent import context
from apps.agent import orient as _orient_mod
from apps.agent import repl as _repl
from apps.agent import routes as _routes
from apps.agent.sessions import SessionMixin


class AgentApp(SessionMixin, BaseApp):

    # Per-session cancel events, keyed by session_id
    _sessions_lock: asyncio.Lock
    _live_sessions: dict[str, AgentSession]

    async def setup(self):
        await super().setup()
        self._sessions_lock = asyncio.Lock()
        self._live_sessions = {}
        self._tools = build_registry()
        # Plan-mode flag per session (in-memory, session-scoped — clears on
        # daemon restart). When True, the run_turn tool gate rejects non-readonly
        # tools so the agent investigates + proposes without touching anything.
        self._plan_modes: dict[str, bool] = {}
        # Edit-history stack per session — every successful Write/Edit that
        # carries `previous_content` gets pushed here. CLI /revert and web
        # POST /api/sessions/{sid}/revert both pop from this. In-memory
        # (clears on daemon restart) — /revert is a "just did that" undo,
        # not a cross-session restore tool.
        self._edit_stacks: dict[str, list[dict]] = {}
        # Per-session override of run_turn's edit_path_limit. Default (None)
        # means use EDIT_PATH_LIMIT. Bumped by the /grant-edits slash command
        # when the user explicitly authorizes more edits to the same file in
        # one turn (e.g. a big refactor the guard would otherwise block).
        self._edit_limits: dict[str, int] = {}
        # Per-session override of run_turn's max_iters. Same pattern as
        # _edit_limits — bumped by /grant-iters when a legitimately long
        # task hit the default cap. None means read agent.max_iters setting.
        self._iter_limits: dict[str, int] = {}
        # Tool hooks — callables invoked before/after every tool dispatch.
        # Signature: hook(session_id: str, tool_name: str, input: dict, result?=None)
        # Register via self.register_tool_hook(before=fn) / register_tool_hook(after=fn)
        self._before_tool_hooks: list = []
        self._after_tool_hooks: list = []
        # Persistent tool audit log — every tool call appended to JSONL file.
        self._audit_path = self.data_dir / "tool-audit.jsonl"
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.register_tool_hook(after=self._audit_log_hook)
        self.register_tool_hook(after=self._task_persist_hook)
        self._sessions = ChatSessionStore(
            self.db,
            prefix="agent_",
            session_extras={"provider": "TEXT NOT NULL DEFAULT ''"},
            message_extras={"provider_kind": "TEXT NOT NULL DEFAULT 'anthropic'"},
        )
        self._sessions.init_schema()

    # Session storage, archive, edit-stack, and per-session limits live on
    # SessionMixin (apps/agent/sessions.py).

    # ── Provider selection ───────────────────────────────────

    def _default_provider_name(self) -> str:
        # Standard tier = openai (gpt-5.4-mini) — best $/quality balance.
        # User overrides via settings 'agent.default_provider'.
        settings = self.service("settings")
        if settings:
            return settings.get("agent.default_provider") or "openai"
        return "openai"

    def _resolve_provider(self, name: str, *, strict: bool = False):
        """Find an agent-driving provider by name.

        Two kinds qualify:
          • ToolCapableProvider — our loop drives it (Anthropic SDK, OpenAI, Ollama)
          • NativelyAgenticProvider — runs its own loop (claude-cli)

        Resolution:
          1. Exact name match, prefer either kind
          2. (strict=False only) First kind-match in the chain (ToolCapable
             first, then Native) — used when the request is "give me any
             working agent provider", e.g. session restore after a provider
             was renamed or removed.

        Pass strict=True for explicit user requests like `/model X` so a
        typo or unloaded section returns None instead of silently routing
        to a different model.
        """
        from emptyos.capabilities.providers._tool_capable import (
            NativelyAgenticProvider, ToolCapableProvider,
        )

        think = self.kernel.capability("think")
        candidates: list = list(think.providers)
        for chain in think._domains.values():
            candidates.extend(chain)
        for chain in think._buckets.values():
            candidates.extend(chain)

        def agent_ok(p):
            return isinstance(p, (ToolCapableProvider, NativelyAgenticProvider))

        # Exact name match first
        for p in candidates:
            if p.name == name and agent_ok(p):
                return p
        if strict:
            return None
        # Fall back to any tool-capable provider (our-loop wins over native)
        for p in candidates:
            if isinstance(p, ToolCapableProvider):
                return p
        for p in candidates:
            if isinstance(p, NativelyAgenticProvider):
                return p
        return None

    def _is_native_provider(self, provider) -> bool:
        from emptyos.capabilities.providers._tool_capable import NativelyAgenticProvider
        return isinstance(provider, NativelyAgenticProvider)

    def _runtime_info_block(self, provider, is_native: bool) -> str:
        return context.runtime_info_block(self, provider, is_native)

    def _app_catalog_block(self, is_native: bool) -> str:
        return context.app_catalog_block(self, is_native)

    def _claude_md_block(self, is_native: bool) -> str:
        return context.claude_md_block(self, is_native)

    def _extract_development_rules(self, text: str, keep_ids: set[int]) -> str:
        return context.extract_development_rules(text, keep_ids)

    def _load_skill_catalog(self) -> dict:
        return context.load_skill_catalog(self)

    def _expand_skill_slash(self, text: str) -> str | None:
        return context.expand_skill_slash(self, text)

    def _skills_info_block(self, is_native: bool) -> str:
        return context.skills_info_block(self, is_native)

    _APP_SCOPE_PATTERNS = context.APP_SCOPE_PATTERNS

    def _app_scaffold_block(self, user_text: str, is_native: bool) -> str:
        return context.app_scaffold_block(user_text, is_native)

    # ── Tool hooks ───────────────────────────────────────────

    def register_tool_hook(self, *, before=None, after=None) -> None:
        """Register a before/after hook for tool dispatch in run_turn.

        before(session_id, tool_name, input) — called after consent, before tool.run()
        after(session_id, tool_name, input, result) — called after tool.run() (result=None on error)

        Both can be sync or async. Exceptions are silently swallowed so hooks
        never break the agent loop.
        """
        if before is not None:
            self._before_tool_hooks.append(before)
        if after is not None:
            self._after_tool_hooks.append(after)

    # Bound from routes.py so they remain methods on self but live in one place.
    _task_persist_hook = _routes.task_persist_hook
    _audit_log_hook = _routes.audit_log_hook

    # ── Orient-before-Act (pre-turn classification + plan) ──

    _orient = _orient_mod.orient
    _orient_block = _orient_mod.orient_block

    # ── WebSocket — live turn streaming ───────────────────────

    @ws_route("/ws/{session_id}")
    async def ws_turn(self, websocket):
        """Turn loop over WebSocket.

        Client → server:
            {"type": "message", "text": "..."}
            {"type": "cancel"}
            {"type": "approve_permission", "id": "<req>", "scope": "once"|"session"}
            {"type": "deny_permission", "id": "<req>"}

        Server → client: all agent:* events from the loop, flattened as:
            {"type": "agent:text", "delta": "..."}
            {"type": "agent:tool_call", ...}
            {"type": "agent:tool_result", ...}
            {"type": "agent:permission_requested", ...}
            {"type": "agent:done", ...}
        """
        session_id = websocket.path_params.get("session_id", "")
        record = self._get_session(session_id)
        if not record:
            await websocket.send_json({"type": "error", "message": "session not found"})
            return

        # Build a per-connection events shim so the loop's events reach this WS.
        # It also forwards to the kernel bus so other apps can observe.
        kernel_events = self.kernel.events

        class WSBridge:
            async def emit(self_inner, etype, data, source="agent"):
                # Only forward events for this session
                if data.get("session_id") in (session_id, None):
                    try:
                        await websocket.send_json({"type": etype, **data})
                    except Exception:
                        pass
                try:
                    await kernel_events.emit(etype, data, source=source)
                except Exception:
                    pass

        bridge = WSBridge()

        # Some agent:* events are emitted directly on the kernel bus (not via
        # bridge.emit) — notably tool_consent's permission_requested /
        # permission_resolved. Subscribe here so the UI sees them.
        async def _forward_bus_event(event):
            data = event.data or {}
            if data.get("session_id") != session_id:
                return
            try:
                await websocket.send_json({"type": event.type, **data})
            except Exception:
                pass

        unsubs = [
            kernel_events.on("agent:permission_requested", _forward_bus_event),
            kernel_events.on("agent:permission_resolved", _forward_bus_event),
        ]

        # Track the currently-running turn so we keep receiving control
        # messages (approve/deny/cancel) while the agent is mid-loop.
        turn_task: asyncio.Task | None = None

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type", "")

                if msg_type == "message":
                    text = (data.get("text") or "").strip()
                    if not text:
                        continue
                    if turn_task and not turn_task.done():
                        await websocket.send_json({
                            "type": "error",
                            "message": "previous turn still in progress",
                        })
                        continue
                    # /context — server-side slash handled before LLM dispatch
                    if text.strip() == "/context":
                        session = self._sessions.get_session(session_id)
                        msgs = session.get("messages", []) if session else []
                        char_count = sum(len(str(m.get("content", ""))) for m in msgs)
                        plan_active = self._plan_modes.get(session_id, False)
                        ctx_lines = [
                            f"**session** `{session_id}`",
                            f"**messages** {len(msgs)} ({char_count:,} chars)",
                            f"**plan mode** {'ON ⚑' if plan_active else 'off'}",
                            f"**edit limit** {self._edit_limits.get(session_id, EDIT_PATH_LIMIT)}",
                            f"**iter limit** {self._iter_limits.get(session_id, DEFAULT_MAX_ITERS)}",
                        ]
                        await websocket.send_json({
                            "type": "agent:slash_result",
                            "session_id": session_id,
                            "cmd": "/context",
                            "text": "\n".join(ctx_lines),
                        })
                        continue

                    # /archive — summarise session and write to vault
                    if text.strip() == "/archive":
                        await websocket.send_json({
                            "type": "agent:status",
                            "session_id": session_id,
                            "text": "Summarising session…",
                        })
                        result = await self._archive_session(session_id)
                        if result.get("ok"):
                            vault_link = (
                                f" · [open]({result['url']})" if result.get("url") else ""
                            )
                            out = (
                                f"Session archived to vault.\n\n"
                                f"**path** `{result.get('note_path', '')}`{vault_link}"
                            )
                        else:
                            out = f"Archive failed: {result.get('error', 'unknown error')}"
                        await websocket.send_json({
                            "type": "agent:slash_result",
                            "session_id": session_id,
                            "cmd": "/archive",
                            "text": out,
                        })
                        continue

                    # Parity with cmd_chat: a user message starting with
                    # /<skill-name> loads SKILL.md and replaces `text` with the
                    # full playbook-prefixed prompt. No skill match → pass through.
                    expanded = self._expand_skill_slash(text)
                    if expanded:
                        await websocket.send_json({
                            "type": "agent:skill_loaded",
                            "session_id": session_id,
                            "name": text.split()[0][1:],
                        })
                        text = expanded
                    turn_task = asyncio.create_task(
                        self._run_ws_turn(session_id, text, bridge, websocket)
                    )

                elif msg_type == "cancel":
                    sess = self._live_sessions.get(session_id)
                    if sess:
                        sess.cancel()

                elif msg_type == "approve_permission":
                    tool_consent = self.service("tool_consent")
                    if tool_consent:
                        scope = data.get("scope", "once")
                        if scope not in ("once", "session"):
                            scope = "once"
                        tool_consent.approve(data.get("id", ""), scope=scope)

                elif msg_type == "deny_permission":
                    tool_consent = self.service("tool_consent")
                    if tool_consent:
                        tool_consent.deny(data.get("id", ""))

                elif msg_type == "set_plan_mode":
                    # Web-UI path for /plan /execute /scrap — flip the session's
                    # plan-mode flag. The tool gate in run_turn reads this on
                    # every iteration, so the change takes effect on the next
                    # turn (mid-turn toggling doesn't interrupt an active call).
                    on = bool(data.get("on", False))
                    if on:
                        self._plan_modes[session_id] = True
                    else:
                        self._plan_modes.pop(session_id, None)
                    await websocket.send_json({
                        "type": "agent:plan_mode",
                        "session_id": session_id,
                        "on": self._plan_modes.get(session_id, False),
                    })

        except Exception:
            pass  # client disconnected
        finally:
            if turn_task and not turn_task.done():
                turn_task.cancel()
            for u in unsubs:
                try: u()
                except Exception: pass
            self._live_sessions.pop(session_id, None)

    async def _run_ws_turn(self, session_id: str, user_text: str, bridge, websocket):
        record = self._get_session(session_id)
        if not record:
            await websocket.send_json({"type": "error", "message": "session not found"})
            return

        provider_name = record.get("provider") or self._default_provider_name()
        provider = self._resolve_provider(provider_name)
        if provider is None:
            await websocket.send_json({
                "type": "error",
                "message": f"no tool-capable provider available (tried {provider_name!r}). "
                           "Install anthropic SDK or configure a function-calling provider.",
            })
            return

        is_native = self._is_native_provider(provider)
        provider_kind = "native" if is_native else provider.kind

        # Build the in-memory session with existing messages
        sess = AgentSession(
            id=session_id,
            messages=self._load_provider_messages(session_id),
            provider_kind=provider_kind,
        )
        self._live_sessions[session_id] = sess

        system = record.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        system = system + "\n\n" + self._runtime_info_block(provider, is_native)
        scaffold = self._app_scaffold_block(user_text, is_native)
        if scaffold:
            system = system + "\n\n" + scaffold
        if self._plan_modes.get(session_id, False):
            system += (
                "\n\n⚑ PLAN MODE ACTIVE — you are in read-only investigation phase. "
                "Write, Edit, Bash (non-readonly), RestartDaemon, CallApp, and "
                "Fetch non-GET are BLOCKED. Use Read, Grep, Glob, Skill, TaskList, "
                "Screenshot, and Fetch-GET. Draft a plan, then STOP — the user will "
                "type /execute or /scrap."
            )
        settings = self.service("settings")
        max_iters = DEFAULT_MAX_ITERS
        if settings:
            try:
                max_iters = int(settings.get("agent.max_iters") or DEFAULT_MAX_ITERS)
            except (TypeError, ValueError):
                pass

        tool_consent = self.service("tool_consent")
        pre_len = len(sess.messages)

        # Orient-before-act: inject a pre-turn analysis block on first turns.
        # Native providers get it in the system prompt; tool-capable gets it
        # prepended to the user message so it's in the conversation history.
        orient_text = user_text
        await websocket.send_json({
            "type": "agent:status", "session_id": session_id,
            "status": "Orienting…",
        })
        try:
            orient_plan = await asyncio.wait_for(
                self._orient(user_text, session_id), timeout=12.0
            )
        except asyncio.TimeoutError:
            orient_plan = None
            await websocket.send_json({
                "type": "agent:status", "session_id": session_id,
                "status": "Orient skipped (timeout)",
            })
        except Exception as e:
            orient_plan = None
            self.log_warn(f"orient failed: {e}")
            await websocket.send_json({
                "type": "agent:status", "session_id": session_id,
                "status": f"Orient skipped ({type(e).__name__})",
            })
        if orient_plan:
            orient_block_text = self._orient_block(orient_plan)
            if is_native:
                system = system + "\n\n" + orient_block_text
            else:
                orient_text = orient_block_text + "\n\n" + user_text
            await websocket.send_json({
                "type": "agent:orient",
                "session_id": session_id,
                "plan": orient_plan,
            })

        turn_timeout = DEFAULT_TURN_TIMEOUT
        if settings:
            try:
                turn_timeout = float(settings.get("agent.turn_timeout") or DEFAULT_TURN_TIMEOUT)
            except (TypeError, ValueError):
                pass
        try:
            if is_native:
                coro = run_native_turn(
                    session=sess,
                    user_text=user_text,
                    provider=provider,
                    events=bridge,
                    system=system,
                )
            else:
                coro = run_turn(
                    session=sess,
                    user_text=orient_text,
                    provider=provider,
                    tools=self._tools,
                    tool_consent=tool_consent,
                    events=bridge,
                    app_ref=self,
                    system=system,
                    max_iters=max_iters,
                    orient_plan=orient_plan,
                    edit_path_limit=self._edit_limit_for(session_id),
                )
            if turn_timeout > 0:
                await asyncio.wait_for(coro, timeout=turn_timeout)
            else:
                await coro
        except asyncio.CancelledError:
            try:
                await websocket.send_json({"type": "agent:cancelled", "session_id": session_id})
            except Exception:
                pass
        except asyncio.TimeoutError:
            self.log_warn(f"turn timed out after {turn_timeout}s (session {session_id})")
            try:
                await websocket.send_json({
                    "type": "agent:error", "session_id": session_id,
                    "error": f"Turn timed out after {turn_timeout:.0f}s — the provider or a tool stopped responding. Try /cancel and resend, or switch backend.",
                })
            except Exception:
                pass
        except Exception as e:
            self.log_error(f"turn failed: {type(e).__name__}: {e}")
            try:
                await websocket.send_json({
                    "type": "agent:error", "session_id": session_id, "error": str(e),
                })
            except Exception:
                pass

        # Persist new messages (full dict — preserves OpenAI tool_calls / tool_call_id)
        for m in sess.messages[pre_len:]:
            self._persist_message(session_id, m, provider_kind)

        self._live_sessions.pop(session_id, None)

    # ── HTTP routes (extracted to routes.py) ──────────────────
    api_list_sessions    = _routes.api_list_sessions
    api_get_session      = _routes.api_get_session
    api_create_session   = _routes.api_create_session
    api_delete_session   = _routes.api_delete_session
    api_update_session   = _routes.api_update_session
    api_archive_session  = _routes.api_archive_session
    api_session_tasks    = _routes.api_session_tasks
    api_fork_session     = _routes.api_fork_session
    api_revert           = _routes.api_revert
    api_edit_stack       = _routes.api_edit_stack
    api_mcp_tool_call    = _routes.api_mcp_tool_call
    api_tool_audit       = _routes.api_tool_audit
    api_mcp_tools        = _routes.api_mcp_tools
    api_list_tools       = _routes.api_list_tools
    api_skills           = _routes.api_skills
    api_slash_commands   = _routes.api_slash_commands
    api_approve_permission = _routes.api_approve_permission
    api_deny_permission    = _routes.api_deny_permission
    api_list_permissions   = _routes.api_list_permissions
    api_status             = _routes.api_status

    # ── CLI (extracted to repl.py) ────────────────────────────
    cmd_chat = _repl.cmd_chat
