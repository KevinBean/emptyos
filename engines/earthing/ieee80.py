"""IEEE Std 80 — Substation grounding-grid analysis (homogeneous soil).

Three calculations from the standard:

- :func:`sverak_grid_resistance` — Sverak's modification of Schwarz's
  formula for grid resistance Rg in homogeneous soil. IEEE 80-2013 §14.

- :func:`tolerable_touch_voltage` / :func:`tolerable_step_voltage` —
  IEEE 80 §8 body-current criterion (Dalziel's 50-kg / 70-kg curves)
  for permissible touch and step potentials given fault duration ``ts``,
  surface-layer resistivity, and surface-layer thickness.

Two-layer soil and grid+rods (full Schwarz) come in a follow-up — V1
covers the homogeneous-soil bench-design loop end-to-end so the app can
ship a useful first answer.
"""

from __future__ import annotations

import math


def plate_grid_resistance(rho_soil_ohm_m: float, grid_area_m2: float) -> float:
    """Plate-bound R_g (IEEE 80 Eq. 55), Ω.

    Rg_min = (ρ/4) · √(π/A)

    Treats the grid footprint as a circular metal plate at the surface —
    the absolute lower bound. No real grid (finite L_T) can beat it, so
    if this is already higher than the design target you need more area
    or lower-resistivity soil; no amount of conductor will close the gap.
    """
    if rho_soil_ohm_m <= 0 or grid_area_m2 <= 0:
        raise ValueError("rho_soil and grid_area must be positive")
    return (rho_soil_ohm_m / 4.0) * math.sqrt(math.pi / grid_area_m2)


def sverak_grid_resistance(
    rho_soil_ohm_m: float,
    grid_total_length_m: float,
    grid_area_m2: float,
    burial_depth_m: float,
) -> float:
    """Sverak's grid-resistance formula (homogeneous soil), Ω.

    Rg = ρ · [1/L_T + 1/√(20·A) · (1 + 1/(1 + h·√(20/A)))]

    Parameters
    ----------
    rho_soil_ohm_m : uniform soil resistivity, Ω·m
    grid_total_length_m : total buried conductor length L_T (m), including
        both grid and rod contributions
    grid_area_m2 : area enclosed by the grid perimeter A, m²
    burial_depth_m : grid burial depth h, m

    Notes
    -----
    Valid for 0.25 ≤ h ≤ 2.5 m. For h outside that range, two-layer
    soil, or grid+rod combinations with very different rod lengths,
    use the full Schwarz two-term expression (not yet implemented).
    """
    if (
        rho_soil_ohm_m <= 0
        or grid_total_length_m <= 0
        or grid_area_m2 <= 0
        or burial_depth_m <= 0
    ):
        raise ValueError("all inputs must be positive")
    sqrt_20A = math.sqrt(20.0 * grid_area_m2)
    rg = rho_soil_ohm_m * (
        1.0 / grid_total_length_m
        + (1.0 / sqrt_20A) * (1.0 + 1.0 / (1.0 + burial_depth_m * math.sqrt(20.0 / grid_area_m2)))
    )
    return rg


def _surface_layer_derating(
    rho_surface_ohm_m: float,
    rho_soil_ohm_m: float,
    surface_layer_thickness_m: float,
) -> float:
    """C_s — IEEE 80 eq. (27) surface-layer derating (Sunde approximation).

    C_s = 1 - 0.09 · (1 - ρ/ρ_s) / (2·h_s + 0.09)

    Where ρ is the underlying soil resistivity and ρ_s is the surface
    crushed-rock layer (typically 2500-3000 Ω·m for clean stone). Caps
    at 1.0 when no surface layer is present (h_s → 0 ⇒ C_s → 1).
    """
    if surface_layer_thickness_m <= 0 or rho_surface_ohm_m <= 0:
        return 1.0
    return 1.0 - 0.09 * (1.0 - rho_soil_ohm_m / rho_surface_ohm_m) / (
        2.0 * surface_layer_thickness_m + 0.09
    )


