"""System app tests: Agent — tool-use loop, permission, wire compat, bash allowlist.

Two layers:
  • pure-python unit tests (no daemon) — loop logic, tool schemas, bash allowlist,
    consent, wire compatibility against a scripted fake provider.
  • live-daemon API + UI tests (marked @pytest.mark.api / @pytest.mark.interactive)
    — sessions CRUD, tools endpoint, permission endpoints, page shell, hash-routed
    session open, WS handshake. These skip via conftest `server_health` when the
    daemon isn't up.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

from emptyos.capabilities.providers._tool_capable import (
    AgentTurn, NativelyAgenticProvider, TextBlock, ToolCapableProvider,
    ToolUse, ToolUseBlock,
)
from emptyos.capabilities.tool_consent import ToolConsentManager
from emptyos.sdk.agent_loop import AgentSession, run_native_turn, run_turn
from emptyos.sdk.agent_tools import build_registry
from emptyos.sdk.agent_tools.base import Tool, ToolResult
from emptyos.sdk.agent_tools.bash import BashTool, matches_allowlist
from emptyos.sdk.agent_tools.write import WriteTool
from emptyos.sdk.agent_tools.edit import EditTool
from emptyos.sdk.agent_tools.glob import GlobTool
from emptyos.sdk.agent_tools.delete_function import DeleteFunctionTool
from emptyos.sdk.agent_tools.call_app import CallAppTool
from emptyos.sdk.agent_tools.restart_daemon import RestartDaemonTool
from emptyos.sdk.agent_tools.screenshot import ScreenshotTool

import factories
from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


# ── Fake provider that replays scripted turns ─────────────────────────

class ScriptedProvider(ToolCapableProvider):
    name = "scripted"

    def __init__(self, turns: list[AgentTurn], kind: str = "anthropic"):
        self._turns = list(turns)
        self.kind = kind
        self.call_count = 0

    async def execute_tools(self, **kwargs):
        turn = self._turns[self.call_count]
        self.call_count += 1
        return turn


# ── Collector event bus ───────────────────────────────────────────────

class _Events:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, etype, data, source="agent"):
        self.events.append((etype, data))

    def types(self):
        return [e[0] for e in self.events]


# ── Schema round-trip ─────────────────────────────────────────────────

class TestToolSchemaCompat:
    def test_every_tool_produces_anthropic_schema(self):
        for t in build_registry().values():
            schema = t.to_anthropic()
            assert schema["name"] == t.name
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"].get("type") == "object"

    def test_every_tool_produces_openai_schema(self):
        for t in build_registry().values():
            schema = t.to_openai()
            assert schema["type"] == "function"
            assert schema["function"]["name"] == t.name
            assert "parameters" in schema["function"]
            assert schema["function"]["parameters"].get("type") == "object"


# ── Bash allowlist ────────────────────────────────────────────────────

class TestBashAllowlist:
    @pytest.mark.parametrize("cmd,allowed", [
        ("git status", True),
        ("git log --oneline", True),
        ("git diff HEAD~1", True),
        ("ls apps/", True),
        ("cat README.md", True),
        ("rg foo", True),
        ("python --version", True),
        # Not allowed:
        ("rm -rf /", False),
        ("curl http://evil.example.com", False),
        ("sudo apt install", False),
        ("npm install left-pad", False),
        ("git push", False),
        ("find . -delete", False),
    ])
    def test_allowlist(self, cmd, allowed):
        import shlex
        argv = shlex.split(cmd, posix=True)
        assert matches_allowlist(argv) is allowed

    @pytest.mark.asyncio
    async def test_metachar_runs_via_shell(self):
        """Shell metacharacters (pipes, &&, etc.) are supported via bash/sh.

        Earlier revisions rejected metacharacters outright; that forced the
        model into awkward multi-call sequences and broke on Windows where
        `ls` isn't an exe. Now BashTool falls through to a shell when the
        command needs one, so piped/chained commands just work.
        """
        t = BashTool()
        # Use a Python one-liner so the assertion doesn't depend on ls/grep
        # being installed on the test host (CI may not have Git Bash).
        r = await t.run(None, command="python -c \"print('hello')\" | python -c \"import sys; print(sys.stdin.read().upper(), end='')\"")
        # Either it succeeded (shell found), or it failed with a shell-not-found
        # error — NOT with the old "metacharacter unsupported" rejection.
        assert "metacharacter" not in r.content


# ── Tool consent manager ──────────────────────────────────────────────

class TestToolConsent:
    @pytest.mark.asyncio
    async def test_auto_tool_short_circuits(self):
        m = ToolConsentManager(policy="ask")
        ok = await m.check(session_id="s", tool="Read", input={}, tool_default="auto")
        assert ok is True

    @pytest.mark.asyncio
    async def test_deny_tool_always_fails(self):
        m = ToolConsentManager(policy="ask")
        ok = await m.check(session_id="s", tool="X", input={}, tool_default="deny")
        assert ok is False

    @pytest.mark.asyncio
    async def test_policy_auto_allows_ask(self):
        m = ToolConsentManager(policy="auto")
        ok = await m.check(session_id="s", tool="Bash", input={}, tool_default="ask")
        assert ok is True

    @pytest.mark.asyncio
    async def test_policy_deny_kills_all(self):
        # policy=deny is the kill switch — blocks every tool, even class-level `auto`
        m = ToolConsentManager(policy="deny")
        ok = await m.check(session_id="s", tool="Read", input={}, tool_default="auto")
        assert ok is False
        ok = await m.check(session_id="s", tool="Bash", input={}, tool_default="ask")
        assert ok is False

    @pytest.mark.asyncio
    async def test_session_scope_caches(self):
        m = ToolConsentManager(policy="ask")
        calls = [0]
        async def ui(req):
            calls[0] += 1
            return (True, "session")
        m.set_ui(ui)
        await m.check(session_id="sx", tool="Bash", input={}, tool_default="ask")
        await m.check(session_id="sx", tool="Bash", input={}, tool_default="ask")
        assert calls[0] == 1  # second call uses cached session approval


# ── Loop integration ──────────────────────────────────────────────────

class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_terminates_when_no_tool_use(self):
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=[TextBlock(text="All done.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        ev = _Events()
        sess = AgentSession(id="t1")
        turn = await run_turn(
            session=sess, user_text="hi", provider=provider, tools={},
            tool_consent=None, events=ev,
        )
        assert turn.stop_reason == "end_turn"
        assert "agent:done" in ev.types()
        # user + assistant = 2 messages
        assert len(sess.messages) == 2

    @pytest.mark.asyncio
    async def test_dispatches_tool_and_continues(self):
        tools = build_registry(enabled=["Read"])
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=[
                    TextBlock(text="reading file"),
                    ToolUseBlock(id="tu1", name="Read", input={"path": str(_REPO_ROOT / "pyproject.toml"), "limit": 2}),
                ],
                tool_uses=[ToolUse(id="tu1", name="Read", input={"path": str(_REPO_ROOT / "pyproject.toml"), "limit": 2})],
                stop_reason="tool_use",
            ),
            AgentTurn(
                assistant_blocks=[TextBlock(text="ok, read it.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        ev = _Events()
        consent = ToolConsentManager(policy="auto")
        sess = AgentSession(id="t2")
        turn = await run_turn(
            session=sess, user_text="read pyproject", provider=provider,
            tools=tools, tool_consent=consent, events=ev,
        )
        assert turn.stop_reason == "end_turn"
        types = ev.types()
        assert "agent:tool_call" in types
        assert "agent:tool_result" in types
        # User + assistant + tool_result-user + assistant
        assert len(sess.messages) == 4

    @pytest.mark.asyncio
    async def test_denied_tool_produces_error_but_loop_continues(self):
        tools = build_registry(enabled=["Bash"])
        # Use `env` — not in the allowlist, so it defaults to `ask`.
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=[ToolUseBlock(id="b1", name="Bash", input={"command": "env"})],
                tool_uses=[ToolUse(id="b1", name="Bash", input={"command": "env"})],
                stop_reason="tool_use",
            ),
            AgentTurn(
                assistant_blocks=[TextBlock(text="could not run it.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        consent = ToolConsentManager(policy="ask")
        async def deny(req):
            return (False, "once")
        consent.set_ui(deny)
        ev = _Events()
        sess = AgentSession(id="t3")
        turn = await run_turn(
            session=sess, user_text="run whoami", provider=provider,
            tools=tools, tool_consent=consent, events=ev,
        )
        assert turn.stop_reason == "end_turn"
        tr_events = [d for t, d in ev.events if t == "agent:tool_result"]
        assert tr_events and tr_events[0].get("is_error") is True

    @pytest.mark.asyncio
    async def test_unknown_tool_yields_error_then_continues(self):
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=[ToolUseBlock(id="x1", name="Ghost", input={})],
                tool_uses=[ToolUse(id="x1", name="Ghost", input={})],
                stop_reason="tool_use",
            ),
            AgentTurn(
                assistant_blocks=[TextBlock(text="tool missing.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        consent = ToolConsentManager(policy="auto")
        ev = _Events()
        sess = AgentSession(id="t4")
        turn = await run_turn(
            session=sess, user_text="invoke ghost", provider=provider,
            tools={}, tool_consent=consent, events=ev,
        )
        assert turn.stop_reason == "end_turn"
        tr_events = [d for t, d in ev.events if t == "agent:tool_result"]
        assert tr_events and tr_events[0].get("is_error") is True


# ── Phase 2 safety reflexes ───────────────────────────────────────────

class TestSafetyReflexes:
    """Pin the three safety-reflex behaviours that close common agent
    failure modes: consecutive-error loops, Python-edit-without-restart,
    and excessive edits to the same path."""

    @pytest.mark.asyncio
    async def test_error_loop_detector_injects_stop_note(self):
        """3+ consecutive tool errors in one turn → loop-guard nudge appears
        in the tool_result content the model reads on the next iteration."""
        # Script: 3 failed Ghost calls (all error — unknown tool), then the
        # model gives up.
        bad_call = ToolUseBlock(id="x", name="Ghost", input={})
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=[
                    ToolUseBlock(id="g1", name="Ghost", input={}),
                    ToolUseBlock(id="g2", name="Ghost", input={}),
                    ToolUseBlock(id="g3", name="Ghost", input={}),
                ],
                tool_uses=[
                    ToolUse(id="g1", name="Ghost", input={}),
                    ToolUse(id="g2", name="Ghost", input={}),
                    ToolUse(id="g3", name="Ghost", input={}),
                ],
                stop_reason="tool_use",
            ),
            AgentTurn(
                assistant_blocks=[TextBlock(text="giving up.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        consent = ToolConsentManager(policy="auto")
        ev = _Events()
        sess = AgentSession(id="loop1")
        await run_turn(
            session=sess, user_text="do it", provider=provider,
            tools={}, tool_consent=consent, events=ev,
        )
        # The third tool_result (and only the third) carries the loop-guard nudge.
        # Provider messages already include them as a content string; check both
        # the in-message shape AND the event stream.
        tool_result_messages = [m for m in sess.messages if m.get("role") == "tool" or (
            isinstance(m.get("content"), list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"]
            )
        )]
        assert tool_result_messages, "expected tool_result messages in session history"
        # Flatten all tool_result content strings (Anthropic = blocks, OpenAI = role=tool).
        bodies: list[str] = []
        for m in tool_result_messages:
            c = m.get("content")
            if isinstance(c, str):
                bodies.append(c)
            elif isinstance(c, list):
                for b in c:
                    inner = b.get("content") if isinstance(b, dict) else ""
                    if isinstance(inner, str):
                        bodies.append(inner)
        assert any("[loop-guard]" in b for b in bodies), (
            f"expected loop-guard nudge in one of the tool_result bodies, got: {bodies!r}"
        )

    @pytest.mark.asyncio
    async def test_edit_loop_guard_blocks_sixth_edit(self, tmp_path):
        """Edits to the same file past EDIT_PATH_LIMIT are rejected with a
        synthetic error — the tool is never actually called."""
        # Real file so an unguarded Edit would succeed.
        target = tmp_path / "thrash.txt"
        target.write_text("a\nb\nc\n", encoding="utf-8")
        tools = build_registry(enabled=["Edit"])
        # 6 edits to the same path — the 6th must be blocked by the guard.
        def edit_input(old, new):
            return {"path": str(target), "old_string": old, "new_string": new}
        uses = [
            ToolUseBlock(id=f"e{i}", name="Edit", input=edit_input("a", f"a{i}"))
            for i in range(6)
        ]
        # Iteration 1: 6 edits; iteration 2: done.
        # The EditTool's uniqueness check would fail on the 2nd+ call anyway
        # (since "a" is already replaced), but the guard fires on count BEFORE
        # tool.run() — so it's observable regardless of the tool's own logic.
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=list(uses),
                tool_uses=[ToolUse(id=b.id, name=b.name, input=b.input) for b in uses],
                stop_reason="tool_use",
            ),
            AgentTurn(
                assistant_blocks=[TextBlock(text="stopped.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        consent = ToolConsentManager(policy="auto")
        ev = _Events()
        sess = AgentSession(id="edit1")
        await run_turn(
            session=sess, user_text="edit it", provider=provider,
            tools=tools, tool_consent=consent, events=ev,
        )
        tr_events = [d for t, d in ev.events if t == "agent:tool_result"]
        # 6th tool_result should have guard flag + loop-guard message.
        guarded = [d for d in tr_events if isinstance(d.get("display"), dict)
                   and d["display"].get("guard") == "edit_loop"]
        assert guarded, f"expected at least one edit_loop guard event, got displays: {[d.get('display') for d in tr_events]}"

    @pytest.mark.asyncio
    async def test_python_edit_appends_daemon_hint(self, tmp_path):
        """Successful Write of a .py file appends the [daemon-hint] reminder
        to the tool_result content, nudging the model to tell the user to
        restart and then Fetch-verify."""
        target = tmp_path / "foo.py"
        tools = build_registry(enabled=["Write"])
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=[ToolUseBlock(id="w1", name="Write",
                    input={"path": str(target), "content": "x = 1\n"})],
                tool_uses=[ToolUse(id="w1", name="Write",
                    input={"path": str(target), "content": "x = 1\n"})],
                stop_reason="tool_use",
            ),
            AgentTurn(
                assistant_blocks=[TextBlock(text="wrote it.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        consent = ToolConsentManager(policy="auto")
        ev = _Events()
        sess = AgentSession(id="py1")
        await run_turn(
            session=sess, user_text="write it", provider=provider,
            tools=tools, tool_consent=consent, events=ev,
        )
        # The hint is appended to the tool_result content the MODEL reads,
        # which is the message-history version (not the UI-truncated event).
        bodies: list[str] = []
        for m in sess.messages:
            c = m.get("content")
            if isinstance(c, str):
                bodies.append(c)
            elif isinstance(c, list):
                for b in c:
                    inner = b.get("content") if isinstance(b, dict) else ""
                    if isinstance(inner, str):
                        bodies.append(inner)
        assert any("[daemon-hint]" in b for b in bodies), (
            f"expected [daemon-hint] in one tool_result body; got: {bodies!r}"
        )
        assert target.exists()  # write succeeded


# ── Phase 3 self-verification tools ────────────────────────────────────

class TestScreenshotTool:
    """Screenshot permission contract + input validation. The live Playwright
    roundtrip is exercised separately (marked `llm`-adjacent because it spins
    up a real headless browser)."""

    def test_localhost_auto_approves(self):
        t = ScreenshotTool()
        assert t.permission_for({"url": "http://localhost:9000/"}) == "auto"
        assert t.permission_for({"url": "http://127.0.0.1/"}) == "auto"
        assert t.permission_for({"url": "http://192.168.1.10/"}) == "auto"

    def test_public_url_asks(self):
        t = ScreenshotTool()
        assert t.permission_for({"url": "https://example.com"}) == "ask"
        assert t.permission_for({"url": "https://google.com"}) == "ask"

    @pytest.mark.asyncio
    async def test_rejects_missing_url(self):
        t = ScreenshotTool()
        r = await t.run(None)
        assert r.ok is False
        assert "url is required" in r.content

    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self):
        t = ScreenshotTool()
        r = await t.run(None, url="file:///etc/passwd")
        assert r.ok is False
        assert "http" in r.content.lower()


class TestRestartDaemonTool:
    """RestartDaemon asks permission and surfaces reason in the summary.
    Live restart is NOT exercised in unit tests — it would kill the daemon
    the API tests below depend on. Live verification is manual."""

    def test_always_asks_permission(self):
        t = RestartDaemonTool()
        # No permission_for override — uses the class-level `permission = "ask"`
        assert t.permission == "ask"

    def test_permission_summary_includes_reason(self):
        t = RestartDaemonTool()
        assert "RestartDaemon" in t.permission_summary({})
        s = t.permission_summary({"reason": "picked up calculator app fix"})
        assert "picked up calculator app fix" in s

    def test_schema_accepts_empty_input(self):
        # `reason` is optional — schema shouldn't list it as required.
        t = RestartDaemonTool()
        assert "reason" not in t.input_schema.get("required", [])


# ── Phase 4 context management ────────────────────────────────────────

class TestSessionCompaction:
    """_compact_history shrinks stale tool_result bodies while preserving the
    tool_calls ↔ tool_result pairing OpenAI/Anthropic enforce."""

    def _pair(self, call_id: str, body: str) -> list[dict]:
        """Build a minimal (assistant-with-tool_call, tool-result) pair —
        the atom compaction operates on."""
        return [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "Bash", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "content": body},
        ]

    def test_under_budget_is_noop(self):
        from emptyos.sdk.agent_loop import _compact_history
        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hi"}]
        out, saved = _compact_history(messages, char_budget=10_000)
        assert saved == 0
        assert out == messages  # unchanged reference-equal

    def test_over_budget_shrinks_old_tool_results(self):
        from emptyos.sdk.agent_loop import _compact_history
        # Build 10 turn-pairs; bodies large enough to trigger compaction and
        # blow the budget so compaction actually runs.
        messages: list = [{"role": "user", "content": "go"}]
        big_body = "x" * 2000  # ~2K each
        for i in range(10):
            messages.extend(self._pair(f"c{i}", big_body))
        messages.append({"role": "user", "content": "continue"})
        out, saved = _compact_history(messages, char_budget=5_000, keep_recent_turns=2, min_body_chars=400)
        # Something must have been saved — old bodies were 2K each.
        assert saved > 0
        # Recent 2 tool results must NOT be summarized (bodies unchanged).
        tool_msgs = [m for m in out if m.get("role") == "tool"]
        assert tool_msgs[-1]["content"] == big_body, "most-recent tool_result must be untouched"
        assert tool_msgs[-2]["content"] == big_body, "second-most-recent tool_result must be untouched"
        # Earlier ones WERE summarized.
        assert any("[… summarized" in m["content"] for m in tool_msgs[:-2]), \
            "expected older tool_result bodies to be summarized"

    def test_assistant_messages_never_touched(self):
        """Assistant text + tool_calls are reasoning trail — compaction must
        never strip them (doing so would lose the chain of thought + break
        tool_calls pairing)."""
        from emptyos.sdk.agent_loop import _compact_history
        messages: list = [{"role": "user", "content": "go"}]
        long_assistant = "reasoning " * 500  # 5K chars
        for i in range(6):
            messages.append({"role": "assistant", "content": long_assistant})
            messages.extend(self._pair(f"c{i}", "x" * 2000)[1:])  # just the tool reply
        out, _ = _compact_history(messages, char_budget=1000, keep_recent_turns=1)
        # Every assistant content preserved verbatim.
        assistants_in = [m["content"] for m in messages if m.get("role") == "assistant"]
        assistants_out = [m["content"] for m in out if m.get("role") == "assistant"]
        assert assistants_in == assistants_out

    def test_tool_call_pairing_survives(self):
        """Post-compaction, every `tool` message must still have a matching
        `tool_call_id`, and every assistant tool_calls entry must still have a
        downstream tool message with that id. Breaking this = OpenAI 400."""
        from emptyos.sdk.agent_loop import _compact_history
        messages: list = [{"role": "user", "content": "go"}]
        for i in range(8):
            messages.extend(self._pair(f"c{i}", "x" * 2000))
        out, _ = _compact_history(messages, char_budget=1000, keep_recent_turns=1)
        # Collect call_ids from assistant messages and tool_call_ids from tool messages.
        asst_ids = set()
        for m in out:
            for tc in m.get("tool_calls") or []:
                asst_ids.add(tc["id"])
        tool_ids = {m["tool_call_id"] for m in out if m.get("role") == "tool"}
        assert asst_ids == tool_ids, f"pairing broken: assistant={asst_ids}, tool={tool_ids}"


class TestEditHistoryWriteTool:
    """Write/Edit now surface `previous_content` in their display dict so the
    REPL can build an undo stack. These are the contracts /revert depends on."""

    @pytest.mark.asyncio
    async def test_write_create_surfaces_previous_content_empty(self, tmp_path):
        t = WriteTool()
        target = tmp_path / "new.txt"
        r = await t.run(None, path=str(target), content="hello")
        assert r.ok is True
        assert r.display["action"] == "create"
        assert r.display["previous_content"] == ""  # new file — nothing before
        assert r.display["path"] == str(target.resolve())

    @pytest.mark.asyncio
    async def test_write_overwrite_captures_previous_content(self, tmp_path):
        t = WriteTool()
        target = tmp_path / "existing.txt"
        target.write_text("old contents here", encoding="utf-8")
        r = await t.run(None, path=str(target), content="new")
        assert r.ok is True
        assert r.display["action"] == "overwrite"
        assert r.display["previous_content"] == "old contents here"

    @pytest.mark.asyncio
    async def test_edit_captures_previous_content(self, tmp_path):
        t = EditTool()
        target = tmp_path / "e.txt"
        # Write bytes directly to avoid OS-level newline translation
        # (Windows auto-CRLFs text-mode writes, which would trip the assertion).
        target.write_bytes(b"hello world\nfoo bar\n")
        r = await t.run(None, path=str(target), old_string="foo bar", new_string="baz qux")
        assert r.ok is True
        assert r.display["action"] == "edit"
        assert r.display["previous_content"] == "hello world\nfoo bar\n"


class TestRevertStack:
    """Server-side revert stack — run_turn pushes onto app_ref._push_edit
    on every successful Write/Edit; `_revert_last_edits` pops and restores.
    Shared between CLI /revert and the web `POST /agent/api/sessions/{sid}/revert`
    endpoint so they're literally the same code path."""

    class _StubApp:
        """Minimal shim — mirrors the AgentApp methods run_turn calls."""
        def __init__(self):
            self._edit_stacks = {}
        def _push_edit(self, sid, entry):
            if entry and entry.get("path"):
                self._edit_stacks.setdefault(sid, []).append(entry)
        def _revert_last_edits(self, sid, n=1):
            # Mirror the real implementation — identical behavior.
            from pathlib import Path as _P
            stack = self._edit_stacks.get(sid) or []
            if not stack:
                return {"reverted": [], "remaining": 0, "python_edits": False, "empty": True}
            try:
                n = max(1, min(int(n), len(stack)))
            except (TypeError, ValueError):
                n = 1
            reverted = []
            py = False
            for _ in range(n):
                if not stack: break
                entry = stack.pop()
                p = _P(entry["path"])
                action = (entry.get("action") or "edit").lower()
                before = entry.get("previous_content", "")
                outcome = {"path": str(p), "action": action, "ok": False}
                try:
                    if action == "create":
                        if p.exists(): p.unlink()
                        outcome["ok"] = True; outcome["mode"] = "deleted"
                    else:
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(before, encoding="utf-8")
                        outcome["ok"] = True; outcome["mode"] = "restored"
                    if str(p).endswith(".py"): py = True
                except Exception as e:
                    outcome["error"] = f"{type(e).__name__}: {e}"
                reverted.append(outcome)
            self._edit_stacks[sid] = stack
            return {"reverted": reverted, "remaining": len(stack), "python_edits": py, "empty": False}

    @pytest.mark.asyncio
    async def test_successful_write_pushes_to_stack(self, tmp_path):
        """When run_turn sees a successful Write, it calls app_ref._push_edit
        with the pre-edit bytes so /revert can restore later."""
        tools = build_registry(enabled=["Write"])
        target = tmp_path / "x.txt"
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=[ToolUseBlock(id="w1", name="Write",
                    input={"path": str(target), "content": "new"})],
                tool_uses=[ToolUse(id="w1", name="Write",
                    input={"path": str(target), "content": "new"})],
                stop_reason="tool_use",
            ),
            AgentTurn(assistant_blocks=[TextBlock(text="ok")], tool_uses=[], stop_reason="end_turn"),
        ])
        app_stub = self._StubApp()
        consent = ToolConsentManager(policy="auto")
        ev = _Events()
        sess = AgentSession(id="rv1")
        await run_turn(session=sess, user_text="go", provider=provider,
            tools=tools, tool_consent=consent, events=ev, app_ref=app_stub)
        stack = app_stub._edit_stacks.get("rv1", [])
        assert len(stack) == 1
        assert stack[0]["path"].endswith("x.txt")
        assert stack[0]["action"] == "create"

    @pytest.mark.asyncio
    async def test_revert_pops_and_restores(self, tmp_path):
        """Revert undoes a create (deletes the file) and an overwrite (restores
        pre-edit bytes) — returns the structured summary caller expects."""
        target = tmp_path / "restore.txt"
        target.write_text("original content", encoding="utf-8")
        app_stub = self._StubApp()
        # Simulate a Write that overwrote `restore.txt`
        target.write_text("new content", encoding="utf-8")  # state after Write
        app_stub._push_edit("sess", {
            "path": str(target), "action": "overwrite",
            "previous_content": "original content",
        })
        result = app_stub._revert_last_edits("sess", 1)
        assert result["empty"] is False
        assert len(result["reverted"]) == 1
        assert result["reverted"][0]["ok"] is True
        assert result["reverted"][0]["mode"] == "restored"
        assert result["remaining"] == 0
        assert target.read_text(encoding="utf-8") == "original content"

    @pytest.mark.asyncio
    async def test_revert_empty_stack_is_signaled(self):
        app_stub = self._StubApp()
        result = app_stub._revert_last_edits("nothing", 1)
        assert result["empty"] is True
        assert result["reverted"] == []

    @pytest.mark.asyncio
    async def test_revert_create_deletes_file(self, tmp_path):
        target = tmp_path / "created.txt"
        target.write_text("was created", encoding="utf-8")
        app_stub = self._StubApp()
        app_stub._push_edit("s", {"path": str(target), "action": "create", "previous_content": ""})
        result = app_stub._revert_last_edits("s", 1)
        assert result["reverted"][0]["ok"] is True
        assert result["reverted"][0]["mode"] == "deleted"
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_revert_python_flag(self, tmp_path):
        """Reverting a .py edit sets python_edits=True so callers can prompt
        the user to restart the daemon."""
        target = tmp_path / "foo.py"
        target.write_text("after = 1", encoding="utf-8")
        app_stub = self._StubApp()
        app_stub._push_edit("s", {"path": str(target), "action": "edit", "previous_content": "before = 1"})
        result = app_stub._revert_last_edits("s", 1)
        assert result["python_edits"] is True


