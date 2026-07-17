"""BaseApp think family — LLM calls, routing, selection, suggestions.

Extracted from base_app.py to keep the BaseApp spine atomic (P4 Atomic,
CLAUDE.md rule 4). Source split only — every function here is re-bound onto
``BaseApp`` in base_app.py's class body, so the public API (``self.think``,
``self.select``, …) is unchanged. Owns: the ``think`` / ``think_stream`` /
``think_pinned`` / ``think_cached`` call paths, output localization,
ability gating (``model_ability`` / ``ability_meets``), forced-choice
``select``, and vault-grounded ``suggest_field`` (with its prompt constants
``CONFIDENCE_ENVELOPE`` / ``SELECT_ENVELOPE`` / ``FIELD_SUGGEST_SYSTEM`` and
the pure ``_parse_suggestions``, which base_app.py re-exports for tests).

Spine pieces deliberately NOT here (used across families, reached via
``self``): ``_emit_think_executed`` (also used by ``pinned_execute``),
``_record_demand`` (also used by ``search`` / ``vault_query``), and the
class-level ``_STRUCTURED_SHAPES`` / ``_LOCALIZE_SUFFIX`` constants.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach (spine helpers via ``self``).
Do not import from ``.base_app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from emptyos.sdk.trace import stamp_trace

if TYPE_CHECKING:
    from .base_app import BaseApp  # noqa: F401 — for type hints only


# ─── Bind to BaseApp class as ────────────────────────────────────────
#   _context_pack_enabled     = _think._context_pack_enabled
#   _maybe_pack_think_context = _think._maybe_pack_think_context
#   think                     = _think.think
#   last_provenance           = _think.last_provenance
#   model_ability             = _think.model_ability
#   ability_meets             = _think.ability_meets
#   _apply_localization       = _think._apply_localization
#   _localize_target          = _think._localize_target
#   _finalize_think           = _think._finalize_think
#   think_safe                = _think.think_safe
#   _think_pinned_provider    = _think._think_pinned_provider
#   think_pinned              = _think.think_pinned
#   _think_with_provider      = _think._think_with_provider
#   think_stream              = _think.think_stream
#   think_compare             = _think.think_compare
#   select                    = _think.select
#   suggest_field             = _think.suggest_field
#   think_cached              = _think.think_cached
# Adding a new method here? Add a matching binding line in base_app.py.
# ─────────────────────────────────────────────────────────────────────


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

SELECT_ENVELOPE = (
    "{prompt}\n\nChoose exactly ONE of the following options:\n{menu}\n\n"
    'Respond with ONLY this JSON, no prose: {{"choice": "<one key exactly as written>"}}'
)

# System prompt for ``BaseApp.suggest_field`` — proposes candidate values to
# pre-fill ONE form input, grounded in the user's vault. The user always picks
# (or ignores) a suggestion, so these are seeds, not commitments.
FIELD_SUGGEST_SYSTEM = """You propose candidate values for a single form field, \
inspired by the user's own material. You receive a short instruction describing \
the field and (optionally) grounding drawn from their vault.

Return JSON only: {"suggestions": ["...", "...", ...]}. No prose, no code fences.

Each suggestion is a ready-to-use value for that one field — concise, specific,
and directly usable as typed. Vary them; do not return near-duplicates.

