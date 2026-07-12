"""Subprocess supervision for embedded EmptyOS daemons.

Both `plugins/dogfood-demo/` (single sidecar on :9001) and
`plugins/sandbox-pool/` (N pool members on :9002+) spawn child `python -m
emptyos start` processes and need the same low-level mechanics:

- Resolve `python.exe` (swapping out `pythonw.exe`, which Windows firewall
  rules treat differently — inbound rules tied to the GUI launcher can
  silently drop traffic that `python.exe` accepts).
- Spawn with `CREATE_NO_WINDOW`, isolated env (`EOS_CONFIG` + a guard env var),
  and configurable stdout/stderr (DEVNULL or per-instance log files).
- Terminate the owned `Popen` handle gracefully — `terminate()` first, then
  `kill()` after a timeout — then poll a caller-supplied probe until the
  port is observed free, so a quick restart doesn't race the OS releasing
  the socket.

This module is **functions, not a base class**: the two consumers have very
different higher-level shapes (single sidecar vs. multi-member lease state
machine), so a class hierarchy would be the wrong abstraction. Each
consumer keeps its own dataclass / dict shape and calls these helpers at
the I/O boundary.

These functions never `taskkill` an unowned PID — the caller passes a
`Popen` handle this module spawned, so the daemon-handling rule (kill only
what you own) holds by construction.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


def resolve_python_exe() -> str:
    """Return a `python.exe` path suitable for spawning a child daemon.

    The running interpreter may be `pythonw.exe` (Windows GUI launcher),
    which inbound firewall rules treat differently from `python.exe` —
    a per-rule binding to pythonw.exe will silently drop traffic that
    python.exe accepts, breaking LAN access to spawned daemons. Swap to
    the sibling `python.exe` when that's what we're running."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        return exe[: -len("pythonw.exe")] + "python.exe"
    return exe


def spawn_emptyos_daemon(
    *,
    config_path: Path,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
    stdout: Any = subprocess.DEVNULL,
    stderr: Any = subprocess.DEVNULL,
    args: tuple[str, ...] = ("-m", "emptyos", "start"),
) -> subprocess.Popen:
    """Spawn a `python -m emptyos start` subprocess for an embedded daemon.

    Args:
        config_path: Path to the daemon's `emptyos.toml`. Set as `EOS_CONFIG`
            in the child's environment.
        cwd: Working directory for the child. Usually the project root.
        extra_env: Additional environment variables (e.g. recursion guards
            like `EOS_DEMO_INSTANCE=1` or `EOS_SANDBOX_POOL_MEMBER=1`).
        stdout / stderr: File handle, `subprocess.DEVNULL`, or `subprocess.PIPE`.
            Pass open file handles (mode `wb`) to capture logs.
        args: Args passed to the python interpreter. Override only if you
            need a non-standard entry point (e.g. a script test harness).

    Returns:
        A `Popen` handle the caller owns and is responsible for terminating
        (via `terminate_daemon` below). Never call `terminate()` / `kill()`
        directly — `terminate_daemon` adds the port-free wait that prevents
        restart races.
    """
    env = os.environ.copy()
    env["EOS_CONFIG"] = str(config_path)
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(  # noqa: ASYNC220
        [resolve_python_exe(), *args],
        cwd=str(cwd),
        env=env,
        creationflags=creation_flags,
        stdout=stdout,
        stderr=stderr,
    )


async def terminate_daemon(
    proc: subprocess.Popen,
    *,
    probe: Callable[[], Awaitable[bool]] | None = None,
    terminate_timeout_s: int = 15,
    kill_timeout_s: int = 5,
    port_free_polls: int = 10,
    port_free_interval_s: float = 0.5,
) -> dict:
    """Terminate a daemon `Popen` handle gracefully.

    Sends `terminate()` first; if the process doesn't exit within
    `terminate_timeout_s`, escalates to `kill()`. After the process is
    reaped, polls `probe()` until it returns False (port observed free)
    so a quick respawn doesn't race the OS releasing the socket.

    Args:
        proc: The `Popen` handle from `spawn_emptyos_daemon`. Caller MUST
            own this handle — don't pass a `Popen` derived from a PID lookup
            of an unrelated process.
        probe: Optional async callable returning True when the daemon is
            still reachable. When provided, polls until False or until the
            poll budget is exhausted. Omit if the caller doesn't care about
            port-free verification.
        terminate_timeout_s: Seconds to wait after `terminate()` before
            escalating to `kill()`.
        kill_timeout_s: Seconds to wait for `kill()` to take effect before
            giving up.
        port_free_polls: Number of probe polls.
        port_free_interval_s: Sleep between probe polls.

    Returns:
        `{ok: True}` when the process exits cleanly.
        `{ok: False, reason: "terminate_failed", error: ...}` when the
        terminate/kill sequence itself raised.

    Note: an unreachable probe at the end is treated as success — the
    port observably released. If the probe still returns True after all
    polls (unlikely; only happens if something else picked up the port
    instantly), the function still returns `{ok: True}` because the
    `Popen` did exit — caller can re-probe later.
    """
    try:
        proc.terminate()
        try:
            proc.wait(timeout=terminate_timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=kill_timeout_s)
    except Exception as e:
        return {"ok": False, "reason": "terminate_failed", "error": str(e)[:200]}
    if probe is not None:
        for _ in range(max(1, port_free_polls)):
            await asyncio.sleep(port_free_interval_s)
            try:
                still_alive = await probe()
            except Exception:
                still_alive = False
            if not still_alive:
                break
    return {"ok": True}