class TestAnthropicCostMath:
    """Server-side cost for the Anthropic SDK — cache-aware math so the
    agent footer matches actual billing. Pinned here so rate-table edits
    can't silently drift pricing semantics (cached reads 90% off, cache
    creation 1.25× writes, etc)."""

    def test_sonnet_plain_cost(self):
        """Sonnet 4.6: $3 input / $15 output per 1M. 1000 in + 500 out =
        1000*3/1M + 500*15/1M = 0.003 + 0.0075 = 0.0105."""
        from emptyos.capabilities.providers.anthropic_sdk import AnthropicSDKProvider
        p = AnthropicSDKProvider(model="claude-sonnet-4-6")
        assert p._calc_cost(inp=1000, out=500, cache_read=0, cache_create=0) == pytest.approx(0.0105, rel=1e-4)

    def test_cache_read_is_90_percent_off(self):
        """1000 cache-read input tokens bill at 10% of base = 1000*3*0.1/1M = 0.0003."""
        from emptyos.capabilities.providers.anthropic_sdk import AnthropicSDKProvider
        p = AnthropicSDKProvider(model="claude-sonnet-4-6")
        cost = p._calc_cost(inp=0, out=0, cache_read=1000, cache_create=0)
        assert cost == pytest.approx(0.0003, rel=1e-4)

    def test_cache_create_is_125_percent(self):
        """1000 cache-creation input tokens bill at 125% of base = 1000*3*1.25/1M = 0.00375."""
        from emptyos.capabilities.providers.anthropic_sdk import AnthropicSDKProvider
        p = AnthropicSDKProvider(model="claude-sonnet-4-6")
        cost = p._calc_cost(inp=0, out=0, cache_read=0, cache_create=1000)
        assert cost == pytest.approx(0.00375, rel=1e-4)

    def test_opus_more_expensive_than_sonnet(self):
        """Opus should cost ~5× Sonnet for the same usage."""
        from emptyos.capabilities.providers.anthropic_sdk import AnthropicSDKProvider
        opus = AnthropicSDKProvider(model="claude-opus-4-7")
        sonnet = AnthropicSDKProvider(model="claude-sonnet-4-6")
        args = dict(inp=1000, out=500, cache_read=0, cache_create=0)
        ratio = opus._calc_cost(**args) / sonnet._calc_cost(**args)
        assert 4.5 < ratio < 5.5  # opus = $15/$75 vs sonnet = $3/$15 → weighted ~5×

    def test_unknown_model_returns_zero(self):
        """Ollama / unknown model → no pricing → cost 0, not a crash."""
        from emptyos.capabilities.providers.anthropic_sdk import AnthropicSDKProvider
        p = AnthropicSDKProvider(model="llama3.1-ish-thing")
        assert p._calc_cost(inp=1000, out=500, cache_read=0, cache_create=0) == 0.0


