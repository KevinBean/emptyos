"""Backward/forward sweep AC load flow on radial networks.

Algorithm (Shirmohammadi 1988):

  1. Build adjacency from EdgeRecord list. Verify the slack node induces
     a tree covering every loaded/intermediate bus. Reject if cycles or
     disconnected components are detected.
  2. Initialise V_k = V_slack (flat start, in line-to-neutral phasor).
  3. Repeat until ||ΔV|| < tol or max_iterations:
       a. Backward sweep — compute branch currents from leaves to slack.
            I_load(k) = conj( S(k) / V(k) )   (per-phase, S already /3)
            I_branch(p→k) = I_load(k) + Σ I_branch(k→child)
       b. Forward sweep — recompute V from slack to leaves.
            V(k) = V(p) - Z_pk * I_branch(p→k)
  4. Compute branch flows + losses from final V, I.

Returned voltages are line-to-line magnitudes (kV) for consistency with
how distribution engineers report them.
"""

from __future__ import annotations

import cmath
import math
from collections import defaultdict, deque

from engines.models import (
    BusLoad,
    BusVoltage,
    LoadFlowInput,
    LoadFlowResult,
    PVBusResult,
    SegmentFlow,
)


def _build_tree(input: LoadFlowInput) -> tuple[dict[str, str | None], list[str], dict[tuple[str, str], str]]:
    """Return (parent_of, bfs_order_from_slack, edge_id_lookup).

    edge_id_lookup is keyed (parent, child) -> edge_id so the caller can
    look up the corresponding SegmentImpedance.
    """
    # adjacency: node -> list[(neighbour, edge_id)]
    adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    node_ids = {n.id for n in input.topology.nodes}
    for e in input.topology.edges:
        if e.from_node not in node_ids or e.to_node not in node_ids:
            raise ValueError(
                f"edge {e.id} references unknown node ({e.from_node} or {e.to_node})"
            )
        adj[e.from_node].append((e.to_node, e.id))
        adj[e.to_node].append((e.from_node, e.id))

    if input.slack_node not in node_ids:
        raise ValueError(f"slack_node '{input.slack_node}' not in topology")

    parent: dict[str, str | None] = {input.slack_node: None}
    edge_lookup: dict[tuple[str, str], str] = {}
    order: list[str] = [input.slack_node]
    q: deque[str] = deque([input.slack_node])
    while q:
        u = q.popleft()
        for v, eid in adj[u]:
            if v in parent:
                if parent[u] != v:
                    raise ValueError(
                        f"network is not radial — cycle detected at edge {eid} ({u}-{v})"
                    )
                continue
            parent[v] = u
            edge_lookup[(u, v)] = eid
            order.append(v)
            q.append(v)

    unreached = node_ids - set(parent)
    if unreached:
        raise ValueError(
            f"network has {len(unreached)} node(s) disconnected from slack: {sorted(unreached)}"
        )
    return parent, order, edge_lookup


