"""Unit tests for agent-loop billing metering (P0.1).

`run_turn` calls `provider.execute_tools()` directly, bypassing `BaseApp.think()`
— so without `_meter_think` the autonomous loop is invisible to billing + the
trace-costs feature. These tests pin the emitter's contract:

  - emits a `think:executed` on the KERNEL bus with normalized token counts
  - normalizes both provider usage dialects (OpenAI prompt/completion_tokens,
    Anthropic input/output_tokens)
  - carries `trace_id` when emitted inside a trace scope (so trace-costs works)
  - stays silent on empty / token-and-cost-less usage (no junk rows)

Pure — no daemon, no LLM.
"""

from __future__ import annotations

import pytest

from emptyos.sdk import trace
from emptyos.sdk.agent_loop import _meter_think
from fake_kernel import make_bare_app, FakeCapability, FakeProvider


def _think_events(app):
    return [(t, d) for (t, d) in app.kernel.events.emitted if t == "think:executed"]


@pytest.mark.asyncio
async def test_meter_think_emits_openai_dialect():
    app = make_bare_app(FakeCapability([]))
    provider = FakeProvider("openai")
    provider.model = "gpt-5-mini"
    usage = {
        "model": "gpt-5-mini",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cached_tokens": 100,
        "cost": 0.00125,
    }

    await _meter_think(app, provider, usage)

    events = _think_events(app)
    assert len(events) == 1
    _, data = events[0]
    assert data["provider"] == "openai"
    assert data["app"] == "test-app"
    assert data["prompt_tokens"] == 1000
    assert data["completion_tokens"] == 500
    assert data["cached_tokens"] == 100
    assert data["cost"] == pytest.approx(0.00125)


@pytest.mark.asyncio
async def test_meter_think_normalizes_anthropic_dialect():
    """Anthropic reports input_tokens/output_tokens — billing wants prompt/completion."""
    app = make_bare_app(FakeCapability([]))
    provider = FakeProvider("anthropic_sdk")
    usage = {
        "model": "claude-opus-4-8",
        "input_tokens": 2000,
        "output_tokens": 800,
        "cache_read_input_tokens": 1500,
        "cost": 0.05,
    }

    await _meter_think(app, provider, usage)

    _, data = _think_events(app)[0]
    assert data["prompt_tokens"] == 2000
    assert data["completion_tokens"] == 800
    assert data["cached_tokens"] == 1500
    assert data["cost"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_meter_think_stamps_trace_id_in_scope():
    app = make_bare_app(FakeCapability([]))
    provider = FakeProvider("openai")
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0}

    token = trace.set_trace_id("trace-deadbeef0")
    try:
        await _meter_think(app, provider, usage)
    finally:
        trace.reset_trace_id(token)

    _, data = _think_events(app)[0]
    assert data["trace_id"] == "trace-deadbeef0"


@pytest.mark.asyncio
async def test_meter_think_no_trace_id_outside_scope():
    app = make_bare_app(FakeCapability([]))
    provider = FakeProvider("openai")
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0}

    await _meter_think(app, provider, usage)

    _, data = _think_events(app)[0]
    assert "trace_id" not in data


@pytest.mark.asyncio
async def test_meter_think_skips_empty_usage():
    app = make_bare_app(FakeCapability([]))
    provider = FakeProvider("openai")

    await _meter_think(app, provider, None)
    await _meter_think(app, provider, {})
    # zero tokens AND zero cost → nothing to bill
    await _meter_think(app, provider, {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0})

    assert _think_events(app) == []


@pytest.mark.asyncio
async def test_meter_think_emits_on_cost_only():
    """Local models report 0 cost but real tokens; a cost-only row must still emit."""
    app = make_bare_app(FakeCapability([]))
    provider = FakeProvider("ollama")
    await _meter_think(app, provider, {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.01})
    assert len(_think_events(app)) == 1
