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
# Hard spend cap — runs before policy, holds even under always-allow
# ---------------------------------------------------------------------------

class TestSpendGuard:
    @pytest.mark.asyncio
    async def test_guard_blocks_even_under_always(self):
        cm = CloudConsentManager(policy="always")

        async def guard(provider, capability):
            return "cap reached"

        cm.set_spend_guard(guard)
        assert await cm.ensure_consent(provider="openai", capability="think") is False
        assert cm.status()["last_decisions"]["openai"]["reason"].startswith("spend_cap:")

    @pytest.mark.asyncio
    async def test_guard_allows_when_under_cap(self):
        cm = CloudConsentManager(policy="always")

        async def guard(provider, capability):
            return None

        cm.set_spend_guard(guard)
        assert await cm.ensure_consent(provider="openai", capability="think") is True

    @pytest.mark.asyncio
    async def test_guard_blocks_session_approved_provider(self):
        cm = CloudConsentManager(policy="ask")
        cm.approve_provider("openai")  # would otherwise pass silently

        async def guard(provider, capability):
            return "cap reached"

        cm.set_spend_guard(guard)
        assert await cm.ensure_consent(provider="openai", capability="think", timeout=1) is False

    @pytest.mark.asyncio
    async def test_guard_emits_spend_blocked_event(self):
        bus = _RecordingBus()
        cm = CloudConsentManager(policy="always", events=bus)

        async def guard(provider, capability):
            return "daily cloud spend cap reached ($1.0000 / $1.00)"

        cm.set_spend_guard(guard)
        await cm.ensure_consent(provider="openai", capability="think")
        assert len(bus.emitted) == 1
        name, payload = bus.emitted[0]
        assert name == "cloud:spend_blocked"
        assert payload["provider"] == "openai"
        assert "cap reached" in payload["reason"]

    @pytest.mark.asyncio
    async def test_guard_exception_fails_open(self):
        cm = CloudConsentManager(policy="always")

        async def guard(provider, capability):
            raise RuntimeError("billing DB locked")

        cm.set_spend_guard(guard)
        # A guard fault must never wedge cloud work.
        assert await cm.ensure_consent(provider="openai", capability="think") is True

    @pytest.mark.asyncio
    async def test_no_guard_is_unaffected(self):
        cm = CloudConsentManager(policy="always")
        assert await cm.ensure_consent(provider="openai", capability="think") is True


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


# ---------------------------------------------------------------------------
# Fail-closed behaviour when the consent-request emit itself breaks.
#
# The `cloud:consent_requested` emit *is* what raises the modal. If it throws,
# nothing can ever resolve the pending future, so the old `except: pass` burned
# the full 120s timeout and then denied with a misleading reason="timeout".
# ---------------------------------------------------------------------------

class _BrokenBus:
    """Emits fine for everything except the consent request."""

    def __init__(self, fail_on="cloud:consent_requested"):
        self.fail_on = fail_on
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, name, payload, source: str = ""):
        if name == self.fail_on:
            raise RuntimeError("bus down")
        self.emitted.append((name, payload))


class TestConsentEmitFailsClosed:
    @pytest.mark.asyncio
    async def test_denies_immediately_without_waiting_for_timeout(self):
        cm = CloudConsentManager(policy="ask", events=_BrokenBus())
        allowed = await asyncio.wait_for(
            cm.ensure_consent(provider="openai", capability="think"),
            timeout=5,  # would blow past this if it fell through to DEFAULT_TIMEOUT
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_reason_is_emit_failure_not_timeout(self):
        cm = CloudConsentManager(policy="ask", events=_BrokenBus())
        await cm.ensure_consent(provider="openai", capability="think")
        assert cm.status()["last_decisions"]["openai"]["reason"] == "consent_emit_failed"

    @pytest.mark.asyncio
    async def test_pending_request_is_not_leaked(self):
        cm = CloudConsentManager(policy="ask", events=_BrokenBus())
        await cm.ensure_consent(provider="openai", capability="think")
        assert cm.status()["pending"] == []

    @pytest.mark.asyncio
    async def test_advisory_emit_failure_still_allows_the_call(self):
        """A broken scan-findings emit is advisory only — must not block."""
        cm = CloudConsentManager(policy="always", events=_BrokenBus("cloud:scan_findings"))
        findings = [{"pattern": "x", "preview": "y"}]
        allowed = await cm.ensure_consent(
            provider="openai", capability="think", findings=findings,
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_broken_spend_guard_fails_open(self):
        """Billing hiccup must not block all cloud work (documented contract)."""
        async def boom(provider, capability):
            raise RuntimeError("billing down")

        cm = CloudConsentManager(policy="always")
        cm.set_spend_guard(boom)
        assert await cm.ensure_consent(provider="openai", capability="think") is True
