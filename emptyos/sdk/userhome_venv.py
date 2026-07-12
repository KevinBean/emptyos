"""User-home venv subprocess helpers — shared plumbing for plugins that run
heavy or version-pinned deps in an isolated venv instead of the daemon
interpreter.

Why this exists: the daemon runs on one Python with one set of deps, and a few
tools can't co-exist with it — CadQuery + VTK need 3.12 wheels; MarkItDown's
`magika` pins `onnxruntime<=1.20.1` on Windows, colliding with the daemon's
`onnxruntime-gpu`. Each installs into ``%LOCALAPPDATA%/eos/envs/<tool>-<pyver>/``
and is reached via subprocess. See ``.claude/rules/environment.md`` §
"User-home Python envs for heavy deps".

**Stateless by design** — module-level functions, NOT a base class or a stateful
object. Each plugin keeps its own ``_python_exe`` / ``_launch_ok`` /
``_launch_err`` attributes and its own *domain* error strings (so cadquery can
say "failed to launch CadQuery interpreter" while markitdown says "...markitdown
interpreter"); the helpers own only the identical ``create_subprocess_exec`` +
``wait_for`` + decode plumbing that was otherwise copy-pasted per plugin.

Consumers: ``plugins/cadquery`` (cadquery-3.12), ``plugins/markitdown``
(markitdown-3.13).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def default_venv_python(tool: str, pyver: str) -> str:
    """Canonical user-home interpreter path for ``<tool>-<pyver>``.

    Windows: ``%LOCALAPPDATA%/eos/envs/<tool>-<pyver>/Scripts/python.exe``
    POSIX:   ``~/.local/eos/envs/<tool>-<pyver>/bin/python``
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "eos" / "envs" / f"{tool}-{pyver}" / "Scripts" / "python.exe")
    return str(Path.home() / ".local" / "eos" / "envs" / f"{tool}-{pyver}" / "bin" / "python")


async def probe_launch(
    python_exe: str, probe_args: list[str], *, timeout: float = 10.0
) -> tuple[bool, str]:
    """One-shot ``python_exe <probe_args...>`` to confirm the interpreter starts
    (and optionally that a key dep imports, e.g. ``["-c", "import markitdown"]``).

    Returns ``(ok, err)``. Never raises — a probe failure is reported, not
    propagated, so plugin boot never blocks. The caller stores the tuple in its
    own ``_launch_ok`` / ``_launch_err``. A file that *exists* but won't launch
    (corrupt venv, wrong arch, broken symlink) yields ``(False, <reason>)``
    rather than passing a bare ``Path.exists()`` check.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            python_exe,
            *probe_args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"launch probe timed out after {timeout}s"
        if proc.returncode == 0:
            return True, ""
        return False, (
            err_b.decode("utf-8", errors="replace").strip() or f"exit code {proc.returncode}"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


@dataclass
class RunResult:
    """Outcome of ``run_venv``.

    ``ok`` is a clean exit (returncode 0, no launch failure / timeout).
    ``launch_failed`` is an ``OSError`` on spawn (interpreter exists as a file
    but won't start); ``timed_out`` is a wall-clock kill. The caller maps each
    to its own domain-specific user message via ``launch_exc``.
    """

    ok: bool
    returncode: int
    stdout: str
    stderr: str
    launch_failed: bool = False
    launch_exc: str = ""
    timed_out: bool = False


async def run_venv(
    python_exe: str, args: list, *, timeout: float = 120.0, cwd: str | None = None
) -> RunResult:
    """Run ``python_exe <args...>`` with a wall-clock timeout, both streams piped
    and utf-8-decoded (``errors='replace'`` — Windows venvs can emit cp1252).

    Never raises for process-level outcomes: an ``OSError`` on spawn becomes
    ``RunResult(launch_failed=True)``, a timeout becomes
    ``RunResult(timed_out=True)``. The caller owns the user-facing wording for
    each (e.g. cadquery's "compile timeout after 60s").
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            python_exe,
            *[str(a) for a in args],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except OSError as exc:
        return RunResult(
            False, -1, "", "", launch_failed=True, launch_exc=f"{type(exc).__name__}: {exc}"
        )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return RunResult(False, -1, "", "", timed_out=True)
    return RunResult(
        proc.returncode == 0,
        proc.returncode,
        out_b.decode("utf-8", errors="replace").strip(),
        err_b.decode("utf-8", errors="replace").strip(),
    )
