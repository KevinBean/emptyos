"""apps/cables/topology — unit tests for pure helpers (no daemon required).

Validates the seam between vault frontmatter shapes and the
LoadFlowInput pydantic contract: impedance resolution chain, source
→ negative-load conversion, slack resolution fallback chain.
"""

from __future__ import annotations

import pytest

from apps.cables.topology import (
    EDGE_TAG,
    NODE_TAG,
    apply_route_to_cable,
    bus_loads_from_nodes,
    build_load_flow_input,
    build_short_circuit_input,
    edge_impedance,
    edge_to_record,
    node_to_record,
    resolve_slack,
    DEFAULT_R_PER_KM,
    DEFAULT_X_PER_KM,
)
from engines.reticulation import compute_short_circuit, solve_load_flow


# ── Frontmatter coercion (vault values arrive as strings) ───────


def test_node_to_record_coerces_string_floats():
    fm = {"id": "n1", "x": "12.5", "y": "0", "voltage_kv": "11.0"}
    n = node_to_record(fm)
    assert n.x == 12.5 and n.y == 0.0 and n.voltage_kv == 11.0


def test_node_metadata_carries_power_and_slack_flag():
    fm = {"id": "n1", "p_load_kw": 100, "is_slack": True}
    n = node_to_record(fm)
    assert n.metadata["p_load_kw"] == 100
    assert n.metadata["is_slack"] is True


def test_edge_to_record_uses_cable_id_as_record_ref():
    fm = {"id": "e1", "from_node": "a", "to_node": "b", "length_m": "100", "cable_id": "c1"}
    e = edge_to_record(fm)
    assert e.record_ref == "c1"
    assert e.length_m == 100.0


# ── Impedance resolution chain ──────────────────────────────────


def test_edge_impedance_uses_edge_override_when_present():
    edge_fm = {"id": "e", "length_m": 1000.0, "r_ohm_per_km": 0.2, "x_ohm_per_km": 0.1}
    r, x, note = edge_impedance(edge_fm, cable_fm=None, library_entry=None)
    assert r == pytest.approx(0.2) and x == pytest.approx(0.1)
    assert note is None


def test_edge_impedance_falls_back_to_library_per_km():
    edge_fm = {"id": "e", "length_m": 2000.0}
    cable_fm = {"id": "c1", "library_id": "L1"}
    lib = {
        "id": "L1",
        "conductor_dc_resistance_20c_ohm_per_km": 0.15,
        "x_ohm_per_km": 0.09,
    }
    r, x, note = edge_impedance(edge_fm, cable_fm, lib)
    assert r == pytest.approx(0.15 * 2.0)
    assert x == pytest.approx(0.09 * 2.0)
    assert note is None


def test_edge_impedance_defaults_emit_note():
    edge_fm = {"id": "e", "length_m": 500.0}
    r, x, note = edge_impedance(edge_fm, cable_fm=None, library_entry=None)
    assert r == pytest.approx(DEFAULT_R_PER_KM * 0.5)
    assert x == pytest.approx(DEFAULT_X_PER_KM * 0.5)
    assert note is not None and "default" in note


# ── Loads: sources become negative ──────────────────────────────


def test_bus_loads_treats_generation_as_negative_load():
    nodes = [
        {"id": "wtg", "p_gen_kw": 6200.0, "q_gen_kvar": 0.0},
        {"id": "load", "p_load_kw": 1000.0, "q_kvar": 200.0},
        {"id": "passive"},  # no power → omitted
    ]
    loads = bus_loads_from_nodes(nodes)
    by_id = {l.node_id: l for l in loads}
    assert by_id["wtg"].p_kw == -6200.0
    assert by_id["load"].p_kw == 1000.0
    assert "passive" not in by_id


def test_bus_loads_combines_load_and_gen_at_same_node():
    nodes = [{"id": "n", "p_load_kw": 100.0, "p_gen_kw": 30.0}]
    loads = bus_loads_from_nodes(nodes)
    assert loads[0].p_kw == 70.0  # net consumption


