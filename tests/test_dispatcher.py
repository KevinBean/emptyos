"""Unit tests for the voice-assistant dispatcher (Phase 1 plan/execute).

These tests don't need a running daemon — they exercise the parser + plan
builder + execute_plan loop directly. Importing the app module would pull
in the kernel; instead we load it via importlib without instantiating.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
APP_PATH = REPO / "apps" / "voice-assistant" / "app.py"


@pytest.fixture(scope="module")
def voice_module():
    """Load apps/voice-assistant/app.py without instantiating it."""
    spec = importlib.util.spec_from_file_location("va_under_test", str(APP_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["va_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def stub_app(voice_module):
    """A minimally-stubbed VoiceAssistantApp instance — bypasses BaseApp.__init__."""
    cls = voice_module.VoiceAssistantApp
    inst = cls.__new__(cls)
    # Bare attributes the methods touch:
    inst._intents = {}
    from collections import deque
    inst._recent_apps = deque(maxlen=2)
    return inst


# ── _find_intents ─────────────────────────────────────────────────────────

class TestFindIntents:
    def test_single_intent(self, voice_module):
        out = voice_module.VoiceAssistantApp._find_intents(
            'hi [INTENT:task.add({"text":"buy milk"})] done'
        )
        assert len(out) == 1
        assert out[0][0] == "task.add"
        assert out[0][1] == '{"text":"buy milk"}'

    def test_two_intents(self, voice_module):
        out = voice_module.VoiceAssistantApp._find_intents(
            '[INTENT:a.b({"x":"1"})] [INTENT:c.d({"y":"2"})]'
        )
        assert [v for v, _, _, _ in out] == ["a.b", "c.d"]

    def test_nested_args(self, voice_module):
        out = voice_module.VoiceAssistantApp._find_intents(
            '[INTENT:foo.bar({"meta":{"k":"v"},"x":1})]'
        )
        assert len(out) == 1
        assert out[0][1] == '{"meta":{"k":"v"},"x":1}'

    def test_brace_inside_string(self, voice_module):
        out = voice_module.VoiceAssistantApp._find_intents(
            '[INTENT:foo.x({"text":"hello {world}"})]'
        )
        assert len(out) == 1
        assert out[0][1] == '{"text":"hello {world}"}'

    def test_string_with_escaped_quote(self, voice_module):
        out = voice_module.VoiceAssistantApp._find_intents(
            '[INTENT:foo({"text":"she said \\"hi\\""})]'
        )
        assert len(out) == 1

    def test_no_intent_in_text(self, voice_module):
        out = voice_module.VoiceAssistantApp._find_intents(
            "just some normal prose, nothing structured here"
        )
        assert out == []

    def test_unterminated_token_skipped(self, voice_module):
        # Open brace, never closes — parser bails, doesn't crash.
        out = voice_module.VoiceAssistantApp._find_intents('[INTENT:a({"x":"y"')
        assert out == []

    def test_missing_close_paren_skipped(self, voice_module):
        # Args close fine, but missing `)]` — parser advances past and finds nothing else.
        out = voice_module.VoiceAssistantApp._find_intents('[INTENT:a({"x":"y"})missing')
        assert out == []

    def test_offsets_are_correct(self, voice_module):
        text = 'pre [INTENT:a.b({"x":1})] post'
        out = voice_module.VoiceAssistantApp._find_intents(text)
        v, args, start, end = out[0]
        assert text[start:end] == '[INTENT:a.b({"x":1})]'


# ── _build_plan_dict ──────────────────────────────────────────────────────

class TestBuildPlanDict:
    def test_known_verb_validates(self, stub_app):
        scoped = [{
            "verb": "task.add",
            "_app_id": "task",
            "method": "voice_add_task",
            "args": {"text": "string"},
            "description": "Capture a quick task",
        }]
        plan = stub_app._build_plan_dict(
            'sure: [INTENT:task.add({"text":"call mom"})]', scoped,
        )
        assert plan["calls"][0]["error"] is None
        assert plan["calls"][0]["app"] == "task"
        assert plan["calls"][0]["args"] == {"text": "call mom"}

    def test_unknown_verb_flags_error(self, stub_app):
        plan = stub_app._build_plan_dict(
            '[INTENT:made.up({"x":"y"})]', scoped=[],
        )
        assert "out-of-scope" in plan["calls"][0]["error"]

    def test_missing_required_arg(self, stub_app):
        scoped = [{"verb": "task.add", "_app_id": "task", "method": "v",
                   "args": {"text": "string"}}]
        plan = stub_app._build_plan_dict(
            '[INTENT:task.add({})]', scoped,
        )
        assert "missing" in plan["calls"][0]["error"].lower()

    def test_optional_arg_marker(self, stub_app):
        scoped = [{"verb": "j.entry", "_app_id": "journal", "method": "v",
                   "args": {"text": "string", "mood": "string?"}}]
        plan = stub_app._build_plan_dict(
            '[INTENT:j.entry({"text":"hi"})]', scoped,
        )
        assert plan["calls"][0]["error"] is None

    def test_bad_json_args(self, stub_app):
        scoped = [{"verb": "task.add", "_app_id": "task", "method": "v",
                   "args": {"text": "string"}}]
        # `{...}` boundary is fine but contents aren't JSON.
        plan = stub_app._build_plan_dict(
            '[INTENT:task.add({not valid json})]', scoped,
        )
        assert "args not JSON" in plan["calls"][0]["error"]

    def test_say_strips_intent_tokens(self, stub_app):
        scoped = [{"verb": "task.add", "_app_id": "task", "method": "v",
                   "args": {"text": "string"}}]
        plan = stub_app._build_plan_dict(
            'Sure, doing it: [INTENT:task.add({"text":"x"})] all set.', scoped,
        )
        assert plan["say"] == "Sure, doing it: all set."

    def test_empty_reply(self, stub_app):
        plan = stub_app._build_plan_dict("", scoped=[])
        assert plan["calls"] == []
        assert plan["say"] == ""


# ── execute_plan ──────────────────────────────────────────────────────────

class TestExecutePlan:
    def test_step_with_error_field_short_circuits(self, stub_app):
        plan = {"calls": [
            {"verb": "x.y", "args": {}, "app": None, "method": None, "error": "bad"},
        ]}
        results = asyncio.run(stub_app.execute_plan(plan))
        assert results[0]["error"] == "bad"
        assert "ok" not in results[0]

    def test_only_indices_skips_others(self, stub_app):
        async def fake(**_):
            return {"say": "did it"}
        stub_app.call_app = lambda app, method, **kw: fake(**kw)
        plan = {"calls": [
            {"verb": "a.b", "args": {}, "app": "a", "method": "m", "error": None},
            {"verb": "c.d", "args": {}, "app": "c", "method": "m", "error": None},
        ]}
        results = asyncio.run(stub_app.execute_plan(plan, only_indices=[1]))
        assert results[0].get("skipped") is True
        assert results[1].get("ok") is True

    def test_call_failure_isolated(self, stub_app):
        async def fail(**_):
            raise RuntimeError("boom")
        async def ok(**_):
            return {"say": "ok"}
        # Simple dispatch by app name.
        async def fake_call_app(app, method, **kw):
            return await (fail(**kw) if app == "fails" else ok(**kw))
        stub_app.call_app = fake_call_app
        plan = {"calls": [
            {"verb": "fails.x", "args": {}, "app": "fails", "method": "m", "error": None},
            {"verb": "ok.x", "args": {}, "app": "ok", "method": "m", "error": None},
        ]}
        results = asyncio.run(stub_app.execute_plan(plan))
        assert "boom" in results[0]["error"]
        assert results[1]["ok"] is True  # second step still ran

    def test_recent_apps_updated_on_success(self, stub_app):
        async def fake_call_app(app, method, **kw):
            return {"say": "ok"}
        stub_app.call_app = fake_call_app
        # _persist_recent_apps writes to data_dir; stub it out.
        stub_app._persist_recent_apps = lambda: None
        plan = {"calls": [
            {"verb": "a.x", "args": {}, "app": "a", "method": "m", "error": None},
            {"verb": "b.x", "args": {}, "app": "b", "method": "m", "error": None},
        ]}
        asyncio.run(stub_app.execute_plan(plan))
        assert list(stub_app._recent_apps) == ["a", "b"]

    def test_non_dict_result_wrapped(self, stub_app):
        async def fake_call_app(app, method, **kw):
            return "raw string result"
        stub_app.call_app = fake_call_app
        stub_app._persist_recent_apps = lambda: None
        plan = {"calls": [
            {"verb": "a.x", "args": {}, "app": "a", "method": "m", "error": None},
        ]}
        results = asyncio.run(stub_app.execute_plan(plan))
        assert results[0]["result"] == {"value": "raw string result"}
