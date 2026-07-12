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

from emptyos.sdk.trace import stamp_trace
from emptyos.sdk.utils import now_iso, slug_from_path as _slug_from_path

from . import base_app_context as _ctx
from . import base_app_media as _media
from . import base_app_think as _think
from . import base_app_vault as _vault
from .base_app_think import (  # noqa: F401 — re-exported; tests import these from here
    CONFIDENCE_ENVELOPE,
    FIELD_SUGGEST_SYSTEM,
    SELECT_ENVELOPE,
    _parse_suggestions,
)
from .base_app_vault import (  # noqa: F401 — re-exported; used by scoped_retrieve + tests
    INTEREST_SKIP_TAGS,
    _interest_folder_key,
)

if TYPE_CHECKING:
    from emptyos.kernel import Kernel
    from emptyos.kernel.app_loader import AppManifest


# Default system prompt for ``BaseApp.propose_kb_extractions`` — distils a
# summary/transcript/digest into durable KB-note candidates. Generalised from
# the video-digest EXTRACTION_SYSTEM (its first consumer); any think-summarised
# content (video digest, room distill, chat transcript) feeds the same shape.
DEFAULT_KB_EXTRACTION_SYSTEM = """You are extracting durable knowledge from a summary into KB-note candidates.

Each candidate is a structured object with these keys:
    kind: one of "concept" | "lesson" | "reference"
        - "concept": an explanatory standalone idea (a model, framework, distinction)
        - "lesson": a practical / hard-won applied insight ("when X, do Y")
        - "reference": a pointer at an external work, person, or canonical entity
    title: short noun phrase, ≤8 words, suitable as a note title
    slug: kebab-case slug (lowercase, hyphens, ascii) ≤40 chars
    domain: subject area (e.g. "buddhism", "ml", "leadership")
    topic: finer slice within the domain (optional)
    body: 3-8 sentences of substance, in the user's voice. Cite specifics (names,
          studies, numbers) where the source has them.

HARD CONSTRAINTS:
- Quality over quantity. 3-6 candidates total for a typical conversation.
- Do NOT propose candidates for trivia, examples, or one-liners.
- Do NOT propose candidates whose body would just paraphrase the title.
- Do NOT include any candidate whose substance the source itself flags as
  unverified speculation, unless the candidate is explicitly framed as such.

Output: ONLY a JSON array of candidates. No prose around it. No markdown fences.
"""


