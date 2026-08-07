"""Unit tests for the ComfyUI VRAM pre-flight guard + health's gpu_reserve.

No daemon, no GPU, no ComfyUI. Both objects are exercised through fakes.

Two properties matter more than the happy path, because getting either wrong
turns a safety feature into an outage:

1. **Dark by default.** With the flag unset the guard must not call health at
   all — the queueing path is byte-for-byte what it was.
2. **Fail-soft in every direction.** A missing health service, a health service
   that raises, a garbage config value — none may propagate out of _preflight.
   The check exists to make a render more likely to succeed; a check that can
   itself stop a render is worse than no check.
"""

from __future__ import annotations

import asyncio
import importlib.util
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_comfyui_plugin():
    """Load plugins/comfyui/plugin.py standalone (no kernel boot)."""
    spec = importlib.util.spec_from_file_location(
        "comfyui_plugin_under_test", REPO / "plugins" / "comfyui" / "plugin.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeSyslog:
    def __init__(self):
        self.lines = []

    def info(self, tag, msg):
        self.lines.append(("info", tag, msg))

    def warning(self, tag, msg):
        self.lines.append(("warning", tag, msg))

    def error(self, tag, msg):
        self.lines.append(("error", tag, msg))


class FakeHealth:
    def __init__(self, verdict=None, raises=False):
        self.verdict = verdict or {"headroom_gb": 9.6, "reason": "fits", "freed": None}
        self.raises = raises
        self.calls = []

    async def gpu_reserve(self, need_gb, *, free_if_needed=True):
        self.calls.append(need_gb)
        if self.raises:
            raise RuntimeError("health exploded")
        return self.verdict


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return FakeResponse(self.payload, self.status)


def _make_plugin(mod, *, config: dict, health=None):
    plugin = mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)
    plugin._config = dict(config)
    services = types.SimpleNamespace(get=lambda name: health if name == "health" else None)
    plugin.kernel = types.SimpleNamespace(services=services, syslog=FakeSyslog())
    return plugin


@pytest.fixture(scope="module")
def mod():
    return _load_comfyui_plugin()


# --- dark by default -----------------------------------------------------

def test_disabled_by_default_never_touches_health(mod):
    health = FakeHealth()
    p = _make_plugin(mod, config={}, health=health)
    asyncio.run(p._preflight("image"))
    assert health.calls == [], "guard must be inert until the flag is set"
    assert p.kernel.syslog.lines == []


def test_explicit_false_is_also_off(mod):
    health = FakeHealth()
    p = _make_plugin(mod, config={"feature.gpu-arbiter.enabled": False}, health=health)
    asyncio.run(p._preflight("image"))
    assert health.calls == []


# --- enabled: uses the right estimate per job kind ----------------------

@pytest.mark.parametrize("kind,expected", [("image", 12.0), ("video", 18.0), ("depth", 2.0)])
def test_enabled_passes_the_per_kind_estimate(mod, kind, expected):
    health = FakeHealth()
    p = _make_plugin(mod, config={"feature.gpu-arbiter.enabled": True}, health=health)
    asyncio.run(p._preflight(kind))
    assert health.calls == [expected]


def test_estimate_is_config_overridable(mod):
    health = FakeHealth()
    p = _make_plugin(
        mod,
        config={"feature.gpu-arbiter.enabled": True, "vram_need_image": 7.5},
        health=health,
    )
    asyncio.run(p._preflight("image"))
    assert health.calls == [7.5]


def test_verdict_is_logged(mod):
    health = FakeHealth({"headroom_gb": 3.1, "reason": "fits_after_free", "freed": {"ollama": {}}})
    p = _make_plugin(mod, config={"feature.gpu-arbiter.enabled": True}, health=health)
    asyncio.run(p._preflight("video"))
    msg = " ".join(m for _, _, m in p.kernel.syslog.lines)
    assert "fits_after_free" in msg and "freed=True" in msg


def test_unknown_kind_with_no_estimate_skips(mod):
    health = FakeHealth()
    p = _make_plugin(mod, config={"feature.gpu-arbiter.enabled": True}, health=health)
    asyncio.run(p._preflight("something-new"))
    assert health.calls == [], "no estimate -> nothing to check, don't invent one"


# --- fail-soft: the guard can never be why a render didn't happen -------

def test_missing_health_service_is_silent(mod):
    p = _make_plugin(mod, config={"feature.gpu-arbiter.enabled": True}, health=None)
    asyncio.run(p._preflight("image"))  # must not raise


