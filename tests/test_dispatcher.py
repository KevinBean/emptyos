"""Unit tests for the voice-assistant dispatcher (Phase 1 plan/execute).

These tests don't need a running daemon — they exercise the parser + plan
builder + execute_plan loop directly. Importing the app module would pull
in the kernel; instead we load it via importlib without instantiating.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Dispatcher parser + plan builder now live in emptyos.sdk.intents.
from emptyos.sdk.intents import find_intents, build_plan_dict


from helpers import app_path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = app_path("voice-assistant")
APP_PATH = APP_DIR / "app.py"


@pytest.fixture(scope="module")
def voice_module():
    """Load apps/voice-assistant/app.py as a package member so relative
    imports (`from .chat_pipeline import ...`) resolve via the dir's __path__."""
    pkg_name = "va_under_test"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(APP_DIR)]
        sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.app", str(APP_PATH),
        submodule_search_locations=[str(APP_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.app"] = mod
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


def _register(stub, verb: str, app: str, method: str, args: dict | None = None) -> None:
    """Register a verb in the stub's intent registry.

    ``execute_plan`` resolves every step against ``_intents`` and derives
    app/method from the entry rather than trusting the plan dict, so a test
    plan only runs for verbs registered here. Before that change these tests
    passed with an empty registry — which was the bypass, not a feature.
    """
    stub._intents[verb] = {
        "verb": verb, "_app_id": app, "method": method, "args": args or {},
    }


# ── _find_intents ─────────────────────────────────────────────────────────

class TestFindIntents:
    def test_single_intent(self):
        out = find_intents(
            'hi [INTENT:task.add({"text":"buy milk"})] done'
        )
        assert len(out) == 1
        assert out[0][0] == "task.add"
        assert out[0][1] == '{"text":"buy milk"}'

    def test_two_intents(self):
        out = find_intents(
            '[INTENT:a.b({"x":"1"})] [INTENT:c.d({"y":"2"})]'
        )
        assert [v for v, _, _, _ in out] == ["a.b", "c.d"]

    def test_nested_args(self):
        out = find_intents(
            '[INTENT:foo.bar({"meta":{"k":"v"},"x":1})]'
        )
        assert len(out) == 1
        assert out[0][1] == '{"meta":{"k":"v"},"x":1}'

    def test_brace_inside_string(self):
        out = find_intents(
            '[INTENT:foo.x({"text":"hello {world}"})]'
        )
        assert len(out) == 1
        assert out[0][1] == '{"text":"hello {world}"}'

    def test_string_with_escaped_quote(self):
        out = find_intents(
            '[INTENT:foo({"text":"she said \\"hi\\""})]'
        )
        assert len(out) == 1

    def test_no_intent_in_text(self):
        out = find_intents(
            "just some normal prose, nothing structured here"
        )
        assert out == []

    def test_unterminated_token_skipped(self):
        # Open brace, never closes — parser bails, doesn't crash.
        out = find_intents('[INTENT:a({"x":"y"')
        assert out == []

    def test_missing_close_paren_skipped(self):
        # Args close fine, but missing `)]` — parser advances past and finds nothing else.
        out = find_intents('[INTENT:a({"x":"y"})missing')
        assert out == []

    def test_offsets_are_correct(self):
        text = 'pre [INTENT:a.b({"x":1})] post'
        out = find_intents(text)
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
        plan = build_plan_dict(
            'sure: [INTENT:task.add({"text":"call mom"})]', scoped,
        )
        assert plan["calls"][0]["error"] is None
        assert plan["calls"][0]["app"] == "task"
        assert plan["calls"][0]["args"] == {"text": "call mom"}

    def test_unknown_verb_flags_error(self, stub_app):
        plan = build_plan_dict(
            '[INTENT:made.up({"x":"y"})]', scoped=[],
        )
        assert "out-of-scope" in plan["calls"][0]["error"]

    def test_missing_required_arg(self, stub_app):
        scoped = [{"verb": "task.add", "_app_id": "task", "method": "v",
                   "args": {"text": "string"}}]
        plan = build_plan_dict(
            '[INTENT:task.add({})]', scoped,
        )
        assert "missing" in plan["calls"][0]["error"].lower()

    def test_optional_arg_marker(self, stub_app):
        scoped = [{"verb": "j.entry", "_app_id": "journal", "method": "v",
                   "args": {"text": "string", "mood": "string?"}}]
        plan = build_plan_dict(
            '[INTENT:j.entry({"text":"hi"})]', scoped,
        )
        assert plan["calls"][0]["error"] is None

    def test_bad_json_args(self, stub_app):
        scoped = [{"verb": "task.add", "_app_id": "task", "method": "v",
                   "args": {"text": "string"}}]
        # `{...}` boundary is fine but contents aren't JSON.
        plan = build_plan_dict(
            '[INTENT:task.add({not valid json})]', scoped,
        )
        assert "args not JSON" in plan["calls"][0]["error"]

    def test_say_strips_intent_tokens(self, stub_app):
        scoped = [{"verb": "task.add", "_app_id": "task", "method": "v",
                   "args": {"text": "string"}}]
        plan = build_plan_dict(
            'Sure, doing it: [INTENT:task.add({"text":"x"})] all set.', scoped,
        )
        assert plan["say"] == "Sure, doing it: all set."

    def test_empty_reply(self, stub_app):
        plan = build_plan_dict("", scoped=[])
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
        _register(stub_app, "a.b", "a", "m")
        _register(stub_app, "c.d", "c", "m")
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
        _register(stub_app, "fails.x", "fails", "m")
        _register(stub_app, "ok.x", "ok", "m")
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
        _register(stub_app, "a.x", "a", "m")
        _register(stub_app, "b.x", "b", "m")
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
        _register(stub_app, "a.x", "a", "m")
        plan = {"calls": [
            {"verb": "a.x", "args": {}, "app": "a", "method": "m", "error": None},
        ]}
        results = asyncio.run(stub_app.execute_plan(plan))
        assert results[0]["result"] == {"value": "raw string result"}


class TestExecutePlanTrustBoundary:
    """The plan reaching execute_plan is caller-supplied (POST /api/execute-plan
    and quick-action's forwarder both hand it straight through), so these pin
    that it is treated as untrusted."""

    def test_unknown_verb_is_refused(self, stub_app):
        called = []
        stub_app.call_app = lambda app, method, **kw: called.append((app, method))
        plan = {"calls": [
            {"verb": "evil.exfiltrate", "args": {}, "app": "evil", "method": "exfiltrate",
             "error": None},
        ]}
        results = asyncio.run(stub_app.execute_plan(plan))
        assert "unknown or out-of-scope" in results[0]["error"]
        assert called == []

    def test_app_and_method_come_from_registry_not_the_plan(self, stub_app):
        # A step naming a benign verb must not be able to dispatch elsewhere.
        seen = []

        async def fake_call_app(app, method, **kw):
            seen.append((app, method))
            return {"ok": True}

        stub_app.call_app = fake_call_app
        stub_app._persist_recent_apps = lambda: None
        _register(stub_app, "task.add", "task", "add")
        plan = {"calls": [
            {"verb": "task.add", "args": {}, "app": "repo", "method": "exec", "error": None},
        ]}
        asyncio.run(stub_app.execute_plan(plan))
        assert seen == [("task", "add")]

    def test_args_are_revalidated(self, stub_app):
        called = []
        stub_app.call_app = lambda app, method, **kw: called.append(kw)
        _register(stub_app, "task.add", "task", "add", {"text": "string"})
        plan = {"calls": [
            {"verb": "task.add", "args": {"text": 42}, "app": "task", "method": "add",
             "error": None},
        ]}
        results = asyncio.run(stub_app.execute_plan(plan))
        assert "text" in results[0]["error"]
        assert called == []
