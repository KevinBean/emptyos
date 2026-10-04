"""`eos autopilot grant` issues on the floor the gates fire on.

With ``[autopilot] derive_floor_from_registry`` on, only the daemon knows the
floor, so the CLI issues through the daemon's issuer for the actor type and
refuses when no daemon answers. With it off, or with ``--data-dir``, the local
policy.json check runs as before. Pure — no daemon, no real data dir.
"""

from __future__ import annotations

import io
import json

import pytest
from typer.testing import CliRunner

from emptyos.cli.commands import autopilot as ap
from emptyos.sdk.autopilot import load_grants

runner = CliRunner()


def _out(res) -> str:
    """CLI output with Rich's line wrapping collapsed, so phrases stay whole."""
    return " ".join(res.output.split())


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "_resolve_data_dir", lambda data_dir: tmp_path)
    return tmp_path


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Sent(list):
    """(url, body) per request, plus the raw requests and their timeouts."""

    def __init__(self):
        super().__init__()
        self.raw = []


def _daemon(monkeypatch, reply):
    """Fake a reachable daemon; record every request the CLI sends it."""
    sent = _Sent()
    monkeypatch.setattr(ap, "daemon_url", lambda: "http://daemon")

    def urlopen(req, timeout=None):
        sent.append((req.full_url, json.loads(req.data.decode("utf-8"))))
        sent.raw.append((req, timeout))
        return _Resp(json.dumps(reply).encode("utf-8"))
    monkeypatch.setattr(ap.urllib.request, "urlopen", urlopen)
    return sent


_GRANT = {"id": "grant-abc", "actor": {"type": "mcp-client", "id": "codex"},
          "verb_pattern": "task.add", "scope": "mcp:codex", "expires_at": None}


def _http_error(code, body):
    import urllib.error
    return urllib.error.HTTPError("http://daemon/x", code, "err", {},
                                  io.BytesIO(json.dumps(body).encode("utf-8")))


def _daemon_raising(monkeypatch, exc):
    monkeypatch.setattr(ap, "daemon_url", lambda: "http://daemon")

    def urlopen(req, timeout=None):
        raise exc
    monkeypatch.setattr(ap.urllib.request, "urlopen", urlopen)


def test_flag_off_checks_policy_json_locally(store, monkeypatch):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: False)
    sent = _daemon(monkeypatch, {"ok": True, "grant": _GRANT})
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add"])
    assert res.exit_code == 0, res.output
    assert sent == []
    assert [g["verb_pattern"] for g in load_grants(store)] == ["task.add"]


def test_flag_on_without_a_daemon_refuses_and_saves_nothing(store, monkeypatch):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    monkeypatch.setattr(ap, "daemon_url", lambda: None)
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add"])
    assert res.exit_code == 1
    assert "only the" in _out(res) and "daemon" in _out(res)
    assert load_grants(store) == []


def test_flag_on_sends_mcp_client_grants_to_the_foundry_route(store, monkeypatch):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    sent = _daemon(monkeypatch, {"ok": True, "grant": _GRANT})
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add", "--ttl", "60"])
    assert res.exit_code == 0, res.output
    assert "grant-abc" in _out(res)
    url, body = sent[0]
    assert url == "http://daemon/agent/api/foundry/grant"
    assert body == {"actor_id": "codex", "verb_pattern": "task.add",
                    "scope": "mcp:codex", "ttl_s": 60, "rationale": ""}
    assert load_grants(store) == []          # the daemon wrote it, not the CLI


def test_flag_on_sends_other_actor_types_to_the_settings_route(store, monkeypatch):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    sent = _daemon(monkeypatch, {"ok": True, "grant": _GRANT})
    res = runner.invoke(ap.autopilot_app, [
        "grant", "claude-cli", "task.add", "--actor-type", "cli", "--scope", "room:r1"])
    assert res.exit_code == 0, res.output
    url, body = sent[0]
    assert url == "http://daemon/settings/api/autopilot/grant"
    assert body == {"actor_type": "cli", "actor_id": "claude-cli", "verb_pattern": "task.add",
                    "scope": "room:r1", "ttl_s": None, "rationale": ""}


def test_a_daemon_refusal_is_reported_and_exits_nonzero(store, monkeypatch):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    _daemon(monkeypatch, {"ok": False, "error": "verb 'custom.verb' is not autopilot-eligible"})
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "custom.verb"])
    assert res.exit_code == 1
    assert "not autopilot-eligible" in _out(res)


