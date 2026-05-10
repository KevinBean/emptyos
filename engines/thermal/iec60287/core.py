"""IEC 60287 forward + inverse orchestration — Phase A.

Forward (compute_ampacity):
    Given installation, ambient, conductor max temperature → I [A]

Inverse (compute_conductor_temperature):
    Given installation + current → conductor temperature [°C]

The forward case for ampacity is the classic IEC formula:

    I² = (Δθ - W_d · [0.5·T1 + n·(T2+T3+T4)]) /
         (R · [T1 + n·(1+λ1)·T2 + n·(1+λ1+λ2)·(T3+T4)])

For single-core 3-cable installations, n = 1 per cable (each path
through the thermal circuit is a single cable). The standard's `n`
becomes >1 only for 3-core common-sheath cables.

In-air installations need an outer iteration on Δθ_surface (T4
depends on the surface-to-air rise). Phase A iterates a fixed-point
loop to convergence; falls back to a buried-equivalent T4 if the
iteration diverges.
"""

from __future__ import annotations

import math

from engines.models import AmpacityInput, AmpacityResult, CableLibraryEntry, CableGeometry

from . import conductor_losses, dielectric_losses, mutual_heating, sheath_losses, thermal_resistances
from . import installation_types as inst


# Default thermal resistivities (K·m/W) — IEC 60287-2-1 Table 1.
RHO_T_INSULATION = {
    "XLPE": 3.5,
    "EPR": 3.5,
    "PVC": 6.0,
    "Paper": 6.0,
    "PILC": 6.0,
    "Other": 5.0,
}
RHO_T_JACKET_DEFAULT = 3.5  # PE jacket
RHO_T_DUCT_DEFAULT = 4.8    # PVC duct (legacy fallback)

# Duct-wall thermal resistivity by material (K·m/W).
RHO_T_DUCT = {
    "PVC": 6.0,
    "HDPE": 3.5,
    "Steel": 0.05,
    "Concrete": 1.0,
    "Earthenware": 1.2,
    "FibreCement": 2.0,
    "Other": 4.8,
}


def _default_geometry(cable: CableLibraryEntry) -> CableGeometry:
    """Synthesize a plausible geometry from CSA + insulation defaults.

    Used for smoke-tests when a library entry doesn't carry geometry.
    Real production use must supply geometry on the library entry.
    """
    a_mm2 = cable.conductor_csa_mm2
    # rough conductor diameter ≈ sqrt(4·A/π), inflated 5% for stranding
    d_c = 1.05 * math.sqrt(4 * a_mm2 / math.pi) * 1e-3
    # crude insulation thickness scaling with voltage (mm per kV)
    t_i = max(2.0, 0.7 * (cable.rated_voltage_kv or 11.0)) * 1e-3
    sheath_t = 0.8e-3
    jacket_t = 2.5e-3
    d_e = d_c + 2 * (t_i + sheath_t + jacket_t)
    return CableGeometry(
        conductor_diameter=d_c,
        insulation_thickness=t_i,
        sheath_thickness=sheath_t,
        sheath_inner_diameter=d_c + 2 * t_i,
        overall_diameter=d_e,
    )


def _spacing(input: AmpacityInput, d_e: float) -> float:
    """Centre-to-centre spacing between adjacent cables.

    For ducted installations the cables follow their ducts, so the
    cable-to-cable distance equals the duct centre-to-centre spacing —
    not the cable OD. Touching ducts in trefoil → spacing = duct OD.
    Used by sheath-loss and proximity-effect calcs that depend on s.
    """
    if input.installation == "in_duct" and input.spacing_mode in ("trefoil", "touching", "flat"):
        if input.spacing_m is not None:
            return input.spacing_m
        if input.duct_external_diameter_m is not None:
            return input.duct_external_diameter_m
        if input.duct_internal_diameter_m is not None:
            return input.duct_internal_diameter_m * 1.15
        return d_e
    if input.spacing_mode == "touching" or input.spacing_mode == "trefoil":
        return d_e
    if input.spacing_m is not None:
        return input.spacing_m
    return d_e  # fallback


