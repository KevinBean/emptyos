"""A locked build ([cloud] locked) runs the operator's think chain.

A learner-saved `think.app.<id>` / `think.domain.<d>` override could route to
any provider the build registers, so in a locked build every routing reader
ignores it (`routing_settings`) and Settings refuses new `think.*` writes.
Each reader is exercised through its real call path, not only the helper, so
a site that goes back to reading settings directly turns a test red.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.base_app_think import routing_settings

OVERRIDES = {
    "think.app.dictionary": "sneaky",
    "think.domain.text": "sneaky",
}


class FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def all(self):
        return dict(self.values)


class Provider:
    def __init__(self, name):
        self.name, self.model, self.is_cloud = name, f"{name}-model", False
        self._current_load = 0

    async def available(self):
        return True

    async def execute_stream(self, prompt="", **kwargs):
        yield {"text": self.name, "done": True}


class FakeThink:
    """The capability chain: answers "chain" and names its first provider."""

    def __init__(self):
        self.providers = [Provider("operator"), Provider("sneaky")]
        self._domains = {}
        self.calls = 0

    def providers_for(self, domain=None):
        return list(self.providers)

    def _get_providers(self, domain=None):
        return list(self.providers)

    async def execute(self, *args, **kwargs):
        self.calls += 1
        return SimpleNamespace(value="chain", provider="operator", model="operator-model",
                               cost=0.0, tokens=0, duration_ms=0, is_cloud=False)

    async def execute_stream(self, prompt="", **kwargs):
        yield {"text": "chain", "done": True}

    def last_provenance(self):
        return {}


async def _noop(*args, **kwargs):
    return None


def _kernel(locked: bool, settings: FakeSettings):
    think_cap = FakeThink()
    return SimpleNamespace(
        config=SimpleNamespace(cloud_locked=locked),
        services=SimpleNamespace(get_optional=lambda name: settings if name == "settings" else None),
        capability=lambda name: think_cap,
        capabilities=SimpleNamespace(get=lambda name: think_cap),
        events=SimpleNamespace(emit=_noop),
    )


def _app(locked: bool):
    app = BaseApp.__new__(BaseApp)
    app.kernel = _kernel(locked, FakeSettings(OVERRIDES))
    app.manifest = SimpleNamespace(id="dictionary")
    routed: list[str] = []

    async def fake_provider(provider, prompt, domain, kwargs):
        routed.append(provider)
        return "override"

    app._think_with_provider = fake_provider
    return app, routed


def test_routing_settings_is_none_only_when_locked():
    s = FakeSettings()
    assert routing_settings(_kernel(True, s)) is None
    assert routing_settings(_kernel(False, s)) is s


@pytest.mark.parametrize("locked", [True, False])
def test_think_ignores_saved_routing_only_when_locked(locked):
    app, routed = _app(locked)
    out = asyncio.run(BaseApp.think(app, "hello", domain="text"))
    assert out == ("chain" if locked else "override")
    assert routed == ([] if locked else ["sneaky"])


@pytest.mark.parametrize("locked", [True, False])
def test_think_stream_ignores_saved_routing_only_when_locked(locked, monkeypatch):
    from emptyos.capabilities import cloud_gate

    monkeypatch.setattr(cloud_gate, "check", lambda *a, **k: None)
    app, _ = _app(locked)

    async def collect():
        return [c["text"] async for c in BaseApp.think_stream(app, "hello", domain="text")
                if isinstance(c, dict) and "text" in c]

    assert asyncio.run(collect()) == (["chain"] if locked else ["sneaky"])


@pytest.mark.parametrize("locked", [True, False])
def test_model_ability_names_the_operator_provider_only_when_locked(locked):
    app, _ = _app(locked)
    got = asyncio.run(BaseApp.model_ability(app, "text"))
    assert got["provider"] == ("operator" if locked else "sneaky")


@pytest.mark.parametrize("locked", [True, False])
def test_settings_refuses_think_writes_only_when_locked(locked):
    from helpers import load_app_module

    mod = load_app_module("settings", "app")
    settings = FakeSettings()
    app = mod.SettingsApp.__new__(mod.SettingsApp)
    app.kernel = _kernel(locked, settings)
    app._settings = lambda: settings
    emitted = []

    async def emit(name, payload):
        emitted.append(name)

    app.emit = emit

    class Req:
        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body

    one = asyncio.run(mod.SettingsApp.api_set(app, Req({"key": "think.domain.text", "value": "sneaky"})))
    bulk = asyncio.run(mod.SettingsApp.api_set_bulk(
        app, Req({"system.theme": "nord", "think.app.dictionary": "sneaky"})))
    # The bare key: set() stores a dict under it as think.domain.* etc.
    nested = asyncio.run(mod.SettingsApp.api_set(
        app, Req({"key": "think", "value": {"domain": {"text": "sneaky"}}})))
    assert ("error" in nested) is locked
    if locked:
        assert "error" in one and "error" in bulk
        assert settings.values == {}          # all or nothing: the theme was not saved either
    else:
        assert "error" not in one and bulk == {"updated": ["system.theme", "think.app.dictionary"]}
        assert settings.values["think.domain.text"] == "sneaky"
    # Non-think keys are never refused.
    ok = asyncio.run(mod.SettingsApp.api_set(app, Req({"key": "system.theme", "value": "nord"})))
    assert "error" not in ok


def _code(path) -> str:
    """Source with comments and docstrings removed, so a check cannot be
    satisfied by an explanatory comment."""
    import io
    import tokenize

    out = []
    toks = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    prev = None
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT):
            continue  # a docstring / bare string statement
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok.type
    return " ".join(out)


def test_routing_readers_without_a_behaviour_test_use_routing_settings():
    """The model-pill route and the assistant/agent labels are pinned here
    (the think()/think_stream()/model_ability readers are driven above). Read
    from code only; nothing in base_app_think may take settings directly
    except routing_settings and the two non-routing readers (context
    packing, output language)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    direct = re.compile(r"""(get_optional|service|get) \( ['"]settings['"] \)""")
    think_code = _code(root / "emptyos/sdk/base_app_think.py")
    # context packing, output language, and routing_settings itself
    assert len(direct.findall(think_code)) == 3

    server = _code(root / "emptyos/web/server.py")
    route = server[server.index("/api/capabilities/think/effective"):]
    route = route[:route.index("override_domain")]
    assert "routing_settings ( kernel )" in route and not direct.search(route)

    for app in ("apps/public/standard/assistant/app.py", "apps/public/standard/agent/app.py"):
        code = _code(root / app)
        assert "routing_settings ( self . kernel )" in code, app


def test_the_model_pill_is_told_the_build_is_locked():
    """The pill cannot switch in a locked build, so the route says so and the
    pill binds no popover."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    server = _code(root / "emptyos/web/server.py")
    route = server[server.index("/api/capabilities/think/effective"):]
    route = route[:route.index("/api/tailnet")]
    assert '"locked" : bool ( getattr ( kernel . config , "cloud_locked" , False ) )' in route
    js = (root / "emptyos/web/static/eos-components.js").read_text(encoding="utf-8")
    js = "\n".join(line for line in js.splitlines() if not line.strip().startswith("//"))
    assert "var locked = !!eff.locked;" in js
    assert "if (locked) pillBtn.setAttribute('aria-disabled', 'true');" in js
    assert "else pillBtn.onclick = openPopover;" in js