def test_health_raising_is_swallowed(mod):
    health = FakeHealth(raises=True)
    p = _make_plugin(mod, config={"feature.gpu-arbiter.enabled": True}, health=health)
    asyncio.run(p._preflight("image"))  # must not raise
    assert any(lvl == "warning" for lvl, _, _ in p.kernel.syslog.lines)


def test_garbage_config_is_swallowed(mod):
    health = FakeHealth()
    p = _make_plugin(
        mod,
        config={"feature.gpu-arbiter.enabled": True, "vram_need_image": "not-a-number"},
        health=health,
    )
    asyncio.run(p._preflight("image"))  # must not raise
    assert health.calls == []


def test_health_without_gpu_reserve_is_skipped(mod):
    """An older health plugin that predates the arbiter."""
    legacy = types.SimpleNamespace()
    p = _make_plugin(mod, config={"feature.gpu-arbiter.enabled": True}, health=legacy)
    asyncio.run(p._preflight("image"))  # must not raise


# --- runtime compatibility ----------------------------------------------

def _runtime_plugin(mod, system, config=None):
    p = _make_plugin(mod, config=config or {}, health=None)
    p._session = FakeSession({"system": system})
    return p


def test_runtime_accepts_exact_companion_packages(mod):
    p = _runtime_plugin(mod, {
        "comfyui_version": "0.28.3",
        "comfy_package_versions": [
            {"name": "comfy-kitchen", "installed": "0.2.20", "required": "0.2.20"},
            {"name": "comfy-aimdo", "installed": "0.4.10", "required": "0.4.10"},
        ],
    }, {"minimum_version": "0.28.0"})
    asyncio.run(p._ensure_runtime_compatible())


def test_runtime_rejects_reachable_half_migration(mod):
    p = _runtime_plugin(mod, {
        "comfyui_version": "0.28.3",
        "comfy_package_versions": [
            {"name": "comfy-kitchen", "installed": None, "required": "0.2.20"},
        ],
    })
    with pytest.raises(RuntimeError, match="comfy-kitchen"):
        asyncio.run(p._ensure_runtime_compatible())


def test_runtime_rejects_configured_old_core(mod):
    p = _runtime_plugin(
        mod,
        {"comfyui_version": "0.17.0"},
        {"minimum_version": "0.28.0"},
    )
    with pytest.raises(RuntimeError, match="below configured minimum"):
        asyncio.run(p._ensure_runtime_compatible())


def test_runtime_old_server_without_package_telemetry_remains_compatible(mod):
    p = _runtime_plugin(mod, {"comfyui_version": "0.17.0"})
    asyncio.run(p._ensure_runtime_compatible())


def test_runtime_check_is_cached(mod):
    p = _runtime_plugin(mod, {"comfyui_version": "0.28.3"})
    asyncio.run(p._ensure_runtime_compatible())
    asyncio.run(p._ensure_runtime_compatible())
    assert p._session.calls == 1


def test_runtime_gate_can_be_disabled(mod):
    p = _runtime_plugin(
        mod,
        {"comfy_package_versions": [
            {"name": "broken", "installed": None, "required": "1.0"},
        ]},
        {"feature.runtime-compatibility.enabled": False},
    )
    asyncio.run(p._ensure_runtime_compatible())
    assert p._session.calls == 0


# --- health.gpu_reserve orchestration -----------------------------------

