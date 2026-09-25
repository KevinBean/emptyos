"""``run_json_script`` — the shared "write spec.json, run runner.py against
it, parse JSON stdout into a structured envelope" plumbing extracted from
``plugins/cadquery``'s ``_run_subprocess`` at the second consumer
(``plugins/manim``'s ``render_scene``). Runs against the real ``sys.executable``
with tiny inline runner scripts — no daemon, no user-home venv needed.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from emptyos.sdk.userhome_venv import run_json_script

HAPPY_RUNNER = """\
import json, sys
spec = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"ok": True, "echo": spec["x"]}))
"""

CRASH_RUNNER = """\
import json, sys
print(json.dumps({"ok": False, "stage": "load", "error": "handled crash"}))
sys.exit(2)
"""

BAD_JSON_RUNNER = """\
print("not json at all")
"""

EMPTY_STDOUT_RUNNER = """\
import sys
sys.exit(0)
"""


def _write_runner(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


class TestRunJsonScriptHappyPath:
    def test_parses_runner_json_stdout_verbatim(self, tmp_path):
        runner = _write_runner(tmp_path, "runner.py", HAPPY_RUNNER)
        payload, result = asyncio.run(run_json_script(
            sys.executable, runner, tmp_path / "spec.json", {"x": 42},
            domain_label="Test interpreter", action_label="run",
        ))
        assert payload == {"ok": True, "echo": 42}
        assert result.ok
        assert result.returncode == 0

    def test_writes_the_spec_as_json(self, tmp_path):
        runner = _write_runner(tmp_path, "runner.py", HAPPY_RUNNER)
        spec_path = tmp_path / "spec.json"
        asyncio.run(run_json_script(
            sys.executable, runner, spec_path, {"x": "hello"},
        ))
        assert json.loads(spec_path.read_text(encoding="utf-8")) == {"x": "hello"}


class TestRunJsonScriptErrorEnvelopes:
    def test_launch_failure_reports_domain_label(self, tmp_path):
        non_exe = tmp_path / "not-an-interpreter.exe"
        non_exe.write_text("not a real binary", encoding="utf-8")
        payload, result = asyncio.run(run_json_script(
            str(non_exe), "runner.py", tmp_path / "spec.json", {},
            domain_label="Widget interpreter",
        ))
        assert payload["ok"] is False
        assert payload["stage"] == "plugin"
        assert "failed to launch Widget interpreter" in payload["error"]
        assert result.launch_failed

    def test_empty_stdout_reports_exit_code(self, tmp_path):
        runner = _write_runner(tmp_path, "runner.py", EMPTY_STDOUT_RUNNER)
        payload, result = asyncio.run(run_json_script(
            sys.executable, runner, tmp_path / "spec.json", {},
        ))
        assert payload["ok"] is False
        assert "no stdout" in payload["error"]
        assert "exit=0" in payload["error"]

    def test_invalid_json_stdout_is_reported_not_raised(self, tmp_path):
        runner = _write_runner(tmp_path, "runner.py", BAD_JSON_RUNNER)
        payload, result = asyncio.run(run_json_script(
            sys.executable, runner, tmp_path / "spec.json", {},
        ))
        assert payload["ok"] is False
        assert "not JSON" in payload["error"]
        assert "not json at all" in payload["error"]

    def test_timeout_reports_action_label_and_seconds(self, tmp_path):
        runner = _write_runner(tmp_path, "runner.py", "import time; time.sleep(5)")
        payload, result = asyncio.run(run_json_script(
            sys.executable, runner, tmp_path / "spec.json", {},
            timeout=0.2, action_label="compile",
        ))
        assert payload["ok"] is False
        assert "compile timeout after 0.2s" in payload["error"]
        assert result.timed_out

    def test_handled_crash_json_passes_through_with_real_returncode(self, tmp_path):
        # A runner that exits non-zero but still prints valid JSON (the
        # cadquery/manim "handled error, exit 2" convention) must pass
        # through untouched — this helper never special-cases returncode,
        # only stdout shape. The caller decides what a given exit code means.
        runner = _write_runner(tmp_path, "runner.py", CRASH_RUNNER)
        payload, result = asyncio.run(run_json_script(
            sys.executable, runner, tmp_path / "spec.json", {},
        ))
        assert payload == {"ok": False, "stage": "load", "error": "handled crash"}
        assert result.returncode == 2
