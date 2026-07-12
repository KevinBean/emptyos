#!/usr/bin/env python3
"""PreToolUse hook — block the two git shapes that have each bitten us twice.

Sibling of `guard_daemon_safety.py` (same deny protocol, same fail-open
posture). Turns two CLAUDE.md § "Git / Version Control" rules from advice into
enforcement:

  1. BACKTICKS IN A COMMIT MESSAGE. In the Bash tool (Git Bash / POSIX sh), a
     backtick inside a *double-quoted* string is command substitution, not a
     markdown code span. A `git commit -m "Drops the `stat` import"` silently
     ships with the word `stat` MISSING and emits stray shell noise that is
     easy to skim past. Real damage still in our history (`…and packages via .
     Give it a` — a word eaten mid-sentence). There is no legitimate use of an
     unescaped backtick in a double-quoted commit message, which makes this a
     zero-false-positive deny. Fix: a single-quoted heredoc (`<<'MSG'`).

  2. `git add -A` / `git add .` / `git commit -a`. Another Claude session or the
     user may have staged files concurrently in the SAME working tree; a broad
     stage sweeps their work into your commit (or yours into theirs). Stage
     explicit paths instead.

WHAT THIS CANNOT DO: it cannot fix the shared-index race itself. Chaining
`add && commit` does NOT help when the other session staged *before* your add.
The guard only removes the broad-stage foot-gun; the race needs `git status
--short` + scoped paths + a post-commit HEAD check (CLAUDE.md).

Safe forms pass through untouched: single-quoted messages, single-quoted
heredocs (`<<'MSG'`), `git add <explicit paths>`, and every non-git command.

Always exits 0 — the deny is expressed via JSON, so a bug in this guard can
never hard-block every command.
"""

from __future__ import annotations

import json
import re
import sys

from hook_common import command_code_view as _flags_view
from hook_common import tool_input


# A git-commit invocation anywhere in the command line (incl. after && / ;).
_GIT_COMMIT = re.compile(r"\bgit\b[^|;&\n]*?\bcommit\b", re.I)

# A git-add invocation, capturing the first argument.
_GIT_ADD_BROAD = re.compile(r"\bgit\s+add\s+(?:-{1,2}\S+\s+)*(-A\b|--all\b|\.(?=\s|$))", re.I)

# `git commit -a` / `-am` / `--all` — stages every modified tracked file.
_GIT_COMMIT_ALL = re.compile(r"\bgit\s+commit\b[^|;&\n]*?\s(?:-[a-zA-Z]*a[a-zA-Z]*|--all)\b")

# Single-quoted spans: POSIX sh does NOT expand anything inside them, so a
# backtick here is literal and harmless.
_SQ = re.compile(r"'[^']*'")

# A quoted heredoc (<<'EOF' … EOF or <<-'EOF'). The quoted delimiter disables
# expansion, so the body is literal — this is the FIX we recommend, and it must
# not trip the guard. An UNQUOTED heredoc (<<EOF) *does* expand backticks, so it
# is deliberately left visible to the scan.
_HEREDOC_QUOTED = re.compile(
    r"<<-?\s*(['\"])(\w+)\1.*?^\2$",
    re.S | re.M,
)


def _literal_stripped(cmd: str) -> str:
    """Drop the spans where a backtick cannot be expanded by the shell.

    Removes single-quoted strings and quoted-heredoc bodies. Whatever backtick
    survives is one the shell WILL execute.
    """
    out = _HEREDOC_QUOTED.sub(" ", cmd)
    out = _SQ.sub(" ", out)
    return out


# Flags/verbs-only view: quoted spans AND heredoc bodies blanked, so a commit
# message that merely *describes* `git add -A` (this guard's own commit did) isn't
# mistaken for one. Shared with guard_daemon_safety.py — see hook_common.
# NOTE `_flags_view` (imported above) is deliberately BROADER than
# `_HEREDOC_QUOTED` above, which stays narrow because an UNQUOTED heredoc still
# expands backticks and so must remain visible to the backtick scan.


def classify(cmd: str) -> str | None:
    """Return a deny reason if the command is an unsafe git shape, else None."""
    if not cmd or "git" not in cmd.lower():
        return None

    flags = _flags_view(cmd)

    if _GIT_COMMIT.search(flags) and "`" in _literal_stripped(cmd):
        return (
            "puts a BACKTICK inside a commit message the shell will expand. In "
            "Git Bash, `x` in a double-quoted string is command substitution, not "
            "a markdown code span — the word is silently REPLACED by the output of "
            "running it, and the commit ships with text missing (this has already "
            "damaged two commits in this repo). Use a single-quoted heredoc:\n"
            "  git commit -F- <<'MSG'\n  subject line\n\n  body with `code spans` "
            "kept literal.\n  MSG"
        )

    if _GIT_ADD_BROAD.search(flags):
        return (
            "stages EVERYTHING (`git add -A` / `git add .`). Another Claude session "
            "or the user may have files staged in this same working tree, and a "
            "broad stage sweeps their work into your commit. Run `git status "
            "--short`, then stage only the paths you touched this session"
        )

    if _GIT_COMMIT_ALL.search(flags):
        return (
            "commits every modified tracked file (`git commit -a` / `-am`) — the "
            "same parallel-session hazard as `git add -A`. Stage explicit paths, "
            "then `git commit <paths> -m ...`"
        )

    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = payload.get("tool_name") or ""
    if tool_name not in ("Bash", "PowerShell", "exec_command", "shell"):
        return 0

    inp = tool_input(payload)
    cmd = str(inp.get("command") or inp.get("cmd") or "")
    reason = classify(cmd)
    if not reason:
        return 0  # silent → normal permission evaluation proceeds

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Blocked by git-safety guard: this command {reason}\n\n"
                "See CLAUDE.md § Git / Version Control."
            ),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
