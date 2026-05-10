"""External thermal resistance T4 — IEC 60287-2-1, 60287-2-2.

Phase A:
  direct_buried_single   — single isolated cable buried in soil
  direct_buried_group    — touching/spaced groups (simple superposition)
  in_duct                — cable in non-metallic duct, duct in soil
  in_air                 — cable in still air (Rayleigh / forced convection)

Pipe-type and riser deferred to Phase B.
"""

from __future__ import annotations

import math


# --- Direct buried, single isolated cable -------------------------------

def t4_direct_buried_single(
    rho_t_soil: float,
    burial_depth_m: float,
    cable_outer_diameter_m: float,
) -> float:
    """T4 for an isolated buried cable — IEC 60287-2-1 eq. (16).

    T4 = ρ_T / (2π) · ln(2L/D_e + sqrt((2L/D_e)² - 1))
       ≈ ρ_T / (2π) · ln(4L/D_e)   for L/D_e >> 1
    """
    if cable_outer_diameter_m <= 0 or rho_t_soil <= 0:
        return 0.0
    u = 2 * burial_depth_m / cable_outer_diameter_m
    # full form (more accurate near surface)
    return (rho_t_soil / (2 * math.pi)) * math.log(u + math.sqrt(u * u - 1))


# --- Direct buried, group of identical cables --------------------------

def t4_direct_buried_group(
    rho_t_soil: float,
    burial_depth_m: float,
    cable_outer_diameter_m: float,
    spacings_m: list[float],
    image_spacings_m: list[float] | None = None,
) -> float:
    """Group of N identical cables — IEC 60287-2-1 eq. (19).

    T4 = ρ_T / (2π) · [ ln(2L/D_e) + Σ ln(d'_pk / d_pk) ]

    spacings_m       — distances from the rated cable to its neighbours d_pk
    image_spacings_m — distances to neighbours' images d'_pk; if None,
                       computed assuming all cables at the same depth L:
                         d'_pk = sqrt((2L)² + d_pk²)        (single-row)
                       which is the equal-depth approximation.
    """
    base = (rho_t_soil / (2 * math.pi)) * math.log(2 * burial_depth_m / cable_outer_diameter_m)
    if not spacings_m:
        return base
    if image_spacings_m is None:
        image_spacings_m = [math.sqrt((2 * burial_depth_m) ** 2 + d * d) for d in spacings_m]
    extra = 0.0
    for d, dp in zip(spacings_m, image_spacings_m):
        if d > 0 and dp > 0:
            extra += math.log(dp / d)
    return base + (rho_t_soil / (2 * math.pi)) * extra


# --- Direct buried, three single-core cables in trefoil ----------------

def t4_direct_buried_trefoil(
    rho_t_soil: float,
    burial_depth_m: float,
    cable_outer_diameter_m: float,
    axial_spacing_m: float | None = None,
) -> float:
    """T4 for three single-core cables in trefoil formation.

    Two cases per IEC 60287-2-1 §4.2.4:

    **Touching trefoil** (axial_spacing_m is None or equals D_e):
    closed form §4.2.4.3.2 absorbs mutual heating into a constant —

        T4 = (1.5/π) · ρT · [ ln(2u) − 0.630 ]      with u = 2L/D_e

    This is the formula CIGRE TB 880 uses (Case 0: T4 = 1.595 K·m/W)
    and is exact for L/D_e ≳ 5; it removes the need for per-cable image
    superposition.

    **Spaced trefoil** (axial_spacing_m > D_e): falls back to
    Kennelly superposition with rated cable taken as one of the two
    bottom (deeper, hotter) cables. Geometry: equilateral triangle of
    side s, centroid at depth L; top cable at L − s/√3, bottom cables
    at L + s/(2√3) separated horizontally by s.
    """
    if cable_outer_diameter_m <= 0 or rho_t_soil <= 0:
        return 0.0
    if burial_depth_m <= cable_outer_diameter_m / 2.0:
        return 0.0

    s = axial_spacing_m if axial_spacing_m is not None else cable_outer_diameter_m

    # Touching trefoil — IEC §4.2.4.3.2 closed form.
    if abs(s - cable_outer_diameter_m) < 1e-9:
        u = 2.0 * burial_depth_m / cable_outer_diameter_m
        if u <= 1.0:
            return 0.0
        return (1.5 / math.pi) * rho_t_soil * (math.log(2.0 * u) - 0.630)

    # Spaced trefoil — Kennelly superposition on the bottom cable.
    sqrt3 = math.sqrt(3.0)
    L_top = burial_depth_m - s / sqrt3
    L_bot = burial_depth_m + s / (2.0 * sqrt3)
    if L_bot <= 0:
        return 0.0
    u_bot = 2.0 * L_bot / cable_outer_diameter_m
    if u_bot < 1.0:
        return 0.0
    self_term = math.log(u_bot + math.sqrt(u_bot * u_bot - 1.0))
    image_top_sq = (s / 2.0) ** 2 + (L_bot + L_top) ** 2
    mutual_top = 0.5 * math.log(image_top_sq / (s * s))
    image_other_sq = s * s + (2.0 * L_bot) ** 2
    mutual_other = 0.5 * math.log(image_other_sq / (s * s))
    return (rho_t_soil / (2.0 * math.pi)) * (self_term + mutual_top + mutual_other)