Do NOT:
- exceed the count requested
- return prose, explanations, numbering, or markdown around the values
- copy the grounding verbatim — riff on its themes, never reproduce a source line
- invent facts about the user; when unsure, stay generic rather than fabricate"""


def _parse_suggestions(raw, count: int = 5) -> list[str]:
    """Pure parse of an LLM suggestion reply into a clean, capped list[str].

    Accepts the documented ``{"suggestions": [...]}`` object, a bare JSON list,
    or a fenced variant (``parse_llm_json`` strips fences). Anything unparseable
    yields ``[]`` — the caller treats empty as "no suggestions", never an error.
    """
    from emptyos.sdk.utils import parse_llm_json

    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, list):
        data = raw
    else:
        data = parse_llm_json(raw if isinstance(raw, str) else "", fallback={})
    if isinstance(data, dict):
        items = data.get("suggestions")
        if not isinstance(items, list):
            # Tolerate a single-key object whose value is the list.
            items = next((v for v in data.values() if isinstance(v, list)), [])
    elif isinstance(data, list):
        items = data
    else:
        items = []
    out, seen = [], set()
    for it in items or []:
        s = str(it).strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
        if len(out) >= max(1, count):
            break
    return out


def _context_pack_enabled(self) -> bool:
    """Whether opt-in think-context packing is on for this app.

    Off unless a settings flag turns it on: ``context_pack.app.<id>`` (per
    app) or ``context_pack.enabled`` (global). The per-call ``context_pack=``
    kwarg is checked by the caller and ORs with this.
    """
    settings = self.kernel.services.get_optional("settings")
    if settings is None:
        return False
    if settings.get(f"context_pack.app.{self.manifest.id}"):
        return True
    return bool(settings.get("context_pack.enabled"))


async def _maybe_pack_think_context(
    self, prompt: str, context: list, context_pack_flag: bool
) -> str:
    """Compress caller-supplied context blocks; prepend to the prompt.

    Fail-open: returns ``prompt`` unchanged when disabled, when there's
    nothing to pack, or on any error. The prompt (user request) is never
    compressed — only the prepended reference context is. Runs the
    synchronous packer off the event loop (Async Boundary rule).
    """
    if not context:
        return prompt
    if not (context_pack_flag or self._context_pack_enabled()):
        return prompt
    try:
        from emptyos.context import ContextBlock, ContextBudget, pack_context

        blocks = [
            c if isinstance(c, ContextBlock) else ContextBlock(**c) for c in context
        ]
        store_root = self.kernel.config.data_dir / "context"
        result = await asyncio.to_thread(
            pack_context, blocks, ContextBudget(), profile="think", store_root=store_root
        )
        packed = (result.text or "").strip()
    except Exception:
        return prompt  # fail-open
    if not packed:
        return prompt
    return f"## Reference Context (compressed)\n{packed}\n\n{prompt}"


async def think(
    self,
    prompt: str = "",
    domain: str | None = None,
    agent: str | None = None,
    *,
    task_shape: str | None = None,
    bucket: str | None = None,
    min_ability: str | None = None,
    messages: list[dict] | None = None,
    provider: str | None = None,
    strict_provider: str | None = None,
    cache: bool = False,
    cache_ttl_hours: int | None = None,
    with_confidence: bool = False,
    confidence_threshold: float = 3.0,
    context_pack: bool = False,
    context: list | None = None,
    localize: bool | None = None,
    temperature: float | None = None,
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

    ``strict_provider=`` selects exactly one provider while still using the
    capability chain's availability, cloud-consent, outbound-scan, middleware,
    provenance, and billing paths. Unlike the legacy ``provider=`` pin, it
    never falls through to another provider and never bypasses cloud consent.

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

    Context Packing (opt-in, off by default): pass `context=[ContextBlock(...)]`
    (or dicts) carrying LARGE, low-risk material — a log, a json dump, search
    output — separately from `prompt`. When `context_pack=True` (or settings
    enable it per-app), those blocks are compressed and prepended to `prompt`
    as a delimited "Reference Context" section; `prompt` itself (the user
    request) is never compressed. This is lossy-final — a one-shot think has
    no loop to recover a `ctx_*` ref — so only hand over material the answer
    can survive losing the fine detail of. Fails open: any packing error
    sends the original context verbatim. See `docs/CONTEXT-PACKING.md`.
    """
    import asyncio

    if messages is not None:
        kwargs["messages"] = messages
    if not prompt and not messages:
        raise ValueError("think() requires either prompt= or messages=")
    if provider and strict_provider:
        raise ValueError("provider and strict_provider are mutually exclusive")

    # with_confidence: append a structured-output envelope to the
    # system prompt and parse the response as JSON. Inspired by
    # Demand-Driven Context — every think() call can self-rate, and
    # low-confidence answers feed the demand log. The caller gets back
    # a dict {answer, confidence, missing, assumed}; on parse failure
    # we fall back to {answer: raw, confidence: None} so existing
    # call sites that opt in never see an exception.
    if with_confidence:
        kwargs["system"] = (kwargs.get("system") or "") + CONFIDENCE_ENVELOPE

    # --- Output localization (born-in-language, not translated-after) ---
    # Applied here — before the cache key below — so en/zh outputs never
    # collide in the cache, and so it flows through every downstream path
    # (settings override, agent, capability chain). Skipped for
    # structured/code shapes so parsers never break. See _localize_target().
    self._apply_localization(kwargs, localize, domain, task_shape)

    # Transfer explicit temperature parameter to kwargs for downstream
    if temperature is not None and 'temperature' not in kwargs:
        kwargs['temperature'] = temperature

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
            # Either pin narrows which provider answers, so both must key the
            # cache — otherwise a pinned call reads back another provider's answer.
            provider=strict_provider or provider,
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

    # --- Context Packing (opt-in, fail-open) ---
    # Compress caller-supplied context blocks and prepend to the prompt.
    # The prompt (user request) is never touched. Runs off the event loop.
    if context:
        prompt = await self._maybe_pack_think_context(prompt, context, context_pack)

    effective_domain = domain
    settings = self.kernel.services.get_optional("settings")
    app_id = self.manifest.id

    if provider:
        result = await self._think_with_provider(provider, prompt, domain, kwargs)
        if result:
            return self._finalize_think(
                result,
                with_confidence=with_confidence,
                prompt=prompt,
                threshold=confidence_threshold,
            )

    if settings and not strict_provider:
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
        min_ability=min_ability,
        only_provider=strict_provider,
        **kwargs,
    )
    latency = round((time.monotonic() - t0) * 1000)

    self._last_think_provider = {
        "provider": result.provider,
        "is_cloud": bool(getattr(result, "is_cloud", False)),
        "model": kwargs.get("model") or getattr(result, "model", None),
        "latency_ms": latency,
        "under_powered": bool(getattr(result, "under_powered", False)),
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
        "under_powered": bool(meta.get("under_powered", False)),
        "citations": list(getattr(self, "_last_think_citations", [])),
    }


