"""Unit tests for BaseApp.think_pinned call styles."""

from __future__ import annotations

import asyncio

from emptyos.sdk.base_app import BaseApp


def test_think_pinned_exact_provider_style():
    app = BaseApp.__new__(BaseApp)
    calls = []

    async def fake_provider(provider, prompt, domain, kwargs):
        calls.append((provider, prompt, domain, kwargs))
        return "ok"

    app._think_with_provider = fake_provider

    out = asyncio.run(
        BaseApp.think_pinned(app, "openai-mini", "hello", "text", temperature=0.1)
    )

    assert out == "ok"
    assert calls == [("openai-mini", "hello", "text", {"temperature": 0.1})]


def test_think_pinned_provider_chain_style():
    app = BaseApp.__new__(BaseApp)
    calls = []

    async def fake_provider(provider, prompt, domain, kwargs):
        calls.append((provider, prompt, domain, kwargs))
        return "ok" if provider == "good" else None

    app._think_with_provider = fake_provider

    out = asyncio.run(
        BaseApp.think_pinned(app, "classify this", providers=("bad", "good"), timeout_s=1)
    )

    assert out == "ok"
    assert [c[0] for c in calls] == ["bad", "good"]

