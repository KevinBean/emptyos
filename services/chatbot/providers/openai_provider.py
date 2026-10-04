from __future__ import annotations

import os
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .base import CompletionResult, Provider

# USD per 1M tokens. Update as OpenAI pricing changes.
# Cached input prices apply when prompt caching kicks in (provider-side, automatic
# when the same prefix is reused). We don't track cache hits explicitly; we
# bill at the un-cached rate (overestimate is the safe direction for the cap).
#
# Verify against https://openai.com/api/pricing — these inform the per-site
# daily $ cap calculation; if a price is stale by, say, 20%, the cap trips
# early or late but the OpenAI org-level monthly budget is the real backstop.
PRICING = {
    # GPT-5 family (current default for chatbot)
    "gpt-5-nano": {"in": 0.05, "out": 0.40},
    "gpt-5-mini": {"in": 0.25, "out": 2.00},
    "gpt-5": {"in": 1.25, "out": 10.00},
    # GPT-4.1 family (kept for fallback / legacy)
    "gpt-4.1-nano": {"in": 0.10, "out": 0.40},
    "gpt-4.1-mini": {"in": 0.40, "out": 1.60},
    "gpt-4.1": {"in": 2.00, "out": 8.00},
    # GPT-4o family
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
}


def _cost(tokens_in: int, tokens_out: int, model: str) -> float:
    rate = PRICING.get(model) or PRICING["gpt-5-nano"]
    return (tokens_in * rate["in"] + tokens_out * rate["out"]) / 1_000_000


def _is_reasoning_model(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


# Reasoning models eat from max_completion_tokens for internal thinking before
# they produce any visible output. Site-Q&A doesn't need reasoning, so we ask
# for the smallest allowed effort and budget enough headroom that a typical
# 2-3 sentence reply isn't truncated by accumulated reasoning tokens.
#
# Per OpenAI: GPT-5 with `reasoning_effort="minimal"` still consumes some
# reasoning tokens; multiply the visible budget by ~3 to be safe.
_REASONING_MULTIPLIER = 3


def _build_kwargs(
    model: str, messages: list[dict], max_tokens: int, *, stream: bool = False
) -> dict:
    """Assemble the per-model kwargs for chat.completions.create.

    GPT-5 / o-series quirks:
      - param name is `max_completion_tokens`, not `max_tokens`
      - custom `temperature` rejected (only default 1.0 accepted)
      - support `reasoning_effort` in {minimal, low, medium, high}; we use
        minimal because site-chat doesn't benefit from chain-of-thought
      - reasoning tokens count against the completion budget, so multiply
        the requested visible-token budget so replies don't come back blank
    """
    kwargs: dict = {"model": model, "messages": messages}
    if stream:
        kwargs["stream"] = True
    if _is_reasoning_model(model):
        kwargs["max_completion_tokens"] = max_tokens * _REASONING_MULTIPLIER
        # OpenAI accepts "minimal" on GPT-5; older o1/o3 use "low" as floor.
        # Sending "minimal" is fine for both — the SDK just passes it through.
        kwargs["reasoning_effort"] = "minimal"
    else:
        kwargs["max_tokens"] = max_tokens
        kwargs["temperature"] = 0.4
    return kwargs


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self.client = AsyncOpenAI(api_key=api_key)

    async def complete(
        self,
        *,
        messages: list[dict],
        system: str,
        model: str,
        max_tokens: int,
    ) -> CompletionResult:
        full_messages = [{"role": "system", "content": system}] + messages
        kwargs = _build_kwargs(model, full_messages, max_tokens)
        resp = await self.client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        return CompletionResult(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_cost(tokens_in, tokens_out, model),
            model=model,
        )

    async def stream(
        self,
        *,
        messages: list[dict],
        system: str,
        model: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        full_messages = [{"role": "system", "content": system}] + messages
        kwargs = _build_kwargs(model, full_messages, max_tokens, stream=True)
        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
