"""Time-stepping kernel — assemble Y, factor LU, solve per timestep.

See `[[nodal-analysis-time-stepping.md]]` (vault: 30_Resources/KB/power-systems/formulas/)
once written. Per-step procedure:

  1. Stamp Y from all elements (R, G_L, G_C, mutual-L block, switch).
  2. Drop ground row/column (node 0).
  3. LU-factor reduced Y once at start; refactor only on switch events.
  4. Per timestep:
       a. Build i_history vector from each element's history term.
       b. Add source contributions at time t.
       c. Solve Y_red · v_red = i_red.
       d. Reconstruct full v_node (v_node[0] = 0).
       e. Update each storage element's history state from v_node.
       f. Sample probes.

Switch refactor: when any IdealSwitch's state changes (open → closed at
t_close), re-stamp Y_red and re-factor. For v0.1 only fault-inception
switches are supported (one-shot, monotone open → closed).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .elements import (
    BranchCurrentProbe,
    Capacitor,
    Element,
    IdealSwitch,
    Inductor,
    ISourceSinusoidal,
    MutualBranchProbe,
    MutualInductorBlock,
    NodeProbe,
    Resistor,
    StampAccumulator,
    VSourceSinusoidal,
)
from .result import ProbeSeries, SimResult, extract_phasor


@dataclass
class SimParams:
    f0_hz: float = 50.0
    dt_s: float = 50e-6
    t_end_s: float = 0.2  # 10 cycles at 50 Hz
    fault_t_s: float | None = None  # informational only — actual switch t_close lives on the switch element
    max_kcl_residual: float = 1e-6
    quiet: bool = True


def _build_Y(elements: list[Element], n_nodes: int, t: float) -> sp.csc_matrix:
    """Stamp full Y from all elements at time t. Real-valued for v0.1
    (R, L, C, mutual L, switch; no frequency-dependent providers)."""
    acc = StampAccumulator()
    for el in elements:
        if isinstance(el, IdealSwitch):
            el._closed = el.is_closed_at(t)
        if isinstance(el, ISourceSinusoidal):
            continue  # ideal current source — no admittance
        el.stamp(acc)
    # Stamps are typed complex but in v0.1 every value is real-valued — drop imag.
    vals_real = np.array([v.real for v in acc.vals], dtype=float)
    Y = sp.coo_matrix(
        (vals_real, (acc.rows, acc.cols)),
        shape=(n_nodes, n_nodes),
        dtype=float,
    ).tocsr()
    return Y


def _drop_ground(Y: sp.csr_matrix, ground: int = 0) -> sp.csc_matrix:
    """Drop ground row/column. Returns (n-1) × (n-1) sparse CSC."""
    n = Y.shape[0]
    keep = [i for i in range(n) if i != ground]
    return Y[keep, :][:, keep].tocsc()


def _lu_factor(Y_red: sp.csc_matrix) -> Any:
    return spla.splu(Y_red)


def _lu_solve(factor, rhs: np.ndarray) -> np.ndarray:
    return factor.solve(rhs)


def _switches_changed(elements: list[Element], t_prev: float, t_now: float) -> bool:
    for el in elements:
        if isinstance(el, IdealSwitch):
            if el.is_closed_at(t_now) != el.is_closed_at(t_prev):
                return True
    return False


def step(
    elements: list[Element],
    n_nodes: int,
    probes: list,
    params: SimParams,
) -> SimResult:
    """Run a full simulation. Returns SimResult with per-probe waveforms + phasors."""
    if n_nodes < 2:
        raise ValueError("network needs at least 2 nodes (one of which is ground)")

    # Wire dt into storage elements
    for el in elements:
        if isinstance(el, (Inductor, Capacitor, MutualInductorBlock)):
            el.dt = params.dt_s

    # Element index for branch probes
    el_by_id = {el.id: el for el in elements if getattr(el, "id", "")}

    # Build initial Y at t=0
    t_start = time.time()
    Y_full = _build_Y(elements, n_nodes, t=0.0)
    Y_red = _drop_ground(Y_full)
    factor = _lu_factor(Y_red)

    # Probe storage
    n_steps = int(round(params.t_end_s / params.dt_s)) + 1
    t_arr = np.arange(n_steps) * params.dt_s

    probe_series: dict[str, ProbeSeries] = {}
    for p in probes:
        probe_series[p.name] = ProbeSeries(
            name=p.name,
            kind=p.kind,
            refs=("node",) if isinstance(p, NodeProbe) else (
                "element", p.element_id) if isinstance(p, BranchCurrentProbe)
            else ("element", p.element_id, str(p.port)),
            values=np.zeros(n_steps),
        )

    v_node = np.zeros(n_nodes, dtype=float)
    kcl_residual_max = 0.0
    warnings: list[str] = []
    t_prev = -1.0

    for step_idx in range(n_steps):
        t = t_arr[step_idx]

        # Refactor on switch event
        if step_idx > 0 and _switches_changed(elements, t_prev, t):
            Y_full = _build_Y(elements, n_nodes, t=t)
            Y_red = _drop_ground(Y_full)
            factor = _lu_factor(Y_red)

        # Build i_history + source injections
        i_h = np.zeros(n_nodes, dtype=float)
        for el in elements:
            if isinstance(el, (Inductor, Capacitor, MutualInductorBlock)):
                el.history_inject(i_h)
        for el in elements:
            if isinstance(el, (VSourceSinusoidal, ISourceSinusoidal)):
                el.source_inject(i_h, t)

        # Solve Y_red · v_red = i_red (drop ground node)
        i_red = np.delete(i_h, 0)
        v_red = _lu_solve(factor, i_red)
        v_node = np.zeros(n_nodes, dtype=float)
        v_node[1:] = v_red

        # KCL residual at this step
        residual_full = (Y_full @ v_node) - i_h
        residual_max = float(np.max(np.abs(residual_full[1:])))
        if residual_max > kcl_residual_max:
            kcl_residual_max = residual_max

        # Update storage history (elements expect a node-vector that supports .real)
        v_node_c = v_node.astype(complex)
        for el in elements:
            if isinstance(el, (Inductor, Capacitor, MutualInductorBlock)):
                el.update_history(v_node_c)

        # Sample probes
        for p in probes:
            if isinstance(p, NodeProbe):
                probe_series[p.name].values[step_idx] = p.sample(v_node_c)
            elif isinstance(p, BranchCurrentProbe):
                probe_series[p.name].values[step_idx] = p.sample(el_by_id, v_node_c)
            elif isinstance(p, MutualBranchProbe):
                probe_series[p.name].values[step_idx] = p.sample(el_by_id, v_node_c)

        t_prev = t

    # Phasor extraction (last full cycle)
    for p in probe_series.values():
        p.phasor = extract_phasor(p.values, params.dt_s, params.f0_hz)

    if kcl_residual_max > params.max_kcl_residual:
        warnings.append(
            f"max KCL residual {kcl_residual_max:.3e} exceeds threshold {params.max_kcl_residual:.0e}"
        )

    return SimResult(
        t=t_arr,
        f0_hz=params.f0_hz,
        dt_s=params.dt_s,
        probes=probe_series,
        kcl_residual_max=kcl_residual_max,
        n_steps=n_steps,
        n_nodes=n_nodes,
        runtime_s=time.time() - t_start,
        warnings=warnings,
    )
