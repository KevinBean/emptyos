"""Unit tests for the life-tree core (``scripts/build_life_tree.py``).

Daemon-free and vault-free: every case builds its own tiny graph, so these run in
CI without :9000 and without touching anyone's notes.

The validator is the reason this file exists. A checker sitting at zero findings
on a healthy tree has proved nothing until it has been watched going red for each
shape it claims to cover, so every validation case below asserts the *red*
direction, not just that a good graph passes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _core():
    spec = importlib.util.spec_from_file_location(
        "_life_tree_core", REPO / "scripts" / "build_life_tree.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _core()

CFG = {
    "fade_months": 6,
    "now": "2026-08-17",
    "eras": [{"id": "past"}, {"id": "now"}],
    "palaces": [{"id": "a"}, {"id": "b"}],
}


def node(nid, **kw):
    base = {
        "id": nid, "title": nid.title(), "palace": "a", "era": "now", "status": "",
        "date": "", "requires": [], "forecloses": [], "ref": "", "evidence": "",
        "inner": "", "touched": "", "body": "", "_file": Path(f"{nid}.md"),
    }
    base.update(kw)
    return base


def states(nodes, cfg=CFG):
    return {k: v["state"] for k, v in core.compute_states(nodes, cfg).items()}


# ── state derivation ─────────────────────────────────────────────────────────


def test_available_when_every_prerequisite_is_done():
    s = states([node("a", status="done"), node("b", requires=["a"])])
    assert s["b"] == "available"


def test_locked_while_any_prerequisite_is_unmet():
    s = states([node("a", status="done"), node("x"), node("b", requires=["a", "x"])])
    assert s["b"] == "locked"


def test_active_prerequisite_does_not_unlock():
    """`active` means in progress, not acquired — it must not open what it gates."""
    s = states([node("a", status="active"), node("b", requires=["a"])])
    assert s["b"] == "locked"


def test_a_fired_choice_forecloses_its_targets():
    s = states([node("a", status="done", forecloses=["b"]), node("b")])
    assert s["b"] == "foreclosed"


def test_an_unfired_choice_forecloses_nothing():
    """The option being *available* must not pre-emptively close what it would."""
    s = states([node("a", forecloses=["b"]), node("b")])
    assert s["a"] == "available" and s["b"] != "foreclosed"


def test_foreclosure_outranks_done():
    """Spending an asset un-acquires it; the other precedence hid that entirely."""
    s = states([node("spend", status="done", forecloses=["fund"]),
                node("fund", status="done")])
    assert s["fund"] == "foreclosed"


def test_enables_is_the_reverse_of_requires():
    st = core.compute_states([node("a", status="done"), node("b", requires=["a"])], CFG)
    assert st["a"]["enables"] == ["b"]
    assert st["b"]["closed_by"] == []


# ── 熏習 decay ───────────────────────────────────────────────────────────────


def test_done_node_fades_once_unreinforced_past_the_threshold():
    s = states([node("skill", status="done", touched="2026-01")])   # ~7.5mo
    assert s["skill"] == "fading"


def test_recently_touched_node_does_not_fade():
    s = states([node("skill", status="done", touched="2026-07")])   # ~1.5mo
    assert s["skill"] == "done"


def test_decay_needs_an_explicit_touch_stamp():
    """A finished past event is not 'unpractised' — a place lived never fades."""
    s = states([node("place", status="done", date="1984")])
    assert s["place"] == "done"


# ── scenario ─────────────────────────────────────────────────────────────────


def test_scenario_reports_what_a_choice_opens_and_closes():
    nodes = [node("gate", forecloses=["kept"]), node("kept"), node("after", requires=["gate"])]
    d = core.scenario_delta(nodes, CFG, ["gate"])
    assert [r["id"] for r in d["closed"]] == ["kept"]
    assert [r["id"] for r in d["opened"]] == ["after"]


def test_scenario_never_mutates_the_caller_s_nodes():
    """It answers a hypothetical; a projection must not become recorded fact."""
    nodes = [node("gate", forecloses=["kept"]), node("kept")]
    core.scenario_delta(nodes, CFG, ["gate"])
    assert nodes[0]["status"] == "" and nodes[1]["status"] == ""


def test_scenario_excludes_the_taken_nodes_from_the_delta():
    nodes = [node("gate"), node("after", requires=["gate"])]
    d = core.scenario_delta(nodes, CFG, ["gate"])
    assert "gate" not in [r["id"] for r in d["moved"]]


# ── validation: every case asserts the RED direction ─────────────────────────


def _findings(nodes, vault=None):
    return core.validate(nodes, CFG, vault or REPO)


def test_healthy_graph_is_silent():
    assert _findings([node("a", status="done"), node("b", requires=["a"])]) == []


@pytest.mark.parametrize(
    "bad, expect",
    [
        (node("b", requires=["ghost"]), "dangling edge"),
        (node("b", forecloses=["ghost"]), "dangling edge"),
        (node("b", palace="nope"), "unknown palace"),
        (node("b", era="nope"), "unknown era"),
        (node("b", status="banana"), "unknown status"),
        (node("b", status="available"), "is derived"),
        (node("b", date="March-ish"), "unparseable date"),
        (node("b", ref="no/such/file.md"), "ref does not resolve"),
    ],
)
def test_validator_catches_each_authoring_mistake(bad, expect):
    hits = [f for f in _findings([bad]) if expect in f]
    assert hits, f"validator stayed silent on {expect!r}"


def test_validator_catches_a_cycle():
    nodes = [node("a", requires=["b"]), node("b", requires=["a"])]
    assert any("cycle" in f for f in _findings(nodes))


def test_validator_catches_a_duplicate_id():
    assert any("duplicate id" in f for f in _findings([node("a"), node("a")]))


# ── layout ───────────────────────────────────────────────────────────────────


def test_nodes_in_one_cell_get_distinct_slots():
    """Overlapping slots stack nodes on top of each other in every renderer."""
    st = core.layout(core.compute_states([node("a"), node("b"), node("c")], CFG), CFG)
    slots = sorted(s["pos"]["slot"] for s in st.values())
    assert slots == [0, 1, 2]


def test_layout_separates_distinct_palaces_into_distinct_lanes():
    st = core.layout(core.compute_states([node("a"), node("b", palace="b")], CFG), CFG)
    assert st["a"]["pos"]["lane"] != st["b"]["pos"]["lane"]


# ── frontmatter coercion ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, want",
    [
        (["x", "y"], ["x", "y"]),
        ("x, y", ["x", "y"]),
        ("", []),
        ("[]", []),          # an empty YAML list reads back as the string "[]"
        (None, []),
        ([" x ", ""], ["x"]),
    ],
)
def test_list_fields_coerce_at_the_boundary(raw, want):
    assert core._as_list(raw) == want


def test_prose_drops_what_the_panel_renders_from_its_own_fields():
    body = "---\n---\n# Title\n\nReal prose.\n\nSource: [[a/b.md]]\n"
    assert core._prose(core.strip_frontmatter(body)) == "Real prose."
