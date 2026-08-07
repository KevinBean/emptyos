"""Provider registration must not depend on ComfyUI winning a boot race.

Observed live 2026-07-28, on a healthy machine with ComfyUI up and answering:

    draw     providers: ['openai-gpt-image-1']   <- no comfyui
    animate  providers: []

ComfyUI starts *after* the daemon, so `connect()`'s `if await self.available()`
was False and the providers were never registered. `ensure_available()` was the
documented workaround, but only publish / music-studio / podcast / studio call
it — app-gen, explore, kb, ppt, reader and scroll call `self.draw()` directly
and were therefore billing the paid cloud provider with a 16GB GPU idle.

Registration is a declaration that the plugin *can* draw; availability is a
runtime question the capability chain already asks
(`emptyos/capabilities/__init__.py`: `if not await p.available(): continue`).
So registration is unconditional and `available()` carries the gate.

That moves the probe onto every draw resolution, which is why the negative
cache exists — and why it must stay one-directional.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "comfyui" / "plugin.py"


def _load():
    name = "eos_comfyui_plugin_reg_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


class _Cap:
    def __init__(self):
        self.providers = []

    def add_provider(self, provider, priority=0):
        self.providers.append(provider)


class _Caps:
    def __init__(self):
        self.draw, self.animate = _Cap(), _Cap()

    def get(self, name):
        return {"draw": self.draw, "animate": self.animate}.get(name)


class _Kernel:
    def __init__(self):
        self.capabilities = _Caps()
        self.syslog = types.SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            error=lambda *a, **k: None)


def _plugin(reachable: bool):
    p = mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)
    p.kernel = _Kernel()
    p._config = {}
    p._session = None
    p._unavailable_until = 0.0
    p._draw_registered = False
    p.probes = 0

    async def available():
        p.probes += 1
        return reachable

    p.available = available
    return p


# --- the race ---------------------------------------------------------------

@pytest.mark.parametrize("reachable", [True, False])
def test_providers_register_whether_or_not_comfyui_is_up_at_boot(reachable):
    p = _plugin(reachable)
    # `connect()` opens a real aiohttp session, so exercise the registration
    # path directly — that call is the behaviour under test, and the parameter
    # proves it no longer consults reachability.
    p._register_draw()
    assert [x.name for x in p.kernel.capabilities.draw.providers] == ["comfyui"]
    assert [x.name for x in p.kernel.capabilities.animate.providers] == ["comfyui-ltx"]


def test_registration_is_idempotent():
    """ensure_available() still calls it; a second call must not double-register."""
    p = _plugin(True)
    p._register_draw()
    p._register_draw()
    assert len(p.kernel.capabilities.draw.providers) == 1
    assert len(p.kernel.capabilities.animate.providers) == 1


# --- the negative cache -----------------------------------------------------

def test_a_failed_probe_is_cached_so_a_batch_does_not_pay_the_timeout():
    p = mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)
    p._config = {}
    p._unavailable_until = 0.0
    calls = {"n": 0}

    class _Resp:
        status = 500
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Session:
        def get(self, *a, **k):
            calls["n"] += 1
            return _Resp()

    p._session = _Session()
    for _ in range(5):
        assert asyncio.run(mod.ComfyUIPlugin.available(p)) is False
    assert calls["n"] == 1, (
        f"a down server was probed {calls['n']}x for 5 calls — a 20-image batch "
        f"would wait out 20 timeouts"
    )


def test_a_successful_probe_is_never_cached():
    """A server that dies mid-run must be noticed on the very next call, so the
    cache has to be one-directional."""
    p = mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)
    p._config = {}
    p._unavailable_until = 0.0
    calls = {"n": 0}

    class _Resp:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Session:
        def get(self, *a, **k):
            calls["n"] += 1
            return _Resp()

    p._session = _Session()
    for _ in range(3):
        assert asyncio.run(mod.ComfyUIPlugin.available(p)) is True
    assert calls["n"] == 3, "a True must never be cached"


def test_recovery_is_bounded_by_the_ttl():
    """The cache must expire, or a ComfyUI started later is never picked up."""
    assert 0 < mod.ComfyUIPlugin._UNAVAILABLE_TTL <= 30, (
        "the negative cache has to be short — it is the recovery latency for a "
        "ComfyUI that starts after the daemon, which is the normal case"
    )
