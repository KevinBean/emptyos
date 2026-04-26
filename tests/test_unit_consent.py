"""Unit tests for emptyos.capabilities.consent.CloudConsentManager.

Pure in-process — no daemon required. Covers policy modes, approve/deny,
session cache behavior, host_is_local heuristics.
"""

from __future__ import annotations

import asyncio

import pytest

from emptyos.capabilities.consent import CloudConsentManager, host_is_local


# ---------------------------------------------------------------------------
# host_is_local — provider auto-classification
# ---------------------------------------------------------------------------

class TestHostIsLocal:
    @pytest.mark.parametrize("host", [
        "", "localhost", "127.0.0.1", "::1", "0.0.0.0",
        "http://localhost:9000", "http://127.0.0.1:11434",
        "10.0.0.5", "192.168.1.10", "172.16.5.5",
        "169.254.1.1",
        "100.64.10.20",  # Tailscale CGNAT
        "myserver.local", "thing.localhost",
        "node.ts.net", "node.tailscale.net",
        "router.lan", "host.home.arpa",
    ])
    def test_local_hosts(self, host):
        assert host_is_local(host) is True, f"{host!r} should be local"

    @pytest.mark.parametrize("host", [
        "api.openai.com",
        "api.anthropic.com",
        "https://generativelanguage.googleapis.com",
        "8.8.8.8",
        "1.1.1.1",
        "example.com",
    ])
    def test_cloud_hosts(self, host):
        assert host_is_local(host) is False, f"{host!r} should be cloud"


# ---------------------------------------------------------------------------
# Policy modes
# ---------------------------------------------------------------------------

class TestPolicyModes:
    def test_default_policy_is_ask(self):
        cm = CloudConsentManager()
        assert cm.policy == "ask"

    def test_invalid_policy_falls_back_to_ask(self):
        cm = CloudConsentManager(policy="bogus")
        assert cm.policy == "ask"

    def test_set_policy_validates(self):
        cm = CloudConsentManager()
        cm.set_policy("always")
        assert cm.policy == "always"
        cm.set_policy("never")
        assert cm.policy == "never"
        cm.set_policy("garbage")  # rejected silently
        assert cm.policy == "never"

    @pytest.mark.asyncio
    async def test_always_policy_auto_approves(self):
        cm = CloudConsentManager(policy="always")
        assert await cm.ensure_consent(provider="openai", capability="think") is True

    @pytest.mark.asyncio
    async def test_never_policy_auto_denies(self):
        cm = CloudConsentManager(policy="never")
        assert await cm.ensure_consent(provider="openai", capability="think") is False


# ---------------------------------------------------------------------------
# Auto-approve paths still surface LLM scanner findings as an event
# ---------------------------------------------------------------------------

class _RecordingBus:
    """Minimal stand-in for EventBus that captures emits into a list."""
    def __init__(self):
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, name, payload, source: str = ""):
        self.emitted.append((name, payload))


class TestScanFindingsEvent:
    @pytest.mark.asyncio
    async def test_always_policy_emits_findings_when_present(self):
        bus = _RecordingBus()
        cm = CloudConsentManager(policy="always", events=bus)
        findings = [{"pattern": "Local LLM classifier", "preview": "home address"}]
        assert await cm.ensure_consent(
            provider="openai", capability="think", findings=findings,
        ) is True
        assert len(bus.emitted) == 1
        name, payload = bus.emitted[0]
        assert name == "cloud:scan_findings"
        assert payload["provider"] == "openai"
        assert payload["policy_reason"] == "always"
        assert payload["findings"] == findings

    @pytest.mark.asyncio
    async def test_always_policy_no_event_when_findings_empty(self):
        bus = _RecordingBus()
        cm = CloudConsentManager(policy="always", events=bus)
        await cm.ensure_consent(provider="openai", capability="think", findings=[])
        assert bus.emitted == []

    @pytest.mark.asyncio
    async def test_session_approved_emits_findings(self):
        bus = _RecordingBus()
        cm = CloudConsentManager(policy="ask", events=bus)
        cm.approve_provider("openai")  # prime the session cache
        findings = [{"pattern": "regex", "preview": "***"}]
        assert await cm.ensure_consent(
            provider="openai", capability="think", findings=findings,
        ) is True
        assert len(bus.emitted) == 1
        name, payload = bus.emitted[0]
        assert name == "cloud:scan_findings"
        assert payload["policy_reason"] == "session_approved"

    @pytest.mark.asyncio
    async def test_never_policy_does_not_emit_findings(self):
        # When the call is blocked, a "you're leaking X" toast would mislead.
        bus = _RecordingBus()
        cm = CloudConsentManager(policy="never", events=bus)
        findings = [{"pattern": "x", "preview": "y"}]
        await cm.ensure_consent(
            provider="openai", capability="think", findings=findings,
        )
        assert bus.emitted == []


