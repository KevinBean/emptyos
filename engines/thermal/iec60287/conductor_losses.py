"""Conductor AC resistance — IEC 60287-1-1 §2.1.

R = R' · (1 + y_s + y_p)
    R'  = DC resistance at the operating conductor temperature
    y_s = skin-effect factor
    y_p = proximity-effect factor
"""

from __future__ import annotations

import math

# Temperature coefficient of resistance, per °C (IEC 60287-1-1 Table 1).
ALPHA_20 = {"Cu": 3.93e-3, "Al": 4.03e-3}

# DC resistivity at 20 °C, Ω·m. Standard reference values.
RHO_20 = {"Cu": 1.7241e-8, "Al": 2.8264e-8}

# k_s, k_p coefficients — IEC 60287-1-1 Table 2 (typical values for
# stranded round non-segmented conductors). Specialised constructions
# (segmental, milliken, hollow) override these.
K_S = {"round_stranded": 1.0, "segmental": 0.435, "milliken": 0.435}
K_P = {"round_stranded": 1.0, "segmental": 0.37, "milliken": 0.37}


def dc_resistance_per_metre(
    csa_mm2: float,
    material: str = "Cu",
    temperature_c: float = 20.0,
    rho_20_override: float | None = None,
) -> float:
    """DC resistance R' at the operating temperature, Ω/m.

    R' = (ρ_20 / A) · (1 + α_20 · (θ - 20))
    """
    rho20 = rho_20_override if rho_20_override is not None else RHO_20[material]
    alpha = ALPHA_20[material]
    a_m2 = csa_mm2 * 1e-6
    return (rho20 / a_m2) * (1 + alpha * (temperature_c - 20.0))


def skin_effect_factor(r_dc_per_m: float, frequency_hz: float, k_s: float = 1.0) -> float:
    """y_s — IEC 60287-1-1 §2.1.2.

    x_s² = (8π·f / R'_dc) · 10⁻⁷ · k_s     (R'_dc in Ω/m)
    y_s  = x_s⁴ / (192 + 0.8 · x_s⁴)        valid for x_s ≤ 2.8
    """
    xs2 = (8 * math.pi * frequency_hz / r_dc_per_m) * 1e-7 * k_s
    xs4 = xs2 * xs2
    return xs4 / (192.0 + 0.8 * xs4)


def proximity_effect_factor(
    r_dc_per_m: float,
    frequency_hz: float,
    conductor_diameter_m: float,
    centre_spacing_m: float,
    n_conductors: int = 3,
    k_p: float = 1.0,
) -> float:
    """y_p — IEC 60287-1-1 §2.1.4 (3-core or 3 single-cores in trefoil/flat).

    Uses the standard form for x_p ≤ 2.8. n_conductors selects the
    expression: 3 → trefoil/3-core; 2 → two single-core flat (Phase B).
    """
    xp2 = (8 * math.pi * frequency_hz / r_dc_per_m) * 1e-7 * k_p
    xp4 = xp2 * xp2
    base = xp4 / (192.0 + 0.8 * xp4)
    dc_s = conductor_diameter_m / centre_spacing_m
    if n_conductors >= 3:
        # 3-core / trefoil / 3 single-core (IEC 60287-1-1 eq. (4))
        bracket = (
            0.312 * (dc_s ** 2)
            + 1.18 / (xp4 / (192.0 + 0.8 * xp4) + 0.27)
        )
        return base * (dc_s ** 2) * bracket
    # 2 single-core flat (eq. (5)) — Phase B
    return base * 2.9 * (dc_s ** 2)


def ac_resistance_per_metre(
    csa_mm2: float,
    conductor_diameter_m: float,
    centre_spacing_m: float,
    material: str = "Cu",
    temperature_c: float = 90.0,
    frequency_hz: float = 50.0,
    n_conductors: int = 3,
    k_s: float = 1.0,
    k_p: float = 1.0,
    rho_20_override: float | None = None,
) -> dict:
    """R = R' · (1 + y_s + y_p), Ω/m. Returns the breakdown."""
    r_dc = dc_resistance_per_metre(csa_mm2, material, temperature_c, rho_20_override)
    ys = skin_effect_factor(r_dc, frequency_hz, k_s)
    yp = proximity_effect_factor(
        r_dc, frequency_hz, conductor_diameter_m, centre_spacing_m, n_conductors, k_p
    )
    r_ac = r_dc * (1 + ys + yp)
    return {"r_dc": r_dc, "y_s": ys, "y_p": yp, "r_ac": r_ac}
