"""Agent-runtime detached spawn + file-tail awaiter.

`spawn_detached(cmd, ...)` returns immediately with the child pid; the child
inherits OS-level detach flags so it survives the spawning process's death
(the use case: dogfood-agent claude-cli runs that must outlive a restart.bat
killing :9000). `await_subprocess(pid, ...)` then polls via psutil + tails
the on-disk stdout to enforce timeout / idle / early-exit.

These tests drive both halves against tiny python subprocesses that simulate
the real shapes: chatty without progress, progress every N seconds, fast
exit, exit-before-attach (reattach simulation).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_PATH = REPO / "plugins" / "agent-runtime" / "plugin.py"


@pytest.fixture(scope="module")
def runtime_module():
    spec = importlib.util.spec_from_file_location(
        "agent_runtime_detached_under_test", PLUGIN_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(runtime_module):
    return runtime_module.AgentRuntimePlugin(kernel=None, manifest={})


def _tool_progress(line: bytes) -> bool:
    return b'"type":"tool_use"' in line or b'"type":"tool_result"' in line


def _paths(tmp_path: Path, name: str) -> tuple[Path, Path]:
    return tmp_path / f"{name}.out", tmp_path / f"{name}.err"


@pytest.mark.asyncio
async def test_spawn_returns_pid_and_writes_to_disk(plugin, tmp_path):
    """spawn_detached returns immediately with pid; child writes to
    stdout_path; await_subprocess sees the exit and reports rc 0."""
    out_path, err_path = _paths(tmp_path, "fast")
    # Brief sleep so await_subprocess (poll_s=0.2) reliably attaches while the
    # child is still alive — a trivial instant print races the attach and
    # intermittently reports exited_before_attach under machine load.
    code = (
        "import sys, time\n"
        "sys.stdout.write('hello world\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.6)\n"
    )
    spawn = plugin.spawn_detached(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        stdout_path=out_path,
        stderr_path=err_path,
    )
    assert spawn["pid"] > 0
    assert isinstance(spawn["started"], float)
    # create_time may be None on systems without psutil; we asserted psutil
    # is installed in conftest of the project, so expect non-None here.
    assert spawn["create_time"] is not None

    result = await plugin.await_subprocess(
        spawn["pid"],
        stdout_path=out_path,
        started=spawn["started"],
        create_time=spawn["create_time"],
        timeout_s=10.0,
        idle_timeout_s=None,
        poll_s=0.2,
    )
    assert result["timeout"] is False
    assert result["idle_timeout"] is False
    assert result["exited_before_attach"] is False
    assert result["returncode"] == 0
    assert out_path.read_text(encoding="utf-8").strip() == "hello world"


@pytest.mark.asyncio
async def test_idle_progress_predicate_kills_wedged_child(plugin, tmp_path):
    """Chatty subprocess emits non-progress lines forever; progress_predicate
    set; idle should fire and child should be killed."""
    out_path, err_path = _paths(tmp_path, "wedged")
    code = (
        "import sys, time\n"
        "for _ in range(200):\n"
        "    sys.stdout.write('{\"type\":\"assistant\"}\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.1)\n"
    )
    spawn = plugin.spawn_detached(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        stdout_path=out_path,
        stderr_path=err_path,
    )
    result = await plugin.await_subprocess(
        spawn["pid"],
        stdout_path=out_path,
        started=spawn["started"],
        create_time=spawn["create_time"],
        timeout_s=30.0,
        idle_timeout_s=3.0,
        progress_predicate=_tool_progress,
        poll_s=0.5,
    )
    assert result["idle_timeout"] is True, f"got {result}"
    assert result["timeout"] is False


@pytest.mark.asyncio
async def test_supervision_record_round_trip(plugin, tmp_path):
    """spawn_detached(supervision_key=...) writes a JSON record;
    list_supervised returns it; clear_supervision removes it."""
    plugin._state_dir_override = tmp_path / "supervised"
    out_path, err_path = _paths(tmp_path, "sup")
    code = "import sys; sys.stdout.write('x\\n'); sys.stdout.flush()\n"
    spawn = plugin.spawn_detached(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        stdout_path=out_path,
        stderr_path=err_path,
        supervision_key="dogfood-run-abc123",
        metadata={"scenario": "tuesday-evening", "persona": "kevin-weekday"},
    )
    records = plugin.list_supervised()
    assert len(records) == 1
    r = records[0]
    assert r["key"] == "dogfood-run-abc123"
    assert r["pid"] == spawn["pid"]
    assert r["metadata"]["scenario"] == "tuesday-evening"
    assert r["stdout_path"] == str(out_path)

    # Let the awaiter finish and verify the supervision file persists until
    # explicitly cleared (reattach pattern: a separate caller decides when to
    # finalize, not the awaiter itself).
    await plugin.await_subprocess(
        spawn["pid"], stdout_path=out_path, started=spawn["started"],
        create_time=spawn["create_time"], timeout_s=10.0, poll_s=0.2,
    )
    assert len(plugin.list_supervised()) == 1
    assert plugin.clear_supervision("dogfood-run-abc123") is True
    assert plugin.list_supervised() == []


@pytest.mark.asyncio
async def test_reattach_finds_exited_child(plugin, tmp_path):
    """Simulate a daemon restart: spawn with supervision_key, drop in-process
    handle, simulate process gone, call reattach(key) — should pull record
    from disk, return exited_before_attach=True + reattached=True, clear
    the supervision file."""
    plugin._state_dir_override = tmp_path / "supervised"
    out_path, err_path = _paths(tmp_path, "reattach2")
    code = "import sys; sys.stdout.write('payload\\n'); sys.stdout.flush()\n"
    spawn = plugin.spawn_detached(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        stdout_path=out_path,
        stderr_path=err_path,
        supervision_key="run-xyz",
        metadata={"scenario": "iphone-audit"},
    )
    await asyncio.sleep(1.5)
    # Simulate daemon restart — drop the in-process Popen handle.
    getattr(plugin, "_detached_handles", {}).pop(spawn["pid"], None)
    await asyncio.sleep(0.5)

    result = await plugin.reattach(
        "run-xyz", timeout_s=5.0, idle_timeout_s=None, poll_s=0.2,
    )
    assert result["reattached"] is True
    assert result["exited_before_attach"] is True
    assert result["metadata"]["scenario"] == "iphone-audit"
    # clear_on_finish defaults True → record gone.
    assert plugin.list_supervised() == []


@pytest.mark.asyncio
async def test_reattach_unknown_key_raises(plugin, tmp_path):
    plugin._state_dir_override = tmp_path / "supervised"
    with pytest.raises(KeyError):
        await plugin.reattach("does-not-exist")


@pytest.mark.asyncio
async def test_reattach_after_pid_gone(plugin, tmp_path):
    """Simulate a daemon restart: spawn → child exits → caller waits longer
    than child runtime → await_subprocess is invoked with the now-dead pid →
    reports exited_before_attach=True with rc None."""
    out_path, err_path = _paths(tmp_path, "reattach")
    code = (
        "import sys\nsys.stdout.write('done\\n'); sys.stdout.flush()\n"
    )
    spawn = plugin.spawn_detached(
        [sys.executable, "-u", "-c", code],
        cwd=str(REPO),
        stdout_path=out_path,
        stderr_path=err_path,
    )
    # Wait long enough for child to definitely exit.
    await asyncio.sleep(2.0)
    # Simulate "daemon restart" — drop the in-process handle the plugin
    # holds. Without this the same-lifetime path would still grab the
    # exit code via Popen.poll() and we'd never hit the reattach branch.
    getattr(plugin, "_detached_handles", {}).pop(spawn["pid"], None)
    # Give Windows a moment to fully release the PID once the handle is
    # dropped — without it the PID may still register as "running" briefly.
    await asyncio.sleep(0.5)

    result = await plugin.await_subprocess(
        spawn["pid"],
        stdout_path=out_path,
        started=spawn["started"],
        create_time=spawn["create_time"],
        timeout_s=10.0,
        idle_timeout_s=None,
        poll_s=0.2,
    )
    assert result["exited_before_attach"] is True
    assert result["returncode"] is None
    assert "done" in out_path.read_text(encoding="utf-8")
