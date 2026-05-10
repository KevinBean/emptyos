"""Auto initial-estimate engine.

Given a sequence of (spacing, apparent_resistivity) pairs and a target layer
count, return a `SoilModel` to seed the optimiser. See DESIGN.md §7.2.

Strategy:
  ρ_1 ≈ ρ_a at the smallest spacing
  ρ_n ≈ ρ_a at the largest spacing (or extrapolated trend)
  h_1 ≈ spacing where ρ_a is at the geometric mean of (ρ_1, ρ_n)
  multi-layer (n > 2): split spacings into n-1 segments by curve extrema, seed each.

This is a heuristic — RESAP makes the user choose `n` and so do we.
The estimate need only be in the right basin of attraction for LM.
"""

from __future__ import annotations
from math import log, sqrt
from typing import Sequence

from .soil_model import SoilModel


def _bracketing_spacing(
    spacings: Sequence[float],
    rhos: Sequence[float],
    target: float,
) -> float:
    """Return the spacing at which ρ_a most closely brackets `target`.

    Uses linear interpolation in log-spacing if `target` lies between two
    adjacent ρ values; falls back to the closest sample.
    """
    for i in range(len(rhos) - 1):
        a, b = rhos[i], rhos[i + 1]
        if (a - target) * (b - target) <= 0 and a != b:
            # target lies between rhos[i] and rhos[i+1]
            t = (target - a) / (b - a)
            log_s = log(spacings[i]) + t * (log(spacings[i + 1]) - log(spacings[i]))
            return float(2.718281828459045**log_s)
    # no bracket — return the spacing whose ρ is closest in log-space
    best = min(range(len(rhos)), key=lambda i: abs(log(rhos[i]) - log(target)))
    return float(spacings[best])


def auto_initial_estimate(
    spacings: Sequence[float],
    apparent_resistivities: Sequence[float],
    n_layers: int,
) -> SoilModel:
    """Return an initial-guess SoilModel for the inverter.

    `spacings` and `apparent_resistivities` must be the same length and sorted
    in ascending spacing order.
    """
    if len(spacings) != len(apparent_resistivities):
        raise ValueError("spacings and apparent_resistivities must have the same length")
    if len(spacings) < 2:
        raise ValueError("need at least 2 measurements for an initial estimate")
    if n_layers < 1:
        raise ValueError("n_layers must be >= 1")
    if any(s <= 0 for s in spacings):
        raise ValueError("all spacings must be > 0")
    if any(r <= 0 for r in apparent_resistivities):
        raise ValueError("all apparent resistivities must be > 0")

    s = list(spacings)
    r = list(apparent_resistivities)

    if n_layers == 1:
        # Best-guess uniform: geometric mean of all measurements
        log_mean = sum(log(rho) for rho in r) / len(r)
        return SoilModel(resistivities=(float(2.718281828459045**log_mean),), thicknesses=())

    rho_top = r[0]
    rho_bot = r[-1]

    if n_layers == 2:
        target = sqrt(rho_top * rho_bot)
        h1 = _bracketing_spacing(s, r, target)
        return SoilModel(resistivities=(rho_top, rho_bot), thicknesses=(h1,))

    # n >= 3: split log-spacing range evenly, seed intermediate ρ by linear log-interp
    log_smin, log_smax = log(s[0]), log(s[-1])
    log_rho_top, log_rho_bot = log(rho_top), log(rho_bot)
    rhos: list[float] = []
    hs: list[float] = []
    for i in range(n_layers):
        t = i / (n_layers - 1)
        rhos.append(float(2.718281828459045 ** (log_rho_top + t * (log_rho_bot - log_rho_top))))
    for i in range(n_layers - 1):
        t = (i + 0.5) / (n_layers - 1)
        log_h = log_smin + t * (log_smax - log_smin)
        hs.append(float(2.718281828459045**log_h))
    return SoilModel(resistivities=tuple(rhos), thicknesses=tuple(hs))