# --- In duct (single duct in soil) -------------------------------------

# IEC 60287-2-1:2015 Table 1 — (U, V, Y) constants for T'4.
# Keys are the duct_material literal on AmpacityInput / CableRecord.
DUCT_CONSTANTS: dict[str, tuple[float, float, float]] = {
    "Steel":         (5.2, 1.4,  0.011),    # metallic conduit
    "Earthenware":   (1.87, 0.28, 0.0036),
    "FibreCement":   (5.2, 1.2,  0.006),
    "PVC":           (1.87, 0.312, 0.0037),  # plastic duct
    "HDPE":          (1.87, 0.312, 0.0037),  # plastic duct
    "Concrete":      (1.87, 0.312, 0.0037),  # treated as plastic for T'4 (concrete corrections handled separately)
    "Other":         (5.2, 1.4,  0.011),    # conservative metallic-conduit default
}


def duct_constants_for(material: str | None) -> tuple[float, float, float]:
    """Look up IEC 60287-2-1 Table 1 (U, V, Y) for a duct material.

    Falls back to the metallic-conduit conservative default when the
    material is unknown / None.
    """
    if material is None:
        return (5.2, 1.4, 0.011)
    return DUCT_CONSTANTS.get(material, (5.2, 1.4, 0.011))


def t4_prime_in_duct(
    cable_outer_diameter_m: float,
    mean_temp_c: float,
    duct_constants: tuple[float, float, float] = (5.2, 1.4, 0.011),
) -> float:
    """T'4 — thermal resistance between cable and duct internal surface.

    IEC 60287-2-1 eq. (22):
        T'4 = U / (1 + 0.1·(V + Y·θ_m)·D_e)

    where **D_e is in millimetres** (the IEC formula's empirical constants
    bake the unit in — passing D_e in metres gives a value ~5× too high).

    Constants (U, V, Y) depend on duct material and mounting — see
    DUCT_CONSTANTS / duct_constants_for(). Default (5.2, 1.4, 0.011) is
    the metallic-conduit row, kept as a conservative fallback.
    """
    U, V, Y = duct_constants
    d_e_mm = cable_outer_diameter_m * 1000.0
    return U / (1 + 0.1 * (V + Y * mean_temp_c) * d_e_mm)


def t4_external_ducts_trefoil_nonmetallic(
    rho_t_soil: float,
    burial_depth_m: float,
    duct_external_diameter_m: float,
) -> float:
    """T'''4 for three non-metallic ducts touching in trefoil — IEC §4.2.4.3.4.

        T'''4 = (ρ_T / 2π) · [ ln(2u) + 2·ln(u) ]   with u = 2L/D_o

    Self term + two mutual terms. The non-metallic form (cables in
    plastic ducts) differs from the metallic-sheath touching-trefoil
    formula §4.2.4.3.2; use that one for direct-buried bare cables, this
    one for cables-in-ducts where the duct is non-metallic.

    Reference value (CIGRE TB 880 Case 0-2, ρ=1, L=1000 mm, D_o=140 mm):
    u=14.286 → T'''4 = 1.38002 K·m/W.
    """
    if duct_external_diameter_m <= 0 or rho_t_soil <= 0 or burial_depth_m <= 0:
        return 0.0
    u = 2.0 * burial_depth_m / duct_external_diameter_m
    if u <= 1.0:
        return 0.0
    return (rho_t_soil / (2.0 * math.pi)) * (math.log(2.0 * u) + 2.0 * math.log(u))


