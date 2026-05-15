"""Unit tests for emptyos.sdk.daemon_supervisor — shared subprocess
supervision used by both `plugins/dogfood-demo/` (single :9001 sidecar)
and `plugins/sandbox-pool/` (multi-member :9002+ pool).

These tests exercise the helpers in isolation:
- `resolve_python_exe()` — swap-out logic for pythonw.exe
- `spawn_emptyos_daemon()` — env construction + Popen call shape
- `terminate_daemon()` — terminate → kill escalation, port-free polling

No subprocess actually spawns in the unit layer — we stub `subprocess.Popen`
and patch the module's `subprocess.TimeoutExpired` so we can simulate every
branch without paying for a real daemon boot.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make repo root importable.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from emptyos.sdk import daemon_supervisor as ds


class TestResolvePythonExe:
    def test_normal_python_exe_returned_unchanged(self):
        with patch.object(ds.sys, "executable", "C:/Python313/python.exe"):
            assert ds.resolve_python_exe() == "C:/Python313/python.exe"

    def test_pythonw_swapped_for_python(self):
        with patch.object(ds.sys, "executable", "C:/Python313/pythonw.exe"):
            assert ds.resolve_python_exe() == "C:/Python313/python.exe"

    def test_case_insensitive_swap(self):
        with patch.object(ds.sys, "executable", "C:/Python313/PythonW.EXE"):
            assert ds.resolve_python_exe() == "C:/Python313/PythonW.EXE/../python.exe".replace("/", "/") or \
                   ds.resolve_python_exe().lower().endswith("python.exe")

    def test_non_windows_path_unchanged(self):
        with patch.object(ds.sys, "executable", "/usr/local/bin/python3.13"):
            assert ds.resolve_python_exe() == "/usr/local/bin/python3.13"


class TestSpawnEmptyosDaemon:
    """Spawning is mocked — we don't fork real daemons here."""

    def test_extra_env_merged_into_popen_call(self, tmp_path):
        cfg = tmp_path / "emptyos.toml"
        cfg.write_text("# stub\n")
        with patch.object(ds.subprocess, "Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            ds.spawn_emptyos_daemon(
                config_path=cfg,
                cwd=tmp_path,
                extra_env={"EOS_TEST_GUARD": "1", "EOS_SANDBOX_POOL_PORT": "9002"},
            )
        assert mock_popen.called
        _, kwargs = mock_popen.call_args
        env = kwargs["env"]
        assert env["EOS_CONFIG"] == str(cfg)
        assert env["EOS_TEST_GUARD"] == "1"
        assert env["EOS_SANDBOX_POOL_PORT"] == "9002"

    def test_python_args_default_to_emptyos_start(self, tmp_path):
        with patch.object(ds.subprocess, "Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            ds.spawn_emptyos_daemon(config_path=tmp_path / "x.toml", cwd=tmp_path)
        args = mock_popen.call_args[0][0]
        # First arg is the python exe; rest is the args tuple.
        assert args[1:] == ["-m", "emptyos", "start"]

    def test_creation_flags_use_create_no_window_on_windows(self, tmp_path):
        with patch.object(ds.subprocess, "Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            ds.spawn_emptyos_daemon(config_path=tmp_path / "x.toml", cwd=tmp_path)
        kwargs = mock_popen.call_args[1]
        expected = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert kwargs["creationflags"] == expected

    def test_stdio_defaults_to_devnull(self, tmp_path):
        with patch.object(ds.subprocess, "Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            ds.spawn_emptyos_daemon(config_path=tmp_path / "x.toml", cwd=tmp_path)
        kwargs = mock_popen.call_args[1]
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL

    def test_custom_stdio_passes_through(self, tmp_path):
        out_path = tmp_path / "out.log"
        err_path = tmp_path / "err.log"
        out_handle = out_path.open("wb")
        err_handle = err_path.open("wb")
        try:
            with patch.object(ds.subprocess, "Popen") as mock_popen:
                mock_popen.return_value = MagicMock()
                ds.spawn_emptyos_daemon(
                    config_path=tmp_path / "x.toml",
                    cwd=tmp_path,
                    stdout=out_handle,
                    stderr=err_handle,
                )
            kwargs = mock_popen.call_args[1]
            assert kwargs["stdout"] is out_handle
            assert kwargs["stderr"] is err_handle
        finally:
            out_handle.close()
            err_handle.close()


class TestTerminateDaemon:
    """Process-level termination is mocked — proc is a MagicMock."""

    def _mock_proc(self, *, exits_normally=True):
        m = MagicMock(spec=subprocess.Popen)
        if exits_normally:
            m.wait.return_value = 0
        else:
            # First wait raises TimeoutExpired, second (after kill) succeeds.
            m.wait.side_effect = [
                subprocess.TimeoutExpired(cmd="x", timeout=15),
                0,
            ]
        return m

    def test_clean_terminate_returns_ok(self):
        async def run():
            proc = self._mock_proc(exits_normally=True)
            res = await ds.terminate_daemon(proc, probe=None)
            assert res == {"ok": True}
            proc.terminate.assert_called_once()
            proc.kill.assert_not_called()
        asyncio.run(run())

    def test_terminate_timeout_escalates_to_kill(self):
        async def run():
            proc = self._mock_proc(exits_normally=False)
            res = await ds.terminate_daemon(proc, probe=None, terminate_timeout_s=1, kill_timeout_s=1)
            assert res == {"ok": True}
            proc.terminate.assert_called_once()
            proc.kill.assert_called_once()
        asyncio.run(run())

    def test_terminate_exception_returns_structured_error(self):
        async def run():
            proc = MagicMock(spec=subprocess.Popen)
            proc.terminate.side_effect = RuntimeError("OS handle gone")
            res = await ds.terminate_daemon(proc, probe=None)
            assert res["ok"] is False
            assert res["reason"] == "terminate_failed"
            assert "OS handle gone" in res["error"]
        asyncio.run(run())

    def test_probe_polls_until_false(self):
        async def run():
            proc = self._mock_proc(exits_normally=True)
            poll_count = {"n": 0}

            async def probe():
                poll_count["n"] += 1
                return poll_count["n"] < 3  # True, True, False

            res = await ds.terminate_daemon(
                proc, probe=probe, port_free_polls=10, port_free_interval_s=0.001,
            )
            assert res["ok"] is True
            assert poll_count["n"] == 3  # Stopped after first False

        asyncio.run(run())

    def test_probe_poll_budget_exhausts_cleanly(self):
        """If the port never frees, terminate_daemon still returns ok=True
        because the Popen DID exit — port-occupancy is informational."""
        async def run():
            proc = self._mock_proc(exits_normally=True)
            poll_count = {"n": 0}

            async def stubborn_probe():
                poll_count["n"] += 1
                return True   # Never frees

            res = await ds.terminate_daemon(
                proc, probe=stubborn_probe, port_free_polls=4, port_free_interval_s=0.001,
            )
            assert res["ok"] is True
            assert poll_count["n"] == 4

        asyncio.run(run())

    def test_probe_exception_treated_as_unreachable(self):
        async def run():
            proc = self._mock_proc(exits_normally=True)

            async def crashing_probe():
                raise ConnectionError("port refused")

            res = await ds.terminate_daemon(
                proc, probe=crashing_probe, port_free_polls=10, port_free_interval_s=0.001,
            )
            assert res["ok"] is True   # Probe exception = unreachable = success

        asyncio.run(run())