def _t4(input: AmpacityInput, d_e: float, surface_rise_k: float | None,
        mean_duct_temp_c: float | None = None) -> float:
    rho_soil = input.soil_thermal_resistivity_kmw
    if input.installation == "direct_buried":
        if input.spacing_mode == "trefoil" and input.grouped_cables == 3:
            s = _spacing(input, d_e)
            return inst.t4_direct_buried_trefoil(rho_soil, input.burial_depth_m, d_e, s)
        if input.grouped_cables > 1:
            s = _spacing(input, d_e)
            spacings = [s * (k + 1) for k in range(input.grouped_cables - 1)]
            return inst.t4_direct_buried_group(rho_soil, input.burial_depth_m, d_e, spacings)
        return inst.t4_direct_buried_single(rho_soil, input.burial_depth_m, d_e)

    if input.installation == "in_duct":
        d_i = input.duct_internal_diameter_m or (d_e * 1.5)
        d_o = input.duct_external_diameter_m or (d_i * 1.15)
        rho_duct = RHO_T_DUCT.get(input.duct_material, RHO_T_DUCT_DEFAULT) if input.duct_material else RHO_T_DUCT_DEFAULT
        duct_const = inst.duct_constants_for(input.duct_material)
        theta_m = mean_duct_temp_c if mean_duct_temp_c is not None else (
            (input.ambient_temperature_c + input.conductor_max_temp_c) / 2
        )
        # When a controlled backfill (concrete / CBS) is declared, T'''4
        # is computed against the backfill ρ_T, and the Donazzi correction
        # adds the soil-vs-backfill delta beyond the duct-bank boundary.
        rho_ext = (
            input.backfill_thermal_resistivity_kmw
            if input.backfill_thermal_resistivity_kmw is not None
            else rho_soil
        )
        t4 = inst.t4_in_duct(
            rho_ext, rho_duct, input.burial_depth_m, d_e,
            d_i, d_o, mean_temp_c=theta_m, duct_constants=duct_const,
            grouped_cables=input.grouped_cables, spacing_mode=input.spacing_mode,
            axial_spacing_m=input.spacing_m,
        )
        if (
            input.backfill_thermal_resistivity_kmw is not None
            and input.backfill_width_m is not None
            and input.backfill_height_m is not None
        ):
            t4 += inst.backfill_correction_kmw(
                input.burial_depth_m,
                input.backfill_width_m, input.backfill_height_m,
                rho_soil, input.backfill_thermal_resistivity_kmw,
                n_cables=max(1, input.grouped_cables),
            )
        return t4

    if input.installation == "in_air":
        rise = surface_rise_k if surface_rise_k is not None else (
            input.conductor_max_temp_c - input.ambient_temperature_c
        )
        # Explicit override wins (e.g. covered-trough mountings not in Table 2).
        if input.in_air_mounting_zeg is not None:
            z, e, g = input.in_air_mounting_zeg
            return inst.t4_in_air(d_e, rise, z=z, e=e, g=g)
        # IEC 60287-2-2 Table 2 mounting constants. Auto-select based on
        # spacing mode + grouped count; fall back to single-cable defaults.
        if input.spacing_mode == "trefoil" and input.grouped_cables == 3:
            # Group 6: three cables in trefoil, touching, vertical formation.
            return inst.t4_in_air(d_e, rise, z=0.96, e=1.25, g=0.20)
        if input.spacing_mode in ("flat", "touching") and input.grouped_cables == 3:
            # Group 5: three cables in flat formation, touching.
            return inst.t4_in_air(d_e, rise, z=0.62, e=1.95, g=0.25)
        return inst.t4_in_air(d_e, rise)

    # pipe-type / riser — Phase B
    raise NotImplementedError(f"Installation type {input.installation!r} is Phase B")