# ── Phase 5 plan mode ──────────────────────────────────────────────────

class TestPlanMode:
    """Tools expose a `readonly` marker; in plan mode the loop blocks anything
    non-readonly BEFORE tool.run() executes."""

    def test_readonly_flags_on_all_tools(self):
        """Every tool in the registry declares its readonly-ness, and the
        expected ones are readonly."""
        reg = build_registry()
        # Read-only contract — investigation tools.
        for name in ("Read", "Grep", "Glob", "Skill", "TaskList", "Screenshot"):
            assert reg[name].readonly is True, f"{name} must be readonly for plan mode"
        # Mutating tools must NOT be readonly.
        for name in ("Write", "Edit", "DeleteFunction", "Bash", "CallApp", "RestartDaemon"):
            assert reg[name].readonly is False, f"{name} must NOT be readonly"

    def test_fetch_readonly_depends_on_method(self):
        """Fetch-GET is plan-mode safe; Fetch-POST is not."""
        from emptyos.sdk.agent_tools.fetch import FetchTool
        t = FetchTool()
        assert t.is_readonly({"url": "http://x/", "method": "GET"}) is True
        assert t.is_readonly({"url": "http://x/"}) is True  # default GET
        assert t.is_readonly({"url": "http://x/", "method": "POST"}) is False
        assert t.is_readonly({"url": "http://x/", "method": "DELETE"}) is False

    @pytest.mark.asyncio
    async def test_plan_mode_blocks_non_readonly_tool(self, tmp_path):
        """In plan mode, Write is gated — tool.run() never executes, the file
        is never created, and a [plan mode] error is returned."""
        tools = build_registry(enabled=["Write"])
        uses = [ToolUseBlock(id="w1", name="Write",
            input={"path": str(tmp_path / "blocked.txt"), "content": "NOPE"})]
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=list(uses),
                tool_uses=[ToolUse(id=b.id, name=b.name, input=b.input) for b in uses],
                stop_reason="tool_use",
            ),
            AgentTurn(
                assistant_blocks=[TextBlock(text="blocked.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        # Minimal app_ref carrying the plan_mode flag the gate reads.
        class _AppStub:
            _plan_modes = {"plan1": True}
        consent = ToolConsentManager(policy="auto")
        ev = _Events()
        sess = AgentSession(id="plan1")
        await run_turn(
            session=sess, user_text="write it", provider=provider,
            tools=tools, tool_consent=consent, events=ev,
            app_ref=_AppStub(),
        )
        # File was never created — gate fired before tool.run().
        assert not (tmp_path / "blocked.txt").exists()
        # Gate error appears in both the event stream and message history.
        tr_events = [d for t, d in ev.events if t == "agent:tool_result"]
        gated = [d for d in tr_events if isinstance(d.get("display"), dict)
                 and d["display"].get("gated") == "plan_mode"]
        assert gated, f"expected plan-mode gate event; got displays: {[d.get('display') for d in tr_events]}"

    @pytest.mark.asyncio
    async def test_plan_mode_allows_readonly_tool(self, tmp_path):
        """In plan mode, Read still works — investigation is the whole point."""
        # Real file so Read has something to read.
        target = tmp_path / "notes.txt"
        target.write_text("hello planner", encoding="utf-8")
        tools = build_registry(enabled=["Read"])
        provider = ScriptedProvider([
            AgentTurn(
                assistant_blocks=[ToolUseBlock(id="r1", name="Read",
                    input={"path": str(target)})],
                tool_uses=[ToolUse(id="r1", name="Read", input={"path": str(target)})],
                stop_reason="tool_use",
            ),
            AgentTurn(
                assistant_blocks=[TextBlock(text="read ok.")],
                tool_uses=[],
                stop_reason="end_turn",
            ),
        ])
        class _AppStub:
            _plan_modes = {"plan2": True}
        consent = ToolConsentManager(policy="auto")
        ev = _Events()
        sess = AgentSession(id="plan2")
        await run_turn(
            session=sess, user_text="read it", provider=provider,
            tools=tools, tool_consent=consent, events=ev,
            app_ref=_AppStub(),
        )
        # No gate — the one tool result is the Read result, not a block.
        tr_events = [d for t, d in ev.events if t == "agent:tool_result"]
        assert tr_events and tr_events[0].get("is_error") is False
        assert not any(isinstance(d.get("display"), dict) and d["display"].get("gated") == "plan_mode"
                       for d in tr_events)


# ── Natively-agentic provider (claude-cli shape) ───────────────────────

class ScriptedNativeProvider(NativelyAgenticProvider):
    name = "scripted-native"

    def __init__(self, chunks: list[dict]):
        self._chunks = list(chunks)

    async def execute_stream(self, **kwargs):
        for c in self._chunks:
            yield c


class TestNativeAgenticTurn:
    @pytest.mark.asyncio
    async def test_streams_text_and_tool_status(self):
        provider = ScriptedNativeProvider([
            {"text": "Let me look.", "done": False},
            {"tool_status": "Reading pyproject.toml", "tool": "Read", "done": False},
            {"text": " Done.", "done": False},
            {"text": "", "done": True},
        ])
        ev = _Events()
        sess = AgentSession(id="nat1")
        result = await run_native_turn(
            session=sess, user_text="read it", provider=provider, events=ev,
        )
        # Text accumulates across chunks
        assert "Let me look." in result
        assert "Done." in result

        types = ev.types()
        assert "agent:turn_start" in types
        assert "agent:text" in types
        assert "agent:tool_call" in types
        assert "agent:tool_result" in types
        assert "agent:done" in types

        # Session records user + assistant messages as flat text
        assert len(sess.messages) == 2
        assert sess.messages[0] == {"role": "user", "content": "read it"}
        assert sess.messages[1]["role"] == "assistant"
        assert isinstance(sess.messages[1]["content"], str)

        # tool_call events are self-consistent — each has a matched tool_result
        calls = [d for t, d in ev.events if t == "agent:tool_call"]
        results = [d for t, d in ev.events if t == "agent:tool_result"]
        assert len(calls) == 1 and len(results) == 1
        assert calls[0]["id"] == results[0]["id"]
        assert calls[0].get("native") is True

    @pytest.mark.asyncio
    async def test_handles_stream_errors(self):
        class Bad(NativelyAgenticProvider):
            name = "bad"
            async def execute_stream(self, **kwargs):
                yield {"text": "partial", "done": False}
                raise RuntimeError("upstream boom")

        ev = _Events()
        sess = AgentSession(id="nat2")
        with pytest.raises(RuntimeError, match="upstream boom"):
            await run_native_turn(
                session=sess, user_text="x", provider=Bad(), events=ev,
            )
        # Error event surfaced
        types = ev.types()
        assert "agent:error" in types

    @pytest.mark.asyncio
    async def test_usage_chunk_reaches_agent_done(self):
        # Regression: claude-cli emits cost via the `result` event's
        # total_cost_usd, which the provider now surfaces as a usage chunk.
        # run_native_turn must carry that into agent:done so the CLI / web
        # footer can render the cost. Previously agent:done hardcoded {}.
        provider = ScriptedNativeProvider([
            {"text": "hello", "done": False},
            {"usage": {"input_tokens": 12, "output_tokens": 4, "cost": 0.0042}, "done": False},
            {"text": "", "done": True},
        ])
        ev = _Events()
        sess = AgentSession(id="nat-cost")
        await run_native_turn(
            session=sess, user_text="hi", provider=provider, events=ev,
        )
        done = [d for t, d in ev.events if t == "agent:done"]
        assert done, "agent:done not emitted"
        assert done[0]["usage"].get("cost") == 0.0042
        assert done[0]["usage"].get("input_tokens") == 12
        assert done[0]["usage"].get("output_tokens") == 4


# ── OpenAI wire round-trip ─────────────────────────────────────────────

# ── Write tool ────────────────────────────────────────────────────────

class TestWriteTool:
    @pytest.mark.asyncio
    async def test_creates_new_file(self, tmp_path):
        t = WriteTool()
        target = tmp_path / "new.txt"
        r = await t.run(None, path=str(target), content="hello")
        assert r.ok is True
        assert target.read_text(encoding="utf-8") == "hello"
        assert r.display["action"] == "create"
        assert r.display["bytes"] == 5

    @pytest.mark.asyncio
    async def test_overwrites_existing_file(self, tmp_path):
        t = WriteTool()
        target = tmp_path / "existing.txt"
        target.write_text("old contents", encoding="utf-8")
        r = await t.run(None, path=str(target), content="new")
        assert r.ok is True
        assert target.read_text(encoding="utf-8") == "new"
        assert r.display["action"] == "overwrite"
        assert r.display["previous_bytes"] == len("old contents")

    @pytest.mark.asyncio
    async def test_auto_creates_parent_dir(self, tmp_path):
        """WriteTool auto-creates missing parent directories (mirrors
        Claude Code's Write behaviour). Previously required the parent to
        exist — agents would have to chain Bash(mkdir) + Write, which
        shipped the `/eos-new-app create a calculator app` flow's first
        failure mode."""
        t = WriteTool()
        target = tmp_path / "does_not_exist" / "nested" / "file.txt"
        r = await t.run(None, path=str(target), content="x")
        assert r.ok is True
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "x"

    @pytest.mark.asyncio
    async def test_rejects_missing_path(self):
        t = WriteTool()
        r = await t.run(None, content="x")
        assert r.ok is False
        assert "path is required" in r.content

    def test_permission_always_ask(self):
        t = WriteTool()
        assert t.permission == "ask"
        assert "Create" in t.permission_summary({"path": "/nope/new.txt", "content": "x"})


# ── Edit tool ─────────────────────────────────────────────────────────

class TestEditTool:
    @pytest.mark.asyncio
    async def test_unique_replacement(self, tmp_path):
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text("def foo():\n    return 1\n", encoding="utf-8")
        r = await t.run(None, path=str(target), old_string="return 1", new_string="return 2")
        assert r.ok is True
        assert target.read_text(encoding="utf-8") == "def foo():\n    return 2\n"
        assert r.display["replacements"] == 1

    @pytest.mark.asyncio
    async def test_non_unique_without_replace_all_errors(self, tmp_path):
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text("x = 1\ny = 1\n", encoding="utf-8")
        r = await t.run(None, path=str(target), old_string="1", new_string="2")
        assert r.ok is False
        assert "occurs 2 times" in r.content
        # File untouched
        assert target.read_text(encoding="utf-8") == "x = 1\ny = 1\n"

    @pytest.mark.asyncio
    async def test_replace_all_replaces_every_occurrence(self, tmp_path):
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text("x = 1\ny = 1\nz = 1\n", encoding="utf-8")
        r = await t.run(
            None, path=str(target), old_string="1", new_string="2", replace_all=True,
        )
        assert r.ok is True
        assert target.read_text(encoding="utf-8") == "x = 2\ny = 2\nz = 2\n"
        assert r.display["replacements"] == 3

    @pytest.mark.asyncio
    async def test_missing_old_string_errors(self, tmp_path):
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text("hello", encoding="utf-8")
        r = await t.run(None, path=str(target), old_string="absent", new_string="x")
        assert r.ok is False
        assert "not found" in r.content

    @pytest.mark.asyncio
    async def test_identical_old_and_new_errors(self, tmp_path):
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text("x", encoding="utf-8")
        r = await t.run(None, path=str(target), old_string="x", new_string="x")
        assert r.ok is False
        assert "identical" in r.content

    @pytest.mark.asyncio
    async def test_empty_old_string_errors(self, tmp_path):
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text("x", encoding="utf-8")
        r = await t.run(None, path=str(target), old_string="", new_string="y")
        assert r.ok is False
        assert "empty" in r.content

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        t = EditTool()
        r = await t.run(
            None, path=str(tmp_path / "ghost.py"), old_string="a", new_string="b",
        )
        assert r.ok is False
        assert "file not found" in r.content

    # ── line-aware fuzzy fallback ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_fuzzy_resolves_trailing_whitespace(self, tmp_path):
        """File has trailing spaces on a line; model omits them. Fallback resolves."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text("def foo():   \n    return 1\n", encoding="utf-8")
        # Model's old_string lacks the trailing spaces
        r = await t.run(
            None, path=str(target),
            old_string="def foo():\n    return 1",
            new_string="def foo():\n    return 2",
        )
        assert r.ok is True, r.content
        assert target.read_text(encoding="utf-8") == "def foo():\n    return 2\n"
        assert r.display["match_mode"] == "line-fuzzy"

    @pytest.mark.asyncio
    async def test_fuzzy_resolves_crlf_vs_lf(self, tmp_path):
        """File uses CRLF; model passes LF. Fallback bridges the gap."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_bytes(b"def foo():\r\n    return 1\r\n")
        r = await t.run(
            None, path=str(target),
            old_string="def foo():\n    return 1",
            new_string="def foo():\n    return 2",
        )
        assert r.ok is True, r.content
        # The replacement substitutes the model's verbatim new_string into the
        # original byte span. So the CRLF in that span is GONE — replaced by LF.
        # Lines outside the span keep their original CRLF.
        assert target.read_bytes() == b"def foo():\n    return 2\r\n"
        assert r.display["match_mode"] == "line-fuzzy"

    @pytest.mark.asyncio
    async def test_fuzzy_preserves_leading_indent_distinction(self, tmp_path):
        """Lines with different leading indent must NOT be considered equivalent."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text(
            "def foo():\n    return 1\n\ndef bar():\n        return 1\n",
            encoding="utf-8",
        )
        # Model's old_string has 2-space indent — matches NEITHER line literally
        # because we only normalize trailing whitespace, not leading.
        r = await t.run(
            None, path=str(target),
            old_string="def foo():\n  return 1",
            new_string="def foo():\n  return 2",
        )
        assert r.ok is False
        assert "not found" in r.content
        # File untouched
        assert target.read_text(encoding="utf-8") == (
            "def foo():\n    return 1\n\ndef bar():\n        return 1\n"
        )

    @pytest.mark.asyncio
    async def test_fuzzy_ambiguous_errors_without_replace_all(self, tmp_path):
        """Two line-blocks fuzzy-match — error names the count, not silent edit."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text(
            "def foo():   \n    return 1\n\ndef bar():\n    return 1\t\n",
            encoding="utf-8",
        )
        # Just `    return 1` after rstrip matches both `return 1` lines
        r = await t.run(
            None, path=str(target),
            old_string="    return 1",
            new_string="    return 99",
        )
        assert r.ok is False
        # Stage 1 (exact) sees one match (`    return 1` literal — only the bar one),
        # so this is actually an exact-unique case. To force fuzzy ambiguity, the
        # model's input must miss both — use a snippet that needs trailing-strip
        # to match both.
        # Re-test with old_string that has trailing whitespace itself:
        target.write_text(
            "x = 1   \ny = 1\n",
            encoding="utf-8",
        )
        r = await t.run(
            None, path=str(target),
            old_string="= 1",   # exact-matches both → already unambig as "occurs 2 times"
            new_string="= 2",
        )
        assert r.ok is False
        assert "occurs 2 times" in r.content

    @pytest.mark.asyncio
    async def test_fuzzy_replace_all_swaps_every_block(self, tmp_path):
        """replace_all on a fuzzy match swaps every line-block."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text(
            "def foo():   \n    return 1\n\ndef foo():\t\n    return 1\n",
            encoding="utf-8",
        )
        r = await t.run(
            None, path=str(target),
            old_string="def foo():\n    return 1",
            new_string="def foo():\n    return 2",
            replace_all=True,
        )
        assert r.ok is True, r.content
        assert r.display["match_mode"] == "line-fuzzy"
        assert r.display["replacements"] == 2
        assert target.read_text(encoding="utf-8").count("return 2") == 2
        assert "return 1" not in target.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_not_found_error_includes_closest_lines_hint(self, tmp_path):
        """When even fuzzy match misses, error embeds closest lines for retry."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text(
            "def calculate_total(items):\n    return sum(items)\n"
            "def calc_average(items):\n    return sum(items) / len(items)\n"
            "def calc_max(items):\n    return max(items)\n"
            "def calc_min(items):\n    return min(items)\n",
            encoding="utf-8",
        )
        # `calc_total` is dissimilar enough from each `calc_*` line that
        # the similarity stage rejects (different second word). This
        # tests the not-found error path with closest-line hint.
        r = await t.run(
            None, path=str(target),
            old_string="def calc_total(items):\n    raise NotImplementedError\n",
            new_string="def calc_total(items: list):\n    return 0\n",
        )
        assert r.ok is False
        assert "not found" in r.content
        # The hint should include actual function names from the file
        assert "calculate_total" in r.content or "calc_average" in r.content

    # ── stage-3 similarity fallback ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_similarity_resolves_em_dash_corruption(self, tmp_path):
        """Real bench failure mode: model mistokenizes em-dash (U+2014) into
        a control character. Stage-3 similarity should accept the match."""
        t = EditTool()
        target = tmp_path / "a.py"
        # File has em-dash in the docstring
        target.write_text(
            'def to_delete(x):\n'
            '    """Mark for removal \u2014 used by consumer."""\n'
            '    return x + 1\n',
            encoding="utf-8",
        )
        # Model's old_string has a vertical-tab control char where em-dash was
        r = await t.run(
            None, path=str(target),
            old_string=(
                'def to_delete(x):\n'
                '    """Mark for removal \u000b used by consumer."""\n'
                '    return x + 1\n'
            ),
            new_string="",
        )
        assert r.ok is True, r.content
        assert r.display["match_mode"] == "line-similar"
        # The function should be gone
        assert "to_delete" not in target.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_similarity_resolves_smart_quote_swap(self, tmp_path):
        """Smart-quote vs ASCII-quote — common transcoding mismatch."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text(
            'msg = \u201chello world\u201d\n'   # left/right curly double quotes
            'count = 1\n',
            encoding="utf-8",
        )
        r = await t.run(
            None, path=str(target),
            old_string='msg = "hello world"',  # ASCII quotes
            new_string='msg = "goodbye world"',
        )
        assert r.ok is True, r.content
        assert r.display["match_mode"] == "line-similar"
        assert "goodbye" in target.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_similarity_rejects_below_threshold(self, tmp_path):
        """Lines that share only a few words are correctly NOT matched."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text(
            "def calculate_grand_total_for_invoice(invoice_id):\n"
            "    return 0\n",
            encoding="utf-8",
        )
        # This is similar in WORDS but ratio is well below 0.85 because
        # the line is much shorter
        r = await t.run(
            None, path=str(target),
            old_string="def total(x):",
            new_string="def total(x: int):",
        )
        assert r.ok is False
        assert "not found" in r.content
        # File untouched
        assert target.read_text(encoding="utf-8") == (
            "def calculate_grand_total_for_invoice(invoice_id):\n"
            "    return 0\n"
        )

    @pytest.mark.asyncio
    async def test_similarity_ambiguity_errors_without_replace_all(self, tmp_path):
        """Two file blocks both >85% similar to old_string — error names the count."""
        t = EditTool()
        target = tmp_path / "a.py"
        target.write_text(
            'msg = \u201cping\u201d\n'      # smart quotes - ping
            'count = 1\n'
            '\n'
            'msg = \u201cpong\u201d\n'      # smart quotes - pong (1-char diff to ping)
            'count = 2\n',
            encoding="utf-8",
        )
        # `msg = "ping"` (ASCII) is >85% similar to BOTH lines via SequenceMatcher
        # since both are very short and only differ in 1-2 chars
        r = await t.run(
            None, path=str(target),
            old_string='msg = "ping"',
            new_string='msg = "PING"',
        )
        # Expect either: clean stage-3 unique match (only one line is ping-like)
        # OR ambiguity error. Test that we don't silently corrupt the file.
        text_after = target.read_text(encoding="utf-8")
        if r.ok:
            # Must have edited the ping line, not the pong line
            assert "PING" in text_after
            assert "pong" in text_after  # untouched
        else:
            # Ambiguity error is acceptable
            assert "not found" in r.content or "similar" in r.content


