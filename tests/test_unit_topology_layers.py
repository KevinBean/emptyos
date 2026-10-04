"""The layers view must place every node, cycles included.

Until 2026-10-02 `/api/topology/layers` assigned depth with a Kahn sort, and a
node inside any dependency cycle never reached in-degree 0 — so it, and
everything depending on it, was silently left out of ``layers``,
``layer_widths`` and ``critical_path``. The hub-panel / calculator-suite
pattern (surface ``optional_calls_app`` atom, atom ``contributes_to`` surface)
made 36 such pairs that chained into a few large groups, which dropped 170 of
237 apps from the view while the endpoint answered 200 and the cycle detector
(which reads ``calls_app`` only) reported one intentional cycle. The depth sort
now also leaves ``contributes_to`` out (a registration, not a dependency), so a
host sits above what it composes instead of sharing a layer with it.

``test_unit_`` prefix -> offline per conftest. Exercises the pure helpers the
endpoint is built on; the live pin is ``test_sys_topology.py::test_layers``.
"""
from __future__ import annotations

from emptyos.web.topology import (
    LAYER_SORT_EDGES,
    STRUCTURAL_EDGES,
    _dependency_lists,
    _layer_nodes,
    _layer_widths,
    _strongly_connected_components,
)


def test_layer_widths_sum_depths_that_share_a_name():
    """Depths 7, 8 and 9 are all "deep"; the width of "deep" is their sum,
    not whichever depth happened to be counted last — and the names come out
    in layer order, whatever order the input arrived in."""
    name_for = lambda lv: "base" if lv == 4 else "deep"  # noqa: E731
    widths = _layer_widths([9, 4, 7, 8, 4, 8], name_for)
    assert widths == {"base": 2, "deep": 4}
    assert list(widths) == ["base", "deep"]
    assert sum(widths.values()) == 6


def _hub_panel_graph():
    """A surface and two atoms wired the way hub panels are, plus one app on top.

    ``cap:read`` stands in for infrastructure with a fixed layer.
    """
    node_types = {
        "app:surface": "app",
        "app:atom-a": "app",
        "app:atom-b": "app",
        "app:top": "app",
        "cap:read": "capability",
    }
    deps = {
        "app:surface": ["app:atom-a", "app:atom-b", "cap:read"],  # optional_calls_app
        "app:atom-a": ["app:surface"],  # contributes_to
        "app:atom-b": ["app:surface", "cap:read"],  # contributes_to
        "app:top": ["app:surface"],  # calls_app
        "cap:read": [],
    }
    return node_types, deps, {"cap:read": 1}


def test_scc_groups_the_mutual_trio_and_nothing_else():
    node_types, deps, _ = _hub_panel_graph()
    comp = _strongly_connected_components(list(node_types), deps)
    assert set(comp) == set(node_types), "every node gets a component"
    assert comp["app:surface"] == comp["app:atom-a"] == comp["app:atom-b"]
    # The two singletons are distinct from the group AND from each other — an
    # implementation that merges everything reachable would pass the first.
    assert len({comp["app:top"], comp["cap:read"], comp["app:surface"]}) == 3


def test_dependency_lists_follow_the_edge_set_and_the_veto():
    """The depth sort drops `contributes_to`; fan-in keeps it; a veto removes
    one edge; an edge to an unknown node is dropped on both sides."""
    nodes = ["app:host", "app:panel", "app:other"]
    edges = [
        {"type": "optional_calls_app", "source": "app:host", "target": "app:panel"},
        {"type": "contributes_to", "source": "app:panel", "target": "app:host"},
        {"type": "calls_app", "source": "app:other", "target": "app:host"},
        {"type": "calls_app", "source": "app:other", "target": "app:gone"},
        {"type": "emits_event", "source": "app:other", "target": "event:x"},
    ]
    deps, rdeps = _dependency_lists(nodes, edges, STRUCTURAL_EDGES)
    assert deps["app:panel"] == ["app:host"], "fan-in analysis keeps contributes_to"
    assert rdeps["app:host"] == ["app:panel", "app:other"]
    assert deps["app:other"] == ["app:host"], "the edge to an unknown node is dropped"
    sort_deps, _ = _dependency_lists(nodes, edges, LAYER_SORT_EDGES)
    assert sort_deps["app:panel"] == [], "the depth sort drops contributes_to"
    assert sort_deps["app:host"] == ["app:panel"]
    vetoed, _ = _dependency_lists(
        nodes, edges, LAYER_SORT_EDGES, skip=lambda e: e["source"] == "app:other"
    )
    assert vetoed["app:other"] == []
    assert vetoed["app:host"] == ["app:panel"]


