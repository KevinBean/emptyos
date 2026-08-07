"""Capabilities — abstract operations that humans or tools can fulfill."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.capabilities.consent import CloudConsentManager


@dataclass
class Result:
    """Result of a capability execution."""

    value: Any
    provider: str  # which provider fulfilled this
    is_cloud: bool = False  # True when this value came from a cloud provider
    under_powered: bool = False  # chosen model is below the requested min_ability


class Provider:
    """One way to fulfill a capability. Subclass and implement.

    Providers with a `host` attribute pointing at a non-localhost/private-IP
    address are automatically classified as cloud. Override `is_cloud` to
    force a classification.
    """

    name: str = "base"
    capacity: int = 0  # 0 = unlimited
    _current_load: int = 0
    host: str = ""  # optional — if set, used by default is_cloud detection
    #: Declared trust domain: ``owned`` | ``rented`` | ``service``. Empty means
    #: undeclared, and ``is_cloud`` falls back to inferring from ``host``.
    #: Declare it for any compute that is not on hardware you own — address
    #: inference cannot tell a rented GPU from your own machine.
    #: See ``.claude/rules/rented-compute.md``.
    trust: str = ""

    @property
    def current_load(self) -> int:
        return self._current_load

    @property
    def at_capacity(self) -> bool:
        return self.capacity > 0 and self._current_load >= self.capacity

    @property
    def is_cloud(self) -> bool:
        """True when this provider's bytes leave hardware you own.

        Resolution order:

        1. **Declared ``trust``** wins. ``owned`` → not cloud; anything else
           (``rented`` / ``service`` / an unrecognised value) → cloud.
        2. Otherwise infer from ``host`` via ``host_is_local``.

        The declaration exists because address inference cannot see the case
        that matters most: a *rented* GPU. ``host_is_local`` treats private
        ranges, Tailscale CGNAT (100.64/10) and ``*.ts.net`` as local — all
        correct for your own machines, and all wrong for someone else's box
        that you happen to have tunnelled to. A provider reaching hardware you
        do not own must declare ``trust``; the network address cannot tell.

        An unrecognised ``trust`` value fails **closed** (treated as cloud) so
        a typo tightens the gate rather than opening it.

        Override this property outright for providers that wrap a remote
        service through a local proxy, or vice versa.
        """
        from emptyos.capabilities.consent import host_is_local

        declared = (getattr(self, "trust", "") or "").strip().lower()
        if declared:
            return declared != "owned"

        host = getattr(self, "host", "") or ""
        return bool(host) and not host_is_local(host)

    @property
    def ability(self) -> str:
        """Intrinsic model strength: ``weak`` | ``standard`` | ``strong``.

        Orthogonal to cost. Operators override per provider by setting
        ``self._ability`` (from ``[capabilities.think.<provider>] ability``);
        otherwise classified heuristically from name + model. Used to gate
        features and route ``think(min_ability=...)``. See
        ``emptyos/capabilities/ability.py``.
        """
        from emptyos.capabilities.ability import classify, normalize

        override = getattr(self, "_ability", None)
        if override:
            return normalize(override)
        return classify(self.name, getattr(self, "model", "") or "")

    @property
    def auth_mode(self) -> str:
        """How this provider authenticates — the input-side mirror of
        ``is_cloud`` (which says *where bytes flow*). Surfaced in the model
        pill + ``/system`` so the user sees who pays / how it connects:

          ``login``   — a local CLI / session credential (e.g. claude-cli on a
                        Max subscription); no API key changes hands here
          ``api-key`` — a server-side env API key
          ``byok``    — a per-request user-supplied key (BYOK header)
          ``local``   — a local host that needs no credential (ollama, lm-studio)
          ``none``    — a cloud host with no key configured (calls will fail)

        Default infers from ``is_cloud``: local→``local``, cloud→``api-key``.
        Override for richer auth (claude_cli→``login``;
        openai_compat→``byok``/``none``). Borrowed from OpenGauss surfacing
        backend auth mode as visible configuration, not hidden behind one
        generic provider name.
        """
        return "api-key" if self.is_cloud else "local"

    def consent_summary(self, **kwargs) -> str:
        """Human-readable summary of what will be sent to this provider.

        Used by the consent UX modal. Override for richer summaries.
        Returns the first string-ish kwarg in full — the UI handles
        scrolling so the user can verify the exact payload.
        """
        for key in ("prompt", "text", "query", "input"):
            val = kwargs.get(key)
            if isinstance(val, str) and val:
                return val
        return ""

    @property
    def variant_id(self) -> str:
        """Unique identity for benchmarking — name + behavior-affecting attrs.

        Two providers with the same `name` but different `model` or `mode`
        produce different outputs, so they must rank as separate variants.
        Default concatenates `name`, `model`, and `mode` (or `effort`) if set.
        Override for providers with unusual identity semantics.
        """
        parts = [self.name]
        model = getattr(self, "model", "") or ""
        if model:
            parts.append(model)
        mode = getattr(self, "mode", "") or getattr(self, "effort", "") or ""
        if mode:
            parts.append(str(mode))
        return ":".join(parts)

    @property
    def variant_meta(self) -> dict:
        """Structured identity fields for storage and UI display."""
        return {
            "provider": self.name,
            "model": getattr(self, "model", "") or "",
            "mode": getattr(self, "mode", "") or getattr(self, "effort", "") or "",
            "variant": self.variant_id,
        }

    async def available(self) -> bool:
        """Can this provider work right now?"""
        return False

    async def health(self) -> dict:
        """Structured availability + recovery hint for the Capability Inspector.

        Default wraps `available()` with no recovery hint. Override to return:
            {
                "available": bool,
                "reason": str | None,        # human-readable why-not
                "recovery": dict | None,     # see kinds below
            }

        Recovery kinds (the inspector renders one button per kind):
            {"kind": "env_var", "name": "OPENAI_API_KEY", "doc": "..."}
            {"kind": "plugin", "id": "voice-api", "launcher": "..."}
            {"kind": "service", "id": "ollama", "url": "http://127.0.0.1:11434", "hint": "..."}
            {"kind": "config", "path": "emptyos.toml", "section": "[capabilities.think]"}
            {"kind": "consent", "provider": "openai_compat"}
        """
        return {"available": await self.available(), "reason": None, "recovery": None}

    async def execute(self, **kwargs) -> Any:
        """Do the work. Raise if it fails."""
        raise NotImplementedError

    async def execute_stream(self, **kwargs) -> AsyncGenerator[dict, None]:
        """Stream results as chunks: {"text": str, "done": bool}.

        Default: wraps execute() into a single chunk. Override for true streaming.
        """
        value = await self.execute(**kwargs)
        yield {"text": value, "done": True}


class Capability:
    """A thing the OS can do. Tries providers in order until one works.

    Supports domain routing: different provider chains for different use cases.
    e.g., think.text uses gpt-4.1-mini, think.code uses gpt-4.1

    Cloud providers pass through the consent gate (`consent_manager`) before
    being invoked. A denied or timed-out consent skips the provider and
    continues the chain.
    """

    name: str = "base"
    consent_manager: CloudConsentManager | None = None

    def __init__(self, providers: list[Provider] | None = None):
        self.providers: list[Provider] = providers or []
        self._domains: dict[str, list[Provider]] = {}  # domain -> provider chain
        self._buckets: dict[str, list[Provider]] = {}  # "domain/task_shape" -> chain

    def add_domain(self, domain: str, providers: list[Provider]):
        """Add a domain-specific provider chain."""
        self._domains[domain] = providers

    def add_bucket(self, bucket: str, providers: list[Provider]):
        """Add a bucket-specific chain. Bucket id is 'domain/task_shape'."""
        self._buckets[bucket] = providers

    @staticmethod
    def _bucket_id(domain: str | None, task_shape: str | None, bucket: str | None) -> str | None:
        if bucket:
            return bucket
        if domain and task_shape:
            return f"{domain}/{task_shape}"
        return None

    def all_providers(self, *, unique: bool = False) -> list[Provider]:
        """Every provider the capability knows: the default chain + every domain
        chain + every bucket chain, in that order.

        ``unique=False`` (default) preserves order and may repeat a provider that
        sits in more than one chain — fine for name lookups (first match wins).
        ``unique=True`` dedups by ``variant_id`` (first occurrence kept) — what
        the benchmark / "every distinct model" callers want.

        The single source of truth for "walk all provider chains"; callers used
        to inline ``list(self.providers) + _domains.values() + _buckets.values()``.
        """
        out = list(self.providers)
        for chain in self._domains.values():
            out.extend(chain)
        for chain in self._buckets.values():
            out.extend(chain)
        if not unique:
            return out
        seen: set = set()
        uniq: list[Provider] = []
        for p in out:
            vid = getattr(p, "variant_id", p.name)
            if vid in seen:
                continue
            seen.add(vid)
            uniq.append(p)
        return uniq

    def providers_for(
        self,
        domain: str | None = None,
        task_shape: str | None = None,
        bucket: str | None = None,
    ) -> list[Provider]:
        """Public: the resolved provider chain, in order, without probing health.

        Use this to *inspect* which providers a call would try (e.g. to pick a
        local-vs-cloud one). ``status()`` is the health-checked sibling — it
        probes every provider across every domain, so it is the wrong tool on a
        hot path. Neither replaces ``execute()``, which owns the actual routing,
        consent, and middleware.
        """
        return self._get_providers(domain, task_shape, bucket)

    def _get_providers(
        self,
        domain: str | None = None,
        task_shape: str | None = None,
        bucket: str | None = None,
    ) -> list[Provider]:
        """Resolve provider chain. Precedence: bucket → domain → default."""
        bid = self._bucket_id(domain, task_shape, bucket)
        if bid and bid in self._buckets:
            return self._buckets[bid]
        if domain and domain in self._domains:
            return self._domains[domain]
        return self.providers

    @staticmethod
    def _reorder_by_ability(
        providers: list[Provider], min_ability: str | None
    ) -> list[Provider]:
        """Prefer providers meeting ``min_ability`` without reshuffling the rest.

        Partition the chain into "meets the bar" and "doesn't", each preserving
        original (cost-optimised) chain order, meeting-group first. So a
        ``min_ability="strong"`` request tries the strong provider before a weak
        one; a ``"standard"`` request still prefers the cheap-but-sufficient
        provider the chain already ordered first. Soft — if none meets the bar,
        the original order is returned unchanged (best-effort fallback).
        """
        if not min_ability:
            return providers
        from emptyos.capabilities.ability import meets

        ok = [p for p in providers if meets(getattr(p, "ability", "standard"), min_ability)]
        if not ok:
            return providers
        rest = [p for p in providers if p not in ok]
        return ok + rest

    @staticmethod
    def _reorder_by_preference(
        providers: list[Provider], prefer: list[str] | str | None
    ) -> list[Provider]:
        """Move named providers to the front, in the order named, keeping the rest.

        A soft per-call routing hint (e.g. a device that can only play WAV asking
        for ``prefer_provider=["kokoro"]``). Names that match no provider are
        ignored; the chain is otherwise unchanged, so it never makes a call fail.
        """
        if not prefer:
            return providers
        names = [prefer] if isinstance(prefer, str) else list(prefer)
        rank = {n: i for i, n in enumerate(names)}
        head = sorted((p for p in providers if p.name in rank), key=lambda p: rank[p.name])
        rest = [p for p in providers if p.name not in rank]
        return head + rest

    def _llm_scan_settings(self) -> dict:
        """Read cloud.llm_scan.* from kernel settings. Returns neutral dict if unavailable."""
        kernel = getattr(self.consent_manager, "kernel", None) if self.consent_manager else None
        settings = getattr(kernel, "settings", None) if kernel else None
        if settings is None:
            return {
                "mode": "off",
                "on_flag": "warn",
                "provider": "",
                "max_chars": 4000,
                "timeout": 5.0,
            }
        mode = settings.get("cloud.llm_scan.mode", "off") or "off"
        if mode not in ("off", "classify", "redact"):
            mode = "off"
        on_flag = settings.get("cloud.llm_scan.on_flag", "warn") or "warn"
        if on_flag not in ("warn", "block"):
            on_flag = "warn"
        try:
            max_chars = int(settings.get("cloud.llm_scan.max_chars", 4000) or 4000)
        except (TypeError, ValueError):
            max_chars = 4000
        try:
            timeout = float(settings.get("cloud.llm_scan.timeout", 5.0) or 5.0)
        except (TypeError, ValueError):
            timeout = 5.0
        return {
            "mode": mode,
            "on_flag": on_flag,
            "provider": settings.get("cloud.llm_scan.provider", "") or "",
            "max_chars": max_chars,
            "timeout": timeout,
        }

    async def _consent_allows(self, provider: Provider, **kwargs) -> bool:
        """True when the consent gate allows this provider call.

        Local providers always pass. Cloud providers are gated by the consent
        manager; if no manager is registered, cloud calls are allowed (quiet
        backward-compat for tests and bare kernels).
        """
        if not getattr(provider, "is_cloud", False):
            return True
        if self.consent_manager is None:
            return True
        from emptyos.capabilities.outbound_scan import llm_classify, scan_outbound

        summary = provider.consent_summary(**kwargs)
        findings = [
            {"pattern": f.pattern_name, "preview": f.preview} for f in scan_outbound(summary)
        ]

        # Optional local-LLM classifier pass. In redact mode we still run the
        # classifier here so the user sees *what* was flagged — the actual
        # rewrite happens later in _preprocess_outbound_kwargs.
        scan_cfg = self._llm_scan_settings()
        if scan_cfg["mode"] in ("classify", "redact") and summary:
            kernel = getattr(self.consent_manager, "kernel", None)
            result = await llm_classify(
                summary,
                kernel,
                max_chars=scan_cfg["max_chars"],
                preferred_variant=scan_cfg["provider"],
                timeout=scan_cfg["timeout"],
            )
            if result.get("ran") and result.get("flagged"):
                findings.append(
                    {
                        "pattern": f"Local LLM classifier ({result.get('provider') or 'local'})",
                        "preview": result.get("reasons", "") or "flagged as sensitive",
                    }
                )
                if scan_cfg["on_flag"] == "block":
                    return False
            elif result.get("ran") is False and result.get("reasons"):
                # Surface classifier availability issues so the user knows the
                # scan didn't actually run — don't silently pretend it passed.
                findings.append(
                    {
                        "pattern": "Local LLM classifier — not run",
                        "preview": result.get("reasons", ""),
                    }
                )

        return await self.consent_manager.ensure_consent(
            provider=provider.name,
            capability=self.name,
            data_summary=summary,
            findings=findings,
        )

    async def _preprocess_outbound_kwargs(self, provider: Provider, kwargs: dict) -> dict:
        """If redact mode is on, rewrite text fields in kwargs via local LLM.

        Called immediately before a cloud provider's execute(). Returns a
        (possibly new) kwargs dict. Non-cloud providers pass through.
        """
        if not getattr(provider, "is_cloud", False):
            return kwargs
        if self.consent_manager is None:
            return kwargs
        scan_cfg = self._llm_scan_settings()
        if scan_cfg["mode"] != "redact":
            return kwargs

        from emptyos.capabilities.outbound_scan import llm_redact

        kernel = getattr(self.consent_manager, "kernel", None)
        new_kwargs = dict(kwargs)
        changed = False
        for key in ("prompt", "text", "query", "input", "system"):
            val = new_kwargs.get(key)
            if isinstance(val, str) and val.strip():
                result = await llm_redact(
                    val,
                    kernel,
                    max_chars=scan_cfg["max_chars"],
                    preferred_variant=scan_cfg["provider"],
                    timeout=scan_cfg["timeout"],
                )
                if result.get("ran") and result.get("redacted") and result["redacted"] != val:
                    new_kwargs[key] = result["redacted"]
                    changed = True
        return new_kwargs if changed else kwargs

    async def _provider_ready(self, provider: Provider, **kwargs) -> bool:
        """Provider is available AND the consent gate approves invocation."""
        if not await provider.available():
            return False
        return await self._consent_allows(provider, **kwargs)

    def _simulate_offline(self) -> bool:
        """True when this capability is set to simulate offline via settings.

        Setting: ``capability.simulate_offline`` — empty = off, ``"all"`` =
        every capability, comma-list = specific names (e.g. ``"think,draw"``).
        When on, ``execute()`` / ``execute_stream()`` raise as if no provider
        were available, without touching real providers. Lets ``think_safe``
        and other graceful-degradation paths be tested deterministically.
        """
        kernel = getattr(self.consent_manager, "kernel", None) if self.consent_manager else None
        settings = getattr(kernel, "settings", None) if kernel else None
        if settings is None:
            return False
        raw = str(settings.get("capability.simulate_offline", "") or "").strip().lower()
        if not raw:
            return False
        if raw == "all":
            return True
        names = {n.strip() for n in raw.split(",") if n.strip()}
        return self.name in names

    async def execute(
        self,
        *,
        domain: str | None = None,
        task_shape: str | None = None,
        bucket: str | None = None,
        min_ability: str | None = None,
        prefer_provider: list[str] | str | None = None,
        only_provider: str | None = None,
        **kwargs,
    ) -> Result:
        """Try each provider in order.

        Two passes: pass 1 skips providers already at capacity; pass 2 tries
        them anyway (queue behind busy ones). Within each pass we use the same
        available + consent + execute + fall-through-on-error logic.

        ``min_ability`` (weak|standard|strong) is a soft routing hint: providers
        meeting the bar are tried first (see ``_reorder_by_ability``). If the
        provider that ends up fulfilling the call is below the bar, the returned
        ``Result.under_powered`` is True so callers can flag degraded output.

        ``only_provider`` is a hard boundary: exactly that named provider is
        tried through the normal availability, consent, scan, and middleware
        path. No other provider is used when it is absent, denied, or fails.
        """
        if self._simulate_offline():
            raise RuntimeError(
                f"Capability '{self.name}' is set to simulate offline (capability.simulate_offline)"
            )
        from emptyos.capabilities.ability import meets

        providers = self._reorder_by_ability(
            self._get_providers(domain, task_shape, bucket), min_ability
        )
        if only_provider:
            providers = [p for p in providers if p.name == only_provider]
        if prefer_provider:
            providers = self._reorder_by_preference(providers, prefer_provider)
        last_error: Exception | None = None
        for pass_num in (1, 2):
            for provider in providers:
                if pass_num == 1 and provider.at_capacity:
                    continue
                if not await self._provider_ready(provider, **kwargs):
                    continue
                call_kwargs = await self._preprocess_outbound_kwargs(provider, kwargs)
                # Middleware chain runs AFTER redaction (preprocess) so PII
                # is scrubbed before compression/etc. — an empty chain is a
                # zero-overhead no-op. See emptyos/capabilities/middleware/.
                from emptyos.capabilities.middleware import run_chain  # noqa: PLC0415

                _mw_ctx = {
                    "provider_name": provider.name,
                    "is_cloud": getattr(provider, "is_cloud", False),
                }
                call_kwargs = await run_chain(
                    self.name, call_kwargs, direction="request", context=_mw_ctx
                )
                provider._current_load += 1
                try:
                    value = await provider.execute(**call_kwargs)
                    # A blank completion from `think` is a FAILURE, not a
                    # success — a reasoning model that exhausts its token budget
                    # inside <think> returns empty content (ollama carries the
                    # reasoning in a separate channel). Fall through to the next
                    # provider instead of handing the caller "". Mirrors the
                    # blank-detection execute_compare already does for benching.
                    if self.name == "think" and not str(
                        getattr(value, "value", value) or ""
                    ).strip():
                        last_error = RuntimeError(
                            f"provider '{provider.name}' returned empty content"
                        )
                        continue
                    return Result(
                        value=value,
                        provider=provider.name,
                        is_cloud=getattr(provider, "is_cloud", False),
                        under_powered=bool(min_ability)
                        and not meets(getattr(provider, "ability", "standard"), min_ability),
                    )
                except Exception as exc:
                    # Fall through to the next provider — but REMEMBER why this
                    # one failed. Without this the terminal error below reports a
                    # provider that ran and raised as a provider that never
                    # existed, which sends the caller after the wrong fix
                    # (e.g. "install playwright" when a live Chrome session
                    # actually refused a screenshot for want of a permission).
                    last_error = exc
                    continue
                finally:
                    provider._current_load -= 1

        chain_label = only_provider or (
            bucket or (f"{domain}/{task_shape}" if domain and task_shape else domain) or "default"
        )
        if last_error is not None:
            raise RuntimeError(
                f"Capability '{self.name}' failed (chain={chain_label}): "
                f"{type(last_error).__name__}: {last_error}"
            ) from last_error
        raise RuntimeError(
            f"No available provider for capability '{self.name}' (chain={chain_label})"
        )

    async def execute_stream(
        self,
        *,
        domain: str | None = None,
        task_shape: str | None = None,
        bucket: str | None = None,
        min_ability: str | None = None,
        prefer_provider: list[str] | str | None = None,
        **kwargs,
    ) -> AsyncGenerator[dict, None]:
        """Stream results from the first available provider (same two-pass pattern as execute).

        As soon as the chosen provider emits its first chunk, yields one
        marker chunk ``{"provider_used": provider.name, "is_cloud": bool,
        "under_powered": bool}`` so consumers can update UI labels immediately
        — not at end-of-stream. If a provider raises *before* any chunk, no
        marker is emitted and the chain falls through to the next provider
        cleanly. ``min_ability`` routes as in ``execute()``.
        """
        if self._simulate_offline():
            raise RuntimeError(
                f"Capability '{self.name}' is set to simulate offline (capability.simulate_offline)"
            )
        from emptyos.capabilities.ability import meets

        providers = self._reorder_by_ability(
            self._get_providers(domain, task_shape, bucket), min_ability
        )
        if prefer_provider:
            providers = self._reorder_by_preference(providers, prefer_provider)
        for pass_num in (1, 2):
            for provider in providers:
                if pass_num == 1 and provider.at_capacity:
                    continue
                if not await self._provider_ready(provider, **kwargs):
                    continue
                call_kwargs = await self._preprocess_outbound_kwargs(provider, kwargs)
                # Same middleware insertion point as execute() — see comment there.
                from emptyos.capabilities.middleware import run_chain  # noqa: PLC0415

                _mw_ctx = {
                    "provider_name": provider.name,
                    "is_cloud": getattr(provider, "is_cloud", False),
                }
                call_kwargs = await run_chain(
                    self.name, call_kwargs, direction="request", context=_mw_ctx
                )
                provider._current_load += 1
                try:
                    emitted = False
                    async for chunk in provider.execute_stream(**call_kwargs):
                        if not emitted:
                            yield {
                                "provider_used": provider.name,
                                "is_cloud": getattr(provider, "is_cloud", False),
                                "under_powered": bool(min_ability)
                                and not meets(
                                    getattr(provider, "ability", "standard"), min_ability
                                ),
                            }
                            emitted = True
                        yield chunk
                    return
                except Exception:
                    continue
                finally:
                    provider._current_load -= 1

        chain_label = (
            bucket or (f"{domain}/{task_shape}" if domain and task_shape else domain) or "default"
        )
        raise RuntimeError(
            f"No available provider for capability '{self.name}' (chain={chain_label})"
        )

    async def execute_compare(
        self,
        *,
        domain: str | None = None,
        task_shape: str | None = None,
        bucket: str | None = None,
        **kwargs,
    ) -> list[dict]:
        """Call ALL available providers in parallel (across all domains). For benchmarking.

        Cloud providers are filtered through the consent manager *silently* — a
        benchmark must never block on a user prompt. Providers whose consent
        would require a prompt (policy="ask" and not yet approved) are returned
        with `error = "skipped: consent not granted"` so the caller can see
        them in the result set and prompt the user to pre-approve.
        """
        import asyncio
        import time

        # Every variant the system knows about, even one only wired into a
        # specific bucket chain. Inline variant_id dedup below (after the
        # human-skip), so walk WITHOUT unique= to keep the existing order.
        seen = set()
        all_providers = self.all_providers()

        approved: list[Provider] = []
        skipped: list[dict] = []
        cm = self.consent_manager
        for p in all_providers:
            if p.name == "human":
                continue
            vid = p.variant_id
            if vid in seen:
                continue
            seen.add(vid)
            if not await p.available():
                continue
            if (
                getattr(p, "is_cloud", False)
                and cm is not None
                and not cm.would_allow_silently(p.name)
            ):
                reason = (
                    "consent policy = never"
                    if cm.policy == "never"
                    else "cloud provider not pre-approved — approve it once, then re-run"
                )
                skipped.append(
                    {
                        **p.variant_meta,
                        "response": None,
                        "latency_ms": 0,
                        "error": f"skipped: {reason}",
                    }
                )
                continue
            approved.append(p)

        if not approved:
            return skipped

        async def _run(provider):
            t0 = time.monotonic()
            meta = provider.variant_meta
            try:
                value = await provider.execute(**kwargs)
                # A blank completion is a FAILURE in a benchmark, but it does not
                # raise — so without this it scores as a success with an empty
                # preview. Real case: a reasoning model that exhausts its token
                # budget inside <think> returns content="" after minutes of work
                # (ollama carries reasoning in a separate field). Surface it as
                # an error so ranking and the compare view both count it.
                text = getattr(value, "value", value)
                if not str(text if text is not None else "").strip():
                    return {
                        **meta,
                        "response": value,
                        "latency_ms": round((time.monotonic() - t0) * 1000),
                        "error": "empty response (no content returned)",
                    }
                return {
                    **meta,
                    "response": value,
                    "latency_ms": round((time.monotonic() - t0) * 1000),
                    "error": None,
                }
            except Exception as e:
                return {
                    **meta,
                    "response": None,
                    "latency_ms": round((time.monotonic() - t0) * 1000),
                    "error": str(e),
                }

        results = await asyncio.gather(*[_run(p) for p in approved])
        return list(results) + skipped

    def add_provider(self, provider: Provider, priority: int | None = None):
        """Add a provider. Lower priority index = tried first."""
        if priority is not None:
            self.providers.insert(priority, provider)
        else:
            self.providers.append(provider)

    async def status(self) -> list[dict]:
        """Check which providers are available, with capacity + recovery info."""

        async def _row(p, domain):
            try:
                h = await p.health()
            except Exception as e:
                h = {"available": False, "reason": f"health check failed: {e}", "recovery": None}
            return {
                "name": p.name,
                "available": bool(h.get("available")),
                "reason": h.get("reason"),
                "recovery": h.get("recovery"),
                "domain": domain,
                "capacity": p.capacity,
                "current_load": p.current_load,
                "is_cloud": bool(getattr(p, "is_cloud", False)),
                "auth_mode": getattr(p, "auth_mode", "") or "",
                "variant": getattr(p, "variant_id", p.name),
                "model": getattr(p, "model", "") or "",
            }

        result = [await _row(p, "default") for p in self.providers]
        for domain, providers in self._domains.items():
            for p in providers:
                result.append(await _row(p, domain))
        return result


class CapabilityRegistry:
    """Registry of all capabilities. Built from config at kernel boot."""

    def __init__(self):
        self._capabilities: dict[str, Capability] = {}
        self._consent_manager: CloudConsentManager | None = None

    def set_consent_manager(self, manager: CloudConsentManager):
        """Attach a consent manager so all capabilities use it for cloud calls."""
        self._consent_manager = manager
        for cap in self._capabilities.values():
            cap.consent_manager = manager

    @property
    def consent_manager(self) -> CloudConsentManager | None:
        return self._consent_manager

    def register(self, name: str, capability: Capability):
        capability.name = name
        if self._consent_manager is not None:
            capability.consent_manager = self._consent_manager
        self._capabilities[name] = capability

    def get(self, name: str) -> Capability:
        cap = self._capabilities.get(name)
        if cap is None:
            raise KeyError(f"Capability not found: {name}")
        return cap

    def has(self, name: str) -> bool:
        return name in self._capabilities

    def list(self) -> dict[str, Capability]:
        return dict(self._capabilities)

    async def status(self) -> dict[str, list[dict]]:
        """Status of all capabilities and their providers."""
        result = {}
        for name, cap in self._capabilities.items():
            result[name] = await cap.status()
        return result
