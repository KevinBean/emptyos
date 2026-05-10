"""Reticulation engine — radial load-flow regression.

Reference cases are hand-computed from the standard balanced 3-phase
load-flow equations (constant-power loads, line-to-neutral phasor):

    V_to = V_from - Z * I,   I = conj(S_phase / V_to),   V_phase = V_LL/√3
"""

from __future__ import annotations

import math

import pytest

from engines.models import (
    BusLoad,
    EdgeRecord,
    LoadFlowInput,
    NetworkTopo,
    NodeRecord,
    SegmentImpedance,
)
from engines.reticulation import check_sizing, solve_load_flow


def _topo(nodes: list[str], edges: list[tuple[str, str, str]]) -> NetworkTopo:
    return NetworkTopo(
        nodes=[NodeRecord(id=n) for n in nodes],
        edges=[EdgeRecord(id=eid, from_node=a, to_node=b) for eid, a, b in edges],
    )


# ── Case 1 — single resistive segment, 3-phase ────────────────────


def test_single_segment_resistive_3ph():
    """A——R=1Ω——B, slack 11 kV LL, 300 kW load at B, pf=1.

    Hand answer (constant-power iterating to convergence):
      V_A_phase = 11000/√3 = 6350.85 V
      Solve V_B such that V_B = V_A - 1·(100000/V_B):
        V_B² - 6350.85·V_B + 100000 = 0
        V_B = (6350.85 + √(6350.85² - 400000))/2 ≈ 6335.07 V
      I = 100000/V_B ≈ 15.785 A
      V_B_LL = V_B·√3 ≈ 10972.4 V
    """
    inp = LoadFlowInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        phases=3,
        loads=[BusLoad(node_id="B", p_kw=300.0, q_kvar=0.0)],
        segments=[SegmentImpedance(edge_id="e1", r_ohm=1.0, x_ohm=0.0)],
    )
    res = solve_load_flow(inp)
    assert res.converged
    assert res.iterations <= 8

    by_node = {b.node_id: b for b in res.bus_voltages}
    assert by_node["A"].voltage_kv == pytest.approx(11.0, abs=1e-9)
    assert by_node["B"].voltage_kv == pytest.approx(10.9724, abs=2e-3)
    assert by_node["B"].voltage_pu == pytest.approx(10.9724 / 11.0, abs=2e-4)

    by_edge = {f.edge_id: f for f in res.segment_flows}
    assert by_edge["e1"].current_a == pytest.approx(15.785, rel=2e-3)
    # Loss = 3 * I² * R = 3 * 15.785² * 1 ≈ 747.5 W
    assert by_edge["e1"].p_loss_kw == pytest.approx(0.7475, rel=2e-3)
    # Active power at the slack end ≈ load + loss
    assert by_edge["e1"].p_flow_kw == pytest.approx(300.0 + 0.7475, rel=3e-3)


# ── Case 2 — two-segment chain ────────────────────────────────────


def test_two_segment_chain_propagates_drop():
    """A—0.5Ω—B—0.5Ω—C, load only at C.

    Same total impedance as Case 1, so V_C should match V_B from Case 1.
    V_B should sit roughly halfway in the drop (slightly less because of
    the constant-power feedback, but very close to the midpoint).
    """
    inp = LoadFlowInput(
        topology=_topo(
            ["A", "B", "C"],
            [("e1", "A", "B"), ("e2", "B", "C")],
        ),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[BusLoad(node_id="C", p_kw=300.0)],
        segments=[
            SegmentImpedance(edge_id="e1", r_ohm=0.5),
            SegmentImpedance(edge_id="e2", r_ohm=0.5),
        ],
    )
    res = solve_load_flow(inp)
    assert res.converged
    by_node = {b.node_id: b for b in res.bus_voltages}
    # Total drop matches single-segment case to ~3 mV
    assert by_node["C"].voltage_kv == pytest.approx(10.9724, abs=2e-3)
    # B sits between A and C (and closer to A by half the drop)
    assert by_node["A"].voltage_kv > by_node["B"].voltage_kv > by_node["C"].voltage_kv
    midpoint_kv = 0.5 * (by_node["A"].voltage_kv + by_node["C"].voltage_kv)
    assert by_node["B"].voltage_kv == pytest.approx(midpoint_kv, abs=5e-4)


# ── Case 3 — branching radial (1 source, 2 leaves) ────────────────


