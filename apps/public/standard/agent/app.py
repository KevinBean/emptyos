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

from emptyos.sdk import BaseApp, ChatSessionStore, normalize_run_status, ws_route
from emptyos.sdk.agent_loop import (
    DEFAULT_MAX_ITERS,
    DEFAULT_SYSTEM_PROMPT,
    EDIT_PATH_LIMIT,
    AgentSession,
    run_native_turn,
    run_turn,
)
from emptyos.sdk.agent_tools import build_registry
from emptyos.sdk.attachments import (
    build_user_content,
    dehydrate_content,
    hydrate_messages,
    mark_vault_derived,
    provider_reads_images,
)
from emptyos.sdk.agent_tools.restricted import ScopedReadTool, VerbCallAppTool, WebFetchTool

DEFAULT_TURN_TIMEOUT = (
    180.0  # seconds — cap on a single turn's tool-loop; override via agent.turn_timeout
)


from . import artifacts as _artifacts
from . import connectors as _connectors
from . import context
from . import modes as _modes
from . import orient as _orient_mod
from . import projects as _projects
from . import repl as _repl
from . import routes as _routes
from . import turn_inputs as _turn_inputs
from .profiles import profile_for, select_session_tools
from .prompts import PROMPTS
from .sessions import SessionMixin


