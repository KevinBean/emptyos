"""Unit tests for BaseApp.pinned_execute.

Exercises the "pin this provider, else fall back to the chain" semantics
with a fake kernel + fake capability — no daemon, no LLM calls.
"""

from __future__ import annotations

import pytest

from fake_kernel import FakeCapability, FakeProvider, make_bare_app


@pytest.mark.asyncio
async def test_pinned_provider_hit_uses_that_provider():
    openai = FakeProvider("openai", returns="OPENAI_RESP")
    ollama = FakeProvider("ollama", returns="OLLAMA_RESP")
    app = make_bare_app(FakeCapability([ollama, openai]))  # order: ollama first

    out = await app.pinned_execute("think", "openai", prompt="hi")

    assert out == "OPENAI_RESP"
    assert openai.called_with == {"prompt": "hi"}
    assert ollama.called_with is None


@pytest.mark.asyncio
async def test_pinned_unavailable_falls_back_to_chain():
    offline_openai = FakeProvider("openai", available=False)
    app = make_bare_app(FakeCapability([offline_openai]))

    out = await app.pinned_execute("think", "openai", prompt="hi")

    assert out == "default-chain-result"


@pytest.mark.asyncio
async def test_pinned_none_skips_search_and_uses_chain():
    openai = FakeProvider("openai", returns="OPENAI_RESP")
    app = make_bare_app(FakeCapability([openai]))

    out = await app.pinned_execute("think", None, prompt="hi")

    assert out == "default-chain-result"
    assert openai.called_with is None  # never consulted


@pytest.mark.asyncio
async def test_pinned_finds_provider_in_domain_subchain():
    # Provider lives only in a domain subchain, not the main chain
    code_only = FakeProvider("code-llm", returns="CODE_RESP")
    cap = FakeCapability(providers=[], domain_providers={"code": [code_only]})
    app = make_bare_app(cap)

    out = await app.pinned_execute("think", "code-llm", prompt="refactor")

    assert out == "CODE_RESP"
