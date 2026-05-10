"""Kennelly's image-method buried-cable temperature.

A closed-form alternative to IEC 60287 T4 for the *temperature field*
around a single cable (not just the surface temperature). Useful as:

  - independent cross-check of `engines/thermal/iec60287.installation_types.t4_*`
  - input to multi-cable thermal interference (superposition is exact
    for the linear heat equation; non-linearity only enters via
    temperature-dependent losses)
  - 2D temperature contour visualization for `apps/cables`

Algorithm (Kennelly's formula, image method in semi-infinite earth):

    ΔT(x, y) = (W / 2π · k) · ln(d_image / d_real)

where:
    W      = heat dissipation per metre (W/m)
    k      = soil thermal conductivity = 1 / ρ_T_soil
    d_real  = distance from field point to cable axis (at depth h)
    d_image = distance from field point to image cable (at height +h)

Sign convention: y < 0 is below ground (cable at (0, −h));
image is at (0, +h); ground surface at y = 0.

Ported from a vault calculator (Kennelly formula).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


def _ambient_at_depth(y: float, surface_temp_c: float, gradient_c_per_m: float) -> float:
    """Linear ground-temperature gradient (default near-surface model)."""
    return surface_temp_c + gradient_c_per_m * y


def temperature_rise_at(
    x: float,
    y: float,
    burial_depth_m: float,
    losses_w_per_m: float,
    soil_thermal_resistivity_kmw: float,
) -> float:
    """ΔT (K) at field point (x, y) above ambient, single cable.

    Cable is at (0, -burial_depth_m); image at (0, +burial_depth_m);
    ground surface at y = 0.
    """
    if soil_thermal_resistivity_kmw <= 0:
        return 0.0
    k = 1.0 / soil_thermal_resistivity_kmw
    d_real = math.hypot(x, y - (-burial_depth_m))
    d_image = math.hypot(x, y - burial_depth_m)
    if d_real <= 0:
        return float("inf")
    return (losses_w_per_m / (2 * math.pi * k)) * math.log(d_image / d_real)


def temperature_at(
    x: float,
    y: float,
    burial_depth_m: float,
    losses_w_per_m: float,
    soil_thermal_resistivity_kmw: float,
    ambient_surface_temp_c: float = 25.0,
    ambient_gradient_c_per_m: float = -0.02,
) -> float:
    """Absolute temperature (°C) at field point (x, y)."""
    delta = temperature_rise_at(
        x, y, burial_depth_m, losses_w_per_m, soil_thermal_resistivity_kmw
    )
    return _ambient_at_depth(y, ambient_surface_temp_c, ambient_gradient_c_per_m) + delta


@dataclass
class TemperatureGrid:
    xs: list[float]
    ys: list[float]
    temps: list[list[float]]   # temps[j][i] for ys[j], xs[i]
    delta: list[list[float]]   # ΔT (rise above ambient)


def temperature_grid(
    burial_depth_m: float,
    losses_w_per_m: float,
    soil_thermal_resistivity_kmw: float,
    x_range: tuple[float, float] = (-2.0, 2.0),
    y_range: tuple[float, float] = (-3.0, 0.0),
    nx: int = 60,
    ny: int = 60,
    ambient_surface_temp_c: float = 25.0,
    ambient_gradient_c_per_m: float = -0.02,
) -> TemperatureGrid:
    """Sampled (x, y) → T grid; ready for contour plotting in apps/cables."""
    xs = [x_range[0] + i * (x_range[1] - x_range[0]) / (nx - 1) for i in range(nx)]
    ys = [y_range[0] + j * (y_range[1] - y_range[0]) / (ny - 1) for j in range(ny)]
    temps: list[list[float]] = []
    deltas: list[list[float]] = []
    for y in ys:
        row_t: list[float] = []
        row_d: list[float] = []
        for x in xs:
            d = temperature_rise_at(
                x, y, burial_depth_m, losses_w_per_m, soil_thermal_resistivity_kmw
            )
            row_d.append(d)
            row_t.append(_ambient_at_depth(y, ambient_surface_temp_c, ambient_gradient_c_per_m) + d)
        temps.append(row_t)
        deltas.append(row_d)
    return TemperatureGrid(xs=xs, ys=ys, temps=temps, delta=deltas)


def superposed_rise_at(
    x: float,
    y: float,
    cables: Iterable[dict],
    soil_thermal_resistivity_kmw: float,
) -> float:
    """ΔT at field point from N cables by linear superposition.

    Each cable: {x, depth, losses_w_per_m}. Soil is shared.
    Exact for the linear heat equation; ignores temperature-dependent
    losses (caller iterates outer fixed-point if that matters).
    """
    total = 0.0
    if soil_thermal_resistivity_kmw <= 0:
        return 0.0
    k = 1.0 / soil_thermal_resistivity_kmw
    for c in cables:
        cx = float(c["x"])
        cd = float(c["depth"])
        W = float(c["losses_w_per_m"])
        d_real = math.hypot(x - cx, y - (-cd))
        d_image = math.hypot(x - cx, y - cd)
        if d_real > 0:
            total += (W / (2 * math.pi * k)) * math.log(d_image / d_real)
    return total