async def model_ability(self, domain: str = "text") -> dict:
    """Ability of the think model that *would* run for this app/domain now.

    Returns ``{ability, provider, model}`` where ability is
    ``weak|standard|strong`` — resolved through the same precedence as
    ``think()`` (app override → domain override → chain). Lets a feature
    check whether the active model is strong enough before offering itself.
    Falls back to ``{"ability": "standard"}`` if resolution fails.
    """
    from emptyos.capabilities.ability import classify, normalize

    try:
        settings = self.kernel.services.get_optional("settings")
        app_id = self.manifest.id
        think_cap = self.kernel.capability("think")
        # Mirror think()'s override precedence to find the provider name.
        name = ""
        if settings:
            ov = settings.get(f"think.app.{app_id}")
            if ov and "," not in str(ov):
                name = str(ov)
            else:
                chain = settings.get(f"think.app.{app_id}.providers")
                if chain:
                    name = str(chain).split(",")[0].strip()
                elif domain and settings.get(f"think.domain.{domain}"):
                    name = str(settings.get(f"think.domain.{domain}"))
        providers = think_cap.providers_for(domain=domain or None)
        chosen = None
        for p in providers:
            if name and getattr(p, "name", "") == name:
                chosen = p
                break
        if chosen is None and providers:
            chosen = providers[0]
        if chosen is None:
            return {"ability": "standard", "provider": "", "model": ""}
        model = getattr(chosen, "model", "") or ""
        override = getattr(chosen, "_ability", None)
        ability = normalize(override) if override else classify(chosen.name, model)
        return {"ability": ability, "provider": chosen.name, "model": model}
    except Exception:
        return {"ability": "standard", "provider": "", "model": ""}


