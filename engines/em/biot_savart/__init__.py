"""3D Biot-Savart magnetic field + method-of-images E-field.

Frequency-domain (single-frequency phasor); arbitrary 3D segmented
conductors with optional catenary sag for spans. Computes B and E at
arbitrary field points, along profiles, and on grids.

Ported from `KevinBean/EMF/emf_calculation_core.js` (≈624 lines).
Algorithm reference:
    Vujević et al., "Comparison of 2D Algorithms for the Computation of
    Power Line Electric and Magnetic Fields" (Int Trans Elec Energy
    Syst, 2011) — included as PDF in the source repo.
    Magnetic field via Biot-Savart with closed-form per-segment
    integration (eqs. 13–15 of the source paper).

This package is consumed by `apps/interference` and any app needing
B/E-field at distance from arbitrary conductor topology (e.g.
substation neighbourhood EMF, induced voltage in parallel cables).
"""

from .core import (
    ConductorSegment,
    CatenaryConductor,
    PowerLine,
    field_at_point,
    field_along_axis,
    field_grid,
)

__all__ = [
    "ConductorSegment",
    "CatenaryConductor",
    "PowerLine",
    "field_at_point",
    "field_along_axis",
    "field_grid",
]
