"""End-to-end tests for the agent hook scripts — run as REAL subprocesses.

Why this file exists, separate from the per-hook unit tests: those load each hook
via `importlib` with an explicit `sys.path.insert(0, "scripts")`. That does NOT
reproduce how a hook actually runs. In production Claude Code invokes

    python <root>/scripts/<hook>.py

and Python puts the *script's own directory* on `sys.path[0]`, which is the only
reason `from hook_common import ...` resolves at all (see hook_common's module
docstring). The unit tests paper over that with their own path insert — so a
broken import, a bad shebang, or a hook that only works from the project cwd
would sail through them.

That is not hypothetical: the 2026-07-12 `hook_common` refactor passed 48 unit
tests while the real-script path was unverified; it was only confirmed by hand.
These tests make the contract non-optional:

  - the hook imports and runs as a standalone script,
  - from a foreign cwd,
  - speaks the hook protocol on stdin/stdout,
  - and ALWAYS exits 0 (fail-open — a non-zero exit from a PreToolUse hook would
    hard-block every command in the session).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

HOOKS = [
    "guard_daemon_safety.py",
    "guard_git_safety.py",
    "check_daemon_restart_needed.py",
    "check_gitignored_path.py",
    "gate_syntax_check.py",
    "probe_hook_payload.py",
]


def run_hook(name: str, payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke a hook exactly the way Claude Code does: as a script, over stdin."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(cwd),
        env={"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": __import__("os").environ.get("PATH", "")},
    )


@pytest.mark.parametrize("hook", HOOKS)
def test_hook_imports_and_exits_zero_from_foreign_cwd(hook, tmp_path):
    """Every hook must import (incl. `hook_common`) and exit 0 on a benign payload.

    Run from tmp_path, NOT the project root — a hook that only resolves its
    imports or its root when cwd happens to be the repo is broken.
    """
    proc = run_hook(hook, {"tool_name": "Read", "tool_input": {}}, cwd=tmp_path)
    assert proc.returncode == 0, f"{hook} exited {proc.returncode}: {proc.stderr}"
    assert "Traceback" not in proc.stderr, f"{hook} raised:\n{proc.stderr}"
    assert "ModuleNotFoundError" not in proc.stderr


def test_git_guard_denies_backtick_commit_as_a_real_script(tmp_path):
    proc = run_hook(
        "guard_git_safety.py",
        {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "eats `this` word"'}},
        cwd=tmp_path,
    )
    assert proc.returncode == 0  # deny is expressed in JSON, never via exit code
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_git_guard_allows_the_fix_it_recommends(tmp_path):
    """The quoted-heredoc form the guard tells you to use must not be blocked."""
    cmd = "git commit -F- <<'MSG'\nfix: keep `code spans` literal\nMSG"
    proc = run_hook("guard_git_safety.py", {"tool_name": "Bash", "tool_input": {"command": cmd}}, cwd=tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_syntax_gate_blocks_broken_python_as_a_real_script(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n")
    payload = {
        "hook_event_name": "PostToolBatch",
        "tool_calls": [{"tool_name": "Edit", "tool_input": {"file_path": str(bad)}}],
    }
    proc = run_hook("gate_syntax_check.py", payload, cwd=tmp_path)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert "SyntaxError" in out["reason"]


def test_syntax_gate_silent_on_healthy_batch_as_a_real_script(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("x = 1\n")
    payload = {
        "hook_event_name": "PostToolBatch",
        "tool_calls": [{"tool_name": "Edit", "tool_input": {"file_path": str(good)}}],
    }
    proc = run_hook("gate_syntax_check.py", payload, cwd=tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_every_registered_hook_exists_on_disk():
    """settings.json must not reference a script that isn't there — a missing hook
    fails silently per-call, which is the worst way to lose a guard."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    missing = []
    for entries in (settings.get("hooks") or {}).values():
        for entry in entries:
            for h in entry.get("hooks") or []:
                cmd = h.get("command") or ""
                if "scripts/" not in cmd:
                    continue
                name = cmd.split("scripts/")[-1].rstrip('"').strip()
                if not (SCRIPTS / name).is_file():
                    missing.append(name)
    assert not missing, f"settings.json references missing hook scripts: {missing}"
