"""OpenAI-compatible provider — works with any API that speaks the OpenAI format.

Covers: OpenAI, Ollama (with /v1/ endpoint), LM Studio, vLLM, llama.cpp, etc.
"""

from __future__ import annotations

import json
import os

import aiohttp

from emptyos.capabilities.providers._tool_capable import (
    AgentTurn,
    TextBlock,
    ToolCapableProvider,
    ToolUse,
    ToolUseBlock,
)


# Request keys `extra_body` may not set: each changes what kind of request this
# is, and the provider sets it on some paths only.
_SHAPE_KEYS = frozenset({"model", "messages", "stream", "stream_options", "tools", "tool_choice"})


def _cached_tokens(usage: dict) -> int:
    """Pull OpenAI's `prompt_tokens_details.cached_tokens` (subset of prompt_tokens).

    Returns 0 when the field is absent (non-OpenAI endpoints, older responses)
    or malformed. Cached prompt tokens bill at 50% of the normal input rate.
    """
    details = usage.get("prompt_tokens_details") or {}
    if not isinstance(details, dict):
        return 0
    try:
        return int(details.get("cached_tokens", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _reported_cost(usage: dict) -> float | None:
    """The USD cost the endpoint itself reports for this call, or None.

    OpenRouter puts the charged amount in `usage.cost`. It is the only price
    that is right for every model it routes. `PRICING` below is keyed by bare
    OpenAI model names, so a slug like "deepseek/deepseek-v4-flash" priced
    through it came out as $0, and billing and budget caps could not see the
    spend. Absent, non-numeric, negative or non-finite values mean "not
    reported", and the caller falls back to the table.

    Not handled: with OpenRouter BYOK (your own upstream key), `usage.cost` is
    only OpenRouter's fee and the model spend is billed to the upstream
    account. EmptyOS does not use BYOK.
    """
    value = usage.get("cost")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value < 0 or value != value or value == float("inf"):
        return None
    # Returned unrounded. Billing still rounds each add to 6 dp
    # (`round(cost + ?, 6)`), so a call under ~$0.0000005 is lost there; an Aura
    # turn on deepseek-v4-flash is ~$0.0003, far above that.
    return value


def _chat_messages(
    prompt: str,
    system: str,
    messages: list[dict] | None,
    images: list[str] | None = None,
) -> list[dict]:
    """Build an OpenAI-style messages list from either `messages` or `prompt`,
    prepending `system` when present and not already the first turn.

    When `images` is non-empty, the final user turn is rewritten as a
    multimodal content array with one text part + one image_url part per
    image. URLs may be http(s) or pre-encoded `data:` URLs — the provider
    sends them through verbatim.
    """
    if messages:
        msgs = list(messages)
        if system and not (msgs and msgs[0].get("role") == "system"):
            msgs = [{"role": "system", "content": system}] + msgs
    else:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})

    if images:
        # Rewrite the LAST user turn to multimodal content.
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                text = msgs[i].get("content") or ""
                parts: list[dict] = []
                if isinstance(text, str) and text:
                    parts.append({"type": "text", "text": text})
                elif isinstance(text, list):
                    # Already multimodal — preserve existing parts
                    parts.extend(text)
                for url in images:
                    if url:
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                msgs[i] = {"role": "user", "content": parts}
                break
    return msgs


# Model-name patterns whose host accepts OpenAI-style image_url parts (cloud
# OpenAI 4o/4.1/5.x + Ollama vision tags). Used so the provider chain can
# fall through to a vision-capable peer when the current model is text-only.
_VISION_MODEL_PATTERNS = (
    "gpt-4o", "gpt-4.1", "gpt-5", "o3", "o4",  # OpenAI
    "llava", "bakllava", "vision", "qwen2.5vl", "qwen2-vl",
    "llama3.2-vision", "llama-3.2-vision", "minicpm-v", "moondream",
    # Gemma is multimodal from 3 onward and the IT tags read document scans
    # well — measured 2026-08-15 on `gemma4:12b-it-qat`, which transcribed a
    # bilingual textbook page (headers, track numbers, tables) at ~1.8 s/page.
    # Omitting it made the provider raise "does not support vision" and fall
    # through, so a locally available vision model looked absent.
    "gemma3", "gemma4",
)


