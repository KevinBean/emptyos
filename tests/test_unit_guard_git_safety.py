"""Unit tests for scripts/guard_git_safety.py — the PreToolUse git guard.

Pins BOTH directions (per `.claude/rules/audits.md`): the guard must fire on the
real regressions that damaged this repo, and stay SILENT on every legitimate git
shape we use daily. A guard that cries wolf gets disabled within a month.

No daemon, no kernel — pure function tests over `classify()`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
# The hook imports `hook_common`, which resolves at runtime because Python puts a
# script's own dir on sys.path[0]. Under importlib that doesn't happen, so add it.
sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "guard_git_safety", _SCRIPTS / "guard_git_safety.py"
)
guard = importlib.util.module_from_spec(_SPEC)
sys.modules["guard_git_safety"] = guard
_SPEC.loader.exec_module(guard)

classify = guard.classify


# ── MUST FIRE — the shapes that actually cost us commits ────────────────────

BLOCKED = [
    # 1. Backtick command substitution inside a double-quoted commit message.
    #    This is the exact shape that ate a word from a real commit body.
    pytest.param(
        'git commit -m "Drops the now-dead `stat` import and `os` usage"',
        "backtick",
        id="backtick-double-quoted-m",
    ),
    pytest.param(
        'git commit -m "ship: does not ship free) and packages via `pip`. Give it a go"',
        "backtick",
        id="backtick-historical-repro",
    ),
    # An UNQUOTED heredoc still expands backticks — must not be mistaken for the fix.
    pytest.param(
        "git commit -F- <<MSG\nsubject\n\nbody with `code` span\nMSG",
        "backtick",
        id="backtick-unquoted-heredoc",
    ),
    pytest.param(
        'git add a.py && git commit -m "fix `foo`"',
        "backtick",
        id="backtick-after-chained-add",
    ),
    # 2. Broad staging — the parallel-session hazard.
    pytest.param("git add -A", "stages EVERYTHING", id="add-dash-A"),
    pytest.param("git add --all", "stages EVERYTHING", id="add-all"),
    pytest.param("git add .", "stages EVERYTHING", id="add-dot"),
    pytest.param("git add -A && git commit -m 'msg'", "stages EVERYTHING", id="add-A-chained"),
    pytest.param('git commit -am "msg"', "every modified tracked file", id="commit-am"),
    pytest.param('git commit -a -m "msg"', "every modified tracked file", id="commit-a"),
]


@pytest.mark.parametrize("cmd,needle", BLOCKED)
def test_blocked(cmd, needle):
    reason = classify(cmd)
    assert reason is not None, f"guard failed to block: {cmd!r}"
    assert needle.lower() in reason.lower()


# ── MUST STAY SILENT — legitimate git we run constantly ─────────────────────

ALLOWED = [
    # The recommended FIX must not trip the guard it recommends.
    ("git commit -F- <<'MSG'\nsubject\n\nbody with `code` spans kept literal.\nMSG",
     "quoted-heredoc-is-the-fix"),
    # Single quotes never expand — a backtick there is literal and safe.
    ("git commit -m 'fix the `parse` helper'", "single-quoted-backtick"),
    # Plain messages, no backticks.
    ('git commit -m "fix(expense): split subscriptions out of bills"', "plain-message"),
    ('git commit scripts/a.py scripts/b.py -m "scoped commit"', "scoped-paths"),
    # A flag that only APPEARS inside the message must not read as a flag.
    ('git commit -m "fix -a bug in the parser"', "flag-lookalike-in-message"),
    ('git commit -m "add . to the ignore list"', "add-dot-lookalike-in-message"),
    ('git commit -m "revert the --all sweep"', "all-lookalike-in-message"),
    # Explicit paths are the whole point — never blocked.
    ("git add scripts/guard_git_safety.py", "explicit-path"),
    ("git add ./scripts/foo.py", "explicit-dot-slash-path"),
    ("git add docs/DEFERRED-WORK.md tests/test_x.py", "multiple-explicit-paths"),
    # --amend is not -a.
    ("git commit --amend --no-edit", "amend-is-not-a"),
    # A heredoc BODY is message data, never a command line. A commit message
    # that merely *describes* the forbidden shapes must not be blocked — this
    # guard's own commit message did exactly that, and an earlier version of
    # the guard blocked it (caught live, 2026-07-12).
    ("git add scripts/x.py && git commit -F- <<'MSG'\n"
     "feat(hooks): block `git add -A` / `git add .` / `git commit -am`\n\n"
     "In Git Bash a backtick in a double-quoted -m is command substitution.\n"
     "MSG",
     "heredoc-body-describing-forbidden-shapes"),
    # Same, unquoted heredoc, no backticks — still just data.
    ("git commit -F- <<MSG\nmentions git add -A in prose\nMSG", "unquoted-heredoc-prose"),
    # Read-only git.
    ("git status --short", "status"),
    ("git log --oneline -20", "log"),
    ('git log --grep "commit" --format="%h"', "log-grep"),
    ("git diff --stat", "diff"),
    # Non-git commands with backticks are none of this guard's business.
    ("echo `date`", "non-git-backtick"),
    ("python -c 'print(1)'", "non-git"),
    ("", "empty"),
]


@pytest.mark.parametrize("cmd,label", ALLOWED)
def test_allowed(cmd, label):
    assert classify(cmd) is None, f"FALSE POSITIVE on {label}: {cmd!r}"


def test_hook_stays_silent_on_non_shell_tool(monkeypatch, capsys):
    """A non-shell tool payload must produce no output and exit 0."""
    import io
    import json

    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"tool_name": "Read", "tool_input": {}}))
    )
    assert guard.main() == 0
    assert capsys.readouterr().out == ""


def test_hook_emits_deny_json(monkeypatch, capsys):
    import io
    import json

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "eats `this` word"'},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert guard.main() == 0  # fail-open: never a hard non-zero exit
    out = json.loads(capsys.readouterr().out)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "backtick" in hso["permissionDecisionReason"].lower()


def test_malformed_stdin_is_fail_open(monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert guard.main() == 0
    assert capsys.readouterr().out == ""