def _component_losses_and_resistances(
    input: AmpacityInput, conductor_temp_c: float, sheath_temp_c: float | None = None,
) -> dict:
    """Compute T1, T3, R(θ), λ1, W_d for the given conductor temperature.

    If `sheath_temp_c` is None, falls back to a θ_c−10 K guess. Outer
    loops iterate sheath temp from W_c·T1 + ½·W_d·T1 for a self-consistent
    rating (matters for cases with high λ₁ where Rs sensitivity is
    significant — TB 880 case 0-3 in particular).
    """
    cable = input.cable
    geom = cable.geometry or _default_geometry(cable)
    d_c = geom.conductor_diameter
    d_e = geom.overall_diameter
    s = _spacing(input, d_e)

    # AC conductor resistance.
    # If the cable record carries an IEC 60228 R20 datasheet value
    # (Ω/km), back it out to an effective ρ20 so the standard formula
    # (R20 = ρ20 / A) reproduces it exactly. This matters for stranded
    # conductors where datasheet R20 differs from the bare ρ20·A by
    # the stranding factor (~3% for 630 mm² Cu).
    #
    # When no datasheet value is supplied, look up the IEC 60228 max R20
    # for (csa, material, class). Datasheet wins when both are present
    # (datasheet is more accurate than the standard's max).
    rho20_override = None
    r20_source = "rho_a"  # bare ρ20 / A formula
    if cable.conductor_dc_resistance_20c_ohm_per_km is not None:
        r20_per_m = cable.conductor_dc_resistance_20c_ohm_per_km * 1e-3  # Ω/m
        rho20_override = r20_per_m * (cable.conductor_csa_mm2 * 1e-6)
        r20_source = "datasheet"
    else:
        from ..references import iec60228_max_r20
        r20_table = iec60228_max_r20(
            cable.conductor_csa_mm2, cable.conductor_material, cable.conductor_class,
        )
        if r20_table is not None:
            r20_per_m = r20_table * 1e-3  # Ω/km → Ω/m
            rho20_override = r20_per_m * (cable.conductor_csa_mm2 * 1e-6)
            r20_source = "iec60228"
    ac = conductor_losses.ac_resistance_per_metre(
        csa_mm2=cable.conductor_csa_mm2,
        conductor_diameter_m=d_c,
        centre_spacing_m=s,
        material=cable.conductor_material,
        temperature_c=conductor_temp_c,
        frequency_hz=input.frequency_hz,
        rho_20_override=rho20_override,
    )
    R = ac["r_ac"]

    # Dielectric losses
    rho_t_ins = RHO_T_INSULATION.get(cable.insulation_material, 5.0)
    eps_r, tan_d = dielectric_losses.INSULATION_DEFAULTS.get(
        cable.insulation_material, (3.0, 0.005)
    )
    C = dielectric_losses.capacitance_per_metre(
        geom.insulation_inner_diameter, geom.insulation_outer_diameter, eps_r
    )
    U0 = ((cable.rated_voltage_kv or 11.0) * 1e3) / math.sqrt(3)
    Wd = dielectric_losses.dielectric_loss_per_metre(C, U0, input.frequency_hz, tan_d)

    # Sheath losses (λ1)
    if geom.sheath_thickness and geom.sheath_inner_diameter:
        sheath_inner_r = geom.sheath_inner_diameter / 2
        sheath_mean_r = sheath_inner_r + geom.sheath_thickness / 2
        # Approximate sheath CSA: π · d_s_mean · t_s × 1e6 mm²/m²
        sheath_csa = math.pi * 2 * sheath_mean_r * geom.sheath_thickness * 1e6
        # Sheath temperature: θ_s ≈ θ_c − W_c · T1 − ½·W_d·T1. The drop
        # depends on losses, which depend on λ₁(Rs(θ_s)). Outer loops
        # iterate; bootstrap with θ_c − 10 K when none supplied.
        theta_s = sheath_temp_c if sheath_temp_c is not None else conductor_temp_c - 10
        Rs = sheath_losses.sheath_resistance_per_metre(
            sheath_csa, cable.sheath_material if cable.sheath_material != "None" else "Cu",
            temperature_c=theta_s,
        )
        lam1 = (
            input.sheath_loss_factor_lambda1
            if input.sheath_loss_factor_lambda1 is not None
            else sheath_losses.compute_lambda1(
                input.bonding, Rs, R, sheath_mean_r, s, input.frequency_hz,
                sheath_thickness_m=geom.sheath_thickness,
                include_eddy_for_solid_bonding=input.include_eddy_for_solid_bonding,
                formation=("flat" if input.spacing_mode == "flat" else "trefoil"),
            )
        )
        # Per-cable λ₁ for flat solid-bonded — feeds the Δθ_P mutual heating
        # term in the rating equation (one rating per cable, take the min I).
        lambda1_per_cable: dict[str, float] | None = None
        if (
            input.spacing_mode == "flat"
            and input.bonding == "solidly_bonded"
            and input.sheath_loss_factor_lambda1 is None
        ):
            flat_split = sheath_losses.lambda1_solidly_bonded_flat(
                Rs, sheath_mean_r, s, input.frequency_hz,
            )
            ratio = Rs / R
            lambda1_per_cable = {
                "lag": ratio * flat_split["lambda1_11"],
                "mid": ratio * flat_split["lambda1_m"],
                "lead": ratio * flat_split["lambda1_12"],
            }
    else:
        lam1 = 0.0
        lambda1_per_cable = None

    lam2 = input.armour_loss_factor_lambda2 or 0.0

    # Thermal resistances
    T1 = thermal_resistances.t1_single_core(
        rho_t_ins, d_c, geom.insulation_thickness, geom.inner_semicon_thickness
    )
    T2 = thermal_resistances.t2_no_armour()
    # T3 runs from the outer-most metallic layer (armour, else sheath,
    # else outer semicon / insulation) through the jacket to D_e. Per
    # IEC 60287-2-1 §4.1.4.1 the inner reference is D'_a = sheath OD when
    # there is no armour (eq. uses D_s, not D_insulation_outer).
    if geom.armour_thickness and geom.armour_inner_diameter:
        d_inner = geom.armour_inner_diameter + 2 * geom.armour_thickness
    elif geom.sheath_thickness and geom.sheath_inner_diameter:
        d_inner = geom.sheath_inner_diameter + 2 * geom.sheath_thickness
    else:
        d_inner = geom.insulation_outer_diameter
    jacket_t = max(0.0, (geom.overall_diameter - d_inner) / 2)
    T3 = thermal_resistances.t3_jacket(RHO_T_JACKET_DEFAULT, d_inner, jacket_t)
    # IEC 60287-2-1 §4.2.4.3.2: for three single-core cables with metallic
    # sheath in trefoil touching formation, T3 must be multiplied by 1.6.
    # Does NOT apply when cables are in ducts (each cable is jacketed in
    # its own duct, no physical contact between sheaths).
    if (
        input.installation == "direct_buried"
        and input.spacing_mode == "trefoil"
        and input.grouped_cables == 3
        and geom.sheath_thickness
    ):
        T3 *= 1.6

    return {
        "R": R, "Wd": Wd, "lambda1": lam1, "lambda2": lam2,
        "T1": T1, "T2": T2, "T3": T3,
        "geometry": geom, "ac_breakdown": ac,
        "lambda1_per_cable": lambda1_per_cable,
        "r20_source": r20_source,
    }