def test_host_sits_above_its_contributors_once_contributes_to_is_out():
    """With the registration edge out of the sort, hub-shaped graphs no longer
    weld the host and its panels into one layer."""
    node_types, deps, fixed = _hub_panel_graph()
    for atom in ("app:atom-a", "app:atom-b"):
        deps[atom] = [d for d in deps[atom] if d != "app:surface"]
    layer, _, members = _layer_nodes(node_types, deps, fixed)
    assert layer["app:atom-a"] == layer["app:atom-b"] == 4
    assert layer["app:surface"] == 5
    assert layer["app:top"] == 6
    assert all(len(g) == 1 for g in members.values())


def test_every_node_in_a_cycle_is_still_placed():
    node_types, deps, fixed = _hub_panel_graph()
    layer, comp, members = _layer_nodes(node_types, deps, fixed)
    assert set(layer) == set(node_types), "a node in a cycle must not vanish"
    # The mutually dependent group shares one layer, at the app floor (its
    # only outside dependency is infrastructure, which does not count).
    assert layer["app:surface"] == layer["app:atom-a"] == layer["app:atom-b"] == 4
    # The app that depends on the group sits one above it.
    assert layer["app:top"] == 5
    assert layer["cap:read"] == 1
    groups = [sorted(g) for g in members.values() if len(g) > 1]
    assert groups == [["app:atom-a", "app:atom-b", "app:surface"]]


def test_chain_of_cycles_still_deepens():
    """A cycle that depends on another cycle lands above it, not beside it."""
    node_types = {f"app:{n}": "app" for n in ("a", "b", "c", "d")}
    deps = {
        "app:a": ["app:b"],
        "app:b": ["app:a"],
        "app:c": ["app:d", "app:a"],
        "app:d": ["app:c"],
    }
    layer, _, members = _layer_nodes(node_types, deps, {})
    assert layer["app:a"] == layer["app:b"] == 4
    assert layer["app:c"] == layer["app:d"] == 5
    assert sorted(len(g) for g in members.values()) == [2, 2]


def test_acyclic_graph_matches_the_old_depth_rule():
    """Without cycles the result is the plain max(dep layer) + 1 the view always had."""
    node_types = {"app:x": "app", "app:y": "app", "app:z": "app", "plugin:p": "plugin"}
    deps = {"app:x": [], "app:y": ["app:x"], "app:z": ["app:y", "plugin:p"], "plugin:p": []}
    layer, _, members = _layer_nodes(node_types, deps, {"plugin:p": 2})
    assert (layer["app:x"], layer["app:y"], layer["app:z"], layer["plugin:p"]) == (4, 5, 6, 2)
    assert all(len(g) == 1 for g in members.values())


def test_scc_is_iterative_on_a_long_chain():
    """A chain deeper than the recursion limit must not blow the stack."""
    n = 5000
    node_types = {f"n{i}": "app" for i in range(n)}
    deps = {f"n{i}": [f"n{i + 1}"] for i in range(n - 1)}
    deps[f"n{n - 1}"] = []
    comp = _strongly_connected_components(list(node_types), deps)
    assert len(set(comp.values())) == n