def test_bus_loads_emits_pv_kind_with_setpoint_and_q_limits():
    """Frontmatter `bus_kind: PV` round-trips into a PV BusLoad. Vault
    values arrive as strings, so coercion through _f matters.
    """
    nodes = [
        {
            "id": "gen",
            "p_gen_kw": "50",
            "bus_kind": "PV",
            "v_setpoint_pu": "1.0",
            "q_min_kvar": "-500",
            "q_max_kvar": "500",
        },
    ]
    loads = bus_loads_from_nodes(nodes)
    assert len(loads) == 1
    bl = loads[0]
    assert bl.bus_kind == "PV"
    assert bl.p_kw == -50.0  # gen → negative load
    assert bl.v_setpoint_pu == 1.0
    assert bl.q_min_kvar == -500.0 and bl.q_max_kvar == 500.0


def test_bus_loads_pv_emitted_even_when_zero_net_power():
    """Synchronous-condenser case: bus_kind=PV with no P/Q must still
    appear in the load list so the solver can hold its setpoint.
    """
    nodes = [{"id": "cond", "bus_kind": "PV", "v_setpoint_pu": 1.02}]
    loads = bus_loads_from_nodes(nodes)
    assert len(loads) == 1 and loads[0].bus_kind == "PV"


def test_bus_loads_pv_via_build_load_flow_input_solves(monkeypatch=None):
    """End-to-end seam: vault frontmatter → build_load_flow_input →
    solve_load_flow returns a populated `pv_buses` block. Mirrors
    engines/reticulation/tests/test_radial.py::test_pv_bus_holds_setpoint
    but starts from frontmatter dicts to validate the topology layer.
    """
    project_fm = {"system_voltage_kv": 11.0, "slack_node": "A", "phases": 3}
    nodes_fm = [
        {"id": "A", "kind": "substation", "voltage_kv": 11.0},
        {
            "id": "B",
            "kind": "turbine",
            "p_gen_kw": 50.0,
            "bus_kind": "PV",
            "v_setpoint_pu": 1.0,
            "q_min_kvar": -500.0,
            "q_max_kvar": 500.0,
        },
        {"id": "C", "kind": "load", "p_load_kw": 200.0, "q_load_kvar": 100.0},
    ]
    edges_fm = [
        {"id": "e1", "from_node": "A", "to_node": "B", "length_m": 1000.0,
         "r_ohm_per_km": 0.5, "x_ohm_per_km": 0.5},
        {"id": "e2", "from_node": "B", "to_node": "C", "length_m": 1000.0,
         "r_ohm_per_km": 0.5, "x_ohm_per_km": 0.5},
    ]
    inp, _notes = build_load_flow_input(
        project_fm, nodes_fm, edges_fm, cables_by_id={}, library_index={},
    )
    res = solve_load_flow(inp)
    assert res.converged, res.notes
    assert len(res.pv_buses) == 1
    pv = res.pv_buses[0]
    assert pv.node_id == "B"
    assert not pv.saturated
    assert pv.v_solved_pu == pytest.approx(1.0, abs=2e-4)


# ── Short circuit assembly ──────────────────────────────────────


def test_build_short_circuit_input_pulls_voltage_factor_from_project():
    project_fm = {"system_voltage_kv": 11.0, "slack_node": "A", "voltage_factor": 0.95}
    nodes_fm = [
        {"id": "A", "kind": "substation", "voltage_kv": 11.0},
        {"id": "B", "kind": "load", "p_load_kw": 100.0},
    ]
    edges_fm = [
        {"id": "e1", "from_node": "A", "to_node": "B", "length_m": 1000.0,
         "r_ohm_per_km": 0.5, "x_ohm_per_km": 0.4},
    ]
    sc_inp, _ = build_short_circuit_input(
        project_fm, nodes_fm, edges_fm, cables_by_id={}, library_index={},
    )
    assert sc_inp.voltage_factor == 0.95
    assert sc_inp.source_mva_3ph is None  # not set on project
    assert sc_inp.slack_node == "A"
    # Loads dropped — short-circuit ignores prefault current.
    assert len(sc_inp.segments) == 1