def test_gpu_reserve_frees_only_ollama_when_short():
    """The targeted eviction is the whole point — a blanket free would evict
    the ComfyUI checkpoint the next job is about to reload."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "health_plugin_under_test", REPO / "plugins" / "health" / "plugin.py"
    )
    hmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hmod)

    plugin = hmod.HealthPlugin.__new__(hmod.HealthPlugin)
    freed_targets = []
    statuses = [
        # before: 16 GB card, 14 GB resident (6.6 of it ollama) -> must free
        {"vram_total_gb": 16.0, "vram_used_gb": 14.0, "headroom_gb": 0.0,
         "ollama": {"total_vram_gb": 6.6}, "comfyui": {"running": True}},
        # after freeing ollama
        {"vram_total_gb": 16.0, "vram_used_gb": 7.4, "headroom_gb": 6.6,
         "ollama": {"total_vram_gb": 0.0}, "comfyui": {"running": True}},
    ]

    async def fake_status():
        return statuses.pop(0) if len(statuses) > 1 else statuses[0]

    async def fake_free(targets=None):
        freed_targets.append(targets)
        return {"ollama": {"ok": True}}

    plugin.gpu_status = fake_status
    plugin.gpu_free = fake_free

    out = asyncio.run(plugin.gpu_reserve(6.0))
    assert out["ok"] is True
    assert freed_targets == [("ollama",)], "must target ollama only, not both"
    assert out["reason"] == "fits_after_free"


def test_gpu_reserve_does_nothing_when_it_already_fits():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "health_plugin_under_test2", REPO / "plugins" / "health" / "plugin.py"
    )
    hmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hmod)

    plugin = hmod.HealthPlugin.__new__(hmod.HealthPlugin)
    freed = []

    async def fake_status():
        return {"vram_total_gb": 32.0, "vram_used_gb": 6.6, "headroom_gb": 23.4,
                "ollama": {"total_vram_gb": 6.6}, "comfyui": {"running": True}}

    async def fake_free(targets=None):
        freed.append(targets)
        return {}

    plugin.gpu_status = fake_status
    plugin.gpu_free = fake_free

    out = asyncio.run(plugin.gpu_reserve(12.0))
    assert out["reason"] == "fits"
    assert freed == [], "32 GB holds FLUX + ollama together — nothing to evict"


# --- model residency (Phase 2.3) ----------------------------------------
#
# The regression risk here is one-directional: keeping models resident when
# there ISN'T room turns a slow render into an OOM. So the 16 GB path (free
# every time, exactly as today) is pinned as hard as the 32 GB path.

class FakeHealthStatus:
    def __init__(self, headroom):
        self.headroom = headroom

    async def gpu_status(self):
        return {"headroom_gb": self.headroom}


def _residency_plugin(mod, *, enabled, headroom, floor=8.0):
    p = _make_plugin(
        mod,
        config={"feature.model-residency.enabled": enabled, "residency_min_vram_gb": floor},
        health=FakeHealthStatus(headroom),
    )
    p.frees = 0

    async def fake_free():
        p.frees += 1

    p.free_gpu = fake_free
    return p


def test_residency_off_frees_every_time(mod):
    """Regression pin on today's behaviour: 3 generations -> 3 frees."""
    p = _residency_plugin(mod, enabled=False, headroom=99.0)

    async def run():
        async with p.gpu_session("stills"):
            for _ in range(3):
                await p._maybe_free_gpu()

    asyncio.run(run())
    assert p.frees == 3 + 1  # 3 per-generation frees + 1 on session exit


def test_residency_on_with_room_keeps_models_warm(mod):
    """32 GB-class headroom: 0 frees inside the session, exactly 1 on exit."""
    p = _residency_plugin(mod, enabled=True, headroom=20.0)
    inside = {}

    async def run():
        async with p.gpu_session("stills"):
            for _ in range(3):
                await p._maybe_free_gpu()
            inside["frees"] = p.frees

    asyncio.run(run())
    assert inside["frees"] == 0, "should not evict between scenes when there's room"
    assert p.frees == 1, "exactly one free, on session exit"


def test_residency_on_without_room_still_frees(mod):
    """The 16 GB path — headroom below the floor falls through to a free."""
    p = _residency_plugin(mod, enabled=True, headroom=1.5, floor=8.0)

    async def run():
        async with p.gpu_session("stills"):
            for _ in range(3):
                await p._maybe_free_gpu()

    asyncio.run(run())
    assert p.frees == 3 + 1, "no room -> behave exactly as the flag were off"


def test_free_outside_a_session_is_unconditional(mod):
    p = _residency_plugin(mod, enabled=True, headroom=99.0)
    asyncio.run(p._maybe_free_gpu())
    assert p.frees == 1


def test_session_frees_once_even_when_body_raises(mod):
    p = _residency_plugin(mod, enabled=True, headroom=99.0)

    async def run():
        async with p.gpu_session("stills"):
            raise RuntimeError("render blew up")

    with pytest.raises(RuntimeError):
        asyncio.run(run())
    assert p.frees == 1, "crash-safety: the finally must still free"


def test_nested_sessions_free_only_at_the_outer_exit(mod):
    p = _residency_plugin(mod, enabled=True, headroom=99.0)

    async def run():
        async with p.gpu_session("outer"):
            async with p.gpu_session("inner"):
                await p._maybe_free_gpu()
            assert p.frees == 0, "inner exit must not free"

    asyncio.run(run())
    assert p.frees == 1


def test_missing_health_falls_through_to_free(mod):
    """Any doubt -> free. An unreadable GPU must not leave models pinned."""
    p = _make_plugin(mod, config={"feature.model-residency.enabled": True}, health=None)
    p.frees = 0

    async def fake_free():
        p.frees += 1

    p.free_gpu = fake_free

    async def run():
        async with p.gpu_session("stills"):
            await p._maybe_free_gpu()

    asyncio.run(run())
    assert p.frees == 2  # the guarded free + the session-exit free