async def ability_meets(self, min_ability: str, domain: str = "text") -> bool:
    """True when the active think model meets ``min_ability`` for ``domain``."""
    from emptyos.capabilities.ability import meets

    info = await self.model_ability(domain=domain)
    return meets(info.get("ability"), min_ability)


def _apply_localization(
    self, kwargs: dict, localize: bool | None, domain: str | None, task_shape: str | None
) -> None:
    """Append the output-language instruction to ``kwargs['system']`` in
    place, when the user picked a non-English ``ui.language`` and this is
    natural-language generation. No-op otherwise. Policy + guards live in
    ``_localize_target``; this is the shared apply step for think() and
    think_stream() so the instruction text exists in exactly one place."""
    lang = self._localize_target(localize, domain, task_shape)
    if not lang:
        return
    from emptyos.sdk.i18n import lang_name

    kwargs["system"] = (kwargs.get("system") or "") + self._LOCALIZE_SUFFIX.format(
        lang=lang_name(lang)
    )


def _localize_target(
    self, localize: bool | None, domain: str | None, task_shape: str | None
) -> str | None:
    """Resolve the language this think() should GENERATE in, or None.

    - ``localize=False`` → never (caller insists on English / source).
    - ``localize=True``  → the user's ``ui.language`` (bypasses the
      structural guards; the caller knows it wants prose).
    - ``localize=None``  → the global ``ui.localize_think`` policy
      (default off). Even when on, only natural-language shapes localize;
      JSON/code/classify stay English so parsers keep working.

    Returns ``None`` when ui.language is English or unset — so the common
    case is a couple of cheap dict lookups and out.
    """
    if localize is False:
        return None
    settings = self.kernel.services.get_optional("settings")
    if not settings:
        return None
    lang = settings.get("ui.language", "en") or "en"
    if lang == "en":
        return None
    if localize is True:
        return lang
    # localize is None → opt-in via global policy, with structural guards.
    raw = str(settings.get("ui.localize_think", "") or "").strip().lower()
    if raw not in ("1", "true", "yes", "on"):
        return None
    if (task_shape or "") in self._STRUCTURED_SHAPES:
        return None
    if domain not in (None, "", "text", "reason", "analysis"):
        return None
    return lang


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


async def _think_pinned_provider(
    self, provider: str, prompt: str = "", domain: str | None = None, **kwargs
) -> str | None:
    """Run think on EXACTLY ``provider`` — no chain fallback. Returns the
    reply text, or ``None`` if that provider is unavailable or errors, so the
    caller can branch on a clean miss (e.g. a fast-lane race that must not
    silently fall through to a slow provider).

    Distinct from the other two routing surfaces:
      - ``think()`` resolves a provider via settings/domain then the default
        chain (falls back on miss) — use when any capable provider will do.
      - ``think_stream(provider=...)`` pins the *streaming* path but also
        falls back to the chain if the pin is unavailable.
    ``think_pinned`` is the strict, non-streaming, single-provider variant.

    ``kwargs`` accepts ``messages=``, ``system=``, ``model=``, ``temperature=``
    etc., forwarded verbatim to the provider.
    """
    return await self._think_with_provider(provider, prompt, domain, kwargs)