def test_build_short_circuit_explicit_overrides_project_value():
    project_fm = {"system_voltage_kv": 11.0, "slack_node": "A", "voltage_factor": 0.95}
    nodes_fm = [
        {"id": "A", "kind": "substation", "voltage_kv": 11.0},
        {"id": "B", "kind": "load", "p_load_kw": 100.0},
    ]
    edges_fm = [
        {"id": "e1", "from_node": "A", "to_node": "B", "length_m": 1000.0,
         "r_ohm_per_km": 0.5, "x_ohm_per_km": 0.4},
    ]
    sc_inp, _ = build_short_circuit_input(
        project_fm, nodes_fm, edges_fm, cables_by_id={}, library_index={},
        voltage_factor=1.10, source_mva_3ph=250.0,
    )
    assert sc_inp.voltage_factor == 1.10
    assert sc_inp.source_mva_3ph == 250.0


def test_short_circuit_end_to_end_via_topology():
    """Frontmatter → build_short_circuit_input → compute_short_circuit
    returns sensible per-bus Isc.
    """
    project_fm = {"system_voltage_kv": 11.0, "slack_node": "A"}
    nodes_fm = [
        {"id": "A", "kind": "substation", "voltage_kv": 11.0},
        {"id": "B", "kind": "load"},
    ]
    edges_fm = [
        {"id": "e1", "from_node": "A", "to_node": "B", "length_m": 1000.0,
         "r_ohm_per_km": 1.0, "x_ohm_per_km": 0.5},
    ]
    sc_inp, _ = build_short_circuit_input(
        project_fm, nodes_fm, edges_fm, cables_by_id={}, library_index={},
    )
    res = compute_short_circuit(sc_inp)
    by_node = {b.node_id: b for b in res.buses}
    # B is 1 km away on R=1, X=0.5 line → |Z| ≈ 1.118 Ω → Isc ≈ 6.25 kA
    assert by_node["B"].isc_3ph_ka == pytest.approx(6.249, abs=2e-2)


# ── Slack resolution chain ──────────────────────────────────────


def test_slack_resolves_from_project_field():
    proj = {"slack_node": "sub", "slack_voltage_kv": 33.0}
    nodes = [{"id": "sub", "kind": "substation"}, {"id": "wtg", "kind": "turbine"}]
    sid, sv = resolve_slack(proj, nodes)
    assert sid == "sub" and sv == 33.0


def test_slack_falls_back_to_is_slack_flag():
    proj = {"slack_voltage_kv": 11.0}
    nodes = [{"id": "wtg", "kind": "turbine"}, {"id": "bess", "is_slack": True}]
    sid, _ = resolve_slack(proj, nodes)
    assert sid == "bess"


def test_slack_falls_back_to_substation_kind():
    proj = {"slack_voltage_kv": 11.0}
    nodes = [{"id": "wtg", "kind": "turbine"}, {"id": "sub", "kind": "substation"}]
    sid, _ = resolve_slack(proj, nodes)
    assert sid == "sub"


def test_slack_voltage_falls_back_to_node_voltage():
    proj = {"slack_node": "sub"}
    nodes = [{"id": "sub", "kind": "substation", "voltage_kv": 22.0}]
    _, sv = resolve_slack(proj, nodes)
    assert sv == 22.0


def test_slack_raises_when_no_resolution_possible():
    proj = {}
    nodes = [{"id": "wtg", "kind": "turbine"}]
    with pytest.raises(ValueError, match="no slack node"):
        resolve_slack(proj, nodes)


