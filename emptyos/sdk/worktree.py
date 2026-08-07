"""Git worktree helpers — pure subprocess wrappers shared by harness apps.

Two apps drive claude-cli edits inside an isolated git worktree:
``apps/app-builder/`` (scaffolds new apps) and ``apps/fix-agent/`` (applies
queued fix-prompts). Both reinvented the same three subprocess wrappers —
``git`` invocation, worktree creation, ``py_compile`` pre-merge gate — so
they collapse here.

Pure functions, no ``self``: each caller passes its own ``cwd`` and the
helpers never reach into kernel state. Anything app-specific (branch
prefix, runs directory layout, lifecycle endpoints) stays in the calling
app — the per-fix-driver knows what it's driving; this module only knows
how to talk to git.

When NOT to use:
    - You need long-running, streaming, or interactive git (e.g. ``git
      log -p`` piped through a pager). These wrappers buffer + time out
      after 60s.
    - You want a structured GitPython-style API. These helpers return raw
      stdout/stderr strings; the caller parses.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def git_run(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = 60,
) -> tuple[int, str, str]:
    """Run a git command. Returns ``(returncode, stdout, stderr)``.

    Never raises — subprocess failures collapse to ``(-1, "", str(exc))``
    so callers can branch on returncode uniformly.
    """
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def ensure_worktree(
    repo: Path,
    wt: Path,
    *,
    ref: str = "HEAD",
) -> tuple[Path, str | None]:
    """Idempotently create a detached git worktree at ``wt`` from ``repo``.

    Returns ``(wt, error_or_None)``. If the worktree already exists (has
    a ``.git`` file inside), returns ``(wt, None)`` without touching it.
    Otherwise creates parents and runs ``git worktree add --detach``.

    Detach is deliberate: the placeholder branch must not collide with a
    branch already checked out elsewhere.
    """
    if wt.exists() and (wt / ".git").exists():
        return wt, None
    wt.parent.mkdir(parents=True, exist_ok=True)
    rc, _, err = git_run(
        ["worktree", "add", "--detach", str(wt), ref],
        cwd=repo,
    )
    if rc != 0:
        return wt, f"git worktree add failed: {err.strip()}"
    return wt, None


def py_compile_files(
    files: list[str],
    cwd: Path,
    *,
    timeout: int = 60,
) -> tuple[bool, str]:
    """Run ``python -m py_compile`` against each file. Returns ``(ok, msg)``.

    Cheap pre-merge gate: catches SyntaxError before a merge lands a diff
    the daemon can't reload. ``msg`` is the combined stripped stdout +
    stderr so callers can surface compilation errors verbatim.
    """
    try:
        r = subprocess.run(
            # sys.executable, never bare "python": PATH may resolve to a
            # different interpreter than the daemon's (this machine carries
            # 3.13 and 3.11), and this gates every fix-agent merge -- a syntax
            # check under the wrong Python is worse than none, because it
            # reports green for source the daemon cannot import.
            [sys.executable, "-m", "py_compile", *files],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, f"py_compile invocation error: {e}"
