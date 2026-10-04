"""The pronounce plugin never warms the model up at boot unless configured to.

Warmup makes the scoring service load its wav2vec2 model at once, and on a
machine with torch + transformers that is a ~1.2 GB download. Since the
plugin ships in the public EnglishOS edition (editions-build A2, 2026-09-29),
an unconfigured boot must not start it; `[plugins.pronounce] warmup_on_boot =
true` opts in. The service still loads the model lazily on the first /score.
Both boot paths are pinned: the service already up, and the plugin starting
its embedded service (the one a fresh boot takes).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_common import load_by_path  # noqa: E402

mod = load_by_path("pronounce_plugin_under_test", "plugins/pronounce/plugin.py")


class _Caps:
    def has(self, name):
        return False


class _Kernel:
    capabilities = _Caps()


def _plugin(settings: dict):
    p = mod.PronouncePlugin.__new__(mod.PronouncePlugin)
    p.kernel = _Kernel()
    p._session = None
    p._embedded_proc = None
    p.config = lambda key, default=None: settings.get(key, default)
    calls = []

    async def up():
        return True

    async def warmup():
        calls.append("warmup")

    p._service_up = up
    p._warmup = warmup
    return p, calls


@pytest.mark.parametrize("settings, expected", [
    ({}, []),
    ({"warmup_on_boot": False}, []),
    ({"warmup_on_boot": "true"}, []),   # a string is not an opt-in
    ({"warmup_on_boot": True}, ["warmup"]),
])
def test_warmup_runs_only_when_opted_in(settings, expected):
    p, calls = _plugin(settings)

    async def run():
        try:
            await p.connect()
        finally:
            if p._session is not None:
                await p._session.close()

    asyncio.run(run())
    assert calls == expected


def test_embedded_start_path_never_warms_up_or_passes_a_warmup_trigger(monkeypatch):
    """A fresh boot finds no service running, so `connect()` spawns the
    embedded one. That path must neither warm the model up nor hand the child
    anything beyond port/model settings that could make it load at start."""
    import os

    p, calls = _plugin({})
    state = {"up": False, "env": None}

    async def up():
        return state["up"]

    async def fake_exec(*args, env=None, **kwargs):
        state["env"] = env
        state["up"] = True  # the child's listener comes up
        return type("Proc", (), {"returncode": None})()

    async def no_sleep(_):
        return None

    p._service_up = up
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def run():
        try:
            await p.connect()
        finally:
            if p._session is not None:
                await p._session.close()

    asyncio.run(run())
    assert state["env"] is not None, "the embedded service was never spawned"
    assert calls == []
    added = {k for k in state["env"] if k not in os.environ or state["env"][k] != os.environ[k]}
    assert added <= {"PRONOUNCE_API_PORT"}, added


def test_health_names_missing_scoring_packages(monkeypatch):
    import importlib.util

    server = load_by_path("pronounce_server_under_test", "services/pronounce/server.py")
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name, *a, **k: None if name == "torch" else real(name, *a, **k),
    )
    assert "torch" in server._missing_deps()
    assert "torch" in asyncio.run(server.health())["deps_missing"]


def test_provider_is_unavailable_while_packages_are_missing():
    from emptyos.capabilities.providers.pronounce_local import LocalPronounceProvider

    class _Plugin:
        host = "http://127.0.0.1:8610"

        def _host(self):
            return self.host

        async def health(self):
            return {"model_state": "idle", "deps_missing": ["torch"]}

    provider = LocalPronounceProvider(_Plugin())
    assert asyncio.run(provider.available()) is False
    h = asyncio.run(provider.health())
    assert h["available"] is False and "torch" in h["reason"]