def _request_is_public(request, *, auth_configured: bool) -> bool:
    """Pure auth-PRESENCE classifier for the public-app pattern.

    Returns True when a request should be served the app's "public face"
    (anonymous), False for the authenticated/owner face. Presence detection
    (*is there a credential?*), NOT identity (*which user?*), so it never
    touches the single-user pin. See .claude/rules/public-app-pattern.md.

    Two topologies, one rule:
      1. Behind a trusted front proxy (cloud / control plane): the proxy marks
         anonymous public-app traffic with ``X-EOS-Public: 1``. We honour it.
         (The proxy always injects its own inner-token bearer, so credential
         absence isn't detectable at the daemon; the header is how the public
         face is requested. It can only RESTRICT to the public subset, never
         escalate, so trusting it is safe.)
      2. Directly-exposed daemon (kiosk / shared public deploy): the auth
         middleware already *validated* any cookie/bearer on a
         ``[provides.web].public_routes`` path and recorded its verdict on
         ``request.state.public_face``. Prefer that flag: presence detection
         alone cannot tell a forged bearer from a real one, so an anonymous
         caller sending ``Authorization: Bearer <garbage>`` would otherwise be
         handed the full face on a public route.

    Presence detection is the fallback for requests the middleware never
    classified (no auth gate installed, or a non-public route).

    No auth configured (local mode) -> everything is already open -> return
    False (serve the full face).
    """
    try:
        if (request.headers.get("x-eos-public", "") or "").strip() == "1":
            return True
    except Exception:
        pass
    if not auth_configured:
        return False
    # Validated verdict from the auth middleware, when it classified this
    # request. Set only for an unauthenticated caller on a public route; an
    # authenticated owner leaves it unset and falls through to presence.
    try:
        validated = getattr(request.state, "public_face", None)
    except Exception:
        validated = None
    if validated is not None:
        return bool(validated)
    try:
        has_cookie = bool(request.cookies.get("eos_session"))
    except Exception:
        has_cookie = False
    try:
        has_bearer = (
            request.headers.get("authorization", "") or ""
        ).lower().startswith("bearer ")
    except Exception:
        has_bearer = False
    return not has_cookie and not has_bearer


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

    @property
    def operation(self):
        """Per-app Operation runner.

        Apps can opt into the structured execution contract without changing
        existing BaseApp primitives. Kept lazy to avoid importing the operation
        module during SDK bootstrap.
        """
        runner = self.__dict__.get("_operation_runner")
        if runner is None:
            from emptyos.sdk.operation import OperationRunner

            runner = OperationRunner(app=self)
            self.__dict__["_operation_runner"] = runner
        return runner

    @property
    def operations(self):
        """Alias for callers that read the layer as a collection of operations."""
        return self.operation

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

    def add_once_job(self, job_id: str, callable_, *, run_at) -> bool:
        """Register a ONE-SHOT job that fires exactly once at ``run_at`` (a
        ``datetime`` or ISO string), then is discarded. The one-time analogue of
        ``add_cron_job`` — precise timed firing instead of a polling loop.

        Idempotent (``replace_existing``). Returns False when the scheduler is
        unavailable or ``run_at`` is unparseable. **Durability caveat:** the
        daemon's APScheduler is in-memory (no jobstore), so a job scheduled for
        the future is LOST on daemon restart. A durable consumer must persist
        the intent itself and re-register on boot + keep a polling backstop
        (the reminders app is the reference consumer). Fail-soft.
        """
        sched = getattr(self.kernel, "scheduler", None)
        if not sched or not getattr(sched, "_scheduler", None):
            return False
        try:
            from datetime import datetime

            when = run_at
            if isinstance(when, str):
                when = datetime.fromisoformat(when)
            from apscheduler.triggers.date import DateTrigger

            sched._scheduler.add_job(
                callable_, trigger=DateTrigger(run_date=when), id=job_id,
                replace_existing=True,
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

    def get_cron_job_next_fire(self, job_id: str) -> str | None:
        """Return ISO timestamp of next scheduled fire for `job_id`, or None
        when the scheduler is missing, the job isn't registered, or has no
        upcoming fire. Fail-soft — safe for header-bar polling endpoints."""
        sched = getattr(self.kernel, "scheduler", None)
        if not sched or not getattr(sched, "_scheduler", None):
            return None
        try:
            job = sched._scheduler.get_job(job_id)
            if job and job.next_run_time:
                return job.next_run_time.isoformat()
        except Exception:
            pass
        return None

    def add_cron_job_logged(
        self,
        job_id: str,
        callable_,
        *,
        cron: str | None = None,
        interval_seconds: float | None = None,
        crash_event: str = "tick_crash",
    ) -> bool:
        """Like ``add_cron_job``, but wraps ``callable_`` in a try/except
        that surfaces per-tick exceptions via ``log_activity`` instead of
        letting them die inside APScheduler's logger.

        The wrapped log shape is
        ``{"event": crash_event, "job": job_id, "error": str(e)[:300]}``.

        Use this when you want a tick crash to land in the app's activity
        log; use the bare ``add_cron_job`` when you want silent fail-soft
        (e.g. rooms room-schedule firing — failures are dashboard-tracked
        elsewhere) or when the callable already does its own error
        accounting.

        Caller still decides whether to log success / registration-failure
        — those tend to be per-app-flavoured (different event names like
        ``cron_registered`` vs ``smoke_cron_registered``)."""
        async def _safe():
            try:
                await callable_()
            except Exception as e:
                self.log_activity({
                    "event": crash_event,
                    "job": job_id,
                    "error": str(e)[:300],
                })
        return self.add_cron_job(
            job_id, _safe, cron=cron, interval_seconds=interval_seconds,
        )

    # --- Test-fix-verify loop: friction sources -------------------------------
    def surface_friction(
        self,
        *,
        kind: str,
        text: str,
        key: str,
        persona: str,
        scenario: str,
        app: str = "",
        frontmatter: dict | None = None,
        extra_sections: str = "",
        turn=None,
        source_hint: str = "",
    ) -> str:
        """Write a fix-prompt into the shared test-fix-verify queue; return its
        filename. Encodes the contract ``apps/fix-agent`` parses (frontmatter +
        the ``## What the persona reported`` block) via ``emptyos.sdk.fix_queue``
        so a friction source can't drift from the reader.

        ``frontmatter`` merges extra fields (e.g. ``source``, ``verify_signature``,
        ``count``); ``extra_sections`` is appended verbatim after the friction
        block (evidence, task instructions). ``source_hint`` (e.g.
        ``apps/foo/pages/index.html:142`` from the platform locator) is recorded
        both in frontmatter (for the index + reader) and as a ``## Where to look``
        body section so it survives fix-agent's strip_frontmatter and reaches the
        fixer. See ``.claude/rules/test-fix-verify-loop.md``.
        """
        from emptyos.sdk.fix_queue import (
            FixPromptQueue,
            friction_block,
            persona_scenario_line,
            render_frontmatter,
            where_to_look_block,
        )

        q = FixPromptQueue(self.kernel.config.data_dir)
        fname = q.slug(key)
        fm = {"kind": kind, "app": app, "key": key}
        if frontmatter:
            fm.update(frontmatter)
        if (source_hint or "").strip():
            fm["source_hint"] = source_hint.strip()
        where = where_to_look_block(source_hint)
        content = (
            render_frontmatter(fm) + "\n\n"
            + "# Fix this EmptyOS friction item\n\n"
            + f"**Kind**: `{kind}`\n"
            + persona_scenario_line(persona, scenario) + "\n\n"
            + friction_block(text, turn=turn)
            + (("\n\n" + where) if where else "")
            + (("\n\n" + extra_sections) if extra_sections else "")
            + "\n"
        )
        q.write(fname, content)
        q.rebuild_index()
        return fname

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

    def autopilot_root(self):
        """Kernel data root holding the shared autopilot store (``data/autopilot/``).

        Grants + the tamper-evident audit chain are shared across every grant
        consumer (voice-assistant session toggle, the outbound MCP foundry,
        runbook scheduled side-effects), so they live under the kernel data root
        — not an app's own ``data_dir`` (which is ``data/apps/<id>``). Pass the
        result to ``emptyos.sdk.autopilot`` calls (``match``, ``save_grant``…).
        """
        from pathlib import Path
        return Path(self.data_dir).parent.parent

    def budget_remaining(self, actor_id: str | None = None) -> "float | None":
        """This actor's MONTHLY autopilot budget remaining in USD, or ``None``
        when the actor has no cap (unbounded).

        Actor defaults to this app's id — how the billing bridge attributes
        spend (``billing/app.py::_meter_spend``). Thin read over the tested
        ``autopilot.budget_status``; fail-soft (returns ``None`` on any error).

        NOT the per-run ceiling: ``emptyos/sdk/run_budget.py`` /
        ``ctx.budget.remaining()`` is the inner per-generation-run budget. This
        is the outer monthly per-actor cap set by ``eos autopilot budget set``.
        """
        from emptyos.sdk import autopilot

        try:
            return autopilot.budget_status(
                self.autopilot_root(), actor_id or self.manifest.id
            )["remaining_usd"]
        except Exception:
            return None

    def over_budget(self, actor_id: str | None = None) -> bool:
        """True iff the actor has a monthly cap AND has reached it. False when
        uncapped (the honest 'unbounded' default) or on any error — so a
        self-throttle gate fails OPEN (runs), never silently blocks."""
        from emptyos.sdk import autopilot

        try:
            return not autopilot.within_budget(
                self.autopilot_root(), actor_id or self.manifest.id
            )
        except Exception:
            return False

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

    async def sweep_method(
        self,
        endpoint: str,
        method_id: str | None,
        *,
        values: list[float],
        payload_for: Callable[[float], Any],
        extract: Callable[[Any], float | None] | str,
        x_label: str = "",
        y_label: str = "",
        cache: bool = True,
    ) -> dict:
        """Run one resolved method across ``values``, return a chart-ready series.

        A "parameter study" — run the same calculation across a range of one
        input and collect the result curve (sensitivity analysis). Reuses the
        method registry so the swept method is the same one the picker selects,
        and per-point compute is memoised through the same cache as single runs.

        Args:
            endpoint: method endpoint (e.g. ``"ampacity"``).
            method_id: method to sweep, or None for the endpoint default.
            values: the x-axis points (build with
                ``emptyos.sdk.utils.sweep_values``).
            payload_for: ``payload_for(x)`` builds the method payload for one
                swept value. Keeps this helper agnostic to payload shape — the
                caller does the clone-with-override (dict spread, dataclass
                ``replace``, pydantic ``model_copy``, …).
            extract: how to pull the scalar y from a result — either a dict key
                (str) or a callable, mirroring ``compare_methods``'
                ``scalar_field_picker``.
            x_label, y_label: axis labels echoed back for the chart.
            cache: memoise each point via ``cache_compute`` (default True).

        Returns chart-ready::

            {endpoint, method, method_version, x, y, x_label, y_label,
             n_points, runtime_ms, warnings, errors}

        ``y[i]`` is None when point i failed (the matching x stays so the
        series aligns); the failure reason lands in ``errors``. Runs
        sequentially — engine compute is CPU-bound, so gathering wouldn't
        parallelise and some engines aren't reentrant.
        """
        from emptyos.sdk.schema import inputs_hash

        spec = self.resolve_method(endpoint, method_id)
        ok, reason = spec.is_available(self)
        if not ok:
            raise RuntimeError(f"method '{spec.id}' unavailable: {reason}")

        if isinstance(extract, str):
            key = extract
            y_label = y_label or key

            def _pick(result: Any) -> float | None:
                if isinstance(result, dict):
                    v = result.get(key)
                    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
                return None
        else:
            _pick = extract  # type: ignore[assignment]

        xs: list[float] = []
        ys: list[float | None] = []
        warnings: list[str] = []
        errors: list[dict] = []
        t0 = time.monotonic()
        for x in values:
            xs.append(float(x))
            try:
                payload = payload_for(x)
                if cache:
                    ck = f"{inputs_hash(payload)}:{spec.id}"
                    result = await self.cache_compute(
                        f"sweep:{endpoint}", ck,
                        lambda p=payload: spec.run(self, p),
                        version=spec.version,
                    )
                else:
                    result = await spec.run(self, payload)
            except Exception as exc:  # noqa: BLE001 — per-point isolation
                ys.append(None)
                errors.append({"x": float(x), "reason": str(exc)})
                continue
            if isinstance(result, dict):
                if result.get("error"):
                    ys.append(None)
                    errors.append({"x": float(x), "reason": str(result["error"])})
                    continue
                for w in (result.get("warnings") or []):
                    if w not in warnings:
                        warnings.append(w)
            ys.append(_pick(result))

        return {
            "endpoint": endpoint,
            "method": spec.id,
            "method_version": spec.version,
            "x": xs,
            "y": ys,
            "x_label": x_label,
            "y_label": y_label,
            "n_points": len(xs),
            "runtime_ms": round((time.monotonic() - t0) * 1000, 1),
            "warnings": warnings,
            "errors": errors,
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

        When the call happens inside a traced agent turn (``emptyos.sdk.trace``),
        ``trace_id`` is stamped on so billing can correlate per-task cost.
        """
        stamp_trace(event_data)
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
    # The think family (think / think_stream / select / suggest_field / …)
    # lives in base_app_think.py and is re-bound at the end of the class
    # body; the non-text verbs (speak, listen, draw, …) follow below.

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

    # Task shapes whose output is machine-parsed — never localize these or a
    # downstream JSON/code parser breaks. The "generated content → born in the
    # target language" rule applies only to natural-language prose.
    _STRUCTURED_SHAPES = frozenset(
        {"parse-json", "json", "classify", "extract", "code", "sql", "tool-call"}
    )

    # Appended to the system prompt to make natural-language generation come out
    # in the user's ui.language (born-in-language, not translated-after). `{lang}`
    # is the human language name. Shared by think() + think_stream().
    _LOCALIZE_SUFFIX = (
        "\n\nIMPORTANT: Write your entire response in {lang}. Keep proper nouns, "
        "code, file paths, identifiers, and URLs unchanged. Do not append a "
        "translation or the English original — respond only in {lang}."
    )

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

    async def footage(
        self, query: str, *, orientation: str = "landscape",
        min_duration: float = 0.0, **kwargs,
    ) -> dict:
        """Fetch a royalty-free stock video clip for a search term.

        Returns ``{"path", "provider", "url", "duration", "width", "height"}``,
        or ``{"path": ""}`` when nothing matched. Cloud providers (Pexels,
        Pixabay via the `footage` plugin) are consent-gated; with no provider
        wired the chain raises — catch it and skip the clip (audio-only /
        cover-only). See the `footage` capability.
        """
        result = await self.kernel.capability("footage").execute(
            query=query, orientation=orientation, min_duration=min_duration, **kwargs
        )
        return result.value

    async def translate(self, text: str, to: str, *, source: str = "en") -> str:
        """Translate one string into `to` (cache-aware, deterministic).

        English passthrough and cache hits are free; misses route through the
        `translate` capability (local NLLB-200 plugin → LLM fallback) and are
        cached so the same string never re-translates. Returns the original
        text unchanged when translation is unavailable — never raises. English
        is the single authored language; this is how everything else is derived.
        See `emptyos.sdk.i18n`.
        """
        if not text or to == source:
            return text
        from emptyos.sdk.i18n import translate_cached

        out = await translate_cached(self.kernel, to, [text])
        return out.get(text, text)

    async def translate_batch(
        self, texts: list[str], to: str, *, source: str = "en"
    ) -> dict:
        """Translate many strings at once → ``{original: translated}``.

        Cache-aware: only uncached strings hit the provider, in one batched
        call. Degrades to English (`{s: s}`) on any failure. Prefer this over a
        loop of `translate()` — NLLB and the LLM provider both batch natively.
        """
        if to == source:
            return {t: t for t in texts}
        from emptyos.sdk.i18n import translate_cached

        return await translate_cached(self.kernel, to, texts)

    async def model(self, prompt: str, *, domain: str = "articulated", **kwargs) -> str:
        """Generate an articulated 3D model from a prompt.

        Sibling of `self.draw()` / `self.animate()` in the CAD-shape domain.
        Returns a path to the record directory containing `model.py`,
        `output.urdf`, and per-part meshes. The caller can read `output.urdf`
        for the 3D structure and the matching `*.md` for frontmatter.

        Local provider (robot-modeller app) runs an LLM agent loop that writes
        Python against `engines.articulated` + compiles via the `cadquery`
        plugin. Cloud providers (Articraft API, MeshyAI) gate through the
        consent manager — never reach a cloud provider without explicit
        user consent.

        Raises when no provider is wired (until `apps/personal/robot-modeller/`
        ships).
        """
        result = await self.kernel.capability("model").execute(
            prompt=prompt, domain=domain, **kwargs
        )
        return result.value

    async def artifact(self, prompt: str, *, shape: str = "3d-scene", **kwargs) -> str:
        """Generate a single-file HTML artifact from a prompt.

        Sibling of `self.model()` but for visual / explanatory output rather
        than parametric CAD. Returns a vault-relative path to the rendered
        `scene.html` (or empty string when no provider is reachable). The
        caller is expected to embed in an iframe or hand the path to a
        publish pipeline.

        Shapes: `3d-scene`, `svg-diagram`, `schematic`, `network-graph`,
        `chart`, `anim-explainer`, `slide-deck`, `math-explainer`, `mermaid`.
        Each shape is a system-prompt preset owned by the local provider
        (apps/viz). Pass `examples=[<kb-slug>, ...]` to inject KB pattern
        notes as few-shot.

        Local provider (apps/viz) writes the HTML in one LLM call — no
        compile gate, correctness judged by eye. Cloud providers (a future
        "render via claude.ai artifacts" backend, etc.) plug in via the
        capability chain with no app changes.
        """
        result = await self.kernel.capability("artifact").execute(
            prompt=prompt, shape=shape, **kwargs
        )
        return result.value

    async def send(self, to: str, subject: str = "", body: str = "", **kwargs) -> dict:
        """Send an outbound message to an external recipient.

        Email is the first channel; the provider chain is configured by
        plugins (e.g. `email-smtp`). Cloud providers pass through the consent
        gate + outbound leak-scan automatically — the recipient and body are
        what gets summarized for the consent prompt.

        Returns the provider's result dict ({"ok", "provider", "detail"}).
        Raises RuntimeError when no `send` provider is available (none
        configured AND not interactive) — apps that treat sending as optional
        should catch and degrade (e.g. surface an on-screen confirmation).

        Don't `await` this inside an HTTP request handler when latency matters
        — wrap it in `asyncio.create_task(...)` so the request returns before
        the (possibly slow, possibly consent-gated) send completes.
        """
        result = await self.kernel.capability("send").execute(
            to=to, subject=subject, body=body, **kwargs
        )
        return result.value

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

    def app_available(self, app_id: str) -> bool:
        """True when another app is loaded and reachable via ``call_app``.

        The cheap feature-detect for ``optional_apps`` integrations — lets a UI
        hide an affordance when the sibling app isn't installed, without paying
        a failed ``call_app`` (which would auto-load or raise).
        """
        try:
            return self.kernel.apps.instances.get(app_id) is not None
        except Exception:
            return False

    async def call_app(self, app_id: str, method: str, /, **kwargs):
        """Call another app's method directly. Auto-loads if not loaded yet.

        Usage: data = await self.call_app("task", "list_tasks")

        ``app_id`` and ``method`` are positional-only — without the ``/``
        marker, a target method that itself takes a ``method`` keyword
        (e.g. ``rooms.save_pending_action(*, app, method, args)`` from
        ``propose_action``) collides with our own ``method`` parameter
        and raises ``TypeError: got multiple values for 'method'``.
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

    async def try_call_app(self, app_id: str, method: str, /, **kwargs):
        """Soft cross-app call for ``optional_apps`` integrations.

        Returns ``(result, "")`` on success, ``(None, error)`` when the target
        app is absent, disabled, or raises — so a degraded section can surface
        *why* instead of silently rendering empty. Extracted after 15+ apps
        hand-rolled the same try/except shape around ``call_app``.

        Usage::

            rows, err = await self.try_call_app("task", "list_all")
            if err:
                return {"available": False, "error": err}
        """
        try:
            return await self.call_app(app_id, method, **kwargs), ""
        except Exception as e:  # noqa: BLE001 — soft by contract
            return None, str(e)[:220] or e.__class__.__name__

    async def poll_call_app(
        self,
        app_id: str,
        method: str,
        *,
        terminal: "Callable[[dict], bool]",
        timeout_s: float,
        interval_s: float = 15.0,
        **call_kwargs,
    ) -> dict | None:
        """Repeatedly invoke ``call_app(app_id, method, **call_kwargs)`` until
        ``terminal(result)`` returns True or ``timeout_s`` elapses.

        Returns the terminal result (or the last non-terminal result on
        timeout, or ``None`` if every call raised). Exceptions during a
        single poll are swallowed — long-running cross-app polls (fix-agent
        runs, dogfood-agent verifies, sandbox-pool leases) routinely hit
        transient AttributeErrors mid-restart and would otherwise crash
        the caller. Three consumers today: fix-agent's verify wait,
        feature-pipeline's fix-agent + dogfood-agent pollers — extracted
        per CLAUDE.md rule 9.
        """
        import asyncio
        import time as _t

        deadline = _t.time() + timeout_s
        last: dict | None = None
        while _t.time() < deadline:
            await asyncio.sleep(interval_s)
            try:
                res = await self.call_app(app_id, method, **call_kwargs)
            except Exception:
                continue
            if not isinstance(res, dict):
                continue
            last = res
            if terminal(res):
                return res
        return last

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

    # --- Local file discovery (the `files` search domain) ---
    #
    # Helpers for apps that locate + open files on the user's machine via the
    # `files` search domain (Everything/mdfind/fd). Shared by the KB source-PDF
    # locator and cable-library's source-file re-link. Every scope check is
    # fail-closed against the configured allowed_roots.

    def files_search_roots(self) -> list:
        """Resolve the configured `files` allowed_roots (tokens + paths) as Paths."""
        from pathlib import Path

        cfg = self.kernel.config.get_section("capabilities.search.files") or {}
        notes_path = str(self.kernel.config.notes_path or "")
        repo = str(self.kernel.config.path.parent) if self.kernel.config.path else ""
        tokens = {"notes": notes_path, "vault": notes_path, "repo": repo}
        configured = cfg.get("allowed_roots") or []
        raw = [str(tokens.get(r, r)) for r in configured] if configured else [notes_path, repo]
        out = []
        for r in raw:
            if not r:
                continue
            try:
                out.append(Path(r).resolve())
            except (OSError, ValueError):
                continue
        return out

    def path_in_files_roots(self, p) -> bool:
        """True when `p` resolves inside a configured files-domain root (fail-closed)."""
        from pathlib import Path

        roots = self.files_search_roots()
        if not roots:
            return False
        try:
            rp = Path(p).resolve()
        except (OSError, ValueError):
            return False
        for root in roots:
            try:
                rp.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def files_domain_active(self) -> bool:
        """True when the `files` search domain is registered (config enabled + restart)."""
        try:
            return "files" in self.kernel.capability("search")._domains
        except Exception:
            return False

    async def open_local_file(self, path: str) -> dict:
        """Open a file in the user's default app. Returns {ok, path} | {ok: False, error}.

        Guards: not a public/remote daemon (os.startfile opens on the daemon
        HOST — fine in local/private mode where that's the user's own machine),
        path inside the files roots (resolve() defeats `..`/symlink escapes), no
        leading-dash basename (argv-flag safety), and the file must exist.
        """
        import os
        import sys
        from pathlib import Path

        path = str(path or "").strip()
        if not path:
            return {"ok": False, "error": "no path given"}
        if self.kernel.config.get("network.mode", "local") == "public":
            return {"ok": False, "error": "open-local is disabled on a public/remote daemon"}
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return {"ok": False, "error": "invalid path"}
        if not self.path_in_files_roots(resolved):
            return {"ok": False, "error": "path is outside the allowed file roots"}
        if resolved.name.startswith("-"):
            return {"ok": False, "error": "invalid file name"}
        if not resolved.is_file():
            return {"ok": False, "error": "file not found"}
        try:
            if sys.platform == "win32":
                os.startfile(str(resolved))  # noqa: S606 — user-directed local open
            else:
                import subprocess

                argv = (
                    ["open", "--", str(resolved)]
                    if sys.platform == "darwin"
                    else ["xdg-open", str(resolved)]
                )
                subprocess.Popen(argv)  # noqa: S603,S607
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": str(resolved)}

    async def relocate_file(self, stored: str, *, type: str = "") -> dict:
        """Resolve a stored file reference, re-finding it by name if it moved.

        `stored` may be an absolute path, a vault-relative path, or a bare
        filename. Returns:
          ``{exists: True,  resolved: <abs>, candidates: []}``  — found in place
          ``{exists: False, resolved: None, candidates: [<abs>, ...]}`` — gone;
              the files domain found same-named files at other locations on disk
        """
        from pathlib import Path

        stored = str(stored or "").strip()
        if not stored:
            return {"exists": False, "resolved": None, "candidates": []}
        p = Path(stored)
        resolved = None
        try:
            if p.is_absolute() and p.is_file():
                resolved = str(p.resolve())
            elif (self.vault_root / stored).is_file():
                resolved = str((self.vault_root / stored).resolve())
        except (OSError, ValueError):
            resolved = None
        if resolved:
            return {"exists": True, "resolved": resolved, "candidates": []}
        name = Path(stored).name
        ext = (type or Path(name).suffix.lstrip(".")).strip()
        stem = Path(name).stem or name
        try:
            hits = await self.search(stem, domain="files", type=ext, limit=20)
        except Exception:
            hits = []
        cands = [h.get("path", "") for h in (hits or []) if h.get("path")]
        return {"exists": False, "resolved": None, "candidates": cands}

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

    async def scoped_retrieve(
        self,
        query: str,
        *,
        scope_folders: tuple[str, ...] = ("10_Projects", "20_Areas", "30_Resources"),
        max_scopes: int = 3,
        scoped_max_notes: int = 300,
        top_k: int = 3,
        min_score: float = 0.30,
        char_cap: int = 1000,
        router: str = "deterministic",
        extra_scopes: "list | None" = None,
        min_ability: str | None = None,
    ) -> "ScopedResult":
        """Two-tier scoped retrieval — the fast L0/L2 'scan a menu, drill the few'
        applied to vault content.

        Pick 1-3 relevant scopes (folder subtree and/or tag) from a coarse interest
        profile, then run an embedding search over **only that narrow slice**. Returns
        a :class:`~emptyos.sdk.scoped_retrieval.ScopedResult`; only ``result.usable``
        (``tier == "scoped"`` with snippets) is answerable on its own — every other
        tier signals the caller to use the whole-vault path.

        Knows no app and reads no config (Rule 15): the caller passes tuned kwargs.
        ``router`` ∈ ``deterministic`` (default, no LLM — the whole point is *fast*),
        ``llm`` (one ``select()`` over a scope menu), ``off`` (force whole-vault).
        ``extra_scopes`` is prepended (seam for e.g. a pinned-project hint).
        """
        from emptyos.sdk.scoped_retrieval import (
            ScopedResult,
            select_scopes_deterministic,
            scope_menu,
            parse_scope_key,
        )
        from emptyos.sdk.utils import strip_frontmatter

        if router == "off" or not self.embeddings_available:
            return ScopedResult(tier="no-embeddings")

        # ── Tier 0: scope routing (cheap, metadata only) ──
        # Build an UNCAPPED profile: scope routing is a pure membership test, so
        # (unlike vault_interest_profile's 30-name token-economy cap) it must see
        # every project/area name or a late-sorted folder is invisible to routing.
        root = self.vault_root

        def _subdirs(folder: str) -> list[str]:
            p = root / folder
            if not (p.exists() and p.is_dir()):
                return []
            try:
                return [d.name for d in p.iterdir()
                        if d.is_dir() and not d.name.startswith((".", "_"))]
            except OSError:
                return []

        profile: dict = {_interest_folder_key(f): _subdirs(f) for f in scope_folders}
        vi = self.kernel.services.get_optional("vault_index")
        if vi is not None and hasattr(vi, "tag_counts"):
            try:
                tc = vi.tag_counts() or {}
                profile["tags"] = [[t, c] for t, c in
                                   sorted(tc.items(), key=lambda x: -x[1])[:60]]
            except Exception:
                profile["tags"] = []
        else:
            profile["tags"] = []
        scopes: list = list(extra_scopes or [])
        if router == "llm":
            menu = scope_menu(profile)
            if len(menu) > 1:
                pick = await self.select(
                    f"Which slice of the vault best answers this question?\n{query}",
                    menu, default="__all__", min_ability=min_ability,
                )
                sc = parse_scope_key(pick)
                if sc is not None:
                    scopes.append(sc)
        else:
            scopes.extend(select_scopes_deterministic(query, profile, max_scopes=max_scopes))
        # dedup + cap
        seen: set = set()
        scopes = [s for s in scopes if not (s.key() in seen or seen.add(s.key()))][:max_scopes]
        if not scopes:
            return ScopedResult(scopes=[], tier="no-scope")

        # ── Tier 1: gather the scoped slice ──
        # Per-scope cap: a scope that is itself too broad (e.g. a whole top-level
        # folder) is *skipped*, not fatal — so a narrow project/tag scope alongside
        # it still drives a fast answer instead of the whole retrieval bailing.
        rows: dict[str, dict] = {}
        for s in scopes:
            srows = self.vault_query(tags=s.tags, folder=s.folder)
            if len(srows) > scoped_max_notes:
                continue
            for r in srows:
                p = r.get("path")
                if p:
                    rows.setdefault(p, r)
        n = len(rows)
        if n == 0:
            # every scope was empty or individually too broad
            return ScopedResult(scopes=scopes, n_candidates=0, tier="empty-slice")
        if n > scoped_max_notes:
            # Reading thousands of bodies is slow + yields a false-confident Tier-1;
            # the whole-vault path is already optimized for breadth.
            return ScopedResult(scopes=scopes, n_candidates=n, tier="scope-too-broad")

        items = []
        for p, r in rows.items():
            body = strip_frontmatter(self.vault_read_at(p))
            if not body.strip():
                continue
            items.append({
                "path": p,
                "name": (r.get("name") or p.replace("\\", "/").split("/")[-1]),
                "text": body[:1500],
            })
        if not items:
            return ScopedResult(scopes=scopes, n_candidates=n, tier="empty-slice")

        # ── Tier 1: semantic search over the scoped slice only ──
        index = await self.embedding_index(items, text_fn=lambda it: it["text"])
        hits = await index.search(query, top_k=top_k, min_score=min_score)
        snippets = [
            {"path": it["path"], "name": it["name"], "text": it["text"][:char_cap], "score": sc}
            for it, sc in hits
        ]
        return ScopedResult(
            snippets=snippets,
            scopes=scopes,
            top_score=(hits[0][1] if hits else 0.0),
            n_candidates=n,
            tier="scoped",
        )

    async def emit(self, event_type: str, data: dict | None = None):
        """Emit an event from this app."""
        await self.kernel.events.emit(event_type, data or {}, source=self.manifest.id)

    async def proactive_notify(
        self,
        kind: str,
        text: str,
        *,
        urgency: str = "normal",
        dedup_key: str | None = None,
        channels: list[str] | None = None,
        link: dict | None = None,
        priority: str = "info",
    ) -> dict:
        """Propose a proactive nudge TO THE USER, routed through the shared gate.

        The gate (``emptyos.sdk.proactive``) enforces restraint — master-enabled,
        per-kind mute, quiet hours, a daily cap, a min gap between nudges, and
        dedup — so the system never trains the user to ignore it. Only delivered
        nudges reach a channel.

        Channels:
          - ``"voice"`` emits ``proactive:announce`` → the hands-free overlay
            speaks it when running (silently dropped when no one's listening).
          - ``"notify"`` sends via the notifications plugin (vault + optional
            Telegram) — the durable record.

        Ships **dark**: policy ``enabled`` defaults False, so nothing delivers
        until the proactive app's master toggle is on. This is for inbound-to-user
        nudges only (reversible/internal → auto-runs once enabled); outbound to a
        third party is a different verb that stays human-gated and never routes here.

        Returns ``{delivered: bool, reason: str, channels: [...]}``.
        """
        from datetime import datetime

        from emptyos.sdk import proactive as _pro

        kind = (kind or "general").strip()
        text = (text or "").strip()
        if not text:
            return {"delivered": False, "reason": "empty-text", "channels": []}

        root = self.kernel.config.data_dir
        now = time.time()
        # Gate + state mutation under a lock so two concurrent nudges can't both
        # pass a cap/gap check against stale state.
        async with self.write_lock("proactive-dispatch"):
            policy = _pro.load_policy(root)
            state = _pro.load_state(root)
            decision = _pro.decide(
                policy, state, kind=kind, dedup_key=dedup_key,
                urgency=urgency, now=now, requested_channels=channels,
            )
            if decision.deliver:
                _pro.record_sent(state, kind, dedup_key, now)
                _pro.save_state(root, state)

        entry = {
            "ts": now,
            "iso": datetime.now(UTC).isoformat(timespec="seconds"),
            "kind": kind,
            "text": text[:280],
            "urgency": urgency,
            "delivered": decision.deliver,
            "reason": decision.reason,
            "channels": list(decision.channels) if decision.deliver else [],
            "source": self.manifest.id,
        }
        if not decision.deliver:
            _pro.append_log(root, entry)
            return {"delivered": False, "reason": decision.reason, "channels": []}

        # Deliver OUTSIDE the lock — a channel handler may recurse through the bus
        # and re-enter app code (lock-held-emit deadlock guard).
        for ch in decision.channels:
            try:
                if ch == "voice":
                    await self.emit(
                        "proactive:announce",
                        {"kind": kind, "text": text, "urgency": urgency, "link": link},
                    )
                elif ch == "notify":
                    svc = self.service("notifications")
                    if svc:
                        await svc.send(text, priority=priority, source=f"proactive:{kind}")
                elif ch == "companion":
                    # Push a soft nudge into the page-assistant rail via the
                    # realtime bus (RealtimeManager forwards every kernel event
                    # to /ws; the rail subscribes when its proactive flag is on).
                    await self.emit(
                        "proactive:companion",
                        {"kind": kind, "text": text, "urgency": urgency,
                         "link": link, "source": self.manifest.id},
                    )
            except Exception:
                pass  # a dead channel must never break the others or the caller

        _pro.append_log(root, entry)
        return {"delivered": True, "reason": "ok", "channels": list(decision.channels)}

    async def proactive_notify_or_raw(
        self,
        kind: str,
        text: str,
        *,
        dedup_key: str | None = None,
        priority: str = "info",
        urgency: str = "normal",
        source: str | None = None,
    ) -> dict:
        """``proactive_notify()`` with a safety net for migrating an EXISTING
        pusher onto the gate.

        A ``"disabled"`` verdict (master toggle off, the default) falls back
        to a raw ``notifications`` send so a pusher users already relied on
        never goes silently dark before the gate is turned on. Only a real
        suppression (quiet-hours/cap/dedup) is honored as-is.

        Use this when migrating a pusher that worked before the gate
        existed (reminders, billing budget alerts, ...). A brand-new nudge
        source should call ``proactive_notify()`` directly — nothing
        delivered before it either, so "disabled" is a legitimate no-op,
        not a regression.
        """
        result = await self.proactive_notify(
            kind, text, urgency=urgency, dedup_key=dedup_key, priority=priority,
        )
        if not result.get("delivered") and result.get("reason") == "disabled":
            notif = self.service("notifications")
            if notif:
                await notif.send(text, priority=priority, source=source or self.manifest.id)
        return result

    # --- Proposed-action helpers (review-gate paradigm) ---

    async def propose_action(
        self,
        *,
        app: str,
        method: str,
        args: dict | None = None,
        source_actor: dict | None = None,
        room_id: str = "",
    ) -> dict:
        """File a pending action via the rooms review gate.

        Implements the proposed-action paradigm (`.claude/rules/proposed-action.md`)
        for apps that want to surface a state change for explicit user review
        rather than auto-applying it. The action lands in the global pending
        dashboard (sidebar ⏳ N badge) and is applied via the normal
        ``apply_pending`` endpoint on user click.

        Returns the saved action dict (carries an ``id`` for follow-up). Raises
        if the rooms app is not loaded — propose_action assumes the review-gate
        plumbing is present, which is true on every standard EmptyOS install
        (rooms is a core app).

        Free-form / irreversible verbs (vault writes, `publish.deploy`,
        outbound messages) are **not** autopilot-eligible regardless of grants
        (`.claude/rules/autopilot-grants.md`) — the user always clicks Apply.
        """
        actor = source_actor or {"type": "app", "id": self.manifest.id}
        return await self.call_app(
            "rooms",
            "save_pending_action",
            app=app,
            method=method,
            args=args or {},
            source_actor=actor,
            room_id=room_id,
        )

    async def propose_kb_note(
        self,
        *,
        kind: str,
        title: str,
        body: str = "",
        domain: str = "",
        topic: str = "",
        references: list[str] | None = None,
        related: list[str] | None = None,
        source: str = "",
        author: str = "",
        room_id: str = "",
    ) -> dict:
        """Propose a KB note for user-reviewed creation.

        Thin wrapper around :meth:`propose_action` for the common case of
        promoting raw content (video digests, book highlights, chat
        transcripts, capture inbox items) into the curated KB. Each call
        creates one pending action targeting ``kb.create_note``; the user
        reviews the rendered note in the pending dashboard and clicks Apply
        to write it to the vault.

        ``author`` (when set) flows through to the note's frontmatter so the
        authorship boundary is recorded — pass ``"ai"`` for machine-extracted
        notes (`.claude/rules/authorship-boundary.md`).

        Reference implementation in :mod:`apps.video-digest`. New producers
        (reader, capture, conversation-digest, media) wire to this same
        helper rather than reinventing the extraction surface.
        """
        return await self.propose_action(
            app="kb",
            method="create_note",
            args={
                "kind": kind,
                "title": title,
                "body": body,
                "domain": domain,
                "topic": topic,
                "references": list(references or []),
                "related": list(related or []),
                "source": source,
                "author": author,
            },
            room_id=room_id,
        )

    async def propose_kb_extractions(
        self,
        summary: str,
        *,
        source_ref: str,
        system: str | None = None,
        content_label: str = "summary",
        max_candidates: int = 6,
        author: str = "ai",
        room_id: str = "",
    ) -> list[dict]:
        """Distil a think-summarised text into review-gated KB-note proposals.

        The shared extract→parse→validate→propose loop: ask the model for KB
        candidates, parse the JSON array, and file each valid one via
        :meth:`propose_kb_note` (so every candidate becomes a pending action the
        user Applies or Rejects — never auto-applied; KB writes are free-form
        content and non-grantable per ``.claude/rules/autopilot-grants.md``).

        Extracted from ``apps.video-digest`` on its second consumer
        (``rooms`` distillation) per CLAUDE.md rule 9. Best-effort by contract:
        a malformed LLM response yields ``[]`` rather than raising — callers
        treat KB candidates as a downstream nice-to-have, not load-bearing.

        ``source_ref`` is a vault link string (e.g. ``"[[2026-06-09-room-foo]]"``)
        stamped on each note's ``source:``. ``author`` defaults to ``"ai"``.
        Returns the list of saved pending-action dicts (each carries an ``id``).
        """
        from emptyos.sdk.utils import parse_llm_json

        prompt = (
            f"Here is a {content_label}. Extract durable KB-note candidates.\n\n"
            f"{content_label.upper()}:\n{summary}\n"
        )
        raw = await self.think(
            prompt,
            system=system or DEFAULT_KB_EXTRACTION_SYSTEM,
            domain="text",
            temperature=0.3,
            max_tokens=3000,
        )
        candidates = parse_llm_json(raw, fallback=[]) or []
        if not isinstance(candidates, list):
            return []
        proposed: list[dict] = []
        for c in candidates:
            if len(proposed) >= max_candidates:
                break
            if not isinstance(c, dict):
                continue
            kind = (c.get("kind") or "").strip().lower()
            title = (c.get("title") or "").strip()
            body = (c.get("body") or "").strip()
            if not kind or not title or not body:
                continue
            try:
                action = await self.propose_kb_note(
                    kind=kind,
                    title=title,
                    body=body,
                    domain=(c.get("domain") or "").strip(),
                    topic=(c.get("topic") or "").strip(),
                    source=source_ref,
                    author=author,
                    room_id=room_id,
                )
            except Exception:
                continue
            proposed.append(action)
        return proposed

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

    # --- Inter-agent mailbox (durable agent→agent handoff) ---
    # Distinct from emit_assignment (a people workload *index*): this is a
    # durable, addressed inbox — deposit a work-item for a specific agent,
    # delivered the next time that agent runs. See emptyos/sdk/agent_mailbox.py.

    def send_to_agent(
        self,
        recipient: str,
        *,
        kind: str = "",
        subject: str = "",
        payload: "dict | None" = None,
    ) -> "dict | None":
        """Durably deposit a work-item into ``recipient``'s inbox (an opaque
        agent id — a staff agent id, a rooms participant id). Returns the saved
        message, or None fail-soft. Sender is stamped as this app's id."""
        from emptyos.sdk.agent_mailbox import AgentMailbox

        return AgentMailbox(self.kernel.config.data_dir).enqueue(
            recipient, sender=self.manifest.id, kind=kind, subject=subject, payload=payload,
        )

    def agent_inbox(self, recipient: str | None = None) -> list[dict]:
        """Pending (undelivered) messages for ``recipient`` — defaults to this
        app's own id. The consumer drains this when it next runs."""
        from emptyos.sdk.agent_mailbox import AgentMailbox

        return AgentMailbox(self.kernel.config.data_dir).list_pending(
            recipient or self.manifest.id
        )

    def agent_inbox_ack(self, msg_id: str, recipient: str | None = None) -> bool:
        """Mark a message delivered (kept on disk for audit) after processing it."""
        from emptyos.sdk.agent_mailbox import AgentMailbox

        return AgentMailbox(self.kernel.config.data_dir).mark_delivered(
            recipient or self.manifest.id, msg_id
        )

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

    def data_subdir(self, *parts: str) -> Path:
        """Ensure and return a subdirectory under this app's ``data_dir``.

        ``self.data_subdir("pending")`` → ``<data_dir>/pending`` (created if
        absent). Varargs nest: ``self.data_subdir("audio", session_id)``.
        Replaces the ``d = self.data_dir / "x"; d.mkdir(parents=True,
        exist_ok=True); return d`` boilerplate that recurred ~27× across apps.

        Not for: paths outside ``data_dir`` (e.g. ``data/store/…`` that
        deliberately bypasses ``apps/<id>/``) — build those explicitly.
        """
        d = self.data_dir.joinpath(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @cached_property
    def _write_locks(self) -> dict[str, asyncio.Lock]:
        return {}

    def write_lock(self, key: str) -> asyncio.Lock:
        """Per-key lazy asyncio.Lock for serialising read-modify-write of a
        shared resource (file, vault note, JSON store).

        Without this, a user POST racing with a reactor handler can both read
        the pre-write content and have the later write wipe the earlier
        writer's change. Wrap the read-modify-write block in
        ``async with self.write_lock(key):``.

        Pick a stable, unique key per logical unit of isolation: a date
        (``"daily:2026-05-25"``), a vault path (``"note:30_Resources/.../foo.md"``),
        a slug, an id. Different keys → different locks → no contention.

        Reentrancy: asyncio.Lock is NOT reentrant. Don't call a method that
        re-acquires the same key under the lock; emit events / call_app via
        ``asyncio.create_task`` if you need to fire-and-forget from inside.

        Consumers (Rule 9 — extracted 2026-05-25 on 2nd-consumer signal):
          - apps/journal/  — keyed by date (daily-note read-modify-write)
          - apps/learn/    — keyed by source path (reader-note append) + "srs"
        """
        lock = self._write_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[key] = lock
        return lock

    def note_lock(self, rel_path: str | Path) -> asyncio.Lock:
        """Kernel-wide lock for one vault note, shared across app instances.

        Callers may pass an absolute vault path or a vault-relative path; both
        normalize to the same slash-separated key. If VaultIndex is not
        mounted (unit tests / no-vault mode), preserve safe per-app behaviour
        via ``write_lock``.
        """
        path = Path(rel_path)
        if path.is_absolute():
            try:
                path = path.resolve().relative_to(self.vault_root.resolve())
            except (OSError, ValueError):
                pass
        rel = str(path).replace("\\", "/").lstrip("/")
        index = self.kernel.services.get_optional("vault_index")
        if index is not None:
            return index.note_lock(rel)
        return self.write_lock(f"note:{rel}")

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

    def _resolved_root(self) -> Path:
        """Root used by safe_repo_path / repo_rel. Defaults to repo_root;
        apps with a configurable path override set ``self._root`` in setup()
        and that takes precedence (e.g. ``[apps.repo] path = "..."``)."""
        return getattr(self, "_root", None) or self.repo_root

    def safe_repo_path(self, rel: str) -> Path | None:
        """Resolve ``rel`` under :py:meth:`_resolved_root` with a traversal
        guard. Returns the resolved Path on success or None when ``rel``
        escapes the root (or the resolution itself errors).

        Single source of truth for the path-safety pattern duplicated across
        apps that work with the repo or a configurable subtree: apps/repo,
        apps/code, apps/rooms' edit-prep helpers.
        """
        root = self._resolved_root()
        if not rel:
            return root
        try:
            candidate = (root / rel).resolve()
        except (OSError, ValueError):
            return None
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def repo_rel(self, p) -> str:
        """Return ``p`` as a forward-slash, root-relative string (Windows-safe).
        Falls back to a forward-slashed copy of the input when ``p`` lies
        outside the root — keeps the API total even on degenerate inputs."""
        root = self._resolved_root()
        try:
            return str(Path(p).resolve().relative_to(root)).replace("\\", "/")
        except (ValueError, OSError):
            return str(p).replace("\\", "/")

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

    async def companion_context(self) -> str | None:
        """Live one-app context for the companion's 📍 focus mode.

        Default returns None (no focus context). An app overrides this to hand
        the companion brain a short, current summary of its own state — what the
        user is looking at right now — so a focused turn is grounded in live
        data without any per-app wiring on the companion side. The companion
        calls it (exception-guarded, budget-capped) only when the user has
        focused on this app and ``feature.companion-focus-context.enabled`` is
        set. Keep it short (a few lines), read-only, and non-sensitive — it's a
        prompt ingredient, not a data dump. See ``.claude/rules/app-ui-patterns.md``
        (4D timeline is the sibling read surface).
        """
        return None

    def is_public_request(self, request) -> bool:
        """True when this request should get the app's anonymous PUBLIC face.

        The reusable core of the "one app, two faces" pattern (see
        ``.claude/rules/public-app-pattern.md``). Any app with a public surface
        declares its read-only routes in ``[provides.web].public_routes`` and
        branches its handlers on this helper: public callers get a teaser /
        read-only view, authenticated owners get the full app — same code, same
        daemon. Auth-presence only, never user identity. Works whether the
        daemon is exposed directly or fronted by the control-plane proxy (which
        marks anonymous public traffic with ``X-EOS-Public: 1``). Reference
        consumer: ``apps/extension/english-learning/radio``.
        """
        try:
            auth_configured = bool(
                self.kernel.config.auth_token or self.kernel.config.login_password
            )
        except Exception:
            auth_configured = False
        return _request_is_public(request, auth_configured=auth_configured)

    def behavior_patterns(
        self,
        subject: str = "self",
        *,
        applies_to: str | None = None,
        min_confidence: float = 0.0,
        include_superseded: bool = False,
    ) -> list[dict]:
        """Evidence-linked behavior-pattern records for a subject (a *current read*).

        Serving helper for the behavior-pattern layer (see
        ``apps/personal/behavior-patterns`` + ``.claude/rules/three-natures-lens.md``).
        Each record is a vault note tagged ``behavior-pattern``; this reads them
        straight off the VaultIndex (frontmatter only — fast, no body parse) so a
        consumer never hard-depends on the behavior-patterns app being installed:
        **records absent ⇒ ``[]`` ⇒ byte-identical legacy behavior** (the
        regression contract). Mining/curation is owned by the app; reading is here.

        A pattern is a derived appearance, not a fixed fact — every record carries
        ``confidence`` + ``as_of`` + ``evidence`` and is surfaced as "how you tend
        to work right now", never as fixed identity. Consumers propose, never
        autofill. ``subject`` is ``"self"`` (v1) or ``"contact:<id>"`` (phase 2).

        ``applies_to`` filters to records that name a consumer (e.g. ``"work-fit"``
        / ``"comms"``); ``min_confidence`` drops weak reads. A record with
        ``superseded_by`` set is a resolved-conflict loser and is **excluded** by
        default (consumers never read a stale read); pass
        ``include_superseded=True`` to see them (the app's own reconcile view).
        Sorted confidence-desc. Never raises — fail-soft to ``[]``.
        """
        out: list[dict] = []
        try:
            rows = self.vault_query(tags=["behavior-pattern"], subject=subject) or []
        except Exception:  # noqa: BLE001 — serving helper is best-effort
            return []
        for r in rows:
            p = r.get("properties", {}) or {}
            sb = p.get("superseded_by")
            superseded = sb.strip() if isinstance(sb, str) else ("" if sb is None else str(sb))
            if superseded and not include_superseded:
                continue
            at = p.get("applies_to") or []
            if isinstance(at, str):
                at = [s.strip() for s in at.split(",") if s.strip()]
            elif not isinstance(at, list):
                at = []
            if applies_to and applies_to not in at:
                continue
            try:
                conf = float(p.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            if conf < min_confidence:
                continue
            ev = p.get("evidence") or []
            if isinstance(ev, str):
                ev = [ev]
            slug = p.get("slug") or _slug_from_path(r.get("path", ""))
            out.append({
                "slug": slug,
                "subject": p.get("subject", subject),
                "pattern": (p.get("pattern") or "").strip(),
                "area": (p.get("area") or "").strip(),
                "signal": (p.get("signal") or "").strip() if isinstance(p.get("signal"), str) else "",
                "applies_to": at,
                "confidence": conf,
                "as_of": p.get("as_of", ""),
                "voice": (p.get("voice") or "").strip(),
                "evidence": [str(e) for e in ev],
                "superseded_by": superseded or "",
                "path": r.get("path", ""),
            })
        out.sort(key=lambda d: d["confidence"], reverse=True)
        return out

    async def embed_viz_into_note(
        self, note_rel: str, *, viz_id: str, mode: str = "snapshot", height=360,
    ) -> dict:
        """Embed an existing viz artifact into a vault note (marker + frontmatter).

        Shared note-side writer for the artifact-embed feature — KB / note /
        journal all call this (CLAUDE.md rule 9). The viz app owns the bytes:
        ``snapshot`` (default) bakes a frozen copy so the source artifact stays
        freely deletable; ``reference`` keeps a live pointer (``viz_ref_ids``)
        that gates the source's deletion. The body gets an ``eos:viz-embed``
        marker appended at end; the note view (``EOS_UI.renderMarkdownWithEmbeds``)
        substitutes it for a sandboxed iframe.

        Returns ``{ok, embed_id, mode, source_viz_id, shape, heavy, height}`` or
        ``{ok: False, error}``. Serialised under ``write_lock(note_rel)`` (notes
        are reactor-touched — the vault read-modify-write race rule); the emit is
        outside the lock.
        """
        res = await self.call_app("viz", "bake_embed", viz_id=viz_id, mode=mode, height=height)
        if not res.get("ok"):
            return res
        embed_id = res["embed_id"]
        source = res["source_viz_id"]
        rec = {
            "embed_id": embed_id,
            "mode": res["mode"],
            "source_viz_id": source,
            "shape": res.get("shape", ""),
            "heavy": bool(res.get("heavy")),
            "height": res.get("height", height),
            "ts": res.get("ts", ""),
        }
        marker = res["marker"]

        async with self.write_lock(note_rel):
            props = self.vault_get_properties(note_rel) or {}
            embeds = self.vault_decode_json(props.get("viz_embeds"), [])
            if not isinstance(embeds, list):
                embeds = []
            if any((e or {}).get("embed_id") == embed_id for e in embeds):
                return {"ok": False, "error": "this artifact is already embedded in the note"}
            updates = {"viz_embeds": self.vault_encode_json(embeds + [rec])}
            if res["mode"] == "reference":
                refs = props.get("viz_ref_ids") or []
                if isinstance(refs, str):
                    refs = [refs]
                if source not in refs:
                    refs = refs + [source]
                updates["viz_ref_ids"] = refs
            # Append the marker at end of file (frontmatter at top is untouched);
            # vault_update then rewrites the frontmatter + re-indexes the file
            # that now carries the marker, so view + index agree.
            full = self.vault_read_at(note_rel)
            if full and not full.endswith("\n"):
                full += "\n"
            self.vault_write_at(note_rel, (full or "") + "\n" + marker + "\n")
            self.vault_update(note_rel, updates)

        await self.emit("note:embed_added", {
            "note": note_rel, "embed_id": embed_id,
            "mode": res["mode"], "source_viz_id": source,
        })
        return {"ok": True, **rec}

    async def render_pdf(self, markdown_text: str, out_path: str, *, style=None) -> "Path":
        """Render markdown to a styled PDF via the EmptyOS PDF markdown profile.

        ``out_path`` may be vault-relative (resolved against notes_path) or
        absolute; returns the absolute Path written. ``style`` may be a theme
        name (``"slate"``, ``"mono"``, ``"default"``…), a ``PdfStyle``, or None.
        Contract: ``.claude/rules/pdf-markdown.md``.

        Async because the renderer drives Playwright's **sync** API, which raises
        if started inside a running asyncio loop — so it runs in a worker thread.
        From a non-loop context (CLI, script, test) call
        ``emptyos.sdk.pdf.render_markdown_pdf`` directly instead.
        """
        import asyncio

        from emptyos.sdk.pdf import render_markdown_pdf

        p = Path(out_path)
        if not p.is_absolute():
            vault = self.kernel.config.notes_path
            if vault:
                p = vault / out_path
        return await asyncio.to_thread(render_markdown_pdf, markdown_text, p, style=style)

    def memory_audit(
        self,
        *,
        stale_days: int | None = None,
        budget: int = 20,
        memory_tags: tuple[str, ...] | None = None,
    ) -> dict:
        """Read-only fidelity audit over machine-touched memory (``docs/MEMORY.md`` §8).

        Scoped (invariant #5 — only AI-authored/inferred notes + curated memory
        corpora; the hand-written vault is exempt), pure-derived (#7), and
        budgeted. **Never writes** — surfaces the fidelity dial (% trusted) plus
        the top-``budget`` stale/unconfirmed claims for the caller to propose via
        the review gate. The only O(N) path; intended to run on a schedule, not
        per request.
        """
        from datetime import datetime

        from emptyos.sdk.attested_memory import (
            DEFAULT_MEMORY_TAGS,
            DEFAULT_STALE_DAYS,
            fidelity_audit,
        )

        tags = frozenset(memory_tags) if memory_tags else DEFAULT_MEMORY_TAGS
        # Gather the scoped candidate set without touching the index internals:
        # each memory tag + AI-authored notes. Dedupe by path.
        seen: dict[str, dict] = {}
        for t in tags:
            for r in self.vault_query(tags=[t]):
                seen[r["path"]] = r
        for a in ("ai", "both"):
            for r in self.vault_query(author=a):
                seen[r["path"]] = r
        report = fidelity_audit(
            list(seen.values()),
            now=datetime.now(UTC).date(),
            stale_days=DEFAULT_STALE_DAYS if stale_days is None else stale_days,
            budget=budget,
            memory_tags=tags,
        )
        return report.to_dict()

    # --- 4D Timeline (past / future / now) ---
    # See .claude/rules/app-ui-patterns.md § "4D Timeline Panel" and
    # .claude/rules/time-dimension.md for the paradigm. Surface: a single
    # entity has a story (past), commitments (future), and current state
    # (now); detail views should render all three, not just now.

    async def git_log(self, rel_path: str, limit: int = 20) -> list[dict]:
        """Git log for a path relative to the vault root.

        Returns [{hash, ts, author, subject}, ...] newest first. Empty list
        if git is unavailable, the vault isn't a git repo, or the file has
        no history. Fail-soft — never raises.
        """
        vault = self.kernel.config.notes_path
        if not vault:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "log", f"-{int(limit)}", "--format=%H|%aI|%an|%s", "--", rel_path,
                cwd=str(vault),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except (FileNotFoundError, asyncio.TimeoutError, OSError, Exception):
            return []
        rows: list[dict] = []
        for line in (out or b"").decode("utf-8", errors="replace").splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                rows.append(
                    {"hash": parts[0], "ts": parts[1], "author": parts[2], "subject": parts[3]}
                )
        return rows

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

    async def compose_message(
        self,
        kind: str,
        channel: str,
        context: dict,
        *,
        tone: str = "warm",
        freeform: str = "",
        variants: bool = False,
        temperature: float = 0.5,
        voice_rules: list[str] | None = None,
    ) -> dict:
        """Generate a professional / networking message draft.

        Thin wrapper over ``emptyos.sdk.compose.propose_message`` that injects
        ``self.think``. ``context`` is an ordered ``{human label: value}`` dict
        the calling app builds from its vault note (the app owns the mapping;
        the engine stays grounding-agnostic). ``voice_rules`` (optional) are the
        user's own voice directives — the caller passes
        ``[r["voice"] for r in self.behavior_patterns("self", applies_to="comms")]``
        when its pattern-voice feature is on. Drafting is reversible/internal, so
        there is no send or consent gate here — the caller decides what to do with
        the returned text. See ``.claude/rules`` / docs/compose.

        Returns ``{ok, kind, channel, tone, subject, body, why, variant}``.
        """
        from emptyos.sdk.compose import propose_message

        async def _think_fn(system: str, user: str) -> str:
            return await self.think(user, system=system, domain="text", temperature=temperature)

        return await propose_message(
            kind, channel, context,
            think_fn=_think_fn, tone=tone, freeform=freeform, variants=variants,
            voice_rules=voice_rules,
        )

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

    # ── Think family (extracted to base_app_think.py) ──
    _context_pack_enabled     = _think._context_pack_enabled
    _maybe_pack_think_context = _think._maybe_pack_think_context
    think                     = _think.think
    last_provenance           = _think.last_provenance
    model_ability             = _think.model_ability
    ability_meets             = _think.ability_meets
    _apply_localization       = _think._apply_localization
    _localize_target          = _think._localize_target
    _finalize_think           = _think._finalize_think
    think_safe                = _think.think_safe
    _think_pinned_provider    = _think._think_pinned_provider
    think_pinned              = _think.think_pinned
    _think_with_provider      = _think._think_with_provider
    think_stream              = _think.think_stream
    think_compare             = _think.think_compare
    select                    = _think.select
    suggest_field             = _think.suggest_field
    think_cached              = _think.think_cached

    # ── Media/serving family (extracted to base_app_media.py) ──
    serve_shared_audio = _media.serve_shared_audio
    save_audio_upload  = _media.save_audio_upload
    serve_data_file    = _media.serve_data_file
    serve_audio_file   = _media.serve_audio_file
    save_calculation   = _media.save_calculation
    save_report_note   = _media.save_report_note
    list_calculations  = _media.list_calculations

    # ── Context/memory family (extracted to base_app_context.py) ──
    bus_context      = _ctx.bus_context
    bus_index        = _ctx.bus_index
    bus_menu         = _ctx.bus_menu
    bus_assemble     = _ctx.bus_assemble
    recall           = _ctx.recall
    _episodic_store  = _ctx._episodic_store
    remember_episode = _ctx.remember_episode
    recall_episodes  = _ctx.recall_episodes
    app_link         = _ctx.app_link
    query            = _ctx.query
    query_notes      = _ctx.query_notes
    timeline         = _ctx.timeline

    # ── Vault family (extracted to base_app_vault.py) ──
    vault_config                = _vault.vault_config
    vault_config_path           = _vault.vault_config_path
    vault_root                  = _vault.vault_root # @property
    vault_dir                   = _vault.vault_dir # @cached_property
    vault_path                  = _vault.vault_path
    vault_rel                   = _vault.vault_rel
    vault_rel_if_exists         = _vault.vault_rel_if_exists
    vault_read                  = _vault.vault_read
    vault_write                 = _vault.vault_write
    vault_list                  = _vault.vault_list
    vault_read_at               = _vault.vault_read_at
    vault_write_at              = _vault.vault_write_at
    vault_query                 = _vault.vault_query
    vault_interest_profile      = _vault.vault_interest_profile
    _infer_lifecycle            = _vault._infer_lifecycle # @staticmethod
    vault_update                = _vault.vault_update
    vault_encode_json           = _vault.vault_encode_json # @staticmethod
    vault_decode_json           = _vault.vault_decode_json # @staticmethod
    vault_create_note           = _vault.vault_create_note
    vault_append_section        = _vault.vault_append_section
    vault_set_section           = _vault.vault_set_section
    vault_get_properties        = _vault.vault_get_properties
    vault_tags                  = _vault.vault_tags
    vault_trust                 = _vault.vault_trust
    vault_confirm               = _vault.vault_confirm
    vault_force_index           = _vault.vault_force_index
    vault_sections              = _vault.vault_sections
    vault_read_section          = _vault.vault_read_section
    vault_read_body             = _vault.vault_read_body
    vault_set_body              = _vault.vault_set_body
    vault_reconcile             = _vault.vault_reconcile
    vault_enrich                = _vault.vault_enrich
    vault_project_list          = _vault.vault_project_list
    vault_project_create        = _vault.vault_project_create
    vault_project_update        = _vault.vault_project_update
    vault_project_delete        = _vault.vault_project_delete
    vault_project_get           = _vault.vault_project_get
    vault_project_read_sidecar  = _vault.vault_project_read_sidecar
    vault_project_write_sidecar = _vault.vault_project_write_sidecar


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
