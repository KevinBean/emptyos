"""BFS routing — pure-function tests."""

from __future__ import annotations

import pytest

from engines.models import EdgeRecord, NetworkTopo, NodeRecord
from engines.routing import find_path


def _topo(nodes: list[str], edges: list[tuple[str, str, str, float | None]]) -> NetworkTopo:
    return NetworkTopo(
        nodes=[NodeRecord(id=n) for n in nodes],
        edges=[
            EdgeRecord(id=eid, from_node=a, to_node=b, length_m=L)
            for eid, a, b, L in edges
        ],
    )


def test_direct_edge():
    t = _topo(["A", "B"], [("e1", "A", "B", 100.0)])
    r = find_path(t, "A", "B")
    assert r.found
    assert r.path == ["A", "B"]
    assert r.edges == ["e1"]
    assert r.total_length_m == 100.0
    assert r.hops == 1


def test_self_loop_returns_zero_hops():
    t = _topo(["A", "B"], [("e1", "A", "B", 100.0)])
    r = find_path(t, "A", "A")
    assert r.found
    assert r.path == ["A"] and r.edges == [] and r.hops == 0


def test_picks_shortest_hop_count_not_length():
    """BFS minimises hops — a shorter-length 2-hop path loses to a
    direct 1-hop edge even when the direct edge is much longer."""
    t = _topo(
        ["A", "B", "C"],
        [
            ("direct", "A", "C", 1000.0),
            ("e1", "A", "B", 1.0),
            ("e2", "B", "C", 1.0),
        ],
    )
    r = find_path(t, "A", "C")
    assert r.found
    assert r.edges == ["direct"]
    assert r.total_length_m == 1000.0


def test_undirected_routing():
    """Edges are bidirectional — A→B then walk back from B to A works."""
    t = _topo(["A", "B"], [("e1", "A", "B", 50.0)])
    r = find_path(t, "B", "A")
    assert r.found and r.path == ["B", "A"]


def test_waypoint_forces_through():
    """With waypoint X, must go via X even if direct path exists."""
    t = _topo(
        ["A", "B", "X"],
        [
            ("direct", "A", "B", 1.0),
            ("via1", "A", "X", 5.0),
            ("via2", "X", "B", 5.0),
        ],
    )
    r = find_path(t, "A", "B", waypoints=["X"])
    assert r.found
    assert r.path == ["A", "X", "B"]
    assert r.total_length_m == 10.0


def test_exclude_edges_forces_detour():
    t = _topo(
        ["A", "B", "C"],
        [
            ("direct", "A", "B", 100.0),
            ("e1", "A", "C", 50.0),
            ("e2", "C", "B", 50.0),
        ],
    )
    r = find_path(t, "A", "B", exclude_edges=["direct"])
    assert r.found
    assert r.edges == ["e1", "e2"]
    assert r.total_length_m == 100.0


def test_no_path_returns_not_found():
    t = _topo(["A", "B", "C"], [("e1", "A", "B", 1.0)])  # C disconnected
    r = find_path(t, "A", "C")
    assert not r.found
    assert r.path == [] and r.edges == []
    assert any("no path" in n for n in r.notes)


def test_unknown_endpoint_returns_not_found():
    t = _topo(["A", "B"], [("e1", "A", "B", 1.0)])
    r = find_path(t, "A", "Z")
    assert not r.found
    assert any("not in topology" in n for n in r.notes)


def test_unknown_waypoint_returns_not_found():
    t = _topo(["A", "B"], [("e1", "A", "B", 1.0)])
    r = find_path(t, "A", "B", waypoints=["Z"])
    assert not r.found
    assert any("waypoint" in n for n in r.notes)


def test_missing_length_emits_note():
    t = _topo(
        ["A", "B", "C"],
        [("e1", "A", "B", 100.0), ("e2", "B", "C", None)],
    )
    r = find_path(t, "A", "C")
    assert r.found
    assert any("length missing" in n for n in r.notes)


def test_branching_picks_first_discovered_among_equal_paths():
    """Multiple equal-hop paths — BFS returns one of them; just verify
    the path is valid (right hops, right endpoints), not which one wins."""
    t = _topo(
        ["A", "B", "C", "D"],
        [
            ("e1", "A", "B", 10.0),
            ("e2", "A", "C", 10.0),
            ("e3", "B", "D", 10.0),
            ("e4", "C", "D", 10.0),
        ],
    )
    r = find_path(t, "A", "D")
    assert r.found
    assert r.hops == 2
    assert r.path[0] == "A" and r.path[-1] == "D"


def test_engine_class_dispatches_to_pure_function():
    """Engine wrapper accepts dict topology; pure function via class."""
    from engines.routing import RoutingEngine

    class FakeManifest:
        raw = {"engine": {"version": "0.1.0"}}

    eng = RoutingEngine(kernel=None, manifest=FakeManifest())
    t = _topo(["A", "B"], [("e1", "A", "B", 100.0)]).model_dump()
    r = eng.find_path(t, "A", "B")
    assert r.found and r.total_length_m == 100.0
