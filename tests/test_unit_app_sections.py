"""Unit tests for emptyos/sdk/app_sections.py — pure, no daemon needed."""

from __future__ import annotations

from types import SimpleNamespace

from emptyos.sdk.app_sections import SECTION_META, group_by_category


def _mf(app_id, *, category=None, name=None, prefix=None, desc="", icon="", user_intent=None):
    """Build a minimal manifest stand-in (duck-typed like AppManifest)."""
    app = {}
    if category is not None:
        app["store_category"] = category
    if icon:
        app["icon"] = icon
    if user_intent is not None:
        app["user_intent"] = user_intent
    raw = {"app": app}
    provides = {"web": {"prefix": prefix}} if prefix else {}
    return SimpleNamespace(
        id=app_id,
        name=name or app_id,
        description=desc,
        provides=provides,
        raw=raw,
    )


def _registry(*manifests):
    return {m.id: m for m in manifests}


def test_buckets_by_store_category():
    regs = _registry(
        _mf("expense", category="productivity"),
        _mf("jianpu", category="creative"),
        _mf("cad", category="engineering"),
    )
    sections = group_by_category(regs)
    keys = {s["key"] for s in sections}
    assert keys == {"productivity", "creative", "engineering"}
    by_key = {s["key"]: s for s in sections}
    assert by_key["productivity"]["apps"][0]["id"] == "expense"
    assert by_key["productivity"]["count"] == 1


def test_missing_or_unknown_category_falls_to_other():
    regs = _registry(
        _mf("noCat"),  # no store_category at all
        _mf("weird", category="totally-made-up"),
    )
    sections = group_by_category(regs)
    assert len(sections) == 1
    assert sections[0]["key"] == "other"
    assert sections[0]["count"] == 2


def test_sections_sorted_by_order_then_label():
    regs = _registry(
        _mf("a", category="other"),
        _mf("b", category="engineering"),
        _mf("c", category="core"),
        _mf("d", category="ai"),
    )
    sections = group_by_category(regs)
    orders = [s["order"] for s in sections]
    assert orders == sorted(orders)
    # core (10) first, other (999) last
    assert sections[0]["key"] == "core"
    assert sections[-1]["key"] == "other"


def test_apps_sorted_by_name_within_section():
    regs = _registry(
        _mf("z-app", category="dev", name="Zebra"),
        _mf("a-app", category="dev", name="Alpha"),
        _mf("m-app", category="dev", name="Middle"),
    )
    sections = group_by_category(regs)
    names = [a["name"] for a in sections[0]["apps"]]
    assert names == ["Alpha", "Middle", "Zebra"]


def test_empty_sections_dropped():
    regs = _registry(_mf("only", category="ai"))
    sections = group_by_category(regs)
    assert [s["key"] for s in sections] == ["ai"]


def test_reachable_ids_filter():
    regs = _registry(
        _mf("shown", category="ai"),
        _mf("hidden", category="ai"),
    )
    sections = group_by_category(regs, reachable_ids={"shown"})
    assert len(sections) == 1
    ids = [a["id"] for a in sections[0]["apps"]]
    assert ids == ["shown"]


def test_app_entry_shape():
    regs = _registry(
        _mf("expense", category="productivity", name="Expense",
            prefix="/expense", desc="Track spending", icon="💰"),
    )
    app = group_by_category(regs)[0]["apps"][0]
    assert app == {
        "id": "expense",
        "name": "Expense",
        "description": "Track spending",
        "web_prefix": "/expense",
        "icon": "💰",
        "user_intent": [],
    }


def test_user_intent_flows_through():
    regs = _registry(
        _mf("expense", category="productivity",
            user_intent=["track my spending", "记账"]),
    )
    app = group_by_category(regs)[0]["apps"][0]
    assert app["user_intent"] == ["track my spending", "记账"]


def test_section_meta_covers_canonical_categories():
    # store.md canonical set must all be presentable.
    for cat in ("core", "productivity", "creative", "engineering",
                "personal", "ai", "dev", "meta", "other"):
        assert cat in SECTION_META
        assert "label" in SECTION_META[cat]
        assert "order" in SECTION_META[cat]
