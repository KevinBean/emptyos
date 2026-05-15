"""Sheath loss factor λ1 — IEC 60287-1-1 §4.3.

Phase A scope:
  - single-point bonded / cross-bonded → only eddy-current (λ1'')
    component, circulating-current λ1' = 0
  - solidly bonded trefoil / flat → λ1' (circulating) using the
    simple 60287 closed form
  - 3-core common sheath → λ1 = 0 (no induced loop)

Armour losses λ2 deferred to Phase B.
"""

from __future__ import annotations

import math


def mutual_inductance_trefoil(spacing_m: float, sheath_mean_radius_m: float) -> float:
    """X = 2ω · 10⁻⁷ · ln(2s / d_s'), Ω/m at 50 Hz. IEC 60287-1-1 eq. (44).

    Returned as reactance in Ω/m; caller multiplies by frequency ratio
    if not 50 Hz.
    """
    # equation gives reactance directly at the supplied frequency via
    # caller — this returns the geometry-dependent inductance L (H/m)
    return 2e-7 * math.log(2 * spacing_m / (2 * sheath_mean_radius_m))


def sheath_resistance_per_metre(
    sheath_csa_mm2: float,
    material: str = "Cu",
    temperature_c: float = 70.0,
) -> float:
    """DC sheath resistance at operating temperature, Ω/m."""
    rho_20 = {"Cu": 1.7241e-8, "Al": 2.8264e-8, "Pb": 21.4e-8, "Steel": 13.8e-8}.get(
        material, 1.7241e-8
    )
    alpha = {"Cu": 3.93e-3, "Al": 4.03e-3, "Pb": 4.0e-3, "Steel": 4.5e-3}.get(material, 3.93e-3)
    a_m2 = sheath_csa_mm2 * 1e-6
    return (rho_20 / a_m2) * (1 + alpha * (temperature_c - 20.0))


def lambda1_solidly_bonded_trefoil(
    sheath_resistance_per_m: float,
    sheath_mean_radius_m: float,
    centre_spacing_m: float,
    frequency_hz: float = 50.0,
) -> float:
    """λ1' for solidly bonded 3 single-core in trefoil, IEC 60287-1-1 eq. (43).

    λ1' = (R_s / R_c) · 1 / (1 + (R_s / X)²)

    where X = 2ωL is sheath reactance per metre. Caller is responsible
    for multiplying by R_s/R_c — this function returns λ1' as a
    multiplier on conductor losses, with R_c provided.
    """
    omega = 2 * math.pi * frequency_hz
    L = 2e-7 * math.log(2 * centre_spacing_m / (2 * sheath_mean_radius_m))
    X = omega * L
    if X <= 0 or sheath_resistance_per_m <= 0:
        return 0.0
    return 1.0 / (1.0 + (sheath_resistance_per_m / X) ** 2)


