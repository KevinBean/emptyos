"""BaseApp — the class all EmptyOS apps inherit from."""

from __future__ import annotations

import inspect
import json
import time
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from emptyos.kernel import Kernel
    from emptyos.kernel.app_loader import AppManifest


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

    async def _emit_think_executed(self, event_data: dict, provider_name: str | None = None) -> None:
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

    async def think(self, prompt: str = "", domain: str | None = None, agent: str | None = None, *, task_shape: str | None = None, bucket: str | None = None, messages: list[dict] | None = None, cache: bool = False, cache_ttl_hours: int | None = None, **kwargs) -> str:
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
        """
        import asyncio

        if messages is not None:
            kwargs["messages"] = messages
        if not prompt and not messages:
            raise ValueError("think() requires either prompt= or messages=")

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
                return _hit

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
                    return result

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
                            return val
                    except (asyncio.TimeoutError, Exception):
                        continue

            # Domain-level override
            if domain:
                domain_provider = settings.get(f"think.domain.{domain}")
                if domain_provider:
                    result = await self._think_with_provider(domain_provider, prompt, domain, kwargs)
                    if result:
                        return result

        # Default: use capability chain
        t0 = time.monotonic()
        result = await self.kernel.capability("think").execute(
            prompt=prompt, domain=effective_domain,
            task_shape=task_shape, bucket=bucket, **kwargs,
        )
        latency = round((time.monotonic() - t0) * 1000)

        self._last_think_provider = {
            "provider": result.provider,
            "is_cloud": bool(getattr(result, "is_cloud", False)),
            "model": kwargs.get("model") or getattr(result, "model", None),
            "latency_ms": latency,
        }

        prompt_len = len(prompt) if prompt else sum(len(m.get("content", "")) for m in (messages or []))
        await self._emit_think_executed({
            "provider": result.provider,
            "is_cloud": getattr(result, "is_cloud", False),
            "domain": domain or "default",
            "app": app_id,
            "latency_ms": latency,
            "prompt_len": prompt_len,
        }, provider_name=result.provider)

        if _tc is not None and _cache_id is not None:
            _tc.put(
                _cache_db, _cache_id,
                prompt=prompt,
                system=kwargs.get("system"),
                model=kwargs.get("model"),
                response=result.value,
                app_id=app_id,
                ttl_hours=cache_ttl_hours,
            )

        return result.value

    def last_provenance(self) -> dict:
        """Return provenance metadata for the most recent think() call.

        Shape: {mode: 'local'|'cloud', provider: str, model: str|None,
                latency_ms: int}. Empty dict if no think() has run yet.

        Intended for API responses that render AI-authored content — pair with
        the frontend EOS_UI.provenance() helper to render the required chip
        per docs/FRONTEND-DESIGN-LANGUAGE.md §6.
        """
        meta = getattr(self, "_last_think_provider", None)
        if not meta:
            return {}
        return {
            "mode": "cloud" if meta.get("is_cloud") else "local",
            "provider": meta.get("provider") or "",
            "model": meta.get("model"),
            "latency_ms": meta.get("latency_ms"),
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

    async def _think_with_provider(self, provider_name: str, prompt: str, domain, kwargs) -> str | None:
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
                    await self._emit_think_executed({
                        "provider": provider_name,
                        "is_cloud": getattr(p, "is_cloud", False),
                        "domain": domain or "default",
                        "app": self.manifest.id,
                        "latency_ms": latency,
                        "prompt_len": prompt_len,
                        "routed_by": "settings",
                    }, provider_name=provider_name)
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
                        await self._emit_think_executed({
                            "provider": provider_name,
                            "is_cloud": getattr(p, "is_cloud", False),
                            "domain": domain or "default",
                            "app": self.manifest.id, "latency_ms": latency,
                            "prompt_len": prompt_len, "routed_by": "settings",
                        }, provider_name=provider_name)
                        return value
                    except Exception:
                        return None
        return None

    async def think_stream(self, prompt: str = "", domain: str | None = None, *, provider: str | None = None, task_shape: str | None = None, bucket: str | None = None, messages: list[dict] | None = None, **kwargs):
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

        prompt_len = len(prompt) if prompt else sum(len(m.get("content", "")) for m in (messages or []))
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
                prompt=prompt, domain=domain,
                task_shape=task_shape, bucket=bucket, **kwargs,
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

    async def think_cached(self, prompt: str, *, key, system: str | None = None,
                           domain: str | None = None, force_live: bool = False,
                           meta: dict | None = None, **kwargs) -> tuple[str, bool]:
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
        _llm_cache.cache_put(self, cache_id, prompt, system, response,
                             key=key, meta=meta or {})
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
                await self._emit_think_executed({
                    "provider": p.name,
                    "is_cloud": getattr(p, "is_cloud", False),
                    "domain": kwargs.get("domain") or "default",
                    "app": self.manifest.id,
                    "latency_ms": round((time.monotonic() - t0) * 1000),
                    "prompt_len": prompt_len,
                    "routed_by": "pinned",
                }, provider_name=p.name)
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

    async def draw(self, prompt: str, **kwargs) -> str:
        """Generate an image from text. Returns file path."""
        result = await self.kernel.capability("draw").execute(prompt=prompt, **kwargs)
        return result.value

    async def see(self, *, mode: str = "snapshot", **kwargs) -> str:
        """Capture an image from a camera. Returns file path."""
        result = await self.kernel.capability("see").execute(mode=mode, **kwargs)
        return result.value

    async def animate(self, prompt: str, *, image: str = "",
                      num_frames: int = 24, **kwargs) -> str:
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
            ".wav": "audio/wav", ".webm": "audio/webm", ".mp3": "audio/mpeg",
            ".mp4": "audio/mp4", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
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
        self, target: str, slot: str, **kwargs,
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

    async def search(self, query: str, **kwargs) -> list:
        """Ask the OS to search. Human remembers, or grep/search-engine finds."""
        result = await self.kernel.capability("search").execute(query=query, **kwargs)
        return result.value

    async def emit(self, event_type: str, data: dict | None = None):
        """Emit an event from this app."""
        await self.kernel.events.emit(
            event_type, data or {}, source=self.manifest.id
        )

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
        await self.emit(event, {
            "person": person_id,
            "item": item,
            "weight_hours": weight_hours,
            "role": role,
        })

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
                        (entry.get("ts", ""), entry.get("app", self.manifest.id),
                         json.dumps(entry, ensure_ascii=False, default=str)),
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
        from datetime import datetime, timezone
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
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

    def read_activity(self, limit: int = 50, filter_key: str = "", filter_val: str = "") -> list[dict]:
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

    # --- Vault Index (query + mutate vault notes via platform index) ---

    def vault_query(self, tags: list[str] | None = None, folder: str | None = None, **properties) -> list[dict]:
        """Query vault notes by tags and/or frontmatter properties.

        Returns list of {path, name, folder, ext, size, properties, tags}.
        Uses the platform VaultIndex (SQLite-backed, fast).
        Falls back to empty list if VaultIndex is unavailable.
        """
        vi = self.kernel.services.get_optional("vault_index")
        if not vi:
            return []
        return vi.find(tags=tags, folder=folder, **properties)

    def vault_update(self, rel_path: str, properties: dict):
        """Update frontmatter properties in a vault note and re-index.

        rel_path: relative to vault root (e.g. "20_Areas/Career/Job-Applications/foo/_app.md")
        properties: dict of key-value pairs to update in frontmatter
        """
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            vi.update_properties(rel_path, properties)

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
                return content[end + 3:].strip()
        return content.strip()

    # --- Vault Data Contracts ---

    def vault_reconcile(self, folder: str, expected_tags: list[str] | None = None,
                        expected_fields: list[str] | None = None) -> dict:
        """Check vault notes against expected structure. Read-only — reports gaps."""
        vi = self.kernel.services.get("vault_index")
        if not vi:
            return {"total": 0, "compliant": 0, "gaps": [], "folder": folder}
        return vi.reconcile(folder, expected_tags, expected_fields)

    def vault_enrich(self, rel_path: str, add_tags: list[str] | None = None,
                     defaults: dict | None = None) -> bool:
        """Add missing tags/defaults to a vault note. Safe — never overwrites."""
        vi = self.kernel.services.get("vault_index")
        if not vi:
            return False
        return vi.enrich(rel_path, add_tags, defaults)

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
        """Find methods with a specific decorator attribute."""
        return [
            (getattr(m, attr), m)
            for _, m in inspect.getmembers(self, predicate=inspect.ismethod)
            if hasattr(m, attr)
        ]

    def get_cli_methods(self) -> list[tuple[dict, Any]]:
        return self._get_decorated("_eos_cli")

    def get_web_methods(self) -> list[tuple[dict, Any]]:
        return self._get_decorated("_eos_web")

    def get_ws_methods(self) -> list[tuple[dict, Any]]:
        return self._get_decorated("_eos_ws")
