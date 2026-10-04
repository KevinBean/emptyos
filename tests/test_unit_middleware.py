"""System tests for the capability middleware registry + Capability.execute integration.

Covers the slot that future plugins (Headroom compression, future .eos-personal
redaction, etc.) hook into. Tests use a stub capability + provider so no
external services or LLM calls are touched.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities import Capability, Provider
from emptyos.capabilities.middleware import (
    list_middlewares,
    register,
    run_chain,
    unregister,
)


class _Fake(Provider):
    name = "fake"
    is_cloud = False

    async def available(self):
        return True

    async def execute(self, **kw):
        return {"echo": kw}


class _Mark:
    """Minimal middleware that stamps a marker into the payload."""

    name = "test-mark"
    capability_scope = None  # all capabilities
    direction = "request"
    order = 10

    async def apply(self, cap_name, payload, context):
        payload = dict(payload)
        payload["_marker"] = cap_name
        return payload


class _ScopedMark:
    name = "test-scoped"
    capability_scope = {"think"}
    direction = "request"
    order = 20

    async def apply(self, cap_name, payload, context):
        payload = dict(payload)
        payload["_scoped"] = True
        return payload


class _Broken:
    name = "test-broken"
    capability_scope = None
    direction = "request"
    order = 5  # runs first so we can prove the chain survives

    async def apply(self, cap_name, payload, context):
        raise RuntimeError("intentional middleware failure")


@pytest.fixture(autouse=True)
def _clean_chain():
    """Each test starts with an empty middleware chain."""
    for entry in list_middlewares():
        unregister(entry["name"])
    yield
    for entry in list_middlewares():
        unregister(entry["name"])


def _stub_cap(name="think"):
    cap = Capability()
    cap.name = name
    cap.add_provider(_Fake())
    return cap


@pytest.mark.asyncio
async def test_empty_chain_is_noop():
    cap = _stub_cap()
    r = await cap.execute(prompt="hi")
    assert r.value == {"echo": {"prompt": "hi"}}, "no middleware should be present"


@pytest.mark.asyncio
async def test_middleware_runs_and_mutates_payload():
    cap = _stub_cap()
    register(_Mark())
    r = await cap.execute(prompt="hi")
    assert r.value["echo"].get("_marker") == "think"


@pytest.mark.asyncio
async def test_capability_scope_filters():
    cap_think = _stub_cap("think")
    cap_draw = _stub_cap("draw")
    register(_ScopedMark())
    r_think = await cap_think.execute(prompt="hi")
    r_draw = await cap_draw.execute(prompt="hi")
    assert r_think.value["echo"].get("_scoped") is True
    assert "_scoped" not in r_draw.value["echo"]


@pytest.mark.asyncio
async def test_broken_middleware_does_not_break_chain():
    cap = _stub_cap()
    register(_Broken())
    register(_Mark())
    r = await cap.execute(prompt="hi")
    # The marker still gets stamped, proving the chain continued past the
    # broken middleware rather than aborting.
    assert r.value["echo"].get("_marker") == "think"


@pytest.mark.asyncio
async def test_ordering_is_respected():
    """Higher order = runs later in the request chain. Last writer wins."""

    class A:
        name = "a"
        capability_scope = None
        direction = "request"
        order = 100

        async def apply(self, cap, payload, context):
            payload = dict(payload)
            payload["last_writer"] = "a"
            return payload

    class B:
        name = "b"
        capability_scope = None
        direction = "request"
        order = 200  # runs after a

        async def apply(self, cap, payload, context):
            payload = dict(payload)
            payload["last_writer"] = "b"
            return payload

    register(A())
    register(B())
    cap = _stub_cap()
    r = await cap.execute(prompt="hi")
    assert r.value["echo"]["last_writer"] == "b", "higher order runs later"


@pytest.mark.asyncio
async def test_register_is_idempotent_on_name():
    register(_Mark())
    register(_Mark())  # same name, should not duplicate
    assert len(list_middlewares()) == 1


@pytest.mark.asyncio
async def test_response_direction_does_not_intercept_request():
    """A response-only middleware must not see the request-side call."""

    class ResponseOnly:
        name = "resp-only"
        capability_scope = None
        direction = "response"
        order = 10

        async def apply(self, cap, payload, context):
            # If this were called on a dict, we'd notice
            if isinstance(payload, dict):
                payload = dict(payload)
                payload["_response_polluted"] = True
            return payload

    register(ResponseOnly())
    cap = _stub_cap()
    r = await cap.execute(prompt="hi")
    assert "_response_polluted" not in r.value["echo"]


@pytest.mark.asyncio
async def test_unregister_removes_middleware():
    register(_Mark())
    assert any(m["name"] == "test-mark" for m in list_middlewares())
    unregister("test-mark")
    assert all(m["name"] != "test-mark" for m in list_middlewares())


@pytest.mark.asyncio
async def test_middleware_can_gate_on_context_provider():
    """A middleware can read context['is_cloud'] / context['provider_name']
    to skip work for local providers — pattern used by the Headroom plugin.
    """

    class CloudOnly:
        name = "cloud-only"
        capability_scope = None
        direction = "request"
        order = 10

        async def apply(self, cap, payload, context):
            if not context.get("is_cloud"):
                return payload  # skip for local providers
            payload = dict(payload)
            payload["_compressed"] = True
            return payload

    register(CloudOnly())

    # Cloud provider — middleware should mark the payload
    out = await run_chain(
        "think",
        {"prompt": "x"},
        direction="request",
        context={"provider_name": "openai", "is_cloud": True},
    )
    assert out.get("_compressed") is True

    # Local provider — middleware should skip
    out = await run_chain(
        "think",
        {"prompt": "x"},
        direction="request",
        context={"provider_name": "ollama", "is_cloud": False},
    )
    assert "_compressed" not in out


@pytest.mark.asyncio
async def test_middleware_can_offload_sync_work_without_blocking_loop():
    """Regression for the 2026-05-24 sync-in-loop wedge (originally fix b2a0393).

    A middleware that wraps a synchronous CPU/IO call in asyncio.to_thread
    must not block other awaitables on the event loop. Without to_thread,
    sync work inside apply() wedges the entire daemon — listener stays up,
    every async route parks. This was the first of three incidents that led
    to the retired `headroom` plugin being deleted + blacklisted on
    2026-05-26 (see plugins/BLACKLIST.toml); the regression itself stays
    here because the platform-level rule applies to every middleware.
    """
    import asyncio
    import time

    sleep_s = 0.3

    class SlowSync:
        name = "slow-sync"
        capability_scope = None
        direction = "request"
        order = 10

        async def apply(self, cap, payload, context):
            await asyncio.to_thread(time.sleep, sleep_s)
            payload = dict(payload)
            payload["_slow_ran"] = True
            return payload

    register(SlowSync())

    tick_count = 0

    async def ticker():
        nonlocal tick_count
        deadline = time.monotonic() + sleep_s
        while time.monotonic() < deadline:
            await asyncio.sleep(0.02)
            tick_count += 1

    cap = _stub_cap()
    chain_result, _ = await asyncio.gather(cap.execute(prompt="hi"), ticker())
    assert chain_result.value["echo"].get("_slow_ran") is True
    # During the 0.3s offload, ticker had room to fire ~15 times (0.3 / 0.02).
    # Generous floor of 8 absorbs scheduler jitter without losing the signal.
    # A sync time.sleep inside apply() would have parked the loop and left
    # tick_count near zero.
    assert tick_count >= 8, (
        f"event loop appears blocked during a to_thread offload — "
        f"ticker fired only {tick_count} times in {sleep_s}s"
    )


@pytest.mark.asyncio
async def test_middleware_timeout_is_enforced_and_chain_continues():
    """A middleware that exceeds its budget is dropped; the chain continues.

    Hard enforcement of the rule that broke `headroom` three times before it
    got blacklisted: a wedged apply() must not park the capability chain.
    """
    import asyncio

    class Wedged:
        name = "wedged"
        capability_scope = None
        direction = "request"
        order = 5
        timeout_s = 0.05  # tight budget; 1s sleep will trip it

        async def apply(self, cap, payload, context):
            await asyncio.sleep(1.0)
            payload = dict(payload)
            payload["_wedged_ran"] = True
            return payload

    register(Wedged())
    register(_Mark())  # runs after Wedged (order=10 vs 5)
    cap = _stub_cap()

    import time

    t0 = time.monotonic()
    r = await cap.execute(prompt="hi")
    elapsed = time.monotonic() - t0

    # The wedged middleware was timed out, so the marker still got stamped
    # by the second middleware — chain continued past the wedge.
    assert r.value["echo"].get("_marker") == "think"
    # And its payload mutation never landed.
    assert "_wedged_ran" not in r.value["echo"]
    # The whole call returned well before the 1s sleep would have completed.
    assert elapsed < 0.5, f"timeout was not enforced — call took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_list_middlewares_surfaces_timeout_budget():
    """Operators reading list_middlewares() should see each middleware's budget."""
    from emptyos.capabilities.middleware import DEFAULT_MIDDLEWARE_TIMEOUT_S

    class Custom:
        name = "custom-budget"
        capability_scope = None
        direction = "request"
        order = 10
        timeout_s = 12.5

        async def apply(self, cap, payload, context):
            return payload

    register(_Mark())  # no declared timeout
    register(Custom())
    entries = {m["name"]: m for m in list_middlewares()}
    assert entries["test-mark"]["timeout_s"] == DEFAULT_MIDDLEWARE_TIMEOUT_S
    assert entries["custom-budget"]["timeout_s"] == 12.5


@pytest.mark.asyncio
async def test_run_chain_directly_with_response_direction():
    """run_chain can also be called outside Capability.execute for response-side use."""

    class WrapResult:
        name = "wrap"
        capability_scope = None
        direction = "response"
        order = 10

        async def apply(self, cap, payload, context):
            return {"wrapped": payload}

    register(WrapResult())
    out = await run_chain("think", "raw", direction="response")
    assert out == {"wrapped": "raw"}
