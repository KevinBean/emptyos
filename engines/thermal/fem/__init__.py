"""engines/thermal/fem — 2D FEM thermal solver (Phase A, MVP).

Variation 5 of CIGRE TB 963: use IEC 60287 closed-form formulas to
compute per-cable losses (W_c, W_d, λ1·W_c) and feed those as heat
sources into a 2D finite-element steady-state thermal solve, replacing
the analytical T1..T4 network with a real temperature field.

MVP scope (v0):
    * direct-buried trefoil (3 single-core, touching), single-point bonding
    * steady-state only (no transient time-stepping)
    * no screen / duct / HDD / joint / Var-1 air gap / dry-out
    * Dirichlet BC on top surface, isothermal on far field

Validated against CIGRE TB 880 case-1 (target 886.1753 A).
Optional dep: gmsh (install via `pip install -e .[fem]`).
"""

from .postprocess import FEMRatingResult, compute_fem_rating, export_heatmap_data

__all__ = ["FEMRatingResult", "compute_fem_rating", "export_heatmap_data"]