def tolerable_touch_voltage(
    fault_duration_s: float,
    rho_soil_ohm_m: float,
    *,
    body_weight_kg: float = 50.0,
    rho_surface_ohm_m: float = 0.0,
    surface_layer_thickness_m: float = 0.0,
) -> float:
    """IEEE 80 §8 tolerable touch voltage E_touch, V.

    For 50 kg body:
        E_touch_50 = (1000 + 1.5 · C_s · ρ_s) · 0.116 / √t_s

    For 70 kg:
        E_touch_70 = (1000 + 1.5 · C_s · ρ_s) · 0.157 / √t_s

    The 50-kg curve is more conservative (lower allowable voltage) and
    is the IEEE 80 default for substation design unless the operator
    population is known to exceed 70 kg.

    ``rho_surface_ohm_m`` and ``surface_layer_thickness_m`` describe a
    high-resistivity surface layer (e.g. crushed rock); pass 0 / 0 if
    there is no such layer (bare-earth case — caller carries the risk).
    """
    if fault_duration_s <= 0:
        raise ValueError("fault_duration_s must be > 0")
    if body_weight_kg == 50:
        k_body = 0.116
    elif body_weight_kg == 70:
        k_body = 0.157
    else:
        raise ValueError("body_weight_kg must be 50 or 70 (IEEE 80 curves)")
    cs = _surface_layer_derating(rho_surface_ohm_m, rho_soil_ohm_m, surface_layer_thickness_m)
    rho_eff = rho_surface_ohm_m if rho_surface_ohm_m > 0 else rho_soil_ohm_m
    return (1000.0 + 1.5 * cs * rho_eff) * k_body / math.sqrt(fault_duration_s)


def tolerable_step_voltage(
    fault_duration_s: float,
    rho_soil_ohm_m: float,
    *,
    body_weight_kg: float = 50.0,
    rho_surface_ohm_m: float = 0.0,
    surface_layer_thickness_m: float = 0.0,
) -> float:
    """IEEE 80 §8 tolerable step voltage E_step, V.

    For 50 kg:
        E_step_50 = (1000 + 6 · C_s · ρ_s) · 0.116 / √t_s

    For 70 kg:
        E_step_70 = (1000 + 6 · C_s · ρ_s) · 0.157 / √t_s

    Step voltage is always higher than touch voltage (different body-
    impedance circuit: foot-foot vs hand-foot), so the design is
    typically driven by the touch criterion.
    """
    if fault_duration_s <= 0:
        raise ValueError("fault_duration_s must be > 0")
    if body_weight_kg == 50:
        k_body = 0.116
    elif body_weight_kg == 70:
        k_body = 0.157
    else:
        raise ValueError("body_weight_kg must be 50 or 70 (IEEE 80 curves)")
    cs = _surface_layer_derating(rho_surface_ohm_m, rho_soil_ohm_m, surface_layer_thickness_m)
    rho_eff = rho_surface_ohm_m if rho_surface_ohm_m > 0 else rho_soil_ohm_m
    return (1000.0 + 6.0 * cs * rho_eff) * k_body / math.sqrt(fault_duration_s)


# ── Mesh + step potentials inside the yard (IEEE 80 §16) ───────


def _effective_n(
    grid_length_m: float,
    grid_width_m: float,
    grid_total_length_m: float,
) -> float:
    """Effective number of parallel conductors n = n_a·n_b·n_c·n_d.

    For square or rectangular grids, n_b = n_c = n_d = 1 (IEEE 80 eqs.
    85-87), so n collapses to n_a = 2·L_C / L_p where L_p is the grid
    perimeter. L-, T- and irregular shapes need the full product.
    """
    if grid_length_m <= 0 or grid_width_m <= 0 or grid_total_length_m <= 0:
        raise ValueError("grid dimensions and L_C must be positive")
    perimeter_m = 2.0 * (grid_length_m + grid_width_m)
    return 2.0 * grid_total_length_m / perimeter_m


