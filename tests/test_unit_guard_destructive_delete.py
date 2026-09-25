"""Unit tests for scripts/guard_destructive_delete.py — the delete-asks guard.

Pins both directions (`.claude/rules/audits.md`): it must ask on the commands
that actually deleted data without permission on 2026-09-23, and stay silent on
everyday commands that merely mention a delete word. No daemon, no kernel.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location(
    "guard_destructive_delete", _SCRIPTS / "guard_destructive_delete.py"
)
guard = importlib.util.module_from_spec(_SPEC)
sys.modules["guard_destructive_delete"] = guard
_SPEC.loader.exec_module(guard)

classify = guard.classify
SCRATCH = "C:/Users/someone/AppData/Local/Temp/claude/D--repo/abc/scratchpad"


ASKS = [
    # The command that deleted the folder, and the installers beside it.
    ('Remove-Item -LiteralPath "D:\\Archive\\2023-2024work" -Recurse -Force -Confirm:$false', "PowerShell"),
    ('$targets = @("D:\\temp\\old")\nforeach ($t in $targets) { Remove-Item -LiteralPath $t -Recurse -Force }', "PowerShell"),
    ('cd "D:/Notes" && rm -- "30_Resources/some note.md"', "Bash"),
    ("rm -rf build/", "Bash"),
    ("del /s /q D:\\data\\*.db", "PowerShell"),
    ("ri notes.md", "PowerShell"),
    ("find . -name '*.log' -delete", "Bash"),
    ("git clean -fdx", "Bash"),
    ('robocopy "D:/a" "D:/b" /MIR', "PowerShell"),
    ("gcloud projects delete my-project --quiet", "Bash"),
    ("gh repo delete someone/x --yes", "Bash"),
    # Through a runtime, inside a quoted string or a heredoc body.
    ("python -c \"import shutil; shutil.rmtree('D:/stuff')\"", "Bash"),
    ("python - <<'PY'\nimport pathlib\npathlib.Path('D:/Notes/x.md').unlink()\nPY", "Bash"),
    ("[IO.Directory]::Delete('D:\\x', $true)", "PowerShell"),
    # A scratch path does not excuse a second, non-scratch target.
    (f'rm "{SCRATCH}/a.txt" "D:/Notes/b.md"', "Bash"),
    # A relative target cannot be proven to be scratch.
    ("rm -rf crt.git crt_paths.txt", "Bash"),
]


@pytest.mark.parametrize("cmd,tool", ASKS)
def test_asks_before_deleting(cmd, tool):
    assert classify(tool, cmd), cmd


SILENT = [
    ("git status --short", "Bash"),
    ("git rm --cached old.txt", "Bash"),            # git history keeps it
    ('git commit -m "remove the rm -rf from the docs"', "Bash"),
    ("echo 'Remove-Item is dangerous'", "PowerShell"),
    ("grep -rn 'shutil' scripts", "Bash"),
    ("Get-ChildItem -LiteralPath D:\\Archive -Force", "PowerShell"),
    ("python -m pytest tests/test_x.py -q", "Bash"),
    ("npm run format", "Bash"),                      # "rm" inside a word
    ("git checkout -- confirm.txt", "Bash"),
    # Claude's own temp scratch area is exempt.
    (f'rm -rf "{SCRATCH}/crt.git" "{SCRATCH}/crt_paths.txt"', "Bash"),
    (f"Remove-Item -LiteralPath '{SCRATCH}/old.py'", "PowerShell"),
]


@pytest.mark.parametrize("cmd,tool", SILENT)
def test_silent_on_non_deletes(cmd, tool):
    assert classify(tool, cmd) is None, cmd


@pytest.mark.parametrize("name,asks", [
    ("mcp__plugin_firebase_firebase__firestore_delete_document", True),
    ("mcp__claude_ai_Gmail__trash_thread", True),
    ("mcp__claude_ai_Google_Calendar__delete_event", True),
    ("mcp__claude_ai_Gmail__search_threads", False),
    ("mcp__plugin_firebase_firebase__firestore_get_document", False),
])
def test_mcp_deletes_ask(name, asks):
    assert bool(classify(name, "")) is asks


def test_hook_emits_ask_not_deny():
    """End to end through stdin: a delete gets permissionDecision "ask"."""
    payload = {"tool_name": "PowerShell",
               "tool_input": {"command": 'Remove-Item -LiteralPath "D:\\x" -Recurse -Force'}}
    out = subprocess.run([sys.executable, str(_SCRIPTS / "guard_destructive_delete.py")],
                         input=json.dumps(payload), capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    decision = json.loads(out.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "approval" in decision["permissionDecisionReason"]


def test_hook_is_silent_and_fails_open():
    for stdin in (json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}), "not json"):
        out = subprocess.run([sys.executable, str(_SCRIPTS / "guard_destructive_delete.py")],
                             input=stdin, capture_output=True, text=True, timeout=30)
        assert out.returncode == 0 and out.stdout.strip() == ""