def model_supports_vision(model: str) -> bool:
    """Whether this model name is one of the multimodal families above.

    Public because it is the single place that judgement lives: the SDK's
    attachment layer asks it before sending an image (emptyos/sdk/attachments.py
    provider_reads_images), so a chat refuses with a clear message instead of
    the provider answering 400."""
    m = (model or "").lower()
    return any(pat in m for pat in _VISION_MODEL_PATTERNS)


_model_supports_vision = model_supports_vision   # pre-existing internal name


def _attach_ollama_images(msgs: list[dict], images: list[str] | None) -> None:
    """Add the bare base64 of each data URL to the final user turn's ``images`` field.

    Ollama's native ``/api/chat`` does not accept multimodal `content` arrays;
    instead each message carries an optional ``images: ["<base64>", ...]``
    sibling field. http/https URLs are skipped — Ollama only reads the
    bytes you hand it. Mutates ``msgs`` in place.
    """
    if not images:
        return
    bare: list[str] = []
    for url in images:
        if not url:
            continue
        if url.startswith("data:") and "base64," in url:
            bare.append(url.split("base64,", 1)[1])
        elif url.startswith("http://") or url.startswith("https://"):
            # Ollama cannot fetch URLs — skip.
            continue
        else:
            bare.append(url)
    if not bare:
        return
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            msgs[i] = dict(msgs[i])
            msgs[i]["images"] = bare
            break


def _openai_image_part(block: dict) -> dict | None:
    """One image block → an OpenAI ``image_url`` part, or ``None``.

    Accepts the OpenAI shape as-is and converts an Anthropic ``image`` block
    (base64 or url source), so a history written for either family replays
    here. Ollama's ``/v1`` endpoint takes the same shape (base64 data URLs).
    """
    if block.get("type") == "image_url":
        url = (block.get("image_url") or {}).get("url") if isinstance(block.get("image_url"), dict) else block.get("image_url")
    else:
        src = block.get("source") or {}
        if src.get("type") == "base64" and src.get("data"):
            url = f"data:{src.get('media_type') or 'image/png'};base64,{src['data']}"
        else:
            url = src.get("url")
    return {"type": "image_url", "image_url": {"url": url}} if url else None


def _ollama_options(kwargs: dict) -> dict:
    """Build the Ollama ``options`` block from think kwargs.

    ``num_ctx`` is only included when the caller asks — a long system prompt +
    input can overflow the model's default context and make Ollama return an
    empty response, so callers can size it up; otherwise Ollama's default holds.
    """
    opts = {
        "temperature": kwargs.get("temperature", 0.7),
        "num_predict": kwargs.get("max_tokens", 4096),
    }
    if kwargs.get("num_ctx"):
        opts["num_ctx"] = int(kwargs["num_ctx"])
    return opts


# Ollama model families whose reasoning tokens the OpenAI-compat shim strips.
# See OpenAICompatThinkProvider._needs_think_false for the measurement and the
# rule for adding to this list.
_OLLAMA_REASONING_FAMILIES = ("qwen3", "qwythos", "deepseek-r1", "qwq")


