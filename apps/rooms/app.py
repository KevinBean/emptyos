"""Rooms — single-agent and (in v2) multi-participant conversation rooms.

Each room has a system prompt, optional knowledge files, and persistent
history. Renamed from `gpts` 2026-05-09; the v1 surface is structurally
identical (one agent per room = a 1:1 chat), with multi-participant +
CLI-participant support landing in subsequent phases.

Today's surface: named agents with custom system prompts, attached vault
knowledge files for context, and chat with persistent per-agent history.

Rooms is the universal AI service layer: page-assistant, assistant UI,
and other apps all consume room agents. Each agent has:
  - Persona (system_prompt)
  - Knowledge (files + dirs, cached 5min)
  - Dynamic context (page metrics, live data — injected per request)
  - Client actions (described to LLM, output as [BUTTON:] for browser)
  - Server actions (described to LLM, output as [DO:] → call_app() on backend)
  - History (per-agent JSON, last 200 msgs)
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

# APScheduler is a hard kernel dependency (pyproject), so the late-import
# defensiveness wasn't pulling its weight — hoisted for clarity. Used by
# set_schedule (cron-expression validation pre-save) and the BaseApp
# add_cron_job helper.
from apscheduler.triggers.cron import CronTrigger

from emptyos.sdk import BaseApp, cli_command, ndjson_response, web_route
from emptyos.sdk.sandbox import SandboxedWrite, StaleSandbox, load_sandbox
from emptyos.sdk.utils import parse_llm_json

from . import agents as _agents
from . import chat as _chat_mod
from . import participants as _participants
from . import pending as _pending
from . import rooms_core as _rooms_core
from . import scheduling as _scheduling
from . import snippets as _snippets
from . import visits as _visits



class RoomsApp(BaseApp):

    async def setup(self):
        await super().setup()
        self._migrate_legacy_gpts_data()
        self._sync_general_assistant_actions()
        self._register_reminder_cron()
        self._register_room_schedules()

    # ── Scheduling (extracted to scheduling.py) ──
    _migrate_legacy_gpts_data       = _scheduling._migrate_legacy_gpts_data
    _sync_general_assistant_actions = _scheduling._sync_general_assistant_actions
    _reminders_path                 = _scheduling._reminders_path
    _load_reminders                 = _scheduling._load_reminders
    _save_reminders                 = _scheduling._save_reminders
    add_reminder                    = _scheduling.add_reminder
    list_reminders                  = _scheduling.list_reminders
    remove_reminder                 = _scheduling.remove_reminder
    _fire_due_reminders             = _scheduling._fire_due_reminders
    _register_reminder_cron         = _scheduling._register_reminder_cron
    api_add_reminder                = _scheduling.api_add_reminder
    api_list_reminders              = _scheduling.api_list_reminders
    api_remove_reminder             = _scheduling.api_remove_reminder
    _schedule_job_id                = _scheduling._schedule_job_id
    _register_room_schedules        = _scheduling._register_room_schedules
    SCHEDULE_SYSTEM_SUFFIX          = _scheduling.SCHEDULE_SYSTEM_SUFFIX
    _fire_room_schedule             = _scheduling._fire_room_schedule
    set_schedule                    = _scheduling.set_schedule
    clear_schedule                  = _scheduling.clear_schedule
    api_set_schedule                = _scheduling.api_set_schedule
    api_clear_schedule              = _scheduling.api_clear_schedule
    api_fire_schedule_now           = _scheduling.api_fire_schedule_now

    # ── Visits (extracted to visits.py) ──
    _visits_path   = _visits._visits_path
    _load_visits   = _visits._load_visits
    _save_visits   = _visits._save_visits
    mark_visited   = _visits.mark_visited
    get_visits     = _visits.get_visits
    get_unread     = _visits.get_unread
    api_visit_room = _visits.api_visit_room
    api_visits     = _visits.api_visits
    api_unread     = _visits.api_unread

    # ── Snippets (extracted to snippets.py) ──
    _snippets_path     = _snippets._snippets_path
    _load_snippets     = _snippets._load_snippets
    _save_snippets     = _snippets._save_snippets
    add_snippet        = _snippets.add_snippet
    remove_snippet     = _snippets.remove_snippet
    list_snippets      = _snippets.list_snippets
    get_snippet        = _snippets.get_snippet
    api_list_snippets  = _snippets.api_list_snippets
    api_get_snippet    = _snippets.api_get_snippet
    api_add_snippet    = _snippets.api_add_snippet
    api_remove_snippet = _snippets.api_remove_snippet

    # ── Pending (extracted to pending.py) ──
    _actions_log_path         = _pending._actions_log_path
    _pending_dir              = _pending._pending_dir
    _pending_path             = _pending._pending_path
    _sandbox_root             = _pending._sandbox_root
    _prepare_write_note       = _pending._prepare_write_note
    _save_pending             = _pending._save_pending
    _load_pending             = _pending._load_pending
    _lookup_inverse           = _pending._lookup_inverse
    _method_signature         = _pending._method_signature
    _execute_server_actions   = _pending._execute_server_actions
    _summarize_server_actions = _pending._summarize_server_actions
    _gate_server_actions      = _pending._gate_server_actions
    list_pending              = _pending.list_pending
    apply_pending             = _pending.apply_pending
    reject_pending            = _pending.reject_pending
    api_room_pending          = _pending.api_room_pending
    api_global_pending        = _pending.api_global_pending
    api_apply_pending         = _pending.api_apply_pending
    api_reject_pending        = _pending.api_reject_pending
    api_undo                  = _pending.api_undo

    # ── Participants (extracted to participants.py) ──
    _normalize_participants = _participants._normalize_participants
    _room_kind              = _participants._room_kind
    _resolve_responder_id   = _participants._resolve_responder_id
    _resolve_responder      = _participants._resolve_responder
    _new_room_id            = _participants._new_room_id
    _build_cli_prompt       = _participants._build_cli_prompt
    _build_cli_system       = _participants._build_cli_system
    _dispatch_cli_turn      = _participants._dispatch_cli_turn
    register_persona        = _participants.register_persona
    unregister_persona      = _participants.unregister_persona
    add_participant         = _participants.add_participant
    remove_participant      = _participants.remove_participant
    api_add_participant     = _participants.api_add_participant
    api_remove_participant  = _participants.api_remove_participant

    # ── Agents (extracted to agents.py) ──
    _agents_dir         = _agents._agents_dir
    _history_dir        = _agents._history_dir
    _agent_path         = _agents._agent_path
    _history_path       = _agents._history_path
    _load_agent         = _agents._load_agent
    _save_agent         = _agents._save_agent
    _list_agents        = _agents._list_agents
    _find_agent_by_name = _agents._find_agent_by_name
    _load_history       = _agents._load_history
    _save_history       = _agents._save_history
    get_agent           = _agents.get_agent
    list_agents         = _agents.list_agents
    save_agent          = _agents.save_agent
    has_agent           = _agents.has_agent
    api_list_agents     = _agents.api_list_agents
    api_get_agent       = _agents.api_get_agent
    api_create_agent    = _agents.api_create_agent
    api_update_agent    = _agents.api_update_agent
    api_delete_agent    = _agents.api_delete_agent
    api_get_history     = _agents.api_get_history
    api_clear_history   = _agents.api_clear_history

    # ── Chat (extracted to chat.py) ──
    chat                    = _chat_mod.chat
    _build_system           = _chat_mod._build_system
    _build_prompt           = _chat_mod._build_prompt
    _build_prompt_async     = _chat_mod._build_prompt_async
    _assemble_prompt        = _chat_mod._assemble_prompt
    _chat                   = _chat_mod._chat
    _vault_log_chat         = _chat_mod._vault_log_chat
    _action_result_links    = _chat_mod._action_result_links
    api_chat                = _chat_mod.api_chat
    api_chat_stream         = _chat_mod.api_chat_stream
    api_do                  = _chat_mod.api_do
    api_debug_system_prompt = _chat_mod.api_debug_system_prompt

    # ── Rooms Core (extracted to rooms_core.py) ──
    _WIKILINK_RE         = _rooms_core._WIKILINK_RE
    _MAX_REF_BYTES       = _rooms_core._MAX_REF_BYTES
    _extract_wikilinks   = _rooms_core._extract_wikilinks
    _resolve_wikilinks   = _rooms_core._resolve_wikilinks
    api_vault_search     = _rooms_core.api_vault_search
    api_vault_export     = _rooms_core.api_vault_export
    _STOPWORDS           = _rooms_core._STOPWORDS
    suggest_agents       = _rooms_core.suggest_agents
    api_suggest_agents   = _rooms_core.api_suggest_agents
    search_messages      = _rooms_core.search_messages
    api_search           = _rooms_core.api_search
    panel_pending_count  = _rooms_core.panel_pending_count
    panel_recent_rooms   = _rooms_core.panel_recent_rooms
    voice_list_rooms     = _rooms_core.voice_list_rooms
    voice_open_room      = _rooms_core.voice_open_room
    list_rooms           = _rooms_core.list_rooms
    archive_room         = _rooms_core.archive_room
    unarchive_room       = _rooms_core.unarchive_room
    export_room          = _rooms_core.export_room
    inspect_context      = _rooms_core.inspect_context
    MAX_MEMORY_ENTRIES   = _rooms_core.MAX_MEMORY_ENTRIES
    add_memory           = _rooms_core.add_memory
    remove_memory        = _rooms_core.remove_memory
    list_memory          = _rooms_core.list_memory
    _memory_block        = _rooms_core._memory_block
    add_knowledge        = _rooms_core.add_knowledge
    remove_knowledge     = _rooms_core.remove_knowledge
    pin_message          = _rooms_core.pin_message
    unpin_message        = _rooms_core.unpin_message
    CATCH_UP_SYSTEM      = _rooms_core.CATCH_UP_SYSTEM
    catch_me_up          = _rooms_core.catch_me_up
    DISTILL_SYSTEM       = _rooms_core.DISTILL_SYSTEM
    distill_room         = _rooms_core.distill_room
    create_room          = _rooms_core.create_room
    attach_task          = _rooms_core.attach_task
    tasks_for_room       = _rooms_core.tasks_for_room
    cmd_rooms            = _rooms_core.cmd_rooms
    api_list_rooms       = _rooms_core.api_list_rooms
    api_create_room      = _rooms_core.api_create_room
    api_archive_room     = _rooms_core.api_archive_room
    api_unarchive_room   = _rooms_core.api_unarchive_room
    api_export_room      = _rooms_core.api_export_room
    api_distill_room     = _rooms_core.api_distill_room
    api_inspect_context  = _rooms_core.api_inspect_context
    api_catch_up         = _rooms_core.api_catch_up
    api_list_memory      = _rooms_core.api_list_memory
    api_add_memory       = _rooms_core.api_add_memory
    api_remove_memory    = _rooms_core.api_remove_memory
    api_add_knowledge    = _rooms_core.api_add_knowledge
    api_remove_knowledge = _rooms_core.api_remove_knowledge
    api_pin_message      = _rooms_core.api_pin_message
    api_unpin_message    = _rooms_core.api_unpin_message
    api_room_tasks       = _rooms_core.api_room_tasks
    api_attach_task      = _rooms_core.api_attach_task
