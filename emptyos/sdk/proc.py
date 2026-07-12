"""Timeout-guarded async subprocess runner.

The shared primitive behind ad-hoc ``asyncio.create_subprocess_exec`` +
``wait_for`` spawns. Returns a small result carrying ``returncode`` + decoded
``stdout``/``stderr`` and **never raises** on timeout or a missing binary — it
folds those into the result so callers branch on ``.ok`` / ``.returncode``
instead of wrapping every call in try/except. On timeout it also ``kill()``s
the child (a hung process is not leaked — the bug every hand-rolled copy had).

First consumers: the ``tailscale`` + ``tailscale-serve`` plugins. Other inline
spawns (``emptyos/sdk/media/*``, ``userhome_venv``) migrate on touch (Rule 9) —
this is the canonical shape they graduate toward, not a tailscale-only helper.

Pure / kernel-free, so it unit-tests without a daemon.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


async def run_command(argv: list[str], *, timeout: float, cwd: str | None = None) -> ProcResult:
    """Run ``argv``, capturing stdout/stderr, killed after ``timeout`` seconds.

    Never raises:
    - timeout → ``ProcResult(returncode=-1, timed_out=True, stderr="timed out")``
      (the child is killed first).
    - missing binary / OS error → ``ProcResult(returncode=-1, stderr=str(e))``.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except (FileNotFoundError, OSError) as e:
        return ProcResult(-1, "", str(e))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        # Kill AND reap the child so its transport closes (no ResourceWarning).
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return ProcResult(-1, "", "timed out", timed_out=True)
    return ProcResult(
        proc.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )
