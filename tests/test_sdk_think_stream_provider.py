"""Unit tests for BaseApp.think_stream(provider=...) — per-call provider pin.

Mirrors the pinned_execute semantics on the streaming path: if ``provider`` is
passed and that provider exists + is available, stream from it directly.
Otherwise fall back to the capability's default chain (which includes
settings-based overrides when no pin is requested).

No daemon, no LLM — the shared fake kernel exercises the control flow.
"""

from __future__ import annotations

import pytest

from fake_kernel import FakeCapability, FakeProvider, make_bare_app


async def _collect(agen):
    return [c async for c in agen]


@pytest.mark.asyncio
async def test_provider_pin_hit_streams_from_that_provider():
    claude = FakeProvider("claude-cli", chunks=[{"text": "CLAUDE", "done": False}])
    ollama = FakeProvider("ollama", chunks=[{"text": "OLLAMA", "done": False}])
    app = make_bare_app(FakeCapability([claude, ollama]))  # claude first in chain

    chunks = await _collect(app.think_stream("hi", provider="ollama"))

    assert chunks == [{"text": "OLLAMA", "done": False}]
    assert ollama.called_with == {"prompt": "hi"}
    assert claude.called_with is None
    assert app.kernel.capability("think").chain_called is False


@pytest.mark.asyncio
async def test_provider_pin_miss_falls_back_to_chain():
    offline = FakeProvider("ollama", available=False)
    app = make_bare_app(FakeCapability([offline]))

    chunks = await _collect(app.think_stream("hi", provider="ollama"))

    assert chunks[0]["text"] == "from-chain"
    assert app.kernel.capability("think").chain_called is True


@pytest.mark.asyncio
async def test_no_provider_pin_uses_chain():
    claude = FakeProvider("claude-cli", chunks=[{"text": "CLAUDE", "done": False}])
    app = make_bare_app(FakeCapability([claude]))

    chunks = await _collect(app.think_stream("hi"))

    assert chunks[0]["text"] == "from-chain"
    assert claude.called_with is None  # direct execute_stream never called


@pytest.mark.asyncio
async def test_provider_pin_finds_provider_in_domain_subchain():
    """Pin should search domain subchains too (e.g. a code-only provider)."""
    code_only = FakeProvider("code-llm", chunks=[{"text": "CODE", "done": False}])
    cap = FakeCapability(providers=[], domain_providers={"code": [code_only]})
    app = make_bare_app(cap)

    chunks = await _collect(app.think_stream("refactor", provider="code-llm"))

    assert chunks == [{"text": "CODE", "done": False}]
    assert code_only.called_with == {"prompt": "refactor"}


@pytest.mark.asyncio
async def test_think_stream_requires_prompt_or_messages():
    app = make_bare_app(FakeCapability([]))
    with pytest.raises(ValueError):
        await _collect(app.think_stream(""))