def t4_double_prime_duct_wall(
    rho_t_duct: float,
    duct_internal_diameter_m: float,
    duct_external_diameter_m: float,
) -> float:
    """T''4 — through the duct wall. IEC 60287-2-1 eq. (23)."""
    if duct_external_diameter_m <= duct_internal_diameter_m:
        return 0.0
    return (rho_t_duct / (2 * math.pi)) * math.log(
        duct_external_diameter_m / duct_internal_diameter_m
    )


def t4_in_duct(
    rho_t_soil: float,
    rho_t_duct: float,
    burial_depth_m: float,
    cable_outer_diameter_m: float,
    duct_internal_diameter_m: float,
    duct_external_diameter_m: float,
    mean_temp_c: float = 50.0,
    duct_constants: tuple[float, float, float] = (5.2, 1.4, 0.011),
    grouped_cables: int = 1,
    spacing_mode: str = "trefoil",
    axial_spacing_m: float | None = None,
) -> float:
    """T4 (cable in duct) = T'4 + T''4 + T'''4   — IEC 60287-2-1 §2.2.7.

    T'''4 is the external thermal resistance from the duct outer surface
    through the soil. For a single isolated duct it's the buried-cable
    T4 with the duct OD. For three ducts in trefoil (`grouped_cables=3`,
    `spacing_mode='trefoil'`), routes to `t4_direct_buried_trefoil` with
    the duct OD as the "cable" diameter — IEC 60287-2-1 §4.2.3.3.
    """
    t4p = t4_prime_in_duct(cable_outer_diameter_m, mean_temp_c, duct_constants)
    t4pp = t4_double_prime_duct_wall(rho_t_duct, duct_internal_diameter_m, duct_external_diameter_m)
    t4ppp = _t4_external_ducts(
        rho_t_soil, burial_depth_m, duct_external_diameter_m,
        grouped_cables, spacing_mode, axial_spacing_m,
    )
    return t4p + t4pp + t4ppp


def _t4_external_ducts(
    rho_t_soil: float,
    burial_depth_m: float,
    duct_external_diameter_m: float,
    grouped_cables: int,
    spacing_mode: str,
    axial_spacing_m: float | None,
) -> float:
    """Resolve T'''4 from formation. Used by both `t4_in_duct` and the
    iteration helper `t4_in_duct_components`.

    - Trefoil touching (3 ducts): IEC §4.2.4.3.4 non-metallic formula
      (ρ/2π × [ln(2u) + 2·ln(u)]).
    - Flat / spaced ducts: single-isolated-duct formula per IEC convention.
      Mutual heating between ducts is handled by the Donazzi backfill
      correction at the bank-equivalent-radius level (§4.2.7.4) when a
      backfill is declared. Kennelly superposition over individual ducts
      double-counts the mutual term and inflates T'''4. Cross-checked
      against vault `cable-current-rating/src/thermal.py` (TB 880 Case 0-3).
    """
    if grouped_cables == 3 and spacing_mode == "trefoil":
        return t4_external_ducts_trefoil_nonmetallic(
            rho_t_soil, burial_depth_m, duct_external_diameter_m
        )
    return t4_direct_buried_single(rho_t_soil, burial_depth_m, duct_external_diameter_m)