# ---------------------------------------------------------------------------
# Ask policy — approval flow
# ---------------------------------------------------------------------------

async def _wait_for_pending(cm: CloudConsentManager, expected: int = 1, ticks: int = 50) -> None:
    for _ in range(ticks):
        if len(cm.pending_list()) >= expected:
            return
        await asyncio.sleep(0.01)


class TestAskFlow:
    @pytest.mark.asyncio
    async def test_approve_resolves_pending_request(self):
        cm = CloudConsentManager(policy="ask")
        task = asyncio.create_task(
            cm.ensure_consent(provider="openai", capability="think", timeout=5)
        )
        await _wait_for_pending(cm)
        pending = cm.pending_list()
        assert len(pending) == 1
        assert pending[0]["provider"] == "openai"
        assert cm.approve(pending[0]["id"]) is True
        assert await task is True

    @pytest.mark.asyncio
    async def test_deny_resolves_pending_request(self):
        cm = CloudConsentManager(policy="ask")
        task = asyncio.create_task(
            cm.ensure_consent(provider="anthropic", capability="think", timeout=5)
        )
        await _wait_for_pending(cm)
        assert cm.deny(cm.pending_list()[0]["id"]) is True
        assert await task is False

    @pytest.mark.asyncio
    async def test_remembered_approval_skips_subsequent_prompts(self):
        cm = CloudConsentManager(policy="ask")
        task = asyncio.create_task(
            cm.ensure_consent(provider="openai", capability="think", timeout=5)
        )
        await _wait_for_pending(cm)
        cm.approve(cm.pending_list()[0]["id"], remember=True)
        await task

        # Second call — should auto-approve without a pending request
        second = await cm.ensure_consent(provider="openai", capability="think", timeout=1)
        assert second is True
        assert cm.pending_list() == []

    @pytest.mark.asyncio
    async def test_non_remembered_approval_reprompts(self):
        cm = CloudConsentManager(policy="ask")
        task = asyncio.create_task(
            cm.ensure_consent(provider="openai", capability="think", timeout=5)
        )
        await _wait_for_pending(cm)
        cm.approve(cm.pending_list()[0]["id"], remember=False)
        await task

        # Second call must produce a new pending request
        task2 = asyncio.create_task(
            cm.ensure_consent(provider="openai", capability="think", timeout=5)
        )
        await _wait_for_pending(cm)
        assert len(cm.pending_list()) == 1
        cm.deny(cm.pending_list()[0]["id"])
        await task2

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self):
        cm = CloudConsentManager(policy="ask")
        result = await cm.ensure_consent(provider="openai", capability="think", timeout=0.05)
        assert result is False


# ---------------------------------------------------------------------------
# Session cache management
# ---------------------------------------------------------------------------

class TestSessionCache:
    @pytest.mark.asyncio
    async def test_approve_provider_pre_authorizes(self):
        cm = CloudConsentManager(policy="ask")
        cm.approve_provider("openai")
        assert cm.would_allow_silently("openai") is True
        result = await cm.ensure_consent(provider="openai", capability="think", timeout=1)
        assert result is True

    def test_revoke_provider_re_prompts(self):
        cm = CloudConsentManager(policy="ask")
        cm.approve_provider("openai")
        cm.revoke_provider("openai")
        assert cm.would_allow_silently("openai") is False

    def test_reset_approvals_clears_cache(self):
        cm = CloudConsentManager(policy="ask")
        cm.approve_provider("openai")
        cm.approve_provider("anthropic")
        cm.reset_approvals()
        assert cm.would_allow_silently("openai") is False
        assert cm.would_allow_silently("anthropic") is False

    def test_would_allow_silently_respects_policy(self):
        cm = CloudConsentManager(policy="always")
        assert cm.would_allow_silently("anyprovider") is True
        cm.set_policy("never")
        assert cm.would_allow_silently("anyprovider") is False


# ---------------------------------------------------------------------------
# approve/deny edge cases + status payload
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_approve_unknown_id_returns_false(self):
        cm = CloudConsentManager()
        assert cm.approve("does-not-exist") is False

    def test_deny_unknown_id_returns_false(self):
        cm = CloudConsentManager()
        assert cm.deny("does-not-exist") is False

    def test_status_returns_expected_shape(self):
        cm = CloudConsentManager(policy="ask")
        cm.approve_provider("openai")
        s = cm.status()
        assert s["policy"] == "ask"
        assert "openai" in s["approved"]
        assert isinstance(s["pending"], list)
        assert isinstance(s["last_decisions"], dict)
