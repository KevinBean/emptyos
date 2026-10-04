"""Tests for emptyos.sdk.compute_cache — per-process LRU for slow methods."""

from __future__ import annotations

import asyncio

import pytest

from emptyos.sdk.compute_cache import (
    cache_or_compute,
    clear,
    get,
    put,
    stats,
)


@pytest.fixture(autouse=True)
def _isolated_cache():
    clear()
    yield
    clear()


def test_put_get_round_trip():
    put("appA", "ns", "k1", {"value": 42})
    assert get("appA", "ns", "k1") == {"value": 42}


def test_get_returns_none_for_missing_keys():
    assert get("appA", "ns", "missing") is None


def test_versioning_isolates_entries():
    put("appA", "ns", "k1", "v1-result", version="1")
    put("appA", "ns", "k1", "v2-result", version="2")
    assert get("appA", "ns", "k1", version="1") == "v1-result"
    assert get("appA", "ns", "k1", version="2") == "v2-result"


def test_apps_have_isolated_caches():
    put("appA", "ns", "k1", "A")
    put("appB", "ns", "k1", "B")
    assert get("appA", "ns", "k1") == "A"
    assert get("appB", "ns", "k1") == "B"


def test_namespace_isolates_within_app():
    put("app", "ns1", "k", 1)
    put("app", "ns2", "k", 2)
    assert get("app", "ns1", "k") == 1
    assert get("app", "ns2", "k") == 2


def test_lru_eviction_when_over_capacity():
    for i in range(5):
        put("app", "ns", f"k{i}", i, capacity=3)
    # Only the last 3 keys should survive
    assert get("app", "ns", "k0") is None
    assert get("app", "ns", "k1") is None
    assert get("app", "ns", "k2") == 2
    assert get("app", "ns", "k3") == 3
    assert get("app", "ns", "k4") == 4


def test_get_promotes_entry_to_mru():
    for i in range(3):
        put("app", "ns", f"k{i}", i, capacity=3)
    # Touch k0 → it becomes MRU; next put should evict k1, not k0
    _ = get("app", "ns", "k0")
    put("app", "ns", "k_new", "new", capacity=3)
    assert get("app", "ns", "k0") == 0
    assert get("app", "ns", "k1") is None  # evicted
    assert get("app", "ns", "k2") == 2
    assert get("app", "ns", "k_new") == "new"


def test_clear_targets_specific_namespace():
    put("app", "ns1", "a", 1)
    put("app", "ns1", "b", 2)
    put("app", "ns2", "a", 3)
    n = clear("app", namespace="ns1")
    assert n == 2
    assert get("app", "ns1", "a") is None
    assert get("app", "ns2", "a") == 3


def test_clear_all_for_app():
    put("app", "ns", "k", 1)
    put("other", "ns", "k", 2)
    n = clear("app")
    assert n == 1
    assert get("app", "ns", "k") is None
    assert get("other", "ns", "k") == 2


def test_clear_global_with_no_args():
    put("app", "ns", "k", 1)
    put("other", "ns", "k", 2)
    n = clear()
    assert n == 2
    assert stats() == {}


# ── cache_or_compute (async) ─────────────────────────────────────────


def test_cache_or_compute_runs_fn_on_miss():
    calls = {"n": 0}

    async def expensive():
        calls["n"] += 1
        return {"answer": 42}

    async def go():
        v1, hit1 = await cache_or_compute("app", "ns", "k", expensive)
        v2, hit2 = await cache_or_compute("app", "ns", "k", expensive)
        return v1, hit1, v2, hit2

    v1, hit1, v2, hit2 = asyncio.run(go())
    assert v1 == {"answer": 42}
    assert hit1 is False
    assert v2 == {"answer": 42}
    assert hit2 is True
    assert calls["n"] == 1  # fn only ran on miss


def test_cache_or_compute_does_not_cache_exceptions():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first call fails")
        return "ok"

    async def go():
        with pytest.raises(RuntimeError):
            await cache_or_compute("app", "ns", "k", flaky)
        v, hit = await cache_or_compute("app", "ns", "k", flaky)
        return v, hit

    v, hit = asyncio.run(go())
    assert v == "ok"
    assert hit is False  # second call ran fn, not cached
    assert calls["n"] == 2