def t4_in_duct_components(
    rho_t_soil: float,
    rho_t_duct: float,
    burial_depth_m: float,
    cable_outer_diameter_m: float,
    duct_internal_diameter_m: float,
    duct_external_diameter_m: float,
    duct_constants: tuple[float, float, float] = (5.2, 1.4, 0.011),
    grouped_cables: int = 1,
    spacing_mode: str = "trefoil",
    axial_spacing_m: float | None = None,
) -> tuple[float, float, tuple[float, float, float], float]:
    """Return (T''4, T'''4, (U,V,Y), D_e) — the θ_m-independent pieces of
    T4-in-duct plus the constants needed to evaluate T'4 at any θ_m.

    Used by core.py to iterate θ_m self-consistently with cable losses.
    """
    t4pp = t4_double_prime_duct_wall(rho_t_duct, duct_internal_diameter_m, duct_external_diameter_m)
    t4ppp = _t4_external_ducts(
        rho_t_soil, burial_depth_m, duct_external_diameter_m,
        grouped_cables, spacing_mode, axial_spacing_m,
    )
    return t4pp, t4ppp, duct_constants, cable_outer_diameter_m


# --- Concrete duct bank / controlled backfill (Donazzi) ---------------

def duct_bank_equivalent_radius_m(width_m: float, height_m: float) -> float:
    """Equivalent radius of a rectangular duct bank — IEC 60287-2-1 §4.2.7.4.

        ln(r_b/x*) = (1/2)·(x/y)·(4/π − x/y)·ln(1 + y²/x²)   with x* = x/2

    where x = shorter side, y = longer side. Valid for y/x < 3.

    Reference (CIGRE TB 880 Case 0-3): width=655 mm, height=325 mm →
    r_b = 222.14 mm.
    """
    if width_m <= 0 or height_m <= 0:
        return 0.0
    x = min(width_m, height_m)
    y = max(width_m, height_m)
    ratio = x / y
    exponent = 0.5 * ratio * (4.0 / math.pi - ratio) * math.log(1.0 + (y / x) ** 2)
    return (x / 2.0) * math.exp(exponent)


def backfill_correction_kmw(
    burial_depth_m: float,
    width_m: float,
    height_m: float,
    rho_t_soil: float,
    rho_t_backfill: float,
    n_cables: int = 1,
) -> float:
    """Donazzi correction for a duct bank embedded in concrete / CBS — IEC §4.2.7.4.

        ΔT4 = (N / 2π) · (ρ_soil − ρ_backfill) · ln(u_b + √(u_b² − 1))

    with u_b = L / r_b. Positive when ρ_soil > ρ_backfill (standard case:
    concrete is more conductive than native soil → adds resistance because
    we replaced backfill in the T'''4 formula with the lower ρ). Caller
    must compute T'''4 using ρ_backfill, then ADD this correction.

    Reference (CIGRE TB 880 Case 0-3): ΔT4 = 0.2087 K·m/W.
    """
    r_b = duct_bank_equivalent_radius_m(width_m, height_m)
    if r_b <= 0:
        return 0.0
    u_b = burial_depth_m / r_b
    if u_b <= 1.0:
        return 0.0
    return (n_cables / (2.0 * math.pi)) * (rho_t_soil - rho_t_backfill) * math.log(
        u_b + math.sqrt(u_b * u_b - 1.0)
    )


# --- In air (still air, IEC 60287-2-2) ---------------------------------

def t4_in_air(
    cable_outer_diameter_m: float,
    delta_theta_k: float,
    z: float = 0.21,
    e: float = 3.94,
    g: float = 0.60,
) -> float:
    """T4 in still air — IEC 60287-2-2 eq. (1).

    Per the standard, h = (Z / D_e^g + E) · Δθ^(1/4)   [W/m²·K^(5/4)]
    T4 = 1 / (π · D_e · h)

    Z, E, g are mounting-dependent constants (IEC 60287-2-2 Table 2):
    defaults shown are for a single isolated cable in free air.

    delta_theta_k is the surface-to-ambient temperature rise; the
    iterative outer loop in core.py supplies it.
    """
    if delta_theta_k <= 0 or cable_outer_diameter_m <= 0:
        return float("inf")
    h = (z / (cable_outer_diameter_m ** g) + e) * (delta_theta_k ** 0.25)
    return 1.0 / (math.pi * cable_outer_diameter_m * h)