def _compute_at_temperature(
    input: AmpacityInput, conductor_temp_c: float, surface_rise_k: float | None,
    mean_duct_temp_c: float | None = None,
    sheath_temp_c: float | None = None,
) -> tuple[float, dict]:
    """Run the IEC 60287 forward equation at a given conductor temperature.

    Returns (I, breakdown). `sheath_temp_c` lets outer loops feed back a
    self-consistent sheath temperature; without it, _component_losses uses
    a θ_c−10 K bootstrap (acceptable when λ₁ is small but inaccurate for
    high-λ₁ cases like flat solid-bonded TB 880 0-3).
    """
    parts = _component_losses_and_resistances(input, conductor_temp_c, sheath_temp_c)
    geom = parts["geometry"]
    R, Wd, lam1, lam2 = parts["R"], parts["Wd"], parts["lambda1"], parts["lambda2"]
    T1, T2, T3 = parts["T1"], parts["T2"], parts["T3"]
    T4 = _t4(input, geom.overall_diameter, surface_rise_k, mean_duct_temp_c)

    n = 1  # single-core path through the thermal circuit
    delta_theta = conductor_temp_c - input.ambient_temperature_c
    numerator = delta_theta - Wd * (0.5 * T1 + n * (T2 + T3 + T4))

    # Solar radiation absorbed at the cable surface — IEC 60287-2-2.
    # Only applies to in_air installations; buried/duct cables are
    # shielded from direct insolation.
    if (
        input.installation == "in_air"
        and input.solar_absorption_coefficient > 0.0
        and input.solar_radiation_w_per_m2 > 0.0
    ):
        sigma_H_De = (
            input.solar_absorption_coefficient
            * input.solar_radiation_w_per_m2
            * geom.overall_diameter
        )
        numerator -= sigma_H_De * T4

    # Per-cable rating with mutual heating Δθ_P — IEC 60287-2-1 §4.2.3.2.
    # Triggers for flat 3-cable installations where per-cable λ₁ differs.
    # Each cable's rating uses (a) its own λ₁ in the denominator and (b) a
    # neighbour-induced Δθ_P term in the numerator. Circuit rating = min(I_p).
    per_cable_lams = parts.get("lambda1_per_cable")
    if (
        per_cable_lams
        and input.spacing_mode == "flat"
        and input.grouped_cables == 3
        and input.bonding == "solidly_bonded"
        and input.spacing_m
        and input.burial_depth_m
        and input.installation != "in_air"
    ):
        # Use backfill ρ_T for mutual heating when a controlled backfill is
        # declared (vault TB 880 Case 0-3 convention); else native soil.
        rho_mut = (
            input.backfill_thermal_resistivity_kmw
            if input.backfill_thermal_resistivity_kmw is not None
            else input.soil_thermal_resistivity_kmw
        )
        coeffs = mutual_heating.delta_theta_p_coefficients_flat_3(
            input.spacing_m, input.burial_depth_m, rho_mut, R, per_cable_lams, Wd,
        )
        I_per_cable: dict[str, float] = {}
        for cid, (a_p, b_p) in coeffs.items():
            lam_p = per_cable_lams[cid]
            denom_R_p = R * (T1 + n * (1 + lam_p) * T2 + n * (1 + lam_p + lam2) * (T3 + T4))
            num_p = numerator - b_p
            denom_eff = denom_R_p + a_p
            I_per_cable[cid] = math.sqrt(num_p / denom_eff) if num_p > 0 and denom_eff > 0 else 0.0
        worst_cid = min(I_per_cable, key=lambda k: I_per_cable[k])
        I = I_per_cable[worst_cid]
        # Worst cable's λ₁ flows downstream into sheath-loss reporting + θ_m.
        lam1 = per_cable_lams[worst_cid]
        a_w, b_w = coeffs[worst_cid]
        delta_theta_p = a_w * I * I + b_w
    else:
        denominator = R * (T1 + n * (1 + lam1) * T2 + n * (1 + lam1 + lam2) * (T3 + T4))
        delta_theta_p = 0.0
        I = math.sqrt(numerator / denominator) if denominator > 0 and numerator > 0 else 0.0

    breakdown = {
        "T1": T1, "T2": T2, "T3": T3, "T4": T4,
        "R_ac": R, "R_dc": parts["ac_breakdown"]["r_dc"],
        "y_s": parts["ac_breakdown"]["y_s"], "y_p": parts["ac_breakdown"]["y_p"],
        "W_d": Wd,
        "lambda1": lam1, "lambda2": lam2,
        "I": I,
        "delta_theta": delta_theta,
        "delta_theta_p": delta_theta_p,
        "geometry": geom,
        "r20_source": parts["r20_source"],
    }
    return I, breakdown