def solve_load_flow(input: LoadFlowInput | dict) -> LoadFlowResult:
    if isinstance(input, dict):
        input = LoadFlowInput(**input)

    parent, bfs_order, edge_lookup = _build_tree(input)
    children: dict[str, list[str]] = defaultdict(list)
    for n, p in parent.items():
        if p is not None:
            children[p].append(n)
    leaves_first = list(reversed(bfs_order))  # safe leaf-to-root traversal

    # Index segments by edge_id; missing impedance => zero-impedance bus tie.
    seg_by_edge = {s.edge_id: s for s in input.segments}

    # Loads keyed by node (sum if duplicates).
    # P and PQ-bus Q are fixed; PV-bus Q is mutable below.
    load_kw: dict[str, float] = defaultdict(float)
    load_kvar_pq: dict[str, float] = defaultdict(float)
    pv_specs: dict[str, BusLoad] = {}
    for ld in input.loads:
        load_kw[ld.node_id] += ld.p_kw
        if ld.bus_kind == "PV":
            if ld.v_setpoint_pu is None:
                raise ValueError(
                    f"PV bus '{ld.node_id}' missing v_setpoint_pu"
                )
            if ld.node_id == input.slack_node:
                raise ValueError(
                    f"slack_node '{ld.node_id}' cannot also be a PV bus"
                )
            # Multiple loads merging into one node with mixed kinds is ambiguous.
            if ld.node_id in pv_specs:
                raise ValueError(
                    f"node '{ld.node_id}' has multiple PV definitions"
                )
            pv_specs[ld.node_id] = ld
        else:
            load_kvar_pq[ld.node_id] += ld.q_kvar

    # Final effective load_kvar = PQ portion + PV solver output (updated per iter).
    # Initial guess for PV Q-injection (kvar, +ve = injection from gen into bus).
    pv_q_inject: dict[str, float] = {n: -spec.q_kvar for n, spec in pv_specs.items()}
    pv_saturated: dict[str, str | None] = {n: None for n in pv_specs}

    # Thevenin reactance from slack to each PV bus = sum of x_ohm along tree path.
    pv_x_thev: dict[str, float] = {}
    for n in pv_specs:
        x = 0.0
        cur = n
        while parent[cur] is not None:
            eid = edge_lookup[(parent[cur], cur)]
            seg = seg_by_edge.get(eid)
            if seg:
                x += seg.x_ohm
            cur = parent[cur]
        pv_x_thev[n] = max(x, 1e-6)

    phase_factor = 3 if input.phases == 3 else 1
    # Slack line-to-neutral phasor, reference angle 0.
    if input.phases == 3:
        v_slack_ln = (input.slack_voltage_kv * 1000.0) / math.sqrt(3.0)
    else:
        v_slack_ln = input.slack_voltage_kv * 1000.0
    v_slack = complex(v_slack_ln, 0.0)

    # Flat start.
    V: dict[str, complex] = {n: v_slack for n in bfs_order}
    I_branch: dict[str, complex] = {}  # keyed by edge_id, current flowing parent→child

    pv_tol_pu = max(input.tolerance_pu, 1e-5)
    converged = False
    iterations = 0
    notes: list[str] = []
    max_dv = 0.0
    for it in range(1, input.max_iterations + 1):
        iterations = it
        V_prev = dict(V)

        # Effective load_kvar this iteration: PQ part + (-pv_q_inject) at PV buses.
        load_kvar = dict(load_kvar_pq)
        for pv_n, q_inj in pv_q_inject.items():
            load_kvar[pv_n] = load_kvar_pq.get(pv_n, 0.0) - q_inj

        # Backward sweep — compute branch currents.
        I_node: dict[str, complex] = {}
        for n in leaves_first:
            # Per-phase load current (S already divided by 3 if 3-phase).
            p_w = load_kw[n] * 1000.0 / phase_factor
            q_var = load_kvar.get(n, 0.0) * 1000.0 / phase_factor
            S_phase = complex(p_w, q_var)
            if abs(V[n]) < 1e-9:
                I_load = 0j
            else:
                I_load = (S_phase / V[n]).conjugate()
            I_total = I_load
            for c in children[n]:
                eid = edge_lookup[(n, c)]
                I_total += I_branch[eid]
            I_node[n] = I_total
            p = parent[n]
            if p is not None:
                eid = edge_lookup[(p, n)]
                I_branch[eid] = I_total

        # Forward sweep — recompute voltages.
        for n in bfs_order:
            p = parent[n]
            if p is None:
                V[n] = v_slack
                continue
            eid = edge_lookup[(p, n)]
            seg = seg_by_edge.get(eid)
            Z = complex(seg.r_ohm, seg.x_ohm) if seg else 0j
            V[n] = V[p] - Z * I_branch[eid]

        # PV-bus Q update — sensitivity ΔQ_inject ≈ ΔV·|V|·phase_factor / X_thev
        # (per-phase ∂|V|/∂Q_phase ≈ X/|V|; ΔQ here is total 3-phase kvar).
        max_dv_pv = 0.0
        for pv_n, spec in pv_specs.items():
            if pv_saturated[pv_n] is not None:
                continue  # locked at limit, treat as PQ
            v_mag = abs(V[pv_n])
            v_target_phase = spec.v_setpoint_pu * v_slack_ln
            dv = v_target_phase - v_mag
            max_dv_pv = max(max_dv_pv, abs(dv) / v_slack_ln)
            x_th = pv_x_thev[pv_n]
            dq_kvar = dv * v_mag * phase_factor / x_th / 1000.0
            new_q_inject = pv_q_inject[pv_n] + dq_kvar
            qmin, qmax = spec.q_min_kvar, spec.q_max_kvar
            if qmin is not None and new_q_inject < qmin:
                new_q_inject = qmin
                pv_saturated[pv_n] = "min"
            elif qmax is not None and new_q_inject > qmax:
                new_q_inject = qmax
                pv_saturated[pv_n] = "max"
            pv_q_inject[pv_n] = new_q_inject

        # Convergence on max ΔV (pu) AND PV setpoint mismatch.
        max_dv = max(abs(V[n] - V_prev[n]) / v_slack_ln for n in V)
        if max_dv < input.tolerance_pu and max_dv_pv < pv_tol_pu:
            converged = True
            break

    if not converged:
        notes.append(
            f"did not converge in {input.max_iterations} iterations "
            f"(last ΔV={max_dv:.2e} pu)"
        )

    # Build outputs.
    bus_voltages: list[BusVoltage] = []
    for n in bfs_order:
        v_phase = V[n]
        v_ll_kv = abs(v_phase) * (math.sqrt(3.0) if input.phases == 3 else 1.0) / 1000.0
        bus_voltages.append(
            BusVoltage(
                node_id=n,
                voltage_kv=v_ll_kv,
                voltage_pu=abs(v_phase) / v_slack_ln,
                angle_deg=math.degrees(cmath.phase(v_phase)),
            )
        )

    segment_flows: list[SegmentFlow] = []
    total_loss_w = 0.0
    for n in bfs_order:
        p = parent[n]
        if p is None:
            continue
        eid = edge_lookup[(p, n)]
        I_p = I_branch[eid]
        I_mag = abs(I_p)
        seg = seg_by_edge.get(eid)
        Z = complex(seg.r_ohm, seg.x_ohm) if seg else 0j
        # 3-phase active/reactive flow at the 'from' (parent) end:
        S_from_phase = V[p] * I_p.conjugate()
        S_from = S_from_phase * phase_factor
        # Loss in branch = |I|² * Z, three-phase.
        S_loss_phase = (I_mag ** 2) * Z
        S_loss = S_loss_phase * phase_factor
        total_loss_w += S_loss.real
        v_from = abs(V[p])
        v_to = abs(V[n])
        vd_pct = 100.0 * (v_from - v_to) / v_from if v_from > 0 else 0.0
        segment_flows.append(
            SegmentFlow(
                edge_id=eid,
                current_a=I_mag,
                p_flow_kw=S_from.real / 1000.0,
                q_flow_kvar=S_from.imag / 1000.0,
                p_loss_kw=S_loss.real / 1000.0,
                q_loss_kvar=S_loss.imag / 1000.0,
                voltage_drop_pct=vd_pct,
            )
        )

    pv_results: list[PVBusResult] = []
    for pv_n, spec in pv_specs.items():
        v_phase = V[pv_n]
        v_solved_pu = abs(v_phase) / v_slack_ln
        sat = pv_saturated[pv_n]
        if sat is not None:
            notes.append(
                f"PV bus '{pv_n}' Q-saturated at {sat} limit "
                f"(reverted to PQ at q_inject={pv_q_inject[pv_n]:.3f} kvar)"
            )
        pv_results.append(
            PVBusResult(
                node_id=pv_n,
                v_setpoint_pu=spec.v_setpoint_pu,
                v_solved_pu=v_solved_pu,
                q_inject_kvar=pv_q_inject[pv_n],
                saturated=sat is not None,
                saturation_limit=sat,
            )
        )

    total_load_kw = sum(load_kw.values())
    return LoadFlowResult(
        converged=converged,
        iterations=iterations,
        bus_voltages=bus_voltages,
        segment_flows=segment_flows,
        pv_buses=pv_results,
        total_loss_kw=total_loss_w / 1000.0,
        total_load_kw=total_load_kw,
        notes=notes,
    )
