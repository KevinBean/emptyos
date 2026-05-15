"""BaseApp — the class all EmptyOS apps inherit from."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Callable
from datetime import UTC
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from emptyos.sdk.utils import now_iso

if TYPE_CHECKING:
    from emptyos.kernel import Kernel
    from emptyos.kernel.app_loader import AppManifest


# Appended to system= when think(with_confidence=True). Asks for a JSON
# envelope so the model self-rates and surfaces what it couldn't find.
# Feeds the demand log on low scores.
CONFIDENCE_ENVELOPE = (
    "\n\nReturn your reply as a single JSON object with this shape: "
    '{"answer": "<your full answer>", '
    '"confidence": <integer 1-5, where 1=guessing, 5=certain>, '
    '"missing": ["<term/concept/fact you needed but could not find>", ...], '
    '"assumed": ["<assumption you made to answer>", ...]}. '
    "Do not wrap in code fences. Output JSON only."
)


class BaseApp:
    """Base class for EmptyOS apps.

    Subclass this and implement setup(). Use decorators to register
    CLI commands, web routes, event handlers, and scheduled jobs.
    """

    def __init__(self, kernel: Kernel, manifest: AppManifest):
        self.kernel = kernel
        self.manifest = manifest
        self._event_unsubs: list = []

    async def setup(self):
        """Called when app is loaded. Override to register handlers and get services."""
        for meta, method in self._get_decorated("_eos_event"):
            unsub = self.kernel.events.on(meta["type"], method)
            self._event_unsubs.append(unsub)

    async def teardown(self):
        """Called when app is stopped. Override for cleanup."""
        for unsub in self._event_unsubs:
            unsub()
        self._event_unsubs.clear()
        # Close per-app SQLite connection if opened
        if "db" in self.__dict__:
            try:
                self.__dict__["db"].close()
            except Exception:
                pass
            del self.__dict__["db"]

    def require(self, service_name: str) -> Any:
        """Get a required service. Raises if not found."""
        return self.kernel.services.get(service_name)

    def service(self, service_name: str) -> Any | None:
        """Get an optional service. Returns None if not found."""
        return self.kernel.services.get_optional(service_name)

    def engine(self, engine_id: str) -> Any | None:
        """Get an engine by ID. Returns None if not available."""
        return self.kernel.services.get_optional(f"engine:{engine_id}")

    # --- Cron / interval scheduling --------------------------------------------
    # Apps that schedule recurring work (rooms reminders, rooms scheduled
    # check-ins, dogfood-agent unattended runs) all walked the same dance:
    # check kernel.scheduler exists, build an APScheduler trigger, add_job
    # with replace_existing. Extracted here so each consumer is one line.

    def add_cron_job(
        self,
        job_id: str,
        callable_,
        *,
        cron: str | None = None,
        interval_seconds: float | None = None,
    ) -> bool:
        """Register a recurring job. Provide exactly one of `cron` (crontab
        expression) or `interval_seconds`. Returns True when registration
        succeeded, False when the scheduler is unavailable or the trigger
        spec is invalid. Idempotent — calling twice with the same job_id
        replaces the previous trigger.
        """
        sched = getattr(self.kernel, "scheduler", None)
        if not sched or not getattr(sched, "_scheduler", None):
            return False
        if (cron is None) == (interval_seconds is None):
            return False  # exactly one must be set
        try:
            if cron is not None:
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger.from_crontab(cron)
            else:
                from apscheduler.triggers.interval import IntervalTrigger
                trigger = IntervalTrigger(seconds=interval_seconds)
            sched._scheduler.add_job(
                callable_, trigger=trigger, id=job_id, replace_existing=True,
            )
            return True
        except Exception:
            return False

    def remove_cron_job(self, job_id: str) -> bool:
        """Drop a job by id. Returns False when scheduler missing or job
        wasn't registered (also a benign condition for fail-soft cleanup)."""
        sched = getattr(self.kernel, "scheduler", None)
        if not sched or not getattr(sched, "_scheduler", None):
            return False
        try:
            sched._scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    # --- Per-entity locking ---------------------------------------------------
    # Many apps need to serialize read-modify-write on a single entity (a
    # journal day, a sim run, a cables project, a lightning study). The
    # canonical pattern (per CLAUDE.md § Development Gotchas → vault races):
    # one asyncio.Lock keyed by the unit of isolation. `entity_lock(scope)`
    # is the shared implementation — pass any string as the scope (date,
    # entity id, vault path) and get back a stable Lock for that scope.
    def entity_lock(self, scope: str) -> "asyncio.Lock":
        """Return a stable asyncio.Lock for `scope`, lazily created."""
        locks = self.__dict__.setdefault("_eos_entity_locks", {})
        lock = locks.get(scope)
        if lock is None:
            lock = asyncio.Lock()
            locks[scope] = lock
        return lock

    # --- Nested-payload vault frontmatter helpers -----------------------------
    # Vault frontmatter is flat-only (see memory: feedback_vault_frontmatter_flat).
    # A list-of-dicts written into a frontmatter value gets shredded by the
    # parser's inline-array detection. These helpers wrap the safe encoding —
    # `{"items": [...]}` so the value doesn't begin with `[` — and decode
    # tolerantly on read (accepts list, JSON-string, or wrapper-dict).
    @staticmethod
    def encode_nested_payload(items: list) -> str:
        """JSON-encode a list as `{"items": [...]}` for frontmatter storage."""
        import json
        return json.dumps({"items": list(items or [])})

    @staticmethod
    def decode_nested_payload(raw: Any) -> list:
        """Decode whatever shape comes back from the vault into a list."""
        import json
        if not raw:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                v = json.loads(raw)
            except (ValueError, TypeError):
                return []
            if isinstance(v, dict) and "items" in v:
                return v["items"] if isinstance(v["items"], list) else []
            return v if isinstance(v, list) else []
        return []

    @staticmethod
    def decode_nested_dict(raw: Any) -> dict:
        """Tolerant decode of a top-level nested dict round-tripped through
        flat-only vault frontmatter. Accepts dict | JSON string | Python repr
        string | None and always returns a dict.

        Sibling to `decode_nested_payload` (list shape). Use at the read
        boundary on any frontmatter field whose value is itself an object —
        e.g. `cable.overrides`, `record.metadata`. Pair with `json.dumps(v)`
        on the write side; the decoder also accepts the legacy Python-repr
        form (single-quoted) so notes created before the write-side fix
        still round-trip cleanly.
        """
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            import json
            try:
                v = json.loads(raw)
                return v if isinstance(v, dict) else {}
            except (ValueError, TypeError):
                pass
            import ast
            try:
                v = ast.literal_eval(raw)
                return v if isinstance(v, dict) else {}
            except (ValueError, SyntaxError):
                return {}
        return {}

    # --- Calculator framework: method registry, typed I/O, comparison ---
    # See `emptyos/sdk/method_registry.py` and `emptyos/sdk/schema.py`. Apps
    # opt in by declaring `[[provides.methods.<endpoint>]]` blocks in their
    # manifest. The registry is built lazily from manifest.provides on first
    # access. Apps with no method blocks pay zero overhead.

    @cached_property
    def method_registry(self) -> Any:
        from emptyos.sdk.method_registry import MethodRegistry
        return MethodRegistry.from_manifest(self.manifest.provides)

    def list_methods(self, endpoint: str) -> list[dict]:
        """Return JSON-friendly listing of methods for an endpoint.

        Each entry: {id, label, default, version, description, references,
        requires_engines, input_schema, output_schema, available,
        disabled_reason}. Use as the body of a `GET /api/methods` route.
        """
        return self.method_registry.to_listing(self, endpoint)

    def resolve_method(self, endpoint: str, method_id: str | None) -> Any:
        """Resolve a method by id (or fall back to the endpoint default).

        Raises ValueError if neither id nor a default exists. The returned
        `MethodSpec` exposes `await spec.run(self, payload)` which records
        compute provenance automatically.
        """
        spec = self.method_registry.resolve(endpoint, method_id)
        if spec is None:
            raise ValueError(
                f"no method registered for endpoint '{endpoint}'"
                + (f" (asked for '{method_id}')" if method_id else "")
            )
        return spec

    async def compare_methods(
        self,
        endpoint: str,
        payload: Any,
        methods: list[str] | None = None,
        reference: dict | None = None,
        scalar_field_picker: Callable[[Any], dict] | None = None,
    ) -> dict:
        """Run multiple methods against the same inputs, return aligned diffs.

        ``methods`` defaults to all available methods at this endpoint.
        ``reference`` is an optional injected column (e.g. a CDEGS / PSCAD /
        textbook ground truth) — its scalar fields participate in the diff
        table so apps can reuse this for validation pages.
        ``scalar_field_picker`` is an app-supplied callable that pulls
        comparable scalars out of a result. Defaults to grabbing every
        numeric top-level key from a dict result.
        """
        from emptyos.sdk.schema import inputs_hash

        registry = self.method_registry
        targets: list[Any] = []
        if methods:
            for mid in methods:
                spec = registry.get(endpoint, mid)
                if spec is None:
                    raise ValueError(f"unknown method '{mid}' for endpoint '{endpoint}'")
                targets.append(spec)
        else:
            targets = registry.list(endpoint)
        if not targets:
            raise ValueError(f"no methods registered for endpoint '{endpoint}'")

        results: dict[str, dict] = {}
        for spec in targets:
            ok, reason = spec.is_available(self)
            if not ok:
                results[spec.id] = {"error": reason}
                continue
            try:
                value = await spec.run(self, payload)
            except Exception as e:  # noqa: BLE001
                results[spec.id] = {"error": str(e)}
                continue
            prov = self.last_compute_provenance(endpoint)
            results[spec.id] = {"result": value, "provenance": prov}

        # Pick scalars per result
        pick = scalar_field_picker or _default_scalar_picker
        scalars_per_method: dict[str, dict[str, float]] = {}
        for mid, entry in results.items():
            if "result" in entry:
                scalars_per_method[mid] = pick(entry["result"]) or {}
        if reference:
            scalars_per_method["reference"] = pick(reference) or {}

        # Build diff: for each scalar key common to ≥2 sources, compute
        # values dict + max relative spread vs. reference (or first method).
        all_keys: set[str] = set()
        for d in scalars_per_method.values():
            all_keys.update(d.keys())
        diffs: list[dict] = []
        for key in sorted(all_keys):
            values = {m: d[key] for m, d in scalars_per_method.items() if key in d}
            if len(values) < 2:
                continue
            anchor = values.get("reference") or next(iter(values.values()))
            if not isinstance(anchor, (int, float)) or anchor == 0:
                # rel_pct undefined; skip the percent column
                diffs.append({"field": key, "values": values, "max_rel_pct": None})
                continue
            spreads = []
            for v in values.values():
                if isinstance(v, (int, float)):
                    spreads.append(abs(v - anchor) / abs(anchor))
            diffs.append({
                "field": key,
                "values": values,
                "max_rel_pct": round(max(spreads) * 100, 4) if spreads else None,
            })

        return {
            "inputs_hash": inputs_hash(payload),
            "results": results,
            "comparison": {
                "matched_fields": [d["field"] for d in diffs],
                "diffs": diffs,
            },
        }

    # --- Compute cache (per-process LRU for slow deterministic methods) ---
    # See `emptyos/sdk/compute_cache.py`. Wrap only the pure-compute portion
    # of a method — side effects (vault writes, event emits) should run on
    # every call, not just cache misses.

    async def cache_compute(
        self,
        namespace: str,
        key: str,
        fn: Callable[[], Any],
        version: str = "1",
    ) -> Any:
        """Cache-or-compute helper. Returns the value; ignores hit/miss.

        ``fn`` is an async zero-arg callable invoked on cache miss. ``key``
        should be a deterministic fingerprint of the inputs (typically
        `inputs_hash(payload)`). ``version`` lets callers invalidate by
        bumping (e.g. when the underlying algorithm changes).
        """
        from emptyos.sdk.compute_cache import cache_or_compute
        value, _hit = await cache_or_compute(
            self.manifest.id, namespace, key, fn, version=version,
        )
        return value

    async def cache_compute_with_status(
        self,
        namespace: str,
        key: str,
        fn: Callable[[], Any],
        version: str = "1",
    ) -> tuple[Any, bool]:
        """Same as cache_compute but returns (value, hit). For provenance /
        UI affordances that want to surface ``cache_hit`` to the caller."""
        from emptyos.sdk.compute_cache import cache_or_compute
        return await cache_or_compute(
            self.manifest.id, namespace, key, fn, version=version,
        )

    def cache_clear(self, namespace: str | None = None) -> int:
        """Drop cache entries for this app. Returns number dropped."""
        from emptyos.sdk.compute_cache import clear
        return clear(self.manifest.id, namespace=namespace)

    # --- Conformance suite (manifest-declared regression cases) ---

    @cached_property
    def conformance_registry(self) -> Any:
        from emptyos.sdk.conformance import ConformanceRegistry
        return ConformanceRegistry.from_manifest(self.manifest.provides)

    def list_conformance(self, endpoint: str) -> list[dict]:
        """Return JSON-friendly listing of conformance cases at an endpoint."""
        cases = self.conformance_registry.list(endpoint)
        return [
            {
                "case_id": c.case_id,
                "label": c.label,
                "methods": list(c.methods),
                "tolerances": dict(c.tolerances),
                "references": list(c.references),
            }
            for c in cases
        ]

    async def run_conformance(
        self,
        endpoint: str,
        case_id: str | None = None,
        method_id: str | None = None,
    ) -> list[dict] | dict:
        """Run one or all conformance cases at an endpoint.

        ``case_id`` None → run every case at this endpoint, returns list.
        ``case_id`` set  → run that one case, returns single dict.
        ``method_id`` restricts to one method (default: all methods listed
        on the case).
        """
        from emptyos.sdk.conformance import run_case
        registry = self.conformance_registry
        if case_id:
            case = registry.get(endpoint, case_id)
            if case is None:
                raise ValueError(f"unknown conformance case '{case_id}' at endpoint '{endpoint}'")
            return await run_case(self, case, method_id=method_id)
        results = []
        for case in registry.list(endpoint):
            results.append(await run_case(self, case, method_id=method_id))
        return results

    def last_compute_provenance(self, endpoint: str | None = None) -> dict:
        """Return the most recent compute provenance for one or all endpoints.

        Shape per endpoint: {endpoint, method, method_version, inputs_hash,
        runtime_s, runtime_ms, warnings, extras}. With endpoint=None returns
        a dict keyed by endpoint. Empty dict if no method has run yet.
        """
        store = getattr(self, "_compute_provenance_by_endpoint", None) or {}
        if endpoint is None:
            return dict(store)
        return dict(store.get(endpoint) or {})

    async def _emit_think_executed(
        self, event_data: dict, provider_name: str | None = None
    ) -> None:
        """Emit ``think:executed`` with real token usage when the provider has it.

        ``event_data`` is mutated — ``last_usage`` (prompt_tokens, completion_tokens,
        cached_tokens, cost, model) is merged in when found on the provider.
        Consumed state is cleared so the next call starts fresh.
        """
        if provider_name:
            cap = self.kernel.capability("think")
            candidates = list(cap.providers)
            for d in getattr(cap, "_domains", {}).values():
                candidates.extend(d)
            for p in candidates:
                if p.name == provider_name and getattr(p, "last_usage", None):
                    event_data.update(p.last_usage)
                    p.last_usage = None
                    break
        await self.kernel.events.emit("think:executed", event_data, source="kernel")

    # --- Capability shortcuts (the core verbs of EmptyOS) ---

    async def think(
        self,
        prompt: str = "",
        domain: str | None = None,
        agent: str | None = None,
        *,
        task_shape: str | None = None,
        bucket: str | None = None,
        messages: list[dict] | None = None,
        cache: bool = False,
        cache_ttl_hours: int | None = None,
        with_confidence: bool = False,
        confidence_threshold: float = 3.0,
        **kwargs,
    ) -> str | dict:
        """Ask the OS to think. Routes to best provider for the domain.

        Accepts either `prompt` (single-turn) or `messages=[{role, content}, ...]`
        (multi-turn chat). When both are given, messages wins and prompt is ignored.

        Routing priority: agent enrichment > app setting > domain setting > default chain.
        Per-app configurable via settings:
          think.app.<id>           — single provider override
          think.app.<id>.providers — provider fallback order (comma-separated)
          think.app.<id>.timeout   — per-provider timeout in seconds
          think.domain.<domain>    — domain-level provider override

        If agent= is provided, the agent's system prompt, knowledge, and
        defaults are merged into the call before routing.

        Set cache=True to enable local SQLite response caching (Layer B cache).
        On a hit the provider is not called and no billing event is emitted.
        cache_ttl_hours=None means entries never expire; pass an integer to set TTL.
        Note: cache=True applies to the capability-chain path; settings-override
        paths (think.app.<id>) bypass the cache write in this version.

        This is separate from provider-side caching (Anthropic prompt cache,
        OpenAI cached tokens) which reduces cost but still makes API calls.

        Set with_confidence=True to ask the model for a structured self-rating.
        Returns a dict `{answer, confidence (1-5), missing: [...], assumed: [...]}`
        instead of a string. Answers below `confidence_threshold` (default 3)
        are logged to the demand log so the system can later surface what's
        chronically under-documented. On parse failure, returns
        `{answer: raw_text, confidence: None, missing: [], assumed: []}` —
        opt-in callers never see an exception.
        """
        import asyncio

        if messages is not None:
            kwargs["messages"] = messages
        if not prompt and not messages:
            raise ValueError("think() requires either prompt= or messages=")

        # with_confidence: append a structured-output envelope to the
        # system prompt and parse the response as JSON. Inspired by
        # Demand-Driven Context — every think() call can self-rate, and
        # low-confidence answers feed the demand log. The caller gets back
        # a dict {answer, confidence, missing, assumed}; on parse failure
        # we fall back to {answer: raw, confidence: None} so existing
        # call sites that opt in never see an exception.
        if with_confidence:
            kwargs["system"] = (kwargs.get("system") or "") + CONFIDENCE_ENVELOPE

        # Snapshot any citations the caller registered via self.cite() before
        # this think() call, then reset for the next one. Citations describe
        # the sources the app fed into the model — kept on _last_think_citations
        # so last_provenance() can surface them to the UI.
        self._last_think_citations = list(getattr(self, "_pending_citations", []))
        self._pending_citations = []

        # --- Auto-hash cache check (Layer B — our local SQLite cache) ---
        # Runs before agent resolution so we short-circuit as early as possible.
        # Key encodes all inputs that affect the response, including agent/domain.
        _cache_id: str | None = None
        _cache_db = None
        _tc = None
        if cache:
            from emptyos.sdk import think_cache as _tc

            _cache_db = _tc.db_path(self)
            _cache_id = _tc.make_key(
                prompt,
                system=kwargs.get("system", ""),
                model=kwargs.get("model", ""),
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
                agent=agent,
                domain=domain,
            )
            _hit = _tc.get(_cache_db, _cache_id)
            if _hit is not None:
                return self._finalize_think(
                    _hit,
                    with_confidence=with_confidence,
                    prompt=prompt,
                    threshold=confidence_threshold,
                )

        # --- Agent resolution ---
        if agent:
            agent_data = self.kernel.agents.resolve(agent)
            if agent_data:
                # Build system prompt: agent base + knowledge + tools
                agent_system = agent_data.get("system_prompt", "")
                knowledge = self.kernel.agents.load_knowledge(agent_data)
                if knowledge:
                    agent_system += "\n\n## Reference Knowledge\n" + knowledge
                tools = agent_data.get("tools", [])
                if tools:
                    agent_system += "\n\n## Available Tools\n" + "\n".join(f"- {t}" for t in tools)
                # Merge: agent system + caller system (caller adds specifics)
                caller_system = kwargs.get("system", "")
                if caller_system:
                    agent_system += "\n\n## Task Instructions\n" + caller_system
                kwargs["system"] = agent_system
                # Apply agent defaults (caller overrides take priority)
                if agent_data.get("temperature") is not None and "temperature" not in kwargs:
                    kwargs["temperature"] = agent_data["temperature"]
                if agent_data.get("model") and "model" not in kwargs:
                    kwargs["model"] = agent_data["model"]

        effective_domain = domain
        settings = self.kernel.services.get_optional("settings")
        app_id = self.manifest.id

        if settings:
            # App-level single provider override (highest priority)
            app_provider = settings.get(f"think.app.{app_id}")
            if app_provider and "," not in str(app_provider):
                result = await self._think_with_provider(app_provider, prompt, domain, kwargs)
                if result:
                    return self._finalize_think(
                        result,
                        with_confidence=with_confidence,
                        prompt=prompt,
                        threshold=confidence_threshold,
                    )

            # App-level provider chain with timeout + fallback
            app_providers = settings.get(f"think.app.{app_id}.providers")
            app_timeout = int(settings.get(f"think.app.{app_id}.timeout", 0) or 0)
            if app_providers:
                chain = [p.strip() for p in str(app_providers).split(",") if p.strip()]
                timeout = app_timeout or 30
                for prov in chain:
                    try:
                        val = await asyncio.wait_for(
                            self._think_with_provider(prov, prompt, domain, kwargs),
                            timeout=timeout,
                        )
                        if val is not None:
                            return self._finalize_think(
                                val,
                                with_confidence=with_confidence,
                                prompt=prompt,
                                threshold=confidence_threshold,
                            )
                    except (TimeoutError, Exception):
                        continue

            # Domain-level override
            if domain:
                domain_provider = settings.get(f"think.domain.{domain}")
                if domain_provider:
                    result = await self._think_with_provider(
                        domain_provider, prompt, domain, kwargs
                    )
                    if result:
                        return self._finalize_think(
                            result,
                            with_confidence=with_confidence,
                            prompt=prompt,
                            threshold=confidence_threshold,
                        )

        # Default: use capability chain
        t0 = time.monotonic()
        result = await self.kernel.capability("think").execute(
            prompt=prompt,
            domain=effective_domain,
            task_shape=task_shape,
            bucket=bucket,
            **kwargs,
        )
        latency = round((time.monotonic() - t0) * 1000)

        self._last_think_provider = {
            "provider": result.provider,
            "is_cloud": bool(getattr(result, "is_cloud", False)),
            "model": kwargs.get("model") or getattr(result, "model", None),
            "latency_ms": latency,
        }

        prompt_len = (
            len(prompt) if prompt else sum(len(m.get("content", "")) for m in (messages or []))
        )
        await self._emit_think_executed(
            {
                "provider": result.provider,
                "is_cloud": getattr(result, "is_cloud", False),
                "domain": domain or "default",
                "app": app_id,
                "latency_ms": latency,
                "prompt_len": prompt_len,
            },
            provider_name=result.provider,
        )

        if _tc is not None and _cache_id is not None:
            _tc.put(
                _cache_db,
                _cache_id,
                prompt=prompt,
                system=kwargs.get("system"),
                model=kwargs.get("model"),
                response=result.value,
                app_id=app_id,
                ttl_hours=cache_ttl_hours,
            )

        return self._finalize_think(
            result.value,
            with_confidence=with_confidence,
            prompt=prompt,
            threshold=confidence_threshold,
        )

    def cite(self, kind: str, ref: str, **extra) -> None:
        """Register a source the next think() call is grounded in.

        ``kind`` is one of: ``vault_note`` (ref = vault-relative path),
        ``eos_doc`` (ref = repo-relative path), ``web_page`` (ref = url),
        ``app_record`` (ref = "<app>/<id>"), ``kb`` (ref = KB slug — resolves
        via call_app("kb", "get_note", slug) at response time). Extra fields
        (e.g. lines, title) are passed through verbatim.

        Citations are consumed by the next ``think()`` call and surface in
        ``last_provenance()['citations']`` so UIs can show users what the
        AI was grounded on. Calling cite() without a subsequent think() is a
        no-op (cleared on the next think()).
        """
        if not hasattr(self, "_pending_citations"):
            self._pending_citations = []
        self._pending_citations.append({"kind": kind, "ref": ref, **extra})

    async def kb_explain(self, slug: str) -> dict:
        """Fetch a KB note's body + metadata for in-app explanation surfaces.

        Returns ``{slug, title, kind, body}`` for the named KB note, or
        ``{error, slug}`` if no such note exists. Intended for "?"/tooltip
        widgets — a UI can show the verbatim explanation of a formula,
        concept, or clause without re-implementing KB rendering.

        Asynchronous because it crosses the call_app boundary. Safe to call
        from any app; no cost if KB is unavailable (returns error dict).
        """
        try:
            return await self.call_app("kb", "get_note", slug=slug) or {"error": "kb unavailable", "slug": slug}
        except Exception as e:  # noqa: BLE001 — call_app failures should not crash callers
            return {"error": str(e), "slug": slug}

    def last_provenance(self) -> dict:
        """Return provenance metadata for the most recent think() call.

        Shape: {mode: 'local'|'cloud', provider: str, model: str|None,
                latency_ms: int, citations: [{kind, ref, ...}]}.
        Empty dict if no think() has run yet.

        Intended for API responses that render AI-authored content — pair with
        the frontend EOS_UI.provenance() helper to render the required chip
        per docs/FRONTEND-DESIGN-LANGUAGE.md §6. Citations enumerate sources
        the app fed in via self.cite() before the think() call.
        """
        meta = getattr(self, "_last_think_provider", None)
        if not meta:
            return {}
        return {
            "mode": "cloud" if meta.get("is_cloud") else "local",
            "provider": meta.get("provider") or "",
            "model": meta.get("model"),
            "latency_ms": meta.get("latency_ms"),
            "citations": list(getattr(self, "_last_think_citations", [])),
        }

    def _finalize_think(
        self,
        raw: str,
        *,
        with_confidence: bool,
        prompt: str,
        threshold: float,
    ) -> str | dict:
        """Post-process a think() result. When with_confidence is set,
        parse the envelope JSON, log low-confidence calls to the demand
        log, and return a dict. Otherwise return the raw string."""
        if not with_confidence:
            return raw
        from emptyos.sdk.utils import parse_llm_json

        parsed = parse_llm_json(raw, fallback={})
        if not isinstance(parsed, dict) or "answer" not in parsed:
            return {"answer": raw, "confidence": None, "missing": [], "assumed": []}
        try:
            conf = float(parsed.get("confidence")) if parsed.get("confidence") is not None else None
        except (TypeError, ValueError):
            conf = None
        missing = parsed.get("missing") or []
        if isinstance(missing, str):
            missing = [missing]
        if conf is not None and conf < threshold:
            self._record_demand(
                kind="think",
                query=prompt[:500],
                result="low_confidence",
                confidence=conf,
                missing=[str(m)[:200] for m in missing][:10],
            )
        return {
            "answer": parsed.get("answer", ""),
            "confidence": conf,
            "missing": missing,
            "assumed": parsed.get("assumed") or [],
        }

    def _record_demand(
        self,
        *,
        kind: str,
        query: str,
        result: str = "empty",
        confidence: float | None = None,
        missing: list[str] | None = None,
        **extra,
    ) -> None:
        """Append one entry to data/demand_log.jsonl.

        kind: "search" | "vault_query" | "think". Frees the schema for
        future hook points without a migration.
        result: "empty" | "low_confidence" | "no_match".
        Never raises — log writes must not break the caller.
        """
        from emptyos.sdk import demand_log

        entry = {
            "app": self.manifest.id,
            "kind": kind,
            "query": query,
            "result": result,
        }
        if confidence is not None:
            entry["confidence"] = confidence
        if missing:
            entry["missing"] = missing
        if extra:
            entry.update(extra)
        demand_log.append(self.kernel.config.data_dir, entry)

    async def think_safe(
        self,
        prompt: str = "",
        *,
        fallback: str | Callable[[Exception], str] = "AI unavailable right now.",
        **kwargs,
    ) -> str:
        """Like ``think()`` but never raises. Returns ``fallback`` if every provider fails.

        Use in UI paths where AI is an enhancement, not a hard dependency —
        the page should still render if ollama is stopped, cloud is denied,
        and all other providers are offline. The fallback string is shown
        verbatim in the UI, so write it as user-facing copy (not a stack trace).

        ``fallback`` may be a callable ``(exc) -> str`` when you want the
        error surfaced in the message (e.g. for debug-mode UIs).
        """
        try:
            return await self.think(prompt, **kwargs)
        except Exception as e:
            self.log_warn(
                "think_safe fallback",
                data={"error": str(e)[:200], "domain": kwargs.get("domain") or ""},
            )
            if callable(fallback):
                try:
                    return fallback(e)
                except Exception:
                    return "AI unavailable right now."
            return fallback

    async def _think_with_provider(
        self, provider_name: str, prompt: str, domain, kwargs
    ) -> str | None:
        """Try to call a specific provider by name. Returns None if unavailable.

        `kwargs` may include `messages=[{role, content}]` for multi-turn chat;
        providers that understand messages will use them instead of `prompt`.
        """
        cap = self.kernel.capability("think")
        msgs = kwargs.get("messages")
        prompt_len = len(prompt) if prompt else sum(len(m.get("content", "")) for m in (msgs or []))
        for p in cap.providers:
            if p.name == provider_name and await p.available():
                t0 = time.monotonic()
                try:
                    value = await p.execute(prompt=prompt, **kwargs)
                    latency = round((time.monotonic() - t0) * 1000)
                    self._last_think_provider = {
                        "provider": provider_name,
                        "is_cloud": bool(getattr(p, "is_cloud", False)),
                        "model": kwargs.get("model"),
                        "latency_ms": latency,
                    }
                    await self._emit_think_executed(
                        {
                            "provider": provider_name,
                            "is_cloud": getattr(p, "is_cloud", False),
                            "domain": domain or "default",
                            "app": self.manifest.id,
                            "latency_ms": latency,
                            "prompt_len": prompt_len,
                            "routed_by": "settings",
                        },
                        provider_name=provider_name,
                    )
                    return value
                except Exception:
                    return None
        # Also check domain providers
        for domain_providers in cap._domains.values():
            for p in domain_providers:
                if p.name == provider_name and await p.available():
                    t0 = time.monotonic()
                    try:
                        value = await p.execute(prompt=prompt, **kwargs)
                        latency = round((time.monotonic() - t0) * 1000)
                        self._last_think_provider = {
                            "provider": provider_name,
                            "is_cloud": bool(getattr(p, "is_cloud", False)),
                            "model": kwargs.get("model"),
                            "latency_ms": latency,
                        }
                        await self._emit_think_executed(
                            {
                                "provider": provider_name,
                                "is_cloud": getattr(p, "is_cloud", False),
                                "domain": domain or "default",
                                "app": self.manifest.id,
                                "latency_ms": latency,
                                "prompt_len": prompt_len,
                                "routed_by": "settings",
                            },
                            provider_name=provider_name,
                        )
                        return value
                    except Exception:
                        return None
        return None

    async def think_stream(
        self,
        prompt: str = "",
        domain: str | None = None,
        *,
        provider: str | None = None,
        task_shape: str | None = None,
        bucket: str | None = None,
        messages: list[dict] | None = None,
        **kwargs,
    ):
        """Stream thinking results. Yields {"text": str, "done": bool} chunks.

        Accepts either `prompt` (single-turn) or `messages=[{role, content}, ...]`
        (multi-turn chat). Providers that support messages use them directly;
        others fall back to a flattened transcript.

        If ``provider`` is passed, pin to that provider (searching main chain +
        domain subchains); fall back to the default chain if the pinned provider
        is absent or unavailable. Mirrors ``pinned_execute`` semantics for the
        streaming path.

        Otherwise respects app-level provider settings (think.app.<id> and
        think.app.<id>.providers) and falls back to the capability chain.
        """
        if messages is not None:
            kwargs["messages"] = messages
        if not prompt and not messages:
            raise ValueError("think_stream() requires either prompt= or messages=")
        cap = self.kernel.capability("think")
        settings = self.kernel.services.get_optional("settings")
        app_id = self.manifest.id
        target_provider = provider  # caller-supplied pin wins over settings

        if not target_provider and settings:
            # App-level single provider override
            app_provider = settings.get(f"think.app.{app_id}")
            if app_provider and "," not in str(app_provider):
                target_provider = app_provider
            # App-level provider chain: use first available
            if not target_provider:
                app_providers = settings.get(f"think.app.{app_id}.providers")
                if app_providers:
                    chain = [p.strip() for p in str(app_providers).split(",") if p.strip()]
                    for prov_name in chain:
                        for p in cap.providers:
                            if p.name == prov_name and await p.available() and not p.at_capacity:
                                target_provider = prov_name
                                break
                        if target_provider:
                            break
            # Domain-level override
            if not target_provider and domain:
                domain_provider = settings.get(f"think.domain.{domain}")
                if domain_provider:
                    target_provider = domain_provider

        prompt_len = (
            len(prompt) if prompt else sum(len(m.get("content", "")) for m in (messages or []))
        )
        t0 = time.monotonic()
        used_provider: str | None = None
        is_cloud = False
        usage_seen: dict | None = None

        async def emit_billing():
            # Prefer usage explicitly yielded in the stream; otherwise fall back
            # to the provider's last_usage (stash set by openai_compat streams).
            event_data = {
                "provider": used_provider or target_provider or "unknown",
                "is_cloud": is_cloud,
                "domain": domain or "default",
                "app": app_id,
                "latency_ms": round((time.monotonic() - t0) * 1000),
                "prompt_len": prompt_len,
                "streamed": True,
            }
            if usage_seen:
                event_data.update(usage_seen)
                await self.kernel.events.emit("think:executed", event_data, source="kernel")
            else:
                await self._emit_think_executed(event_data, provider_name=event_data["provider"])

        try:
            if target_provider:
                # Stream from the specific provider
                for p in list(cap.providers) + [pp for d in cap._domains.values() for pp in d]:
                    if p.name == target_provider and await p.available():
                        used_provider = p.name
                        is_cloud = getattr(p, "is_cloud", False)
                        p._current_load += 1
                        try:
                            async for chunk in p.execute_stream(prompt=prompt, **kwargs):
                                u = chunk.get("usage") if isinstance(chunk, dict) else None
                                if u:
                                    usage_seen = u
                                yield chunk
                            return
                        finally:
                            p._current_load -= 1

            # Default: use capability chain
            async for chunk in cap.execute_stream(
                prompt=prompt,
                domain=domain,
                task_shape=task_shape,
                bucket=bucket,
                **kwargs,
            ):
                if isinstance(chunk, dict):
                    if "provider_used" in chunk:
                        used_provider = chunk.get("provider_used")
                        is_cloud = chunk.get("is_cloud", False)
                    elif "usage" in chunk and chunk["usage"]:
                        usage_seen = chunk["usage"]
                yield chunk
        finally:
            try:
                await emit_billing()
            except Exception:
                pass

    async def think_compare(self, prompt: str, **kwargs) -> list[dict]:
        """Send same prompt to ALL think providers in parallel. For benchmarking."""
        return await self.kernel.capability("think").execute_compare(prompt=prompt, **kwargs)

    async def think_cached(
        self,
        prompt: str,
        *,
        key,
        system: str | None = None,
        domain: str | None = None,
        force_live: bool = False,
        meta: dict | None = None,
        **kwargs,
    ) -> tuple[str, bool]:
        """Like ``think()`` but reads / writes a vault-backed response cache.

        Returns ``(response, from_cache)``. ``from_cache`` is True when the
        response came from the vault and no model was called.

        ``key`` is a logical cache key — a string, tuple, or dict. Encode
        whatever inputs *should* invalidate a cached answer (prompt version,
        entity id, model name) into the key; encoding the prompt text itself
        is usually overkill.

        Set ``force_live=True`` to bypass the read path (the UI "Re-run live"
        button does this). The fresh output is still written back to cache
        so subsequent reads hit.

        Use this for user-facing LLM output that should be deterministic
        across restarts (demo walkthroughs, printed case-studies). For one-
        shot analytical calls, just use ``self.think()``.
        """
        from emptyos.sdk import llm_cache as _llm_cache

        cache_id = _llm_cache.hash_key(self.manifest.id, key)
        if not force_live:
            cached = _llm_cache.cache_get(self, cache_id)
            if cached is not None:
                return cached, True
        response = await self.think(prompt, domain=domain, system=system, **kwargs)
        _llm_cache.cache_put(self, cache_id, prompt, system, response, key=key, meta=meta or {})
        return response, False

    # --- Per-call provider pinning ---

    async def pinned_execute(self, cap_name: str, provider_name: str | None, **kwargs):
        """Execute a capability pinned to ``provider_name``, falling back to
        the default chain if that provider is absent or unavailable.

        Use for "the user picked a pipeline mode this session" — e.g. speaking
        app lets a user choose Local vs OpenAI, or jobs/practice wants higher-
        quality feedback from a cloud model. The caller owns the choice; this
        helper honours it without failing when the chosen provider is offline.

        Scans both the main chain and any domain-specific subchains so a
        provider that's only registered under ``think.domains.code`` is still
        findable.

        Not for: the common path (just call ``self.think/listen/speak`` and
        trust the configured chain). Not for: hard-require-this-provider
        semantics (use a direct capability lookup + raise on miss).
        """
        cap = self.kernel.capability(cap_name)

        async def _call_direct(p):
            """Run a pinned provider. Emits ``think:executed`` for cap_name='think'
            so pinned calls show up in billing with real token usage."""
            t0 = time.monotonic()
            value = await p.execute(**kwargs)
            if cap_name == "think":
                prompt = kwargs.get("prompt", "") or ""
                msgs = kwargs.get("messages") or []
                prompt_len = len(prompt) if prompt else sum(len(m.get("content", "")) for m in msgs)
                await self._emit_think_executed(
                    {
                        "provider": p.name,
                        "is_cloud": getattr(p, "is_cloud", False),
                        "domain": kwargs.get("domain") or "default",
                        "app": self.manifest.id,
                        "latency_ms": round((time.monotonic() - t0) * 1000),
                        "prompt_len": prompt_len,
                        "routed_by": "pinned",
                    },
                    provider_name=p.name,
                )
            return value

        if provider_name:
            for p in cap.providers:
                if p.name == provider_name and await p.available():
                    return await _call_direct(p)
            for domain_providers in getattr(cap, "_domains", {}).values():
                for p in domain_providers:
                    if p.name == provider_name and await p.available():
                        return await _call_direct(p)
        result = await cap.execute(**kwargs)
        return result.value

    # --- Non-text modalities (available when platform provides them) ---

    async def speak(self, text: str, **kwargs) -> bytes | str:
        """Text to speech. Returns audio data or file path."""
        result = await self.kernel.capability("speak").execute(text=text, **kwargs)
        return result.value

    async def listen(self, audio: bytes | str, **kwargs) -> str:
        """Speech to text. Accepts audio data or file path."""
        result = await self.kernel.capability("listen").execute(audio=audio, **kwargs)
        return result.value

    async def pronounce(
        self,
        audio: bytes | str,
        reference_text: str,
        *,
        language: str = "en-us",
        **kwargs,
    ) -> dict:
        """Score pronunciation of `audio` against `reference_text`.

        Returns the structured response from the pronounce service —
        `alignment` (per-phone match/sub/del rows with timestamps),
        `word_alignment`, `summary` with `phone_accuracy` + `weak_phones`,
        plus the raw transcript and reference phone lists.

        Raises when no provider is available (plugin offline or model still
        loading); callers should `try/except` and fall back to a heuristic
        path. See `services/pronounce/server.py` for the response shape.
        """
        result = await self.kernel.capability("pronounce").execute(
            audio=audio, reference_text=reference_text, language=language, **kwargs
        )
        return result.value

    async def draw(self, prompt: str, **kwargs) -> str:
        """Generate an image from text. Returns file path."""
        result = await self.kernel.capability("draw").execute(prompt=prompt, **kwargs)
        return result.value

    async def download_drawn_image(self, filename, target: Path) -> bool:
        """Materialize a `draw()` result to a known path.

        `draw()` returns whatever the active provider gave back — an existing
        absolute path (some providers) or a bare ComfyUI filename (most). This
        helper handles both: copies the file if it's already on disk, otherwise
        pulls it from ComfyUI's `/view` endpoint. Returns True on success.
        """
        from pathlib import Path as _P
        p = filename if isinstance(filename, _P) else _P(str(filename))
        if p.is_absolute() and p.exists():
            try:
                import shutil as _sh
                target.parent.mkdir(parents=True, exist_ok=True)
                _sh.copy2(str(p), str(target))
                return True
            except Exception:
                return False
        comfy = self.kernel.services.get_optional("comfyui")
        host = ""
        if comfy and hasattr(comfy, "_host"):
            try:
                host = comfy._host()
            except Exception:
                host = ""
        if not host:
            host = "http://127.0.0.1:8188"
        from urllib.parse import quote
        url = f"{host}/view?filename={quote(str(filename), safe='')}"
        try:
            session = getattr(comfy, "_session", None) if comfy else None
            if session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return False
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(await resp.read())
                    return True
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        return False
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(await resp.read())
                    return True
        except Exception:
            return False

    async def see(self, *, mode: str = "snapshot", **kwargs) -> str:
        """Capture an image from a camera. Returns file path."""
        result = await self.kernel.capability("see").execute(mode=mode, **kwargs)
        return result.value

    async def browse(self, action: str, **kwargs) -> Any:
        """Drive a headless browser. Returns provider-shaped dict per verb.

        Verbs: navigate, click, fill, screenshot, snapshot, eval, wait_for,
        close. Pass `context_id="..."` across calls to keep cookies + the
        same page open between actions; omit for a one-shot using the
        default context.

        Examples:
            await self.browse("navigate", url="http://127.0.0.1:9001/")
            await self.browse("click", selector="#add-text")
            await self.browse("fill", selector="#new-task", value="buy milk")
            shot = await self.browse("screenshot", full_page=True)
            snap = await self.browse("snapshot", selector="#task-list")

        Raises RuntimeError if no `browse` provider is available — apps that
        treat browser automation as optional should catch and degrade.
        """
        result = await self.kernel.capability("browse").execute(action=action, **kwargs)
        return result.value

    async def try_browse(self, action: str, **kwargs) -> tuple[bool, Any]:
        """Non-raising variant of `browse` for trace-shaped UI scripts.

        Returns ``(True, result)`` on success or ``(False, error_str)`` on
        any failure. Lets multi-step scripts (dogfood UI walks, fix-agent
        repro loops) record per-step status in a trace instead of aborting
        on the first failure — the error path stringifies the exception so
        it can land directly in a response payload.
        """
        try:
            return True, await self.browse(action, **kwargs)
        except Exception as e:
            return False, str(e)

    async def animate(self, prompt: str, *, image: str = "", num_frames: int = 24, **kwargs) -> str:
        """Generate a video clip. Returns local file path to the rendered MP4/WEBP.

        `image` is an optional reference still (provider-specific format —
        local providers expect a ComfyUI-known filename; cloud providers
        accept a local path or URL).
        """
        result = await self.kernel.capability("animate").execute(
            prompt=prompt, image=image, num_frames=num_frames, **kwargs
        )
        return result.value

    # --- Audio upload/serve helpers (used by voice-input apps) ---

    async def save_audio_upload(
        self, upload_file, *, subdir: str = "audio", prefix: str = "rec"
    ) -> tuple[Path, str]:
        """Persist a multipart-uploaded audio blob under data_dir/{subdir}.

        Accepts a Starlette UploadFile (or anything with an async `.read()` and
        an optional `.filename`). Returns (absolute_path, filename). Filename
        is randomized with the original suffix preserved (default .webm).
        """
        import uuid

        audio_dir = self.data_dir / subdir
        audio_dir.mkdir(parents=True, exist_ok=True)
        ext = ".webm"
        orig = getattr(upload_file, "filename", None)
        if orig:
            ext = Path(orig).suffix or ext
        filename = f"{prefix}_{uuid.uuid4().hex[:12]}{ext}"
        filepath = audio_dir / filename
        filepath.write_bytes(await upload_file.read())
        return filepath, filename

    def serve_data_file(
        self,
        subdir: str,
        *parts: str,
        media_type: str = "application/octet-stream",
    ):
        """Serve a file under ``data_dir/{subdir}/{*parts}``.

        Path-traversal-safe via two checks: any segment containing ``/`` or
        ``\\`` is rejected up-front (those split a single component into
        many — almost always a routing error), then the fully resolved
        target must still sit under the resolved ``data_dir/{subdir}``
        root. The latter catches ``..`` traversals, symlink escapes, and
        any future filesystem escape vector — without rejecting legitimate
        filenames that happen to contain ``..`` as a substring (e.g.
        ``file..tar.gz``).

        Returns 400 ``{"error": "invalid path"}`` on a routing-shape
        violation or a resolved-out-of-root attempt, 404 on a missing
        file. Use this for serving generated artifacts (screenshots,
        exports) through a route shaped like ``/api/<thing>/{ts}/{name}``.
        """
        from starlette.responses import FileResponse, JSONResponse

        for p in parts:
            if "/" in p or "\\" in p:
                return JSONResponse({"error": "invalid path"}, status_code=400)
        root = (self.data_dir / subdir).resolve()
        target = root
        for p in parts:
            target = target / p
        try:
            target = target.resolve()
            target.relative_to(root)
        except (ValueError, OSError):
            return JSONResponse({"error": "invalid path"}, status_code=400)
        if not target.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(target), media_type=media_type)

    def serve_audio_file(self, filename: str, *, subdir: str = "audio"):
        """Return a Starlette FileResponse for data_dir/{subdir}/{filename}.

        Returns a 404 JSONResponse when the file is missing. Content-Type is
        inferred from the file extension.
        """
        from starlette.responses import FileResponse, JSONResponse

        filepath = self.data_dir / subdir / filename
        if not filepath.exists() or not filepath.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        content_types = {
            ".wav": "audio/wav",
            ".webm": "audio/webm",
            ".mp3": "audio/mpeg",
            ".mp4": "audio/mp4",
            ".ogg": "audio/ogg",
            ".m4a": "audio/mp4",
        }
        ct = content_types.get(filepath.suffix.lower(), "audio/webm")
        return FileResponse(str(filepath), media_type=ct)

    # --- Hub panel helpers ---

    @staticmethod
    def stat_tile(icon: str, value, label: str, href: str) -> dict:
        """Standard shape for a `stat-tile` hub panel.

        Keeps the {icon, value, label, href} contract canonical — changes to
        the tile shape land here rather than being repeated across every
        contributing app. See docs/APP-DEVELOPMENT.md § "Hub Panel
        Contributions" (renderer table, `stat-tile` row).
        """
        return {"icon": icon, "value": value, "label": label, "href": href}

    # --- App-to-app calls (in-process, no HTTP) ---

    async def call_app(self, app_id: str, method: str, **kwargs):
        """Call another app's method directly. Auto-loads if not loaded yet.

        Usage: data = await self.call_app("task", "list_tasks")
        """
        instance = self.kernel.apps.instances.get(app_id)
        if not instance:
            instance = await self.kernel.apps.load(app_id)
        fn = getattr(instance, method, None)
        if fn is None:
            raise AttributeError(f"App '{app_id}' has no method '{method}'")
        result = fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def call_contributions(
        self,
        target: str,
        slot: str,
        **kwargs,
    ) -> list[tuple[dict, Any]]:
        """Enumerate `[[contributes.<target>.<slot>]]`, dispatch each to its
        contributor's `method`, return `(entry, result)` pairs in manifest order.

        - Skips contributors whose method returns `None` (the contract for
          "nothing to show right now").
        - Catches exceptions per contributor and logs to syslog so one bad
          contributor can't break the consumer.

        Usage from the consumer side (e.g. voice-assistant gathering context):

            for entry, ctx in await self.call_contributions("voice-assistant", "context"):
                if ctx:
                    parts.append(ctx)
        """
        entries = self.kernel.apps.get_contributions(target, slot)
        out: list[tuple[dict, Any]] = []
        for entry in entries:
            app_id = entry.get("_app_id")
            method = entry.get("method")
            if not app_id or not method:
                continue
            try:
                result = await self.call_app(app_id, method, **kwargs)
            except Exception as e:
                syslog = getattr(self.kernel, "syslog", None)
                if syslog:
                    syslog.warn(
                        target,
                        f"contribution {target}.{slot} '{entry.get('id')}' "
                        f"({app_id}.{method}) failed: {e}",
                    )
                continue
            if result is None:
                continue
            out.append((entry, result))
        return out

    # --- Vault operations ---

    async def read(self, path: str, **kwargs) -> str:
        """Ask the OS to read a file. Human pastes, or filesystem reads."""
        result = await self.kernel.capability("read").execute(path=path, **kwargs)
        return result.value

    async def write(self, path: str, content: str, **kwargs):
        """Ask the OS to write a file. Human saves, or filesystem writes."""
        result = await self.kernel.capability("write").execute(path=path, content=content, **kwargs)
        return result.value

    @staticmethod
    async def read_json(request) -> dict:
        # Tolerant request-body decode. Windows curl sends literal "—" as
        # cp1252 byte 0x97; Starlette's request.json() can't decode it and
        # surfaces a 500. Try utf-8, fall back to cp1252, last-resort
        # utf-8-with-replace so em-dashes / smart quotes from real keyboards
        # never crash a write path.
        body = await request.body()
        if not body:
            return {}
        for enc in ("utf-8", "cp1252"):
            try:
                return json.loads(body.decode(enc))
            except UnicodeDecodeError:
                continue
        return json.loads(body.decode("utf-8", errors="replace"))

    @staticmethod
    async def safe_json(request) -> dict:
        # Tolerant sibling of read_json: returns {} on any decode error
        # instead of raising. Use when an empty or malformed body should
        # be silently treated as no payload (most write endpoints where
        # individual fields are optional). Use read_json when invalid
        # JSON should surface as an error to the caller.
        try:
            return await BaseApp.read_json(request)
        except Exception:
            return {}

    async def search(self, query: str, **kwargs) -> list:
        """Ask the OS to search. Human remembers, or grep/search-engine finds.

        Empty results are logged to the demand log so periodic classification
        can surface unknown unknowns. See `emptyos.sdk.demand_log`.
        """
        result = await self.kernel.capability("search").execute(query=query, **kwargs)
        value = result.value
        if not value:
            self._record_demand(kind="search", query=query, result="empty")
        return value

    # --- Embedding helpers (semantic search, related-item discovery) ---
    #
    # Backed by emptyos.sdk.embeddings.Embedder. Cache lives at
    # data/embeddings/<app>.json so every app that uses embeddings shares
    # the OpenAI cost-per-content-hash regardless of which app embedded
    # it first (the underlying vec is keyed on text hash, not app id).

    def _embedder(self):
        from emptyos.sdk.embeddings import Embedder

        if not hasattr(self, "_embedder_instance"):
            cache_path = self.kernel.config.data_dir / "embeddings" / "shared.json"
            self._embedder_instance = Embedder(cache_path=cache_path)
        return self._embedder_instance

    async def embed_text(self, text: str) -> list[float]:
        """Single-shot embed. Returns 1536-dim vector (or zero-vec if no API key)."""
        return await self._embedder().embed_one(text)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch embed. Cached by content hash — repeat calls on same texts are free."""
        return await self._embedder().embed_many(texts)

    @property
    def embeddings_available(self) -> bool:
        """True iff OPENAI_API_KEY is set. Apps should fall back to lexical
        retrieval when False so they don't break on a fresh self-host."""
        return self._embedder().available

    async def embedding_index(self, items: list, text_fn):
        """Build a queryable embedding index for `items`. `text_fn(item) -> str`
        produces the text to embed for each one.

        Returns an EmbeddingIndex with `.search(query, top_k)` method. The
        index is rebuilt each call but embeddings are content-hash cached,
        so unchanged items pay nothing on subsequent rebuilds.
        """
        from emptyos.sdk.embeddings import build_index

        return await build_index(self._embedder(), items, text_fn)

    async def emit(self, event_type: str, data: dict | None = None):
        """Emit an event from this app."""
        await self.kernel.events.emit(event_type, data or {}, source=self.manifest.id)

    # --- Assignment protocol (People ↔ other apps) ---

    async def emit_assignment(
        self,
        person_id: str,
        item: dict,
        weight_hours: float = 1.0,
        role: str = "assignee",
        assigned: bool = True,
    ):
        """Declare (or revoke) that `person_id` is working on `item`.

        `item` should be `{"app": <app_id>, "id": <item_id>, "title": ..., ...}`
        so the people app can link back to the source. `role` is a free-form
        string; conventional values are `assignee` (default), `designer`,
        `checker`, `approver`, `reviewer` — the people app segments workload
        views by role. `weight_hours` feeds capacity math; keep it conservative
        (reviewers are lighter than designers).

        The people app subscribes to `people:assigned` / `people:unassigned`
        and maintains an aggregate index. Apps that own assignable items
        should also override `list_assignments()` so the index can be rebuilt
        on boot.
        """
        event = "people:assigned" if assigned else "people:unassigned"
        await self.emit(
            event,
            {
                "person": person_id,
                "item": item,
                "weight_hours": weight_hours,
                "role": role,
            },
        )

    async def list_assignments(self) -> list[dict]:
        """Return every current assignment this app owns.

        Override in apps that have assignable items. Each row:
            {"person": <id>, "item": {...}, "weight_hours": float, "role": str}

        The people app calls this on boot for a full rebuild of its
        aggregate index. Default is `[]` — apps without assignments ignore
        the protocol entirely."""
        return []

    # --- Structured Logging ---

    def log(self, message: str, level: str = "info", data: dict | None = None, job_id: str = ""):
        """Write to the system log. Persisted to SQLite, queryable via /system-log/api/logs."""
        self.kernel.syslog.log(level, self.manifest.id, message, data=data, job_id=job_id)

    def log_warn(self, message: str, **kwargs):
        self.log(message, level="warn", **kwargs)

    def log_error(self, message: str, **kwargs):
        self.log(message, level="error", **kwargs)

    @cached_property
    def db(self):
        """Per-app SQLite database at data/apps/{id}/app.db. WAL mode for concurrent reads."""
        import sqlite3

        path = self.data_dir / "app.db"
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @cached_property
    def data_dir(self) -> Path:
        """Directory for this app's persistent data (JSON, SQLite, etc.)."""
        d = self.kernel.config.data_dir / "apps" / self.manifest.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def repo_root(self) -> Path:
        """EmptyOS repo root — the directory containing ``emptyos.toml``.

        Use for paths into the codebase (``self.repo_root / "apps"``,
        ``self.repo_root / "CLAUDE.md"``). Derived from the config-file
        location, not ``__file__`` depth counting (which breaks when files
        move). Falls back to ``Path.cwd()`` if the config path is unavailable
        during very-early init.
        """
        try:
            return Path(self.kernel.config.path).resolve().parent
        except Exception:
            return Path.cwd()

    @cached_property
    def state_path(self) -> Path:
        """Path to this app's persistent state file."""
        return self.kernel.config.data_dir / "state" / f"{self.manifest.id}.json"

    def runs(self, kind: str = "runs", *, state_filename: str = "run.json"):
        """Per-run scratchpad + state at ``data_dir / kind``.

        Returns a :class:`RunRegistry`. Use when an app has the harness shape:
        each run gets a stable id, a directory of intermediate artifacts, and
        a small JSON state file. ``kind`` lets one app keep multiple registries
        side-by-side (e.g. ``runs("verify-runs")``)."""
        from emptyos.sdk.run_registry import RunRegistry

        return RunRegistry(self.data_dir / kind, state_filename=state_filename)

    def load_state(self, default: Any = None) -> Any:
        """Load persistent state from disk."""
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return default if default is not None else {}

    # --- Activity Log (SQLite) ---
    # Platform-level append-only logging. Any app can use it.

    def _ensure_activity_table(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                app TEXT NOT NULL,
                data TEXT NOT NULL
            )
        """)
        self.db.commit()
        # Migrate from JSONL if exists
        jsonl_path = self.data_dir / "activity.jsonl"
        if jsonl_path.exists():
            try:
                for line in jsonl_path.read_text(encoding="utf-8").strip().split("\n"):
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    self.db.execute(
                        "INSERT INTO activity (ts, app, data) VALUES (?,?,?)",
                        (
                            entry.get("ts", ""),
                            entry.get("app", self.manifest.id),
                            json.dumps(entry, ensure_ascii=False, default=str),
                        ),
                    )
                self.db.commit()
                jsonl_path.rename(jsonl_path.with_suffix(".jsonl.bak"))
            except Exception:
                pass

    _activity_table_ready: bool = False

    def log_activity(self, entry: dict, max_lines: int = 2000, trim_to: int = 1000):
        """Append an entry to the activity log (SQLite). Auto-trims when exceeding max_lines."""
        if not self._activity_table_ready:
            self._ensure_activity_table()
            self._activity_table_ready = True
        from datetime import datetime

        entry.setdefault("ts", datetime.now(UTC).isoformat())
        entry.setdefault("app", self.manifest.id)
        self.db.execute(
            "INSERT INTO activity (ts, app, data) VALUES (?,?,?)",
            (entry["ts"], entry["app"], json.dumps(entry, ensure_ascii=False, default=str)),
        )
        # Auto-trim
        count = self.db.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
        if count > max_lines:
            self.db.execute(
                "DELETE FROM activity WHERE id IN (SELECT id FROM activity ORDER BY id LIMIT ?)",
                (count - trim_to,),
            )
        self.db.commit()

    def read_activity(
        self, limit: int = 50, filter_key: str = "", filter_val: str = ""
    ) -> list[dict]:
        """Read recent activity entries, optionally filtered."""
        if not self._activity_table_ready:
            self._ensure_activity_table()
            self._activity_table_ready = True
        if filter_key:
            # Filter via JSON extraction
            rows = self.db.execute(
                "SELECT data FROM activity WHERE json_extract(data, '$.' || ?) = ? ORDER BY id DESC LIMIT ?",
                (filter_key, filter_val, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT data FROM activity ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_state(self, data: Any):
        """Save persistent state to disk."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(data, indent=2, default=str))

    # --- Settings Helpers ---

    def get_countdown_items(self) -> list[dict]:
        """Get user-configured countdown items from settings."""
        settings = self.kernel.services.get_optional("settings")
        if not settings:
            return []
        raw = settings.get("countdown.items", [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = []
        return raw if isinstance(raw, list) else []

    # --- Vault Map (path discovery) ---

    def vault_config(self, key: str, default: str = "") -> str:
        """Get a vault path from the vault map. Settings override map file.

        Usage: path = self.vault_config("people_dir", "30_Resources/People")
        """
        app_id = self.manifest.id
        # Settings override (highest priority)
        settings = self.kernel.services.get_optional("settings")
        if settings:
            override = settings.get(f"{app_id}.vault_path.{key}")
            if override:
                return str(override)
        # Vault map
        return self.kernel.vault_map.get(app_id, key, default)

    def setting(self, key: str, default: Any = None) -> Any:
        """Read a value from the Settings service (UI-configurable at runtime).

        Falls back to *default* when the settings service is unavailable or the
        key has no stored value.  Complements ``app_config`` which reads static
        per-machine config from ``emptyos.toml``.
        """
        svc = self.kernel.services.get_optional("settings")
        if svc is None:
            return default
        val = svc.get(key, default)
        return default if val is None else val

    def app_config(self, key: str, default: Any = None) -> Any:
        """Get app-specific config from emptyos.toml [apps.<app_id>] section.

        Usage: artist = self.app_config("artist", "Unknown Artist")
        Reads from: [apps.music-studio] artist = "3:30 Channel"
        """
        return self.kernel.config.get(f"apps.{self.manifest.id}.{key}", default)

    def vault_config_path(self, key: str, default: str = "") -> Path | None:
        """Get an absolute vault path from the vault map."""
        rel = self.vault_config(key, default)
        if not rel:
            return None
        vault = self.kernel.config.notes_path
        if not vault:
            return None
        return vault / rel

    # --- Vault Storage ---
    # Apps can persist human-readable data to the vault (markdown files).
    # Unlike state (JSON in data/), vault files are visible in vault apps,
    # synced across devices, and editable by the user.

    @property
    def vault_root(self) -> Path:
        """The configured vault root, or Path(".") if no vault is mounted.

        Use when the app needs to read/write anywhere in the vault (not just its
        own subdir). For per-app storage use ``vault_dir`` / ``vault_write``.
        """
        return self.kernel.config.notes_path or Path(".")

    @cached_property
    def vault_dir(self) -> Path:
        """This app's directory in the vault. Created on first write.

        Reads vault base from kernel config. Subfolder prefix configurable
        via settings key 'vault.app_prefix' (default: '30_Resources/EmptyOS').
        """
        vault = self.vault_root
        prefix = "30_Resources/EmptyOS"
        settings = self.kernel.services.get_optional("settings")
        if settings:
            custom = settings.get("vault.app_prefix")
            if custom:
                prefix = str(custom)
        return vault / prefix / self.manifest.id

    def vault_path(self, filename: str) -> Path:
        """Get a path inside this app's vault directory."""
        return self.vault_dir / filename

    def vault_read(self, filename: str, default: str = "") -> str:
        """Read a file from this app's vault directory."""
        p = self.vault_path(filename)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="ignore")
        return default

    def vault_write(self, filename: str, content: str):
        """Write a file to this app's vault directory."""
        p = self.vault_path(filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def vault_list(self, pattern: str = "*.md") -> list[Path]:
        """List files in this app's vault directory."""
        if not self.vault_dir.exists():
            return []
        return sorted(self.vault_dir.glob(pattern))

    def vault_read_at(self, rel_path: str, default: str = "") -> str:
        """Read a file at a vault-root-relative path.

        Use when the file lives outside the app's own vault_dir (e.g. a
        path resolved from ``vault_config()`` that already includes the
        full ``30_Resources/EmptyOS/<app>/...`` prefix).
        """
        p = self.vault_root / rel_path
        if p.exists():
            return p.read_text(encoding="utf-8", errors="ignore")
        return default

    def vault_write_at(self, rel_path: str, content: str) -> None:
        """Write a file at a vault-root-relative path. Creates parents."""
        p = self.vault_root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # --- Vault Index (query + mutate vault notes via platform index) ---

    def vault_query(
        self, tags: list[str] | None = None, folder: str | None = None, **properties
    ) -> list[dict]:
        """Query vault notes by tags and/or frontmatter properties.

        Returns list of {path, name, folder, ext, size, properties, tags}.
        Uses the platform VaultIndex (SQLite-backed, fast).
        Falls back to empty list if VaultIndex is unavailable.
        """
        vi = self.kernel.services.get_optional("vault_index")
        if not vi:
            return []
        rows = vi.find(tags=tags, folder=folder, **properties)
        if not rows:
            self._record_demand(
                kind="vault_query",
                query=json.dumps({"tags": tags, "folder": folder, **properties}, default=str)[:500],
                result="empty",
            )
        return rows

    def vault_update(self, rel_path: str, properties: dict):
        """Update frontmatter properties in a vault note and re-index.

        rel_path: relative to vault root (e.g. "20_Areas/Career/Job-Applications/foo/_app.md")
        properties: dict of key-value pairs to update in frontmatter
        """
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            vi.update_properties(rel_path, properties)

    # ── flat-frontmatter JSON encoding ──
    # Vault frontmatter is flat YAML — nested structures (list of dicts,
    # etc.) must be JSON-encoded into a single string field. The `@json `
    # sentinel prefix keeps `vault_index._parse_fm` from misreading the
    # value as an inline YAML array, which would silently corrupt every
    # nested dict on read. Two consumers today: `apps/kb/` (Document
    # paragraphs) and `apps/actions/` (Workflow steps).
    @staticmethod
    def vault_encode_json(value) -> str:
        """Serialize a nested structure for storage in a single frontmatter field."""
        return "@json " + json.dumps(value if value is not None else [])

    @staticmethod
    def vault_decode_json(raw, default=None):
        """Decode the inverse of `vault_encode_json`. Tolerates legacy/empty/malformed input."""
        if default is None:
            default = []
        if raw is None:
            return default
        if not isinstance(raw, str):
            return raw  # already-decoded list/dict — pass through
        payload = raw.strip()
        if payload.startswith("@json "):
            payload = payload[len("@json "):].strip()
        if not payload:
            return default
        try:
            return json.loads(payload)
        except Exception:
            return default

    def vault_create_note(self, rel_path: str, frontmatter: dict, body: str = ""):
        """Create a new vault note with frontmatter + body and index it.

        rel_path: relative to vault root
        frontmatter: dict of YAML frontmatter key-value pairs
        body: markdown body content
        """
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            vi.create_note(rel_path, frontmatter, body)
        else:
            # Direct write fallback
            vault = self.kernel.config.notes_path
            if vault:
                from emptyos.runtime.vault_index import _serialize_fm

                abs_path = vault / rel_path
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(_serialize_fm(frontmatter) + "\n\n" + body, encoding="utf-8")

    def vault_append_section(self, rel_path: str, section: str, text: str):
        """Append text to a ## section in a vault note and re-index."""
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            vi.append_to_section(rel_path, section, text)

    def vault_get_properties(self, rel_path: str) -> dict:
        """Get all frontmatter properties for a vault note (from index, fast)."""
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            return vi.get_properties(rel_path)
        return {}

    def vault_sections(self, rel_path: str) -> list[str]:
        """List ## section names in a vault note (from index, instant)."""
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            entry = vi._files.get(rel_path)
            return list(entry.get("sections", [])) if entry else []
        return []

    def vault_read_section(self, rel_path: str, section: str) -> str:
        """Read content of a specific ## section from a vault note.

        Returns the text between ## section and the next ## (or end of file).
        Reads from disk (sections are not cached in index).
        """
        vault = self.kernel.config.notes_path
        if not vault:
            return ""
        abs_path = vault / rel_path
        if not abs_path.exists():
            return ""
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception:
            return ""
        header = f"## {section}"
        lines = content.split("\n")
        collecting = False
        result = []
        for line in lines:
            if line.strip() == header:
                collecting = True
                continue
            if collecting:
                if line.startswith("## ") and not line.startswith("### "):
                    break
                result.append(line)
        # Strip leading/trailing blank lines
        text = "\n".join(result).strip()
        return text

    def vault_read_body(self, rel_path: str) -> str:
        """Read everything after frontmatter from a vault note."""
        vault = self.kernel.config.notes_path
        if not vault:
            return ""
        abs_path = vault / rel_path
        if not abs_path.exists():
            return ""
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception:
            return ""
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                return content[end + 3 :].strip()
        return content.strip()

    # --- Vault Data Contracts ---

    def vault_reconcile(
        self,
        folder: str,
        expected_tags: list[str] | None = None,
        expected_fields: list[str] | None = None,
    ) -> dict:
        """Check vault notes against expected structure. Read-only — reports gaps."""
        vi = self.kernel.services.get("vault_index")
        if not vi:
            return {"total": 0, "compliant": 0, "gaps": [], "folder": folder}
        return vi.reconcile(folder, expected_tags, expected_fields)

    def vault_enrich(
        self, rel_path: str, add_tags: list[str] | None = None, defaults: dict | None = None
    ) -> bool:
        """Add missing tags/defaults to a vault note. Safe — never overwrites."""
        vi = self.kernel.services.get("vault_index")
        if not vi:
            return False
        return vi.enrich(rel_path, add_tags, defaults)

    # --- Vault-backed projects (generic CRUD over tag-marked notes) ---
    # Pattern: an app's "projects" are vault notes with a known tag, a
    # frontmatter shape the app owns, and a settable-fields whitelist.
    # These helpers handle the plumbing; the app supplies tag, path,
    # extra creation fields, and event names.

    def vault_project_list(
        self,
        *,
        tag: str,
        list_fields: list[str] | None = None,
        defaults: dict | None = None,
    ) -> list[dict]:
        """List non-archived vault notes carrying ``tag``.

        Each row carries id/name/created/updated/_path plus any extra
        frontmatter keys named in ``list_fields``. ``defaults`` fills in
        missing keys (use for fields the app guarantees a default for —
        e.g. ``frequency_hz=50.0``). Sorted by ``updated`` desc.
        """
        notes = self.vault_query(tags=[tag])
        defaults = defaults or {}
        out: list[dict] = []
        for n in notes:
            fm = n.get("properties") or {}
            if fm.get("archived"):
                continue
            row: dict = {
                "id": fm.get("id") or Path(n["path"]).stem,
                "name": fm.get("name") or fm.get("id") or Path(n["path"]).stem,
                "created": fm.get("created"),
                "updated": fm.get("updated"),
                "_path": n["path"],
            }
            for f in (list_fields or []):
                if f in fm:
                    row[f] = fm[f]
                elif f in defaults:
                    row[f] = defaults[f]
            out.append(row)
        return sorted(out, key=lambda p: p.get("updated") or "", reverse=True)

    async def vault_project_create(
        self,
        *,
        project_id: str,
        name: str,
        tag: str,
        path: str,
        extra_fm: dict | None = None,
        body: str | None = None,
        event_name: str | None = None,
    ) -> dict:
        """Create a vault-backed project note. Returns ``{"ok", "id", "path"}``
        or ``{"error": ...}`` if a note already exists at ``path``."""
        if self.vault_get_properties(path):
            return {"error": f"project {project_id} already exists"}
        fm: dict = {
            "id": project_id,
            "name": name,
            "tags": [tag],
            "created": now_iso(),
            "updated": now_iso(),
        }
        if extra_fm:
            fm.update(extra_fm)
        body_md = body if body is not None else f"# {name}\n\n## Notes\n\n## Tasks\n\n"
        self.vault_create_note(path, fm, body_md)
        if event_name:
            await self.emit(event_name, {"id": project_id, "name": name})
        return {"ok": True, "id": project_id, "path": path}

    async def vault_project_update(
        self,
        *,
        project_id: str,
        path: str,
        fields: dict,
        settable: set,
        event_name: str | None = None,
    ) -> dict:
        """Update whitelisted frontmatter on a project note. Returns
        ``{"ok": True}`` or ``{"error": ...}``."""
        if not self.vault_get_properties(path):
            return {"error": "project not found"}
        bad = [k for k in fields if k not in settable]
        if bad:
            return {"error": f"fields not settable: {bad}"}
        update = dict(fields)
        update["updated"] = now_iso()
        self.vault_update(path, update)
        if event_name:
            await self.emit(event_name, {
                "id": project_id, "fields": list(fields.keys()),
            })
        return {"ok": True}

    async def vault_project_delete(
        self,
        *,
        project_id: str,
        path: str,
        event_name: str | None = None,
    ) -> dict:
        """Soft-delete a project note (sets ``archived: true`` in frontmatter).
        Real deletion is a vault-tool concern, not the app's."""
        if not self.vault_get_properties(path):
            return {"ok": False, "error": "project not found"}
        self.vault_update(path, {"archived": True, "updated": now_iso()})
        if event_name:
            await self.emit(event_name, {"id": project_id})
        return {"ok": True}

    def vault_project_get(self, path: str) -> dict | None:
        """Read a project note's frontmatter, with ``_path`` injected.

        Returns the merged frontmatter dict or ``None`` if no note exists at
        ``path``. Callers typically wrap as ``return {"project": fm}`` or
        ``return {"error": "project not found"}``.
        """
        fm = self.vault_get_properties(path)
        if not fm:
            return None
        return {**fm, "_path": path}

    def vault_project_read_sidecar(
        self,
        *,
        project_path: str,
        sidecar_path: str,
        key: str = "readings",
    ) -> dict:
        """Read a sidecar JSON file next to a project note.

        Sidecars hold structured data (lists of readings, geometry, …) that
        vault frontmatter can't carry — frontmatter is flat-only. Returns
        ``{"ok": True, <key>: [...]}`` on success, ``{"error": ...}`` if the
        project is missing or the sidecar can't be parsed. Empty/missing
        sidecar returns ``{"ok": True, <key>: []}`` — the absence of a
        sidecar is not an error.
        """
        if not self.vault_get_properties(project_path):
            return {"error": "project not found"}
        raw = self.vault_read_at(sidecar_path)
        if not raw:
            return {"ok": True, key: []}
        try:
            data = json.loads(raw)
        except ValueError as e:
            return {"error": f"cannot read sidecar: {e}"}
        items = data if isinstance(data, list) else (data.get(key) or [])
        return {"ok": True, key: items}

    async def vault_project_write_sidecar(
        self,
        *,
        project_id: str,
        project_path: str,
        sidecar_path: str,
        items: list,
        key: str = "readings",
        event_name: str | None = None,
    ) -> dict:
        """Replace a project's sidecar JSON. Caller pre-cleans ``items``.

        Writes ``{<key>: items}`` as indented JSON, bumps the project's
        ``updated`` timestamp, and (optionally) emits ``event_name`` with
        ``{"id": project_id, "n": len(items)}``. Returns ``{"ok": True,
        "n": ...}`` or ``{"error": "project not found"}``. Row-shape
        validation lives in the caller — sidecar schemas vary per app.
        """
        if not self.vault_get_properties(project_path):
            return {"error": "project not found"}
        self.vault_write_at(sidecar_path, json.dumps({key: items}, indent=2))
        self.vault_update(project_path, {"updated": now_iso()})
        if event_name:
            await self.emit(event_name, {"id": project_id, "n": len(items)})
        return {"ok": True, "n": len(items)}

    # --- Job Tracking (platform-level progress for long-running operations) ---

    def _emit_job_event(self, event_type: str, job: dict):
        """Fire-and-forget emit a job event to the EventBus/WebSocket."""
        import asyncio

        data = {k: v for k, v in job.items() if v is not None}
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.kernel.events.emit(event_type, data, source=self.manifest.id))
        except RuntimeError:
            pass  # no event loop — CLI mode, skip

    def start_job(self, job_id: str, label: str = "") -> dict:
        """Start tracking a long-running job. Returns the job dict.

        Usage:
            job = self.start_job("gen-123", "Generating podcast")
            self.update_job(job["id"], phase="scripting", pct=10)
            ...
            self.finish_job(job["id"])
        """
        job = {
            "id": job_id,
            "app": self.manifest.id,
            "label": label,
            "phase": "starting",
            "detail": "",
            "pct": 0,
            "started": time.time(),
            "finished": None,
            "error": None,
        }
        self.kernel.jobs[job_id] = job
        self._emit_job_event("job:started", job)
        return job

    def update_job(self, job_id: str, phase: str = "", detail: str = "", pct: int = -1):
        """Update a running job's status."""
        job = self.kernel.jobs.get(job_id)
        if not job:
            return
        if phase:
            job["phase"] = phase
        if detail:
            job["detail"] = detail
        if pct >= 0:
            job["pct"] = min(pct, 100)
        self._emit_job_event("job:progress", job)

    def finish_job(self, job_id: str, error: str = ""):
        """Mark a job as done or failed. Evicts old finished jobs (keep last 100)."""
        job = self.kernel.jobs.get(job_id)
        if not job:
            return
        job["phase"] = "error" if error else "done"
        job["detail"] = error if error else "completed"
        job["pct"] = 100 if not error else job.get("pct", 0)
        job["finished"] = time.time()
        self._emit_job_event("job:completed" if not error else "job:failed", job)
        # Kernel-level trim: evict finished jobs older than 1h or exceeding 200
        self.kernel.trim_jobs()

    def get_job(self, job_id: str) -> dict | None:
        """Get a job's current status."""
        return self.kernel.jobs.get(job_id)

    def print_rich(self, text: str):
        """Print rich-formatted output. Falls back to plain print on encoding errors."""
        try:
            from rich import print as rprint

            rprint(text)
        except (UnicodeEncodeError, OSError):
            # Windows cp1252 can't handle some emoji/unicode
            print(text.encode("utf-8", errors="replace").decode("ascii", errors="replace"))

    def print_json(self, data: Any):
        """Print JSON output."""
        print(json.dumps(data, indent=2, default=str))

    def _get_decorated(self, attr: str) -> list[tuple[dict, Any]]:
        """Find methods with a specific decorator attribute.

        Walk the class MRO instead of `inspect.getmembers(self, …)` — the latter
        does `getattr(self, name)` for every attribute, which TRIGGERS every
        `@property` and `@cached_property` on the class. On BaseApp that means
        opening a SQLite connection (`self.db`) per app at setup, costing ~1.3s
        × ~80 apps on cold boot. Walking the class only sees the descriptor
        objects, never their computed values.
        """
        result: list[tuple[dict, Any]] = []
        seen: set[str] = set()
        for cls in type(self).__mro__:
            for name, raw in cls.__dict__.items():
                if name in seen or not callable(raw) or not hasattr(raw, attr):
                    continue
                seen.add(name)
                # Bind via the instance so `self` is passed to the call.
                # `getattr(self, name)` on a regular method is a cheap
                # descriptor lookup; it doesn't trigger any properties.
                result.append((getattr(raw, attr), getattr(self, name)))
        return result

    def get_cli_methods(self) -> list[tuple[dict, Any]]:
        return self._get_decorated("_eos_cli")

    def get_web_methods(self) -> list[tuple[dict, Any]]:
        return self._get_decorated("_eos_web")

    def get_ws_methods(self) -> list[tuple[dict, Any]]:
        return self._get_decorated("_eos_ws")


def _default_scalar_picker(result: Any) -> dict[str, float]:
    """Default scalar extraction for compare_methods.

    Pulls every top-level int/float (skipping bool) out of a dict result.
    Apps with custom result shapes can pass their own picker."""
    if not isinstance(result, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in result.items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = float(v)
    return out
