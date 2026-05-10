"""Schwarz two-term grid+rod resistance + 2-layer effective resistivity.

IEEE Std 80-2013 §14.5 — Schwarz's expression for the resistance of a
horizontal grid combined with N vertical ground rods. Replaces Sverak's
single-term homogeneous formula when rod contribution is non-trivial
(typically when n_rods · L_rod is comparable to grid total length).

Two-layer soil is handled by a Tagg/Burgsdorf-style equivalent uniform
resistivity ρ_a, which can then be plugged into either Sverak (grid-only)
or Schwarz (grid+rod). This is the practical engineering form taught in
IEEE 80-2013 §14.4 / EPRI TR-100622; modal Green's-function treatments
exist but the simple form is within ~10% of measured for h_1 / r in the
practical range.

Public surface:
    schwarz_grid_resistance(...) -> dict{R_grid, R_rods, R_mutual, R_g}
    schwarz_coefficients(grid_length_m, grid_width_m, burial_depth_m,
                         grid_area_m2) -> tuple[float, float]
    two_layer_effective_resistivity(rho_1, rho_2, h_layer1_m,
                                    grid_area_m2) -> float
"""

from __future__ import annotations

import math


def schwarz_coefficients(
    grid_length_m: float,
    grid_width_m: float,
    burial_depth_m: float,
    grid_area_m2: float,
) -> tuple[float, float]:
    """k₁, k₂ Schwarz coefficients per IEEE 80 §14.5 / Sverak (1981) fits.

    Both depend on grid aspect ratio L/W and on burial depth normalised
    by √A. The published closed forms cover three depths (0, √A/10,
    √A/6); this routine linearly interpolates between them for arbitrary
    h, clamping to the bounding cases outside the published range.

    Parameters are mutually consistent: ``grid_length_m`` and
    ``grid_width_m`` are the bounding-rectangle sides, ``grid_area_m2``
    is normally L·W (or close — slight differences are fine).
    """
    if grid_length_m <= 0 or grid_width_m <= 0 or grid_area_m2 <= 0:
        raise ValueError("grid dimensions must be positive")
    if burial_depth_m < 0:
        raise ValueError("burial_depth_m must be ≥ 0")

    lw = grid_length_m / grid_width_m if grid_width_m > 0 else 1.0
    sqrt_A = math.sqrt(grid_area_m2)

    # Sverak (1981) closed-form fits at three normalised depths.
    def coeffs_at(level: int) -> tuple[float, float]:
        if level == 0:                              # h = 0
            return -0.04 * lw + 1.41,  0.15 * lw + 5.50
        if level == 1:                              # h = √A / 10
            return -0.05 * lw + 1.20,  0.10 * lw + 4.68
        return -0.05 * lw + 1.13, -0.05 * lw + 4.40  # h = √A / 6

    h_norm = burial_depth_m / sqrt_A if sqrt_A > 0 else 0.0
    if h_norm <= 0:
        return coeffs_at(0)
    if h_norm <= 0.1:
        # interpolate between h=0 and h=√A/10
        t = h_norm / 0.1
        k1_a, k2_a = coeffs_at(0)
        k1_b, k2_b = coeffs_at(1)
        return k1_a + t * (k1_b - k1_a), k2_a + t * (k2_b - k2_a)
    if h_norm <= 1.0 / 6.0:
        # interpolate between h=√A/10 and h=√A/6
        t = (h_norm - 0.1) / (1.0 / 6.0 - 0.1)
        k1_a, k2_a = coeffs_at(1)
        k1_b, k2_b = coeffs_at(2)
        return k1_a + t * (k1_b - k1_a), k2_a + t * (k2_b - k2_a)
    # Beyond √A/6 — clamp to the deepest bracket. IEEE 80 doesn't
    # publish coefficients for very deep grids; in practice anything
    # beyond ~h/√A = 1/6 is unusual for substation design.
    return coeffs_at(2)


