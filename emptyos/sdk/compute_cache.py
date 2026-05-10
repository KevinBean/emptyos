"""Content-addressed compute cache — for slow deterministic methods.

Per-process LRU keyed by `(app_id, namespace, inputs_hash, version)`. Used
by calculator-framework methods that take seconds-to-minutes per run and
produce deterministic output (EMTP solve, FEM rating, OpenDSS sweep).

Distinct from `llm_cache.py` (vault-backed, keyed by caller-supplied logical
key) and `think_cache.py` (per-app SQLite, keyed by prompt contents). This
one is in-process only — daemon restart clears it. SQLite persistence is a
v0.3 follow-up; not warranted until the working-set warm-up is shown to
matter beyond the daemon lifetime.

Public surface, used via BaseApp helpers:

    self.cache_compute(namespace, key, async_fn, version="1") -> result
    self.cache_clear(namespace=None) -> int   # number of entries dropped

Don't use this for:
- Methods with side effects you want to re-run (vault writes, event emits)
  — wrap only the pure-compute portion of the method.
- LLM responses — use `BaseApp.think(cache=True)` / `llm_cache`.
- User-facing data — a process-restart wipes everything.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Awaitable, Callable

# Module-global store keyed by app_id; each app gets its own LRU bounded
# capacity. Keeping it module-global rather than per-instance lets test
# harnesses inspect / clear without importing from BaseApp.
_DEFAULT_CAPACITY = 128
_caches: dict[str, OrderedDict] = {}


def _store(app_id: str) -> OrderedDict:
    if app_id not in _caches:
        _caches[app_id] = OrderedDict()
    return _caches[app_id]


def _make_key(namespace: str, key: str, version: str) -> str:
    return f"{namespace}::{version}::{key}"


def get(app_id: str, namespace: str, key: str, version: str = "1") -> Any | None:
    """Return cached value or None. Bumps the entry to MRU on hit."""
    k = _make_key(namespace, key, version)
    store = _store(app_id)
    if k in store:
        store.move_to_end(k)
        return store[k]
    return None


def put(
    app_id: str,
    namespace: str,
    key: str,
    value: Any,
    version: str = "1",
    capacity: int = _DEFAULT_CAPACITY,
) -> None:
    """Insert or update; evict LRU when capacity exceeded."""
    k = _make_key(namespace, key, version)
    store = _store(app_id)
    store[k] = value
    store.move_to_end(k)
    while len(store) > capacity:
        store.popitem(last=False)


def clear(app_id: str | None = None, namespace: str | None = None) -> int:
    """Drop entries; return count.

    No args → clear everything across all apps (for tests).
    app_id only → clear that app's cache.
    app_id + namespace → drop only entries in that namespace.
    """
    if app_id is None:
        n = sum(len(s) for s in _caches.values())
        _caches.clear()
        return n
    store = _caches.get(app_id)
    if store is None:
        return 0
    if namespace is None:
        n = len(store)
        store.clear()
        return n
    prefix = namespace + "::"
    drop = [k for k in store if k.startswith(prefix)]
    for k in drop:
        store.pop(k, None)
    return len(drop)


def stats(app_id: str | None = None) -> dict:
    """Returns a small JSON-friendly snapshot for debugging / observability."""
    if app_id is None:
        return {a: len(s) for a, s in _caches.items()}
    return {"app_id": app_id, "size": len(_store(app_id))}


async def cache_or_compute(
    app_id: str,
    namespace: str,
    key: str,
    fn: Callable[[], Awaitable[Any]],
    version: str = "1",
    capacity: int = _DEFAULT_CAPACITY,
) -> tuple[Any, bool]:
    """Wrap an async compute fn with cache lookup. Returns (value, hit_bool).

    On hit: returns cached value without invoking fn. On miss: awaits fn,
    stores result, returns it. Exceptions are NOT cached (fn re-runs on
    next call after a failure).
    """
    cached = get(app_id, namespace, key, version=version)
    if cached is not None:
        return cached, True
    value = await fn()
    put(app_id, namespace, key, value, version=version, capacity=capacity)
    return value, False
