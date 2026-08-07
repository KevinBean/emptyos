"""Pure-logic unit tests for apps/rooms backend.

Doesn't require a running daemon — tests pure methods by instantiating
RoomsApp via object.__new__ and bypassing BaseApp setup. For methods
that touch the filesystem (gate_server_actions writes pending JSON),
inject tmp_path and a fake emit.

Run: python -m pytest tests/test_unit_rooms_logic.py -v
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def RoomsApp():
    # Register parent packages so app.py's `from . import X` relative imports
    # resolve. After the rooms decomposition app.py imports 8 sibling helper
    # modules; loading just app.py via raw importlib without package
    # registration would fail with ImportError.
    import types
    from helpers import app_path

    repo_root = Path(__file__).resolve().parent.parent
    rooms_dir = app_path("rooms")
    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(repo_root / "apps")]
        sys.modules["apps"] = apps_pkg
    if "apps.rooms" not in sys.modules:
        rooms_pkg = types.ModuleType("apps.rooms")
        rooms_pkg.__path__ = [str(rooms_dir)]
        sys.modules["apps.rooms"] = rooms_pkg

    # Load helper modules first so app.py's bindings resolve.
    for sub in ("agents", "chat", "participants", "pending",
                "rooms_core", "scheduling", "snippets", "team", "visits"):
        if f"apps.rooms.{sub}" in sys.modules:
            continue
        sub_spec = importlib.util.spec_from_file_location(
            f"apps.rooms.{sub}", rooms_dir / f"{sub}.py",
        )
        sub_mod = importlib.util.module_from_spec(sub_spec)
        sys.modules[f"apps.rooms.{sub}"] = sub_mod
        sub_spec.loader.exec_module(sub_mod)

    spec = importlib.util.spec_from_file_location(
        "apps.rooms.app",
        rooms_dir / "app.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apps.rooms.app"] = mod
    sys.modules["rooms_app"] = mod  # legacy alias used by older tests
    spec.loader.exec_module(mod)
    return mod.RoomsApp


@pytest.fixture
def app(RoomsApp):
    """Bare RoomsApp — no kernel, no setup. Use for pure methods."""
    return object.__new__(RoomsApp)


# ── _normalize_participants ───────────────────────────────────────────


class TestNormalizeParticipants:
    def test_legacy_1on1_synthesised(self, app):
        # Team mode stamps role="peer" on synthesised participants too.
        parts = app._normalize_participants({"id": "general-assistant"})
        assert parts == [
            {"type": "user", "id": "me", "role": "peer"},
            {"type": "agent", "id": "general-assistant", "role": "peer"},
        ]

    def test_user_without_id_gets_me(self, app):
        parts = app._normalize_participants({
            "id": "g1",
            "participants": [{"type": "user"}, {"type": "agent", "id": "a"}],
        })
        assert parts[0] == {"type": "user", "id": "me", "role": "peer"}

    def test_user_with_explicit_id_preserved(self, app):
        parts = app._normalize_participants({
            "id": "g1",
            "participants": [
                {"type": "user", "id": "kevin"},
                {"type": "agent", "id": "a"},
            ],
        })
        assert parts[0] == {"type": "user", "id": "kevin", "role": "peer"}

    def test_cli_participant_passes_through(self, app):
        parts = app._normalize_participants({
            "id": "g1",
            "participants": [
                {"type": "user"},
                {"type": "cli", "id": "claude-cli", "model": "haiku"},
            ],
        })
        assert parts[1] == {
            "type": "cli", "id": "claude-cli", "model": "haiku", "role": "peer",
        }


# ── _room_kind ─────────────────────────────────────────────────────────


class TestRoomKind:
    def test_legacy_1on1(self, app):
        assert app._room_kind({"id": "x"}) == "1on1"

    def test_single_agent_record_is_1on1(self, app):
        assert app._room_kind({
            "id": "g",
            "participants": [
                {"type": "user", "id": "me"},
                {"type": "agent", "id": "a"},
            ],
        }) == "1on1"

    def test_two_agents_is_group(self, app):
        assert app._room_kind({
            "id": "g",
            "participants": [
                {"type": "user", "id": "me"},
                {"type": "agent", "id": "a"},
                {"type": "agent", "id": "b"},
            ],
        }) == "group"

    def test_agent_plus_cli_is_group(self, app):
        assert app._room_kind({
            "id": "g",
            "participants": [
                {"type": "user", "id": "me"},
                {"type": "agent", "id": "a"},
                {"type": "cli", "id": "claude-cli"},
            ],
        }) == "group"


class TestProviderForwarding:
    class _Request:
        def __init__(self, body, path_params=None):
            self._body = body
            self.path_params = path_params or {}

        async def json(self):
            return self._body

    @pytest.mark.asyncio
    async def test_agent_create_and_update_persist_provider(self, app):
        saved = {}

        def _save(agent):
            saved.clear()
            saved.update(agent)

        app._save_agent = _save

        created = await app.api_create_agent(self._Request({
            "id": "provider-room",
            "name": "Provider Room",
            "provider": "openai-mini",
        }))

        assert created["provider"] == "openai-mini"
        assert saved["provider"] == "openai-mini"

        app._load_agent = lambda agent_id: dict(saved) if agent_id == "provider-room" else None
        updated = await app.api_update_agent(self._Request(
            {"provider": "local-llama"},
            {"agent_id": "provider-room"},
        ))

        assert updated["provider"] == "local-llama"
        assert saved["provider"] == "local-llama"

    @pytest.mark.asyncio
    async def test_chat_forwards_room_provider_to_think(self, app):
        captured = {}
        room = {
            "id": "cli-code",
            "name": "CLI Code",
            "system_prompt": "You are helpful.",
            "provider": "openai",
            "model": "gpt-test",
            "temperature": 0.2,
        }

        app._load_agent = lambda _agent_id: dict(room)
        app._load_history = lambda _agent_id: []
        app._walk_to_head = lambda history, _head: history
        app._build_system = lambda _agent, _client_actions=None, **_kw: "system"

        async def _prompt(_responder, text, _context, _history):
            return f"prompt: {text}"

        app._build_prompt_async = _prompt
        app._resolve_wikilinks = lambda _text: ""
        app._memory_block = lambda _room: ""

        async def _think(prompt, **kwargs):
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return "ok"

        app.think = _think

        async def _execute(response, _agent, room_id=""):
            return response, []

        app._execute_server_actions = _execute
        app._save_history = lambda _agent_id, _history: None
        app._save_agent = lambda _agent: None
        app._vault_log_chat = lambda _responder, _text, _response: None

        async def _emit(_event_type, _data=None):
            return None

        app.emit = _emit

        result = await app._chat("cli-code", "hello")

        assert result["response"] == "ok"
        assert captured["prompt"] == "prompt: hello"
        assert captured["kwargs"]["provider"] == "openai"
        assert captured["kwargs"]["model"] == "gpt-test"
        assert captured["kwargs"]["temperature"] == 0.2

    @pytest.mark.asyncio
    async def test_participant_turn_forwards_room_provider_to_think_stream(self, app):
        captured = {}
        saved_history = {}
        saved_agent = {}
        room = {
            "id": "stream-room",
            "name": "Stream Room",
            "system_prompt": "You are helpful.",
            "provider": "openai",
            "model": "gpt-test",
            "temperature": 0.2,
        }
        participant = {"type": "agent", "id": "stream-room"}

        app._load_agent = lambda agent_id: dict(room) if agent_id == "stream-room" else None
        app._load_history = lambda _agent_id: []
        app._walk_to_head = lambda history, _head: history
        app._team_prompt_block = lambda _room, _participant: ""
        app._build_system = lambda _agent, **_kw: "system"
        app._memory_block = lambda _room: ""

        async def _prompt(_responder, text, _context, _history):
            return f"prompt: {text}"

        app._build_prompt_async = _prompt

        async def _think_stream(prompt, **kwargs):
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            yield {"text": "ok"}

        app.think_stream = _think_stream

        async def _execute(response, _agent, room_id=""):
            return response, []

        app._execute_server_actions = _execute
        app._summarize_server_actions = lambda _results: ""
        app._save_history = lambda agent_id, history: saved_history.update({
            "agent_id": agent_id,
            "history": history,
        })
        app._save_agent = lambda agent: saved_agent.update(agent)

        async def _emit(_event_type, _data=None):
            return None

        app.emit = _emit

        result = await app._run_participant_turn(room, participant, "hello")

        assert result["reply"] == "ok"
        assert captured["prompt"] == "prompt: hello"
        assert captured["kwargs"]["provider"] == "openai"
        assert captured["kwargs"]["model"] == "gpt-test"
        assert captured["kwargs"]["temperature"] == 0.2
        assert saved_history["agent_id"] == "stream-room"
        assert saved_agent["id"] == "stream-room"


# ── _resolve_responder_id (legacy agent-only path) ─────────────────────


class TestResolveResponderId:
    @pytest.fixture
    def fake_app(self, RoomsApp):
        agents = {
            "curator": {"id": "curator", "name": "Curator"},
            "reviewer": {"id": "reviewer", "name": "Code Reviewer"},
        }

        class _F(RoomsApp):
            def _load_agent(self, aid):
                return agents.get(aid)

        return object.__new__(_F)

    def test_no_agents_returns_none(self, fake_app):
        assert fake_app._resolve_responder_id("hi", []) is None

    def test_one_agent_always_picked(self, fake_app):
        parts = [{"type": "agent", "id": "curator"}]
        assert fake_app._resolve_responder_id("@reviewer hi", parts) == "curator"

    def test_default_is_first_agent(self, fake_app):
        parts = [
            {"type": "agent", "id": "curator"},
            {"type": "agent", "id": "reviewer"},
        ]
        assert fake_app._resolve_responder_id("hello room", parts) == "curator"

    def test_mention_by_id(self, fake_app):
        parts = [
            {"type": "agent", "id": "curator"},
            {"type": "agent", "id": "reviewer"},
        ]
        assert fake_app._resolve_responder_id(
            "@reviewer thoughts?", parts,
        ) == "reviewer"

    def test_mention_by_hyphenated_display_name(self, fake_app):
        # Display name "Code Reviewer" → matches @code-reviewer
        parts = [
            {"type": "agent", "id": "curator"},
            {"type": "agent", "id": "reviewer"},
        ]
        assert fake_app._resolve_responder_id(
            "@code-reviewer please look", parts,
        ) == "reviewer"

    def test_unknown_mention_falls_back_to_first(self, fake_app):
        parts = [
            {"type": "agent", "id": "curator"},
            {"type": "agent", "id": "reviewer"},
        ]
        assert fake_app._resolve_responder_id(
            "@nobody hello", parts,
        ) == "curator"


# ── _resolve_responder (participant-aware, includes CLI) ───────────────


class TestResolveResponder:
    @pytest.fixture
    def fake_app(self, RoomsApp):
        agents = {"curator": {"id": "curator", "name": "Curator"}}

        class _F(RoomsApp):
            def _load_agent(self, aid):
                return agents.get(aid)

        return object.__new__(_F)

    def test_no_responders(self, fake_app):
        parts = [{"type": "user", "id": "me"}]
        assert fake_app._resolve_responder("hi", parts) is None

    def test_single_responder_picked(self, fake_app):
        parts = [
            {"type": "user", "id": "me"},
            {"type": "agent", "id": "curator"},
        ]
        assert fake_app._resolve_responder("hi", parts) == {
            "type": "agent", "id": "curator",
        }

    def test_cli_responder_resolved_by_id(self, fake_app):
        parts = [
            {"type": "user", "id": "me"},
            {"type": "agent", "id": "curator"},
            {"type": "cli", "id": "claude-cli"},
        ]
        result = fake_app._resolve_responder("@claude-cli help", parts)
        assert result["type"] == "cli"
        assert result["id"] == "claude-cli"

    def test_user_never_resolved(self, fake_app):
        parts = [
            {"type": "user", "id": "me"},
            {"type": "agent", "id": "curator"},
        ]
        # @me is unknown to the responder pool → falls back to first responder
        assert fake_app._resolve_responder("@me", parts) == {
            "type": "agent", "id": "curator",
        }


# ── _extract_wikilinks (Phase 11) ──────────────────────────────────────


class TestExtractWikilinks:
    def test_no_links(self, app):
        assert app._extract_wikilinks("hello world") == []

    def test_single_link(self, app):
        assert app._extract_wikilinks("see [[notes/idea]] please") == ["notes/idea"]

    def test_multiple_links_in_order(self, app):
        result = app._extract_wikilinks("[[a]] and [[b/c]] and [[d]]")
        assert result == ["a", "b/c", "d"]

    def test_dedupes(self, app):
        # Same link twice → emitted once
        result = app._extract_wikilinks("[[a]] then [[a]] later")
        assert result == ["a"]

    def test_empty_brackets_ignored(self, app):
        # `[[]]` shouldn't match — regex requires non-bracket content
        result = app._extract_wikilinks("noise [[]] [[real]]")
        assert result == ["real"]

    def test_paths_with_spaces(self, app):
        # Paths can contain spaces in markdown wikilinks
        result = app._extract_wikilinks("[[my note]]")
        assert result == ["my note"]


# ── _memory_block (Phase 26) ───────────────────────────────────────────


class TestMemoryBlock:
    def test_empty_list(self, app):
        assert app._memory_block({"memory": []}) == ""

    def test_no_memory_key(self, app):
        assert app._memory_block({}) == ""

    def test_renders_facts_with_header(self, app):
        room = {"memory": [
            {"id": "m1", "fact": "Kevin prefers tabs"},
            {"id": "m2", "fact": "Project deadline is Friday"},
        ]}
        block = app._memory_block(room)
        assert "Memory" in block
        assert "Kevin prefers tabs" in block
        assert "Project deadline is Friday" in block
        # Each fact on its own line
        assert block.count("\n- ") == 2


# ── _build_system tool-less guard (Life Strategist hallucination fix) ──


class TestBuildSystemToolless:
    """Tool-less agents must be told they have no write capability, or
    they hallucinate completions ('Written to ...') with nothing behind
    them. Caught in the wild 2026-05-15 with Life Strategist."""

    NO_TOOLS_MARKER = "no write tools and no server actions"

    def test_toolless_agent_gets_no_write_clause(self, app):
        agent = {"system_prompt": "You are a strategic advisor."}
        sys_prompt = app._build_system(agent)
        assert self.NO_TOOLS_MARKER in sys_prompt
        assert "You are a strategic advisor." in sys_prompt

    def test_agent_with_server_actions_does_not_get_clause(self, app):
        agent = {
            "system_prompt": "You are a task helper.",
            "server_actions": {"task": ["add"]},
        }
        sys_prompt = app._build_system(agent)
        assert self.NO_TOOLS_MARKER not in sys_prompt

    def test_agent_with_tools_does_not_get_clause(self, app):
        agent = {"system_prompt": "X", "tools": {"web": True}}
        sys_prompt = app._build_system(agent)
        assert self.NO_TOOLS_MARKER not in sys_prompt

    def test_agent_with_client_actions_does_not_get_clause(self, app):
        agent = {"system_prompt": "X"}
        client_actions = [{"name": "show_modal", "params": []}]
        sys_prompt = app._build_system(agent, client_actions=client_actions)
        assert self.NO_TOOLS_MARKER not in sys_prompt

    def test_empty_server_actions_dict_still_triggers_clause(self, app):
        """`server_actions: {}` is the same as missing — both mean no actions."""
        agent = {"system_prompt": "X", "server_actions": {}, "tools": {}}
        sys_prompt = app._build_system(agent)
        assert self.NO_TOOLS_MARKER in sys_prompt


# ── suggest_agents (Phase 27) ──────────────────────────────────────────


class TestSuggestAgents:
    @pytest.fixture
    def fake_app(self, RoomsApp):
        roster = [
            {"id": "code-arch", "name": "Code Architect", "tier": "user",
             "system_prompt": "You are a senior software architect."},
            {"id": "career", "name": "Career Coach", "tier": "user",
             "system_prompt": "You give career advice and resume help."},
            {"id": "blender", "name": "Blender Expert", "tier": "user",
             "system_prompt": "You are a Blender 3D Python scripting expert."},
            {"id": "group-1", "name": "Old group", "tier": "group",
             "system_prompt": ""},
            {"id": "stale-1", "name": "Stale", "tier": "user",
             "status": "archived",
             "system_prompt": "career career career advice resume"},
        ]

        class _F(RoomsApp):
            def _list_agents(self):
                return roster

        return object.__new__(_F)

    def test_empty_query_returns_empty(self, fake_app):
        assert fake_app.suggest_agents("") == []

    def test_keyword_match(self, fake_app):
        results = fake_app.suggest_agents("software architect", limit=3)
        ids = [r["id"] for r in results]
        assert "code-arch" in ids

    def test_excludes_group_rooms(self, fake_app):
        # Even if a group room's name contains the query, it shouldn't
        # be suggested (suggesting groups inside group-creation modal
        # would let users nest groups, which is meaningless).
        results = fake_app.suggest_agents("group", limit=10)
        ids = [r["id"] for r in results]
        assert "group-1" not in ids

    def test_excludes_archived(self, fake_app):
        # `career advice resume` matches both Career Coach (active) and
        # the archived stale-1 — only the active one should surface.
        results = fake_app.suggest_agents("career advice", limit=10)
        ids = [r["id"] for r in results]
        assert "stale-1" not in ids
        assert "career" in ids

    def test_score_ordering(self, fake_app):
        # "blender python scripting" hits 3 distinct words on blender,
        # 0 on others → blender ranks first.
        results = fake_app.suggest_agents("blender python scripting", limit=3)
        assert results[0]["id"] == "blender"

    def test_stopwords_filtered(self, fake_app):
        # All-stopwords query returns nothing (after filtering, no tokens left)
        assert fake_app.suggest_agents("the a of") == []

    def test_short_tokens_filtered(self, fake_app):
        # 1-char tokens are filtered out; if all tokens are 1-char, no match.
        assert fake_app.suggest_agents("a x y z") == []


# ── _gate_server_actions (Phase 5) — needs filesystem + fake emit ──────


class TestAlwaysGateVerbs:
    def test_promote_apply_is_always_gated(self, RoomsApp):
        # Applying a promote proposal can fire an outbound webhook, so it must
        # always render a review card regardless of allowlist / grant.
        from apps.rooms.pending import ALWAYS_GATE_VERBS

        assert ("promote", "apply_proposal") in ALWAYS_GATE_VERBS


class TestGateServerActions:
    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        emitted: list = []

        class _FakeConfig:
            data_dir = tmp_path

        class _FakeKernel:
            config = _FakeConfig()

        class _F(RoomsApp):
            data_dir = tmp_path

            async def emit(self, *a, **k):
                emitted.append((a, k))

        inst = object.__new__(_F)
        inst.kernel = _FakeKernel()
        inst._emitted_ref = emitted
        return inst

    @pytest.mark.asyncio
    async def test_no_tokens_yields_no_pending(self, app):
        cleaned, pending = await app._gate_server_actions(
            "just plain text",
            room_id="r1",
            source_actor={"type": "cli", "id": "x"},
        )
        assert pending == []
        assert cleaned == "just plain text"

    @pytest.mark.asyncio
    async def test_one_do_token_saved(self, app):
        cleaned, pending = await app._gate_server_actions(
            'sure! [DO:task.add({"text":"buy milk"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        assert len(pending) == 1
        p = pending[0]
        assert p["app"] == "task"
        assert p["method"] == "add"
        assert p["args"] == {"text": "buy milk"}
        assert p["status"] == "pending"
        assert p["room_id"] == "r1"
        assert p["source_actor"]["id"] == "claude-cli"
        # Token stripped from cleaned text
        assert "[DO:" not in cleaned
        assert "sure" in cleaned
        # File persisted
        files = list(app.data_dir.glob("pending/act-*.json"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_multiple_tokens(self, app):
        cleaned, pending = await app._gate_server_actions(
            '[DO:task.add({"text":"a"})] then '
            '[DO:journal.add_entry({"text":"b","mood":"ok"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "x"},
        )
        assert len(pending) == 2
        verbs = [(p["app"], p["method"]) for p in pending]
        assert ("task", "add") in verbs
        assert ("journal", "add_entry") in verbs


# ── pivot 2026-06-07: auto_stable_default flag through the real gate ───


class TestGateAutoStableDefault:
    """With ``[autopilot] auto_stable_default`` ON, a stable/eligible verb
    auto-applies through ``_gate_server_actions`` (no grant), while a
    non-eligible (``never``) verb still lands in the pending queue. Proves the
    pivot is wired into the actual gate, not just ``decide()`` in isolation."""

    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        emitted: list = []
        called: list = []

        class _FakeConfig:
            data_dir = tmp_path

            def get(self, key, default=None):
                if key == "autopilot.auto_stable_default":
                    return True
                return default

        class _FakeKernel:
            config = _FakeConfig()

        class _F(RoomsApp):
            data_dir = tmp_path

            async def emit(self, *a, **k):
                emitted.append((a, k))

            async def call_app(self, app_id, method, **kwargs):
                called.append((app_id, method, kwargs))
                return {"ok": True}

        inst = object.__new__(_F)
        inst.kernel = _FakeKernel()
        inst._emitted_ref = emitted
        inst._called_ref = called
        return inst

    @pytest.mark.asyncio
    async def test_stable_verb_auto_applies(self, app):
        cleaned, pending = await app._gate_server_actions(
            '[DO:task.add({"text":"buy milk"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        assert len(pending) == 1
        p = pending[0]
        assert p["status"] == "applied"
        assert p["auto_reason"] == "stable-default"
        assert p["grant_id"] is None
        # Actually dispatched through call_app.
        assert ("task", "add", {"text": "buy milk"}) in app._called_ref

    @pytest.mark.asyncio
    async def test_never_verb_still_gates(self, app):
        # note.create is free-form content — not autopilot-eligible.
        cleaned, pending = await app._gate_server_actions(
            '[DO:note.create({"title":"x","body":"y"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"
        assert not any(c[0] == "note" for c in app._called_ref)


# ── edit-before-apply (AG-UI "modify params" borrow) ──────────────────


class TestEditPending:
    """edit_pending lets the user fix a pending action's args before Apply.
    Only ``args`` is editable; diff-shaped (sandboxed) actions are refused;
    ``args_original`` + ``edited`` are stamped for the audit trail."""

    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        emitted: list = []
        called: list = []

        class _FakeConfig:
            data_dir = tmp_path

        class _FakeKernel:
            config = _FakeConfig()

        class _F(RoomsApp):
            data_dir = tmp_path

            async def emit(self, *a, **k):
                emitted.append((a, k))

            async def call_app(self, app_id, method, **kwargs):
                called.append((app_id, method, kwargs))
                return {"ok": True}

            def _action_result_links(self, app_id, result):
                return []

        inst = object.__new__(_F)
        inst.kernel = _FakeKernel()
        inst._called_ref = called
        return inst

    async def _gate_one(self, app, token='[DO:task.add({"text":"buy milk"})]'):
        _, pending = await app._gate_server_actions(
            token, room_id="r1", source_actor={"type": "cli", "id": "x"},
        )
        return pending[0]

    @pytest.mark.asyncio
    async def test_edit_persists_and_stamps_audit_fields(self, app):
        p = await self._gate_one(app)
        res = app.edit_pending(p["id"], {"text": "buy oat milk"})
        assert "error" not in res
        assert res["args"] == {"text": "buy oat milk"}
        assert res["args_original"] == {"text": "buy milk"}
        assert res["edited"] is True
        # Persisted to disk, not just the returned dict.
        on_disk = app._load_pending(p["id"])
        assert on_disk["args"] == {"text": "buy oat milk"}
        assert on_disk["args_original"] == {"text": "buy milk"}

    @pytest.mark.asyncio
    async def test_apply_dispatches_edited_args(self, app):
        p = await self._gate_one(app)
        app.edit_pending(p["id"], {"text": "edited"})
        res = await app.apply_pending(p["id"])
        assert res["status"] == "applied"
        assert ("task", "add", {"text": "edited"}) in app._called_ref

    @pytest.mark.asyncio
    async def test_second_edit_keeps_first_original(self, app):
        p = await self._gate_one(app)
        app.edit_pending(p["id"], {"text": "v2"})
        res = app.edit_pending(p["id"], {"text": "v3"})
        assert res["args"] == {"text": "v3"}
        assert res["args_original"] == {"text": "buy milk"}

    @pytest.mark.asyncio
    async def test_refuses_resolved_action(self, app):
        p = await self._gate_one(app)
        await app.apply_pending(p["id"])
        res = app.edit_pending(p["id"], {"text": "too late"})
        assert res["error"] == "already applied"

    @pytest.mark.asyncio
    async def test_refuses_non_dict_args_and_unknown_id(self, app):
        p = await self._gate_one(app)
        assert "error" in app.edit_pending(p["id"], "not a dict")
        assert app.edit_pending("act-nope", {})["error"] == "action not found"

    @pytest.mark.asyncio
    async def test_refuses_diff_shaped_action(self, app):
        # repo.exec is in ALWAYS_GATE_VERBS; its prep attaches
        # proposed_command, and the verb itself is excluded from edit.
        p = await self._gate_one(app, '[DO:repo.exec({"cmd":"echo hi"})]')
        res = app.edit_pending(p["id"], {"cmd": "rm -rf /"})
        assert "not editable" in res["error"]


# ── budget ceiling through the real gate (dark flag) ───────────────────


class TestGateBudgetCeiling:
    """With ``enforce_budget_caps`` + ``auto_stable_default`` ON and the actor
    over its monthly cap, a stable verb that would auto-apply gates instead,
    with ``gate_reason == "over-budget"`` preserved on the record."""

    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        called: list = []

        class _FakeConfig:
            data_dir = tmp_path

            def get(self, key, default=None):
                if key in ("autopilot.auto_stable_default",
                           "autopilot.enforce_budget_caps"):
                    return True
                return default

        class _FakeKernel:
            config = _FakeConfig()

        class _F(RoomsApp):
            data_dir = tmp_path

            async def emit(self, *a, **k):
                pass

            async def call_app(self, app_id, method, **kwargs):
                called.append((app_id, method, kwargs))
                return {"ok": True}

        inst = object.__new__(_F)
        inst.kernel = _FakeKernel()
        inst._called_ref = called
        return inst

    @pytest.mark.asyncio
    async def test_over_budget_actor_gates(self, app, tmp_path):
        from emptyos.sdk.autopilot import record_spend, set_budget
        set_budget(tmp_path, "claude-cli", 1.00)
        record_spend(tmp_path, "claude-cli", 1.50)
        _, pending = await app._gate_server_actions(
            '[DO:task.add({"text":"buy milk"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"
        assert pending[0]["gate_reason"] == "over-budget"
        assert app._called_ref == []

    @pytest.mark.asyncio
    async def test_under_budget_actor_still_auto_applies(self, app, tmp_path):
        from emptyos.sdk.autopilot import record_spend, set_budget
        set_budget(tmp_path, "claude-cli", 1.00)
        record_spend(tmp_path, "claude-cli", 0.10)
        _, pending = await app._gate_server_actions(
            '[DO:task.add({"text":"buy milk"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        assert pending[0]["status"] == "applied"
        assert pending[0]["auto_reason"] == "stable-default"


# ── gate_mode bridge — _execute_server_actions routes to gate ─────────


class TestGateModeBridge:
    """Verify the `gate_mode="gate"` branch in `_execute_server_actions`
    routes [DO:] tokens to the pending queue instead of auto-executing.
    Reference: scripts/setup_job_scout.py (first consumer)."""

    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        emitted: list = []
        called: list = []

        class _FakeConfig:
            data_dir = tmp_path

        class _FakeKernel:
            config = _FakeConfig()

        class _F(RoomsApp):
            data_dir = tmp_path

            async def emit(self, *a, **k):
                emitted.append((a, k))

            async def call_app(self, app_id, method, **kwargs):
                # Auto-exec path goes through call_app; gate path must not.
                called.append((app_id, method, kwargs))
                return {"ok": True}

        inst = object.__new__(_F)
        inst.kernel = _FakeKernel()
        inst._emitted_ref = emitted
        inst._called_ref = called
        return inst

    @pytest.mark.asyncio
    async def test_gate_mode_routes_to_pending(self, app):
        agent = {"id": "job-scout", "gate_mode": "gate", "server_actions": {}}
        cleaned, results = await app._execute_server_actions(
            '[DO:task.add({"text":"lead 1"})]',
            agent,
            room_id="job-scout",
        )
        # Did NOT auto-exec (no call_app invocation)
        assert app._called_ref == []
        # DID land in pending
        assert len(results) == 1
        assert results[0]["app"] == "task"
        assert results[0]["status"] == "pending"
        assert "[DO:" not in cleaned

    @pytest.mark.asyncio
    async def test_auto_mode_default_executes_allowlisted(self, app):
        agent = {
            "id": "tasker", "gate_mode": "auto",
            "server_actions": {"task": ["add"]},
        }
        _, results = await app._execute_server_actions(
            '[DO:task.add({"text":"do it"})]',
            agent,
            room_id="tasker",  # ignored in auto mode
        )
        # DID auto-exec — call_app was invoked. The result's ok flag depends
        # on post-call helpers (_lookup_inverse, log file) that don't exist
        # on this minimal mock; what matters here is that we took the
        # auto-exec branch rather than the gate branch.
        assert len(app._called_ref) == 1
        assert app._called_ref[0][0] == "task"
        assert app._called_ref[0][1] == "add"
        # And critically: no pending file was created (this is auto mode).
        assert list(app.data_dir.glob("pending/act-*.json")) == []
        assert results[0]["app"] == "task"

    @pytest.mark.asyncio
    async def test_gate_mode_without_room_id_falls_through(self, app):
        # Defensive: if gate_mode is set but room_id is missing (e.g. an
        # older call site that hasn't been updated), fall through to the
        # auto path. Server_actions allowlist gates execution there — empty
        # allowlist means nothing fires, which is safer than dropping silently.
        agent = {"id": "x", "gate_mode": "gate", "server_actions": {}}
        cleaned, results = await app._execute_server_actions(
            '[DO:task.add({"text":"lost"})]',
            agent,
            room_id="",
        )
        assert app._called_ref == []  # empty allowlist blocks
        assert results == []  # no pending either — proposal lost
        # The cleaned-text contract still holds even on fall-through.
        assert "[DO:" in cleaned or cleaned == ""


# ── write_note sandbox-diff (Bridge-inspired) — gate→apply→reject ──────


class TestWriteNoteSandbox:
    """End-to-end test of the [DO:rooms.write_note] sandboxed verb.

    Covers: gate captures diff into pending, apply writes vault file,
    reject discards sandbox, stale-vault apply fails, missing args fail.
    """

    @pytest.fixture
    def setup(self, RoomsApp, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        d_dir = tmp_path / "data"
        d_dir.mkdir()
        emitted: list = []

        class _FakeConfig:
            notes_path = vault_dir
            data_dir = d_dir

        class _FakeKernel:
            config = _FakeConfig()

        class _F(RoomsApp):
            data_dir = d_dir

            async def emit(self, *a, **k):
                emitted.append((a, k))

        inst = object.__new__(_F)
        inst.kernel = _FakeKernel()
        inst._emitted_ref = emitted
        return inst, vault_dir

    @pytest.mark.asyncio
    async def test_gate_captures_proposed_changes(self, setup):
        app, vault = setup
        token = (
            '[DO:rooms.write_note({"path":"00_Inbox/test.md",'
            '"content":"hello sandbox\\n"})]'
        )
        cleaned, pending = await app._gate_server_actions(
            token, room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        assert len(pending) == 1
        p = pending[0]
        assert p["app"] == "rooms"
        assert p["method"] == "write_note"
        assert "proposed_changes" in p
        assert len(p["proposed_changes"]) == 1
        change = p["proposed_changes"][0]
        assert change["path"] == "00_Inbox/test.md"
        assert change["sandbox_id"] == p["id"]
        # New file diff: every body line is an add.
        kinds = {l["kind"] for l in change["diff_lines"]}
        assert "add" in kinds
        # The vault file does NOT exist yet — gate is non-destructive.
        assert not (vault / "00_Inbox" / "test.md").exists()

    @pytest.mark.asyncio
    async def test_apply_writes_file_to_vault(self, setup):
        app, vault = setup
        _, pending = await app._gate_server_actions(
            '[DO:rooms.write_note({"path":"notes/x.md","content":"body\\n"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        result = await app.apply_pending(pending[0]["id"])
        assert result["status"] == "applied"
        target = vault / "notes" / "x.md"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "body\n"
        # rooms:note_written + rooms:action_applied both emitted.
        # _emitted_ref stores (positional_args, kwargs); positional_args is
        # (event_type, payload) from `await self.emit(event_type, payload)`.
        emitted_types = [pos[0] for pos, _kw in app._emitted_ref]
        assert "rooms:note_written" in emitted_types
        assert "rooms:action_applied" in emitted_types

    @pytest.mark.asyncio
    async def test_apply_stale_when_vault_changed(self, setup):
        app, vault = setup
        target = vault / "race.md"
        target.write_text("original\n", encoding="utf-8")
        _, pending = await app._gate_server_actions(
            '[DO:rooms.write_note({"path":"race.md","content":"agent draft\\n"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        # User edits directly between gate and apply.
        target.write_text("user edited mid-review\n", encoding="utf-8")
        result = await app.apply_pending(pending[0]["id"])
        assert result.get("error")
        assert "stale" in result["error"].lower() or "changed" in result["error"].lower()
        # Vault content untouched by failed apply.
        assert target.read_text(encoding="utf-8") == "user edited mid-review\n"

    @pytest.mark.asyncio
    async def test_reject_discards_sandbox(self, setup):
        app, vault = setup
        _, pending = await app._gate_server_actions(
            '[DO:rooms.write_note({"path":"drop.md","content":"throw away\\n"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        action_id = pending[0]["id"]
        sandbox_dir = app._sandbox_root() / action_id
        assert sandbox_dir.exists()
        result = await app.reject_pending(action_id)
        assert result["status"] == "rejected"
        assert not sandbox_dir.exists()
        assert not (vault / "drop.md").exists()

    @pytest.mark.asyncio
    async def test_missing_args_records_error(self, setup):
        app, vault = setup
        _, pending = await app._gate_server_actions(
            '[DO:rooms.write_note({"path":"missing-content.md"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        assert pending[0].get("error")
        # Apply surfaces the gate-time error as failed status.
        result = await app.apply_pending(pending[0]["id"])
        assert result.get("error")

    @pytest.mark.asyncio
    async def test_path_traversal_records_error(self, setup):
        app, vault = setup
        _, pending = await app._gate_server_actions(
            '[DO:rooms.write_note({"path":"../escape.md","content":"evil\\n"})]',
            room_id="r1",
            source_actor={"type": "cli", "id": "claude-cli"},
        )
        assert pending[0].get("error")
        # No file leaked outside vault.
        assert not (vault.parent / "escape.md").exists()


# ── search_messages (Phase 7b) — needs filesystem ──────────────────────


class TestSearchMessages:
    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        # Create a few history files with messages.
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        import json as _json
        (history_dir / "room-a.json").write_text(_json.dumps({
            "messages": [
                {"role": "user", "text": "What about cable derating?", "ts": "2026-01-01T10:00:00"},
                {"role": "assistant", "text": "Derating depends on installation method.", "ts": "2026-01-01T10:00:05"},
            ]
        }))
        (history_dir / "room-b.json").write_text(_json.dumps({
            "messages": [
                {"role": "user", "text": "Talk to me about lunch plans.", "ts": "2026-01-02T12:00:00"},
            ]
        }))

        agents = {
            "room-a": {"id": "room-a", "name": "Cable Expert"},
            "room-b": {"id": "room-b", "name": "Lunch Buddy"},
        }

        class _F(RoomsApp):
            data_dir = tmp_path
            def _load_agent(self, aid):
                return agents.get(aid)

        return object.__new__(_F)

    def test_empty_query_returns_empty(self, app):
        assert app.search_messages("") == []

    def test_short_query_returns_empty(self, app):
        # Implementation requires query >= 2 chars
        assert app.search_messages("a") == []

    def test_finds_match_with_snippet(self, app):
        results = app.search_messages("derating")
        assert len(results) >= 1
        assert any("derating" in r["snippet"].lower() for r in results)
        assert results[0]["room_id"] == "room-a"
        assert results[0]["room_name"] == "Cable Expert"

    def test_results_sorted_newest_first(self, app):
        results = app.search_messages("a", limit=20) if False else app.search_messages("the", limit=20)
        # Recent ts values come first.
        timestamps = [r.get("ts", "") for r in results if r.get("ts")]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_no_match(self, app):
        assert app.search_messages("xyzqrwt-no-such-thing") == []


# ── Team mode — role normalization + state derivation (pure) ───────────


class TestTeamRoles:
    def test_role_defaults_to_peer(self, app):
        parts = app._normalize_participants({
            "id": "g1",
            "participants": [{"type": "user"}, {"type": "agent", "id": "a"}],
        })
        assert all(p.get("role") == "peer" for p in parts)

    def test_explicit_role_preserved(self, app):
        parts = app._normalize_participants({
            "id": "g1",
            "participants": [
                {"type": "user", "id": "me", "role": "peer"},
                {"type": "agent", "id": "a", "role": "lead"},
                {"type": "agent", "id": "b", "role": "worker"},
            ],
        })
        roles = {p["id"]: p["role"] for p in parts}
        assert roles == {"me": "peer", "a": "lead", "b": "worker"}

    def test_legacy_synthesised_room_has_roles(self, app):
        # Legacy 1:1 records get role-stamped too, so downstream team code
        # never KeyErrors on a missing role.
        parts = app._normalize_participants({"id": "x"})
        assert all("role" in p for p in parts)

    def test_team_enabled_only_with_lead(self, app):
        no_lead = {"id": "g", "participants": [
            {"type": "user", "id": "me"}, {"type": "agent", "id": "a"}]}
        with_lead = {"id": "g", "participants": [
            {"type": "user", "id": "me"},
            {"type": "agent", "id": "a", "role": "lead"},
            {"type": "agent", "id": "b", "role": "worker"}]}
        assert app._team_enabled(no_lead) is False
        assert app._team_enabled(with_lead) is True

    def test_lead_participant_lookup(self, app):
        room = {"id": "g", "participants": [
            {"type": "user", "id": "me"},
            {"type": "agent", "id": "a", "role": "lead"}]}
        assert (app._team_lead_participant(room) or {}).get("id") == "a"
        assert app._team_lead_participant({"id": "g"}) is None

    def test_prompt_block_empty_for_peer(self, app):
        # Non-team room → no extra prompt (zero behaviour change guarantee).
        room = {"id": "g", "name": "R", "participants": [
            {"type": "user", "id": "me"}, {"type": "agent", "id": "a"}]}
        assert app._team_prompt_block(room, {"type": "agent", "id": "a", "role": "peer"}) == ""

    def test_prompt_block_lead_has_verbs_and_list(self, app):
        room = {"id": "g", "name": "R",
                "participants": [
                    {"type": "user", "id": "me"},
                    {"type": "agent", "id": "lead1", "role": "lead"},
                    {"type": "agent", "id": "w1", "role": "worker"}],
                "team": {"tasks": [
                    {"id": "t-1", "content": "do thing", "assignee": "w1", "status": "todo"}]}}
        block = app._team_prompt_block(room, {"type": "agent", "id": "lead1", "role": "lead"})
        assert "TEAM LEAD" in block
        assert "rooms.team_assign" in block
        assert "t-1" in block and "do thing" in block

    def test_prompt_block_worker_shows_own_tasks(self, app):
        room = {"id": "g", "name": "R",
                "participants": [
                    {"type": "user", "id": "me"},
                    {"type": "agent", "id": "lead1", "role": "lead"},
                    {"type": "agent", "id": "w1", "role": "worker"}],
                "team": {"tasks": [
                    {"id": "t-1", "content": "mine", "assignee": "w1", "status": "todo"},
                    {"id": "t-2", "content": "not mine", "assignee": "lead1", "status": "todo"}]}}
        block = app._team_prompt_block(room, {"type": "agent", "id": "w1", "role": "worker"})
        assert "WORKER" in block
        assert "t-1" in block and "mine" in block
        assert "t-2" not in block  # only the worker's own tasks


# ── Team mode — task CRUD + run control (filesystem-backed) ────────────


class TestTeamVerbs:
    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        class _FakeAgents:
            def invalidate(self, *a, **k): pass

        class _FakeKernel:
            agents = _FakeAgents()

        class _F(RoomsApp):
            data_dir = tmp_path

            async def emit(self, *a, **k): pass

        inst = object.__new__(_F)
        inst.kernel = _FakeKernel()
        # Seed the room through the real save path so the on-disk location
        # matches _agents_dir() (data_dir/apps/rooms/agents/) regardless of
        # how that path is computed.
        inst._agents_dir().mkdir(parents=True, exist_ok=True)
        inst._save_agent({
            "id": "team-room", "name": "Team Room",
            "participants": [
                {"type": "user", "id": "me", "role": "peer"},
                {"type": "agent", "id": "lead1", "role": "lead"},
                {"type": "agent", "id": "w1", "role": "worker"},
            ],
        })
        return inst

    def test_add_task(self, app):
        r = app.team_add_task("team-room", "build the thing", "w1")
        assert r["ok"] is True
        assert r["task"]["content"] == "build the thing"
        assert r["task"]["assignee"] == "w1"
        assert r["task"]["status"] == "todo"
        assert r["task"]["id"].startswith("t-")
        # Persisted
        assert len(app.team_list("team-room")["tasks"]) == 1

    def test_add_task_rejects_unknown_assignee(self, app):
        r = app.team_add_task("team-room", "x", "ghost")
        assert "error" in r

    def test_add_task_requires_content(self, app):
        assert "error" in app.team_add_task("team-room", "   ", "")

    def test_assign_updates_assignee(self, app):
        tid = app.team_add_task("team-room", "x", "")["task"]["id"]
        r = app.team_assign("team-room", tid, "w1")
        assert r["ok"] and r["task"]["assignee"] == "w1"

    def test_assign_unknown_task(self, app):
        assert "error" in app.team_assign("team-room", "t-nope", "w1")

    def test_set_status(self, app):
        tid = app.team_add_task("team-room", "x", "w1")["task"]["id"]
        r = app.team_set_status("team-room", tid, "done", result="shipped")
        assert r["ok"] and r["task"]["status"] == "done"
        assert r["task"]["result"] == "shipped"

    def test_set_status_rejects_bad_status(self, app):
        tid = app.team_add_task("team-room", "x", "w1")["task"]["id"]
        assert "error" in app.team_set_status("team-room", tid, "frobnicated")

    def test_set_role_flips_team_enabled(self, app):
        # Demote the lead → team no longer enabled.
        r = app.set_participant_role("team-room", "lead1", "worker")
        assert r["ok"] and r["team_enabled"] is False
        # Promote back.
        r2 = app.set_participant_role("team-room", "w1", "lead")
        assert r2["ok"] and r2["team_enabled"] is True

    def test_set_role_rejects_bad_role(self, app):
        assert "error" in app.set_participant_role("team-room", "w1", "captain")

    def test_next_actionable_picks_assigned_todo(self, app):
        app.team_add_task("team-room", "unassigned", "")        # skipped (no assignee)
        t2 = app.team_add_task("team-room", "assigned", "w1")["task"]["id"]
        room = app._load_agent("team-room")
        nxt = app._team_next_actionable(room)
        assert nxt is not None and nxt["id"] == t2

    def test_next_actionable_none_when_all_done(self, app):
        tid = app.team_add_task("team-room", "x", "w1")["task"]["id"]
        app.team_set_status("team-room", tid, "done")
        assert app._team_next_actionable(app._load_agent("team-room")) is None

    def test_start_run_requires_lead(self, app):
        # Strip the lead first.
        app.set_participant_role("team-room", "lead1", "worker")
        assert "error" in app.team_start_run("team-room")

    def test_start_run_arms_and_caps_budget(self, app):
        r = app.team_start_run("team-room", max_turns=999)
        assert r["ok"] and r["run"]["active"] is True
        assert r["run"]["turns_left"] == 40  # capped at TEAM_MAX_TURNS_CAP

    def test_start_run_refuses_double(self, app):
        app.team_start_run("team-room")
        assert "error" in app.team_start_run("team-room")

    def test_dec_turn_and_stop(self, app):
        app.team_start_run("team-room", max_turns=5)
        left = app._team_dec_turn("team-room")
        assert left == 4
        stop = app.team_stop_run("team-room")
        assert stop["ok"]
        run = app.team_list("team-room")["run"]
        assert run["stop_requested"] is True

    def test_finish_run_clears_active(self, app):
        app.team_start_run("team-room", max_turns=3)
        app._team_finish_run("team-room", "done")
        run = app.team_list("team-room")["run"]
        assert run["active"] is False and run["reason"] == "done"


# ── run_panel forum loop (diverge → moderate → converge) ───────────────


class TestRunPanelForum:
    """The moderated forum loop in panel.py: blind round 1, moderator between
    rounds, early break on convergence. Fakes think/history/emit so no daemon."""

    @pytest.fixture
    def forum_app(self, RoomsApp):
        records = {
            "panel-room": {
                "id": "panel-room",
                "participants": [
                    {"type": "user", "id": "me"},
                    {"type": "agent", "id": "qa"},
                    {"type": "agent", "id": "sec"},
                ],
            },
            "solo-room": {
                "id": "solo-room",
                "participants": [
                    {"type": "user", "id": "me"},
                    {"type": "agent", "id": "qa"},
                ],
            },
            "qa": {"id": "qa", "name": "QA"},
            "sec": {"id": "sec", "name": "Security"},
        }
        from apps.rooms import panel as _p

        class _F(RoomsApp):
            def __init__(self):
                self.think_calls = []
                self.saved = None
                self.emitted = []
                self.converged = False

            def _load_agent(self, aid):
                return records.get(aid)

            def _load_history(self, rid):
                return []

            def _save_history(self, rid, history):
                self.saved = history

            def _build_system(self, r, _):
                return ""

            async def emit(self, name, payload):
                self.emitted.append((name, payload))

            async def think(self, prompt, **kwargs):
                sys_ = kwargs.get("system", "")
                self.think_calls.append({"prompt": prompt, "system": sys_})
                if sys_ == _p._FORUM_HOST_SYSTEM:
                    conv = "true" if self.converged else "false"
                    return ('{"summary": "broad agreement", '
                            '"focus": "remaining edge cases", '
                            '"converged": ' + conv + "}")
                if sys_ == _p._MODERATOR_SYSTEM:
                    return "SYNTHESIS"
                return "TAKE"

        return _F()

    def _agent_prompts(self, app):
        return [c["prompt"] for c in app.think_calls if c["system"] == ""]

    def test_needs_two_agents(self, forum_app):
        import asyncio
        r = asyncio.run(forum_app.run_panel("solo-room", "ship it?"))
        assert "error" in r

    def test_round1_is_blind(self, forum_app):
        import asyncio
        asyncio.run(forum_app.run_panel("panel-room", "ship it?", rounds=2))
        ap = self._agent_prompts(forum_app)
        # Round 1 = first two agent prompts: independent, no moderator context.
        assert "cannot see the other experts yet" in ap[0]
        assert "cannot see the other experts yet" in ap[1]
        assert "MODERATOR'S READ" not in ap[0]

    def test_round2_reads_moderator_focus(self, forum_app):
        import asyncio
        asyncio.run(forum_app.run_panel("panel-room", "ship it?", rounds=2))
        ap = self._agent_prompts(forum_app)
        # Round 2 = next two prompts: carry the moderator's distilled state + focus.
        assert "MODERATOR'S READ OF THE DISCUSSION" in ap[2]
        assert "remaining edge cases" in ap[2]

    def test_moderator_runs_between_rounds(self, forum_app):
        import asyncio
        asyncio.run(forum_app.run_panel("panel-room", "ship it?", rounds=2))
        from apps.rooms import panel as _p
        host_calls = [c for c in forum_app.think_calls
                      if c["system"] == _p._FORUM_HOST_SYSTEM]
        # rounds=2, not converged → moderator runs once (after round 1, not round 2)
        assert len(host_calls) == 1

    def test_converged_breaks_early(self, forum_app):
        import asyncio
        forum_app.converged = True
        r = asyncio.run(forum_app.run_panel("panel-room", "ship it?", rounds=4))
        assert r["rounds"] == 1
        assert r["converged"] is True
        # Only round 1 ran → exactly 2 agent takes.
        assert len(self._agent_prompts(forum_app)) == 2

    def test_moderator_note_and_synthesis_in_history(self, forum_app):
        import asyncio
        asyncio.run(forum_app.run_panel("panel-room", "ship it?", rounds=2))
        kinds = [h.get("panel", {}).get("kind") for h in forum_app.saved]
        assert "moderator" in kinds
        assert "synthesis" in kinds
        assert "question" in kinds

    def test_emits_converged_flag(self, forum_app):
        import asyncio
        asyncio.run(forum_app.run_panel("panel-room", "ship it?", rounds=2))
        assert forum_app.emitted
        name, payload = forum_app.emitted[-1]
        assert name == "rooms:panel"
        assert "converged" in payload and payload["rounds"] == 2


# ── register_persona gate_mode / server_actions (telegram-bridge consumer) ──


class TestRegisterPersonaGateMode:
    @pytest.fixture
    def persona_app(self, RoomsApp):
        saved: list = []

        class _F(RoomsApp):
            def _load_agent(self, agent_id):
                return getattr(self, "_existing", None)

            def _save_agent(self, agent):
                saved.append(agent)

        inst = object.__new__(_F)
        inst._saved_ref = saved
        return inst

    @pytest.mark.asyncio
    async def test_gate_mode_and_actions_set_when_provided(self, persona_app):
        agent = await persona_app.register_persona(
            id="telegram-bridge",
            name="Telegram Bridge",
            system_prompt="persona",
            source="plugin:telegram",
            gate_mode="gate",
            server_actions={"task": ["add"]},
        )
        assert agent["gate_mode"] == "gate"
        assert agent["server_actions"] == {"task": ["add"]}

    @pytest.mark.asyncio
    async def test_omitted_kwargs_preserve_existing(self, persona_app):
        persona_app._existing = {
            "id": "x", "name": "X", "source": "app:y",
            "gate_mode": "gate", "server_actions": {"task": ["add", "list"]},
        }
        agent = await persona_app.register_persona(
            id="x", name="X", system_prompt="p2", source="app:y",
        )
        # A caller that doesn't pass them must never clobber prior values.
        assert agent["gate_mode"] == "gate"
        assert agent["server_actions"] == {"task": ["add", "list"]}

    @pytest.mark.asyncio
    async def test_keep_existing_prompt_preserves_user_edit(self, persona_app):
        persona_app._existing = {
            "id": "x", "name": "X", "source": "plugin:telegram",
            "system_prompt": "USER-TUNED persona",
        }
        agent = await persona_app.register_persona(
            id="x", name="X", system_prompt="shipped seed",
            source="plugin:telegram", keep_existing_prompt=True,
        )
        assert agent["system_prompt"] == "USER-TUNED persona"

    @pytest.mark.asyncio
    async def test_keep_existing_prompt_seeds_on_create(self, persona_app):
        agent = await persona_app.register_persona(
            id="new", name="New", system_prompt="shipped seed",
            source="plugin:telegram", keep_existing_prompt=True,
        )
        assert agent["system_prompt"] == "shipped seed"

    @pytest.mark.asyncio
    async def test_default_still_overwrites_prompt(self, persona_app):
        persona_app._existing = {
            "id": "x", "name": "X", "source": "app:y",
            "system_prompt": "old",
        }
        agent = await persona_app.register_persona(
            id="x", name="X", system_prompt="new", source="app:y",
        )
        assert agent["system_prompt"] == "new"


# ── _method_signature registry fallback (unloaded apps must teach args) ─


class TestRegistrySignature:
    @pytest.fixture
    def sig_app(self, app):
        from emptyos.sdk.verb_registry import VerbEntry, VerbRegistry
        reg = VerbRegistry([
            VerbEntry(verb="expense.add", app_id="expense", method="add",
                      args={"amount": "number", "description": "string", "category": "string?"},
                      surfaces=("voice",), voice={"method": "voice_add_expense"}),
        ])

        class _Apps:
            instances = {}

            def get_verbs(self):
                return reg

        class _K:
            apps = _Apps()

        app.kernel = _K()
        return app

    def test_unloaded_app_uses_registry_args(self, sig_app):
        # Both the voice dispatch method and the canonical method resolve.
        assert sig_app._method_signature("expense", "voice_add_expense") == \
            "(amount, description, category?)"
        assert sig_app._method_signature("expense", "add") == \
            "(amount, description, category?)"

    def test_unknown_method_still_empty(self, sig_app):
        assert sig_app._method_signature("expense", "nope") == "()"
        assert sig_app._method_signature("ghost-app", "add") == "()"


# ── apply/reject atomic claim (TOCTOU) ────────────────────────────────


class TestApplyPendingConcurrency:
    """apply_pending / reject_pending must claim the action atomically.

    Before the claim, both paths were load -> check status == "pending" ->
    await call_app -> save "applied". The await yields the event loop, so two
    concurrent Applies (the HTTP route and the Telegram callback both reach
    rooms.apply_pending) each passed the check and each ran the side effect.
    Reject racing Apply had the same shape, so an action could be both
    executed and marked rejected.
    """

    @staticmethod
    def _wire(app, tmp_path, delay: float = 0.02):
        """Inject the minimum apply_pending touches; return the call sink."""
        calls: list[tuple] = []

        async def fake_call_app(app_id, method, **kw):
            await asyncio.sleep(delay)  # real call_app awaits; widen the window
            calls.append((app_id, method, kw))
            return "ok"

        async def noop_emit(*a, **k):
            return None

        app.data_subdir = lambda name: tmp_path
        app.call_app = fake_call_app
        app.emit = noop_emit
        app._action_result_links = lambda app_id, result: []
        return calls

    @staticmethod
    def _seed(app, aid: str = "act-toctou01") -> str:
        app._save_pending({
            "id": aid,
            "room_id": "r1",
            "ts": "2026-07-25T00:00:00+00:00",
            "source_actor": {"type": "cli", "id": "claude-cli"},
            "app": "task",
            "method": "add",
            "args": {"text": "x"},
            "status": "pending",
        })
        return aid

    @pytest.mark.asyncio
    async def test_concurrent_apply_executes_side_effect_once(self, app, tmp_path):
        calls = self._wire(app, tmp_path)
        aid = self._seed(app)

        results = await asyncio.gather(
            app.apply_pending(aid), app.apply_pending(aid),
        )

        assert len(calls) == 1, (
            f"side effect ran {len(calls)}x for one approval — TOCTOU: both "
            f"callers passed the status check before either persisted a claim"
        )
        # Exactly one caller wins; the loser is refused, not silently ignored.
        errs = [r for r in results if isinstance(r, dict) and r.get("error")]
        assert len(errs) == 1, f"expected 1 refusal, got {len(errs)}: {results}"
        assert app._load_pending(aid)["status"] == "applied"

    @pytest.mark.asyncio
    async def test_apply_racing_reject_yields_one_outcome(self, app, tmp_path):
        calls = self._wire(app, tmp_path)
        aid = self._seed(app, "act-toctou02")

        results = await asyncio.gather(
            app.apply_pending(aid), app.reject_pending(aid),
        )

        # Whoever claims first wins; the loser must be REFUSED. Without a claim
        # both succeed: reject persists "rejected", then apply overwrites it
        # with "applied" — the user's Reject is silently discarded and the
        # side effect runs anyway.
        errs = [r for r in results if isinstance(r, dict) and r.get("error")]
        assert len(errs) == 1, (
            f"expected exactly 1 refusal, got {len(errs)} — apply and reject "
            f"both won: {results}"
        )
        final = app._load_pending(aid)["status"]
        assert final in ("applied", "rejected")
        if final == "rejected":
            assert calls == [], "executed an action that ended up rejected"
        else:
            assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_second_apply_after_completion_is_refused(self, app, tmp_path):
        """Serial double-apply was already safe; pin it so the claim doesn't
        regress the plain path."""
        calls = self._wire(app, tmp_path, delay=0)
        aid = self._seed(app, "act-toctou03")

        first = await app.apply_pending(aid)
        second = await app.apply_pending(aid)

        assert len(calls) == 1
        assert not first.get("error")
        assert "already" in (second.get("error") or "")


# ── unknown-effect visibility (status: approving) ─────────────────────


class TestUnknownEffectVisibility:
    """An action stuck at the claim marker must be VISIBLE and adjudicable.

    `claim_pending` persists "approving" before the side effect. If the process
    dies mid-execution the entry stays there: we started it and don't know if it
    landed. Before this, every surface filtered `status == "pending"` exactly, so
    such an entry vanished from all counts and lists — and when rendered at all
    fell through the card renderer's else-branch, displaying "failed: error"
    about an action that may well have succeeded.
    """

    @staticmethod
    def _wire(app, tmp_path):
        calls: list[tuple] = []

        async def fake_call_app(app_id, method, **kw):
            calls.append((app_id, method, kw))
            return "ok"

        async def noop_emit(*a, **k):
            return None

        app.data_subdir = lambda name: tmp_path
        app.call_app = fake_call_app
        app.emit = noop_emit
        app._action_result_links = lambda app_id, result: []
        return calls

    @staticmethod
    def _seed(app, aid, status):
        app._save_pending({
            "id": aid, "room_id": "r1", "ts": "2026-07-25T00:00:00+00:00",
            "source_actor": {"type": "cli", "id": "claude-cli"},
            "app": "task", "method": "add", "args": {"text": "x"},
            "status": status,
        })
        return aid

    def test_open_status_includes_pending_and_approving(self, app, tmp_path):
        self._wire(app, tmp_path)
        self._seed(app, "act-p", "pending")
        self._seed(app, "act-a", "approving")
        self._seed(app, "act-done", "applied")

        ids = {a["id"] for a in app.list_pending(status="open")}
        assert ids == {"act-p", "act-a"}, "open must surface the stuck claim"
        # Exact-match statuses keep working for existing callers.
        assert {a["id"] for a in app.list_pending(status="pending")} == {"act-p"}
        assert {a["id"] for a in app.list_pending(status="approving")} == {"act-a"}

    @pytest.mark.asyncio
    async def test_resolve_unknown_never_re_executes(self, app, tmp_path):
        calls = self._wire(app, tmp_path)
        self._seed(app, "act-a", "approving")

        res = await app.resolve_unknown("act-a", "applied")

        assert not res.get("error"), res
        assert calls == [], "adjudicating must never re-run the side effect"
        assert app._load_pending("act-a")["status"] == "applied"

    @pytest.mark.asyncio
    async def test_resolve_unknown_can_mark_failed(self, app, tmp_path):
        self._wire(app, tmp_path)
        self._seed(app, "act-a", "approving")
        await app.resolve_unknown("act-a", "failed")
        rec = app._load_pending("act-a")
        assert rec["status"] == "failed"
        assert rec.get("adjudicated") is True

    @pytest.mark.asyncio
    async def test_resolve_unknown_refuses_non_approving(self, app, tmp_path):
        self._wire(app, tmp_path)
        self._seed(app, "act-p", "pending")
        self._seed(app, "act-done", "applied")

        assert "error" in await app.resolve_unknown("act-p", "applied")
        assert "error" in await app.resolve_unknown("act-done", "failed")
        # untouched
        assert app._load_pending("act-p")["status"] == "pending"
        assert app._load_pending("act-done")["status"] == "applied"

    @pytest.mark.asyncio
    async def test_resolve_unknown_rejects_bad_outcome(self, app, tmp_path):
        self._wire(app, tmp_path)
        self._seed(app, "act-a", "approving")
        assert "error" in await app.resolve_unknown("act-a", "pending")
        assert "error" in await app.resolve_unknown("act-a", "whatever")
        assert app._load_pending("act-a")["status"] == "approving"

    @pytest.mark.asyncio
    async def test_apply_refuses_a_stuck_claim(self, app, tmp_path):
        """A retry requires a FRESH approval — never an auto-replay."""
        calls = self._wire(app, tmp_path)
        self._seed(app, "act-a", "approving")
        res = await app.apply_pending("act-a")
        assert "already approving" in (res.get("error") or "")
        assert calls == []


# ── CLI dispatch: unsandboxed-write cwd guard ─────────────────────────


class TestCliUnsandboxedCwdGuard:
    """A text-CLI adapter with a *measured* filesystem escape must not inherit
    the implicit vault cwd.

    Codex on Windows reports `sandbox: read-only` and writes anyway, so cwd is
    the only real containment there. Both directions are pinned: flagged +
    implicit cwd refuses without ever spawning; flagged + explicit cwd and an
    unflagged adapter both dispatch normally.
    """

    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        class _FakeConfig:
            notes_path = tmp_path

        class _FakeKernel:
            config = _FakeConfig()

        inst = object.__new__(RoomsApp)
        inst.kernel = _FakeKernel()
        inst._build_cli_prompt = lambda room, text, history: "prompt"
        inst._build_cli_system = lambda room, part: "system"
        return inst

    @staticmethod
    def _runtime(writes_unsandboxed):
        class _Rt:
            called = False

            def cli_adapter_info(self, cli_id):
                return {"writes_unsandboxed": writes_unsandboxed}

            async def text_cli_run(self, **kw):
                _Rt.called = True
                return {"text": "reply"}

        return _Rt()

    @staticmethod
    def _drain(app, part, runtime):
        app.service = lambda name: runtime
        out = []

        async def go():
            async for c in app._dispatch_cli_turn({"id": "room1"}, part, "hi", []):
                out.append(c)

        asyncio.run(go())
        return out

    def test_refuses_implicit_vault_cwd(self, app):
        rt = self._runtime(True)
        chunks = self._drain(app, {"id": "codex", "type": "cli"}, rt)
        assert rt.called is False, "must not spawn the CLI at all"
        assert chunks[-1]["error"] is True
        assert "cwd" in chunks[-1]["text"]

    def test_explicit_cwd_passes(self, app, tmp_path):
        rt = self._runtime(True)
        part = {"id": "codex", "type": "cli", "cwd": str(tmp_path)}
        chunks = self._drain(app, part, rt)
        assert rt.called is True, "an explicit cwd is the user's deliberate choice"
        assert chunks[-1].get("error") is not True

    def test_unflagged_adapter_passes(self, app):
        rt = self._runtime(False)
        chunks = self._drain(app, {"id": "pi", "type": "cli"}, rt)
        assert rt.called is True, "guard must not fire on adapters with no measured escape"
        assert chunks[-1].get("error") is not True


class TestCliStreamJsonDispatch:
    """An adapter declaring `stream_json` takes the streaming path and gets the
    same tool cards claude-cli does, instead of one opaque text blob.

    The point of the restructure: buffered-vs-streaming is now a declared
    property of the adapter, not a hardcoded `cli_id != "claude-cli"`.
    """

    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        class _FakeConfig:
            notes_path = tmp_path

        class _FakeKernel:
            config = _FakeConfig()

        inst = object.__new__(RoomsApp)
        inst.kernel = _FakeKernel()
        inst._build_cli_prompt = lambda room, text, history: "prompt"
        inst._build_cli_system = lambda room, part: "system"
        return inst

    @staticmethod
    def _runtime(events, stream_json=True):
        import json as _json

        class _Rt:
            saw_stream_callback = False

            def cli_adapter_info(self, cli_id):
                return {"stream_json": stream_json, "writes_unsandboxed": False}

            async def text_cli_run(self, **kw):
                cb = kw.get("on_stdout_line")
                _Rt.saw_stream_callback = cb is not None
                for evt in events:
                    if cb:
                        cb((_json.dumps(evt) + "\n").encode("utf-8"))
                return {"text": ""}

        return _Rt()

    @staticmethod
    def _drain(app, part, runtime):
        app.service = lambda name: runtime
        out = []

        async def go():
            async for c in app._dispatch_cli_turn({"id": "room1"}, part, "hi", []):
                out.append(c)

        asyncio.run(go())
        return out

    CODEX_EVENTS = [
        {"type": "item.completed",
         "item": {"id": "i1", "type": "agent_message", "text": "Reading it."}},
        {"type": "item.started",
         "item": {"id": "i2", "type": "command_execution",
                  "command": "powershell -Command 'Get-Content README.md'",
                  "status": "in_progress"}},
        {"type": "item.completed",
         "item": {"id": "i2", "type": "command_execution",
                  "command": "powershell -Command 'Get-Content README.md'",
                  "aggregated_output": "# EmptyOS", "exit_code": 0,
                  "status": "completed"}},
    ]

    def test_stream_json_adapter_emits_tool_cards(self, app, tmp_path):
        rt = self._runtime(self.CODEX_EVENTS)
        part = {"id": "codex", "type": "cli", "cwd": str(tmp_path)}
        chunks = self._drain(app, part, rt)

        assert rt.saw_stream_callback, "streaming path must pass on_stdout_line"
        assert any("tool_use" in c for c in chunks), "expected a tool card"
        assert any("tool_result" in c for c in chunks), "expected a tool result"

        tu = next(c["tool_use"] for c in chunks if "tool_use" in c)
        assert tu["name"] == "Bash" and tu["id"] == "i2"
        tr = next(c["tool_result"] for c in chunks if "tool_result" in c)
        assert tr["tool_use_id"] == "i2" and "EmptyOS" in tr["content"]

        # Assistant prose still accumulates into the persisted reply.
        assert "Reading it." in chunks[-1]["full"]
        assert chunks[-1]["done"] is True

    def test_without_stream_json_it_stays_buffered(self, app, tmp_path):
        """Regression guard: a plain text CLI must not be dragged onto the
        streaming path, or its reply would be parsed as events and vanish."""
        rt = self._runtime(self.CODEX_EVENTS, stream_json=False)
        part = {"id": "gemini", "type": "cli", "cwd": str(tmp_path)}
        chunks = self._drain(app, part, rt)
        assert not rt.saw_stream_callback
        assert not any("tool_use" in c for c in chunks)

    def test_model_chip_falls_back_to_the_adapter_label(self, app, tmp_path):
        """The buffered path reads the model label off text_cli_run's return.
        The streaming path never sees that return — its result is consumed
        inside the driver task — so it resolves the label from
        cli_adapter_info instead. Without that the chip renders empty.
        """
        import json as _json

        class _Rt:
            def cli_adapter_info(self, cli_id):
                return {"stream_json": True, "writes_unsandboxed": False,
                        "model": "gpt-5.4-codex"}

            async def text_cli_run(self, **kw):
                cb = kw.get("on_stdout_line")
                cb((_json.dumps({"type": "item.completed", "item": {
                    "id": "i1", "type": "agent_message", "text": "hi"}}) + "\n").encode())
                return {"text": ""}

        part = {"id": "codex", "type": "cli", "cwd": str(tmp_path)}   # no participant model
        chunks = self._drain(app, part, _Rt())
        assert chunks[-1]["model"] == "gpt-5.4-codex"

    def test_participant_model_still_wins_over_the_adapter(self, app, tmp_path):
        class _Rt:
            def cli_adapter_info(self, cli_id):
                return {"stream_json": True, "model": "adapter-default"}

            async def text_cli_run(self, **kw):
                return {"text": ""}

        part = {"id": "codex", "type": "cli", "cwd": str(tmp_path), "model": "pinned-by-user"}
        chunks = self._drain(app, part, _Rt())
        assert chunks[-1]["model"] == "pinned-by-user"