def mesh_geometric_factor_km(
    spacing_m: float,
    burial_depth_m: float,
    conductor_diameter_m: float,
    n_eff: float,
    *,
    rods_on_perimeter: bool,
) -> dict:
    """K_m — IEEE 80 eq. (81) geometric factor for mesh voltage.

    K_m = (1/2π)·[ ln(D²/(16·h·d) + (D+2h)²/(8·D·d) − h/(4·d))
                  + (K_ii/K_h)·ln(8 / (π·(2n−1))) ]

    Returns a dict with K_m plus its sub-factors so the caller can
    surface them in audit output.

    K_ii is the corrective weighting for rod placement:

        - K_ii = 1.0 for grids with rods on the perimeter or corners,
          or any grid where rods are along the edge.
        - K_ii = 1 / (2n)^(2/n) for grids with no rods, or rods placed
          only inside the grid (not on the perimeter).

    K_h = √(1 + h/h_0) with reference depth h_0 = 1 m corrects for the
    burial-depth dependence; deeper grids reduce mesh voltage.
    """
    if spacing_m <= 0 or burial_depth_m <= 0 or conductor_diameter_m <= 0:
        raise ValueError("spacing, burial_depth, conductor_diameter must be positive")
    if n_eff < 1:
        raise ValueError("n_eff must be ≥ 1")

    h = burial_depth_m
    d = conductor_diameter_m
    D = spacing_m

    if rods_on_perimeter:
        k_ii = 1.0
    else:
        k_ii = 1.0 / (2.0 * n_eff) ** (2.0 / n_eff)

    h0 = 1.0
    k_h = math.sqrt(1.0 + h / h0)

    term1 = (
        D * D / (16.0 * h * d)
        + (D + 2.0 * h) ** 2 / (8.0 * D * d)
        - h / (4.0 * d)
    )
    if term1 <= 0:
        raise ValueError("K_m geometric term went non-positive — check D/h/d inputs")
    term2 = (k_ii / k_h) * math.log(8.0 / (math.pi * (2.0 * n_eff - 1.0)))
    k_m = (1.0 / (2.0 * math.pi)) * (math.log(term1) + term2)
    return {"K_m": k_m, "K_ii": k_ii, "K_h": k_h, "n_eff": n_eff}


def step_geometric_factor_ks(
    spacing_m: float,
    burial_depth_m: float,
    n_eff: float,
) -> float:
    """K_s — IEEE 80 eq. (92) geometric factor for step voltage.

    K_s = (1/π)·[ 1/(2h) + 1/(D+h) + (1/D)·(1 − 0.5^(n−2)) ]

    Step voltage is foot-to-foot, so the depth term 1/(2h) dominates;
    deeper grids reduce step voltage strongly.
    """
    if spacing_m <= 0 or burial_depth_m <= 0:
        raise ValueError("spacing and burial_depth must be positive")
    if n_eff < 2:
        # Eq. (92) is defined for n ≥ 2 (grids with at least 2 parallel
        # conductors per side). Smaller n = single conductor; not a grid.
        raise ValueError("n_eff must be ≥ 2 for step-voltage formula")
    h = burial_depth_m
    D = spacing_m
    return (1.0 / math.pi) * (
        1.0 / (2.0 * h)
        + 1.0 / (D + h)
        + (1.0 / D) * (1.0 - 0.5 ** (n_eff - 2.0))
    )


def irregularity_factor_ki(n_eff: float) -> float:
    """K_i — IEEE 80 eq. (89). Linear in n_eff.

    K_i = 0.644 + 0.148·n
    """
    if n_eff < 1:
        raise ValueError("n_eff must be ≥ 1")
    return 0.644 + 0.148 * n_eff


def _effective_length_lm(
    grid_total_length_m: float,
    grid_length_m: float,
    grid_width_m: float,
    *,
    n_rods: int,
    rod_length_m: float,
    rods_on_perimeter: bool,
) -> float:
    """L_M — effective buried length for mesh voltage (IEEE 80 eqs. 85-86).

    No rods or rods inside only → L_M = L_C + L_R
    Rods on perimeter / corners → L_M = L_C + [1.55 + 1.22·(L_r/√(L_x²+L_y²))]·L_R
    """
    L_R = float(n_rods) * float(rod_length_m)
    if n_rods <= 0 or rod_length_m <= 0 or not rods_on_perimeter:
        return grid_total_length_m + L_R
    diag = math.sqrt(grid_length_m * grid_length_m + grid_width_m * grid_width_m)
    weight = 1.55 + 1.22 * (rod_length_m / diag)
    return grid_total_length_m + weight * L_R