def test_branching_radial():
    """A—B (trunk, 1Ω) and B—C (branch, 1Ω) with loads at B and C.

    Branch current B→C must equal C's load current alone; trunk A→B
    must equal both loads' currents. Voltage drop on trunk uses the
    sum, drop on branch uses only the branch current.
    """
    inp = LoadFlowInput(
        topology=_topo(
            ["A", "B", "C"],
            [("trunk", "A", "B"), ("branch", "B", "C")],
        ),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[
            BusLoad(node_id="B", p_kw=100.0),
            BusLoad(node_id="C", p_kw=100.0),
        ],
        segments=[
            SegmentImpedance(edge_id="trunk", r_ohm=1.0),
            SegmentImpedance(edge_id="branch", r_ohm=1.0),
        ],
    )
    res = solve_load_flow(inp)
    assert res.converged
    by_edge = {f.edge_id: f for f in res.segment_flows}
    # Trunk carries ~2x branch current (both ≈ 100 kW each at ~11 kV).
    assert by_edge["trunk"].current_a > by_edge["branch"].current_a * 1.9
    assert by_edge["trunk"].current_a < by_edge["branch"].current_a * 2.1
    # Trunk loss = 3 * I_trunk² * 1
    expected_trunk_loss_kw = 3 * by_edge["trunk"].current_a ** 2 / 1000.0
    assert by_edge["trunk"].p_loss_kw == pytest.approx(expected_trunk_loss_kw, rel=1e-6)


# ── Case 4 — single-phase mode ────────────────────────────────────


def test_single_phase_mode():
    """Same R, P, V_LN — single-phase. Drop should be √3× larger than
    the equivalent 3-phase line-to-neutral drop (no √3 factor on V_LL)."""
    inp = LoadFlowInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=0.230,  # 230 V
        phases=1,
        loads=[BusLoad(node_id="B", p_kw=5.0)],
        segments=[SegmentImpedance(edge_id="e1", r_ohm=0.1)],
    )
    res = solve_load_flow(inp)
    assert res.converged
    by_node = {b.node_id: b for b in res.bus_voltages}
    # I ≈ 5000/230 ≈ 21.74 A, drop ≈ 0.1 * 21.74 ≈ 2.17 V → V_B ≈ 227.83 V
    assert by_node["B"].voltage_kv == pytest.approx(0.2278, abs=2e-4)
    by_edge = {f.edge_id: f for f in res.segment_flows}
    assert by_edge["e1"].current_a == pytest.approx(21.95, rel=5e-3)


# ── Case 5 — topology validation ──────────────────────────────────


def test_cycle_rejected():
    inp = LoadFlowInput(
        topology=_topo(
            ["A", "B", "C"],
            [("e1", "A", "B"), ("e2", "B", "C"), ("e3", "C", "A")],
        ),
        slack_node="A",
        slack_voltage_kv=11.0,
        segments=[
            SegmentImpedance(edge_id=e, r_ohm=1.0) for e in ("e1", "e2", "e3")
        ],
    )
    with pytest.raises(ValueError, match="not radial"):
        solve_load_flow(inp)


def test_disconnected_rejected():
    inp = LoadFlowInput(
        topology=_topo(
            ["A", "B", "X"],  # X has no edges
            [("e1", "A", "B")],
        ),
        slack_node="A",
        slack_voltage_kv=11.0,
        segments=[SegmentImpedance(edge_id="e1", r_ohm=1.0)],
    )
    with pytest.raises(ValueError, match="disconnected"):
        solve_load_flow(inp)


def test_unknown_slack_rejected():
    inp = LoadFlowInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="Z",
        slack_voltage_kv=11.0,
        segments=[SegmentImpedance(edge_id="e1", r_ohm=1.0)],
    )
    with pytest.raises(ValueError, match="slack_node"):
        solve_load_flow(inp)


# ── Case 6 — sizing checks ────────────────────────────────────────


def test_sizing_flags_overload_and_voltage_drop():
    """Tiny rated_a + tight V-drop limit → both flags fire."""
    inp = LoadFlowInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[BusLoad(node_id="B", p_kw=300.0)],
        segments=[
            SegmentImpedance(edge_id="e1", r_ohm=1.0, rated_a=10.0),
        ],
        voltage_drop_limit_pct=0.1,  # 0.1% — guaranteed violation
    )
    res = solve_load_flow(inp)
    sizing = check_sizing(inp, res)

    assert not sizing.ok
    types = {v.type for v in sizing.violations}
    assert "overload" in types
    assert "voltage_drop" in types
    by_edge = {c.edge_id: c for c in sizing.checks}
    assert by_edge["e1"].utilization_pct is not None
    assert by_edge["e1"].utilization_pct > 100.0


def test_sizing_passes_with_adequate_rating():
    inp = LoadFlowInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[BusLoad(node_id="B", p_kw=300.0)],
        segments=[SegmentImpedance(edge_id="e1", r_ohm=1.0, rated_a=200.0)],
        voltage_drop_limit_pct=5.0,
    )
    res = solve_load_flow(inp)
    sizing = check_sizing(inp, res)
    assert sizing.ok
    assert not sizing.violations


