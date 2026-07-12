from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class CompletionResult:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str


class Provider:
    name: str = "base"

    async def complete(
        self,
        *,
        messages: list[dict],
        system: str,
        model: str,
        max_tokens: int,
    ) -> CompletionResult:
        raise NotImplementedError

    async def stream(
        self,
        *,
        messages: list[dict],
        system: str,
        model: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        # Default fallback: yield whole reply at once.
        result = await self.complete(
            messages=messages, system=system, model=model, max_tokens=max_tokens
        )
        yield result.text
