"""The SessionStart hooks print `session: #<sid8>` — the only way a session
learns its own id, which a plan-row claim records (`.claude/rules/session-plans.md`).

Runs the real hook scripts as subprocesses with a hook payload on stdin, against
an empty project dir, so no vault or daemon is involved.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from hook_common import session_tag  # noqa: E402

SID = "70b339b6-4caa-4074-99ca-43760fe62115"


@pytest.mark.parametrize("raw,want", [
    (SID, "#70b339b6"),
    ("70B339B6-4CAA", "#70b339b6"),
    ("", ""),
    (None, ""),
    ("not-a-uuid-at-all", ""),   # refused, never truncated into a wrong tag
    ("70b339", ""),              # too short
    ("70b339b6zzzz", ""),        # 8 hex then garbage: not an id, so not truncated into one
])
def test_session_tag(raw, want):
    assert session_tag({"session_id": raw}) == want


def test_session_tag_without_payload():
    assert session_tag(None) == ""


def _run(script: str, payload: dict, root: Path) -> str:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, str(SCRIPTS / script)], input=json.dumps(payload),
                         capture_output=True, text=True, env=env, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_startup_brief_prints_the_session_line_even_with_nothing_else(tmp_path):
    ctx = json.loads(_run("session_start_brief.py", {"session_id": SID}, tmp_path))
    assert "session: #70b339b6" in ctx["hookSpecificOutput"]["additionalContext"]


def test_startup_brief_stays_silent_with_no_session_and_nothing_to_say(tmp_path):
    assert _run("session_start_brief.py", {}, tmp_path).strip() == ""


def test_compaction_refresh_keeps_the_session_line(tmp_path):
    ctx = json.loads(_run("session_start_refresh.py", {"session_id": SID}, tmp_path))
    assert "session: #70b339b6" in ctx["hookSpecificOutput"]["additionalContext"]


def test_compaction_refresh_omits_the_line_rather_than_a_placeholder(tmp_path):
    # The resume skill copies this line into a claim; a placeholder would read as a stamp.
    ctx = json.loads(_run("session_start_refresh.py", {}, tmp_path))
    assert "session:" not in ctx["hookSpecificOutput"]["additionalContext"]