# ── Case 7 — PV-bus support ──────────────────────────────────────


def test_pv_bus_holds_setpoint():
    """slack(A) — line(R+jX) — gen(B,PV) — line(R+jX) — load(C,PQ).

    PV gen at B with v_setpoint_pu=1.0 should hold |V_B| at 1.0 within the
    PV tolerance, by injecting reactive power. With resistive+inductive
    drop along A→B, gen Q-inject must be positive (inductive support).
    """
    inp = LoadFlowInput(
        topology=_topo(
            ["A", "B", "C"],
            [("e1", "A", "B"), ("e2", "B", "C")],
        ),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[
            BusLoad(
                node_id="B",
                p_kw=-50.0,  # generator (negative consumption)
                bus_kind="PV",
                v_setpoint_pu=1.0,
                q_min_kvar=-500.0,
                q_max_kvar=500.0,
            ),
            BusLoad(node_id="C", p_kw=200.0, q_kvar=100.0),
        ],
        segments=[
            SegmentImpedance(edge_id="e1", r_ohm=0.5, x_ohm=0.5),
            SegmentImpedance(edge_id="e2", r_ohm=0.5, x_ohm=0.5),
        ],
        max_iterations=80,
    )
    res = solve_load_flow(inp)
    assert res.converged, f"did not converge: {res.notes}"

    by_node = {b.node_id: b for b in res.bus_voltages}
    # |V_B| held at setpoint
    assert by_node["B"].voltage_pu == pytest.approx(1.0, abs=2e-4)
    # PV result reported
    assert len(res.pv_buses) == 1
    pv = res.pv_buses[0]
    assert pv.node_id == "B"
    assert pv.v_setpoint_pu == 1.0
    assert pv.v_solved_pu == pytest.approx(1.0, abs=2e-4)
    assert not pv.saturated
    # Need positive Q-injection (gen supports voltage against the C load downstream).
    assert pv.q_inject_kvar > 0


def test_pv_bus_saturates_at_q_max():
    """PV setpoint demands more Q than gen can deliver → bus reverts to
    PQ at q_max and |V_B| settles below setpoint."""
    inp = LoadFlowInput(
        topology=_topo(
            ["A", "B", "C"],
            [("e1", "A", "B"), ("e2", "B", "C")],
        ),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[
            BusLoad(
                node_id="B",
                p_kw=0.0,
                bus_kind="PV",
                v_setpoint_pu=1.05,  # above slack — needs lots of Q
                q_min_kvar=-10.0,
                q_max_kvar=10.0,  # tight cap
            ),
            BusLoad(node_id="C", p_kw=500.0, q_kvar=200.0),
        ],
        segments=[
            SegmentImpedance(edge_id="e1", r_ohm=0.3, x_ohm=0.4),
            SegmentImpedance(edge_id="e2", r_ohm=0.3, x_ohm=0.4),
        ],
        max_iterations=80,
    )
    res = solve_load_flow(inp)
    assert res.converged

    pv = res.pv_buses[0]
    assert pv.saturated
    assert pv.saturation_limit == "max"
    assert pv.q_inject_kvar == pytest.approx(10.0, abs=1e-9)
    # Voltage falls below the setpoint because Q is clamped.
    assert pv.v_solved_pu < pv.v_setpoint_pu
    assert any("Q-saturated" in n for n in res.notes)


def test_pv_bus_missing_setpoint_rejected():
    inp = LoadFlowInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[BusLoad(node_id="B", bus_kind="PV")],  # no v_setpoint_pu
        segments=[SegmentImpedance(edge_id="e1", r_ohm=0.5, x_ohm=0.5)],
    )
    with pytest.raises(ValueError, match="v_setpoint_pu"):
        solve_load_flow(inp)


def test_pv_on_slack_rejected():
    inp = LoadFlowInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[BusLoad(node_id="A", bus_kind="PV", v_setpoint_pu=1.0)],
        segments=[SegmentImpedance(edge_id="e1", r_ohm=0.5, x_ohm=0.5)],
    )
    with pytest.raises(ValueError, match="slack"):
        solve_load_flow(inp)


def test_sizing_skips_check_when_rated_a_missing():
    """Segments without rated_a get utilization=None, no overload flag."""
    inp = LoadFlowInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        loads=[BusLoad(node_id="B", p_kw=300.0)],
        segments=[SegmentImpedance(edge_id="e1", r_ohm=1.0)],  # no rated_a
        voltage_drop_limit_pct=5.0,
    )
    res = solve_load_flow(inp)
    sizing = check_sizing(inp, res)
    assert sizing.checks[0].utilization_pct is None
    assert not any(v.type == "overload" for v in sizing.violations)
