"""Safety pin: the Ollama-Cloud GLM think provider must classify as CLOUD and
must require its API key to be 'available' — so the cloud-consent gate (Rule
18/19) always fires and it can never silently run keyless.

The privacy trap this guards against: reaching a `:cloud` model through a LOCAL
ollama host (localhost:11434) would classify as local and bypass the consent
gate while data still leaves the machine. These tests assert the *safe* wiring
(host = ollama.com) is cloud-gated, and that the unsafe wiring would NOT be.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities.consent import host_is_local
from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider


def _glm() -> OpenAICompatThinkProvider:
    # Mirrors what setup.py's generic-host branch builds from
    # [capabilities.think.glm].
    return OpenAICompatThinkProvider(
        host="https://ollama.com",
        model="glm-5.2",
        api_key_env="OLLAMA_API_KEY",
        provider_name="glm",
    )


def test_glm_host_is_cloud():
    """ollama.com is a public host → is_cloud True → consent gate eligible."""
    assert host_is_local("https://ollama.com") is False
    assert _glm().is_cloud is True


def test_localhost_cloud_tag_is_the_trap():
    """Reaching a :cloud model via localhost would (wrongly) read as local —
    documents exactly why the config must point at ollama.com, not 11434."""
    trap = OpenAICompatThinkProvider(
        host="http://localhost:11434", model="glm-5.2:cloud", provider_name="glm-trap"
    )
    assert host_is_local("http://localhost:11434") is True
    assert trap.is_cloud is False  # ← would bypass the consent gate; never wire this


@pytest.mark.asyncio
async def test_glm_unavailable_without_key(monkeypatch):
    """No key → not available → chain skips it (never runs keyless)."""
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert await _glm().available() is False


@pytest.mark.asyncio
async def test_glm_available_with_key(monkeypatch):
    """Key present → available without an unauthenticated /v1/models probe."""
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key-123")
    p = _glm()
    assert await p.available() is True
    assert p.auth_mode == "api-key"  # model pill shows it as a keyed cloud


@pytest.mark.asyncio
async def test_glm_health_names_missing_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    h = await _glm().health()
    assert h["available"] is False
    assert "OLLAMA_API_KEY" in (h["reason"] or "")
    assert (h["recovery"] or {}).get("name") == "OLLAMA_API_KEY"


def test_glm_ability_override():
    """ability='strong' is honored (set by setup._build_think_provider)."""
    p = _glm()
    p._ability = "strong"
    assert p.ability == "strong"
