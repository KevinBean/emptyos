"""Per-conductor R and full n×n L matrix from Carson, **without** bundle reduction.

The fault-distribution analytic solver has `per_unit_length_matrix` (n×n complex Z)
followed by `reduce_to_phase_bundle` (collapse to 2×2). The EMTP path uses ONLY
the unreduced matrix, then split it into:

    R_diag[i] = Re(Z[i,i])         — per-conductor series resistance
    L_full[i,j] = Im(Z[i,j]) / ω   — full n×n inductance matrix (henries/m)

This is the load-bearing decision documented in the plan: time-domain solves
need per-conductor info, which bundle reduction destroys.

Reuses fault-distribution's `per_unit_length_matrix` verbatim — Carson formulas,
GMR with permeability, all the validated bug-fixes from the analytic path.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np


def _load_fd_solver_module(name: str):
    """Load a fault-distribution solver module without depending on emptyos
    package layout (engines/ shouldn't hard-import from apps/personal/).

    Tries app namespace first; falls back to direct file load."""
    try:
        return __import__(
            f"emptyos.apps.personal.fault_distribution.solver.{name}",
            fromlist=["*"],
        )
    except ImportError:
        pass
    # Direct file load
    repo_root = Path(__file__).resolve().parents[3]
    fd_solver = repo_root / "apps" / "personal" / "fault-distribution" / "solver"
    target = fd_solver / f"{name}.py"
    if not target.exists():
        raise ImportError(f"cannot find fault-distribution solver module {name!r} at {target}")
    # Need to register parent package so relative imports work
    parent_pkg = "fd_solver_proxy"
    if parent_pkg not in sys.modules:
        import types
        pkg = types.ModuleType(parent_pkg)
        pkg.__path__ = [str(fd_solver)]
        pkg.__package__ = parent_pkg
        sys.modules[parent_pkg] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{parent_pkg}.{name}", target,
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = parent_pkg
    sys.modules[f"{parent_pkg}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


def per_unit_length_RL(conductors, soil_resistivity_ohm_m: float, frequency_hz: float):
    """Build n×n per-meter Z via Carson, then split into (R_full, L_full).

    Returns:
        R_full : (n, n) array of per-meter ohms (Re(Z)). Mutual entries get the
                 Carson real term (π² f × 10⁻⁷) — needed for the per-span
                 induced EMF in the bundle (phase-bundle mutual contributes a
                 small but non-negligible real component at 50/60 Hz).
        L_full : (n, n) array of per-meter henries (Im(Z) / ω)
    """
    impedances = _load_fd_solver_module("impedances")
    model = _load_fd_solver_module("model")
    SoilLayer = model.SoilLayer
    SoilModel = model.SoilModel

    soil = SoilModel(layers=[SoilLayer(resistivity_ohm_m=soil_resistivity_ohm_m)])
    Z = impedances.per_unit_length_matrix(conductors, soil, frequency_hz)
    n = len(Z)
    omega = 2 * math.pi * frequency_hz

    R_full = np.zeros((n, n), dtype=float)
    L_full = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(n):
            R_full[i, j] = float(Z[i][j].real)
            L_full[i, j] = float(Z[i][j].imag) / omega
    # Symmetrize (reciprocity)
    R_full = 0.5 * (R_full + R_full.T)
    L_full = 0.5 * (L_full + L_full.T)
    return R_full, L_full
