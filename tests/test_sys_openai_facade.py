"""System tests for the OpenAI-compatible /v1 facade.

The facade routes any LiteLLM-shaped chat completions call through
EmptyOS's ``think`` capability — so a single endpoint exposes the whole
provider chain (claude-cli, ollama, openai-mini, …) to external tools.

Tests run against the live daemon (or sandbox via EOS_TEST_DAEMON env)
and use the EmptyOS auth_token as the bearer credential. They exercise
the wire-shape contract; they don't assert on the LLM output text.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_URL = os.environ.get("EOS_TEST_DAEMON", "http://localhost:9000")


def _auth_token() -> str:
    """Read auth_token from emptyos.toml."""
    try:
        import tomllib

        cfg = tomllib.loads((REPO_ROOT / "emptyos.toml").read_text(encoding="utf-8"))
        return (cfg.get("network") or {}).get("auth_token", "") or ""
    except Exception:
        return ""


def _auth_headers() -> dict:
    tok = _auth_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


@pytest.fixture
def _daemon_reachable():
    try:
        r = httpx.get(f"{DAEMON_URL}/api/health", timeout=2.0)
        if r.status_code != 200:
            pytest.skip(f"daemon at {DAEMON_URL} not healthy")
    except Exception as e:
        pytest.skip(f"daemon at {DAEMON_URL} not reachable: {e}")


# --- Models endpoint -----------------------------------------------------


def test_models_endpoint_returns_list_shape(_daemon_reachable):
    r = httpx.get(f"{DAEMON_URL}/v1/models", headers=_auth_headers(), timeout=5.0)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)
    # At least one provider should be in the chain (human is always last).
    assert data["data"], "expected at least one model in the chain"
    for entry in data["data"]:
        assert entry["object"] == "model"
        assert "id" in entry
        assert "owned_by" in entry


# --- Chat completions — shape + auth + error paths -----------------------


def test_chat_completions_requires_auth(_daemon_reachable):
    """Without a bearer token (when auth is enabled), the facade must reject.

    Probes the daemon for its actual auth state (not the local TOML) so the
    test does the right thing whether run against the user's :9000 (gated)
    or a sandbox (typically un-gated)."""
    # Probe with an obviously-bad token; if the daemon doesn't gate, this
    # will succeed and we skip.
    probe = httpx.get(
        f"{DAEMON_URL}/v1/models",
        headers={"Authorization": "Bearer obviously-wrong-token"},
        timeout=5.0,
    )
    if probe.status_code == 200:
        pytest.skip(f"{DAEMON_URL} doesn't gate /v1 — auth disabled on this daemon")

    r = httpx.post(
        f"{DAEMON_URL}/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        timeout=5.0,
    )
    assert r.status_code in (401, 403), f"expected auth rejection, got {r.status_code}"


def test_chat_completions_rejects_invalid_body(_daemon_reachable):
    """Missing or malformed messages array → 400 with OpenAI-shaped error."""
    r = httpx.post(
        f"{DAEMON_URL}/v1/chat/completions",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        json={"model": "anything"},  # no messages
        timeout=5.0,
    )
    assert r.status_code == 400
    body = r.json()
    assert "error" in body
    assert body["error"]["type"] == "invalid_request"


def test_chat_completions_rejects_streaming_request(_daemon_reachable):
    """We don't implement SSE yet — should refuse explicitly, not silently
    drop the stream flag."""
    r = httpx.post(
        f"{DAEMON_URL}/v1/chat/completions",
        headers=_auth_headers(),
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        timeout=5.0,
    )
    assert r.status_code == 501
    body = r.json()
    assert body["error"]["type"] == "unsupported"


def test_chat_completions_happy_path_shape(_daemon_reachable):
    """A real completion call returns OpenAI Chat Completions shape.

    Doesn't assert on text content (depends on which provider answers, which
    depends on the chain) — only on the wire shape. Marked llm so it can be
    excluded from non-LLM CI runs.
    """
    r = httpx.post(
        f"{DAEMON_URL}/v1/chat/completions",
        headers=_auth_headers(),
        json={
            "model": "openai/eos-think",
            "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
            "temperature": 0.1,
        },
        timeout=120.0,  # claude-cli first responses can be slow
    )
    # 503 acceptable when no provider is available (e.g. think chain only
    # has human and we're in daemon mode). Otherwise must succeed.
    if r.status_code == 503:
        pytest.skip(f"no think provider available right now: {r.json()}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    assert body["model"] == "openai/eos-think"
    assert isinstance(body["choices"], list) and body["choices"]
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    assert isinstance(choice["message"]["content"], str)
    assert choice["finish_reason"] == "stop"
    assert "usage" in body
    # EmptyOS extension fields are present
    assert "_eos_provider" in body
    assert "_eos_is_cloud" in body


# --- Wrapper script ------------------------------------------------------


def test_wrapper_check_reports_facade_state():
    """`python scripts/run_codeboarding.py --check` must run cleanly and
    report all three states (install, bridge, facade)."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_codeboarding.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode in (0, 1), f"unexpected exit {result.returncode}; stderr={result.stderr}"
    out = result.stdout
    # The check should mention the three concerns we care about
    assert "codeboarding" in out.lower() or "install" in out.lower()
    assert "bridge" in out.lower() or "config" in out.lower()
    assert "facade" in out.lower()


def test_wrapper_setup_writes_bridge_config(tmp_path, monkeypatch):
    """`--setup` writes a config file pointing at the facade with the
    auth_token. Uses HOME override so the test doesn't clobber the user's
    real config."""
    import subprocess
    import sys

    if not _auth_token():
        pytest.skip("no auth_token in emptyos.toml — can't test bridge write")

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)  # Windows uses USERPROFILE

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_codeboarding.py"), "--setup"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert result.returncode == 0, f"setup failed: {result.stdout}\n{result.stderr}"
    cfg = fake_home / ".codeboarding" / "config.toml"
    assert cfg.exists()
    content = cfg.read_text(encoding="utf-8")
    assert "api_base = " in content
    assert "/v1" in content
    assert _auth_token() in content
