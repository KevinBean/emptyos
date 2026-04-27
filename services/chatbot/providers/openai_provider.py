from __future__ import annotations

import os
from typing import AsyncIterator

from openai import AsyncOpenAI

from .base import Provider, CompletionResult


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
    "gpt-5-nano":     {"in": 0.05, "out": 0.40},
    "gpt-5-mini":     {"in": 0.25, "out": 2.00},
    "gpt-5":          {"in": 1.25, "out": 10.00},
    # GPT-4.1 family (kept for fallback / legacy)
    "gpt-4.1-nano":   {"in": 0.10, "out": 0.40},
    "gpt-4.1-mini":   {"in": 0.40, "out": 1.60},
    "gpt-4.1":        {"in": 2.00, "out": 8.00},
    # GPT-4o family
    "gpt-4o-mini":    {"in": 0.15, "out": 0.60},
    "gpt-4o":         {"in": 2.50, "out": 10.00},
}


def _cost(tokens_in: int, tokens_out: int, model: str) -> float:
    rate = PRICING.get(model) or PRICING["gpt-5-nano"]
    return (tokens_in * rate["in"] + tokens_out * rate["out"]) / 1_000_000


def _max_tokens_kwarg(model: str, n: int) -> dict:
    """Pick the right param name for the model's API version.

    GPT-5 family + the o1/o3 reasoning families use `max_completion_tokens`;
    GPT-4 and older use the legacy `max_tokens`. OpenAI rejects requests that
    use the wrong one with a 400 invalid_request_error.
    """
    if model.startswith(("gpt-5", "o1", "o3", "o4")):
        return {"max_completion_tokens": n}
    return {"max_tokens": n}


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
        kwargs = {
            "model": model,
            "messages": full_messages,
            **_max_tokens_kwarg(model, max_tokens),
        }
        # GPT-5 / o-series models reject custom `temperature` (only support
        # the default of 1.0). Older chat models accept it.
        if not model.startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["temperature"] = 0.4
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
        kwargs = {
            "model": model,
            "messages": full_messages,
            "stream": True,
            **_max_tokens_kwarg(model, max_tokens),
        }
        if not model.startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["temperature"] = 0.4
        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
