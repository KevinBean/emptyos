"""Unit tests for the config-fallback path shared by the two KB/app alignment
maintenance scripts (scripts/audit_kb_app_alignment.py,
scripts/fix_kb_app_alignment_vault.py).

Both scripts read `emptyos.toml` and, on a fresh clone where that file is
gitignored-and-absent, fall back to the tracked `emptyos.example.toml`. This
pins that fallback so a future rename of either file doesn't silently break
it (as happened when the older `emptyos.toml.example` duplicate was retired).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_TOML = (REPO_ROOT / "emptyos.example.toml").read_text(encoding="utf-8")


def _load_audit_module():
    path = REPO_ROOT / "scripts" / "audit_kb_app_alignment.py"
    spec = importlib.util.spec_from_file_location("audit_kb_app_alignment", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_audit_load_config_falls_back_to_example_toml(tmp_path, monkeypatch):
    mod = _load_audit_module()
    (tmp_path / "emptyos.example.toml").write_text(EXAMPLE_TOML, encoding="utf-8")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

    config = mod._load_config()

    assert config == tomllib.loads(EXAMPLE_TOML)


def test_audit_load_config_prefers_real_toml_when_present(tmp_path, monkeypatch):
    mod = _load_audit_module()
    (tmp_path / "emptyos.example.toml").write_text(EXAMPLE_TOML, encoding="utf-8")
    (tmp_path / "emptyos.toml").write_text('[os]\nname = "real"\n', encoding="utf-8")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

    config = mod._load_config()

    assert config == {"os": {"name": "real"}}


def test_fix_vault_script_falls_back_to_example_toml_and_reports_unconfigured(tmp_path):
    # fix_kb_app_alignment_vault.py resolves its own repo root as
    # Path(__file__).resolve().parents[1] and runs `_vault_root()` at import
    # time, so exercising the fallback needs a real subprocess against a
    # scratch layout rather than an in-process import (which would touch
    # this repo's real emptyos.toml). This also pins the honest failure
    # mode: an unconfigured notes.path raises a clear SystemExit rather
    # than silently resolving to a bogus path.
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_src = (REPO_ROOT / "scripts" / "fix_kb_app_alignment_vault.py").read_text(encoding="utf-8")
    (scripts_dir / "fix_kb_app_alignment_vault.py").write_text(script_src, encoding="utf-8")
    (tmp_path / "emptyos.example.toml").write_text(EXAMPLE_TOML, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(scripts_dir / "fix_kb_app_alignment_vault.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "notes.path is not configured" in result.stderr
