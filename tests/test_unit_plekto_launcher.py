from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "plekto_launcher.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("plekto_launcher_under_test", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_startup_url_opens_code_workspace():
    launcher = _load_launcher()
    assert launcher._startup_url(9010) == "http://127.0.0.1:9010/code/"


def test_first_run_setup_writes_local_config(tmp_path):
    launcher = _load_launcher()
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"

    config_path = launcher.first_run_setup(data_dir, vault_dir, 9010)

    assert config_path == data_dir / "emptyos.toml"
    assert vault_dir.is_dir()
    text = config_path.read_text(encoding="utf-8")
    assert "port = 9010" in text
    assert "mode = \"local\"" in text
    assert str(vault_dir.as_posix()) in text
