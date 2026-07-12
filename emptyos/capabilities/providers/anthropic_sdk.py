"""Anthropic SDK provider — native `tool_use` blocks for the agent loop.

Unlike `openai_compat` pointed at api.anthropic.com, this provider speaks
Anthropic's native Messages API with its own content-block shape. That's the
only way to get proper `tool_use` / `tool_result` semantics, prompt caching,
and streaming deltas per block.

The SDK (`anthropic` package) is an optional dependency — the provider
registers only when importable AND ANTHROPIC_API_KEY is set. Otherwise it's
silently absent and the chain falls through to the next provider.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

from emptyos.capabilities.providers._tool_capable import (
    AgentTurn,
    TextBlock,
    ToolCapableProvider,
    ToolUse,
    ToolUseBlock,
)

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_MAX_TOKENS = 8192


class AnthropicSDKProvider(ToolCapableProvider):
    """Anthropic Messages API with native tool-use.

    Usage through the Capability chain: `execute_tools(messages=..., system=...,
    tools=[tool.to_anthropic() for tool in tools])` → AgentTurn.

    `execute()` and `execute_stream()` are implemented too so the provider can
    serve as a plain text-completion fallback when no tools are passed — that
    way it can sit in the normal think chain.
    """

    name = "anthropic_sdk"
    kind = "anthropic"
    host = "https://api.anthropic.com"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key_env: str = "ANTHROPIC_API_KEY",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = 120,
        cache_system: bool = True,
    ):
        self.model = model
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.cache_system = cache_system
        self._client = None

    def _api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError(
                    "anthropic SDK not installed — run `pip install anthropic>=0.40`"
                ) from e
            key = self._api_key()
            if not key:
                raise RuntimeError(f"missing {self.api_key_env}")
            self._client = anthropic.AsyncAnthropic(api_key=key, timeout=self.timeout)
        return self._client

    async def available(self) -> bool:
        if not self._api_key():
            return False
        try:
            import anthropic  # noqa: F401

            return True
        except ImportError:
            return False

    async def health(self) -> dict:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return {
                "available": False,
                "reason": "anthropic SDK not installed",
                "recovery": {
                    "kind": "service",
                    "id": "anthropic-sdk",
                    "url": "",
                    "hint": "Run `pip install anthropic>=0.40`",
                },
            }
        if not self._api_key():
            return {
                "available": False,
                "reason": f"{self.api_key_env} is not set",
                "recovery": {"kind": "env_var", "name": self.api_key_env},
            }
        return {"available": True, "reason": None, "recovery": None}

    # ── Plain text completion (Provider interface) ─────────────────

    async def execute(self, **kwargs) -> str:
        """Single-turn completion — no tools."""
        client = self._get_client()
        messages = self._to_anthropic_messages(kwargs)
        system = kwargs.get("system", "") or ""
        resp = await client.messages.create(
            model=self.model,
            max_tokens=kwargs.get("max_tokens") or self.max_tokens,
            system=system,
            messages=messages,
            temperature=kwargs.get("temperature", 1.0),
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        self._record_usage(resp)
        return "".join(parts)

    async def execute_stream(self, **kwargs) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        messages = self._to_anthropic_messages(kwargs)
        system = kwargs.get("system", "") or ""
        async with client.messages.stream(
            model=self.model,
            max_tokens=kwargs.get("max_tokens") or self.max_tokens,
            system=system,
            messages=messages,
            temperature=kwargs.get("temperature", 1.0),
        ) as stream:
            async for text in stream.text_stream:
                yield {"text": text, "done": False}
            final = await stream.get_final_message()
            self._record_usage(final)
            yield {"text": "", "done": True, "usage": self._usage_dict(final)}

    # ── Tool-capable path (ToolCapableProvider interface) ──────────

    async def execute_tools(
        self,
        *,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
        **kwargs,
    ) -> AgentTurn:
        client = self._get_client()
        resp = await client.messages.create(
            model=self.model,
            max_tokens=kwargs.get("max_tokens") or self.max_tokens,
            system=self._apply_system_cache(system),
            messages=messages,
            tools=self._apply_tools_cache(tools or []),
            temperature=kwargs.get("temperature", 1.0),
        )
        return self._turn_from_message(resp)

    async def execute_tools_stream(
        self,
        *,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
        **kwargs,
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        async with client.messages.stream(
            model=self.model,
            max_tokens=kwargs.get("max_tokens") or self.max_tokens,
            system=self._apply_system_cache(system),
            messages=messages,
            tools=self._apply_tools_cache(tools or []),
            temperature=kwargs.get("temperature", 1.0),
        ) as stream:
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    btype = getattr(block, "type", "")
                    if btype == "tool_use":
                        yield {
                            "tool_use_start": {
                                "id": block.id,
                                "name": block.name,
                            }
                        }
                elif etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    dtype = getattr(delta, "type", "")
                    if dtype == "text_delta":
                        yield {"text_delta": delta.text}
                    elif dtype == "input_json_delta":
                        # index identifies the content block
                        yield {
                            "tool_use_delta": {
                                "index": event.index,
                                "partial_json": delta.partial_json,
                            }
                        }
                elif etype == "content_block_stop":
                    yield {"content_block_stop": {"index": event.index}}
            final = await stream.get_final_message()
            self._record_usage(final)
            yield {"turn": self._turn_from_message(final)}

    # ── Internals ──────────────────────────────────────────────────

    def _to_anthropic_messages(self, kwargs: dict) -> list[dict]:
        """Build a messages list for text-only calls — supports `messages=` or
        `prompt=` kwargs like the other providers."""
        if kwargs.get("messages"):
            # Strip any "system" role entries — Anthropic takes system top-level.
            return [m for m in kwargs["messages"] if m.get("role") != "system"]
        prompt = kwargs.get("prompt", "") or ""
        return [{"role": "user", "content": prompt}]

    def _apply_system_cache(self, system: str):
        """Promote system prompt to a cache-control block when caching enabled."""
        if not system:
            return ""
        if not self.cache_system:
            return system
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def _apply_tools_cache(self, tools: list[dict]) -> list[dict]:
        """Attach cache_control to the final tool — Anthropic caches the whole
        tools array up to that marker. Cheap even for small tool sets."""
        if not tools or not self.cache_system:
            return tools
        out = [dict(t) for t in tools]
        out[-1]["cache_control"] = {"type": "ephemeral"}
        return out

    def _turn_from_message(self, resp: Any) -> AgentTurn:
        """Convert an Anthropic Message response into an AgentTurn."""
        blocks: list[Any] = []
        tool_uses: list[ToolUse] = []
        for b in resp.content:
            btype = getattr(b, "type", "")
            if btype == "text":
                blocks.append(TextBlock(text=b.text))
            elif btype == "tool_use":
                tu = ToolUseBlock(id=b.id, name=b.name, input=dict(b.input) if b.input else {})
                blocks.append(tu)
                tool_uses.append(ToolUse(id=b.id, name=b.name, input=tu.input))
        stop_reason = getattr(resp, "stop_reason", "end_turn") or "end_turn"
        if stop_reason not in ("tool_use", "end_turn", "max_tokens", "stop_sequence"):
            stop_reason = "end_turn"
        return AgentTurn(
            assistant_blocks=blocks,
            tool_uses=tool_uses,
            stop_reason=stop_reason,
            usage=self._usage_dict(resp),
            raw=resp,
        )

    def _usage_dict(self, resp: Any) -> dict:
        u = getattr(resp, "usage", None)
        if not u:
            return {}
        inp = getattr(u, "input_tokens", 0) or 0
        out = getattr(u, "output_tokens", 0) or 0
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(u, "cache_creation_input_tokens", 0) or 0
        return {
            "model": self.model,
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_create,
            "cost": self._calc_cost(inp, out, cache_read, cache_create),
        }

    def _calc_cost(self, inp: int, out: int, cache_read: int, cache_create: int) -> float:
        """Compute the turn cost with Anthropic's cache-aware pricing.

        Anthropic reports `input_tokens` as the uncached portion only; the cached
        portions are separate fields. Billing follows three rates:
          * uncached input  → 1× base input price
          * cache_creation  → 1.25× base (one-time write cost)
          * cache_read      → 0.1× base (90% discount — the whole point of caching)

        Pricing per 1M tokens (input, output) — mirrors Anthropic's April 2026 prices.
        Match by substring so "claude-opus-4-7-20260131" resolves to the opus row."""
        pricing = (0.0, 0.0)
        m = (self.model or "").lower()
        for key, rates in self.PRICING:
            if key in m:
                pricing = rates
                break
        in_rate, out_rate = pricing
        cost = (
            inp * in_rate / 1_000_000
            + cache_read * in_rate * 0.1 / 1_000_000
            + cache_create * in_rate * 1.25 / 1_000_000
            + out * out_rate / 1_000_000
        )
        return round(cost, 6)

    # Anthropic pricing per 1M tokens (input, output). Ordered most-specific first
    # so substring match picks up "opus" / "sonnet" / "haiku" correctly regardless
    # of trailing version suffix.
    PRICING = [
        ("claude-opus-4-7", (15.00, 75.00)),
        ("claude-opus", (15.00, 75.00)),
        ("claude-sonnet-4-6", (3.00, 15.00)),
        ("claude-sonnet", (3.00, 15.00)),
        ("claude-haiku-4-5", (0.80, 4.00)),
        ("claude-haiku", (0.80, 4.00)),
    ]

    def _record_usage(self, resp: Any) -> None:
        self.last_usage = self._usage_dict(resp)