def lambda1_solidly_bonded_flat(
    sheath_resistance_per_m: float,
    sheath_mean_radius_m: float,
    centre_spacing_m: float,
    frequency_hz: float = 50.0,
) -> dict:
    """Per-cable λ1' for solidly bonded 3 single-core in flat formation.

    IEC 60287-1-1:2014 §2.3.5, equations (8), (9), (10):

        X  = 2ω · 10⁻⁷ · ln(2s / d_s)        (self-reactance)
        Xm = 2ω · 10⁻⁷ · ln(2)                (mutual between outers)
        CP = X + Xm
        CQ = X - Xm/3
        λ1_m   = (Rs/R) · CQ² / (Rs²+CQ²)                       — middle
        λ1_11  = (Rs/R) · [0.75·CP²/(Rs²+CP²) + 0.25·CQ²/(Rs²+CQ²)
                + 2·Rs·CP·CQ·Xm / (√3 · (Rs²+CP²) · (Rs²+CQ²))]   — outer lagging
        λ1_12  = (Rs/R) · [0.75·CP²/(Rs²+CP²) + 0.25·CQ²/(Rs²+CQ²)
                − 2·Rs·CP·CQ·Xm / (√3 · (Rs²+CP²) · (Rs²+CQ²))]   — outer leading

    The √3 in the cross term is a coefficient — NOT under a square root over
    the entire denominator product. Common error.

    Returns dict with all three per-cable values + worst-case (lagging).
    """
    omega = 2 * math.pi * frequency_hz
    L = 2e-7 * math.log(2 * centre_spacing_m / (2 * sheath_mean_radius_m))
    X = omega * L
    Xm = omega * 2e-7 * math.log(2)

    if X <= 0 or sheath_resistance_per_m <= 0:
        return {"lambda1_m": 0.0, "lambda1_11": 0.0, "lambda1_12": 0.0,
                "lambda1": 0.0, "X": X, "Xm": Xm}

    Rs = sheath_resistance_per_m
    CP = X + Xm
    CQ = X - Xm / 3.0
    # Returns base values WITHOUT Rs/R_ac scaling (caller multiplies),
    # matching `lambda1_solidly_bonded_trefoil`'s convention.
    base_m = CQ**2 / (Rs**2 + CQ**2)
    term_CP = 0.75 * CP**2 / (Rs**2 + CP**2)
    term_CQ = 0.25 * CQ**2 / (Rs**2 + CQ**2)
    cross = 2 * Rs * CP * CQ * Xm / (
        math.sqrt(3) * (Rs**2 + CP**2) * (Rs**2 + CQ**2)
    )
    base_11 = term_CP + term_CQ + cross
    base_12 = term_CP + term_CQ - cross
    base_worst = max(base_m, base_11, base_12)

    return {
        "X": X, "Xm": Xm, "CP": CP, "CQ": CQ,
        "lambda1_m": base_m,
        "lambda1_11": base_11,
        "lambda1_12": base_12,
        "lambda1": base_worst,
    }


def lambda1_eddy_trefoil(
    sheath_resistance_per_m: float,
    sheath_mean_diameter_m: float,
    sheath_thickness_m: float,
    centre_spacing_m: float,
    conductor_resistance_per_m: float,
    frequency_hz: float = 50.0,
) -> float:
    """Eddy-current loss factor λ1'' for 3 single-core in trefoil.

    IEC 60287-1-1:2023 §2.3.6.1 (tubular metallic layer, single-circuit):

        m       = ω/R_s · 1e-7
        r       = d_mean / (2·s)
        λ₀      = 3 · m²/(1+m²) · r²
        δ₁      = (1.14·m^2.45 + 0.33) · r^(0.92m + 1.66)
        β₁      = √(4π·ω / (1e7·ρ_s,θ))         (sheath material skin-depth)
        D_o     = d_mean + t_s                     (sheath outer diameter)
        gs      = 1 + (t_s/D_o)^1.74 · (β₁·D_o − 1.6)   [D_o in metres → ×1e-3 if mm]
        λ₁''    = (R_s/R_ac) · [gs · λ₀ · (1 + δ₁) + (β₁·t_s)⁴ / (12·1e12)]

    Returns λ₁'' as the full IEC factor (already includes R_s/R_ac scaling).
    Cross-checked against vault `cable-current-rating/src/sheath_losses.py`
    `tubular_eddy_current_loss`. TB 880 Case 0-SPB (single-point bonded) →
    λ₁'' ≈ 0.077.
    """
    if sheath_resistance_per_m <= 0 or sheath_thickness_m <= 0:
        return 0.0
    if sheath_mean_diameter_m <= 0 or centre_spacing_m <= 0:
        return 0.0
    if conductor_resistance_per_m <= 0:
        return 0.0

    omega = 2 * math.pi * frequency_hz
    Rs = sheath_resistance_per_m
    d_mean_mm = sheath_mean_diameter_m * 1000.0
    t_mm = sheath_thickness_m * 1000.0
    s_mm = centre_spacing_m * 1000.0
    D_o_mm = d_mean_mm + t_mm

    m = omega / Rs * 1e-7
    r = d_mean_mm / (2.0 * s_mm)
    if r <= 0 or m <= 0:
        return 0.0

    lambda_0 = 3.0 * (m * m / (1 + m * m)) * r * r
    delta_1 = (1.14 * m**2.45 + 0.33) * r ** (0.92 * m + 1.66)

    # β₁ from sheath material resistivity at operating temperature.
    # Recover ρ_s,θ from R_s (R_s = ρ / A, A ≈ π·d·t in m²).
    A = math.pi * sheath_mean_diameter_m * sheath_thickness_m
    if A <= 0:
        return 0.0
    rho_s_theta = Rs * A
    beta_1 = math.sqrt(4 * math.pi * omega / (1e7 * rho_s_theta))

    # gs uses D_o in metres
    gs = 1.0 + (t_mm / D_o_mm) ** 1.74 * (beta_1 * D_o_mm * 1e-3 - 1.6)

    term1 = gs * lambda_0 * (1.0 + delta_1)
    term2 = (beta_1 * t_mm) ** 4 / (12.0 * 1e12)
    return (Rs / conductor_resistance_per_m) * (term1 + term2)


