"""Reticulation engine — 3-phase short-circuit calc regression.

Hand-computed reference values from IEC 60909 simplified Thevenin model:

    Z_thev(k) = sum of (R+jX) along tree path slack→k
    I_sc      = c · V_LN / |Z_thev|
    S_sc      = √3 · V_LL · I_sc
"""

from __future__ import annotations

import math

import pytest

from engines.models import (
    EdgeRecord,
    NetworkTopo,
    NodeRecord,
    SegmentImpedance,
    ShortCircuitInput,
)
from engines.reticulation import compute_short_circuit


def _topo(nodes: list[str], edges: list[tuple[str, str, str]]) -> NetworkTopo:
    return NetworkTopo(
        nodes=[NodeRecord(id=n) for n in nodes],
        edges=[EdgeRecord(id=eid, from_node=a, to_node=b) for eid, a, b in edges],
    )


def test_radial_two_bus_infinite_source():
    """A—(R=1Ω, X=0.5Ω)—B, slack 11 kV LL, infinite-bus source, c=1.10.

    V_LN = 11000/√3 ≈ 6350.85 V
    Z_B  = 1 + j0.5 → |Z| = √1.25 ≈ 1.11803
    I_sc = 1.10 · 6350.85 / 1.11803 ≈ 6249.4 A → 6.249 kA
    S_sc = √3 · 11000 · 6249.4 ≈ 119.05 MVA
    Slack |Z|=0 → reported as 0 (with explanatory note).
    """
    inp = ShortCircuitInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        segments=[SegmentImpedance(edge_id="e1", r_ohm=1.0, x_ohm=0.5)],
    )
    res = compute_short_circuit(inp)
    by_node = {b.node_id: b for b in res.buses}

    assert by_node["A"].z_thev_ohm == pytest.approx(0.0, abs=1e-12)
    assert by_node["A"].isc_3ph_ka == 0.0
    assert any("infinite-bus" in n for n in res.notes)
    assert any("unbounded" in n for n in res.notes)

    pb = by_node["B"]
    assert pb.r_thev_ohm == pytest.approx(1.0, abs=1e-9)
    assert pb.x_thev_ohm == pytest.approx(0.5, abs=1e-9)
    assert pb.z_thev_ohm == pytest.approx(math.hypot(1.0, 0.5), abs=1e-9)
    assert pb.isc_3ph_ka == pytest.approx(6.2494, abs=1e-3)
    assert pb.ssc_mva == pytest.approx(math.sqrt(3) * 11.0 * pb.isc_3ph_ka, abs=1e-3)


def test_path_impedance_accumulates_along_tree():
    """A—(R=0.3, X=0.4)—B—(R=0.6, X=0.8)—C.

    Z_B = 0.3+j0.4 → |Z|=0.5
    Z_C = 0.9+j1.2 → |Z|=1.5  (sum, not in parallel — radial)
    I_sc(B)/I_sc(C) = |Z_C|/|Z_B| = 3.0  (Isc inversely proportional to Z)
    """
    inp = ShortCircuitInput(
        topology=_topo(["A", "B", "C"], [("e1", "A", "B"), ("e2", "B", "C")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        segments=[
            SegmentImpedance(edge_id="e1", r_ohm=0.3, x_ohm=0.4),
            SegmentImpedance(edge_id="e2", r_ohm=0.6, x_ohm=0.8),
        ],
    )
    res = compute_short_circuit(inp)
    by_node = {b.node_id: b for b in res.buses}

    assert by_node["B"].z_thev_ohm == pytest.approx(0.5, abs=1e-9)
    assert by_node["C"].z_thev_ohm == pytest.approx(1.5, abs=1e-9)
    assert by_node["B"].isc_3ph_ka / by_node["C"].isc_3ph_ka == pytest.approx(3.0, abs=1e-6)


def test_source_mva_lowers_isc_at_slack_and_downstream():
    """Source impedance from a 100 MVA upstream grid at 11 kV, c=1.10.

    Z_src = 1.10 · 11000² / 100e6 = 1.331 Ω (pure X by convention).
    A—(R=1, X=0.5)—B.
    Z_B = R=1, X=0.5+1.331=1.831 → |Z|=√(1+3.353)=2.0871
    I_sc(B) = 1.10·6350.85/2.0871 ≈ 3347 A → 3.347 kA
    Slack now has finite Isc as well.
    """
    inp = ShortCircuitInput(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        segments=[SegmentImpedance(edge_id="e1", r_ohm=1.0, x_ohm=0.5)],
        source_mva_3ph=100.0,
    )
    res = compute_short_circuit(inp)
    by_node = {b.node_id: b for b in res.buses}

    assert by_node["A"].x_thev_ohm == pytest.approx(1.331, abs=1e-3)
    assert by_node["A"].isc_3ph_ka > 0
    assert by_node["B"].isc_3ph_ka == pytest.approx(3.3485, abs=2e-3)
    assert not any("infinite-bus" in n for n in res.notes)


def test_voltage_factor_min_gives_lower_isc():
    """c=0.95 (min-fault, used for protection reach) < c=1.10 (max-fault)."""
    base = dict(
        topology=_topo(["A", "B"], [("e1", "A", "B")]),
        slack_node="A",
        slack_voltage_kv=11.0,
        segments=[SegmentImpedance(edge_id="e1", r_ohm=1.0, x_ohm=0.5)],
    )
    res_max = compute_short_circuit(ShortCircuitInput(**base, voltage_factor=1.10))
    res_min = compute_short_circuit(ShortCircuitInput(**base, voltage_factor=0.95))
    isc_max = next(b for b in res_max.buses if b.node_id == "B").isc_3ph_ka
    isc_min = next(b for b in res_min.buses if b.node_id == "B").isc_3ph_ka
    assert isc_max > isc_min
    assert isc_min / isc_max == pytest.approx(0.95 / 1.10, abs=1e-6)


def test_dict_input_accepted():
    """Solver accepts dict input (FastAPI body shape)."""
    res = compute_short_circuit({
        "topology": {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [{"id": "e1", "from_node": "A", "to_node": "B"}],
        },
        "slack_node": "A",
        "slack_voltage_kv": 11.0,
        "segments": [{"edge_id": "e1", "r_ohm": 1.0, "x_ohm": 0.5}],
    })
    assert len(res.buses) == 2
    assert res.method == "iec_60909_radial_thevenin"