class AgentApp(SessionMixin, BaseApp):
    # Read-only on the harness-runs board — subagent runs are a record, not
    # an editable collection.
    SETTABLE_FIELDS: set[str] = set()

    # Per-session cancel events, keyed by session_id
    _sessions_lock: asyncio.Lock
    _live_sessions: dict[str, AgentSession]
    # Sessions with a WS turn in flight, across ALL sockets — see _claim_turn.
    _turn_active: set[str]

    async def list_all(self) -> list[dict]:
        """Normalized run rows for the cross-app harness-runs board (boards
        view-layer contract). Surfaces detached (background) SubAgent runs —
        see emptyos/sdk/agent_tools/subagent.py. Read-only (SETTABLE_FIELDS).
        """
        rows = []
        for _h, m in self.runs("subagent-runs").recent_states(50):
            native = m.get("status", "")
            rows.append({
                "id": m.get("run_id", ""),
                "harness": "agent",
                "kind": "subagent",
                "title": m.get("task", "") or m.get("run_id", ""),
                "target": "",
                "status": native,
                "phase": normalize_run_status(native, finished=m.get("finished")),
                "branch": "",
                "diff_stat": "",
                "scope": m.get("session_id") or m.get("parent_session") or "",
                "started": m.get("started", ""),
                "finished": m.get("finished", ""),
            })
        return rows

    async def list_sessions_summary(self, limit: int = 8) -> dict:
        """Read-only session summary for sibling work surfaces such as /code/.

        The agent app owns the session store; UI-shaped siblings should consume
        this method instead of reaching into ``_sessions`` directly.
        """
        try:
            n = max(1, min(50, int(limit)))
        except (TypeError, ValueError):
            n = 8
        sessions = self._sessions.list_sessions()
        return {
            "total": len(sessions),
            "active": sum(1 for s in sessions if s.get("status") == "active"),
            "recent": [
                {
                    "id": s.get("id", ""),
                    "name": s.get("name", "") or s.get("id", ""),
                    "status": s.get("status", ""),
                    "provider": s.get("provider", ""),
                    "message_count": int(s.get("message_count") or 0),
                    "last_message": s.get("last_message") or s.get("created", ""),
                    "created": s.get("created", ""),
                }
                for s in sessions[:n]
            ],
        }

    async def setup(self):
        await super().setup()
        self._sessions_lock = asyncio.Lock()
        self._live_sessions = {}
        self._turn_active = set()
        self._tools = build_registry()
        # The narrowed Fetch / CallApp / Read a restricted profile gets instead
        # of the stock tools (profiles.NARROWED, agent_tools/restricted.py).
        self._narrowed_tools = {
            "Fetch": WebFetchTool(),
            "CallApp": VerbCallAppTool(),
            "Read": ScopedReadTool(),
        }
        # Inbound MCP (dark-flagged): consume external MCP servers' tools.
        self._mcp_clients: list = []
        await self._maybe_connect_inbound_mcp()
        # Browser-use (dark-flagged): interactive step-by-step browser driving on
        # top of the shared Playwright plugin. Off by default
        # ([apps.agent] feature.browser.enabled) → registry byte-identical.
        from emptyos.sdk.agent_tools.base import feature_enabled

        if feature_enabled(self, "browser"):
            from emptyos.sdk.agent_tools.browse import BrowseTool

            self._tools["Browse"] = BrowseTool()
        # Artifacts (dark-flagged, B4): a chat answer that is a THING — a page
        # the model writes and viz stores, shown in a side panel. Removed from
        # the registry rather than filtered per profile, so with the flag off no
        # surface (chat, /agent/, MCP) is offered a schema it cannot fulfil and
        # the tool set is byte-identical to pre-feature.
        if not feature_enabled(self, "artifacts"):
            self._tools.pop("CreateArtifact", None)
        # Plan-mode flag per session (in-memory, session-scoped — clears on
        # daemon restart). When True, the run_turn tool gate rejects non-readonly
        # tools so the agent investigates + proposes without touching anything.
        self._plan_modes: dict[str, bool] = {}
        # Behavioral mode per session (in-memory, session-scoped) — the sticky
        # /mode persona (code/research/strategy). Distinct from plan-mode above
        # (which gates tools). Dark-flagged via feature.agent-modes.enabled;
        # default seeded from the agent.default_mode setting on first use.
        self._agent_modes: dict[str, str] = {}
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
        self.register_tool_hook(after=self._artifact_hook)
        self._sessions = ChatSessionStore(
            self.db,
            prefix="agent_",
            session_extras={
                "provider": "TEXT NOT NULL DEFAULT ''",
                "model": "TEXT NOT NULL DEFAULT ''",
                # profiles.py — blank = "coding" (every session /agent/ creates).
                "profile": "TEXT NOT NULL DEFAULT ''",
                # A chat project (B2) and the MCP servers enabled for this chat (B5).
                "project_id": "TEXT NOT NULL DEFAULT ''",
                "connectors": "TEXT NOT NULL DEFAULT ''",
            },
            message_extras={
                "provider_kind": "TEXT NOT NULL DEFAULT 'anthropic'",
                # Display metadata beside the provider content (sessions.turn_display_marks):
                # what the user actually typed when the server prepended context to it,
                # and who authored a user-role message ("user" / "system").
                "display_text": "TEXT NOT NULL DEFAULT ''",
                "origin": "TEXT NOT NULL DEFAULT ''",
            },
            # Chat projects + conversation search (projects.py). The first boot
            # with search on indexes every stored message (measured 41 ms for
            # 1,580 messages), then each new message is indexed as it lands.
            projects=True,
            search=True,
            # Which artifacts a chat produced (artifacts.py) — the link only;
            # the artifacts live in viz.
            artifacts=True,
        )
        self._sessions.init_schema()

    async def _maybe_connect_inbound_mcp(self) -> None:
        """Dark-flagged: connect external MCP servers and add their tools to the
        registry so the agent loop can use them like native tools (gated by the
        same ToolConsentManager — proxy tools are ``permission="ask"``).

        Off by default (`[apps.agent] feature.mcp-inbound.enabled`) → the
        registry is byte-identical. Fail-soft: a bad server, a parse error, or a
        missing dep must never break agent boot.

        Servers are declared as `[[apps.agent.mcp_servers]]` rows, each carrying
        EITHER a `command` (stdio — we spawn it) or a `url` (HTTP — it is already
        running)::

            [[apps.agent.mcp_servers]]
            id = "files"
            command = "npx"
            args = ["-y", "@modelcontextprotocol/server-filesystem", "/data"]

            [[apps.agent.mcp_servers]]
            id = "velorn"
            url = "http://127.0.0.1:19790/mcp"

        An HTTP server we do not spawn is simply *absent* when it is not running
        — `connect_mcp_servers` drops it and the registry is unchanged. That is
        the normal state for a desktop app like Velorn, so it must stay silent
        rather than log an error on every boot.
        """
        try:
            # _connectors_enabled, not the bare flag: the routes refuse in
            # public mode and the BOOT path is where a process actually gets
            # spawned, so gating only the routes left the claim ("refused in
            # public mode") true of the UI and false of the execution.
            if not self._connectors_enabled():
                return
            # Config rows AND the runtime store (connectors.py, B5) — a server
            # the user added from the UI must come back on the next boot, or
            # "added" would mean "until the daemon restarts".
            rows = self._connector_rows()
            from emptyos.sdk.agent_tools.mcp_proxy import proxy_tools_for
            from emptyos.sdk.mcp_client import connect_mcp_servers, specs_from_config

            expanded = []
            for row in rows:
                env, miss_e = _connectors.expand_mapping(row.get("env"))
                headers, miss_h = _connectors.expand_mapping(row.get("headers"))
                if miss_e or miss_h:
                    # Say which name is missing. A server connected without its
                    # credential answers "unauthorized", which reads as a broken
                    # server rather than an unset environment variable.
                    self.log(
                        f"inbound MCP: {row.get('id')} skipped — environment "
                        f"variable(s) not set: {', '.join(sorted(set(miss_e + miss_h)))}",
                        level="warning",
                    )
                    continue
                expanded.append({**row, "env": env, "headers": headers})
            specs = specs_from_config(expanded)
            if not specs:
                return
            self._mcp_clients = await connect_mcp_servers(specs)
            added = 0
            for client in self._mcp_clients:
                for tool in proxy_tools_for(client):
                    self._tools[tool.name] = tool
                    added += 1
            if added:
                self.log(f"inbound MCP: {added} tool(s) from {len(self._mcp_clients)} server(s)")
        except Exception as e:
            try:
                self.log(f"inbound MCP wiring skipped: {type(e).__name__}: {e}")
            except Exception:
                pass

    async def teardown(self) -> None:
        """Stop any spawned inbound-MCP server subprocesses, then chain up."""
        for client in getattr(self, "_mcp_clients", []) or []:
            try:
                await client.stop()
            except Exception:
                pass
        await super().teardown()  # loader wraps teardown in try/except + logs

    # Session storage, archive, edit-stack, and per-session limits live on
    # SessionMixin (apps/agent/sessions.py).

    # ── Provider selection ───────────────────────────────────

    def _default_provider_name(self) -> str:
        """Provider for a NEW session (an existing one keeps its own).

        1. ``think.app.agent`` — the one knob: the page's model pill, the
           settings-panel field and ``eos chat``'s ``/model … save`` all write it.
           Before this, new sessions ignored it, so the pill only moved the
           app's side calls (orient, archive summaries), never the session.
        2. ``agent.default_provider`` — legacy key, still honoured when set.
        3. ``openai`` — the historical fallback.

        A stored value is used only when it names a provider this app's loop can
        drive. The pill lists every ``think`` provider (``codex`` included), and
        a session stored under a name the loop cannot drive would be silently
        re-routed to another model at its first turn while the pill still named
        the first one.
        """
        from emptyos.sdk.base_app_think import routing_settings

        # A locked build ([cloud] locked) ignores both saved choices, as think() does.
        settings = routing_settings(self.kernel)
        if settings:
            for key in ("think.app.agent", "agent.default_provider"):
                value = settings.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue  # unset, or a nested dict (think.app.agent.providers stored under it)
                try:
                    drivable = self._resolve_provider(value.strip(), strict=True) is not None
                except Exception:
                    drivable = False
                if drivable:
                    return value.strip()
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
            NativelyAgenticProvider,
            ToolCapableProvider,
        )

        think = self.kernel.capability("think")
        candidates: list = think.all_providers()

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

    async def _cloud_turn_allowed(self, provider, user_text: str) -> bool:
        """Cloud-consent check for one turn — the gate ``Capability.execute``
        runs for every other cloud call. Local providers always pass; with no
        consent manager (a bare kernel) cloud is allowed, as it is there."""
        if not getattr(provider, "is_cloud", False):
            return True
        manager = getattr(self.kernel, "cloud_consent", None)
        if manager is None:
            return True
        summary = f"Chat turn on {provider.name}: {user_text[:200]}"
        return await manager.ensure_consent(provider=provider.name, capability="think", data_summary=summary)

    def _runtime_info_block(self, provider, is_native: bool, **kw) -> str:
        return context.runtime_info_block(self, provider, is_native, **kw)

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

    async def _auto_skill_block(self, user_text: str, provider, is_native: bool) -> str:
        return await context.auto_skill_block(self, user_text, provider, is_native)

    # ── Behavioral modes (sticky /mode personas — feature.agent-modes.enabled) ──
    def _agent_modes_enabled(self) -> bool:
        return bool(self.app_config("feature.agent-modes.enabled", False))

    def _load_agent_modes(self) -> dict:
        return _modes.load_modes(self)

    def _resolve_mode_id(self, session_id: str) -> str:
        """Active mode for a session: explicit per-session choice, else the
        ``agent.default_mode`` setting. Empty string = no mode."""
        mid = self._agent_modes.get(session_id)
        if mid is not None:
            return mid
        try:
            s = self.service("settings")
            return (s.get("agent.default_mode") or "") if s else ""
        except Exception:
            return ""

    def _active_mode_block(self, session_id: str) -> str:
        """System-prompt persona for the session's active mode ('' if off/none)."""
        if not self._agent_modes_enabled():
            return ""
        mid = self._resolve_mode_id(session_id)
        if not mid:
            return ""
        mode = self._load_agent_modes().get(mid)
        return _modes.mode_block(mode) if mode else ""

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
    _connectors_enabled = _connectors._connectors_enabled
    _connector_store_path = _connectors._connector_store_path
    _load_connectors = _connectors._load_connectors
    _save_connectors = _connectors._save_connectors
    _connector_rows = _connectors._connector_rows
    _put_proposal = _connectors._put_proposal
    _take_proposal = _connectors._take_proposal
    _connect_connector = _connectors._connect_connector
    _disconnect_connector = _connectors._disconnect_connector
    _refused_connectors = _connectors._refused_connectors
    api_connectors = _connectors.api_connectors
    api_connector_add = _connectors.api_connector_add
    api_connector_remove = _connectors.api_connector_remove
    api_connector_connect = _connectors.api_connector_connect
    api_session_connectors = _connectors.api_session_connectors
    _artifact_hook = _artifacts._artifact_hook
    _artifact_exists = _artifacts._artifact_exists
    api_artifacts = _artifacts.api_artifacts
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
                    # A chat composer's extras (turn_inputs.py): attachment
                    # paths, "ground in my notes", and the per-message yes
                    # that lets them go to a cloud model.
                    extras = {
                        # Not truncated here: prepare_turn caps them and says
                        # which ones it refused.
                        "attachments": [str(p) for p in (data.get("attachments") or []) if p],
                        "vault_context": bool(data.get("vault_context")),
                        "cloud_ok": bool(data.get("cloud_ok")),
                    }
                    if not text and not extras["attachments"]:
                        continue
                    if session_id in self._turn_active:  # this socket's turn or another's
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": "previous turn still in progress",
                            }
                        )
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
                        await websocket.send_json(
                            {
                                "type": "agent:slash_result",
                                "session_id": session_id,
                                "cmd": "/context",
                                "text": "\n".join(ctx_lines),
                            }
                        )
                        continue

                    # /archive — summarise session and write to vault
                    if text.strip() == "/archive":
                        await websocket.send_json(
                            {
                                "type": "agent:status",
                                "session_id": session_id,
                                "text": "Summarising session…",
                            }
                        )
                        result = await self._archive_session(session_id)
                        if result.get("ok"):
                            vault_link = f" · [open]({result['url']})" if result.get("url") else ""
                            out = (
                                f"Session archived to vault.\n\n"
                                f"**path** `{result.get('note_path', '')}`{vault_link}"
                            )
                        else:
                            out = f"Archive failed: {result.get('error', 'unknown error')}"
                        await websocket.send_json(
                            {
                                "type": "agent:slash_result",
                                "session_id": session_id,
                                "cmd": "/archive",
                                "text": out,
                            }
                        )
                        continue

                    # Parity with cmd_chat: a user message starting with
                    # /<skill-name> loads SKILL.md and replaces `text` with the
                    # full playbook-prefixed prompt. No skill match → pass through.
                    typed = text  # what the user sees in history, whatever the model gets
                    expanded = self._expand_skill_slash(text)
                    if expanded:
                        await websocket.send_json(
                            {
                                "type": "agent:skill_loaded",
                                "session_id": session_id,
                                "name": text.split()[0][1:],
                            }
                        )
                        text = expanded
                    # Re-checked here, not only above: the skill branch awaits
                    # a send, and another socket may have claimed the session
                    # during it. Claim and task creation stay await-free.
                    task = self._claim_turn(
                        session_id,
                        lambda: self._run_ws_turn(session_id, text, bridge, websocket, typed=typed, **extras),
                    )
                    if task is None:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": "previous turn still in progress",
                            }
                        )
                        continue
                    turn_task = task

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
                        # Bind the decision to THIS socket's session. The request
                        # id rides `agent:permission_requested` over the event
                        # bus, which /ws broadcasts to every client — so without
                        # this any connected client could approve another
                        # session's tool call.
                        tool_consent.approve(
                            data.get("id", ""), scope=scope,
                            expected_session_id=session_id,
                        )

                elif msg_type == "deny_permission":
                    tool_consent = self.service("tool_consent")
                    if tool_consent:
                        tool_consent.deny(
                            data.get("id", ""), expected_session_id=session_id,
                        )

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
                    await websocket.send_json(
                        {
                            "type": "agent:plan_mode",
                            "session_id": session_id,
                            "on": self._plan_modes.get(session_id, False),
                        }
                    )

        except Exception:
            pass  # client disconnected
        finally:
            # Cancelling is enough: _run_ws_turn drops its own live-session
            # entry in a finally. Popping here would let a socket that is
            # merely closing orphan ANOTHER socket's running turn.
            if turn_task and not turn_task.done():
                turn_task.cancel()
            for u in unsubs:
                try:
                    u()
                except Exception:
                    pass

    def _claim_turn(self, session_id: str, make_coro) -> asyncio.Task | None:
        """Start a WS turn unless one is already running on this session.

        App-wide, not per socket: the portal and a quick-entry window on the
        same session would otherwise run two turns that interleave into one
        history. Synchronous on purpose — check, claim and ``create_task`` with
        no ``await`` between them is atomic on the event loop. The claim is
        released by a done-callback rather than a ``finally`` in the coroutine,
        because a task cancelled before its first step never enters its body.
        ``make_coro`` is a factory so a refused claim never creates a coroutine
        it would then have to leave un-awaited. ``spawn_background`` holds the
        task, logs a failure to syslog and cancels it on app teardown.

        Scope: one daemon process. ``eos chat`` runs its own turn loop in the
        CLI process against the same store, which this set cannot see.
        """
        if session_id in self._turn_active:
            return None
        self._turn_active.add(session_id)
        try:
            task = self.spawn_background(make_coro(), label=f"ws turn {session_id}")
        except BaseException:
            self._turn_active.discard(session_id)
            raise
        task.add_done_callback(lambda _t: self._release_turn(session_id))
        return task

    def _release_turn(self, session_id: str) -> None:
        """Done-callback for a claimed turn, on EVERY exit — normal, cancelled,
        or raised before ``_run_ws_turn``'s own cleanup ran. While the claim is
        held no other turn can start on this session, so the live-session entry
        still present here is this turn's; dropping it frees the AgentSession
        (and its whole message list) that a raised turn would otherwise leave."""
        self._live_sessions.pop(session_id, None)
        self._turn_active.discard(session_id)

    async def _orient_or_none(self, user_text: str, session_id: str, websocket):
        """The pre-turn orient pass with its status frames; ``None`` when it
        times out or fails (the turn then runs without it)."""
        await websocket.send_json(
            {"type": "agent:status", "session_id": session_id, "status": "Orienting…"}
        )
        try:
            return await asyncio.wait_for(self._orient(user_text, session_id), timeout=12.0)
        except TimeoutError:
            status = "Orient skipped (timeout)"
        except Exception as e:
            self.log_warn(f"orient failed: {e}")
            status = f"Orient skipped ({type(e).__name__})"
        await websocket.send_json({"type": "agent:status", "session_id": session_id, "status": status})
        return None

    async def _run_ws_turn(
        self, session_id: str, user_text: str, bridge, websocket, typed: str = "",
        attachments=None, vault_context: bool = False, cloud_ok: bool = False,
    ):
        record = self._get_session(session_id)
        if not record:
            await websocket.send_json({"type": "error", "message": "session not found"})
            return

        provider_name = record.get("provider") or self._default_provider_name()
        provider = self._resolve_provider(provider_name)
        if provider is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"no tool-capable provider available (tried {provider_name!r}). "
                    "Install anthropic SDK or configure a function-calling provider.",
                }
            )
            return

        is_native = self._is_native_provider(provider)
        provider_kind = "native" if is_native else provider.kind

        # Session profile (profiles.py): "coding" — /agent/'s sessions, blank in
        # the store — runs exactly as before; "chat" narrows tools and prompt and
        # skips the coding passes. A native provider runs its OWN tools past our
        # registry and consent gate, so it cannot honour a restricted profile.
        profile = profile_for(record.get("profile"))
        if profile is None:
            await websocket.send_json(
                {"type": "error", "message": f"unknown conversation profile {record.get('profile')!r}"}
            )
            return
        # User posture (public demo, hosted): the `coding` profile offers the
        # whole tool registry (Bash/Write/Edit/Python) = host code execution to a
        # non-operator. Force the tool-narrowed `chat` profile even for a session
        # that was created as coding. See docs/AUTH.md § Operator vs user.
        if profile.tools is None and not getattr(self.kernel.config, "web_is_operator", True):
            profile = profile_for("chat")
        if profile.tools is not None and is_native:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"{provider.name} runs its own tools, so it cannot drive a "
                    f"{profile.name} conversation — pick another model.",
                }
            )
            return
        # A restricted conversation reads the vault; a cloud model gets it only
        # through the same consent gate Capability.execute applies (CLAUDE.md
        # rules 18/19) — run_turn calls the provider directly and would skip it.
        if profile.tools is not None and not await self._cloud_turn_allowed(provider, user_text):
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"{provider.name} is a cloud model and was not approved for this "
                    "conversation. Approve it when asked, or pick a local model.",
                }
            )
            return
        tools = select_session_tools(
            self._tools, profile, record.get("connectors"), narrowed=self._narrowed_tools
        )
        # Attachments + vault context (turn_inputs.py). None = refused, and the
        # socket was told why (cloud needs a yes; the model can't read images).
        inputs = await self._prepare_turn_inputs(
            provider, is_native, user_text, attachments, vault_context, cloud_ok, websocket, session_id
        )
        if inputs is None:
            return
        # What the user wrote, kept beside what the model gets — and what the
        # transcript and the chat search read. A send that is only attachments
        # says so, rather than indexing a whole extracted PDF as their words.
        typed = typed or user_text or inputs["attached_label"]

        # Build the in-memory session with existing messages. Stored images are
        # path references (eos_image) — hydrate them into this provider's parts.
        sess = AgentSession(
            id=session_id,
            messages=hydrate_messages(
                self._load_provider_messages(session_id), self.kernel.config.notes_path, provider_kind,
                # An attachment approved for one message is not re-sent to a
                # cloud model by every later turn (CLAUDE.md rule 19) — unless
                # it was approved for this same model.
                withhold=bool(getattr(provider, "is_cloud", False)) and not cloud_ok,
                provider_name=provider.name,
                # A model that cannot read images gets a note, not a 400.
                images=provider_reads_images(provider),
            ),
            provider_kind=provider_kind,
        )
        self._live_sessions[session_id] = sess

        if profile.tools is None:
            system = record.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
            system = system + "\n\n" + self._runtime_info_block(provider, is_native)
        else:
            base = getattr(PROMPTS, profile.system_prompt_key) if profile.system_prompt_key else DEFAULT_SYSTEM_PROMPT
            system = record.get("system_prompt") or base
            system = system + "\n\n" + self._runtime_info_block(
                provider, is_native, tool_count=len(tools), coding=False
            )
        # The session's chat project (projects.py): the user's standing
        # instructions, in the system prompt so they hold for every turn.
        # Built once per turn like the rest of `system` (prefix-cache rule 2).
        project_block = self._project_block(record)
        if project_block:
            system = system + "\n\n" + project_block
        scaffold = self._app_scaffold_block(user_text, is_native) if profile.scaffold else ""
        if scaffold:
            system = system + "\n\n" + scaffold
        # Sticky behavioral mode persona (dark flag) — persists across the session.
        _mode_block = self._active_mode_block(session_id)
        if _mode_block:
            system = system + "\n\n" + _mode_block
        # Deterministic skill auto-trigger (dark flag) — skip if a skill was already
        # loaded manually this turn (user typed /<skill>, which expands inline).
        if profile.auto_skill and context.SKILL_PLAYBOOK_MARKER not in user_text:
            try:
                auto_skill = await self._auto_skill_block(user_text, provider, is_native)
            except Exception:
                auto_skill = ""
            if auto_skill:
                system = system + "\n\n" + auto_skill
        if profile.plan_mode and self._plan_modes.get(session_id, False):
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
        orient_text = inputs["text"]
        orient_plan = None
        if profile.orient:
            orient_plan = await self._orient_or_none(user_text, session_id, websocket)
        if orient_plan:
            orient_block_text = self._orient_block(orient_plan)
            if is_native:
                system = system + "\n\n" + orient_block_text
            else:
                orient_text = orient_block_text + "\n\n" + user_text
            await websocket.send_json(
                {
                    "type": "agent:orient",
                    "session_id": session_id,
                    "plan": orient_plan,
                }
            )

        # Episodic memory (dark flag): on the FIRST turn of a fresh session,
        # surface relevant past sessions so the agent starts with memory. Kept
        # out of the orient input above so it doesn't skew classification.
        if pre_len == 0 and profile.episodic:
            try:
                _ep_block = await self._episodic_recall_block(session_id, user_text)
            except Exception:
                _ep_block = ""
            if _ep_block:
                if is_native:
                    system = system + "\n\n" + _ep_block
                else:
                    orient_text = _ep_block + "\n\n" + orient_text

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
                    # Images ride as provider-native parts beside the text.
                    user_content=build_user_content(provider_kind, orient_text, inputs["image_urls"])
                    if inputs["image_urls"] else None,
                    provider=provider,
                    tools=tools,
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
        except TimeoutError:
            self.log_warn(f"turn timed out after {turn_timeout}s (session {session_id})")
            try:
                await websocket.send_json(
                    {
                        "type": "agent:error",
                        "session_id": session_id,
                        "error": f"Turn timed out after {turn_timeout:.0f}s — the provider or a tool stopped responding. Try /cancel and resend, or switch backend.",
                    }
                )
            except Exception:
                pass
        except Exception as e:
            self.log_error(f"turn failed: {type(e).__name__}: {e}")
            try:
                await websocket.send_json(
                    {
                        "type": "agent:error",
                        "session_id": session_id,
                        "error": str(e),
                    }
                )
            except Exception:
                pass

        # Persist new messages (full dict — preserves OpenAI tool_calls / tool_call_id).
        # The opening user message keeps its images as path references, not
        # megabytes of base64 in the store (hydrated again on the next load).
        new_messages = sess.messages[pre_len:]
        if inputs["vault_derived"] and new_messages:
            # EVERY message of the turn, not just the user's: the reply quotes
            # the file and the tool results carry it, so a later unapproved
            # cloud turn must not replay any of them.
            approved = provider.name if (cloud_ok and getattr(provider, "is_cloud", False)) else ""
            first = new_messages[0]
            new_messages = [
                mark_vault_derived(
                    {**first, "content": dehydrate_content(first.get("content"), inputs["image_paths"])},
                    typed=typed, approved_for=approved, names=inputs["names"],
                ),
                *(mark_vault_derived(m, approved_for=approved) for m in new_messages[1:]),
            ]
        self._persist_turn(session_id, new_messages, provider_kind, typed)

        # Episodic memory (dark flag): record a cheap, no-LLM digest of the
        # session's current state so future sessions can recall it. Pass the raw
        # user_text so the recall/orient blocks don't leak into the episode task.
        self._remember_turn_episode(session_id, sess, user_text)

        self._live_sessions.pop(session_id, None)

    # ── HTTP routes (extracted to routes.py) ──────────────────
    # ── A turn's attachments + vault context (extracted to turn_inputs.py) ──
    _prepare_turn_inputs = _turn_inputs._prepare_turn_inputs
    _vault_context_block = _turn_inputs._vault_context_block

    # ── Chat projects + search (extracted to projects.py) ──
    api_list_projects = _projects.api_list_projects
    api_create_project = _projects.api_create_project
    api_update_project = _projects.api_update_project
    api_delete_project = _projects.api_delete_project
    api_search = _projects.api_search
    create_chat_project = _projects.create_chat_project
    assign_chat_project = _projects.assign_chat_project
    _project_block = _projects._project_block

    api_list_sessions = _routes.api_list_sessions
    api_get_session = _routes.api_get_session
    api_create_session = _routes.api_create_session
    api_providers = _routes.api_providers
    api_delete_session = _routes.api_delete_session
    api_update_session = _routes.api_update_session
    api_archive_session = _routes.api_archive_session
    api_session_tasks = _routes.api_session_tasks
    api_fork_session = _routes.api_fork_session
    api_revert = _routes.api_revert
    api_edit_stack = _routes.api_edit_stack
    api_mcp_tool_call = _routes.api_mcp_tool_call
    api_tool_audit = _routes.api_tool_audit
    api_mcp_tools = _routes.api_mcp_tools
    api_mcp_foundry_verbs = _routes.api_mcp_foundry_verbs
    api_verbs_sweep = _routes.api_verbs_sweep
    api_foundry_status = _routes.api_foundry_status
    api_foundry_grant = _routes.api_foundry_grant
    api_foundry_revoke = _routes.api_foundry_revoke
    api_autopilot_review = _routes.api_autopilot_review
    panel_autopilot_review = _routes.panel_autopilot_review
    api_list_tools = _routes.api_list_tools
    api_skills = _routes.api_skills
    api_slash_commands = _routes.api_slash_commands
    api_approve_permission = _routes.api_approve_permission
    api_deny_permission = _routes.api_deny_permission
    api_list_permissions = _routes.api_list_permissions
    api_status = _routes.api_status

    # ── CLI (extracted to repl.py) ────────────────────────────
    cmd_chat = _repl.cmd_chat
    cmd_code = _repl.cmd_code