async def think_pinned(
    self,
    first: str,
    prompt: str | None = None,
    domain: str | None = "text",
    *,
    providers: tuple[str, ...] | list[str] | None = None,
    timeout_s: float = 10.0,
    fallback_timeout_s: float | None = None,
    **kwargs,
) -> str | None:
    """Run a pinned non-streaming think call.

    With ``providers=None``, ``first`` is an exact provider name and there is
    no chain fallback. With ``providers=(...)``, ``first`` is the prompt;
    named providers are tried in order, then the default chain is used.
    Returns None when everything fails.

    For latency-sensitive bounded calls (routing, classification, quick
    summaries) where the default chain's lead provider is too slow —
    e.g. claude-cli regularly exceeds a 15s budget on `-p` calls.

    Pinned providers bypass the cloud-consent gate (same property as the
    ``think.app.<id>`` settings override), so **pin local providers only**
    unless the deployment has already consented to the cloud one. The
    fallback goes through ``self.think()`` and keeps full gate semantics.

    Extracted from the explore channel-router; ``apps/public/core/search``
    hand-rolls the same loop (with per-provider attribution it still
    needs) and can adopt this when next touched.
    """
    import asyncio

    if providers is None:
        return await self._think_with_provider(first, prompt or "", domain, dict(kwargs))

    prompt_text = first
    for prov in providers:
        try:
            val = await asyncio.wait_for(
                self._think_with_provider(prov, prompt_text, domain, dict(kwargs)),
                timeout=timeout_s,
            )
            if val is not None:
                return val
        except Exception:
            continue
    try:
        coro = self.think(prompt_text, domain=domain, **kwargs)
        if fallback_timeout_s:
            return await asyncio.wait_for(coro, timeout=fallback_timeout_s)
        return await coro
    except Exception:
        return None


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
    min_ability: str | None = None,
    messages: list[dict] | None = None,
    localize: bool | None = None,
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

    # Output localization — same rule as think() (born-in-language for
    # natural-language generation; skipped for structured shapes). This is
    # the conversational path (Aura, chat), where it matters most.
    self._apply_localization(kwargs, localize, domain, task_shape)

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
        event_data = stamp_trace(
            {
                "provider": used_provider or target_provider or "unknown",
                "is_cloud": is_cloud,
                "domain": domain or "default",
                "app": app_id,
                "latency_ms": round((time.monotonic() - t0) * 1000),
                "prompt_len": prompt_len,
                "streamed": True,
            }
        )
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
            min_ability=min_ability,
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


async def select(
    self,
    prompt: str,
    choices: "list[str] | dict[str, str]",
    *,
    domain: str | None = None,
    system: str | None = None,
    default: str | None = None,
    min_ability: str | None = None,
    **kwargs,
) -> str:
    """LLM-backed router — pick exactly one key from ``choices`` and return it.

    The named *Selector* primitive. Where ``[DO:app.verb]`` / ``[INTENT:...]``
    parse a verb out of free-text *generation*, ``select()`` does the inverse:
    force the model to choose **one branch from a closed set** so the caller
    drives deterministic control flow on the returned key. Use it for
    classification / routing — "which sub-handler", "which bucket", "refund
    vs escalate vs reply" — not for content generation.

    ``choices`` is either a list of keys (``["refund", "escalate", "reply"]``)
    or a ``{key: description}`` map; descriptions are shown to the model to
    disambiguate but the return value is always one of the **keys**.

    Never raises and never invents a key the caller didn't offer: on an
    unrecognised or unparseable response it returns ``default`` if given,
    else the first key. Pass ``system=`` for the routing persona/rules (per
    CLAUDE.md rule 12) and ``min_ability=`` to gate weak models off a
    nuanced routing decision (see ``.claude/rules/model-ability.md``).
    """
    if isinstance(choices, dict):
        keys = list(choices.keys())
        menu = "\n".join(f"- {k}: {v}" for k, v in choices.items())
    else:
        keys = list(choices)
        menu = "\n".join(f"- {k}" for k in keys)
    if not keys:
        raise ValueError("select() requires a non-empty choices set")
    fallback = default if default in keys else keys[0]

    envelope = SELECT_ENVELOPE.format(prompt=prompt, menu=menu)
    kwargs.setdefault("temperature", 0.1)
    try:
        raw = await self.think(
            envelope, domain=domain, system=system, min_ability=min_ability, **kwargs
        )
    except Exception:
        return fallback

    from emptyos.sdk.utils import parse_llm_json

    # think() normally returns a string, but a kwarg (e.g. with_confidence)
    # can make it return a dict — parse_llm_json would then .strip() a dict
    # and raise outside the try, violating the "never raises" contract.
    if isinstance(raw, dict):
        parsed = raw
    else:
        parsed = parse_llm_json(raw if isinstance(raw, str) else "", fallback={})
    pick = parsed.get("choice") if isinstance(parsed, dict) else None
    if pick in keys:
        return pick
    # Tolerate a bare-key answer or loose casing/whitespace before giving up.
    text = (raw if isinstance(raw, str) else str(pick or "")).strip()
    if text in keys:
        return text
    low = text.lower()
    for k in keys:
        if k.lower() == low:
            return k
    self._record_demand(kind="think", query=prompt[:500], result="no_match")
    return fallback