class OpenAICompatThinkProvider(ToolCapableProvider):
    """Think via any OpenAI-compatible chat completions API."""

    name = "openai_compat"
    kind = "openai"

    def __init__(
        self,
        host: str = "https://api.openai.com",
        model: str = "gpt-5",
        api_key_env: str = "OPENAI_API_KEY",
        provider_name: str = "",
        timeout: int = 0,
        extra_body: dict | None = None,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout or 60
        # Operator-set request fields the endpoint understands but this class
        # does not model, e.g. OpenRouter's upstream routing
        # ({"provider": {"order": [...]}}). Never overrides a field the
        # provider sets on a request. Keys that change the request's shape are
        # dropped outright, because the provider sets them only on some paths:
        # `stream` on a plain call returns SSE that .json() cannot read, and
        # `tools` would turn a plain call into a tool call.
        extra = dict(extra_body) if isinstance(extra_body, dict) else {}
        self.extra_body = {k: v for k, v in extra.items() if k not in _SHAPE_KEYS}
        if provider_name:
            self.name = provider_name

    def _apply_extra_body(self, payload: dict) -> None:
        for key, value in self.extra_body.items():
            payload.setdefault(key, value)

    def _api_key(self) -> str:
        # BYOK first: if the current request supplied a user key for this
        # provider (via X-User-{Name}-Key header), use it. Otherwise fall
        # back to the server's env var. The contextvar is per-request, so
        # one visitor's key never bleeds into another visitor's call.
        try:
            from emptyos.capabilities.byok import get_byok_key

            host = (self.host or "").lower()
            if "openai.com" in host:
                user_key = get_byok_key("openai")
            elif "anthropic.com" in host:
                user_key = get_byok_key("anthropic")
            else:
                user_key = ""
            if user_key:
                return user_key
        except Exception:
            pass
        return os.environ.get(self.api_key_env, "")

    @property
    def auth_mode(self) -> str:
        # Local hosts (ollama, lm-studio) need no key. Cloud hosts distinguish
        # a per-request BYOK key from a server env key from no-key-at-all.
        if not self.is_cloud:
            return "local"
        try:
            from emptyos.capabilities.byok import get_byok_key
            host = (self.host or "").lower()
            prov = "openai" if "openai.com" in host else ("anthropic" if "anthropic.com" in host else "")
            if prov and get_byok_key(prov):
                return "byok"
        except Exception:
            pass
        return "api-key" if os.environ.get(self.api_key_env, "") else "none"

    async def available(self) -> bool:
        # Keyed cloud hosts (OpenAI, Anthropic, Ollama Cloud, OpenRouter, …):
        # key presence is the only thing checkable without a network call.
        # Probing /v1/models unauthenticated 401s on these and would wrongly
        # report the provider unavailable. `is_cloud` is host-based, so this
        # also keeps the cloud-consent gate honest — a provider only counts as
        # "available" here if it's a real keyed cloud endpoint.
        if self.is_cloud and self.api_key_env:
            return bool(self._api_key())
        # For local APIs (ollama, lm-studio, vllm), try a quick health check
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.host}/v1/models", timeout=aiohttp.ClientTimeout(total=2)
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def health(self) -> dict:
        # Keyed cloud APIs: missing key is the only failure we can name without
        # a network call. Covers OpenAI/Anthropic and any other keyed cloud
        # (Ollama Cloud, OpenRouter) — all classified cloud by host.
        if self.is_cloud and self.api_key_env:
            if not self._api_key():
                return {
                    "available": False,
                    "reason": f"{self.api_key_env} is not set in this process's environment",
                    "recovery": {"kind": "env_var", "name": self.api_key_env},
                }
            return {"available": True, "reason": None, "recovery": None}
        # Local API (ollama / lm-studio / vllm) — probe the host.
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.host}/v1/models", timeout=aiohttp.ClientTimeout(total=2)
                ) as resp:
                    if resp.status == 200:
                        return {"available": True, "reason": None, "recovery": None}
                    return {
                        "available": False,
                        "reason": f"{self.host}/v1/models returned HTTP {resp.status}",
                        "recovery": {
                            "kind": "service",
                            "id": self.name,
                            "url": self.host,
                            "hint": "Service is reachable but /v1/models did not return 200 — check the model is loaded",
                        },
                    }
        except Exception as e:
            hint = (
                "Run `ollama serve`"
                if self._is_ollama
                else f"Start the OpenAI-compatible service at {self.host}"
            )
            return {
                "available": False,
                "reason": f"cannot reach {self.host}: {e.__class__.__name__}",
                "recovery": {"kind": "service", "id": self.name, "url": self.host, "hint": hint},
            }

    @property
    def _is_ollama(self) -> bool:
        return "11434" in self.host or self.name == "ollama"

    @property
    def _needs_think_false(self) -> bool:
        """Ollama models that must go through the native API with think:false.

        These emit a ``<think>`` block that ollama carries in a SEPARATE field.
        The OpenAI-compat ``/v1/chat/completions`` shim drops that field, so if
        the model spends its budget reasoning the response arrives with
        ``content=""`` — a silent, expensive failure rather than an error.

        Measured on the model-bench code/js-exec prompt (2026-07-24):

            qwythos-32k via /v1/  ->  0 chars, 32,545 tokens, 387s
            qwythos-32k native    ->  442 chars (working code), 177 tokens, 9.8s

        Matching is substring-on-the-tag, so ``qwen3`` also covers
        ``qwen3.5-32k``. It was ``qwen3`` alone, which silently missed
        ``qwythos-32k`` — a Qwen3.5 finetune whose tag shares no substring with
        its base. Add a family here when a local reasoning model starts
        returning empty content; a trivial prompt will NOT reproduce it (short
        reasoning terminates fine), so reproduce with a hard one.
        """
        if not self._is_ollama:
            return False
        tag = (self.model or "").lower()
        return any(fam in tag for fam in _OLLAMA_REASONING_FAMILIES)

    @property
    def _wants_max_completion_tokens(self) -> bool:
        """OpenAI's gpt-5 / o-series models reject `max_tokens` and require
        `max_completion_tokens`. Older OpenAI models, Ollama, LM Studio, and
        other compat servers still accept `max_tokens`."""
        if "api.openai.com" not in self.host:
            return False
        m = (self.model or "").lower()
        return (
            m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")
        )

    def _apply_token_limit(self, payload: dict, kwargs: dict) -> None:
        """Set the right token-limit field for the current model."""
        limit = kwargs.get("max_tokens", 4096)
        if self._wants_max_completion_tokens:
            payload["max_completion_tokens"] = limit
        else:
            payload["max_tokens"] = limit

    def supports_vision(self) -> bool:
        """True when this provider's current model accepts image_url message parts."""
        return _model_supports_vision(self.model)

    def _build_request(
        self,
        prompt: str,
        system: str = "",
        *,
        messages: list[dict] | None = None,
        images: list[str] | None = None,
        **kwargs,
    ) -> tuple[dict, dict]:
        """Build messages, headers, and payload for a chat completion request.

        If `messages` is supplied, it's used as-is (with system prepended when
        not already present). Otherwise a single-user-turn message is built from
        `prompt`. When `images` is non-empty, the final user turn is rewritten
        as multimodal content — provider must support vision or the upstream
        API will reject the request.
        """
        if images and not self.supports_vision():
            raise RuntimeError(
                f"{self.name} model '{self.model}' does not support vision; "
                "fall through to a vision-capable provider"
            )
        msgs = _chat_messages(prompt, system, messages, images=images)

        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict = {
            "model": self.model,
            "messages": msgs,
        }
        # gpt-5 / o-series only accept temperature=1; skip the kwarg there.
        if not self._wants_max_completion_tokens:
            payload["temperature"] = kwargs.get("temperature", 0.7)
        self._apply_token_limit(payload, kwargs)
        self._apply_extra_body(payload)
        return headers, payload

    # Last usage data — captured for billing
    last_usage: dict | None = None

    async def execute(
        self,
        *,
        prompt: str = "",
        system: str = "",
        messages: list[dict] | None = None,
        images: list[str] | None = None,
        **kwargs,
    ) -> str:
        # Ollama reasoning models: native API with think:false (see
        # _needs_think_false — the compat shim drops their <think> field).
        if self._needs_think_false:
            return await self._execute_ollama_native(
                prompt, system, messages=messages, images=images, **kwargs
            )

        headers, payload = self._build_request(
            prompt, system, messages=messages, images=images, **kwargs
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.host}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status == 400:
                    error = await resp.json()
                    await self._diagnose_error(session, headers, error)
                resp.raise_for_status()
                data = await resp.json()

                # Capture real token usage for billing
                usage = data.get("usage")
                if usage:
                    pt = usage.get("prompt_tokens", 0)
                    ct = usage.get("completion_tokens", 0)
                    cached = _cached_tokens(usage)
                    self.last_usage = {
                        "provider": self.name,
                        "model": self.model,
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "cached_tokens": cached,
                        "total_tokens": usage.get("total_tokens", pt + ct),
                        "cost": self._usage_cost(usage, pt, ct, cached),
                    }

                return data["choices"][0]["message"]["content"]

    async def _execute_ollama_native(
        self,
        prompt: str,
        system: str = "",
        *,
        messages: list[dict] | None = None,
        images: list[str] | None = None,
        **kwargs,
    ) -> str:
        """Use Ollama native API with think:false to disable reasoning mode."""
        if images and not self.supports_vision():
            raise RuntimeError(
                f"{self.name} model '{self.model}' does not support vision; "
                "fall through to a vision-capable provider"
            )
        msgs = _chat_messages(prompt, system, messages)
        _attach_ollama_images(msgs, images)

        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "think": bool(kwargs.get("think", False)),
            "options": _ollama_options(kwargs),
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["message"]["content"]

    async def _stream_ollama_native(
        self,
        prompt: str,
        system: str = "",
        *,
        messages: list[dict] | None = None,
        images: list[str] | None = None,
        **kwargs,
    ):
        """Streaming version of _execute_ollama_native — yields content chunks.

        Reaches Ollama's native ``/api/chat`` endpoint with ``think:false`` so
        qwen3-family models don't waste the token budget on reasoning tokens
        that the openai-compat endpoint drops.
        """
        if images and not self.supports_vision():
            raise RuntimeError(
                f"{self.name} model '{self.model}' does not support vision; "
                "fall through to a vision-capable provider"
            )
        msgs = _chat_messages(prompt, system, messages)
        _attach_ollama_images(msgs, images)

        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": True,
            "think": bool(kwargs.get("think", False)),
            "options": _ollama_options(kwargs),
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.timeout * 3),
            ) as resp:
                resp.raise_for_status()
                got_content = False
                got_done = False
                async for line in resp.content:
                    raw = line.decode("utf-8", errors="replace").strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    content = (data.get("message") or {}).get("content", "")
                    if content:
                        yield {"text": content, "done": False}
                        got_content = True
                    if data.get("done"):
                        got_done = True
                        # Ollama's final chunk carries eval_count / prompt_eval_count.
                        pt = data.get("prompt_eval_count", 0)
                        ct = data.get("eval_count", 0)
                        if pt or ct:
                            yield {
                                "usage": {
                                    "model": self.model,
                                    "prompt_tokens": pt,
                                    "completion_tokens": ct,
                                    "total_tokens": pt + ct,
                                    "cost": 0.0,
                                },
                                "done": False,
                            }
                        yield {"text": "", "done": True}
                        return

                # Stream closed without a done marker — the connection dropped
                # mid-generation. Any content already yielded is a TRUNCATED
                # reply, not a complete one — raise unconditionally (not just
                # when content is also empty) so the capability chain falls
                # through instead of silently persisting a cut-off answer.
                if not got_done:
                    raise RuntimeError(
                        f"{self.name} stream ended without a completion marker "
                        f"(model={self.model}, host={self.host}, "
                        f"got_content={got_content})"
                    )

    async def _diagnose_error(self, session, headers, error):
        """Self-diagnose: when a request fails, figure out why and suggest fixes."""
        error_msg = error.get("error", {}).get("message", str(error))
        print(f"[{self.name}] Error: {error_msg}")

        # Check if model exists
        if "model" in error_msg.lower() or error.get("error", {}).get("code") == "model_not_found":
            try:
                async with session.get(
                    f"{self.host}/v1/models",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        models = [m["id"] for m in data.get("data", [])]
                        # Find similar model names
                        similar = [m for m in models if self.model.split("-")[0] in m][:5]
                        if similar:
                            print(
                                f"[{self.name}] Model '{self.model}' not found. Similar: {similar}"
                            )
                        else:
                            print(
                                f"[{self.name}] Model '{self.model}' not found. Available: {models[:10]}"
                            )
            except Exception:
                pass

    def _calc_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = self.PRICING.get(self.model, (0, 0))
        return round((prompt_tokens * pricing[0] + completion_tokens * pricing[1]) / 1_000_000, 6)

    def _usage_cost(
        self, usage: dict, prompt_tokens: int, completion_tokens: int, cached_tokens: int
    ) -> float:
        """Cost of one call: the endpoint's own figure when it reports one,
        else the PRICING table (see `_reported_cost`)."""
        reported = _reported_cost(usage)
        if reported is not None:
            return reported
        return self._calc_cost_with_cache(prompt_tokens, completion_tokens, cached_tokens)

    def _calc_cost_with_cache(
        self, prompt_tokens: int, completion_tokens: int, cached_tokens: int
    ) -> float:
        """Same as _calc_cost, but OpenAI-cached input tokens bill at 50% rate.

        `cached_tokens` is a SUBSET of `prompt_tokens` (OpenAI reports total
        prompt AND how many were served from cache — the two overlap, they
        don't add). So uncached = prompt_tokens - cached_tokens, charged full;
        cached portion charged at 50%.
        """
        pricing = self.PRICING.get(self.model, (0, 0))
        cached_tokens = max(0, min(int(cached_tokens or 0), int(prompt_tokens or 0)))
        uncached = max(0, prompt_tokens - cached_tokens)
        input_cost = (uncached * pricing[0] + cached_tokens * pricing[0] * 0.5) / 1_000_000
        output_cost = completion_tokens * pricing[1] / 1_000_000
        return round(input_cost + output_cost, 6)

    # OpenAI pricing per 1M tokens (input, output)
    PRICING = {
        "gpt-5.4": (2.50, 15.00),
        "gpt-5.4-mini": (0.75, 4.50),
        "gpt-5.4-nano": (0.20, 1.25),
        "gpt-5": (1.25, 10.00),
        "gpt-5-mini": (0.25, 2.00),
        "gpt-5-nano": (0.05, 0.40),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "o3": (10.00, 40.00),
        "o4-mini": (1.10, 4.40),
    }

    async def execute_stream(
        self,
        *,
        prompt: str = "",
        system: str = "",
        messages: list[dict] | None = None,
        images: list[str] | None = None,
        **kwargs,
    ):
        """Stream chat completion chunks.

        Yields:
          {"text": str, "done": bool} — content chunks
          {"usage": {...}, "cost": float} — token usage on final chunk
        """
        # Ollama + qwen3: mirror execute()'s native path so thinking mode is
        # disabled. Otherwise qwen3 burns the token budget on reasoning and
        # often emits zero content via the openai-compat endpoint.
        if self._needs_think_false:
            async for chunk in self._stream_ollama_native(
                prompt, system, messages=messages, images=images, **kwargs
            ):
                yield chunk
            return

        headers, payload = self._build_request(
            prompt, system, messages=messages, images=images, **kwargs
        )
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.host}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout * 3),
            ) as resp:
                resp.raise_for_status()
                usage_data = None
                got_content = False
                got_done = False
                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        got_done = True
                        break
                    try:
                        data = json.loads(data_str)

                        # Capture usage from final chunk
                        if "usage" in data and data["usage"]:
                            usage_data = data["usage"]

                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield {"text": content, "done": False}
                                got_content = True
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

                # No [DONE] marker — the connection dropped mid-stream (network
                # hiccup, gateway timeout, proxy cutting an idle response).
                # Whatever content already arrived is a TRUNCATED reply, not a
                # complete one — raise unconditionally (not just when content is
                # also empty) so the capability chain falls through to the next
                # provider instead of silently persisting a cut-off answer.
                if not got_done:
                    raise RuntimeError(
                        f"{self.name} stream ended without a completion marker "
                        f"(model={self.model}, host={self.host}, "
                        f"got_content={got_content})"
                    )

                # Emit usage info if captured
                if usage_data:
                    pt = usage_data.get("prompt_tokens", 0)
                    ct = usage_data.get("completion_tokens", 0)
                    cached = _cached_tokens(usage_data)
                    usage_dict = {
                        "provider": self.name,
                        "model": self.model,
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "cached_tokens": cached,
                        "total_tokens": usage_data.get("total_tokens", pt + ct),
                        "cost": self._usage_cost(usage_data, pt, ct, cached),
                    }
                    # Stash on the provider so callers that don't read stream
                    # chunks (billing path) can still see final usage.
                    self.last_usage = usage_dict
                    yield {"usage": usage_dict, "done": False}

                yield {"text": "", "done": True}

    # ── Tool-capable path ──────────────────────────────────────────

    async def execute_tools(
        self,
        *,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
        **kwargs,
    ) -> AgentTurn:
        """One model round-trip with function-calling schemas.

        Accepts messages in OpenAI-compat shape (tool_calls + role=tool results).
        Returns an AgentTurn with tool_uses normalized to the ToolUse dataclass.
        """
        msgs = self._normalize_messages_for_openai(messages, system)
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict = {
            "model": self.model,
            "messages": msgs,
        }
        if not self._wants_max_completion_tokens:
            payload["temperature"] = kwargs.get("temperature", 0.7)
        self._apply_token_limit(payload, kwargs)
        if tools:
            payload["tools"] = tools
            # "auto" lets the model decide; the loop relies on tool_calls being
            # present to continue. Force-tool is not supported in v1.
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")
        self._apply_extra_body(payload)

        # Use a generous timeout: local Ollama on a multi-KB prompt + tool schemas
        # can take longer than 60s. Matches streaming paths (self.timeout * 3).
        # asyncio.TimeoutError carries an empty str(), so translate to a useful
        # RuntimeError instead — otherwise the UI shows a bare "Error:".
        tool_timeout = self.timeout * 3
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.host}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=tool_timeout),
                ) as resp:
                    if resp.status >= 400:
                        # Surface the provider's error body up the stack — otherwise
                        # agent turns fail with "400 Bad Request" and no context.
                        body = await resp.text()
                        try:
                            err = json.loads(body)
                            err_msg = (
                                (err.get("error") or {}).get("message")
                                or err.get("message")
                                or body
                            )
                        except Exception:
                            err_msg = body
                        try:
                            await self._diagnose_error(
                                session, headers, {"error": {"message": err_msg}}
                            )
                        except Exception:
                            pass
                        raise RuntimeError(
                            f"{self.name} tool-call request failed (HTTP {resp.status}): {err_msg}"
                        )
                    data = await resp.json()
        except TimeoutError:
            raise RuntimeError(
                f"{self.name} tool-call timed out after {tool_timeout}s "
                f"(model={self.model}, messages={len(msgs)}, tools={len(tools or [])}). "
                f"Try a smaller prompt or a faster model (e.g. /model openai)."
            ) from None

        return self._turn_from_response(data)

    def _normalize_messages_for_openai(self, messages: list[dict], system: str) -> list[dict]:
        """Ensure system is at index 0 and assistant/tool messages round-trip.

        The OpenAI API requires every assistant message with `tool_calls` to be
        followed by matching `role=tool` messages for each `tool_call_id`.
        History replays can become invalid if a session was persisted before we
        preserved provider-specific fields or if legacy rows are partially
        malformed. This normalizer keeps the wire shape valid instead of letting
        a stale replay fail the next turn.
        """
        out: list[dict] = []
        seen_tool_parent = False
        if system and not (messages and messages[0].get("role") == "system"):
            out.append({"role": "system", "content": system})
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system" and out and out[0].get("role") == "system":
                continue
            # Pass tool_calls / tool_call_id through unchanged
            if isinstance(content, list):
                # Content-block list from a tool-capable turn — flatten text
                # blocks, drop tool_use (already represented via tool_calls
                # which we expect the caller to include separately).
                text_parts = []
                image_parts = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        text_parts.append(b.get("text", ""))
                    elif isinstance(b, dict) and b.get("type") in ("image_url", "image"):
                        # An attached image. Dropping it here (the old
                        # behaviour) sent the model the text alone, so it
                        # answered as if nothing were attached.
                        part = _openai_image_part(b)
                        if part:
                            image_parts.append(part)
                    elif isinstance(b, dict) and b.get("type") == "tool_result":
                        tool_call_id = b.get("tool_use_id", "")
                        if tool_call_id:
                            out.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_call_id,
                                    "content": str(b.get("content", "")),
                                }
                            )
                            seen_tool_parent = True
                if image_parts and role == "user":
                    # OpenAI's multimodal shape: text part, then image_url parts.
                    text = "".join(text_parts)
                    out.append({"role": "user", "content": ([{"type": "text", "text": text}] if text else []) + image_parts})
                elif text_parts:
                    msg = {"role": role, "content": "".join(text_parts)}
                    # Preserve any tool_calls on assistant turns
                    if m.get("tool_calls"):
                        msg["tool_calls"] = m["tool_calls"]
                    out.append(msg)
                    seen_tool_parent = bool(m.get("tool_calls")) or seen_tool_parent
                elif m.get("tool_calls"):
                    # Tool-only assistant turn (no text blocks) — must still be
                    # emitted so the subsequent role=tool results have a parent.
                    out.append({"role": role, "content": "", "tool_calls": m["tool_calls"]})
                    seen_tool_parent = True
                continue
            if role == "assistant":
                if content is None:
                    m = dict(m)
                    m["content"] = ""
                out.append(m)
                seen_tool_parent = bool(m.get("tool_calls"))
                continue
            if role == "tool":
                if not seen_tool_parent or not m.get("tool_call_id"):
                    continue
                out.append(m)
                continue
            out.append(m)

        # Second pass: strip orphan tool_calls (assistant tool_calls whose ids
        # have no matching following role=tool message). OpenAI 400s otherwise:
        # "An assistant message with 'tool_calls' must be followed by tool
        # messages responding to each 'tool_call_id'". Orphans appear when a
        # session was truncated mid-turn before tool results arrived.
        final: list[dict] = []
        i = 0
        while i < len(out):
            m = out[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                # Collect tool_call_ids from immediately-following role=tool messages
                present_ids: set[str] = set()
                j = i + 1
                while j < len(out) and out[j].get("role") == "tool":
                    tcid = out[j].get("tool_call_id")
                    if tcid:
                        present_ids.add(tcid)
                    j += 1
                kept_calls = [
                    tc for tc in m["tool_calls"]
                    if tc.get("id") and tc["id"] in present_ids
                ]
                if kept_calls:
                    nm = dict(m)
                    nm["tool_calls"] = kept_calls
                    final.append(nm)
                    kept_ids = {tc["id"] for tc in kept_calls}
                    # Re-emit only tool messages whose id was kept
                    for k in range(i + 1, j):
                        tm = out[k]
                        if tm.get("tool_call_id") in kept_ids:
                            final.append(tm)
                else:
                    # No matching tool results — drop tool_calls field.
                    # Keep the assistant message only if it has text content.
                    content = m.get("content")
                    if isinstance(content, str) and content.strip():
                        nm = dict(m)
                        nm.pop("tool_calls", None)
                        final.append(nm)
                    # Following tool messages (j range) are orphans — drop them.
                i = j
            else:
                final.append(m)
                i += 1
        return final

    def _turn_from_response(self, data: dict) -> AgentTurn:
        """Convert a /v1/chat/completions response into an AgentTurn."""
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {}) or {}
        finish_reason = choice.get("finish_reason", "stop") or "stop"

        blocks: list = []
        tool_uses: list[ToolUse] = []

        text = msg.get("content") or ""
        if text:
            blocks.append(TextBlock(text=text))

        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                input_ = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                input_ = {"__unparsed__": raw_args}
            tc_id = tc.get("id") or ""
            blocks.append(ToolUseBlock(id=tc_id, name=name, input=input_))
            tool_uses.append(ToolUse(id=tc_id, name=name, input=input_))

        # Map OpenAI finish_reason → our StopReason vocabulary
        stop_map = {
            "tool_calls": "tool_use",
            "stop": "end_turn",
            "length": "max_tokens",
            "content_filter": "end_turn",
        }
        stop_reason = stop_map.get(finish_reason, "end_turn")

        usage = data.get("usage") or {}
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        # OpenAI's automatic prompt caching reports `cached_tokens` under
        # `prompt_tokens_details` when the prefix was reused from the cache.
        # Cache hits are charged at 50% of the normal input rate — discount
        # the cost so the footer reflects actual spend.
        cached = _cached_tokens(usage)
        usage_dict = (
            {
                "model": self.model,
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "cached_tokens": cached,
                "total_tokens": usage.get("total_tokens", pt + ct),
                "cost": self._usage_cost(usage, pt, ct, cached),
            }
            if usage
            else {}
        )
        if usage_dict:
            self.last_usage = usage_dict

        return AgentTurn(
            assistant_blocks=blocks,
            tool_uses=tool_uses,
            stop_reason=stop_reason,
            usage=usage_dict,
            raw=data,
        )
