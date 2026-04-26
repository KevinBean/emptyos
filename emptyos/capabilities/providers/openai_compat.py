"""OpenAI-compatible provider — works with any API that speaks the OpenAI format.

Covers: OpenAI, Ollama (with /v1/ endpoint), LM Studio, vLLM, llama.cpp, etc.
"""

from __future__ import annotations

import asyncio
import os

import json

import aiohttp

from emptyos.capabilities import Provider
from emptyos.capabilities.providers._tool_capable import (
    AgentTurn, TextBlock, ToolCapableProvider, ToolUse, ToolUseBlock,
)


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


def _chat_messages(prompt: str, system: str, messages: list[dict] | None) -> list[dict]:
    """Build an OpenAI-style messages list from either `messages` or `prompt`,
    prepending `system` when present and not already the first turn."""
    if messages:
        msgs = list(messages)
        if system and not (msgs and msgs[0].get("role") == "system"):
            msgs = [{"role": "system", "content": system}] + msgs
        return msgs
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


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
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout or 60
        if provider_name:
            self.name = provider_name

    def _api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    async def available(self) -> bool:
        # If it's a remote API, we need a key. Local APIs (ollama, lm-studio) don't.
        if "api.openai.com" in self.host or "api.anthropic.com" in self.host:
            return bool(self._api_key())
        # For local APIs, try a quick health check
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.host}/v1/models", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def health(self) -> dict:
        # Cloud APIs: missing key is the only failure we can name without a network call.
        if "api.openai.com" in self.host or "api.anthropic.com" in self.host:
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
                async with session.get(f"{self.host}/v1/models", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        return {"available": True, "reason": None, "recovery": None}
                    return {
                        "available": False,
                        "reason": f"{self.host}/v1/models returned HTTP {resp.status}",
                        "recovery": {"kind": "service", "id": self.name, "url": self.host,
                                     "hint": "Service is reachable but /v1/models did not return 200 — check the model is loaded"},
                    }
        except Exception as e:
            hint = "Run `ollama serve`" if self._is_ollama else f"Start the OpenAI-compatible service at {self.host}"
            return {
                "available": False,
                "reason": f"cannot reach {self.host}: {e.__class__.__name__}",
                "recovery": {"kind": "service", "id": self.name, "url": self.host, "hint": hint},
            }

    @property
    def _is_ollama(self) -> bool:
        return "11434" in self.host or self.name == "ollama"

    @property
    def _wants_max_completion_tokens(self) -> bool:
        """OpenAI's gpt-5 / o-series models reject `max_tokens` and require
        `max_completion_tokens`. Older OpenAI models, Ollama, LM Studio, and
        other compat servers still accept `max_tokens`."""
        if "api.openai.com" not in self.host:
            return False
        m = (self.model or "").lower()
        return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")

    def _apply_token_limit(self, payload: dict, kwargs: dict) -> None:
        """Set the right token-limit field for the current model."""
        limit = kwargs.get("max_tokens", 4096)
        if self._wants_max_completion_tokens:
            payload["max_completion_tokens"] = limit
        else:
            payload["max_tokens"] = limit

    def _build_request(self, prompt: str, system: str = "", *, messages: list[dict] | None = None, **kwargs) -> tuple[dict, dict]:
        """Build messages, headers, and payload for a chat completion request.

        If `messages` is supplied, it's used as-is (with system prepended when
        not already present). Otherwise a single-user-turn message is built from
        `prompt`.
        """
        msgs = _chat_messages(prompt, system, messages)

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
        return headers, payload

    # Last usage data — captured for billing
    last_usage: dict | None = None

    async def execute(self, *, prompt: str = "", system: str = "", messages: list[dict] | None = None, **kwargs) -> str:
        # Ollama: use native API with think:false for qwen3 models
        if self._is_ollama and "qwen3" in self.model.lower():
            return await self._execute_ollama_native(prompt, system, messages=messages, **kwargs)

        headers, payload = self._build_request(prompt, system, messages=messages, **kwargs)

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
                        "provider": self.name, "model": self.model,
                        "prompt_tokens": pt, "completion_tokens": ct,
                        "cached_tokens": cached,
                        "total_tokens": usage.get("total_tokens", pt + ct),
                        "cost": self._calc_cost_with_cache(pt, ct, cached),
                    }

                return data["choices"][0]["message"]["content"]

    async def _execute_ollama_native(self, prompt: str, system: str = "", *, messages: list[dict] | None = None, **kwargs) -> str:
        """Use Ollama native API with think:false to disable reasoning mode."""
        msgs = _chat_messages(prompt, system, messages)

        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "think": False,
            "options": {
                "temperature": kwargs.get("temperature", 0.7),
                "num_predict": kwargs.get("max_tokens", 4096),
            },
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

    async def _stream_ollama_native(self, prompt: str, system: str = "", *, messages: list[dict] | None = None, **kwargs):
        """Streaming version of _execute_ollama_native — yields content chunks.

        Reaches Ollama's native ``/api/chat`` endpoint with ``think:false`` so
        qwen3-family models don't waste the token budget on reasoning tokens
        that the openai-compat endpoint drops.
        """
        msgs = _chat_messages(prompt, system, messages)

        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": True,
            "think": False,
            "options": {
                "temperature": kwargs.get("temperature", 0.7),
                "num_predict": kwargs.get("max_tokens", 4096),
            },
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
                                    "model": self.model, "prompt_tokens": pt,
                                    "completion_tokens": ct, "total_tokens": pt + ct,
                                    "cost": 0.0,
                                },
                                "done": False,
                            }
                        yield {"text": "", "done": True}
                        return

                # Stream closed without content AND without a done marker — something
                # went wrong upstream. Raise so the capability chain falls through.
                if not got_content and not got_done:
                    raise RuntimeError(
                        f"{self.name} stream produced no content "
                        f"(model={self.model}, host={self.host})"
                    )

    async def _diagnose_error(self, session, headers, error):
        """Self-diagnose: when a request fails, figure out why and suggest fixes."""
        error_msg = error.get("error", {}).get("message", str(error))
        print(f"[{self.name}] Error: {error_msg}")

        # Check if model exists
        if "model" in error_msg.lower() or error.get("error", {}).get("code") == "model_not_found":
            try:
                async with session.get(
                    f"{self.host}/v1/models", headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        models = [m["id"] for m in data.get("data", [])]
                        # Find similar model names
                        similar = [m for m in models if self.model.split("-")[0] in m][:5]
                        if similar:
                            print(f"[{self.name}] Model '{self.model}' not found. Similar: {similar}")
                        else:
                            print(f"[{self.name}] Model '{self.model}' not found. Available: {models[:10]}")
            except Exception:
                pass

    def _calc_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = self.PRICING.get(self.model, (0, 0))
        return round((prompt_tokens * pricing[0] + completion_tokens * pricing[1]) / 1_000_000, 6)

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

    async def execute_stream(self, *, prompt: str = "", system: str = "", messages: list[dict] | None = None, **kwargs):
        """Stream chat completion chunks.

        Yields:
          {"text": str, "done": bool} — content chunks
          {"usage": {...}, "cost": float} — token usage on final chunk
        """
        # Ollama + qwen3: mirror execute()'s native path so thinking mode is
        # disabled. Otherwise qwen3 burns the token budget on reasoning and
        # often emits zero content via the openai-compat endpoint.
        if self._is_ollama and "qwen3" in self.model.lower():
            async for chunk in self._stream_ollama_native(prompt, system, messages=messages, **kwargs):
                yield chunk
            return

        headers, payload = self._build_request(prompt, system, messages=messages, **kwargs)
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

                # No content AND no [DONE] marker — upstream failed mid-stream.
                # Raise so the capability chain falls through to the next provider.
                if not got_content and not got_done:
                    raise RuntimeError(
                        f"{self.name} stream produced no content "
                        f"(model={self.model}, host={self.host})"
                    )

                # Emit usage info if captured
                if usage_data:
                    pt = usage_data.get("prompt_tokens", 0)
                    ct = usage_data.get("completion_tokens", 0)
                    cached = _cached_tokens(usage_data)
                    usage_dict = {
                        "provider": self.name,
                        "model": self.model, "prompt_tokens": pt, "completion_tokens": ct,
                        "cached_tokens": cached,
                        "total_tokens": usage_data.get("total_tokens", pt + ct),
                        "cost": self._calc_cost_with_cache(pt, ct, cached),
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
                                or err.get("message") or body
                            )
                        except Exception:
                            err_msg = body
                        try:
                            await self._diagnose_error(session, headers, {"error": {"message": err_msg}})
                        except Exception:
                            pass
                        raise RuntimeError(
                            f"{self.name} tool-call request failed (HTTP {resp.status}): {err_msg}"
                        )
                    data = await resp.json()
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"{self.name} tool-call timed out after {tool_timeout}s "
                f"(model={self.model}, messages={len(msgs)}, tools={len(tools or [])}). "
                f"Try a smaller prompt or a faster model (e.g. /model openai)."
            )

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
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        text_parts.append(b.get("text", ""))
                    elif isinstance(b, dict) and b.get("type") == "tool_result":
                        tool_call_id = b.get("tool_use_id", "")
                        if tool_call_id:
                            out.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": str(b.get("content", "")),
                            })
                            seen_tool_parent = True
                if text_parts:
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
        return out

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
        usage_dict = {
            "model": self.model,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "cached_tokens": cached,
            "total_tokens": usage.get("total_tokens", pt + ct),
            "cost": self._calc_cost_with_cache(pt, ct, cached),
        } if usage else {}
        if usage_dict:
            self.last_usage = usage_dict

        return AgentTurn(
            assistant_blocks=blocks,
            tool_uses=tool_uses,
            stop_reason=stop_reason,
            usage=usage_dict,
            raw=data,
        )
