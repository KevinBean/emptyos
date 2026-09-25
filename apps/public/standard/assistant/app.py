"""Assistant — multi-session AI chat with vault integration.

Absorbed from AI Phone Agent. Features:
- Multi-session management (create/rename/delete)
- WebSocket streaming with real-time token display
- Smart auto-routing (vault queries → local AI, else → cloud AI)
- 13+ slash commands routing to EmptyOS apps via call_app()
- Vault context injection (search + read top results)
- Custom personas + persistent memories

This file is the spine — see helper modules for the bulk of the logic.
Concerns split out:
  - slash.py          slash command discovery + dispatch
  - context.py        chat messages + system prompt + vault retrieval
  - tools.py          read-only tool-use loop
  - research_ws.py    /research WebSocket streaming
  - chat_ws.py        main /ws/{session_id} handler
  - attachments.py    vault file picker + image fetch + multipart upload
  - api_sessions.py   REST CRUD for sessions
  - api_chat.py       legacy /api/chat + /api/compare + listings
  - voice.py          TTS / STT shims + PWA manifest + `eos ask` CLI
  - sessions.py       SQLite session store + export + archive (mixin)
  - prompts.py        SYSTEM_PROMPT + TOOLS_SYSTEM_PROMPT + limits
  - files.py          attached-file extraction
  - vision.py         vault image → data URL resolution
  - research.py       /research engine
  - passages.py       passage-level citation anchors + the passage reader
"""

from __future__ import annotations

import asyncio

from emptyos.sdk import BaseApp

from . import api_chat as _api_chat
from . import api_sessions as _api_sessions
from . import attachments as _attachments
from . import chat_ws as _chat_ws
from . import context as _context
from . import passages as _passages
from . import reconcile as _reconcile_mod
from . import research_ws as _research_ws
from . import slash as _slash
from . import tools as _tools
from . import voice as _voice
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

    # ── Provider Routing (kept on the spine — small + shared) ────────

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

    # ── Slash (extracted to slash.py) ────────────────────────────────
    _discover_slash_commands = _slash._discover_slash_commands
    _handle_slash            = _slash._handle_slash

    # ── Context (extracted to context.py) ────────────────────────────
    _build_chat_messages   = _context._build_chat_messages
    _build_user_state      = _context._build_user_state
    _build_system          = _context._build_system
    _should_skip_retrieval = _context._should_skip_retrieval
    _build_context         = _context._build_context
    _collect_context       = _context._collect_context
    _retrieval_query       = _context._retrieval_query
    _scoped_context        = _context._scoped_context
    _full_answer           = _context._full_answer
    _reconcile             = _context._reconcile
    _note_date             = _context._note_date
    _provenance            = _context._provenance
    _provenance_items      = _context._provenance_items

    # ── Passage citations (extracted to passages.py) ─────────────────
    _passage_citations_enabled = _passages._passage_citations_enabled
    _passage_for               = _passages._passage_for
    api_passage                = _passages.api_passage

    # ── Tools (extracted to tools.py) ────────────────────────────────
    _use_tools_default     = _tools._use_tools_default
    _resolve_tool_provider = _tools._resolve_tool_provider
    _chat_with_tools       = _tools._chat_with_tools

    # ── Research WS (extracted to research_ws.py) ────────────────────
    _run_research_ws = _research_ws._run_research_ws

    # ── Chat WS (extracted to chat_ws.py) ────────────────────────────
    ws_chat = _chat_ws.ws_chat

    # ── Attachments (extracted to attachments.py) ────────────────────
    api_vault_files = _attachments.api_vault_files
    api_image       = _attachments.api_image
    api_upload      = _attachments.api_upload

    # ── REST sessions (extracted to api_sessions.py) ─────────────────
    api_list_sessions  = _api_sessions.api_list_sessions
    api_create_session = _api_sessions.api_create_session
    api_get_session    = _api_sessions.api_get_session
    get_session        = _api_sessions.get_session
    list_recent_messages = _api_sessions.list_recent_messages
    api_update_session = _api_sessions.api_update_session
    api_pin_project    = _api_sessions.api_pin_project
    api_unpin_project  = _api_sessions.api_unpin_project
    api_delete_session = _api_sessions.api_delete_session
    api_export_session = _api_sessions.api_export_session
    api_archive        = _api_sessions.api_archive

    # ── Legacy REST chat (extracted to api_chat.py) ──────────────────
    api_chat           = _api_chat.api_chat
    api_providers      = _api_chat.api_providers
    api_slash_commands = _api_chat.api_slash_commands
    api_compare        = _api_chat.api_compare
    api_dispatch       = _api_chat.api_dispatch
    api_browser_session_snapshots = _api_chat.api_browser_session_snapshots
    api_propose_kb_note = _api_chat.api_propose_kb_note

    # ── Durable ripple (extracted to reconcile.py) ───────────────────
    api_reconcile_propose   = _reconcile_mod.api_reconcile_propose
    api_reconcile_apply     = _reconcile_mod.api_reconcile_apply
    api_reconcile_reject    = _reconcile_mod.api_reconcile_reject
    _reconcile_store        = _reconcile_mod._reconcile_store

    # ── Voice + PWA + CLI (extracted to voice.py) ────────────────────
    api_tts      = _voice.api_tts
    api_audio    = _voice.api_audio
    api_stt      = _voice.api_stt
    api_manifest = _voice.api_manifest
    cmd_ask      = _voice.cmd_ask
