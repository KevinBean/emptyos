"""Tier resolution helpers for ``release.toml``.

A tier is a named bundle of apps + plugins + skills, optionally inheriting
from another tier via ``extends = "..."``. Three callers need to resolve the
same recursion (which apps end up in which tier after the extends chain is
walked):

* ``apps/tiers/`` — UI for editing tier membership
* ``scripts/release-public.py`` — filter snapshot to public-shippable subset
* ``scripts/release-readiness.py`` — bucket apps by ship-readiness

This module is pure-data: callers parse ``release.toml`` with whatever they
prefer (``tomllib`` for read-only, ``tomlkit`` for round-trip) and pass in
the parsed ``tiers`` mapping. No I/O here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def resolve_tier(tiers: Mapping[str, Any], name: str, key: str = "apps") -> set[str]:
    """All items at ``tiers[name][key]`` including those inherited via ``extends``.

    Cycles are broken; missing tier names yield an empty set rather than raising.
    """
    return _walk(tiers, name, key, set())


def tier_union(
    tiers: Mapping[str, Any],
    names: Iterable[str],
    key: str = "apps",
) -> set[str]:
    """Union of :func:`resolve_tier` across multiple tier names."""
    out: set[str] = set()
    for n in names:
        out |= resolve_tier(tiers, n, key)
    return out


def reverse_index(tiers: Mapping[str, Any], key: str = "apps") -> dict[str, set[str]]:
    """Map each item to the set of tier names that effectively include it."""
    out: dict[str, set[str]] = {}
    for tier_name in tiers:
        for item in resolve_tier(tiers, tier_name, key):
            out.setdefault(item, set()).add(tier_name)
    return out


def _walk(tiers: Mapping[str, Any], name: str, key: str, seen: set[str]) -> set[str]:
    if name in seen or name not in tiers:
        return set()
    seen.add(name)
    t = tiers[name]
    out = set(t.get(key, []) or [])
    parent = t.get("extends")
    if parent:
        out |= _walk(tiers, parent, key, seen)
    return out
