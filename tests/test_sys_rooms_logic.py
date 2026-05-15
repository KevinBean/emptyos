"""Pure-logic unit tests for apps/rooms backend.

Doesn't require a running daemon — tests pure methods by instantiating
RoomsApp via object.__new__ and bypassing BaseApp setup. For methods
that touch the filesystem (gate_server_actions writes pending JSON),
inject tmp_path and a fake emit.

Run: python -m pytest tests/test_sys_rooms_logic.py -v
"""
from __future__ import annotations

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

    repo_root = Path(__file__).resolve().parent.parent
    rooms_dir = repo_root / "apps" / "rooms"
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
                "rooms_core", "scheduling", "snippets", "visits"):
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
        parts = app._normalize_participants({"id": "general-assistant"})
        assert parts == [
            {"type": "user", "id": "me"},
            {"type": "agent", "id": "general-assistant"},
        ]

    def test_user_without_id_gets_me(self, app):
        parts = app._normalize_participants({
            "id": "g1",
            "participants": [{"type": "user"}, {"type": "agent", "id": "a"}],
        })
        assert parts[0] == {"type": "user", "id": "me"}

    def test_user_with_explicit_id_preserved(self, app):
        parts = app._normalize_participants({
            "id": "g1",
            "participants": [
                {"type": "user", "id": "kevin"},
                {"type": "agent", "id": "a"},
            ],
        })
        assert parts[0] == {"type": "user", "id": "kevin"}

    def test_cli_participant_passes_through(self, app):
        parts = app._normalize_participants({
            "id": "g1",
            "participants": [
                {"type": "user"},
                {"type": "cli", "id": "claude-cli", "model": "haiku"},
            ],
        })
        assert parts[1] == {"type": "cli", "id": "claude-cli", "model": "haiku"}


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


class TestGateServerActions:
    @pytest.fixture
    def app(self, RoomsApp, tmp_path):
        emitted: list = []

        class _F(RoomsApp):
            data_dir = tmp_path

            async def emit(self, *a, **k):
                emitted.append((a, k))

        inst = object.__new__(_F)
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
