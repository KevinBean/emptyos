"""Thermal resistances T1..T4 — IEC 60287-2-1.

T1 — between conductor and sheath (insulation)
T2 — between sheath and armour (none if no armour)
T3 — outside the cable (jacket)
T4 — surroundings (soil / duct / air); installation-specific, in
     installation_types.py

Per-metre, K·m/W. Single-core formulas in Phase A; multi-core
adapters in Phase B.
"""

from __future__ import annotations

import math


def t1_single_core(
    rho_t_insulation: float,
    conductor_diameter_m: float,
    insulation_thickness_m: float,
    inner_semicon_thickness_m: float = 0.0,
) -> float:
    """T1 = ρ_T / (2π) · ln(D_i / d_c)  — IEC 60287-2-1 eq. (1).

    Per IEC 60287-2-1 §4.1.1 NOTE: the inner semiconducting layer
    (conductor screen) is treated as part of the insulation system
    for thermal purposes. The outer semicon is bonded thermally to
    the metallic sheath and is excluded from D_i.

    D_i = d_c + 2·(t_innersc + t_ins).
    """
    effective_thickness = insulation_thickness_m + inner_semicon_thickness_m
    if effective_thickness <= 0:
        return 0.0
    return (rho_t_insulation / (2 * math.pi)) * math.log(
        1 + 2 * effective_thickness / conductor_diameter_m
    )


def t2_no_armour() -> float:
    return 0.0


def t3_jacket(
    rho_t_jacket: float,
    armour_outer_diameter_m: float,
    jacket_thickness_m: float,
) -> float:
    """T3 = ρ_T / (2π) · ln(1 + 2t₃/D'_a)  — IEC 60287-2-1 eq. (5)."""
    if jacket_thickness_m <= 0 or armour_outer_diameter_m <= 0:
        return 0.0
    return (rho_t_jacket / (2 * math.pi)) * math.log(
        1 + 2 * jacket_thickness_m / armour_outer_diameter_m
    )
