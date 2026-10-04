"""Capability-level fallback: when a provider raises mid-chain (rate-limit,
network blip, empty stream), the chain must transparently switch to the next
provider, and the consumer must see a `provider_used` marker identifying
whichever provider actually answered.

This is what makes EmptyOS resilient when Claude hits its usage limit — every
app using self.think() / self.think_stream() gets a response from openai or
ollama instead of "the whole system fails".
"""

from __future__ import annotations

import pytest

from emptyos.capabilities import Capability


class _Provider:
    """Minimal Provider — no subclass needed for these tests."""

    def __init__(self, name, *, raises=None, execute_stream_raises=None,
                 chunks=None, returns=None):
        self.name = name
        self._raises = raises
        self._execute_stream_raises = execute_stream_raises or raises
        self._chunks = chunks or []
        self._returns = returns
        self._current_load = 0
        self.capacity = 0
        self.is_cloud = False

    @property
    def at_capacity(self):
        return False

    async def available(self):
        return True

    async def execute(self, **kwargs):
        if self._raises:
            raise self._raises
        return self._returns

    async def execute_stream(self, **kwargs):
        if self._execute_stream_raises:
            raise self._execute_stream_raises
        for c in self._chunks:
            yield c


@pytest.mark.asyncio
async def test_stream_happy_path_yields_provider_used():
    """Successful stream from the first provider must still announce itself."""
    claude = _Provider("claude-cli", chunks=[
        {"text": "hello ", "done": False},
        {"text": "world", "done": False},
        {"text": "", "done": True},
    ])
    cap = Capability()
    cap.add_provider(claude)

    seen = []
    async for chunk in cap.execute_stream(prompt="hi"):
        seen.append(chunk)

    providers_used = [c["provider_used"] for c in seen if "provider_used" in c]
    assert providers_used == ["claude-cli"]
    text = "".join(c.get("text", "") for c in seen if "text" in c)
    assert text == "hello world"


@pytest.mark.asyncio
async def test_stream_falls_through_on_provider_error():
    """Claude raises → openai runs → provider_used reports openai."""
    claude = _Provider("claude-cli", execute_stream_raises=RuntimeError("usage limit"))
    openai = _Provider("openai", chunks=[
        {"text": "openai-answer", "done": False},
        {"text": "", "done": True},
    ])
    cap = Capability()
    cap.add_provider(claude)
    cap.add_provider(openai)

    seen = []
    async for chunk in cap.execute_stream(prompt="hi"):
        seen.append(chunk)

    # Consumer did NOT receive any partial output from the failing provider
    text = "".join(c.get("text", "") for c in seen if "text" in c)
    assert text == "openai-answer"
    # And the marker names openai
    markers = [c["provider_used"] for c in seen if "provider_used" in c]
    assert markers == ["openai"]


@pytest.mark.asyncio
async def test_stream_falls_through_all_the_way_to_third_provider():
    """Two failing providers → third succeeds."""
    claude = _Provider("claude-cli", execute_stream_raises=RuntimeError("usage limit"))
    openai = _Provider("openai", execute_stream_raises=RuntimeError("quota exceeded"))
    ollama = _Provider("ollama", chunks=[
        {"text": "local-answer", "done": False},
        {"text": "", "done": True},
    ])
    cap = Capability()
    cap.add_provider(claude)
    cap.add_provider(openai)
    cap.add_provider(ollama)

    seen = []
    async for chunk in cap.execute_stream(prompt="hi"):
        seen.append(chunk)

    text = "".join(c.get("text", "") for c in seen if "text" in c)
    assert text == "local-answer"
    markers = [c["provider_used"] for c in seen if "provider_used" in c]
    assert markers == ["ollama"]


@pytest.mark.asyncio
async def test_stream_raises_when_all_providers_fail():
    """If every provider in the chain raises, execute_stream must raise."""
    claude = _Provider("claude-cli", execute_stream_raises=RuntimeError("usage limit"))
    openai = _Provider("openai", execute_stream_raises=RuntimeError("quota exceeded"))
    cap = Capability()
    cap.add_provider(claude)
    cap.add_provider(openai)

    with pytest.raises(RuntimeError, match="No available provider"):
        async for chunk in cap.execute_stream(prompt="hi"):
            pass


@pytest.mark.asyncio
async def test_blocking_execute_falls_through_on_error():
    """Non-streaming path must fall through too — apps using self.think()
    (reactor, staff, scheduler) rely on this."""
    claude = _Provider("claude-cli", raises=RuntimeError("usage limit"))
    openai = _Provider("openai", returns="openai-answer")
    cap = Capability()
    cap.add_provider(claude)
    cap.add_provider(openai)

    result = await cap.execute(prompt="hi")
    assert result.value == "openai-answer"
    assert result.provider == "openai"