def test_slack_raises_when_voltage_unresolvable():
    proj = {"slack_node": "sub"}
    nodes = [{"id": "sub", "kind": "substation"}]
    with pytest.raises(ValueError, match="no slack voltage"):
        resolve_slack(proj, nodes)


# ── End-to-end assembly + solve ─────────────────────────────────


def test_build_load_flow_input_end_to_end():
    """Two-node project: substation slack at 11 kV, 300 kW load on bus.
    Result must match the engine's standalone Case 1 (V_B ≈ 10.972 kV).
    """
    proj = {"id": "p1", "slack_voltage_kv": 11.0}
    nodes = [
        {"id": "sub", "kind": "substation", "voltage_kv": 11.0, "is_slack": True},
        {"id": "bus", "kind": "bus", "p_load_kw": 300.0},
    ]
    edges = [
        {
            "id": "e1",
            "from_node": "sub",
            "to_node": "bus",
            "length_m": 1000.0,
            "r_ohm_per_km": 1.0,
            "x_ohm_per_km": 0.0,
        }
    ]
    inp, notes = build_load_flow_input(proj, nodes, edges, {}, {})
    assert inp.slack_node == "sub"
    assert inp.slack_voltage_kv == 11.0
    assert notes == []  # impedance came from edge override

    result = solve_load_flow(inp)
    assert result.converged
    by_node = {b.node_id: b for b in result.bus_voltages}
    assert by_node["bus"].voltage_kv == pytest.approx(10.9724, abs=2e-3)


def test_build_load_flow_input_carries_ampacity_into_segment_rated_a():
    """Cable's ampacity_a (set by run_schedule) must propagate to
    SegmentImpedance.rated_a so sizing.check_sizing can flag overload."""
    proj = {"slack_node": "sub", "slack_voltage_kv": 11.0}
    nodes = [
        {"id": "sub", "kind": "substation"},
        {"id": "bus", "p_load_kw": 100.0},
    ]
    edges = [
        {
            "id": "e1",
            "from_node": "sub",
            "to_node": "bus",
            "length_m": 500.0,
            "cable_id": "c1",
        }
    ]
    cables = {"c1": {"id": "c1", "ampacity_a": 250.0}}
    inp, _ = build_load_flow_input(proj, nodes, edges, cables, {})
    seg = inp.segments[0]
    assert seg.rated_a == 250.0


def test_build_load_flow_input_emits_note_for_defaulted_impedance():
    proj = {"slack_node": "sub", "slack_voltage_kv": 11.0}
    nodes = [
        {"id": "sub", "kind": "substation"},
        {"id": "bus", "p_load_kw": 50.0},
    ]
    # No r/x on edge, no cable_id → engine falls back to default + notes it.
    edges = [
        {"id": "e1", "from_node": "sub", "to_node": "bus", "length_m": 500.0}
    ]
    _, notes = build_load_flow_input(proj, nodes, edges, {}, {})
    assert len(notes) == 1
    assert "e1" in notes[0]
    assert "default" in notes[0]


def test_build_load_flow_input_rejects_empty_topology():
    with pytest.raises(ValueError, match="no nodes"):
        build_load_flow_input({"slack_voltage_kv": 11.0}, [], [], {}, {})


# ── Auto-route helper ───────────────────────────────────────────


def test_apply_route_assigns_endpoints_path_edges_and_length():
    cable = {"id": "c1"}  # no user-set length_m
    edges_fm = [
        {"id": "e1", "from_node": "A", "to_node": "B"},
        {"id": "e2", "from_node": "B", "to_node": "C"},
    ]
    cu, eu = apply_route_to_cable(
        "c1", cable,
        path=["A", "B", "C"], edges=["e1", "e2"],
        total_length_m=300.0, edges_fm=edges_fm,
    )
    assert cu["start_node"] == "A" and cu["end_node"] == "C"
    assert cu["network_path"] == ["A", "B", "C"]
    assert cu["network_edges"] == ["e1", "e2"]
    assert cu["routed_length_m"] == 300.0
    # length_m back-filled because cable didn't have one
    assert cu["length_m"] == 300.0
    eu_by_id = dict(eu)
    assert eu_by_id["e1"] == {"cable_id": "c1"}
    assert eu_by_id["e2"] == {"cable_id": "c1"}