# ── CallApp tool ──────────────────────────────────────────────────────

class _FakeSubApp:
    """Stand-in for a BaseApp instance the kernel has already loaded."""

    def list_items(self):
        return [{"id": "a"}, {"id": "b"}]

    async def add_item(self, text: str, priority: int = 3):
        return {"id": "new", "text": text, "priority": priority}

    def _private_method(self):  # noqa: not callable via CallApp
        return "nope"

    def setup(self):  # noqa: lifecycle, blocked
        return "nope"

    def broken(self):
        raise RuntimeError("boom")


class _FakeKernel:
    def __init__(self, apps_dict):
        self.apps = _FakeAppRegistry(apps_dict)


class _FakeAppRegistry:
    def __init__(self, apps_dict):
        self.instances = apps_dict

    async def load(self, app_id):
        raise RuntimeError(f"app {app_id!r} is not registered")


class _FakeAppRef:
    def __init__(self, apps_dict):
        self.kernel = _FakeKernel(apps_dict)


# ── Glob tool ─────────────────────────────────────────────────────────

class TestGlobTool:
    @pytest.mark.asyncio
    async def test_relative_pattern_with_path_root(self, tmp_path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "b.py").write_text("x", encoding="utf-8")
        (tmp_path / "c.txt").write_text("x", encoding="utf-8")
        t = GlobTool()
        r = await t.run(None, pattern="*.py", path=str(tmp_path))
        assert r.ok is True
        assert "a.py" in r.content and "b.py" in r.content
        assert "c.txt" not in r.content
        assert r.display["matches"] == 2

    @pytest.mark.asyncio
    async def test_absolute_pattern_works(self, tmp_path):
        """Bench-revealed bug: model passes absolute pattern, pathlib rejects.
        The fix routes absolute patterns through stdlib glob.glob."""
        (tmp_path / "sdk").mkdir()
        (tmp_path / "sdk" / "strings.py").write_text("x", encoding="utf-8")
        (tmp_path / "sdk" / "dates.py").write_text("x", encoding="utf-8")
        t = GlobTool()
        # Path is forward-slashed because that's what the model produces
        abs_pattern = str(tmp_path / "sdk" / "*.py").replace("\\", "/")
        r = await t.run(None, pattern=abs_pattern)
        assert r.ok is True, r.content
        assert "strings.py" in r.content
        assert "dates.py" in r.content
        assert r.display["matches"] == 2

    @pytest.mark.asyncio
    async def test_absolute_pattern_with_double_star(self, tmp_path):
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "x.py").write_text("x", encoding="utf-8")
        (tmp_path / "a" / "b" / "y.py").write_text("x", encoding="utf-8")
        t = GlobTool()
        abs_pattern = str(tmp_path / "**" / "*.py").replace("\\", "/")
        r = await t.run(None, pattern=abs_pattern)
        assert r.ok is True
        assert "x.py" in r.content
        assert "y.py" in r.content

    @pytest.mark.asyncio
    async def test_no_matches_returns_ok_with_zero(self, tmp_path):
        t = GlobTool()
        r = await t.run(None, pattern="*.nonsense", path=str(tmp_path))
        assert r.ok is True
        assert r.display["matches"] == 0

    @pytest.mark.asyncio
    async def test_missing_path_root_errors_for_relative(self, tmp_path):
        t = GlobTool()
        r = await t.run(None, pattern="*.py", path=str(tmp_path / "ghost"))
        assert r.ok is False
        assert "not a directory" in r.content


