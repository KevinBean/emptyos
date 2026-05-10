"""Earthing engine — substation grounding-grid analysis.

Two atomic capabilities, composable into a full IEEE 80 design loop:

- :mod:`resap` — Wenner four-pin soil-resistivity test interpretation.
  Forward: given a soil model (1- or 2-layer), compute ρ_apparent at
  each Wenner spacing. Inverse: given measured ρ_a(a), fit a 2-layer
  model (ρ₁, ρ₂, h) by direct grid search (no scipy dependency).

- :mod:`ieee80` — IEEE Std 80 grid-resistance Rg via the Sverak formula
  for homogeneous soil. Tolerable touch / step potentials per §8.

The app layer (`apps/earthing`, queued) wires these into a
substation-grounding workflow. Engines live here so they're testable
without the daemon and importable from other engines.
"""

from __future__ import annotations

from .ieee80 import (
    irregularity_factor_ki,
    mesh_geometric_factor_km,
    mesh_voltage,
    step_geometric_factor_ks,
    step_voltage,
    sverak_grid_resistance,
    tolerable_step_voltage,
    tolerable_touch_voltage,
)
from .resap import (
    apparent_resistivity_two_layer,
    apparent_resistivity_homogeneous,
    fit_two_layer_grid_search,
    wenner_resistance_to_apparent_rho,
)
from .split_factor import (
    annex_c_split_factor,
    estimate_split_factor,
    infinite_line_impedance,
    parallel_lines_impedance,
)

__all__ = [
    "annex_c_split_factor",
    "apparent_resistivity_homogeneous",
    "apparent_resistivity_two_layer",
    "estimate_split_factor",
    "fit_two_layer_grid_search",
    "infinite_line_impedance",
    "irregularity_factor_ki",
    "mesh_geometric_factor_km",
    "mesh_voltage",
    "parallel_lines_impedance",
    "step_geometric_factor_ks",
    "step_voltage",
    "sverak_grid_resistance",
    "tolerable_step_voltage",
    "tolerable_touch_voltage",
    "wenner_resistance_to_apparent_rho",
]
