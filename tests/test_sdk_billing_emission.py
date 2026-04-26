"""Regression tests for billing-event emission from non-default think paths.

Protects the wiring that makes streaming + pinned calls show up in billing
with real token counts (not the 4-char heuristic fallback). Bugs here silently
degrade cost accuracy, so an event-level assertion catches them before the
billing UI stops matching the OpenAI dashboard.

Covers:
  - think_stream(): usage chunk from the capability becomes a think:executed row
  - think_stream() with a pinned provider: same, but via the pinned branch
  - pinned_execute("think", ...): emits think:executed with provider.last_usage
  - pinned_execute() on a non-think capability: does NOT emit think:executed
"""

from __future__ import annotations

import pytest

from fake_kernel import FakeCapability, FakeProvider, make_bare_app


async def _drain(agen):
    return [c async for c in agen]


def _think_events(app):
    return [(t, d) for (t, d) in app.kernel.events.emitted if t == "think:executed"]


@pytest.mark.asyncio
async def test_think_stream_emits_billing_with_usage_from_chain():
    """Capability-chain stream: usage chunk → think:executed carries token counts."""
    usage = {
        "provider": "openai", "model": "gpt-5-mini",
        "prompt_tokens": 1000, "completion_tokens": 500,
        "cached_tokens": 0, "total_tokens": 1500,
        "cost": 0.00125,
    }
    cap = FakeCapability(
        providers=[],
        chain_chunks=[
            {"provider_used": "openai", "is_cloud": True},
            {"text": "hello", "done": False},
            {"usage": usage, "done": False},
            {"text": "", "done": True},
        ],
    )
    app = make_bare_app(cap)

    await _drain(app.think_stream("hi"))

    events = _think_events(app)
    assert len(events) == 1, f"expected 1 think:executed, got {len(events)}"
    _, data = events[0]
    assert data["provider"] == "openai"
    assert data["is_cloud"] is True
    assert data["prompt_tokens"] == 1000
    assert data["completion_tokens"] == 500
    assert data["cost"] == pytest.approx(0.00125)
    assert data["streamed"] is True


@pytest.mark.asyncio
async def test_think_stream_pinned_provider_emits_billing_from_stream_usage():
    """Pinned-provider stream: usage chunk from the provider is picked up."""
    usage = {
        "provider": "openai", "model": "gpt-5",
        "prompt_tokens": 2000, "completion_tokens": 1000,
        "cost": 0.0125,
    }
    pinned = FakeProvider(
        "openai",
        chunks=[
            {"text": "hi", "done": False},
            {"usage": usage, "done": False},
            {"text": "", "done": True},
        ],
    )
    app = make_bare_app(FakeCapability([pinned]))

    await _drain(app.think_stream("q", provider="openai"))

    events = _think_events(app)
    assert len(events) == 1
    _, data = events[0]
    assert data["provider"] == "openai"
    assert data["prompt_tokens"] == 2000
    assert data["completion_tokens"] == 1000
    assert data["cost"] == pytest.approx(0.0125)
    assert data["streamed"] is True


@pytest.mark.asyncio
async def test_think_stream_falls_back_to_provider_last_usage():
    """No usage chunk in stream → helper reads provider.last_usage as a fallback."""
    pinned = FakeProvider(
        "openai",
        chunks=[
            {"text": "hi", "done": False},
            {"text": "", "done": True},
        ],
    )
    # Simulate the openai_compat stream stashing usage on the provider
    pinned.last_usage = {
        "provider": "openai", "prompt_tokens": 500,
        "completion_tokens": 250, "cost": 0.000625,
    }
    app = make_bare_app(FakeCapability([pinned]))

    await _drain(app.think_stream("q", provider="openai"))

    events = _think_events(app)
    assert len(events) == 1
    _, data = events[0]
    assert data["prompt_tokens"] == 500
    assert data["cost"] == pytest.approx(0.000625)
    # last_usage should be cleared after emission
    assert pinned.last_usage is None


@pytest.mark.asyncio
async def test_pinned_execute_think_emits_billing():
    """pinned_execute('think', ...) emits think:executed with last_usage merged."""
    openai = FakeProvider("openai", returns="answer")
    openai.last_usage = {
        "provider": "openai", "model": "gpt-5-mini",
        "prompt_tokens": 100, "completion_tokens": 50,
        "cost": 0.000125,
    }
    app = make_bare_app(FakeCapability([openai]))

    result = await app.pinned_execute("think", "openai", prompt="hello")

    assert result == "answer"
    events = _think_events(app)
    assert len(events) == 1
    _, data = events[0]
    assert data["provider"] == "openai"
    assert data["routed_by"] == "pinned"
    assert data["prompt_tokens"] == 100
    assert data["cost"] == pytest.approx(0.000125)


@pytest.mark.asyncio
async def test_pinned_execute_non_think_does_not_emit():
    """pinned_execute('speak', ...) must not emit think:executed — it's not a think call."""
    speak_provider = FakeProvider("openai-tts", returns=b"audio")
    app = make_bare_app(FakeCapability([speak_provider]))
    # Override so kernel.capability('speak') returns our fake too
    app.kernel._cap = FakeCapability([speak_provider])

    await app.pinned_execute("speak", "openai-tts", text="hi")

    assert _think_events(app) == []


@pytest.mark.asyncio
async def test_think_stream_emits_even_when_consumer_breaks_early():
    """If the caller stops iterating mid-stream, the finally block still emits.

    This covers the common case where a UI cancels a streaming response —
    OpenAI has already billed for tokens produced up to that point, so
    billing must still record the partial call.
    """
    pinned = FakeProvider(
        "openai",
        chunks=[
            {"text": "partial", "done": False},
            {"text": "more", "done": False},
        ],
    )
    pinned.last_usage = {"provider": "openai", "prompt_tokens": 10,
                        "completion_tokens": 5, "cost": 0.00001}
    app = make_bare_app(FakeCapability([pinned]))

    agen = app.think_stream("q", provider="openai")
    first = await agen.__anext__()
    assert first["text"] == "partial"
    await agen.aclose()

    events = _think_events(app)
    assert len(events) == 1
    _, data = events[0]
    assert data["provider"] == "openai"
