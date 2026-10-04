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
import sys
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


def _git_paths(*args: str) -> list[Path]:
    """Run a git command that lists paths; return them as absolute Paths.

    `-z` is load-bearing twice over. Without it git C-quotes any path holding a
    non-ASCII byte (`"products/writedesk/\\345…txt"`), and reading stdout with
    `text=True` decodes through the Windows locale (cp1252). Either alone turns
    the path into one that does not exist, and every scanner here skips a file it
    cannot open — silently. Measured 2026-09-25: 26 tracked files were invisible
    to check-personal, check-branding and the UI scanners this way. NUL-separated
    raw bytes, decoded as UTF-8, is the one form git never rewrites.
    """
    out = subprocess.run(
        ["git", *args, "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [REPO_ROOT / p for p in out.stdout.decode("utf-8").split("\0") if p]


def git_tracked() -> list[Path]:
    """Absolute paths of every file tracked by git."""
    return _git_paths("ls-files")


def git_staged() -> list[Path]:
    """Absolute paths of files staged for commit (filter ACM = added/copied/modified)."""
    return _git_paths("diff", "--cached", "--name-only", "--diff-filter=ACM")


def files_under(root: Path) -> list[str]:
    """Root-relative POSIX paths of every file under ``root`` (``.git`` excluded).

    The scope for a scan of a tree that is not this repo's working tree — the
    release snapshot. ``git_tracked`` there would list the WORKING TREE's files
    (git runs in ``REPO_ROOT``), so a file the release created in the snapshot was
    never listed, and every tracked file the snapshot dropped was "scanned" by
    failing to open. Walking the snapshot is the only list that
    matches what ships.
    """
    root = Path(root)
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    )


def arg_value(flag: str) -> str | None:
    """Value of ``flag`` in argv (``--x v`` or ``--x=v``), or None. A missing
    value is a usage error (exit 2): an unread ``--patterns=`` or ``--root=``
    falls back to the default scope and reports clean on the wrong input."""
    for arg in sys.argv:
        if arg.startswith(flag + "="):
            value = arg.split("=", 1)[1]
            if not value:
                print(f"ERROR: {flag} needs a value")
                sys.exit(2)
            return value
    if flag not in sys.argv:
        return None
    i = sys.argv.index(flag)
    if i + 1 >= len(sys.argv):
        print(f"ERROR: {flag} needs a value")
        sys.exit(2)
    return sys.argv[i + 1]


def git_untracked() -> list[Path]:
    """Absolute paths of untracked, non-ignored files.

    Neither ``git_tracked`` nor ``git_staged`` can see a file that has not been
    ``git add``-ed yet, so a scanner run over either one reports a clean tree
    while a violation sits in a brand-new file. That is not hypothetical: on
    2026-07-28 ``check-personal.py`` printed "No personal data found in all
    tracked files (3757 files)" while a hardcoded personal vault path sat in an
    untracked test — and the green was read as clearance by two reviewers.

    ``--exclude-standard`` honours .gitignore, so scratch dirs stay out.
    """
    return _git_paths("ls-files", "--others", "--exclude-standard")


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
