"""Adapter — fault-distribution Network → time-domain Netlist.

CRITICAL CALL: this adapter does **not** apply bundle-equivalent reduction
(`reduce_to_phase_bundle`). The whole reason the EMTP path exists is to
recover per-conductor currents (the OHEW-shunt-vs-OHEW-contribution
invariant from the PSCAD developer's note). We consume the full unreduced
n×n per-meter Z from `per_unit_length_matrix`, split into per-conductor R
matrix and full n×n L matrix, and instantiate a per-span k-port mutual L
block over the **bundle conductors only** (phase is treated as a stiff
current injection per the FCDIST/F09 convention).

Topology (per terminal, mirroring chain.py + thevenin-fault-bus):

    ground          # reference
    |
    Z_e                                    (terminal earth, R + L)
    |
    terminal_grid_k (V_grid = -I_t · Z_e in steady state)
    |
    R_NCC                                  (NCC: 1e-3 bonded, 1e9 open)
    |
    T[0] -- Z_t -- ground                  (structure shunt at T[0])
        |
        span 0:
            T[0] -- (per bundle conductor c) [R_cc + V_emf_c series] -- L_port_c -- T[1]
            (phase NOT in circuit — induced via V_emf_c synthesized from -z_pn × I_t × span_len)
        |
        T[1] -- Z_t -- ground
        ...
        T[N] = central
    |
    central -- Z_s -- ground               (central earth, R + L)

Per terminal we add two ISources:
  - ISrc_central_k:  pumps I_t into central (n_pos=central, n_neg=ground)
  - ISrc_grid_k:     forces V_grid = -I_t·Z_e at steady state by injecting
                     +I_t into ground / withdrawing from terminal_grid
                     (n_pos=ground, n_neg=terminal_grid)

Per-span EMF VSource per bundle conductor c carries phasor
    V_emf = -(R_full[c, phase] + jωL_full[c, phase]) × span_length × I_t
which reproduces the analytic chain.py `emf_per_span = -z_m × I_t` term.

KCL at central in steady state:
    Σ_k I_t (injected by ISrc_central_k)  =  V_central / Z_s  +  Σ_k i_bundle_at_central_k
which matches `[[thevenin-fault-bus]]` exactly.

Probes:
    - v_central                 — central GPR
    - i_central_earth           — current through Z_s (gives split factor)
    - v_grid_k                  — terminal grid GPR
    - i_grounding_k             — current via NCC into terminal_grid (bonded only)
    - i_bundle_t{k}_s{s}_c{c}   — per-conductor port current within span s,
                                  conductor c on terminal k. Probed for first
                                  span by default; pass probe_all_bundle_ports=True
                                  for full per-span probing (heavy, diagnostic).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..elements import (
    BranchCurrentProbe,
    Inductor,
    ISourceSinusoidal,
    MutualBranchProbe,
    MutualInductorBlock,
    NodeProbe,
    Resistor,
    VSourceSinusoidal,
)
from ..netlist import Netlist
from ..stepper import SimParams


def _split_RL(z: complex, omega: float) -> tuple[float, float]:
    R = float(z.real) if z.real > 0 else 0.0
    L = float(z.imag / omega) if abs(z.imag) > 1e-15 else 0.0
    return R, L


def network_to_netlist(
    network: Any,
    *,
    t_end: float = 0.16,
    dt: float = 50e-6,
    probe_all_bundle_ports: bool = False,
) -> Netlist:
    f0 = float(network.frequency_hz)
    omega = 2 * math.pi * f0

    if not network.soil.is_uniform:
        raise NotImplementedError("two-layer soil not supported in EMTP v0.1")
    rho = network.soil.layers[0].resistivity_ohm_m

    nodes: list[str] = ["ground"]

    def add_node(name: str) -> int:
        nodes.append(name)
        return len(nodes) - 1

    elements: list[Any] = []
    probes: list[Any] = []
    eid_counter = {"i": 0}

    def eid(prefix: str) -> str:
        eid_counter["i"] += 1
        return f"{prefix}_{eid_counter['i']:05d}"

    def add_series_RL(n_pos: int, n_neg: int, R: float, L: float, label: str) -> str | None:
        if R <= 0 and L <= 0:
            return None
        if L <= 0:
            r_id = eid(f"R_{label}")
            elements.append(Resistor(id=r_id, n_pos=n_pos, n_neg=n_neg, r=max(R, 1e-9)))
            return r_id
        if R <= 0:
            l_id = eid(f"L_{label}")
            elements.append(Inductor(id=l_id, n_pos=n_pos, n_neg=n_neg, L=max(L, 1e-15)))
            return l_id
        internal = add_node(f"{nodes[n_pos]}__{label}__int")
        r_id = eid(f"R_{label}")
        elements.append(Resistor(id=r_id, n_pos=n_pos, n_neg=internal, r=R))
        l_id = eid(f"L_{label}")
        elements.append(Inductor(id=l_id, n_pos=internal, n_neg=n_neg, L=L))
        return r_id

    # ── Central bus + central earth Z_s ──────────────────────────────
    central = add_node("central")
    R_s, L_s = _split_RL(network.central.grid_z_ohm, omega)
    central_R_id = add_series_RL(central, 0, R_s, L_s, "central_earth")
    probes.append(NodeProbe(name="v_central", node=central))
    if central_R_id:
        probes.append(BranchCurrentProbe(name="i_central_earth", element_id=central_R_id))

    from .impedance_helpers import per_unit_length_RL

    # ── Per-terminal builds ───────────────────────────────────────────
    for k, term in enumerate(network.terminals):
        I_t = term.source_current
        if not term.block:
            raise ValueError(f"terminal {term.name!r} has no spans")
        first_xs = term.block[0].conductors
        n_total = len(first_xs)
        phase_idx = next((i for i, c in enumerate(first_xs) if c.role == "phase"), None)
        if phase_idx is None:
            raise ValueError(f"terminal {term.name!r}: no phase conductor")
        bundle_idx = [i for i in range(n_total) if i != phase_idx]
        n_bundle = len(bundle_idx)

        # Terminal earth + grid node
        tg = add_node(f"tg_{k}")
        R_e, L_e = _split_RL(term.earth_z_ohm, omega)
        add_series_RL(tg, 0, R_e, L_e, f"earth_t{k}")
        probes.append(NodeProbe(name=f"v_grid_{k}", node=tg))

        # ISource — forces V_grid = -I_t · Z_e in steady state by withdrawing
        # I_t from tg (n_pos=ground, n_neg=tg → at tg: -I_t injection).
        elements.append(ISourceSinusoidal(
            id=eid(f"Isrc_grid_t{k}"),
            n_pos=0, n_neg=tg,
            I_re=float(I_t.real), I_im=float(I_t.imag),
            f_hz=f0,
        ))

        # ISource — delivers I_t at central (n_pos=central, n_neg=ground)
        elements.append(ISourceSinusoidal(
            id=eid(f"Isrc_central_t{k}"),
            n_pos=central, n_neg=0,
            I_re=float(I_t.real), I_im=float(I_t.imag),
            f_hz=f0,
        ))

        # NCC — between T[0] and tg
        ncc_open = False
        if term.bonding and term.bonding.bonding:
            for _, z_b in term.bonding.bonding.items():
                if abs(z_b) >= 1e8:
                    ncc_open = True
                    break

        # Tower nodes T[0..N], with T[N] == central
        flat_subs: list = []
        for sub in term.block:
            for _ in range(sub.n_spans):
                flat_subs.append(sub)
        N = len(flat_subs)

        T: list[int] = [add_node(f"T_t{k}_s0")]
        for s in range(1, N):
            T.append(add_node(f"T_t{k}_s{s}"))
        T.append(central)

        # NCC
        ncc_R = 1e9 if ncc_open else 1e-3
        ncc_id = eid(f"R_NCC_t{k}")
        elements.append(Resistor(id=ncc_id, n_pos=T[0], n_neg=tg, r=ncc_R))
        if not ncc_open:
            probes.append(BranchCurrentProbe(name=f"i_grounding_{k}", element_id=ncc_id))

        # Per-span build — bundle only (phase NOT a circuit conductor)
        for s, sub in enumerate(flat_subs):
            R_full, L_full = per_unit_length_RL(sub.conductors, rho, f0)
            length = float(sub.span_length_m)

            # Bundle (k-1)×(k-1) submatrices
            R_bb = R_full[np.ix_(bundle_idx, bundle_idx)] * length    # diagonal-dominant
            L_bb = L_full[np.ix_(bundle_idx, bundle_idx)] * length    # for L block
            # Phase-to-bundle mutual (R + jωL) — synthesizes EMF
            R_pb = R_full[bundle_idx, phase_idx] * length
            L_pb = L_full[bundle_idx, phase_idx] * length

            # Internal nodes per bundle conductor on the terminal side
            T_int = {c: add_node(f"T_t{k}_s{s}_int_c{c}") for c in bundle_idx}
            # Internal node between L-port and EMF source (central side)
            T_emf = {c: add_node(f"T_t{k}_s{s}_emf_c{c}") for c in bundle_idx}

            # Per-conductor self-R (diagonal of R_bb): T[s] -- R_cc -- T_int[c]
            for ii, c in enumerate(bundle_idx):
                elements.append(Resistor(
                    id=eid(f"R_bb_t{k}_s{s}_c{c}"),
                    n_pos=T[s], n_neg=T_int[c],
                    r=max(float(R_bb[ii, ii]), 1e-12),
                ))

            # Mutual L block for bundle: ports = bundle conductors,
            # n_pos = T_emf[c] (central side), n_neg = T_int[c] (terminal side).
            n_pos_ports = [T_emf[c] for c in bundle_idx]
            n_neg_ports = [T_int[c] for c in bundle_idx]
            block_id = eid(f"M_t{k}_s{s}")
            elements.append(MutualInductorBlock(
                id=block_id,
                n_pos=n_pos_ports,
                n_neg=n_neg_ports,
                L=L_bb,
            ))

            # EMF VSource per bundle conductor: phasor = -(R_pb + jωL_pb) × I_t
            # Polarity: V_pos = T[s+1] (central side), V_neg = T_emf[c]. This
            # matches chain.py's `v_acc += emf_per_span` — walking the bundle
            # centralward, the induced EMF raises v_acc by emf_per_span.
            for ii, c in enumerate(bundle_idx):
                z_pb_c = complex(float(R_pb[ii]), omega * float(L_pb[ii]))
                v_emf = -z_pb_c * I_t                     # phasor
                elements.append(VSourceSinusoidal(
                    id=eid(f"Vemf_t{k}_s{s}_c{c}"),
                    n_pos=T[s + 1], n_neg=T_emf[c],
                    V_re=float(v_emf.real), V_im=float(v_emf.imag),
                    f_hz=f0, r_int=1e-3,
                ))

            # Probes on first span (and optionally all spans)
            if s == 0 or probe_all_bundle_ports:
                for port_idx, c in enumerate(bundle_idx):
                    probes.append(MutualBranchProbe(
                        name=f"i_bundle_t{k}_s{s}_c{c}",
                        element_id=block_id, port=port_idx,
                    ))

            # Structure shunt at T[s] (terminal-side tower of this span;
            # mirror chain.py — N shunts, one per span at T[0..N-1])
            R_t, L_t = _split_RL(sub.structure_z_ohm, omega)
            if R_t > 0 or L_t > 0:
                add_series_RL(T[s], 0, R_t, L_t, f"tower_t{k}_s{s}")

    return Netlist(
        nodes=nodes,
        elements=elements,
        probes=probes,
        params=SimParams(f0_hz=f0, dt_s=dt, t_end_s=t_end),
    )
