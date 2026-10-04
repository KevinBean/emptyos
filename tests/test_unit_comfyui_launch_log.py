"""A logging change must never be able to stop ComfyUI from starting.

``auto_start`` is the unattended launch path — it fires when ComfyUI has died
mid-session and nobody is watching. Redirecting its output to a file bought the
boot/crash text that ``DEVNULL`` was throwing away (the per-prompt timings were
never at stake; ComfyUI writes those itself to ``ComfyUI/user/comfyui.log``).

That trade is only worth it while it stays strictly subordinate to launching.
A read-only directory, a log held open by another process, a full disk — none of
those are reasons to leave the GPU idle, so the open is allowed to fail and the
launch proceeds on DEVNULL. The parent's handle must also be released once the
child owns its inherited copy, or the daemon pins the file for the whole 60s
startup wait and the next restart cannot rotate it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "comfyui" / "plugin.py"


def _load():
    name = "eos_comfyui_plugin_launchlog_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


mod = _load()


class _FakePopen:
    """Record what the launch asked for, without spawning anything."""

    calls: list[dict] = []

    def __init__(self, argv, **kw):
        _FakePopen.calls.append({"argv": argv, **kw})


def _plugin(launcher: Path):
    p = mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)
    p.kernel = types.SimpleNamespace(
        capabilities=types.SimpleNamespace(get=lambda _n: None),
        syslog=types.SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            error=lambda *a, **k: None),
    )
    p._config = {"launcher": str(launcher)}
    p._draw_registered = False
    p.config = lambda key, default=None: p._config.get(key, default)
    p._register_draw = lambda: None

    seen = {"n": 0}

    async def available():
        # False once so the launch happens, then True so the wait loop exits.
        seen["n"] += 1
        return seen["n"] > 1

    p.available = available
    return p


@pytest.fixture(autouse=True)
def _fast_and_fake(monkeypatch):
    _FakePopen.calls = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


def _launcher(tmp_path: Path) -> Path:
    (tmp_path / "python_embeded").mkdir()
    (tmp_path / "ComfyUI").mkdir()
    return tmp_path / "run.bat"


def test_launch_output_lands_in_the_log_and_stderr_joins_it(tmp_path):
    launcher = _launcher(tmp_path)
    assert asyncio.run(_plugin(launcher).auto_start()) is True

    call = _FakePopen.calls[0]
    log = tmp_path / "comfyui.log"
    assert call["stdout"] is not subprocess.DEVNULL
    assert call["stdout"].name == str(log)
    # A traceback splits across both streams; one file or it is unreadable.
    assert call["stderr"] is subprocess.STDOUT


def test_the_log_is_appended_never_truncated(tmp_path):
    launcher = _launcher(tmp_path)
    log = tmp_path / "comfyui.log"
    log.write_bytes(b"BOOT FROM THE PREVIOUS LAUNCH\n")

    assert asyncio.run(_plugin(launcher).auto_start()) is True

    # Assert the mode, not merely that the bytes survived: with no redirect at
    # all nothing writes to the file either, so a surviving-content check passes
    # against the very behaviour this is meant to pin.
    # restart.bat rotates and writes this same path; truncating here would erase
    # the boot that preceded a mid-session death — the context that explains it.
    handle = _FakePopen.calls[0]["stdout"]
    assert handle.mode == "ab"
    assert log.read_bytes().startswith(b"BOOT FROM THE PREVIOUS LAUNCH")


def test_parent_releases_the_handle_so_the_next_restart_can_rotate(tmp_path):
    launcher = _launcher(tmp_path)
    assert asyncio.run(_plugin(launcher).auto_start()) is True

    handle = _FakePopen.calls[0]["stdout"]
    assert handle.closed, "daemon pinned the log for the whole startup wait"


def test_an_unopenable_log_still_launches_comfyui(tmp_path, monkeypatch):
    launcher = _launcher(tmp_path)
    real_open = Path.open

    refused = []

    def refuse(self, *a, **kw):
        if self.name == "comfyui.log":
            refused.append(self)
            raise OSError("read-only directory")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", refuse)

    # The GPU is the point; the log is a nicety. Never invert that.
    assert asyncio.run(_plugin(launcher).auto_start()) is True
    # Without this the test also passes against a build that never opens a log
    # at all — it would assert the fallback while proving only the absence.
    assert refused, "the log open was never attempted, so no fallback was taken"
    call = _FakePopen.calls[0]
    assert call["stdout"] is subprocess.DEVNULL
    assert call["stderr"] is subprocess.DEVNULL
