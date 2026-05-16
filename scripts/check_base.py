"""Shared plumbing for scripts/check-*.py scanners.

Three things every scanner does identically:

  1. List git-tracked files (full repo).
  2. List files staged for the next commit (pre-commit hook scope).
  3. Install a .git/hooks/pre-commit that re-runs the scanner with --staged.

Each scanner still owns its own pattern logic, filtering, and report format —
this module exists only to keep the four scanners' boilerplate honest.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def git_tracked() -> list[Path]:
    """Absolute paths of every file tracked by git."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line]


def git_staged() -> list[Path]:
    """Absolute paths of files staged for commit (filter ACM = added/copied/modified)."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line]


def install_pre_commit_hook(
    *,
    script: str,
    backup_suffix: str,
    idempotent_marker: str | None = None,
) -> int:
    """Install `.git/hooks/pre-commit` to run `python scripts/<script> --staged`.

    `script`            — relative path under scripts/ (e.g. "check-app-nav.py").
    `backup_suffix`     — extension for backing up the existing hook
                          (e.g. ".pre-app-nav.bak"). Standard scanner backups use
                          ".pre-<scanner>.bak" so multiple scanners can layer
                          without overwriting each other's backups.
    `idempotent_marker` — when set, if an existing hook already mentions this
                          string, do nothing and return 0. Lets scanners stay
                          opt-in safe across re-invocations.

    Returns 0 on success, 1 if not inside a git checkout.
    """
    repo = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not repo:
        print("Not inside a git repository.")
        return 1

    hook = Path(repo) / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)

    if hook.exists() and idempotent_marker:
        existing = hook.read_text(encoding="utf-8", errors="ignore")
        if idempotent_marker in existing:
            print(f"OK: pre-commit hook already invokes {script} ({hook})")
            return 0

    if hook.exists():
        backup = hook.with_suffix(backup_suffix)
        hook.rename(backup)
        print(f"NOTE: existing hook moved to {backup}")

    body = f"#!/bin/sh\nexec python scripts/{script} --staged\n"
    hook.write_text(body, encoding="utf-8")
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    print(f"OK: installed pre-commit hook at {hook}")
    return 0
