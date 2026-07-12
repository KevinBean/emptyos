"""Unit tests for the podcast app's VibeVoice multi-speaker branch.

These assert the *fallback* contract — the whole point of Phase 1 is that the
new single-pass path can never regress the existing per-turn TTS path. We load
``apps/personal/podcast/pipeline.py`` standalone (no kernel boot, no daemon) and
drive ``_try_vibevoice_dialogue`` with a stub ``self``.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest
from helpers import app_path

_SCRIPT = [
    {"speaker": "A", "text": "Why does this matter?"},
    {"speaker": "B", "text": "Because the default should never change."},
]


def _load_pipeline():
    try:
        path = app_path("podcast") / "pipeline.py"
    except FileNotFoundError:
        pytest.skip("podcast app is not present in this checkout")
    spec = importlib.util.spec_from_file_location("podcast_pipeline_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _StubApp:
    """Minimal stand-in for PodcastApp covering only what the branch touches."""

    def __init__(self, *, flag=False, service=None):
        self._flag = flag
        self._service = service
        self.warnings: list[str] = []

    def app_config(self, key, default=None):
        if key == "vibevoice_enabled":
            return self._flag
        return default

    def service(self, _name):
        return self._service

    def log(self, *_a, **_k):
        pass

    def log_warn(self, msg, *_a, **_k):
        self.warnings.append(msg)


def test_disabled_by_default_returns_none(tmp_path):
    """Dark-default: flag off → None → caller uses per-turn TTS. No service touched."""
    pipe = _load_pipeline()
    app = _StubApp(flag=False)
    out = asyncio.run(
        pipe._try_vibevoice_dialogue(app, _SCRIPT, "emma", "michael", tmp_path, None)
    )
    assert out is None


def test_enabled_but_no_comfyui_returns_none(tmp_path):
    """Flag on but no comfyui service → None (graceful fallback)."""
    pipe = _load_pipeline()
    app = _StubApp(flag=True, service=None)
    out = asyncio.run(
        pipe._try_vibevoice_dialogue(app, _SCRIPT, "emma", "michael", tmp_path, None)
    )
    assert out is None


def test_enabled_but_service_lacks_generate_dialogue(tmp_path):
    """Flag on, comfyui present but missing generate_dialogue → None."""
    pipe = _load_pipeline()

    class _OldComfy:  # no generate_dialogue attribute
        async def available(self):
            return True

    app = _StubApp(flag=True, service=_OldComfy())
    out = asyncio.run(
        pipe._try_vibevoice_dialogue(app, _SCRIPT, "emma", "michael", tmp_path, None)
    )
    assert out is None


def test_enabled_but_comfyui_unreachable_returns_none(tmp_path):
    """Flag on, generate_dialogue present, but ComfyUI reports down → None + warn."""
    pipe = _load_pipeline()

    class _DownComfy:
        async def ensure_available(self):
            return False

        async def generate_dialogue(self, **_k):  # never reached
            return "/should/not/happen.mp3"

    app = _StubApp(flag=True, service=_DownComfy())
    out = asyncio.run(
        pipe._try_vibevoice_dialogue(app, _SCRIPT, "emma", "michael", tmp_path, None)
    )
    assert out is None
    assert any("ComfyUI unavailable" in w for w in app.warnings)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
