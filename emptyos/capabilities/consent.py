"""Cloud consent manager — gate for cloud providers in capability chain.

Policy:
  "ask"    — prompt on first call per provider per session (default)
  "always" — auto-approve all cloud providers
  "never"  — deny all cloud providers (skip, fall through to local/human)

Approvals are session-scoped: they live in memory and reset on daemon restart.
The UI (via EventBus) surfaces pending requests as modals.
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from emptyos.kernel.event_bus import EventBus


_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")

_LOOPBACK_LITERALS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")
_LOOPBACK_IN_URL = ("://localhost", "://127.0.0.1", "://::1", "://[::1]", "://0.0.0.0")


def host_is_loopback(host: str) -> bool:
    """True only for *this machine* — never for private-LAN or tailnet hosts.

    Narrower than :func:`host_is_local`, which also returns True for private
    ranges, Tailscale CGNAT and ``*.ts.net``. That breadth is right for consent
    (your LAN is not the public internet) but hides the case that matters for
    **trust**: a rented GPU joined to your tailnet is indistinguishable from
    your own desktop by address alone.

    Callers that must separate "definitely mine" from "looks local but could be
    someone else's box" use this; see ``.claude/rules/rented-compute.md`` and
    ``scripts/check_provider_trust.py``.
    """
    if not host:
        return True
    low = host.strip().lower()
    if low in _LOOPBACK_LITERALS:
        return True
    return any(lit in low for lit in _LOOPBACK_IN_URL)


def host_is_local(host: str) -> bool:
    """Return True for localhost / loopback / private-network hosts.

    Used to auto-classify providers as local vs cloud. A host is considered
    local when:
      - empty / missing
      - literally 'localhost', '127.0.0.1', '::1', '0.0.0.0'
      - inside a private IPv4 range (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
      - inside link-local (169.254.0.0/16) or CGNAT (100.64.0.0/10 — used by
        Tailscale for tailnet addresses)
      - *.ts.net or *.tailscale.net (Tailscale MagicDNS)
      - *.local (mDNS)
    """
    if not host:
        return True

    # Fast path — recognize common loopback literals even when urlparse would
    # choke on them (e.g. "http://::1" without IPv6 brackets).
    if host_is_loopback(host):
        return True
    low = host.strip().lower()

    try:
        parsed = urlparse(host) if "://" in host else urlparse(f"http://{host}")
        hostname = (parsed.hostname or host).strip().lower()
    except Exception:
        hostname = low

    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return True
        # Tailscale CGNAT (not flagged as private by Python)
        if isinstance(ip, ipaddress.IPv4Address) and ip in _TAILSCALE_CGNAT:
            return True
        return False
    except ValueError:
        pass

    # Hostname heuristics — trusted private suffixes
    if hostname.endswith(".local") or hostname.endswith(".localhost"):
        return True
    if hostname.endswith(".ts.net") or hostname.endswith(".tailscale.net"):
        return True
    if hostname.endswith(".lan") or hostname.endswith(".home.arpa"):
        return True

    # Single-segment hostnames (no dots, not an IP) cannot be public DNS —
    # public domains require at least one TLD dot. They're Docker compose
    # service names ("ollama", "redis"), /etc/hosts aliases, NetBIOS names,
    # or short hostnames added via container --network. All are local.
    if "." not in hostname and ":" not in hostname:
        return True

    # Default: treat unknown hostnames as cloud
    return False


@dataclass
class _PendingRequest:
    id: str
    provider: str
    capability: str
    data_summary: str
    future: asyncio.Future
    created_at: float
    findings: list[dict] = field(default_factory=list)


class CloudConsentManager:
    """Decides whether a cloud provider call may proceed.

    One instance per kernel. Capabilities check with it before invoking a
    cloud provider. In "ask" mode, the manager emits a
    `cloud:consent_requested` event and awaits an approval or denial via
    `approve()` / `deny()` (called from the HTTP consent endpoint).
    """

    # How long to wait for an approval before giving up (and denying).
    DEFAULT_TIMEOUT_SECONDS = 120

    def __init__(self, policy: str = "ask", events: EventBus | None = None, kernel: Any = None):
        self.policy = policy if policy in ("ask", "always", "never") else "ask"
        self.events = events
        # Kernel ref — only used for the optional local-LLM scan path
        # (reads settings + picks a local think provider). Safe to leave None.
        self.kernel = kernel
        # Provider names approved for this session
        self._session_approved: set[str] = set()
        # In-flight consent requests by id
        self._pending: dict[str, _PendingRequest] = {}
        # Most recent decision per provider (for UI display only)
        self._last_decision: dict[str, dict[str, Any]] = {}
        # Optional hard spend cap. Returns a non-empty reason string to BLOCK
        # the call, or "" / None to allow. Runs *before* the policy check, so a
        # hard cap holds even under policy="always". Filled by the billing app.
        self._spend_guard: Callable[[str, str], Awaitable[str | None]] | None = None

    def _log(self, level: str, message: str, **kwargs):
        """Best-effort syslog write via the optional kernel ref.

        The consent gate is a security boundary — a swallowed failure here used
        to be invisible. Logging must itself never raise (a broken logger can't
        be allowed to break the gate), hence the guard.
        """
        try:
            syslog = getattr(self.kernel, "syslog", None) if self.kernel else None
            if syslog:
                getattr(syslog, level)("cloud_consent", message, **kwargs)
        except Exception:
            pass

    def set_spend_guard(self, fn: Callable[[str, str], Awaitable[str | None]] | None):
        """Register a pre-flight spend cap. The guard is awaited before every
        cloud call; a truthy return value blocks it (the reason is surfaced).

        Fail-open by contract: the guard itself must swallow its own errors and
        return None on failure, so a billing hiccup never blocks all cloud work.
        """
        self._spend_guard = fn

    def set_policy(self, policy: str):
        if policy in ("ask", "always", "never"):
            self.policy = policy

    def reset_approvals(self):
        """Clear the session cache — next cloud call will re-prompt (in 'ask' mode)."""
        self._session_approved.clear()

    async def _emit_scan_findings(
        self,
        provider: str,
        capability: str,
        findings: list[dict] | None,
        policy_reason: str,
    ):
        """Advisory event for auto-approved cloud calls that still produced findings.

        Fires when the consent gate would have shown findings in a modal but the
        policy skipped the modal (always-allow / session-approved). Payload
        intentionally omits `data_summary` to keep vault content out of the
        event log — the toast only needs pattern names + short previews.
        """
        if not findings or not self.events:
            return
        try:
            await self.events.emit(
                "cloud:scan_findings",
                {
                    "provider": provider,
                    "capability": capability,
                    "findings": findings,
                    "policy_reason": policy_reason,
                },
                source="cloud_consent",
            )
        except Exception as e:
            # Advisory toast only — the call still proceeds. Log so a
            # permanently broken emit path is visible.
            self._log("warn", f"scan-findings emit failed for {provider}: {e}")

    async def ensure_consent(
        self,
        *,
        provider: str,
        capability: str,
        data_summary: str = "",
        findings: list[dict] | None = None,
        timeout: float | None = None,
    ) -> bool:
        """Return True if this cloud call may proceed, False otherwise.

        Never raises — callers should treat False as "skip this provider"
        rather than "fail the capability."
        """
        # Hard spend cap runs first — a ceiling the user set must hold even
        # under policy="always" / session-approved. The guard is fail-open.
        if self._spend_guard is not None:
            try:
                block_reason = await self._spend_guard(provider, capability)
            except Exception as e:
                # Fail-open by contract (a billing hiccup must not block all
                # cloud work) — but a guard that *always* throws would silently
                # disable the spend cap forever, so make it visible.
                block_reason = None
                self._log("warn", f"spend guard failed for {provider}, allowing: {e}")
            if block_reason:
                self._last_decision[provider] = {
                    "decision": "denied",
                    "reason": f"spend_cap: {block_reason}",
                    "at": time.time(),
                }
                if self.events:
                    try:
                        await self.events.emit(
                            "cloud:spend_blocked",
                            {
                                "provider": provider,
                                "capability": capability,
                                "reason": block_reason,
                            },
                            source="cloud_consent",
                        )
                    except Exception as e:
                        # The block itself still holds (we return False below);
                        # only the user-facing toast is lost.
                        self._log("warn", f"spend-blocked emit failed for {provider}: {e}")
                return False

        if self.policy == "never":
            self._last_decision[provider] = {
                "decision": "denied",
                "reason": "policy=never",
                "at": time.time(),
            }
            return False
        if self.policy == "always":
            await self._emit_scan_findings(provider, capability, findings, "always")
            return True
        # policy == "ask"
        if provider in self._session_approved:
            await self._emit_scan_findings(provider, capability, findings, "session_approved")
            return True

        # Emit consent request and wait for a decision
        req_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        pending = _PendingRequest(
            id=req_id,
            provider=provider,
            capability=capability,
            data_summary=data_summary or "",
            future=future,
            created_at=time.time(),
            findings=findings or [],
        )
        self._pending[req_id] = pending

        if self.events:
            try:
                await self.events.emit(
                    "cloud:consent_requested",
                    {
                        "id": req_id,
                        "provider": provider,
                        "capability": capability,
                        "data_summary": data_summary,
                        "findings": findings or [],
                    },
                    source="cloud_consent",
                )
            except Exception as e:
                # Load-bearing: this emit *is* what raises the consent modal.
                # If it fails nobody can ever resolve `future`, so waiting would
                # burn the full timeout and then deny with a misleading
                # "timeout" reason. Deny now, fail-closed, and say why.
                self._pending.pop(req_id, None)
                self._log("error", f"consent-request emit failed for {provider}, denying: {e}")
                self._last_decision[provider] = {
                    "decision": "denied",
                    "reason": "consent_emit_failed",
                    "at": time.time(),
                }
                return False

        try:
            result = await asyncio.wait_for(
                future,
                timeout=timeout or self.DEFAULT_TIMEOUT_SECONDS,
            )
            return bool(result)
        except TimeoutError:
            self._last_decision[provider] = {
                "decision": "denied",
                "reason": "timeout",
                "at": time.time(),
            }
            return False
        finally:
            self._pending.pop(req_id, None)

    def approve(self, request_id: str, *, remember: bool = True) -> bool:
        """Approve a pending request by id. Returns True if found."""
        pending = self._pending.get(request_id)
        if not pending:
            return False
        if remember:
            self._session_approved.add(pending.provider)
        self._last_decision[pending.provider] = {
            "decision": "approved",
            "remember": remember,
            "at": time.time(),
        }
        if not pending.future.done():
            pending.future.set_result(True)
        return True

    def deny(self, request_id: str) -> bool:
        """Deny a pending request by id. Returns True if found."""
        pending = self._pending.get(request_id)
        if not pending:
            return False
        self._last_decision[pending.provider] = {
            "decision": "denied",
            "at": time.time(),
        }
        if not pending.future.done():
            pending.future.set_result(False)
        return True

    def approve_provider(self, provider: str):
        """Pre-approve a provider for the session (no pending request needed)."""
        self._session_approved.add(provider)

    def revoke_provider(self, provider: str):
        self._session_approved.discard(provider)

    def would_allow_silently(self, provider: str) -> bool:
        """True when `ensure_consent` would proceed without prompting.

        Use from callers that must not block on user interaction (benchmarks,
        scheduled jobs, batch comparisons). Returns False when a modal would
        fire or the policy is `never`.
        """
        if self.policy == "always":
            return True
        if self.policy == "never":
            return False
        return provider in self._session_approved

    def pending_list(self) -> list[dict]:
        return [
            {
                "id": p.id,
                "provider": p.provider,
                "capability": p.capability,
                "data_summary": p.data_summary,
                "findings": p.findings,
                "created_at": p.created_at,
            }
            for p in self._pending.values()
        ]

    def status(self) -> dict:
        return {
            "policy": self.policy,
            "approved": sorted(self._session_approved),
            "pending": self.pending_list(),
            "last_decisions": dict(self._last_decision),
        }