def test_data_dir_writes_that_store_locally_and_warns_with_the_flag_on(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    sent = _daemon(monkeypatch, {"ok": True, "grant": _GRANT})
    res = runner.invoke(ap.autopilot_app, [
        "grant", "codex", "task.add", "--data-dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert sent == []
    assert "warning" in _out(res) and "never fires" in _out(res)
    assert [g["verb_pattern"] for g in load_grants(tmp_path)] == ["task.add"]


def test_flag_off_prints_no_floor_warning(store, monkeypatch):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: False)
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add"])
    assert res.exit_code == 0 and "warning" not in _out(res)


def test_success_line_describes_the_stored_grant(store, monkeypatch):
    # The daemon normalises the request; the line must show what it stored.
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    stored = {**_GRANT, "scope": "global", "verb_pattern": "task.*"}
    _daemon(monkeypatch, {"ok": True, "grant": stored})
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add"])
    assert "scope=global" in _out(res) and "task.*" in _out(res)


_CLI_ACTOR = ["--actor-type", "cli", "--scope", "room:r1"]


# Bodies as the daemon really sends them: FastAPI's unknown-route 404 is
# {"detail": ...}; the posture refusal is {"error": ...} and exists only on the
# settings route (operator=True), so only there may the hint say "user posture".
@pytest.mark.parametrize("extra,code,body,reason,hint,not_hint", [
    ([], 404, {"detail": "Not Found"}, "Not Found", "'agent' app", "user posture"),
    (_CLI_ACTOR, 404, {"detail": "Not Found"}, "Not Found", "'settings' app", "'agent' app"),
    (_CLI_ACTOR, 403, {"error": "not available in this edition"},
     "not available in this edition", "user posture", "app installed"),
    ([], 403, {"error": "forbidden"}, "forbidden", None, "user posture"),
])
def test_an_http_error_reports_the_daemons_reason(store, monkeypatch, extra, code, body,
                                                  reason, hint, not_hint):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    _daemon_raising(monkeypatch, _http_error(code, body))
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add", *extra])
    out = _out(res)
    assert res.exit_code == 1
    assert f"HTTP {code}" in out and reason in out
    if hint:
        assert hint in out
    assert not_hint not in out
    assert load_grants(store) == []


def test_the_daemon_request_is_authenticated_and_bounded(store, monkeypatch):
    # An unauthenticated POST 401s in private mode; an unbounded one hangs on a
    # wedged daemon whose health probe still answered.
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    monkeypatch.setenv("EOS_NETWORK_AUTH_TOKEN", "tok-123")
    sent = _daemon(monkeypatch, {"ok": True, "grant": _GRANT})
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add"])
    assert res.exit_code == 0, res.output
    req, timeout = sent.raw[0]
    assert req.get_header("Authorization") == "Bearer tok-123"
    assert req.get_header("Content-type") == "application/json"
    assert req.get_method() == "POST"
    assert isinstance(timeout, (int, float)) and 0 < timeout <= 60


def test_an_unreachable_daemon_mid_request_fails(store, monkeypatch):
    import urllib.error
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    _daemon_raising(monkeypatch, urllib.error.URLError("connection reset"))
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add"])
    assert res.exit_code == 1 and "connection reset" in _out(res)


@pytest.mark.parametrize("reply", [["not", "a", "dict"], {"ok": True}])
def test_a_malformed_reply_fails_cleanly(store, monkeypatch, reply):
    monkeypatch.setattr(ap, "_registry_floor_on", lambda: True)
    _daemon(monkeypatch, reply)
    res = runner.invoke(ap.autopilot_app, ["grant", "codex", "task.add"])
    assert res.exit_code == 1
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception
    assert "failed" in _out(res)


@pytest.mark.parametrize("value,on", [
    ("true", True), ("1", True), ("yes", True), ("On", True),
    ("false", False), ("0", False), ("no", False), ("", False),
])
def test_registry_floor_flag_reads_like_the_kernel(monkeypatch, value, on):
    monkeypatch.setattr(ap, "find_config", lambda: None)
    monkeypatch.setenv("EOS_AUTOPILOT_DERIVE_FLOOR_FROM_REGISTRY", value)
    assert ap._registry_floor_on() is on


def test_the_env_override_beats_the_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text("[autopilot]\nderive_floor_from_registry = true\n", encoding="utf-8")
    monkeypatch.setattr(ap, "find_config", lambda: str(cfg))
    monkeypatch.setenv("EOS_AUTOPILOT_DERIVE_FLOOR_FROM_REGISTRY", "false")
    assert ap._registry_floor_on() is False


def test_registry_floor_flag_reads_the_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text("[autopilot]\nderive_floor_from_registry = true\n", encoding="utf-8")
    monkeypatch.delenv("EOS_AUTOPILOT_DERIVE_FLOOR_FROM_REGISTRY", raising=False)
    monkeypatch.setattr(ap, "find_config", lambda: str(cfg))
    assert ap._registry_floor_on() is True
