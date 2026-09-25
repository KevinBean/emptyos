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

``run_json_script`` is the second layer, for the "write a spec.json, run a
runner.py against it, parse its JSON stdout into a structured envelope" shape
— extracted at the second consumer (``plugins/manim`` mirroring
``plugins/cadquery``'s ``_run_subprocess``, both writing byte-identical
launch-failed / timed-out / empty-stdout / bad-json envelopes with only the
domain word and the in-flight verb differing). Same statelessness rule
applies: it never touches the caller's ``_launch_ok``/``_launch_err`` — it
returns the raw ``RunResult`` alongside the parsed payload so the caller
updates its own state exactly as it did before extraction.

Consumers: ``plugins/cadquery`` (cadquery-3.12), ``plugins/markitdown``
(markitdown-3.13), ``plugins/manim`` (manim-3.12, ``run_json_script`` only).
"""

from __future__ import annotations

import asyncio
import json
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


async def run_json_script(
    python_exe: str,
    runner_path: str,
    spec_path,
    spec: dict,
    *,
    timeout: float = 120.0,
    domain_label: str = "interpreter",
    action_label: str = "run",
) -> tuple[dict, RunResult]:
    """Write ``spec`` to ``spec_path`` and run
    ``python_exe runner_path spec_path``, parsing the runner's JSON stdout
    into a structured envelope.

    The caller owns the temp-dir lifecycle (it already needs one for the
    script/source file the spec references) — this only owns the
    write-spec + run + parse-or-envelope-error plumbing that was duplicated
    byte-for-byte between ``plugins/cadquery``'s ``_run_subprocess`` and
    ``plugins/manim``'s ``render_scene``.

    Returns ``(payload, result)``:
      - ``payload`` is always a dict — either the runner's own JSON stdout
        verbatim, or a ``{"ok": False, "stage": "plugin", "error": "..."}``
        envelope for any subprocess-level failure (didn't launch, timed out,
        empty stdout, invalid JSON). ``domain_label`` / ``action_label``
        customise the two wordings that differ per plugin (e.g. "CadQuery
        interpreter" / "compile" vs "Manim interpreter" / "render") — every
        other word in the four error shapes is intentionally identical
        across every consumer, so a caller reading `.claude/rules/
        environment.md` for one plugin recognises the shape in another.
      - ``result`` is the raw ``RunResult``. This helper never touches the
        caller's own ``_launch_ok``/``_launch_err`` (same statelessness rule
        as the rest of this module) — the caller reads
        ``result.launch_failed`` and updates its own state exactly as before
        extraction.
    """
    from pathlib import Path

    spec_path = Path(spec_path)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    result = await run_venv(
        python_exe, [runner_path, str(spec_path)], timeout=timeout,
    )

    if result.launch_failed:
        return (
            {
                "ok": False, "stage": "plugin",
                "error": f"failed to launch {domain_label}: {result.launch_exc}",
            },
            result,
        )
    if result.timed_out:
        return (
            {
                "ok": False, "stage": "plugin",
                "error": f"{action_label} timeout after {timeout}s",
            },
            result,
        )
    if not result.stdout:
        return (
            {
                "ok": False, "stage": "plugin",
                "error": (
                    f"runner produced no stdout (exit={result.returncode}); "
                    f"stderr={result.stderr[:500]}"
                ),
            },
            result,
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return (
            {
                "ok": False, "stage": "plugin",
                "error": (
                    f"runner stdout is not JSON: {exc}; raw={result.stdout[:500]}; "
                    f"stderr={result.stderr[:500]}"
                ),
            },
            result,
        )
    return payload, result