async def suggest_field(
    self,
    *,
    field: str,
    instruction: str,
    count: int = 5,
    vault_tags: list[str] | None = None,
    folder: str | None = None,
    title_only: bool = True,
    frontmatter_fields: list[str] | None = None,
    context: dict | None = None,
    domain: str = "text",
    temperature: float = 0.8,
    min_ability: str | None = None,
) -> list[str]:
    """Up to ``count`` AI-suggested values to pre-fill one form field,
    grounded in the user's vault.

    The shared backend of the ✨ field-suggest affordance
    (see ``.claude/rules/field-suggest.md``). ``instruction`` describes the
    field in the user's terms ("Propose interactive-fiction premises…");
    grounding is drawn from ``vault_query(tags=vault_tags, folder=folder)``.

    **Rule-19 safe by default** — ``title_only=True`` feeds the cloud only
    note titles plus the named ``frontmatter_fields`` (structured metadata),
    never note bodies. Set ``title_only=False`` only for a local-only model.

    ``context`` is a dict of sibling form-field values the model should
    respect (e.g. ``{"cefr": "C1", "lang": "English"}``). Returns a flat
    ``list[str]`` (empty + logged on any failure); never raises, so a UI
    affordance can call it without a try/except.
    """
    # 1. Gather grounding (metadata only unless title_only is False).
    ground_lines: list[str] = []
    try:
        rows = self.vault_query(tags=vault_tags, folder=folder) if (vault_tags or folder) else []
    except Exception:
        rows = []
    for r in rows[:40]:
        props = r.get("properties") or {}
        title = props.get("title") or (r.get("name") or "").rsplit(".", 1)[0]
        if not title:
            continue
        extra = ""
        for fkey in frontmatter_fields or []:
            val = props.get(fkey)
            if val:
                extra += f" · {fkey}: {val}"
        ground_lines.append(f"- {title}{extra}")

    # 2. Compose the user message.
    parts: list[str] = []
    if ground_lines:
        label = "titles only" if title_only else "notes"
        parts.append(f"Drawn from the user's vault ({label}):\n" + "\n".join(ground_lines))
    if context:
        ctx = "; ".join(f"{k}={v}" for k, v in context.items() if v not in (None, ""))
        if ctx:
            parts.append(f"Current form values: {ctx}")
    parts.append(instruction.strip())
    parts.append(
        f'Propose {count} value(s) for the "{field}" field. '
        'Return JSON only: {"suggestions": ["...", ...]}.'
    )
    user = "\n\n".join(parts)

    # 3. Think → parse → cap.
    try:
        raw = await self.think(
            user,
            system=FIELD_SUGGEST_SYSTEM,
            domain=domain,
            temperature=temperature,
            min_ability=min_ability,
        )
    except Exception as e:  # noqa: BLE001 — never propagate to a UI affordance
        self.log_warn(f"suggest_field({field}) failed: {e}")
        self._record_demand(kind="think", query=f"suggest:{field}"[:500], result="error")
        return []
    return _parse_suggestions(raw, count)


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
