"""Regression tests for the Codex hook wrapper."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import scripts.run_codex_hook as run_codex_hook


def test_wrapper_resets_sys_argv_for_target_script(monkeypatch, tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    target = scripts_dir / "target_hook.py"
    target.write_text("# placeholder\n", encoding="utf-8")

    seen: list[list[str]] = []

    def fake_run_path(path: str, *, run_name: str):
        assert Path(path) == target
        assert run_name == "__main__"
        seen.append(sys.argv[:])

    payload = {"cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(run_codex_hook.runpy, "run_path", fake_run_path)

    rc = run_codex_hook.main(["run_codex_hook.py", "target_hook.py"])

    assert rc == 0
    assert seen == [[str(target)]]
