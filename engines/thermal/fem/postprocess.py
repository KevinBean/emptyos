"""FEM rating loop — Variation 5 of CIGRE TB 963.

Iterate on the conductor current I:
    1. Compute per-cable losses (W_c, λ1·W_c, W_d) from EmptyOS
       iec60287 closed-form formulas at the current θ_c estimate.
    2. Map losses → volumetric heat sources per element, by zone.
    3. Solve 2D FEM steady-state thermal field.
    4. Extract θ_c (mean over conductor elements per cable).
    5. Scale I = I · sqrt(Δθ_target / Δθ_actual). Newton-like.
    6. Stop when |max(θ_c) − target| < tol_temp and |ΔI| < tol_I.

The mesh, soil/insulation/sheath/oversheath thermal conductivities, and
the cable geometry come from EmptyOS contracts (AmpacityInput +
CableLibraryEntry.geometry). The IEC 60287 module supplies R_ac, λ1, W_d
— the FEM replaces the analytical T1..T4 network with a real 2D field.

MVP scope: direct-buried trefoil only. Validated against TB 880 case-1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from engines.models import AmpacityInput

from ..iec60287 import conductor_losses, dielectric_losses, sheath_losses
from .mesh import (
    MAT_CONDUCTOR,
    MAT_INSULATION,
    MAT_OVERSHEATH,
    MAT_SHEATH,
    MAT_SOIL,
    CableLayers,
    FEMMesh,
    create_trefoil_mesh,
    trefoil_centres_mm,
)
from .solver import element_average_temperature, solve_thermal


# Thermal conductivities k = 1/ρ_T (W/m·K).
# Insulation defaults — IEC 60287-2-1 Table 1; jacket = PE; soil set per call.
K_CONDUCTOR = 400.0       # Cu (effectively isothermal compared to surroundings)
K_INSULATION_XLPE = 1.0 / 3.5
K_SHEATH_AL = 237.0
K_SHEATH_CU = 400.0
K_SHEATH_PB = 35.0
K_OVERSHEATH_PE = 1.0 / 3.5

K_BY_INSULATION = {
    "XLPE": 1.0 / 3.5, "EPR": 1.0 / 3.5, "PVC": 1.0 / 6.0,
    "Paper": 1.0 / 6.0, "PILC": 1.0 / 6.0, "Other": 1.0 / 5.0,
}
K_BY_SHEATH = {
    "Cu": 400.0, "Al": 237.0, "Pb": 35.0, "PbAlloy": 33.0,
    "Steel": 50.0, "None": 50.0,
}


@dataclass
class FEMCableResult:
    cable_id: str
    theta_c: float
    theta_sh: float
    R_ac: float
    lambda1: float
    W_c: float
    W_sh: float
    W_d: float


@dataclass
class FEMRatingResult:
    I: float
    converged: bool
    n_iterations: int
    per_cable: list[FEMCableResult]
    max_theta_c: float
    temperature_field: np.ndarray | None = None
    mesh: FEMMesh | None = None


def compute_fem_rating(
    input: AmpacityInput,
    target_temp_c: float | None = None,
    max_iterations: int = 30,
    tol_temp: float = 0.05,
    tol_I: float = 0.1,
    initial_I: float = 600.0,
    domain_width_mm: float = 40000.0,
    domain_depth_mm: float = 20000.0,
    mesh_size_cable: float = 1.0,
    mesh_size_far: float = 600.0,
    return_field: bool = False,
) -> FEMRatingResult:
    """Iterate FEM thermal solve to find I such that max θ_c = target.

    MVP only supports direct-buried trefoil with grouped_cables=3.
    """
    if input.installation != "direct_buried":
        raise NotImplementedError(
            f"FEM MVP supports direct_buried only, got {input.installation!r}"
        )
    if input.spacing_mode != "trefoil" or input.grouped_cables != 3:
        raise NotImplementedError(
            "FEM MVP supports trefoil grouped_cables=3 only"
        )
    cable = input.cable
    geom = cable.geometry
    if geom is None:
        raise ValueError("CableLibraryEntry.geometry required for FEM rating")
    if not geom.sheath_thickness or not geom.sheath_inner_diameter:
        raise ValueError("FEM MVP requires explicit sheath geometry")

    target = target_temp_c if target_temp_c is not None else input.conductor_max_temp_c
    T_ambient = input.ambient_temperature_c

    # ── Geometry → cable layer radii (mm) ─────────────────────
    d_c = geom.conductor_diameter
    d_e = geom.overall_diameter
    sheath_inner_d = geom.sheath_inner_diameter
    sheath_outer_d = sheath_inner_d + 2 * geom.sheath_thickness
    layers = CableLayers(
        r_conductor=d_c / 2 * 1000.0,
        r_insulation=geom.insulation_outer_diameter / 2 * 1000.0,
        r_sheath=sheath_outer_d / 2 * 1000.0,
        r_oversheath=d_e / 2 * 1000.0,
    )

    centres = trefoil_centres_mm(input.burial_depth_m, d_e)
    mesh = create_trefoil_mesh(
        layers, centres,
        domain_width_mm=domain_width_mm,
        domain_depth_mm=domain_depth_mm,
        mesh_size_cable=mesh_size_cable,
        mesh_size_far=mesh_size_far,
    )

    # ── Static loss inputs (don't change across iterations) ───
    sheath_inner_r = sheath_inner_d / 2
    sheath_mean_r = sheath_inner_r + geom.sheath_thickness / 2
    sheath_csa_mm2 = math.pi * 2 * sheath_mean_r * geom.sheath_thickness * 1e6

    eps_r, tan_d = dielectric_losses.INSULATION_DEFAULTS.get(
        cable.insulation_material, (3.0, 0.005)
    )
    C = dielectric_losses.capacitance_per_metre(
        geom.insulation_inner_diameter, geom.insulation_outer_diameter, eps_r
    )
    U0 = ((cable.rated_voltage_kv or 11.0) * 1e3) / math.sqrt(3)
    W_d = dielectric_losses.dielectric_loss_per_metre(C, U0, input.frequency_hz, tan_d)

    # rho20 override from datasheet R20 (matters at ~3% level for stranded conductors)
    rho20_override = None
    if cable.conductor_dc_resistance_20c_ohm_per_km is not None:
        r20_per_m = cable.conductor_dc_resistance_20c_ohm_per_km * 1e-3
        rho20_override = r20_per_m * (cable.conductor_csa_mm2 * 1e-6)

    # spacing for trefoil-touching is just d_e
    s_m = d_e

    # ── Pre-build per-element conductivity (depends on θ_sh slightly via
    # Rs but conductivity itself is geometry, so it's constant). Soil k
    # comes from input.soil_thermal_resistivity_kmw.
    k_cond = K_CONDUCTOR
    k_ins = K_BY_INSULATION.get(cable.insulation_material, 1.0 / 3.5)
    k_sh = K_BY_SHEATH.get(cable.sheath_material, 50.0)
    k_os = K_OVERSHEATH_PE
    k_soil = 1.0 / input.soil_thermal_resistivity_kmw

    conductivity = np.full(mesh.n_elements, k_soil, dtype=np.float64)
    conductivity[mesh.material_tags == MAT_CONDUCTOR] = k_cond
    conductivity[mesh.material_tags == MAT_INSULATION] = k_ins
    conductivity[mesh.material_tags == MAT_SHEATH] = k_sh
    conductivity[mesh.material_tags == MAT_OVERSHEATH] = k_os
    # MAT_SOIL is the default fill above

    # ── Iteration ─────────────────────────────────────────────
    I = initial_I
    n_cables = len(mesh.cable_zones)
    theta_c = {z.cable_id: target for z in mesh.cable_zones}
    theta_sh = {z.cable_id: target - 7.0 for z in mesh.cable_zones}

    converged = False
    n_iter = 0
    T_field: np.ndarray | None = None
    last_per_cable: dict[str, dict] = {}

    for iteration in range(1, max_iterations + 1):
        n_iter = iteration

        # Per-cable losses at the current θ_c, θ_sh estimate
        per_cable_losses: dict[str, dict] = {}
        for zone in mesh.cable_zones:
            cid = zone.cable_id
            ac = conductor_losses.ac_resistance_per_metre(
                csa_mm2=cable.conductor_csa_mm2,
                conductor_diameter_m=d_c,
                centre_spacing_m=s_m,
                material=cable.conductor_material,
                temperature_c=theta_c[cid],
                frequency_hz=input.frequency_hz,
                rho_20_override=rho20_override,
            )
            R_ac = ac["r_ac"]

            sheath_mat = cable.sheath_material if cable.sheath_material != "None" else "Cu"
            Rs = sheath_losses.sheath_resistance_per_metre(
                sheath_csa_mm2, sheath_mat, temperature_c=theta_sh[cid],
            )
            lam1 = sheath_losses.compute_lambda1(
                input.bonding, Rs, R_ac, sheath_mean_r, s_m, input.frequency_hz,
                sheath_thickness_m=geom.sheath_thickness,
                include_eddy_for_solid_bonding=input.include_eddy_for_solid_bonding,
                formation="trefoil",
                n_cores=cable.n_conductors,
            )

            W_c = I * I * R_ac
            W_sh = lam1 * W_c
            per_cable_losses[cid] = {
                "R_ac": R_ac, "lambda1": lam1, "W_c": W_c, "W_sh": W_sh,
            }

        # Build heat-source array (W/m² in 2D = W/m of cable per m² of cross-section)
        # Each cable's W_c is distributed over its conductor area; W_sh over sheath area;
        # W_d over insulation area. Conductor area in m² = π·(d_c/2)².
        heat_source = np.zeros(mesh.n_elements, dtype=np.float64)
        A_cond_m2 = math.pi * (d_c / 2) ** 2
        A_ins_m2 = math.pi * (
            (geom.insulation_outer_diameter / 2) ** 2 - (geom.insulation_inner_diameter / 2) ** 2
        )
        A_sh_m2 = math.pi * (
            (sheath_outer_d / 2) ** 2 - (sheath_inner_d / 2) ** 2
        )
        for zone in mesh.cable_zones:
            losses = per_cable_losses[zone.cable_id]
            heat_source[zone.conductor_elems] = losses["W_c"] / A_cond_m2
            heat_source[zone.insulation_elems] = W_d / A_ins_m2 if A_ins_m2 > 0 else 0.0
            if A_sh_m2 > 0:
                heat_source[zone.sheath_elems] = losses["W_sh"] / A_sh_m2

        T_field = solve_thermal(mesh, conductivity, heat_source, T_ambient)

        # Extract per-cable θ_c, θ_sh
        max_tc = -999.0
        for zone in mesh.cable_zones:
            cid = zone.cable_id
            tc = element_average_temperature(T_field, mesh.elements, zone.conductor_elems)
            tsh = element_average_temperature(T_field, mesh.elements, zone.sheath_elems)
            theta_c[cid] = tc
            if tsh > T_ambient:
                theta_sh[cid] = tsh
            if tc > max_tc:
                max_tc = tc

        # Scale current
        if max_tc > T_ambient + 1.0:
            delta_target = target - T_ambient
            delta_actual = max_tc - T_ambient
            I_new = I * math.sqrt(delta_target / delta_actual)
        else:
            I_new = I * 1.5

        if iteration > 1 and abs(max_tc - target) < tol_temp and abs(I_new - I) < tol_I:
            converged = True
            I = I_new
            last_per_cable = per_cable_losses
            break

        I = I_new
        last_per_cable = per_cable_losses

    per_cable_results = [
        FEMCableResult(
            cable_id=zone.cable_id,
            theta_c=theta_c[zone.cable_id],
            theta_sh=theta_sh[zone.cable_id],
            R_ac=last_per_cable[zone.cable_id]["R_ac"],
            lambda1=last_per_cable[zone.cable_id]["lambda1"],
            W_c=last_per_cable[zone.cable_id]["W_c"],
            W_sh=last_per_cable[zone.cable_id]["W_sh"],
            W_d=W_d,
        )
        for zone in mesh.cable_zones
    ]

    return FEMRatingResult(
        I=I,
        converged=converged,
        n_iterations=n_iter,
        per_cable=per_cable_results,
        max_theta_c=max(theta_c.values()),
        temperature_field=T_field if return_field else None,
        mesh=mesh if return_field else None,
    )


def export_heatmap_data(
    result: FEMRatingResult,
    viewport_margin_mm: float = 1500.0,
    max_triangles: int = 100_000,
) -> dict:
    """Export FEM result as a triangle list for canvas-based heatmap rendering.

    Clips to a viewport around the cable group (``cable bbox + margin``) and
    returns triangle vertices with their average temperatures. The shape is
    deliberately flat — `[x1,y1, x2,y2, x3,y3, T_avg]` per triangle — so a
    plain `fillStyle = colormap(T)` + `fill()` loop on a 2D canvas can
    render the whole field without WebGL.

    The far-field background (median-or-larger triangles) is kept verbatim;
    fine-detail triangles near the cables are subsampled if the budget is
    exceeded so the export size stays bounded regardless of mesh density.
    Pass `return_field=True` to `compute_fem_rating` first; otherwise the
    function returns ``{"error": ...}``.

    Args:
        result: rating result from `compute_fem_rating(..., return_field=True)`.
        viewport_margin_mm: padding around the cable bounding box (mm).
        max_triangles: cap on returned triangles. Default 100k keeps the
            payload under ~5 MB of JSON for typical meshes.
    """
    if result.mesh is None or result.temperature_field is None:
        return {
            "error": (
                "No mesh/temperature data — call compute_fem_rating with "
                "return_field=True"
            )
        }

    mesh = result.mesh
    T = result.temperature_field
    nodes = mesh.nodes
    elements = mesh.elements

    cable_xs: list[float] = []
    cable_ys: list[float] = []
    cable_positions: list[dict] = []
    for zone in mesh.cable_zones:
        cond_nodes = np.unique(elements[zone.conductor_elems].flatten())
        if len(cond_nodes) == 0:
            continue
        cx = float(nodes[cond_nodes, 0].mean())
        cy = float(nodes[cond_nodes, 1].mean())
        cable_xs.append(cx)
        cable_ys.append(cy)
        pcr = next((c for c in result.per_cable if c.cable_id == zone.cable_id), None)
        cable_positions.append({
            "cable_id": zone.cable_id,
            "x": round(cx, 1),
            "y": round(cy, 1),
            "theta_c": round(float(pcr.theta_c), 1) if pcr else 0.0,
        })

    if not cable_xs:
        return {"error": "No cable zones found in mesh"}

    x_min = min(cable_xs) - viewport_margin_mm
    x_max = max(cable_xs) + viewport_margin_mm
    y_min = min(cable_ys) - viewport_margin_mm
    y_max = min(max(cable_ys) + viewport_margin_mm, 50.0)  # clamp to soil surface

    tri_nodes = nodes[elements]
    tri_x_min = tri_nodes[:, :, 0].min(axis=1)
    tri_x_max = tri_nodes[:, :, 0].max(axis=1)
    tri_y_min = tri_nodes[:, :, 1].min(axis=1)
    tri_y_max = tri_nodes[:, :, 1].max(axis=1)
    in_viewport = (
        (tri_x_max >= x_min) & (tri_x_min <= x_max)
        & (tri_y_max >= y_min) & (tri_y_min <= y_max)
    )
    visible = np.where(in_viewport)[0]

    if len(visible) > max_triangles:
        n0 = nodes[elements[visible, 0]]
        n1 = nodes[elements[visible, 1]]
        n2 = nodes[elements[visible, 2]]
        areas = 0.5 * np.abs(
            (n1[:, 0] - n0[:, 0]) * (n2[:, 1] - n0[:, 1])
            - (n2[:, 0] - n0[:, 0]) * (n1[:, 1] - n0[:, 1])
        )
        median_area = np.median(areas)
        large_idx = np.where(areas >= median_area)[0]
        small_idx = np.where(areas < median_area)[0]
        budget = max(0, max_triangles - len(large_idx))
        if len(small_idx) > budget > 0:
            step = len(small_idx) / budget
            sampled = small_idx[(np.arange(budget) * step).astype(int)]
            keep = np.concatenate([large_idx, sampled])
        else:
            keep = np.concatenate([large_idx, small_idx])
        visible = visible[keep.astype(int)]

    triangles: list[list[float]] = []
    T_min = float("inf")
    T_max = float("-inf")
    for idx in visible:
        n_idx = elements[idx]
        t_avg = float(T[n_idx].mean())
        if t_avg < T_min:
            T_min = t_avg
        if t_avg > T_max:
            T_max = t_avg
        triangles.append([
            round(float(nodes[n_idx[0], 0]), 1),
            round(float(nodes[n_idx[0], 1]), 1),
            round(float(nodes[n_idx[1], 0]), 1),
            round(float(nodes[n_idx[1], 1]), 1),
            round(float(nodes[n_idx[2], 0]), 1),
            round(float(nodes[n_idx[2], 1]), 1),
            round(t_avg, 2),
        ])

    return {
        "viewport": {
            "x_min": round(x_min, 1), "x_max": round(x_max, 1),
            "y_min": round(y_min, 1), "y_max": round(y_max, 1),
        },
        "T_range": {"min": round(T_min, 2), "max": round(T_max, 2)},
        "cables": cable_positions,
        "n_triangles": len(triangles),
        "triangles": triangles,
    }
