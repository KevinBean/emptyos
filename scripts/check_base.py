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
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directory names whose subtrees the AST scanners skip: bytecode cache and the
# two gitignored-style archives (flagging dead/retired code is pure noise).
SKIP_DIR_PARTS = {"__pycache__", "_archive", "_retired"}


def iter_py_files(roots: list[str]) -> Iterator[Path]:
    """Yield every `.py` file under each root, skipping cache/archive/retired.

    A root may be a directory (walked recursively) or a single `.py` file.
    Non-existent roots are silently skipped. Shared by the AST-based scanners
    (check-asyncio-blocking, check-swallowed-exceptions, …) so they agree on
    exactly which files count.
    """
    for r in roots:
        p = (REPO_ROOT / r).resolve()
        if not p.exists():
            continue
        if p.is_file() and p.suffix == ".py":
            yield p
            continue
        for f in p.rglob("*.py"):
            if SKIP_DIR_PARTS & set(f.parts):
                continue
            yield f


def filter_noqa(findings: list[dict], token: str) -> list[dict]:
    """Drop findings suppressed by an inline `# noqa: <token>` comment.

    On the finding's source line, a bare `# noqa: <token>` suppresses every
    rule on that line; `# noqa: <token>:<rule-id>` suppresses only the named
    rule. Each finding dict must carry `file`, `line` (1-based), and `rule`.
    A line that can't be read is never suppressed (the finding survives).
    """
    marker = f"# noqa: {token}"
    cache: dict[str, list[str]] = {}
    keep: list[dict] = []
    for h in findings:
        path = REPO_ROOT / h["file"]
        try:
            lines = cache.setdefault(
                h["file"], path.read_text(encoding="utf-8").splitlines()
            )
        except OSError:
            keep.append(h)
            continue
        idx = h["line"] - 1
        if 0 <= idx < len(lines) and marker in lines[idx]:
            rest = lines[idx].split(marker, 1)[1]
            if rest.startswith(":"):
                if f":{h['rule']}" in rest:
                    continue  # per-rule suppression matched
            else:
                continue  # bare token suppresses all rules on this line
        keep.append(h)
    return keep


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