def compute_ampacity(input: AmpacityInput) -> AmpacityResult:
    """Forward calc — return continuous current rating at θ_max."""
    notes: list[str] = []
    if input.cable.geometry is None:
        notes.append("Cable geometry synthesized from CSA + voltage defaults.")

    if input.installation == "in_duct":
        # Outer loop: T'4 depends on mean duct temperature θ_m, which
        # depends on the total cable losses W_t, which depend on I (via
        # I²R + sheath losses), which depend on T4. IEC 60287-2-1 §4.2.3:
        #   θ_m = θ_a + W_t · (T''4 + T'''4 + 0.5·T'4)
        # Iterate until convergence.
        cable = input.cable
        geom_seed = cable.geometry or _default_geometry(cable)
        d_e = geom_seed.overall_diameter
        d_i = input.duct_internal_diameter_m or (d_e * 1.5)
        d_o = input.duct_external_diameter_m or (d_i * 1.15)
        rho_duct = RHO_T_DUCT.get(input.duct_material, RHO_T_DUCT_DEFAULT) if input.duct_material else RHO_T_DUCT_DEFAULT
        duct_const = inst.duct_constants_for(input.duct_material)
        # If a controlled backfill is declared, T'''4 uses ρ_backfill +
        # Donazzi corr. Otherwise T'''4 uses native soil ρ_T directly.
        rho_ext = (
            input.backfill_thermal_resistivity_kmw
            if input.backfill_thermal_resistivity_kmw is not None
            else input.soil_thermal_resistivity_kmw
        )
        t4pp, t4ppp, _, _ = inst.t4_in_duct_components(
            rho_ext, rho_duct, input.burial_depth_m,
            d_e, d_i, d_o, duct_const,
            grouped_cables=input.grouped_cables, spacing_mode=input.spacing_mode,
            axial_spacing_m=input.spacing_m,
        )
        donazzi_corr = 0.0
        if (
            input.backfill_thermal_resistivity_kmw is not None
            and input.backfill_width_m is not None
            and input.backfill_height_m is not None
        ):
            donazzi_corr = inst.backfill_correction_kmw(
                input.burial_depth_m,
                input.backfill_width_m, input.backfill_height_m,
                input.soil_thermal_resistivity_kmw,
                input.backfill_thermal_resistivity_kmw,
                n_cables=max(1, input.grouped_cables),
            )
        t4ppp += donazzi_corr
        theta_m = (input.ambient_temperature_c + input.conductor_max_temp_c) / 2
        # Iterate sheath temperature only for high-λ₁ flat formations where
        # Rs(θ_s) sensitivity moves the answer (TB 880 case 0-3). Trefoil
        # cases have small λ₁ and the fixed θ_c−10 bootstrap is closer to
        # vault's iterated value than our simple W_c·T1 estimate.
        iterate_theta_s = (
            input.spacing_mode == "flat"
            and input.bonding == "solidly_bonded"
        )
        theta_s_iter: float | None = None if not iterate_theta_s else input.conductor_max_temp_c - 10
        I = 0.0
        breakdown = {}
        for _ in range(40):
            I, breakdown = _compute_at_temperature(
                input, input.conductor_max_temp_c, None, theta_m, theta_s_iter,
            )
            Wt = (
                I * I * breakdown["R_ac"] * (1 + breakdown["lambda1"] + breakdown["lambda2"])
                + breakdown["W_d"]
            )
            t4p = inst.t4_prime_in_duct(d_e, theta_m, duct_const)
            # In a multi-cable bank, the limiting cable's duct sees both its
            # own losses' temperature rise AND mutual heating Δθ_P from the
            # other cables' heat reaching it through the soil. The standard
            # θ_m formula assumes an isolated cable; add Δθ_P explicitly.
            dtp = breakdown.get("delta_theta_p", 0.0) or 0.0
            new_theta_m = input.ambient_temperature_c + dtp + Wt * (t4pp + t4ppp + 0.5 * t4p)
            new_theta_s = (
                None if not iterate_theta_s
                else input.conductor_max_temp_c
                     - I * I * breakdown["R_ac"] * breakdown["T1"]
                     - 0.5 * breakdown["W_d"] * breakdown["T1"]
            )
            theta_s_converged = (
                True if new_theta_s is None
                else abs(new_theta_s - (theta_s_iter or 0)) < 0.05
            )
            if abs(new_theta_m - theta_m) < 0.05 and theta_s_converged:
                theta_m = new_theta_m
                if new_theta_s is not None:
                    theta_s_iter = new_theta_s
                I, breakdown = _compute_at_temperature(
                    input, input.conductor_max_temp_c, None, theta_m, theta_s_iter,
                )
                break
            theta_m = 0.5 * (theta_m + new_theta_m)
            if new_theta_s is not None:
                theta_s_iter = 0.5 * ((theta_s_iter or 0) + new_theta_s)
        else:
            notes.append("In-duct θ_m iteration did not fully converge after 40 iterations.")
        notes.append(f"Mean duct temperature θ_m = {theta_m:.2f} °C")
    elif input.installation == "in_air":
        # Outer loop: surface temp rise depends on T4 which depends on rise.
        rise = input.conductor_max_temp_c - input.ambient_temperature_c  # initial guess
        theta_s_iter = input.conductor_max_temp_c - 10  # bootstrap
        I = 0.0
        breakdown: dict = {}
        # Solar absorption enters the surface heat balance (not just the
        # ampacity numerator) — the cable's surface is hotter than internal
        # losses alone would predict, which feeds back into T4 via Δθ.
        w_solar = 0.0
        for _ in range(40):
            I, breakdown = _compute_at_temperature(input, input.conductor_max_temp_c, rise, sheath_temp_c=theta_s_iter)
            geom_iter = breakdown["geometry"]
            if input.solar_absorption_coefficient > 0.0 and input.solar_radiation_w_per_m2 > 0.0:
                w_solar = (
                    input.solar_absorption_coefficient
                    * input.solar_radiation_w_per_m2
                    * geom_iter.overall_diameter
                )
            # Total surface heat = internal losses + absorbed solar.
            Wt = (
                I * I * breakdown["R_ac"] * (1 + breakdown["lambda1"] + breakdown["lambda2"])
                + breakdown["W_d"]
                + w_solar
            )
            new_rise = max(1.0, Wt * breakdown["T4"])
            new_theta_s = (
                input.conductor_max_temp_c
                - I * I * breakdown["R_ac"] * breakdown["T1"]
                - 0.5 * breakdown["W_d"] * breakdown["T1"]
            )
            if abs(new_rise - rise) < 0.001 and abs(new_theta_s - theta_s_iter) < 0.05:
                rise = new_rise
                theta_s_iter = new_theta_s
                break
            rise = 0.5 * (rise + new_rise)
            theta_s_iter = 0.5 * (theta_s_iter + new_theta_s)
        else:
            notes.append("In-air iteration did not fully converge after 40 iterations.")
        # Final pass at the converged rise + sheath temp.
        I, breakdown = _compute_at_temperature(input, input.conductor_max_temp_c, rise, sheath_temp_c=theta_s_iter)
    else:
        I, breakdown = _compute_at_temperature(input, input.conductor_max_temp_c, None)

    if breakdown.get("r20_source") == "iec60228":
        notes.append(
            f"R20 from IEC 60228 Class {input.cable.conductor_class} table "
            f"(no datasheet value supplied)."
        )

    losses = {
        "conductor_w_per_m": I * I * breakdown["R_ac"],
        "dielectric_w_per_m": breakdown["W_d"],
        "sheath_w_per_m": I * I * breakdown["R_ac"] * breakdown["lambda1"],
        "armour_w_per_m": I * I * breakdown["R_ac"] * breakdown["lambda2"],
    }
    return AmpacityResult(
        ampacity_a=I,
        conductor_temperature_c=input.conductor_max_temp_c,
        losses=losses,
        thermal_resistances={
            "T1": breakdown["T1"], "T2": breakdown["T2"],
            "T3": breakdown["T3"], "T4": breakdown["T4"],
        },
        derating_factors={"lambda1": breakdown["lambda1"], "lambda2": breakdown["lambda2"]},
        notes=notes,
        metadata={
            "y_s": breakdown["y_s"], "y_p": breakdown["y_p"],
            "R_ac": breakdown["R_ac"], "R_dc": breakdown["R_dc"],
        },
    )


def compute_conductor_temperature(input: AmpacityInput, current_a: float) -> AmpacityResult:
    """Inverse calc — bisection on θ_c such that compute_ampacity(θ_c) = I."""
    if current_a <= 0:
        return compute_ampacity(input)

    lo, hi = input.ambient_temperature_c + 0.1, 250.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        probe = AmpacityInput(**{**input.model_dump(), "conductor_max_temp_c": mid})
        I_max = compute_ampacity(probe).ampacity_a
        if I_max < current_a:
            lo = mid
        else:
            hi = mid
        if hi - lo < 0.01:
            break

    final_temp = 0.5 * (lo + hi)
    probe = AmpacityInput(**{**input.model_dump(), "conductor_max_temp_c": final_temp})
    result = compute_ampacity(probe)
    result.conductor_temperature_c = final_temp
    result.ampacity_a = current_a
    result.notes.append(f"Inverse bisection: θ_c at I={current_a} A is {final_temp:.2f} °C")
    return result
