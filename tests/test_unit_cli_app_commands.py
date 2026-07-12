from __future__ import annotations

import urllib.error
import urllib.request
from types import SimpleNamespace

from emptyos.sdk.cli_args import bind_cli_kwargs


def _sample_command(action: str = "status", lease_id: str = "", purpose: str = "",
                    ttl_s: int = 0, enabled: bool = False):
    return action, lease_id, purpose, ttl_s, enabled


def test_bind_cli_kwargs_supports_positional_and_key_value():
    kwargs = bind_cli_kwargs(
        _sample_command,
        ["lease", "purpose=cli-test", "ttl_s=1800", "--enabled=true"],
    )
    assert kwargs == {
        "action": "lease",
        "lease_id": "",
        "purpose": "cli-test",
        "ttl_s": 1800,
        "enabled": True,
    }


def test_bind_cli_kwargs_unknown_key_falls_through_as_positional():
    kwargs = bind_cli_kwargs(
        _sample_command,
        ["lease", "unknown=value", "ttl_s=1800"],
    )
    assert kwargs == {
        "action": "lease",
        "lease_id": "unknown=value",
        "purpose": "",
        "ttl_s": 1800,
        "enabled": False,
    }


def test_bind_cli_kwargs_key_value_can_skip_optional_positionals():
    kwargs = bind_cli_kwargs(_sample_command, ["lease", "ttl_s=1800"])
    assert kwargs["action"] == "lease"
    assert kwargs["lease_id"] == ""
    assert kwargs["ttl_s"] == 1800


def test_daemon_cli_headers_include_bearer_token(tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text(
        """
[network]
auth_token = "test-token"
""".strip(),
        encoding="utf-8",
    )

    from emptyos.cli import main

    headers = main._daemon_cli_headers(str(cfg))
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer test-token"


def test_register_app_command_uses_per_command_help(monkeypatch, tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text("[network]\n", encoding="utf-8")
    manifest = SimpleNamespace(
        id="sample",
        description="fallback",
        requires={},
        provides={
            "cli": {
                "commands": ["chat", "code"],
                "interactive": ["chat", "code"],
                "help": {"chat": "chat help", "code": "code help"},
            }
        },
    )
    captured = {}

    from emptyos.cli import main

    def fake_command(name, *, help):
        captured[name] = help
        return lambda handler: handler

    monkeypatch.setattr(main.app, "command", fake_command)
    main._register_app_command("code", manifest, str(cfg))

    assert captured == {"code": "code help"}


def test_run_via_daemon_sends_bearer_header(monkeypatch, tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text('[network]\nauth_token = "abc123"\n', encoding="utf-8")
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")

        class Resp:
            def read(self):
                return b'{"ok": true, "output": "done\\n", "error": null}'

        return Resp()

    from emptyos.cli import main

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    main._run_via_daemon("http://127.0.0.1:9000", "providers", "providers", ["live"], str(cfg))

    assert seen == {
        "url": "http://127.0.0.1:9000/api/cli",
        "auth": "Bearer abc123",
    }


def test_service_dependent_command_does_not_fallback_when_daemon_unreachable(
    monkeypatch, tmp_path, capsys,
):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text("[network]\n", encoding="utf-8")

    from emptyos.cli import main

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("refused")),
    )
    fallback_calls = []
    monkeypatch.setattr(main, "_run_locally", lambda *a, **k: fallback_calls.append(a))

    main._run_via_daemon(
        "http://127.0.0.1:9000",
        "sandbox",
        "sandbox",
        ["status"],
        str(cfg),
        daemon_required=True,
        required_services=["sandbox-pool"],
    )

    out = capsys.readouterr().out
    assert "Cannot run 'sandbox' locally" in out
    assert "service:sandbox-pool" in out
    assert fallback_calls == []


def test_service_free_command_falls_back_when_daemon_unreachable(monkeypatch, tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text("[network]\n", encoding="utf-8")

    from emptyos.cli import main

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("refused")),
    )
    fallback_calls = []
    monkeypatch.setattr(main, "_run_locally", lambda *a, **k: fallback_calls.append(a))

    main._run_via_daemon(
        "http://127.0.0.1:9000",
        "providers",
        "providers",
        ["live"],
        str(cfg),
    )

    assert fallback_calls == [("providers", "providers", str(cfg), ["live"])]


def test_service_free_command_falls_back_on_os_error(monkeypatch, tmp_path):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text("[network]\n", encoding="utf-8")

    from emptyos.cli import main

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("refused")),
    )
    fallback_calls = []
    monkeypatch.setattr(main, "_run_locally", lambda *a, **k: fallback_calls.append(a))

    main._run_via_daemon(
        "http://127.0.0.1:9000",
        "providers",
        "providers",
        ["live"],
        str(cfg),
    )

    assert fallback_calls == [("providers", "providers", str(cfg), ["live"])]


def test_http_401_reports_auth_error_without_local_fallback(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text('[network]\nauth_token = "wrong"\n', encoding="utf-8")

    from emptyos.cli import main

    def raise_401(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://127.0.0.1:9000/api/cli",
            401,
            "Unauthorized",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", raise_401)
    fallback_calls = []
    monkeypatch.setattr(main, "_run_locally", lambda *a, **k: fallback_calls.append(a))

    main._run_via_daemon(
        "http://127.0.0.1:9000",
        "sandbox",
        "sandbox",
        ["status"],
        str(cfg),
    )

    out = capsys.readouterr().out
    assert "Daemon rejected 'sandbox' (HTTP 401)" in out
    assert "network.auth_token" in out
    assert fallback_calls == []
