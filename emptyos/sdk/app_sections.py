"""App sections — stable, declared grouping of apps by ``store_category``.

The home launcher historically grouped apps via the dependency-graph
clustering in ``emptyos/sdk/clustering.py`` — dynamic, but the section names
shift as wiring changes and apps land in surprising buckets. This module is
the *declared* alternative: each app states a ``[app] store_category`` in its
manifest (the canonical set lives in ``.claude/rules/store.md``), and we bucket
into fixed, ordered sections.

Pure module — no kernel import, so it unit-tests without a daemon (mirrors
``clustering.py``). The web layer passes in the manifest registry + the set of
reachable app ids; everything else is deterministic.
"""

from __future__ import annotations

from typing import Any, Iterable

from emptyos.sdk.app_icons import manifest_icon_id

# Canonical store_category -> section presentation. Keys match the set in
# `.claude/rules/store.md`; "other" is the fallback bucket for apps that
# declare no (or an unknown) category. `order` drives top-to-bottom layout.
SECTION_META: dict[str, dict[str, Any]] = {
    "core": {"label": "Essentials", "icon": "⭐", "order": 10},
    "productivity": {"label": "Productivity", "icon": "✅", "order": 20},
    "ai": {"label": "AI & Agents", "icon": "\U0001f9e0", "order": 30},
    "engineering": {"label": "Engineering", "icon": "\U0001f4d0", "order": 40},
    "creative": {"label": "Creative", "icon": "\U0001f3a8", "order": 50},
    "dev": {"label": "Developer", "icon": "\U0001f6e0️", "order": 60},
    "personal": {"label": "Personal", "icon": "\U0001f464", "order": 70},
    "meta": {"label": "System", "icon": "\U0001f5c2️", "order": 80},
    "other": {"label": "Other", "icon": "\U0001f4e6", "order": 999},
}

DEFAULT_CATEGORY = "other"


def category_of(manifest: Any) -> str:
    """Read ``[app] store_category`` off a manifest, normalized to a known key.

    Returns ``DEFAULT_CATEGORY`` for absent/unknown categories. Public so other
    section-building surfaces (e.g. the hub launcher panel) classify identically.
    """
    raw = getattr(manifest, "raw", None) or {}
    app = raw.get("app", {}) or {}
    cat = (app.get("store_category") or "").strip().lower()
    return cat if cat in SECTION_META else DEFAULT_CATEGORY


def sections_from_buckets(
    buckets: dict[str, list[dict]], *, app_sort_key
) -> list[dict[str, Any]]:
    """Assemble ``{category: [app dicts]}`` into ordered, non-empty sections.

    Shared by every surface that groups apps by category — the app dict shape is
    the caller's (``name``/``web_prefix`` for the API, ``title``/``href`` for the
    hub launcher), so the caller passes its own ``app_sort_key``.
    """
    sections: list[dict[str, Any]] = []
    for key, apps in buckets.items():
        meta = SECTION_META.get(key, SECTION_META[DEFAULT_CATEGORY])
        apps.sort(key=app_sort_key)
        sections.append(
            {
                "key": key,
                "label": meta["label"],
                "icon": meta["icon"],
                "order": meta["order"],
                "count": len(apps),
                "apps": apps,
            }
        )
    sections.sort(key=lambda s: (s["order"], s["label"].lower()))
    return sections


def _app_entry(manifest: Any, icon_ids: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    raw = getattr(manifest, "raw", None) or {}
    app = raw.get("app", {}) or {}
    provides = getattr(manifest, "provides", None) or {}
    return {
        "id": getattr(manifest, "id", ""),
        "name": getattr(manifest, "name", "") or getattr(manifest, "id", ""),
        "description": getattr(manifest, "description", "") or "",
        "web_prefix": (provides.get("web", {}) or {}).get("prefix", ""),
        "icon": app.get("icon", ""),
        "icon_id": manifest_icon_id(manifest, icon_ids),
        # Function-search data layer — lets surfaces filter by what an app does,
        # not just its name (see .claude/rules/user-intent.md).
        "user_intent": app.get("user_intent", []),
    }


def group_by_category(
    manifests: dict[str, Any] | Iterable[Any],
    reachable_ids: Iterable[str] | None = None,
    *,
    icon_ids: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Bucket apps into declared ``store_category`` sections.

    ``manifests`` is the manifest registry (id -> manifest) or any iterable of
    manifests. ``reachable_ids`` optionally restricts to apps that are actually
    serving (same gate the launcher uses); ``None`` includes everything.

    Returns sections sorted by ``order`` then label, each as::

        {"key", "label", "icon", "order", "count", "apps": [<app entry>, ...]}

    Empty sections are dropped; apps within a section are sorted by name.
    """
    items = manifests.values() if isinstance(manifests, dict) else manifests
    allow = set(reachable_ids) if reachable_ids is not None else None

    buckets: dict[str, list[dict[str, Any]]] = {}
    for m in items:
        mid = getattr(m, "id", None)
        if not mid:
            continue
        if allow is not None and mid not in allow:
            continue
        buckets.setdefault(category_of(m), []).append(_app_entry(m, icon_ids))

    return sections_from_buckets(
        buckets, app_sort_key=lambda a: (a["name"] or a["id"]).lower()
    )
