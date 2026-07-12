"""Unit tests for the daemon-safety PreToolUse guard's classifier.

Pure — imports the script module and calls ``classify`` directly, no kernel
boot, no daemon. Pins the two real false positives that motivated the
quote-stripping fix (a commit message / echo / grep that merely *mentions* a
trigger token must NOT be blocked) while keeping genuine daemon-destructive
commands denied.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
# The guard imports `hook_common`, which resolves at runtime because Python puts a
# script's own dir on sys.path[0]. Under importlib that doesn't happen, so add it.
sys.path.insert(0, str(_SCRIPTS))

_GUARD = _SCRIPTS / "guard_daemon_safety.py"
_spec = importlib.util.spec_from_file_location("guard_daemon_safety", _GUARD)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)
classify = guard.classify


class TestAllowsSafeMentions:
    """Trigger tokens quoted as data must pass through (return None)."""

    def test_commit_message_mentioning_restart_bat(self):
        cmd = 'git commit -m "feat(hooks): block restart.bat / taskkill python / data/*.db deletes"'
        assert classify(cmd) is None

    def test_echo_comment_with_kill_word(self):
        assert classify('echo "=== silent-load-kill shape ==="') is None

    def test_grep_over_emptyos_files_with_kill_in_echo(self):
        cmd = 'grep -rn "import" emptyos/sdk/cable_render.py; echo "no kill found"'
        assert classify(cmd) is None

    def test_commit_message_mentioning_emptyos_start(self):
        assert classify('git commit -m "docs: how to run emptyos start"') is None

    def test_grep_for_db_string(self):
        assert classify('grep -rn "data/foo.db" docs/') is None


class TestDeniesRealThreats:
    """Actual destructive invocations must still be blocked."""

    def test_restart_bat(self):
        assert classify("restart.bat") is not None

    def test_stop_bat_pathed(self):
        assert classify("D:/emptyos/stop.bat") is not None

    def test_python_m_emptyos_start(self):
        assert classify("python -m emptyos start") is not None

    def test_eos_start(self):
        assert classify("eos start") is not None

    def test_taskkill_python(self):
        assert classify("taskkill /F /IM python.exe") is not None

    def test_taskkill_quoted_target(self):
        # verb unquoted, target quoted — still caught (path matches original cmd)
        assert classify('taskkill /F /IM "python.exe"') is not None

    def test_stop_process_python(self):
        assert classify("Get-Process python | Stop-Process") is not None

    def test_rm_db_file(self):
        assert classify("rm data/syslog.db") is not None

    def test_rm_quoted_db_path(self):
        assert classify('rm "data/syslog.db-wal"') is not None

    def test_remove_item_data_recurse(self):
        assert classify("Remove-Item data/ -Recurse -Force") is not None

    def test_rm_rf_data_dir(self):
        assert classify("rm -rf data/") is not None


class TestNeutralCommands:
    """Ordinary safe commands are never blocked."""

    def test_git_status(self):
        assert classify("git status --short") is None

    def test_curl_sandbox(self):
        assert classify("curl -s http://127.0.0.1:9002/api/health") is None

    def test_pytest(self):
        assert classify("python -m pytest tests/ -v") is None

    def test_tasklist_diagnostic(self):
        assert classify("tasklist | findstr python") is None


class TestHookPayloads:
    """Payload parsing must cover both Claude and Codex shell tool shapes."""

    def test_codex_exec_command_payload_denies_daemon_start(self, monkeypatch, capsys):
        payload = {
            "tool_name": "exec_command",
            "tool_input": {"cmd": "python -m emptyos start"},
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

        assert guard.main() == 0
        out = json.loads(capsys.readouterr().out)
        decision = out["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"


class TestHeredocBodyIsData:
    """A heredoc BODY is prose, never a command line.

    Regression, 2026-07-12: this guard blocked a devlog being appended via
    `cat >> file <<'EOF'` because the prose contained the words "kill switch"
    and "emptyos" — its verb + target regexes read that as a kill aimed at the
    daemon. It blanked quoted spans but not heredoc bodies. The shared
    `hook_common.command_code_view` now blanks both. Same bug independently hit
    guard_git_safety.py on its own commit message.
    """

    def test_devlog_heredoc_mentioning_kill_and_emptyos(self):
        cmd = '''cat >> "D:/Vault/log/2026-07-12.md" <<'EOF'
Its headline "100% kill" is not a kill switch: the hook returns continue:true
even at 100%. Meanwhile emptyos already has run_budget.py, which actually raises.
EOF'''
        assert classify(cmd) is None

    def test_commit_heredoc_mentioning_taskkill_and_db_delete(self):
        cmd = """git commit -F- <<'MSG'
docs: explain why we never taskkill python or delete data/*.db in emptyos
MSG"""
        assert classify(cmd) is None

    def test_real_kill_outside_a_heredoc_is_still_blocked(self):
        """The blanking must not become an escape hatch: a genuine kill sitting
        AFTER a heredoc still gets caught."""
        cmd = """cat >> notes.md <<'EOF'
harmless prose
EOF
taskkill /F /IM python.exe"""
        assert classify(cmd) is not None