def f_factor_solid_bonding(
    sheath_resistance_per_m: float,
    sheath_mean_radius_m: float,
    centre_spacing_m: float,
    frequency_hz: float = 50.0,
) -> float:
    """F-factor for solid-bonding eddy reduction — CIGRE GP 31.

    F = 1 / (1 + (X_s/R_s)²)

    where X_s = ω · 2e-7 · ln(2s / d_s) is the per-metre sheath reactance.
    Caller multiplies λ1'' by F when bonding is solid.
    """
    if sheath_resistance_per_m <= 0 or sheath_mean_radius_m <= 0:
        return 1.0
    omega = 2 * math.pi * frequency_hz
    L = 2e-7 * math.log(2 * centre_spacing_m / (2 * sheath_mean_radius_m))
    X_s = omega * L
    if X_s <= 0:
        return 1.0
    return 1.0 / (1.0 + (X_s / sheath_resistance_per_m) ** 2)


def compute_lambda1(
    bonding: str,
    sheath_resistance_per_m: float,
    conductor_resistance_per_m: float,
    sheath_mean_radius_m: float | None = None,
    centre_spacing_m: float | None = None,
    frequency_hz: float = 50.0,
    sheath_thickness_m: float | None = None,
    include_eddy_for_solid_bonding: bool = True,
    formation: str = "trefoil",
    n_cores: int = 1,
) -> float:
    """Aggregate λ1 = λ1' + λ1'' for the given bonding.

    Per CIGRE GP 6, eddy currents (λ1'') are always included for
    single-point / cross-bonding, and (when `include_eddy_for_solid_bonding`
    is True, the recommended modern default) also for solid bonding —
    reduced by the GP 31 F-factor.

    Set `include_eddy_for_solid_bonding=False` to reproduce the
    IEC-strict baseline that neglects the eddy term for solid bonding
    with round-stranded Cu/Al conductors (CIGRE TB 880 Case 0).

    ``n_cores`` is the cable's number of conductors sharing one sheath.
    A 3-core common-sheath cable has no net induced loop in the sheath
    (three balanced currents sum to zero), so λ1 = 0 regardless of
    bonding — IEC 60287-1-1 §4.3 / sheath_losses module docstring.
    Set explicitly per cable; single-core trefoil keeps the default 1.
    """
    if n_cores >= 3:
        return 0.0
    eddy = 0.0
    if (
        sheath_mean_radius_m is not None
        and centre_spacing_m is not None
        and sheath_thickness_m is not None
    ):
        eddy = lambda1_eddy_trefoil(
            sheath_resistance_per_m,
            2 * sheath_mean_radius_m,
            sheath_thickness_m,
            centre_spacing_m,
            conductor_resistance_per_m,
            frequency_hz,
        )

    if bonding in ("single_point", "cross_bonded"):
        return eddy

    if bonding == "solidly_bonded":
        if sheath_mean_radius_m is None or centre_spacing_m is None:
            return 0.0
        if formation == "flat":
            flat = lambda1_solidly_bonded_flat(
                sheath_resistance_per_m, sheath_mean_radius_m, centre_spacing_m, frequency_hz
            )
            circ_base = flat["lambda1"]  # worst-case (outer lagging)
        else:
            circ_base = lambda1_solidly_bonded_trefoil(
                sheath_resistance_per_m, sheath_mean_radius_m, centre_spacing_m, frequency_hz
            )
        circ = (sheath_resistance_per_m / conductor_resistance_per_m) * circ_base
        if not include_eddy_for_solid_bonding:
            return circ
        F = f_factor_solid_bonding(
            sheath_resistance_per_m, sheath_mean_radius_m, centre_spacing_m, frequency_hz
        )
        return circ + F * eddy

    return 0.0
