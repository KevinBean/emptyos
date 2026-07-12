from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from emptyos.cli import _common


def test_emit_json_envelope_and_success_exit(capsys):
    with pytest.raises(typer.Exit) as exc:
        _common.emit(True, "ok", "done", {"n": 1}, as_json=True)

    assert exc.value.exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "code": "ok",
        "message": "done",
        "data": {"n": 1},
    }


def test_emit_failure_exit_code(capsys):
    with pytest.raises(typer.Exit) as exc:
        _common.emit(False, "drift", "changed", as_json=True)

    assert exc.value.exit_code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_config_candidates_include_pointer_target(monkeypatch, tmp_path):
    home = tmp_path / "home"
    pointer = home / ".config" / "emptyos" / "config-path.txt"
    pointer.parent.mkdir(parents=True)
    target = tmp_path / "config" / "emptyos.toml"
    target.parent.mkdir()
    target.write_text("[network]\nport = 9010\n", encoding="utf-8")
    pointer.write_text(str(target), encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("EOS_CONFIG", raising=False)
    monkeypatch.chdir(workdir)

    assert target in _common.config_candidates()
    assert _common.find_config() == str(target)


def test_resolve_data_dir_uses_canonical_os_shape(tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text('[os]\ndata_dir = "./runtime-data"\n', encoding="utf-8")

    assert _common.resolve_data_dir(config_path=cfg) == tmp_path / "runtime-data"


def test_resolve_data_dir_keeps_legacy_data_shape(tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text('[data]\npath = "./legacy-data"\n', encoding="utf-8")

    assert _common.resolve_data_dir(config_path=cfg) == tmp_path / "legacy-data"


def test_bearer_headers_with_and_without_token(monkeypatch, tmp_path):
    monkeypatch.delenv("EOS_NETWORK_AUTH_TOKEN", raising=False)
    private = tmp_path / "private.toml"
    private.write_text('[network]\nauth_token = "secret"\n', encoding="utf-8")
    public = tmp_path / "public.toml"
    public.write_text("[network]\n", encoding="utf-8")

    assert _common.bearer_headers(private, json_content=True) == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
    }
    assert _common.bearer_headers(public) == {}


def test_client_base_url_normalizes_bind_all(tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text(
        '[network]\nmode = "private"\nhost = "0.0.0.0"\nport = 9123\n',
        encoding="utf-8",
    )

    assert _common.client_base_url(cfg) == "http://127.0.0.1:9123"


def test_env_key_for_maps_dotted_keys():
    assert _common.env_key_for("network.auth_token") == "EOS_NETWORK_AUTH_TOKEN"
    assert _common.env_key_for("os.data_dir") == "EOS_OS_DATA_DIR"


def test_cfg_get_matches_kernel_config_get(monkeypatch, tmp_path):
    """Parity gate against the class `_common` mirrors.

    `_common` is deliberately kernel-free, which is exactly why nothing else
    would notice it drifting from `Config.get`. Losing its env-override layer
    is what broke the demo container's auth once already.
    """
    from emptyos.kernel.config import Config

    cfg = tmp_path / "emptyos.toml"
    cfg.write_text(
        '[network]\nmode = "private"\nport = 9000\nauth_token = "from-toml"\n'
        '[os]\ndata_dir = "./data"\n',
        encoding="utf-8",
    )
    keys = [
        "network.mode",
        "network.port",
        "network.auth_token",
        "network.host",  # absent from the TOML
        "os.data_dir",
        "missing.key",
    ]
    parsed = _common._load_config(cfg)

    for key in keys:
        assert _common.cfg_get(parsed, key) == Config(str(cfg)).get(key), key

    monkeypatch.setenv("EOS_NETWORK_AUTH_TOKEN", "from-env")
    monkeypatch.setenv("EOS_NETWORK_PORT", "9002")
    monkeypatch.setenv("EOS_NETWORK_HOST", "10.0.0.5")

    for key in keys:
        assert _common.cfg_get(parsed, key) == Config(str(cfg)).get(key), key


def test_bearer_headers_honour_env_token(monkeypatch, tmp_path):
    """The demo container injects EOS_NETWORK_AUTH_TOKEN and leaves it out of TOML."""
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text('[network]\nmode = "public"\n', encoding="utf-8")
    monkeypatch.setenv("EOS_NETWORK_AUTH_TOKEN", "from-env")

    assert _common.bearer_headers(cfg) == {"Authorization": "Bearer from-env"}


def test_env_token_applies_without_a_config_file(monkeypatch):
    monkeypatch.setattr(_common, "find_config", lambda: None)
    monkeypatch.setenv("EOS_NETWORK_AUTH_TOKEN", "from-env")

    assert _common.bearer_headers() == {"Authorization": "Bearer from-env"}


def test_env_token_overrides_toml_token(monkeypatch, tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text('[network]\nauth_token = "from-toml"\n', encoding="utf-8")
    monkeypatch.setenv("EOS_NETWORK_AUTH_TOKEN", "from-env")

    assert _common.bearer_headers(cfg)["Authorization"] == "Bearer from-env"


def test_client_base_url_honours_env_host_and_port(monkeypatch, tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text('[network]\nmode = "local"\nport = 9000\n', encoding="utf-8")
    monkeypatch.setenv("EOS_NETWORK_PORT", "9002")

    assert _common.client_base_url(cfg) == "http://127.0.0.1:9002"


def test_resolve_data_dir_honours_env_override(monkeypatch, tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text('[os]\ndata_dir = "./runtime-data"\n', encoding="utf-8")
    override = tmp_path / "elsewhere"
    monkeypatch.setenv("EOS_OS_DATA_DIR", str(override))

    assert _common.resolve_data_dir(config_path=cfg) == override


def test_explicit_data_dir_beats_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EOS_OS_DATA_DIR", str(tmp_path / "env"))
    explicit = tmp_path / "explicit"

    assert _common.resolve_data_dir(explicit=explicit) == explicit


def test_health_snapshot_reports_http_status_on_error(monkeypatch):
    import urllib.error

    def _raise(*_a, **_k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(_common.urllib.request, "urlopen", _raise)

    assert _common.health_snapshot("http://127.0.0.1:9002") == {
        "reachable": False,
        "ready": False,
        "status": "http_401",
        "apps": 0,
    }


def test_health_snapshot_requires_ok_status_and_loaded_apps(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"status":"ok","apps":7}'

    monkeypatch.setattr(_common.urllib.request, "urlopen", lambda *a, **k: Response())

    assert _common.health_snapshot("http://127.0.0.1:9002") == {
        "reachable": True,
        "ready": True,
        "status": "ok",
        "apps": 7,
    }


def test_json_envelope_consumers_remain_machine_parseable(tmp_path):
    from typer.testing import CliRunner

    from emptyos.cli import main

    runner = CliRunner()
    commands = [
        ["verb", "list", "--json"],
        ["autopilot", "review", "--json", "--data-dir", str(tmp_path)],
        ["autopilot", "budget", "show", "--json", "--data-dir", str(tmp_path)],
    ]

    for command in commands:
        result = runner.invoke(main.app, command)
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True
        assert envelope["code"] == "ok"
        assert "data" in envelope
