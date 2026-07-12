#!/usr/bin/env python3
"""PreToolUse hook — block daemon-destructive commands from agent sessions.

Turns `.claude/rules/daemon-handling.md` from advice into enforcement. Inspects
Bash / PowerShell / Codex shell commands and DENIES the ones the rule forbids
against the user-owned daemons (:9000 main, :9001 dogfood):

  - running restart.bat / stop.bat
  - booting a daemon under the agent (`python -m emptyos start`, `eos start`)
  - taskkill / Stop-Process / pkill / kill targeting python or emptyos
  - deleting data/*.db / *.db-wal / *.db-shm (live SQLite WAL handles), or
    recursively deleting the data/ directory

Sandbox-pool members (:9002+) are managed via the /sandbox/api/* HTTP path
(curl), never via taskkill, so those calls pass through untouched.

Read-only diagnostics (tasklist, Get-Process, Get-NetTCPConnection, netstat)
are NOT blocked. On allow, the hook stays silent so the normal permission
flow proceeds. Always exits 0 — the deny is expressed via JSON, not a non-zero
exit, so a bug in this guard can never hard-block every command.
"""

from __future__ import annotations

import json
import re
import sys

from hook_common import command_code_view as _code_view


# (compiled regex, human reason). Matched case-insensitively against the
# command string.
_DAEMON_LIFECYCLE = [
    (
        re.compile(r"\b(restart|stop)\.bat\b", re.I),
        "runs restart.bat / stop.bat — the daemon is the user's to restart, not Claude's",
    ),
    (
        re.compile(r"python\s+-m\s+emptyos\s+start\b", re.I),
        "boots a daemon under the agent's process group (it dies when the tool ends)",
    ),
    (
        re.compile(r"\b(emptyos|eos)\s+start\b", re.I),
        "boots a daemon under the agent's process group (it dies when the tool ends)",
    ),
]

# Process-kill verbs are only dangerous when they could hit python / the daemon.
_KILL_VERB = re.compile(r"\b(taskkill|pkill|kill)\b|stop-process", re.I)
_KILL_TARGET = re.compile(r"python|emptyos", re.I)

# Deletion of a live SQLite file (.db / .db-wal / .db-shm).
_DELETE_VERB = re.compile(r"\b(rm|del|erase|unlink|remove-item|ri)\b", re.I)
_DB_FILE = re.compile(r"\.db(-wal|-shm)?\b", re.I)

# Recursive delete of the data/ directory root (not a sub-path under it).
_DATA_DIR_RECURSIVE = re.compile(
    r"rm\s+-[a-z]*r[a-z]*\s+[\"']?\.?[/\\]?data[/\\]?[\"']?(?:\s|$|;|&)"
    r"|remove-item\b[^\n]*[\"' ]\.?[/\\]?data[/\\]?\b[^\n]*-recurse",
    re.I,
)

# String DATA (quoted spans AND heredoc bodies) is blanked before matching
# program/verb names, so a trigger token that merely appears as *data* — inside a
# `git commit -m "…"` message, an `echo "…"`, a `grep` pattern, or a heredoc body —
# doesn't masquerade as a command being executed. A real destructive invocation has
# the verb OUTSIDE the data (`taskkill /IM python.exe`, `rm data/x.db`,
# `restart.bat`). File-path matches (the `.db` suffix) still run against the
# original command so a quoted path like `rm "data/syslog.db"` stays caught.
#
# The heredoc half is not hypothetical: this guard blocked a devlog written via
# `cat >> file <<'EOF'` because the prose contained the words "kill switch" and
# "emptyos" (2026-07-12). Shared with guard_git_safety.py, which had the same bug.
# `_code_view` is imported from hook_common at the top of this module.


def classify(cmd: str) -> str | None:
    """Return a deny reason if the command is daemon-destructive, else None.

    Verb/program-name patterns match the quote-stripped ``code`` view so a
    trigger token quoted as data (commit message, echo, grep pattern) can't
    trip the guard; file-path patterns still see the original command.
    """
    if not cmd:
        return None
    code = _code_view(cmd)
    for pat, reason in _DAEMON_LIFECYCLE:
        if pat.search(code):
            return reason
    if _KILL_VERB.search(code) and _KILL_TARGET.search(cmd):
        return (
            "ends a python / emptyos process — may hit the user-owned "
            ":9000/:9001 daemon and corrupt SQLite WAL handles. Diagnose with "
            "tasklist / Get-Process and let the user act"
        )
    if _DELETE_VERB.search(code) and _DB_FILE.search(cmd):
        return (
            "deletes a live SQLite db/-wal/-shm file — restart.bat cleans these "
            "up safely after the writers are gone; nothing else should"
        )
    if _DATA_DIR_RECURSIVE.search(cmd) and _DELETE_VERB.search(code):
        return "recursively deletes the data/ directory (live daemon state)"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = payload.get("tool_name") or ""
    if tool_name not in ("Bash", "PowerShell", "exec_command", "shell"):
        return 0

    tool_input = payload.get("tool_input") or {}
    cmd = str(tool_input.get("command") or tool_input.get("cmd") or "")
    reason = classify(cmd)
    if not reason:
        return 0  # stay silent → normal permission evaluation proceeds

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Blocked by daemon-safety guard: this command {reason}. "
                "See .claude/rules/daemon-handling.md. To verify code changes "
                "end-to-end, lease a sandbox-pool member via "
                "POST /sandbox/api/lease (:9002+), never the user's daemon."
            ),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