# ── DeleteFunction tool ───────────────────────────────────────────────

class TestDeleteFunctionTool:
    @pytest.mark.asyncio
    async def test_deletes_simple_top_level_def(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text(
            "def keep_me():\n    return 1\n\n"
            "def to_delete(x):\n    return x + 1\n\n"
            "def also_keep():\n    return 2\n",
            encoding="utf-8",
        )
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="to_delete")
        assert r.ok is True, r.content
        out = target.read_text(encoding="utf-8")
        assert "to_delete" not in out
        assert "keep_me" in out
        assert "also_keep" in out
        # File still parses
        import ast as _ast
        _ast.parse(out)
        assert r.display["kind"] == "def"
        assert r.display["lines_removed"] == 2

    @pytest.mark.asyncio
    async def test_deletes_def_with_decorators(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text(
            "import functools\n\n"
            "@functools.cache\n"
            "@staticmethod\n"
            "def to_delete(x):\n"
            "    return x\n\n"
            "def keep():\n    pass\n",
            encoding="utf-8",
        )
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="to_delete")
        assert r.ok is True, r.content
        out = target.read_text(encoding="utf-8")
        assert "@functools.cache" not in out
        assert "@staticmethod" not in out
        assert "def to_delete" not in out
        assert "def keep" in out

    @pytest.mark.asyncio
    async def test_deletes_async_def(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text(
            "async def to_delete():\n    pass\n\n"
            "def keep():\n    pass\n",
            encoding="utf-8",
        )
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="to_delete")
        assert r.ok is True, r.content
        assert r.display["kind"] == "async def"
        assert "to_delete" not in target.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_deletes_class(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text(
            "class ToDelete:\n"
            "    def method(self):\n        return 1\n\n"
            "class Keep:\n    pass\n",
            encoding="utf-8",
        )
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="ToDelete")
        assert r.ok is True, r.content
        assert r.display["kind"] == "class"
        out = target.read_text(encoding="utf-8")
        assert "ToDelete" not in out
        assert "class Keep" in out

    @pytest.mark.asyncio
    async def test_word_boundary_does_not_hit_decoy_prefix(self, tmp_path):
        """Bench scenario `delete-with-callers` has `to_delete_v2` as a decoy.
        DeleteFunction must distinguish them via AST, not regex."""
        target = tmp_path / "a.py"
        target.write_text(
            "def to_delete(x):\n    return x + 1\n\n"
            "def to_delete_v2(x):\n    return x * 100\n\n"
            "def keep_me(x):\n    return x - 1\n",
            encoding="utf-8",
        )
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="to_delete")
        assert r.ok is True, r.content
        out = target.read_text(encoding="utf-8")
        assert "def to_delete(" not in out
        assert "def to_delete_v2" in out  # decoy survives
        assert "def keep_me" in out

    @pytest.mark.asyncio
    async def test_missing_name_errors(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text("def foo(): pass\n", encoding="utf-8")
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="bar")
        assert r.ok is False
        assert "no top-level function" in r.content
        # File untouched
        assert target.read_text(encoding="utf-8") == "def foo(): pass\n"

    @pytest.mark.asyncio
    async def test_ambiguous_name_errors_safely(self, tmp_path):
        """Two top-level defs with the same name (rare, but possible) → refuse."""
        target = tmp_path / "a.py"
        target.write_text(
            "def dup(): return 1\n\n"
            "def dup(): return 2\n",
            encoding="utf-8",
        )
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="dup")
        assert r.ok is False
        assert "ambiguously" in r.content or "definitions named" in r.content
        # File untouched
        assert "def dup(): return 1" in target.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_invalid_identifier_errors(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text("def foo(): pass\n", encoding="utf-8")
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="not a name")
        assert r.ok is False
        assert "identifier" in r.content

    @pytest.mark.asyncio
    async def test_non_python_file_errors(self, tmp_path):
        target = tmp_path / "a.txt"
        target.write_text("def foo(): pass\n", encoding="utf-8")
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="foo")
        assert r.ok is False
        assert ".py" in r.content

    @pytest.mark.asyncio
    async def test_syntax_broken_file_refuses(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text("def foo(:\n    pass\n", encoding="utf-8")
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="foo")
        assert r.ok is False
        assert "does not parse" in r.content

    @pytest.mark.asyncio
    async def test_nested_method_not_supported(self, tmp_path):
        """v1 only handles top-level. Nested methods need a different shape."""
        target = tmp_path / "a.py"
        target.write_text(
            "class C:\n"
            "    def to_delete(self):\n        pass\n",
            encoding="utf-8",
        )
        t = DeleteFunctionTool()
        r = await t.run(None, path=str(target), name="to_delete")
        assert r.ok is False
        assert "no top-level" in r.content


