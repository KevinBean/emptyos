"""The embedding cache must not load until something actually embeds.

eos-insights 2026-08-05 traced the "~2s BaseApp.setup() boot tax" (a proposal
open since 2026-06-25) to `Embedder.__init__` doing a synchronous
`json.loads` of the on-disk vector cache. On this machine that file is 265 MB:
**2.4 s of blocked event loop and 0.39 GB resident, per Embedder**.

Two things made it expensive rather than merely slow:

* `available` — by far the most common call, and the ONLY thing journal's
  `setup()` wanted — reads provider config and pings reachability. It never
  touches a single cached vector, so the whole cost bought nothing. Measured
  live: journal 208 boot samples, 2.65 s median, 97 s worst.
* `_embedder()` cached per BaseApp instance, so every app touching embeddings
  kept its own copy of the same content-hash-keyed file.

These tests pin both. All fail against the pre-fix code.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from emptyos.sdk.embeddings import Embedder


@pytest.fixture()
def cache_file(tmp_path):
    """A cache on disk, named the way Embedder namespaces it per model."""
    probe = Embedder(cache_path=tmp_path / "shared.json")
    probe.cache_path.parent.mkdir(parents=True, exist_ok=True)
    probe.cache_path.write_text(json.dumps({"sig-a": [0.5] * 4}), encoding="utf-8")
    return tmp_path / "shared.json"


# ── laziness ─────────────────────────────────────────────────────────────────

def test_construction_does_not_read_the_cache(cache_file):
    e = Embedder(cache_path=cache_file)
    assert e.cache_loaded is False


def test_available_does_not_read_the_cache(cache_file):
    """The load-bearing one: this is the call journal's setup() makes, and the
    only reason it ever paid 2.4s."""
    e = Embedder(cache_path=cache_file)
    _ = e.available
    assert e.cache_loaded is False


def test_touching_the_cache_loads_it(cache_file):
    e = Embedder(cache_path=cache_file)
    assert e.cache == {"sig-a": [0.5] * 4}
    assert e.cache_loaded is True


def test_the_load_happens_once(cache_file):
    e = Embedder(cache_path=cache_file)
    _ = e.cache
    cache_file.parent.joinpath(e.cache_path.name).write_text("{}", encoding="utf-8")
    assert e.cache == {"sig-a": [0.5] * 4}, "re-read a file it had already loaded"


def test_a_missing_cache_file_is_an_empty_dict(tmp_path):
    e = Embedder(cache_path=tmp_path / "nothing-here.json")
    assert e.cache == {}
    assert e.cache_loaded is True


def test_an_unreadable_cache_degrades_to_empty(cache_file):
    e = Embedder(cache_path=cache_file)
    e.cache_path.write_text("{ this is not json", encoding="utf-8")
    assert e.cache == {}


def test_writes_still_work_through_the_property(cache_file):
    e = Embedder(cache_path=cache_file)
    e.cache["sig-b"] = [1.0] * 4
    assert set(e.cache) == {"sig-a", "sig-b"}


def test_the_setter_still_works(cache_file):
    """`self.cache = {}` appears in the error path — the property must accept it."""
    e = Embedder(cache_path=cache_file)
    e.cache = {"replaced": [0.0]}
    assert e.cache == {"replaced": [0.0]}
    assert e.cache_loaded is True


# ── the async path must not block the loop on that load ──────────────────────

def test_embed_many_loads_the_cache_off_the_event_loop(cache_file, monkeypatch):
    """A 2.4s json.loads on the loop is the sync-in-async wedge class. The load
    still happens — it just happens in a thread."""
    e = Embedder(cache_path=cache_file)
    loaded_in: dict[str, str] = {}
    real = e._ensure_cache

    def spy():
        import threading

        # Record only the call that ACTUALLY reads the file. `_ensure_cache` is
        # idempotent and later calls are no-ops, so recording every call would
        # measure whichever happened last (main thread) rather than the load.
        first = e._cache is None
        real()
        if first:
            loaded_in["thread"] = threading.current_thread().name

    monkeypatch.setattr(e, "_ensure_cache", spy)
    monkeypatch.setattr(type(e), "available", property(lambda self: False))

    async def go():
        import threading

        loaded_in["loop"] = threading.current_thread().name
        return await e.embed_many(["some text"])

    asyncio.run(go())
    assert loaded_in.get("thread"), "the cache was never loaded"
    assert loaded_in["thread"] != loaded_in["loop"], (
        "cache parsed on the event loop thread — that is the wedge"
    )


# ── one Embedder per daemon, not per app ─────────────────────────────────────

def test_apps_share_one_embedder_via_the_kernel(tmp_path):
    """0.39 GB of identical vectors was being held once per app. The cache file
    is content-hash keyed and shared by path, so the split isolated nothing."""
    from emptyos.sdk.base_app import BaseApp

    class _Cfg:
        data_dir = tmp_path

    kernel = type("K", (), {"config": _Cfg()})()

    def _app():
        a = object.__new__(BaseApp)
        a.kernel = kernel
        return a

    first, second = _app()._embedder(), _app()._embedder()
    assert first is second, "each app built its own copy of the same cache"


def test_a_kernel_that_refuses_attributes_still_works(tmp_path):
    """Test doubles use __slots__-ish stand-ins; degrade to per-app rather than
    raising, since an embedder is not worth breaking an app over."""
    from emptyos.sdk.base_app import BaseApp

    class _Locked:
        __slots__ = ("config",)

        def __init__(self, cfg):
            self.config = cfg

    kernel = _Locked(type("C", (), {"data_dir": tmp_path})())
    a = object.__new__(BaseApp)
    a.kernel = kernel
    assert isinstance(a._embedder(), Embedder)
