"""Agent-runtime terminate(key) — the cancel primitive behind run-center's
"swarm cancel" UX.

terminate(key) kills the supervised child agent-runtime OWNS, guarded by a
create_time match (the same PID-reuse guard await_subprocess uses), then clears
the supervision record. It must:
  - kill a live owned child + clear the record,
  - REFUSE to kill when create_time doesn't match (PID reuse) but still drop
    the stale record,
  - return a benign result for an unknown key.

Driven against tiny python subprocesses, same shape as
test_unit_agent_runtime_detached.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import psutil
import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_PATH = REPO / "plugins" / "agent-runtime" / "plugin.py"


@pytest.fixture(scope="module")
def runtime_module():
    spec = importlib.util.spec_from_file_location(
        "agent_runtime_terminate_under_test", PLUGIN_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(runtime_module, tmp_path):
    p = runtime_module.AgentRuntimePlugin(kernel=None, manifest={})
    p._state_dir_override = tmp_path / "supervised"
    return p


def _paths(tmp_path: Path, name: str) -> tuple[Path, Path]:
    return tmp_path / f"{name}.out", tmp_path / f"{name}.err"


# A child that runs effectively forever so it's alive when we terminate it.
_SLEEPER = "import time\nfor _ in range(600): time.sleep(0.5)\n"


def test_terminate_kills_owned_child_and_clears_record(plugin, tmp_path):
    out_path, err_path = _paths(tmp_path, "kill")
    spawn = plugin.spawn_detached(
        [sys.executable, "-u", "-c", _SLEEPER],
        cwd=str(REPO),
        stdout_path=out_path,
        stderr_path=err_path,
        supervision_key="run-kill-1",
        metadata={"scenario": "cancel-me"},
    )
    pid = spawn["pid"]
    assert psutil.pid_exists(pid)
    assert len(plugin.list_supervised()) == 1

    res = plugin.terminate("run-kill-1", grace_s=3.0)
    assert res["ok"] is True
    assert res["killed"] is True
    assert res["pid"] == pid
    # Record gone.
    assert plugin.list_supervised() == []
    # Process actually dead (give the OS a moment to reap).
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            p = psutil.Process(pid)
            if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"pid {pid} still running after terminate")


def test_terminate_refuses_on_pid_reuse(plugin, tmp_path):
    """If the supervision record's create_time no longer matches the live PID,
    the original child is gone and the PID was reused — terminate must NOT kill
    it, only drop the stale record."""
    out_path, err_path = _paths(tmp_path, "reuse")
    spawn = plugin.spawn_detached(
        [sys.executable, "-u", "-c", _SLEEPER],
        cwd=str(REPO),
        stdout_path=out_path,
        stderr_path=err_path,
        supervision_key="run-reuse-1",
        metadata={},
    )
    pid = spawn["pid"]
    try:
        # Corrupt the recorded create_time so the guard treats this PID as
        # reused by an unrelated process.
        sf = plugin._supervision_file("run-reuse-1")
        record = json.loads(sf.read_text(encoding="utf-8"))
        record["create_time"] = (record.get("create_time") or time.time()) - 9999.0
        sf.write_text(json.dumps(record), encoding="utf-8")

        res = plugin.terminate("run-reuse-1", grace_s=2.0)
        assert res["ok"] is True
        assert res["killed"] is False
        assert res["reason"] == "pid_reused"
        # Stale record dropped...
        assert plugin.list_supervised() == []
        # ...but the live process is untouched.
        assert psutil.pid_exists(pid)
        assert psutil.Process(pid).is_running()
    finally:
        # Clean up the still-running sleeper.
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            pass


def test_terminate_unknown_key_is_benign(plugin):
    res = plugin.terminate("does-not-exist")
    assert res["ok"] is False
    assert res["killed"] is False
    assert res["reason"] == "no_supervision_record"