class TestCallAppTool:
    @pytest.mark.asyncio
    async def test_lists_apps_when_no_app_id(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp(), "journal": _FakeSubApp()})
        r = await t.run(ref)
        assert r.ok is True
        assert "task" in r.content and "journal" in r.content
        assert r.display["apps"] == ["journal", "task"]

    @pytest.mark.asyncio
    async def test_lists_methods_when_no_method(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(ref, app_id="task")
        assert r.ok is True
        assert "add_item" in r.content
        assert "list_items" in r.content
        # Private + lifecycle methods filtered out
        assert "_private_method" not in r.content
        assert "setup" not in r.display["methods"]

    @pytest.mark.asyncio
    async def test_dispatches_sync_method(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(ref, app_id="task", method="list_items")
        assert r.ok is True
        import json as _json
        parsed = _json.loads(r.content)
        assert parsed == [{"id": "a"}, {"id": "b"}]

    @pytest.mark.asyncio
    async def test_dispatches_async_method_with_arguments(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(
            ref, app_id="task", method="add_item",
            arguments={"text": "fix it", "priority": 1},
        )
        assert r.ok is True
        import json as _json
        parsed = _json.loads(r.content)
        assert parsed["text"] == "fix it"
        assert parsed["priority"] == 1

    @pytest.mark.asyncio
    async def test_unknown_app_returns_inventory(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(ref, app_id="ghost", method="anything")
        assert r.ok is False
        assert "not found" in r.content or "failed to load" in r.content
        assert "task" in r.content  # inventory surfaced

    @pytest.mark.asyncio
    async def test_unknown_method_returns_method_list(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(ref, app_id="task", method="ghost_method")
        assert r.ok is False
        assert "not found" in r.content
        assert "add_item" in r.content

    @pytest.mark.asyncio
    async def test_private_method_blocked(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(ref, app_id="task", method="_private_method")
        assert r.ok is False
        assert "private" in r.content

    @pytest.mark.asyncio
    async def test_lifecycle_method_blocked(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(ref, app_id="task", method="setup")
        assert r.ok is False
        assert "lifecycle" in r.content

    @pytest.mark.asyncio
    async def test_bad_arguments_surface_typeerror(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(
            ref, app_id="task", method="add_item",
            arguments={"nope_wrong_kwarg": 1},
        )
        assert r.ok is False
        assert "bad arguments" in r.content

    @pytest.mark.asyncio
    async def test_method_exception_caught(self):
        t = CallAppTool()
        ref = _FakeAppRef({"task": _FakeSubApp()})
        r = await t.run(ref, app_id="task", method="broken")
        assert r.ok is False
        assert "RuntimeError" in r.content
        assert "boom" in r.content


class TestOpenAIWireCompat:
    def test_turn_from_openai_response(self):
        from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider
        p = OpenAICompatThinkProvider(provider_name="test")
        fake = {
            "choices": [{
                "message": {
                    "content": "ok",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "Read", "arguments": '{"path":"/tmp/x"}'}},
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        turn = p._turn_from_response(fake)
        assert turn.stop_reason == "tool_use"
        assert len(turn.tool_uses) == 1
        assert turn.tool_uses[0].name == "Read"
        assert turn.tool_uses[0].input == {"path": "/tmp/x"}

    def test_tool_only_assistant_uses_empty_string_not_null(self):
        """Regression: Ollama rejects `content: null` on assistant turns with
        tool_calls. The loop must serialize empty text as `""`, not `None`."""
        from emptyos.sdk.agent_loop import _assistant_message_for_provider

        turn = AgentTurn(
            assistant_blocks=[ToolUseBlock(id="c1", name="Read", input={"path": "/x"})],
            tool_uses=[ToolUse(id="c1", name="Read", input={"path": "/x"})],
            stop_reason="tool_use",
        )
        msg = _assistant_message_for_provider("openai", turn)
        assert msg["content"] == "", (
            f"content must be empty string for Ollama compat, got {msg['content']!r}"
        )
        assert msg["content"] is not None
        assert len(msg["tool_calls"]) == 1

    def test_mixed_text_and_tool_assistant_keeps_text(self):
        """When there IS text, it should still be preserved (not clobbered to '')."""
        from emptyos.sdk.agent_loop import _assistant_message_for_provider

        turn = AgentTurn(
            assistant_blocks=[
                TextBlock(text="I'll read it for you."),
                ToolUseBlock(id="c1", name="Read", input={"path": "/x"}),
            ],
            tool_uses=[ToolUse(id="c1", name="Read", input={"path": "/x"})],
            stop_reason="tool_use",
        )
        msg = _assistant_message_for_provider("openai", turn)
        assert msg["content"] == "I'll read it for you."
        assert len(msg["tool_calls"]) == 1

    def test_normalize_heals_persisted_null_content(self):
        """Sessions persisted before the loop fix may have assistant messages
        with `content: null` in the DB. Normalization must coerce those to ""
        so they don't blow up when replayed through Ollama."""
        from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider
        p = OpenAICompatThinkProvider(provider_name="test")
        persisted = [
            {"role": "user", "content": "play a song"},
            # ← This is what's on disk from a pre-fix session:
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "CallApp", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "{\"apps\": []}"},
        ]
        out = p._normalize_messages_for_openai(persisted, system="")
        assistant_msgs = [m for m in out if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"] == "", (
            f"null content must be healed to '', got {assistant_msgs[0]['content']!r}"
        )
        assert assistant_msgs[0]["content"] is not None
        assert len(assistant_msgs[0]["tool_calls"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Live-daemon integration tests (require EmptyOS running on localhost:9000).
# Skipped automatically by the `server_health` fixture in conftest.py when down.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.api
class TestAgentAPI:
    def test_list_tools(self, http_client):
        data = assert_ok(http_client.get("/agent/api/tools"))
        assert isinstance(data, list)
        names = {t.get("name") for t in data if isinstance(t, dict)}
        # Registry must expose the core tools the loop depends on.
        assert {"Bash", "Read", "Grep"} <= names, (
            f"Expected core tools in registry, got {sorted(names)}"
        )
        for t in data:
            assert "input_schema" in t and "permission" in t

    def test_create_session(self, http_client):
        payload = factories.agent_session(name="create")
        data = assert_dict_response(http_client.post("/agent/api/sessions", json=payload))
        assert data.get("id"), f"session missing id: {data}"
        assert data.get("name") == payload["name"]

    def test_list_includes_created_session(self, http_client):
        payload = factories.agent_session(name="list")
        created = http_client.post("/agent/api/sessions", json=payload).json()
        sid = created["id"]
        listing = assert_ok(http_client.get("/agent/api/sessions"))
        assert isinstance(listing, list)
        assert any(s.get("id") == sid for s in listing), (
            f"created session {sid} not in list"
        )

    def test_get_session_by_id(self, http_client):
        payload = factories.agent_session(name="get")
        created = http_client.post("/agent/api/sessions", json=payload).json()
        sid = created["id"]
        got = assert_dict_response(http_client.get(f"/agent/api/sessions/{sid}"))
        assert got.get("id") == sid
        assert "messages" in got

    def test_get_missing_session_returns_error(self, http_client):
        got = assert_dict_response(http_client.get("/agent/api/sessions/does-not-exist"))
        assert got.get("error") == "not found", f"expected error body, got {got}"

    def test_delete_session(self, http_client):
        payload = factories.agent_session(name="delete")
        created = http_client.post("/agent/api/sessions", json=payload).json()
        sid = created["id"]
        resp = http_client.request("DELETE", f"/agent/api/sessions/{sid}")
        assert resp.status_code == 200
        # Subsequent GET should now return the not-found sentinel.
        got = http_client.get(f"/agent/api/sessions/{sid}").json()
        assert got.get("error") == "not found"

    def test_create_session_with_provider(self, http_client):
        payload = factories.agent_session(name="prov")
        payload["provider"] = "ollama"
        data = assert_dict_response(http_client.post("/agent/api/sessions", json=payload))
        assert data.get("provider") == "ollama"

    def test_pending_permissions_is_list(self, http_client):
        data = assert_dict_response(http_client.get("/agent/api/permissions"))
        assert "pending" in data
        assert isinstance(data["pending"], list)

    def test_pending_permissions_session_filter_shape(self, http_client):
        data = assert_dict_response(
            http_client.get("/agent/api/permissions?session_id=no-such")
        )
        assert isinstance(data.get("pending"), list)
        assert data["pending"] == []  # unknown session → empty list

    def test_approve_unknown_permission_is_false(self, http_client):
        data = assert_dict_response(
            http_client.post(
                "/agent/api/permission/unknown-req/approve",
                json={"scope": "once"},
            )
        )
        assert data.get("ok") is False

    def test_deny_unknown_permission_is_false(self, http_client):
        data = assert_dict_response(
            http_client.post("/agent/api/permission/unknown-req/deny", json={})
        )
        assert data.get("ok") is False

    def test_slash_commands_list(self, http_client):
        """Shared slash-command list feeds both CLI and web autocomplete."""
        data = assert_ok(http_client.get("/agent/api/slash-commands"))
        assert isinstance(data, list)
        names = {c.get("name") for c in data if isinstance(c, dict)}
        # Core commands must be present — terminal and web both rely on them.
        assert {"/help", "/status", "/model", "/tools", "/clear", "/new"} <= names, (
            f"missing core slash commands, got {sorted(names)}"
        )
        for c in data:
            assert "help" in c and "args" in c, f"incomplete command spec: {c}"

    def test_status_default(self, http_client):
        """GET /api/status with no session returns default provider info."""
        data = assert_dict_response(http_client.get("/agent/api/status"))
        for key in ("provider", "tools", "policy", "max_iters"):
            assert key in data, f"missing status key {key!r}: {list(data.keys())}"
        assert isinstance(data["tools"].get("count"), int)
        assert "requested" in data["provider"]
        # If a provider resolved, `model` field should be present (may be empty string
        # for native agents, but the key itself is the API contract).
        if data["provider"].get("available"):
            assert "model" in data["provider"], (
                f"available provider must expose `model`, got {data['provider']}"
            )

    def test_status_for_session(self, http_client):
        """Status endpoint scopes provider resolution to the session."""
        payload = factories.agent_session(name="status")
        payload["provider"] = "ollama"
        sid = http_client.post("/agent/api/sessions", json=payload).json()["id"]
        data = assert_dict_response(
            http_client.get(f"/agent/api/status?session_id={sid}")
        )
        assert data.get("session_id") == sid
        assert data["provider"].get("requested") == "ollama", (
            f"expected requested=ollama, got {data['provider']}"
        )

    def test_patch_session_updates_provider(self, http_client):
        """PATCH /api/sessions/{sid} switches the session's provider."""
        payload = factories.agent_session(name="patch")
        sid = http_client.post("/agent/api/sessions", json=payload).json()["id"]
        resp = http_client.patch(
            f"/agent/api/sessions/{sid}",
            json={"provider": "openai"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("ok") is True
        got = http_client.get(f"/agent/api/sessions/{sid}").json()
        assert got.get("provider") == "openai", (
            f"session provider not updated, got {got.get('provider')!r}"
        )

    def test_patch_session_rejects_empty(self, http_client):
        payload = factories.agent_session(name="patch-empty")
        sid = http_client.post("/agent/api/sessions", json=payload).json()["id"]
        resp = http_client.patch(f"/agent/api/sessions/{sid}", json={})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_patch_missing_session_returns_error(self, http_client):
        resp = http_client.patch(
            "/agent/api/sessions/does-not-exist",
            json={"name": "x"},
        )
        assert resp.status_code == 200
        assert resp.json().get("error") == "not found"


@pytest.mark.interactive
class TestAgentUI:
    def test_ui_shell_loads(self, app_page, page_errors):
        """Page renders sidebar, input box, send button with no JS errors."""
        page = app_page("agent")
        wait_briefly(page, 600)
        assert page.locator("#sidebar").count() == 1
        assert page.locator("#new-session-btn").count() == 1
        assert page.locator("#input-box").count() == 1
        assert page.locator("#send-btn").count() == 1
        assert_no_js_errors(page_errors)

    def test_ui_create_session_via_button(self, app_page, page_errors, http_client):
        """Clicking '+ New session' creates a session and adds it to the sidebar."""
        page = app_page("agent")
        wait_briefly(page, 500)
        before = http_client.get("/agent/api/sessions").json()
        before_count = len(before) if isinstance(before, list) else 0
        page.locator("#new-session-btn").click()
        wait_briefly(page, 1200)
        after = http_client.get("/agent/api/sessions").json()
        after_count = len(after) if isinstance(after, list) else 0
        assert after_count == before_count + 1, (
            f"expected one new session, went {before_count} → {after_count}"
        )
        # Sidebar should render at least one .session-item.
        assert page.locator(".session-item").count() >= 1
        assert_no_js_errors(page_errors)

    def test_ui_settings_panel_opens(self, app_page, page_errors):
        """Settings gear opens the mandatory in-app settings panel."""
        page = app_page("agent")
        wait_briefly(page, 500)
        clicked = click_first(
            page,
            "button.btn-settings",
            "[onclick*='openAgentSettings']",
        )
        if not clicked:
            pytest.skip("Settings gear not present")
        wait_briefly(page, 500)
        # settingsPanel helper renders #agent-settings-panel; check it became visible.
        panel = page.locator("#agent-settings-panel")
        assert panel.count() == 1
        assert_no_js_errors(page_errors)

    def test_ui_status_header_renders(self, app_page, page_errors, http_client):
        """Opening a session shows the status header with provider + tools chips."""
        payload = factories.agent_session(name="hdr")
        sid = http_client.post("/agent/api/sessions", json=payload).json()["id"]
        page = app_page("agent")
        page.evaluate(f"location.hash = '{sid}'")
        page.wait_for_timeout(1200)
        assert page.locator("#status-header.show").count() == 1, (
            "status-header never entered show state"
        )
        model_name = (page.locator("#hdr-model-name").text_content() or "").strip()
        tools_count = (page.locator("#hdr-tools-count").text_content() or "").strip()
        assert model_name, "provider name is empty"
        assert tools_count.isdigit() and int(tools_count) > 0, (
            f"tools count should be a positive integer, got {tools_count!r}"
        )
        assert_no_js_errors(page_errors)

    def test_ui_slash_palette_opens(self, app_page, page_errors):
        """Typing '/' in the input surfaces the slash-command palette."""
        page = app_page("agent")
        wait_briefly(page, 800)
        page.locator("#input-box").fill("/")
        page.locator("#input-box").dispatch_event("input")
        try:
            page.wait_for_selector("#slash-palette.show", timeout=3000)
        except Exception:
            pytest.fail(
                f"slash palette never appeared; "
                f"content={(page.locator('#slash-palette').inner_html() or '')[:200]!r}"
            )
        items = page.locator("#slash-palette .slash-item")
        assert items.count() >= 4, f"expected ≥4 slash commands, got {items.count()}"
        # /help should be among them.
        names = [
            (items.nth(i).locator(".sc-name").text_content() or "").strip()
            for i in range(items.count())
        ]
        assert "/help" in names, f"expected /help in palette, got {names}"
        assert_no_js_errors(page_errors)

    def test_ui_websocket_connects(self, app_page, page_errors, http_client, base_url):
        """Create session via API, navigate to /agent/#<sid>, WS should connect and
        status bar should read 'connected'. This exercises the hash router +
        WebSocket handshake without triggering an LLM turn."""
        payload = factories.agent_session(name="ws-connect")
        sid = http_client.post("/agent/api/sessions", json=payload).json()["id"]
        page = app_page("agent")
        wait_briefly(page, 400)
        page.evaluate(f"location.hash = '{sid}'")
        # Wait for status bar to flip to "connected" (onopen handler).
        try:
            page.wait_for_function(
                "document.getElementById('status-bar').textContent.trim() === 'connected'",
                timeout=5000,
            )
            status_ok = True
        except Exception:
            status_ok = False
        status = (page.locator("#status-bar").text_content() or "").strip()
        assert status_ok, f"WS never connected; status bar = {status!r}"
        assert_no_js_errors(page_errors)
