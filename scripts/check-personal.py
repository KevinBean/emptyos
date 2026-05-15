#!/usr/bin/env python3
"""Scan committed files for personal data patterns.

Reads .eos-personal for regex patterns, scans all git-tracked files,
and reports matches. Exit code 0 = clean, 1 = personal data found.

Usage:
    python scripts/check-personal.py              # scan all tracked files
    python scripts/check-personal.py --staged     # scan only staged files (for pre-commit)
    python scripts/check-personal.py --install-hook   # write .git/hooks/pre-commit
"""

import sys
from pathlib import Path

# Make `emptyos.sdk` importable when the script is run from the repo root,
# even before `pip install -e .`. Pre-commit hooks and the release snapshot
# scanner both invoke this script from a checkout that has `emptyos/` on
# disk but may not have it installed in the active venv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emptyos.sdk.personal_patterns import load as _load_personal_patterns
from _check_base import (
    REPO_ROOT,
    git_staged,
    git_tracked,
    install_pre_commit_hook,
)

PATTERNS_FILE = ".eos-personal"
# Files that are allowed to contain personal patterns
ALLOWLIST = {
    ".eos-personal",  # the patterns file itself
    "scripts/check-personal.py",  # this script
    "data/personal-defaults.json",
    ".claude/settings.local.json",  # machine-specific auto-approve rules
}
# Binary extensions to skip
BINARY_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".wav",
    ".mp3",
    ".mp4",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".db",
    ".sqlite",
    ".pyc",
}


def load_patterns(path: str):
    if not Path(path).exists():
        print(f"Warning: {path} not found, no patterns to check")
        return []
    return _load_personal_patterns(
        path,
        on_error=lambda line, err: print(f"Warning: invalid pattern '{line}': {err}"),
    )


def get_files(staged_only: bool = False) -> list[str]:
    """Repo-relative path strings (existing call sites read them as such)."""
    paths = git_staged() if staged_only else git_tracked()
    return [p.relative_to(REPO_ROOT).as_posix() for p in paths]


def main():
    if "--install-hook" in sys.argv:
        sys.exit(
            install_pre_commit_hook(
                script="check-personal.py",
                backup_suffix=".pre-eos.bak",
                idempotent_marker="check-personal.py",
            )
        )
    staged_only = "--staged" in sys.argv
    patterns = load_patterns(PATTERNS_FILE)
    if not patterns:
        sys.exit(0)

    files = get_files(staged_only)
    violations = []

    for filepath in files:
        if filepath in ALLOWLIST:
            continue
        if Path(filepath).suffix.lower() in BINARY_EXT:
            continue
        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            for pattern in patterns:
                if pattern.search(line):
                    violations.append((filepath, lineno, pattern.pattern, line.strip()[:120]))

    if violations:
        print(f"\n{'=' * 60}")
        print(f"  PERSONAL DATA DETECTED in {len(violations)} location(s)")
        print(f"{'=' * 60}\n")
        for filepath, lineno, pattern, preview in violations:
            print(f"  {filepath}:{lineno}")
            print(f"    Pattern: {pattern}")
            print(f"    Content: {preview}")
            print()
        print(f"Fix these before committing. Patterns defined in {PATTERNS_FILE}")
        print("Move personal values to data/personal-defaults.json (git-ignored)\n")
        sys.exit(1)
    else:
        mode = "staged files" if staged_only else "all tracked files"
        print(
            f"OK: No personal data found in {mode} ({len(files)} files, {len(patterns)} patterns)"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
