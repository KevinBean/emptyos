#!/usr/bin/env python3
"""PostToolUse hook — warn when a newly written file is gitignored.

The failure this catches is silent and machine-local: the file exists, every
import works HERE, and it is simply absent from a fresh clone or a release
snapshot. Two shapes have bitten this repo:

  - `scripts/_*.py` (scratch-script pattern) would have made a shared helper
    named `scripts/_common.py` untracked while three scanners imported it —
    perfect locally, `ImportError` in CI and in any clone.
  - `data/` matches ANY directory named `data` at ANY depth, so
    `engines/<x>/data/` reference datasets vanished from releases. That one bit
    three engines across separate sessions.

`.gitignore` patterns match FILES, not just directories — which is why the
`dev-gotchas.md` rule ("check-ignore new paths") generalises to both.

Advisory ONLY: writes one line to stderr and always exits 0. It never blocks a
write — some ignored writes are entirely correct (`data/`, `dist/`, scratch,
`apps/personal/`, `emptyos.toml`). The point is that the agent SEES it at the
moment of writing, instead of discovering it from a broken clone weeks later.

Mirrors `check_daemon_restart_needed.py`: reads the hook payload from stdin,
resolves the project root from CLAUDE_PROJECT_DIR, exits 0 unconditionally.
"""

from __future__ import annotations

import json
import subprocess
import sys

from hook_common import project_root, relpath, root_marker, tool_input

# Paths that are *supposed* to be ignored — writing here is normal and warning
# every time would train the reader to ignore the hook (see audits.md: a check
# that cries wolf gets disabled within a month).
EXPECTED_IGNORED = (
    "data/",
    "dist/",
    "apps/personal/",
    "engines/personal/",
    "sandbox-",
    "dogfood/",
    ".claude/scratch/",
    "emptyos.toml",
    "node_modules/",
    ".agent-bus/",
)


def is_expected(rel: str) -> bool:
    """True only for paths under a KNOWN-ignored root.

    Deliberately a prefix match, never a substring one: `data/` here means the
    project-root `data/` dir, NOT any nested `data/` at any depth. A nested
    `apps/<x>/data/` or `engines/<x>/data/` IS the trap this hook exists to
    catch (it silently untracked three engines' reference datasets), so it must
    fall through to the real `git check-ignore`.
    """
    return any(rel.startswith(p) for p in EXPECTED_IGNORED)


def check_ignored(path: str, root) -> str | None:
    """Return the matching .gitignore rule if `path` is ignored, else None."""
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-v", "--", path],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    # exit 0 = ignored; 1 = not ignored; 128 = error (not a repo, etc.)
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip().splitlines()
    return line[0] if line else None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if (payload.get("tool_name") or "") != "Write":
        return 0

    path = str(tool_input(payload).get("file_path") or "")
    if not path:
        return 0

    root = project_root(payload)
    rel = relpath(path, root_marker(payload))
    if is_expected(rel):
        return 0

    rule = check_ignored(path, root)
    if not rule:
        return 0

    print(
        f"[gitignore] {rel} is IGNORED by git ({rule}).\n"
        f"  It exists here but will be ABSENT from a fresh clone and from release "
        f"snapshots. If anything imports it, that breaks in CI and for every other "
        f"user. Either rename it out of the ignore pattern, or add a negation to "
        f".gitignore. See .claude/rules/dev-gotchas.md.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
