#!/usr/bin/env python3
"""PreToolUse hook — make every delete ask the user first.

Sibling of `guard_git_safety.py` and `guard_daemon_safety.py` (same payload
handling, same fail-open posture), with one difference: it does not deny. It
returns ``permissionDecision: "ask"``, which forces a permission prompt for the
command even in auto mode, so the user approves or refuses each delete.

Why it exists: on 2026-09-23 an agent deleted a 6.8 GB, 22,482-file folder, a set
of installers and a zip with ``Remove-Item -Recurse -Force`` after telling the
user it would leave that to them. ``Remove-Item`` bypasses the Recycle Bin, so
nothing could be undone locally. A standing instruction ("finish the work") is
not permission to delete; only the user, per delete, can give that.

What counts as a delete:
  * shell deletes — rm, rmdir, unlink, del, erase, rd, Remove-Item (and its ri
    alias), Clear-Content, Clear-RecycleBin, ``find -delete``, ``git clean -f``,
    ``robocopy /MIR`` or ``/PURGE``;
  * the same through a language runtime — Python ``os.remove`` / ``os.unlink`` /
    ``os.rmdir`` / ``shutil.rmtree`` / ``Path.unlink`` / ``Path.rmdir``, and .NET
    ``[IO.File]::Delete`` / ``[IO.Directory]::Delete``;
  * cloud and account deletes — ``gcloud … delete``, ``firebase … delete``,
    ``gh repo|release|gist … delete``, and any MCP tool whose name says
    delete / trash / remove.

``git rm`` is not asked about: it only removes a tracked file, which git history
keeps.

One exemption: a delete whose every absolute path sits inside Claude's own temp
scratch area (``…/AppData/Local/Temp/claude/…`` or ``/tmp/claude…``) and that
names no relative target it cannot see. Anything the guard cannot prove is
scratch — a relative path, a variable, a ``cd`` into somewhere else — asks.

Always exits 0 — the decision is expressed via JSON, so a bug in this guard can
never hard-block every command.
"""

from __future__ import annotations

import json
import re
import sys

from hook_common import command_code_view as _code_view
from hook_common import tool_input


# Shell delete verbs, matched only in command position (start of the line or
# after ; & | ( or a newline), in the view with quoted data blanked, so the words
# inside a commit message or an echo cannot trigger it.
_CMD_START = r"(?:^|[;&|(\n{]|\bthen\b|\bdo\b|\bxargs\s+)\s*(?:sudo\s+)?"
_SHELL_VERBS = re.compile(
    _CMD_START
    + r"(rm|rmdir|unlink|del|erase|rd|ri|Remove-Item|Clear-Content|Clear-RecycleBin)\b",
    re.I | re.M,
)
_FIND_DELETE = re.compile(r"\bfind\b[^;&|\n]*\s-delete\b", re.I)
_GIT_CLEAN = re.compile(r"\bgit\s+clean\b[^;&|\n]*\s-[a-zA-Z]*f", re.I)
_ROBOCOPY_MIRROR = re.compile(r"\brobocopy\b[^;&|\n]*\s/(?:MIR|PURGE)\b", re.I)
_CLOUD_DELETE = re.compile(
    r"\b(?:gcloud|firebase)\b[^;&|\n]*\bdelete\b"
    r"|\bgh\s+(?:repo|release|gist|secret|variable)\s+delete\b",
    re.I,
)

# Deletes through a language runtime. Matched on the RAW command, because these
# usually live inside `python -c "…"` strings or heredoc bodies.
_API_DELETE = re.compile(
    r"\bos\.(?:remove|unlink|rmdir|removedirs)\s*\("
    r"|\bshutil\.rmtree\s*\("
    r"|\.(?:unlink|rmdir)\s*\("
    r"|\[(?:System\.)?IO\.(?:File|Directory)\]::Delete\s*\(",
    re.I,
)

# MCP tools that delete or trash something.
_MCP_DESTRUCTIVE = re.compile(r"(?:^|_)(?:delete|trash|remove|purge|wipe|destroy)", re.I)

# Where scratch deletes are allowed without asking.
_SCRATCH = re.compile(r"(?:AppData[\\/]+Local[\\/]+Temp[\\/]+claude[\\/]|/tmp/claude)", re.I)
# An absolute Windows or POSIX-drive path token.
_ABS_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|(?<![\w.])/[a-z]/)[^\s\"'`;|&<>)]*")


def _delete_kind(cmd: str) -> str | None:
    """Name the kind of delete a shell command performs, or None."""
    view = _code_view(cmd)
    m = _SHELL_VERBS.search(view)
    if m:
        return m.group(1)
    for rx, label in (
        (_FIND_DELETE, "find -delete"),
        (_GIT_CLEAN, "git clean -f"),
        (_ROBOCOPY_MIRROR, "robocopy /MIR or /PURGE"),
        (_CLOUD_DELETE, "a cloud or repository delete"),
    ):
        if rx.search(view):
            return label
    m = _API_DELETE.search(cmd)
    if m:
        return m.group(0).rstrip("( ")
    return None


def _scratch_only(cmd: str) -> bool:
    """True when every path the command names is inside Claude's temp scratch."""
    paths = _ABS_PATH.findall(cmd)
    if not paths:
        return False
    return all(_SCRATCH.search(p) for p in paths)


def classify(tool_name: str, cmd: str) -> str | None:
    """Return a reason to ask the user, or None to let the call through."""
    if tool_name.startswith("mcp__"):
        verb = tool_name.split("__")[-1]
        if _MCP_DESTRUCTIVE.search(verb):
            return f"calls {tool_name}, which deletes or trashes something"
        return None
    if not cmd:
        return None
    kind = _delete_kind(cmd)
    if not kind:
        return None
    if _scratch_only(cmd):
        return None
    return f"deletes data ({kind})"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = str(payload.get("tool_name") or "")
    shell = tool_name in ("Bash", "PowerShell", "exec_command", "shell")
    if not (shell or tool_name.startswith("mcp__")):
        return 0

    inp = tool_input(payload)
    cmd = str(inp.get("command") or inp.get("cmd") or "") if shell else ""
    reason = classify(tool_name, cmd)
    if not reason:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": (
                f"This command {reason}. Deleting data needs the user's own "
                "approval for this specific delete; a general instruction to finish "
                "a task is not that approval. Say what will be deleted and whether "
                "it can be recovered, then let the user decide."
            ),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