def test_apply_route_preserves_user_set_length_m():
    """If cable already has a user-entered length_m, auto-route must
    write routed_length_m but leave length_m alone (override priority)."""
    cable = {"id": "c1", "length_m": 250.0}  # user override
    edges_fm = [{"id": "e1", "from_node": "A", "to_node": "B"}]
    cu, _ = apply_route_to_cable(
        "c1", cable, path=["A", "B"], edges=["e1"],
        total_length_m=300.0, edges_fm=edges_fm,
    )
    assert cu["routed_length_m"] == 300.0
    assert "length_m" not in cu  # left alone


def test_apply_route_skips_already_linked_edges():
    cable = {"id": "c1"}
    edges_fm = [
        {"id": "e1", "from_node": "A", "to_node": "B", "cable_id": "c1"},
        {"id": "e2", "from_node": "B", "to_node": "C"},
    ]
    _, eu = apply_route_to_cable(
        "c1", cable, path=["A", "B", "C"], edges=["e1", "e2"],
        total_length_m=200.0, edges_fm=edges_fm,
    )
    assert [eid for eid, _ in eu] == ["e2"]  # e1 already correct, no rewrite


def test_apply_route_does_not_overwrite_other_cable_by_default():
    cable = {"id": "c1"}
    edges_fm = [{"id": "e1", "from_node": "A", "to_node": "B", "cable_id": "OTHER"}]
    _, eu = apply_route_to_cable(
        "c1", cable, path=["A", "B"], edges=["e1"],
        total_length_m=100.0, edges_fm=edges_fm,
    )
    assert eu == []  # left alone — caller can re-call with overwrite_edges=True


def test_apply_route_overwrites_when_flag_set():
    cable = {"id": "c1"}
    edges_fm = [{"id": "e1", "from_node": "A", "to_node": "B", "cable_id": "OTHER"}]
    _, eu = apply_route_to_cable(
        "c1", cable, path=["A", "B"], edges=["e1"],
        total_length_m=100.0, edges_fm=edges_fm,
        overwrite_edges=True,
    )
    assert eu == [("e1", {"cable_id": "c1"})]


def test_apply_route_omits_length_when_total_unknown():
    cable = {"id": "c1"}
    edges_fm = [{"id": "e1", "from_node": "A", "to_node": "B"}]
    cu, _ = apply_route_to_cable(
        "c1", cable, path=["A", "B"], edges=["e1"],
        total_length_m=None, edges_fm=edges_fm,
    )
    assert "length_m" not in cu
    assert "routed_length_m" not in cu


def test_edge_impedance_falls_back_to_cable_routed_length():
    """If edge has no length and cable has only routed_length_m, use that."""
    edge_fm = {"id": "e", "r_ohm_per_km": 0.1, "x_ohm_per_km": 0.05}
    cable_fm = {"id": "c1", "routed_length_m": 2000.0}
    r, x, _ = edge_impedance(edge_fm, cable_fm, library_entry=None)
    assert r == pytest.approx(0.1 * 2.0)
    assert x == pytest.approx(0.05 * 2.0)


def test_apply_route_returns_empty_for_empty_path():
    cu, eu = apply_route_to_cable(
        "c1", {"id": "c1"}, path=[], edges=[],
        total_length_m=None, edges_fm=[],
    )
    assert cu == {} and eu == []


# ── Tag constants ───────────────────────────────────────────────


def test_tag_constants_match_app():
    assert NODE_TAG == "cable-node"
    assert EDGE_TAG == "cable-edge"