def _effective_length_ls(
    grid_total_length_m: float,
    *,
    n_rods: int,
    rod_length_m: float,
) -> float:
    """L_S — effective buried length for step voltage. IEEE 80 eq. (93).

    L_S = 0.75·L_C + 0.85·L_R
    """
    L_R = float(n_rods) * float(rod_length_m)
    return 0.75 * grid_total_length_m + 0.85 * L_R


def mesh_voltage(
    rho_a_ohm_m: float,
    fault_current_a: float,
    *,
    grid_length_m: float,
    grid_width_m: float,
    grid_total_length_m: float,
    spacing_m: float,
    burial_depth_m: float,
    conductor_diameter_m: float,
    n_rods: int = 0,
    rod_length_m: float = 0.0,
    rods_on_perimeter: bool = False,
) -> dict:
    """Mesh voltage E_m inside the grid, V. IEEE 80 §16, eq. (80).

    E_m = ρ · K_m · K_i · I_G / L_M

    Where ``I_G`` is the actual ground-return portion of the fault
    current (after the split-factor analysis); for a first-pass
    calculation the user can pass the full fault current.

    Returns a dict with E_m plus K_m, K_i, K_ii, K_h, n_eff, L_M so
    audit output can show every intermediate factor.
    """
    if rho_a_ohm_m <= 0 or fault_current_a <= 0:
        raise ValueError("rho and fault current must be positive")
    n_eff = _effective_n(grid_length_m, grid_width_m, grid_total_length_m)
    km = mesh_geometric_factor_km(
        spacing_m,
        burial_depth_m,
        conductor_diameter_m,
        n_eff,
        rods_on_perimeter=rods_on_perimeter,
    )
    k_i = irregularity_factor_ki(n_eff)
    L_M = _effective_length_lm(
        grid_total_length_m,
        grid_length_m,
        grid_width_m,
        n_rods=n_rods,
        rod_length_m=rod_length_m,
        rods_on_perimeter=rods_on_perimeter,
    )
    if L_M <= 0:
        raise ValueError("effective length L_M went non-positive")
    e_m = rho_a_ohm_m * km["K_m"] * k_i * fault_current_a / L_M
    return {
        "E_m_v": e_m,
        "K_m": km["K_m"],
        "K_ii": km["K_ii"],
        "K_h": km["K_h"],
        "K_i": k_i,
        "n_eff": n_eff,
        "L_M_m": L_M,
    }


def step_voltage(
    rho_a_ohm_m: float,
    fault_current_a: float,
    *,
    grid_length_m: float,
    grid_width_m: float,
    grid_total_length_m: float,
    spacing_m: float,
    burial_depth_m: float,
    n_rods: int = 0,
    rod_length_m: float = 0.0,
) -> dict:
    """Step voltage E_s along the perimeter, V. IEEE 80 §16, eq. (91).

    E_s = ρ · K_s · K_i · I_G / L_S

    Step voltage is the foot-to-foot potential difference at the worst
    location (typically just outside the corner of the grid); for grids
    with crushed-rock surface treatment the tolerable E_step is much
    higher than E_touch, so step rarely drives the design — but it must
    still be checked.
    """
    if rho_a_ohm_m <= 0 or fault_current_a <= 0:
        raise ValueError("rho and fault current must be positive")
    n_eff = _effective_n(grid_length_m, grid_width_m, grid_total_length_m)
    k_s = step_geometric_factor_ks(spacing_m, burial_depth_m, n_eff)
    k_i = irregularity_factor_ki(n_eff)
    L_S = _effective_length_ls(
        grid_total_length_m,
        n_rods=n_rods,
        rod_length_m=rod_length_m,
    )
    if L_S <= 0:
        raise ValueError("effective length L_S went non-positive")
    e_s = rho_a_ohm_m * k_s * k_i * fault_current_a / L_S
    return {
        "E_s_v": e_s,
        "K_s": k_s,
        "K_i": k_i,
        "n_eff": n_eff,
        "L_S_m": L_S,
    }
