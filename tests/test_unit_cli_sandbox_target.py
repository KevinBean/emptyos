from __future__ import annotations

import json
from pathlib import Path


def _write_main_config(root: Path) -> Path:
    cfg = root / "emptyos.toml"
    cfg.write_text('[os]\ndata_dir = "./data"\n[network]\nport = 9000\n', encoding="utf-8")
    return cfg


def _write_member(root: Path, port: int):
    d = root / f"sandbox-{port}"
    d.mkdir(parents=True)
    (d / "emptyos.toml").write_text(
        f'[network]\nmode = "local"\nport = {port}\n',
        encoding="utf-8",
    )


def test_resolve_prefers_ready_leased_member(monkeypatch, tmp_path):
    cfg = _write_main_config(tmp_path)
    _write_member(tmp_path, 9002)
    _write_member(tmp_path, 9003)
    state_dir = tmp_path / "data" / "sandbox"
    state_dir.mkdir(parents=True)
    (state_dir / "pool.json").write_text(
        json.dumps({
            "members": [
                {"port": 9002, "state": "idle", "lease_id": None},
                {"port": 9003, "state": "leased", "lease_id": "lease-1"},
            ]
        }),
        encoding="utf-8",
    )

    from emptyos.cli import sandbox_target

    monkeypatch.setattr(
        sandbox_target,
        "_member_health",
        lambda port: {"reachable": True, "ready": True, "status": "ok", "apps": 10},
    )

    assert sandbox_target.resolve_sandbox_config(str(cfg)) == tmp_path / "sandbox-9003" / "emptyos.toml"


def test_resolve_rejects_not_ready_member(monkeypatch, tmp_path):
    cfg = _write_main_config(tmp_path)
    _write_member(tmp_path, 9002)

    from emptyos.cli import sandbox_target

    monkeypatch.setattr(
        sandbox_target,
        "_member_health",
        lambda port: {"reachable": True, "ready": False, "status": "starting", "apps": 0},
    )

    try:
        sandbox_target.resolve_sandbox_config(str(cfg), port=9002)
    except RuntimeError as e:
        assert "Sandbox 9002 is not ready" in str(e)
    else:
        raise AssertionError("expected not-ready sandbox to be rejected")


def test_eos_sandbox_callback_sets_eos_config(monkeypatch, tmp_path):
    cfg = _write_main_config(tmp_path)
    target = tmp_path / "sandbox-9002" / "emptyos.toml"
    target.parent.mkdir()
    target.write_text("[network]\nport = 9002\n", encoding="utf-8")

    from typer.testing import CliRunner

    from emptyos.cli import main

    monkeypatch.setenv("EOS_CONFIG", str(cfg))
    monkeypatch.setattr(
        "emptyos.cli.sandbox_target.activate_sandbox_config",
        lambda _main_config, _port=None: str(target),
    )

    result = CliRunner().invoke(main.app, ["--sandbox"])

    assert result.exit_code == 0
    assert "Config:" in result.output
    assert "sandbox-9002/emptyos.toml" in result.output.replace("\\", "/")
