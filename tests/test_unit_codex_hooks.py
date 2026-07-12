from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


hook_common = _load("hook_common", ROOT / "scripts" / "hook_common.py")
check_restart = _load(
    "check_daemon_restart_needed",
    ROOT / "scripts" / "check_daemon_restart_needed.py",
)
suggest_skill = _load("suggest_skill_on_edit", ROOT / "scripts" / "suggest_skill_on_edit.py")


def test_edited_paths_extracts_apply_patch_files():
    payload = {
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": """*** Begin Patch
*** Update File: apps/public/core/foo/app.py
@@
 pass
*** Add File: apps/public/core/foo/pages/index.html
+<main></main>
*** Delete File: plugins/old/plugin.py
*** End Patch
"""
        },
    }

    assert hook_common.edited_paths(payload) == [
        "apps/public/core/foo/app.py",
        "apps/public/core/foo/pages/index.html",
        "plugins/old/plugin.py",
    ]


def test_check_daemon_restart_needed_accepts_codex_patch_payload(monkeypatch, capsys):
    payload = {
        "cwd": str(ROOT),
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": """*** Begin Patch
*** Update File: emptyos/kernel/config.py
@@
 pass
*** End Patch
"""
        },
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert check_restart.main() == 0
    assert "emptyos/kernel/config.py" in capsys.readouterr().err


def test_suggest_skill_on_edit_accepts_codex_patch_payload(monkeypatch, capsys):
    payload = {
        "cwd": str(ROOT),
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": """*** Begin Patch
*** Update File: apps/public/core/foo/pages/index.html
@@
 <main></main>
*** End Patch
"""
        },
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert suggest_skill.main() == 0
    assert "/eos-page-design-review" in capsys.readouterr().err