def schwarz_grid_resistance(
    rho_soil_ohm_m: float,
    *,
    grid_total_length_m: float,
    grid_area_m2: float,
    grid_length_m: float,
    grid_width_m: float,
    burial_depth_m: float,
    conductor_diameter_m: float,
    n_rods: int = 0,
    rod_length_m: float = 0.0,
    rod_diameter_m: float = 0.016,
) -> dict:
    """Schwarz combined grid+rod resistance, homogeneous soil.

    Returns a dict with the three intermediate resistances
    (``R_grid``, ``R_rods``, ``R_mutual``) and the combined parallel
    answer ``R_g`` (Ω). When ``n_rods == 0`` or ``rod_length_m <= 0``,
    falls back to the grid-only Schwarz term — useful for benchmarking
    against Sverak (the two agree to ~5% in the validity window of
    Sverak; Schwarz extends usefully beyond it).

    Schwarz formulas (IEEE 80 §14.5):

        R₁ = (ρ / (π·L_c)) · [ln(2·L_c/a') + k₁·L_c/√A − k₂]
        R₂ = (ρ / (2π·n_R·L_R)) · [ln(8·L_R/d_R) − 1
                                   + 2·k₁·L_R·(√n_R − 1)² / √A]
        R_m = (ρ / (π·L_c)) · [ln(2·L_c/L_R) + k₁·L_c/√A − k₂ + 1]
        R_g = (R₁·R₂ − R_m²) / (R₁ + R₂ − 2·R_m)

    where a' = √(2·a·h) for a buried grid, with a = conductor radius.
    """
    if rho_soil_ohm_m <= 0:
        raise ValueError("rho_soil_ohm_m must be positive")
    if grid_total_length_m <= 0 or grid_area_m2 <= 0:
        raise ValueError("grid total length and area must be positive")
    if conductor_diameter_m <= 0:
        raise ValueError("conductor_diameter_m must be positive")
    if burial_depth_m <= 0:
        raise ValueError("burial_depth_m must be positive")

    L_c = grid_total_length_m
    A = grid_area_m2
    sqrt_A = math.sqrt(A)
    a = conductor_diameter_m / 2.0
    a_prime = math.sqrt(2.0 * a * burial_depth_m)

    k1, k2 = schwarz_coefficients(grid_length_m, grid_width_m, burial_depth_m, A)

    # R₁ — grid-only term.
    R1 = (rho_soil_ohm_m / (math.pi * L_c)) * (
        math.log(2.0 * L_c / a_prime) + k1 * L_c / sqrt_A - k2
    )

    if n_rods <= 0 or rod_length_m <= 0:
        return {
            "R_grid": R1,
            "R_rods": float("inf"),
            "R_mutual": 0.0,
            "R_g": R1,
            "k1": k1,
            "k2": k2,
        }

    if rod_diameter_m <= 0:
        raise ValueError("rod_diameter_m must be positive when rods present")

    L_R = rod_length_m
    n_R = n_rods

    # R₂ — rods-only term. The (√n − 1)² factor captures the mutual
    # coupling between rods (single rod when n = 1 → factor is 0).
    R2 = (rho_soil_ohm_m / (2.0 * math.pi * n_R * L_R)) * (
        math.log(8.0 * L_R / rod_diameter_m) - 1.0
        + 2.0 * k1 * L_R * (math.sqrt(n_R) - 1.0) ** 2 / sqrt_A
    )

    # R_m — mutual resistance grid ↔ rods.
    R_m = (rho_soil_ohm_m / (math.pi * L_c)) * (
        math.log(2.0 * L_c / L_R) + k1 * L_c / sqrt_A - k2 + 1.0
    )

    denom = R1 + R2 - 2.0 * R_m
    if denom <= 0:
        # Shouldn't happen for valid inputs — Schwarz produces real
        # positive denominators in the practical envelope. Fall back
        # to series-parallel of the two terms ignoring mutual.
        Rg = (R1 * R2) / (R1 + R2)
    else:
        Rg = (R1 * R2 - R_m * R_m) / denom

    return {
        "R_grid": R1,
        "R_rods": R2,
        "R_mutual": R_m,
        "R_g": Rg,
        "k1": k1,
        "k2": k2,
    }


def two_layer_effective_resistivity(
    rho_1_ohm_m: float,
    rho_2_ohm_m: float,
    h_layer1_m: float,
    grid_area_m2: float,
) -> float:
    """Equivalent uniform ρ for a buried grid in 2-layer soil — Ω·m.

    Tagg/Burgsdorf simplified form (IEEE 80 §14.4 / EPRI TR-100622):

        ρ_a = ρ₁ · ρ₂ · (h₁ + r) / (ρ₂·h₁ + ρ₁·r)

    where r = √(A/π) is the equivalent grid radius. Limits behave
    correctly:

      - h₁ → ∞ (deep top layer)  →  ρ_a → ρ₁
      - h₁ → 0 (bottom-only soil) →  ρ_a → ρ₂
      - ρ₁ = ρ₂                   →  ρ_a = ρ₁

    Use this as the input to Sverak / Schwarz when the soil is layered.
    Accuracy is typically within ~10% of full integral methods for
    h₁ / r ≲ 1; outside that range, prefer a numerical Green's-function
    code for safety-critical work.
    """
    if rho_1_ohm_m <= 0 or rho_2_ohm_m <= 0:
        raise ValueError("layer resistivities must be positive")
    if h_layer1_m < 0:
        raise ValueError("h_layer1_m must be ≥ 0")
    if grid_area_m2 <= 0:
        raise ValueError("grid_area_m2 must be positive")

    r = math.sqrt(grid_area_m2 / math.pi)
    if h_layer1_m == 0:
        return rho_2_ohm_m
    denom = rho_2_ohm_m * h_layer1_m + rho_1_ohm_m * r
    if denom <= 0:
        return rho_1_ohm_m
    return rho_1_ohm_m * rho_2_ohm_m * (h_layer1_m + r) / denom
