"""Notes stay on the machine unless the learner allows otherwise
(emptyos/capabilities/note_scope.py), and the `[cloud] locked` switch that keeps
a hosted build's policy and model out of the learner's hands.

Pinned:
  - note_scope: off unless `note_apps` is set; only a listed app, only a cloud
    provider, and only while the learner's switch is off; the switch is read
    per call and only an explicit yes counts;
  - the capability chain: a listed app's call skips cloud providers and, with
    no local one, raises NotesToCloudOff (keeping the "No available provider"
    prefix); a local provider still answers; an unlisted app is unaffected; the
    spend cap is reported first when both apply; streaming and compare too;
  - BaseApp passes the calling app into the chain; cloud_gate gives the same
    answers to the paths that call a provider directly; the agent loop uses it;
  - what the learner is told: the server's offline reply, learn's quiz, and a
    Privacy switch in Settings that appears only when the rule is on;
  - `[cloud] locked`: a saved policy is ignored, the policy route refuses, the
    settings model override is ignored.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from emptyos.capabilities import Capability, Provider, cloud_gate
from emptyos.capabilities.note_scope import NOTES_SETTING, NoteScope, NotesToCloudOff
from emptyos.capabilities.spend_cap import SpendCap, SpendCapReached


class _Settings:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Config:
    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        return self.value if key == "cloud.note_apps" else default


class _Prov(Provider):
    def __init__(self, name, *, cloud, up=True):
        self.name = name
        self.trust = "service" if cloud else "owned"
        self.up = up
        self.calls = 0

    async def available(self):
        return self.up

    async def execute(self, **kw):
        self.calls += 1
        return f"answer from {self.name}"

    async def execute_stream(self, **kw):
        self.calls += 1
        yield f"answer from {self.name}"


def CLOUD():  # noqa: N802
    return _Prov("openrouter", cloud=True)


def _scope(opted=None):
    settings = _Settings() if opted is None else _Settings(**{NOTES_SETTING: opted})
    return NoteScope(["kb", "learn"], settings)


# ── the rule ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, [], "", ()])
def test_off_unless_note_apps_are_set(value):
    assert NoteScope.from_config(_Config(value)) is None


def test_note_apps_from_a_list_or_the_env_string():
    assert NoteScope.from_config(_Config(["kb", " learn "])).note_apps == {"kb", "learn"}
    assert NoteScope.from_config(_Config("kb, learn,")).note_apps == {"kb", "learn"}


def test_blocks_only_a_listed_app_on_a_cloud_provider():
    scope = _scope()
    assert scope.blocks(CLOUD(), "kb") is True
    assert scope.blocks(CLOUD(), "dictionary") is False
    assert scope.blocks(_Prov("ollama", cloud=False), "kb") is False
    assert scope.blocks(CLOUD(), None) is False


@pytest.mark.parametrize("value, allowed", [
    (True, True), ("true", True), ("on", True),
    (False, False), ("false", False), ("", False), ("maybe", False), (1, False),
])
def test_only_an_explicit_yes_lets_notes_leave(value, allowed):
    assert _scope(value).blocks(CLOUD(), "kb") is (not allowed)


def test_the_switch_is_read_on_every_call():
    settings = _Settings()
    scope = NoteScope(["kb"], settings)
    assert scope.blocks(CLOUD(), "kb") is True
    settings.values[NOTES_SETTING] = True
    assert scope.blocks(CLOUD(), "kb") is False


# ── the capability chain ──────────────────────────────────────────────────

def _think(*providers, opted=None, spend=None):
    think = Capability(list(providers))
    think.name = "think"
    think.note_scope = _scope(opted)
    think.spend_cap = spend
    return think


def test_a_listed_app_with_only_cloud_raises_notes_to_cloud_off():
    cloud = CLOUD()
    with pytest.raises(NotesToCloudOff) as exc:
        asyncio.run(_think(cloud).execute(prompt="my note", caller_app="kb"))
    assert cloud.calls == 0
    assert str(exc.value).startswith("No available provider for capability 'think'")
    assert "Let AI features read my notes" in str(exc.value)


def test_a_listed_app_is_answered_by_a_local_provider():
    cloud, local = CLOUD(), _Prov("ollama", cloud=False)
    result = asyncio.run(_think(cloud, local).execute(prompt="n", caller_app="kb"))
    assert result.value == "answer from ollama" and cloud.calls == 0


def test_after_opting_in_the_cloud_provider_answers():
    result = asyncio.run(_think(CLOUD(), opted=True).execute(prompt="n", caller_app="kb"))
    assert result.value == "answer from openrouter"


def test_an_unlisted_app_is_unaffected():
    result = asyncio.run(_think(CLOUD()).execute(prompt="a word", caller_app="dictionary"))
    assert result.value == "answer from openrouter"


def test_the_spend_cap_is_reported_first_when_both_apply(tmp_path):
    # The metered provider is stopped by the cap; the subscription one (not
    # metered, so the cap ignores it) is stopped only by the notes rule. Both
    # reasons apply, and the one the learner cannot change is reported.
    cap = SpendCap(tmp_path, 0.25)
    cap.record(0.30)
    subscription = _Prov("claude-cli", cloud=True)
    subscription.metered = False
    with pytest.raises(SpendCapReached):
        asyncio.run(_think(CLOUD(), subscription, spend=cap).execute(prompt="n", caller_app="kb"))


def test_streaming_raises_notes_to_cloud_off():
    async def drain():
        async for _ in _think(CLOUD()).execute_stream(prompt="n", caller_app="kb"):
            pass

    with pytest.raises(NotesToCloudOff):
        asyncio.run(drain())


def test_compare_skips_cloud_for_a_listed_app():
    cloud, local = CLOUD(), _Prov("ollama", cloud=False)
    rows = asyncio.run(_think(cloud, local).execute_compare(prompt="n", caller_app="kb"))
    by = {r["provider"]: r for r in rows}
    assert cloud.calls == 0 and "notes" in by["openrouter"]["error"]
    assert by["ollama"]["response"] == "answer from ollama"


# ── BaseApp and the direct paths ──────────────────────────────────────────

def test_base_app_think_tells_the_chain_which_app_is_calling():
    from fake_kernel import FakeCapability, make_bare_app

    seen = {}

    from emptyos.capabilities import Result

    class Recording(FakeCapability):
        async def execute(self, **kwargs):
            seen.update(kwargs)
            return Result(value="ok", provider="fake")

        async def execute_stream(self, **kwargs):
            seen.update(kwargs)
            async for c in super().execute_stream(**kwargs):
                yield c

        async def execute_compare(self, **kwargs):
            seen.update(kwargs)
            return []

    app = make_bare_app(Recording([]))
    asyncio.run(app.think("hi"))
    assert seen.get("caller_app") == "test-app"
    seen.clear()

    async def drain():
        async for _ in app.think_stream("hi"):
            pass

    asyncio.run(drain())
    assert seen.get("caller_app") == "test-app"
    seen.clear()
    asyncio.run(app.think_compare("hi"))
    assert seen.get("caller_app") == "test-app"


def test_pinned_execute_tells_the_chain_which_app_is_calling():
    from fake_kernel import FakeCapability, make_bare_app

    seen = {}

    class Recording(FakeCapability):
        async def execute(self, **kwargs):
            seen.update(kwargs)
            return await super().execute(**kwargs)

    app = make_bare_app(Recording([]))
    asyncio.run(app.pinned_execute("think", None, prompt="hi"))
    assert seen.get("caller_app") == "test-app"


def test_cloud_gate_answers_like_the_chain(tmp_path):
    kernel = SimpleNamespace(spend_cap=None, note_scope=_scope())
    assert cloud_gate.check(kernel, CLOUD(), "think", "kb") == "notes"
    assert cloud_gate.check(kernel, CLOUD(), "think", "dictionary") is None
    cap = SpendCap(tmp_path, 0.25)
    cap.record(0.30)
    kernel.spend_cap = cap
    assert cloud_gate.check(kernel, CLOUD(), "think", "kb") == "spend_cap"
    assert isinstance(cloud_gate.error(kernel, "notes", "think", "x"), NotesToCloudOff)
    assert isinstance(cloud_gate.error(kernel, "spend_cap", "think", "x"), SpendCapReached)
    assert str(cloud_gate.error(kernel, "notes", "think", "x")).startswith(
        "No available provider for capability 'think'")


def test_a_settings_chosen_provider_is_skipped_for_a_listed_app():
    from emptyos.sdk import base_app_think

    cloud = CLOUD()
    think = _think(cloud)
    kernel = SimpleNamespace(spend_cap=None, note_scope=think.note_scope,
                             capability=lambda name: think)

    class App:
        manifest = SimpleNamespace(id="kb")

        def __init__(self):
            self.kernel = kernel

        async def _emit_think_executed(self, *a, **k):
            return None

    assert asyncio.run(base_app_think._think_with_provider(App(), "openrouter", "n", None, {})) is None
    assert cloud.calls == 0


def test_the_agent_loop_refuses_a_listed_app():
    from emptyos.sdk.agent_loop import AgentSession, run_turn

    class ToolProvider:
        name, kind, is_cloud = "openrouter", "openai", True

        def __init__(self):
            self.calls = 0

        async def execute_tools(self, **kw):
            self.calls += 1
            raise LookupError("reached the provider")

    provider = ToolProvider()
    app_ref = SimpleNamespace(kernel=SimpleNamespace(spend_cap=None, note_scope=_scope()),
                              manifest=SimpleNamespace(id="kb"))
    with pytest.raises(NotesToCloudOff):
        asyncio.run(run_turn(session=AgentSession(id="t"), user_text="hi", provider=provider,
                             tools={}, tool_consent=None, events=None, app_ref=app_ref))
    assert provider.calls == 0


# ── what the learner is told ──────────────────────────────────────────────

def test_the_offline_reply_asks_for_the_switch():
    from emptyos.web.server import _ai_offline_body

    body = _ai_offline_body(NotesToCloudOff(
        "No available provider for capability 'think' (chain=x): this feature sends your notes"))
    assert body["error"] == "ai_offline" and body["reason"] == "notes_opt_in"
    assert "Settings" in body["message"]


def test_learn_quiz_asks_for_the_switch(tmp_path):
    from helpers import load_app_module

    srs = load_app_module("learn", "srs")

    class App:
        _generate_quiz_for_slug = srs._generate_quiz_for_slug
        _quiz_cache_dir = tmp_path

        async def _resolve_lesson_source(self, slug):
            return {"title": "T", "body_md": "my lesson note"}

        async def think(self, prompt, **kw):
            raise NotesToCloudOff("No available provider for capability 'think': notes")

    got = asyncio.run(App()._generate_quiz_for_slug("lesson-1"))
    assert got["needs_opt_in"] is True and "Settings" in got["error"]


def _settings_schema(note_scope):
    from helpers import load_app_module

    mod = load_app_module("settings", "app")
    app = mod.SettingsApp.__new__(mod.SettingsApp)
    app.kernel = SimpleNamespace(note_scope=note_scope, apps=SimpleNamespace(manifests={}))
    return asyncio.run(mod.SettingsApp.api_schema(app, None))["sections"]


def test_settings_shows_the_switch_only_when_the_rule_is_on():
    on = _settings_schema(_scope())
    keys = [s["key"] for sec in on for s in sec["settings"]]
    assert NOTES_SETTING in keys
    switch = next(s for sec in on for s in sec["settings"] if s["key"] == NOTES_SETTING)
    assert switch["type"] == "toggle" and switch["default"] is False
    off = _settings_schema(None)
    assert NOTES_SETTING not in [s["key"] for sec in off for s in sec["settings"]]


# ── [cloud] locked ────────────────────────────────────────────────────────

def _kernel(tmp_path, extra):
    from emptyos.kernel import Kernel

    (tmp_path / "vault").mkdir(exist_ok=True)
    toml = tmp_path / "emptyos.toml"
    toml.write_text(
        f'[os]\ndata_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'[notes]\npath = "{(tmp_path / "vault").as_posix()}"\n{extra}',
        encoding="utf-8",
    )
    return Kernel(str(toml))


def _save_policy(tmp_path, policy):
    import json

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"cloud": {"consent": policy}}),
                                                    encoding="utf-8")


def test_kernel_wires_the_rule_into_every_capability(tmp_path):
    k = _kernel(tmp_path, '[cloud]\nnote_apps = ["kb", "learn"]\n')
    assert k.note_scope is not None and k.note_scope.note_apps == {"kb", "learn"}
    assert k.capabilities.get("think").note_scope is k.note_scope

    from emptyos.capabilities import CapabilityRegistry

    late = CapabilityRegistry()
    late.set_note_scope(k.note_scope)
    late.register("think", Capability([]))
    assert late.get("think").note_scope is k.note_scope


def test_kernel_without_note_apps_has_no_rule(tmp_path):
    k = _kernel(tmp_path, "")
    assert k.note_scope is None and k.capabilities.get("think").note_scope is None


def test_a_saved_policy_is_ignored_when_locked(tmp_path):
    _save_policy(tmp_path, "always")
    k = _kernel(tmp_path, '[cloud]\nconsent = "never"\nlocked = true\n')
    assert k.cloud_consent.policy == "never"
    assert k.cloud_consent.set_policy("always") is False and k.cloud_consent.policy == "never"


@pytest.mark.parametrize("value, locked", [("true", True), ("1", True), ("false", False), ("", False)])
def test_the_lock_can_come_from_the_env(tmp_path, monkeypatch, value, locked):
    from emptyos.kernel.config import Config

    monkeypatch.setenv("EOS_CLOUD_LOCKED", value)
    p = tmp_path / "e.toml"
    p.write_text("", encoding="utf-8")
    assert Config(str(p)).cloud_locked is locked


def test_a_saved_policy_still_wins_when_not_locked(tmp_path):
    _save_policy(tmp_path, "always")
    assert _kernel(tmp_path, '[cloud]\nconsent = "never"\n').cloud_consent.policy == "always"


def test_locked_build_keeps_the_operators_model(tmp_path):
    from emptyos.capabilities.setup import _build_think_provider_raw
    from emptyos.kernel.config import Config

    def build(locked):
        p = tmp_path / f"c{locked}.toml"
        p.write_text(
            f'[cloud]\nlocked = {"true" if locked else "false"}\n'
            '[capabilities.think.openrouter]\nhost = "https://openrouter.ai/api"\n'
            'model = "deepseek/deepseek-v4-flash"\napi_key_env = "OPENROUTER_API_KEY"\n',
            encoding="utf-8",
        )
        settings = _Settings(**{"think.openrouter.model": "some/expensive-model"})
        return _build_think_provider_raw("openrouter", Config(str(p)), settings=settings).model

    assert build(True) == "deepseek/deepseek-v4-flash"
    assert build(False) == "some/expensive-model"


def test_the_policy_route_refuses_when_locked():
    from emptyos.capabilities.consent import CloudConsentManager

    locked = CloudConsentManager(policy="never", locked=True)
    assert locked.set_policy("always") is False
    unlocked = CloudConsentManager(policy="never")
    assert unlocked.set_policy("always") is True and unlocked.policy == "always"



def test_the_policy_route_answers_409_and_saves_nothing_when_locked():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from emptyos.capabilities.consent import CloudConsentManager
    from emptyos.web.routes_auth import register_cloud_routes

    saved = {}
    kernel = SimpleNamespace(
        cloud_consent=CloudConsentManager(policy="never", locked=True),
        settings=SimpleNamespace(set=lambda k, v: saved.__setitem__(k, v)),
    )
    server = FastAPI()
    register_cloud_routes(server, kernel)
    r = TestClient(server).post("/api/cloud/policy", json={"policy": "always"})
    assert r.status_code == 409
    assert saved == {} and kernel.cloud_consent.policy == "never"


@pytest.mark.parametrize("value, locked", [(1, True), (0, False), ("maybe", True)])
def test_the_lock_fails_closed_on_an_odd_value(tmp_path, value, locked):
    from emptyos.kernel.config import Config

    p = tmp_path / "e.toml"
    p.write_text(f"[cloud]\nlocked = {value!r}\n".replace("'", '"'), encoding="utf-8")
    assert Config(str(p)).cloud_locked is locked


def test_a_policy_block_outranks_a_failing_local_provider():
    class Broken(_Prov):
        async def execute(self, **kw):
            raise OSError("local model crashed")

    with pytest.raises(NotesToCloudOff) as exc:
        asyncio.run(_think(CLOUD(), Broken("ollama", cloud=False)).execute(prompt="n", caller_app="kb"))
    assert isinstance(exc.value.__cause__, OSError)


# ── every direct-provider path consults the rule ──────────────────────────

def _bare_kb_app(think):
    from fake_kernel import make_bare_app

    app = make_bare_app(think)
    app.kernel.note_scope = think.note_scope
    app.kernel.spend_cap = None
    app.manifest = SimpleNamespace(id="kb")
    return app


def test_a_pinned_cloud_provider_is_not_called_directly_for_a_listed_app():
    cloud = CLOUD()
    app = _bare_kb_app(_think(cloud))
    with pytest.raises(NotesToCloudOff):
        asyncio.run(app.pinned_execute("think", "openrouter", prompt="n"))
    assert cloud.calls == 0


def test_a_pinned_domain_provider_is_not_called_directly_for_a_listed_app():
    cloud = CLOUD()
    think = _think()
    think._domains = {"code": [cloud]}
    app = _bare_kb_app(think)
    with pytest.raises(RuntimeError):
        asyncio.run(app.pinned_execute("think", "openrouter", prompt="n"))
    assert cloud.calls == 0


def test_a_streamed_pin_is_not_called_directly_for_a_listed_app():
    cloud = CLOUD()
    app = _bare_kb_app(_think(cloud))

    async def drain():
        async for _ in app.think_stream("n", provider="openrouter"):
            pass

    with pytest.raises(NotesToCloudOff):
        asyncio.run(drain())
    assert cloud.calls == 0


def test_a_settings_chosen_domain_provider_is_skipped_for_a_listed_app():
    from emptyos.sdk import base_app_think

    cloud = CLOUD()
    think = _think()
    think._domains = {"code": [cloud]}
    kernel = SimpleNamespace(spend_cap=None, note_scope=think.note_scope,
                             capability=lambda name: think)

    class App:
        manifest = SimpleNamespace(id="kb")

        def __init__(self):
            self.kernel = kernel

        async def _emit_think_executed(self, *a, **k):
            return None

    assert asyncio.run(base_app_think._think_with_provider(App(), "openrouter", "n", None, {})) is None
    assert cloud.calls == 0


def test_kb_flipbook_refine_names_its_app_to_the_chain():
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "apps/public/standard/kb/flipbook_gen.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "execute"]
    assert calls, "expected the direct think execute call"
    for c in calls:
        assert "caller_app" in {k.arg for k in c.keywords}
