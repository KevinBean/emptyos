"""3-phase symmetric short-circuit calc on radial networks (IEC 60909-style).

For each bus k:
    Z_thev(k) = Z_source + Σ Z_segment along the tree path slack→k
    I_sc(k)   = c · V_LN / |Z_thev(k)|     [A per phase]
    S_sc(k)   = √3 · V_LL · I_sc(k)        [VA, three-phase]

Where:
    c            = IEC voltage factor (1.10 for max-Isc, 0.95 for min-Isc)
    V_LN         = line-to-neutral phasor magnitude at the slack
    Z_source     = c · V_LL² / S_source if upstream MVA given, else 0 (infinite-bus)

Single-phase (line-to-ground) currents need zero-sequence impedance which
SegmentImpedance doesn't carry. Defer until a project demands it.

Reuses _build_tree from loadflow.py — same radiality + slack-resolution
contract.
"""

from __future__ import annotations

import math
from collections import defaultdict

from engines.models import (
    BusShortCircuit,
    ShortCircuitInput,
    ShortCircuitResult,
)

from .loadflow import _build_tree
from .loadflow import LoadFlowInput  # for adapter use only — see below


def _to_loadflow_input(inp: ShortCircuitInput) -> LoadFlowInput:
    """ShortCircuitInput shares topology + segments + slack with
    LoadFlowInput; reuse _build_tree by adapting the shape. Loads are
    irrelevant to the short-circuit calc (we ignore prefault current).
    """
    return LoadFlowInput(
        topology=inp.topology,
        slack_node=inp.slack_node,
        slack_voltage_kv=inp.slack_voltage_kv,
        phases=inp.phases,
        loads=[],
        segments=inp.segments,
    )


def compute_short_circuit(
    input: ShortCircuitInput | dict,
) -> ShortCircuitResult:
    if isinstance(input, dict):
        input = ShortCircuitInput(**input)

    lf_inp = _to_loadflow_input(input)
    parent, bfs_order, edge_lookup = _build_tree(lf_inp)

    seg_by_edge = {s.edge_id: s for s in input.segments}
    notes: list[str] = []

    # Source impedance (treated as pure reactance per IEC; if user has
    # split R/X for the upstream grid, they can encode it as a virtual
    # segment from a phantom slack instead).
    c = input.voltage_factor
    v_ll = input.slack_voltage_kv * 1000.0  # V
    if input.source_mva_3ph:
        z_src = c * (v_ll ** 2) / (input.source_mva_3ph * 1e6)  # Ω
        x_src = z_src  # assume X/R → ∞ at the grid; conservative for max-Isc
        r_src = 0.0
    else:
        x_src = 0.0
        r_src = 0.0
        notes.append("source treated as infinite-bus — Isc is upper-bound")

    # Walk slack→bus once; cache R+jX_thev for every bus.
    r_thev: dict[str, float] = {input.slack_node: r_src}
    x_thev: dict[str, float] = {input.slack_node: x_src}
    for n in bfs_order:
        p = parent[n]
        if p is None:
            continue
        eid = edge_lookup[(p, n)]
        seg = seg_by_edge.get(eid)
        r_add = seg.r_ohm if seg else 0.0
        x_add = seg.x_ohm if seg else 0.0
        if seg is None:
            notes.append(f"edge {eid} has no impedance — assumed bus tie (Z=0)")
        r_thev[n] = r_thev[p] + r_add
        x_thev[n] = x_thev[p] + x_add

    if input.phases == 3:
        v_ln = v_ll / math.sqrt(3.0)
    else:
        v_ln = v_ll

    buses: list[BusShortCircuit] = []
    for n in bfs_order:
        r = r_thev[n]
        x = x_thev[n]
        z = math.hypot(r, x)
        if z < 1e-9:
            # Slack with infinite-bus source → Isc undefined (∞). Surface
            # as zero rather than divide-by-zero; the note already explains.
            isc_a = 0.0
            ssc_va = 0.0
            notes.append(f"node {n}: zero path impedance — Isc unbounded, reported as 0")
        else:
            isc_a = c * v_ln / z
            ssc_va = math.sqrt(3.0) * v_ll * isc_a if input.phases == 3 else v_ll * isc_a
        buses.append(BusShortCircuit(
            node_id=n,
            r_thev_ohm=r,
            x_thev_ohm=x,
            z_thev_ohm=z,
            isc_3ph_ka=isc_a / 1000.0,
            ssc_mva=ssc_va / 1e6,
        ))

    return ShortCircuitResult(
        buses=buses,
        voltage_factor=c,
        source_mva_3ph=input.source_mva_3ph,
        notes=notes,
    )
