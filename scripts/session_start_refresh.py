#!/usr/bin/env python3
"""SessionStart(compact) hook — re-inject session state after compaction.

When Claude Code compacts a long conversation, architectural context (git
state, daemon reachability, the pending session brief, the daemon-handling
reminder) can fall out of the window. This hook fires on the `compact` matcher
and feeds a compact status block back into context via additionalContext.

Stdlib only, every probe time-boxed, always exits 0. Resolves the project root
from CLAUDE_PROJECT_DIR (portable across clones).
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from hook_common import project_root, session_tag, vault_path


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=8
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _daemon_up(port: int) -> str:
    """Return 'up' if the port answers HTTP (even a gated 401/403), else 'down'."""
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        urllib.request.urlopen(url, timeout=2)
        return "up"
    except urllib.error.HTTPError:
        return "up"  # responding, just auth-gated — still alive
    except Exception:
        return "down"


def _next_brief(root: Path) -> str:
    vault = vault_path(root)
    if not vault:
        return "(vault not resolvable)"
    base = vault / "10_Projects" / "emptyos" / "log"
    idx = base / "_next" / "_index.md"
    if idx.exists():
        return str(idx).replace("\\", "/")
    legacy = base / "_next.md"
    if legacy.exists():
        return str(legacy).replace("\\", "/")
    return "(no _next brief found)"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    root = project_root(payload)

    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "(unknown)"
    dirty_lines = [l for l in _git(root, "status", "--short").splitlines() if l.strip()]
    if not dirty_lines:
        dirty_summary = "clean"
    else:
        shown = "\n".join(f"    {l}" for l in dirty_lines[:15])
        more = "\n    …" if len(dirty_lines) > 15 else ""
        dirty_summary = f"{len(dirty_lines)} changed:\n{shown}{more}"

    # Omitted, never a placeholder: the resume skill copies this line into a
    # plan-row claim, and "(unknown)" there would read as a stamp.
    tag = session_tag(payload)
    session_line = f"session: {tag}\n" if tag else ""

    ctx = f"""[Post-compaction state refresh — EmptyOS]

{session_line}Git branch: {branch}
Working tree: {dirty_summary}
Daemons: :9000 {_daemon_up(9000)} · :9001 {_daemon_up(9001)}
Next session brief: {_next_brief(root)}

Reminder — the :9000 and :9001 daemons are USER-OWNED. Never run restart.bat /
stop.bat / `python -m emptyos start` / taskkill against them, and never delete
data/*.db*. To verify Python changes, lease a sandbox-pool member via
POST /sandbox/api/lease (:9002+). See .claude/rules/daemon-handling.md."""

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
