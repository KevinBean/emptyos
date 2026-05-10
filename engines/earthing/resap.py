"""Wenner four-pin soil-resistivity test (RESAP) — IEEE 81.

DELEGATED. As of v0.4.0, `apps/earthing/`'s `/api/soil/fit-two-layer`
and `/api/soil/predict` routes call into `engines/soil/` (Levenberg-
Marquardt LM inversion + Stefanesco/Hankel-DLF forward kernel) rather
than this module. This file is retained as a rollback path; it is no
longer reached from the running app. Plan to retire after one full
release proves the delegated path on real projects.

Wenner setup: 4 electrodes equispaced at distance ``a``; current is
injected through the outer pair, voltage is measured across the inner
pair, giving R = V/I in Ω. Apparent resistivity:

    ρ_a(a) = 2π · a · R(a)        [Ω·m]

For homogeneous soil ρ_a is constant with ``a``. For two-layer soil
(ρ₁ from surface to depth ``h``, ρ₂ below), ρ_a varies — small ``a``
samples the upper layer, large ``a`` reaches the lower layer. The
classical forward model (Sunde, IEEE 81-2012 Annex C):

    ρ_a(a) = ρ₁ · [1 + 4 · Σ_{n=1}^∞ K^n · (1/√(1+(2nh/a)²)
                                          - 1/√(4+(2nh/a)²))]

where K = (ρ₂ - ρ₁) / (ρ₂ + ρ₁) is the reflection coefficient. The
series converges quickly for |K| < 1 (typical earth resistivities);
50 terms is comfortably enough for engineering accuracy.

Inversion is a 3-parameter least-squares fit. We do a direct grid
search rather than calling out to scipy — this engine is pure-Python
so the daemon doesn't drag scipy into its base install. The grid is
log-spaced over reasonable physical ranges; coarse-then-fine refinement
keeps total cost under ~1 s for a typical 6-spacing test.
"""

from __future__ import annotations

import math
from typing import Iterable


def wenner_resistance_to_apparent_rho(spacing_m: float, resistance_ohm: float) -> float:
    """ρ_a = 2π · a · R for a Wenner four-pin test."""
    if spacing_m <= 0 or resistance_ohm <= 0:
        return 0.0
    return 2.0 * math.pi * spacing_m * resistance_ohm


def apparent_resistivity_homogeneous(rho_ohm_m: float, spacings_m: Iterable[float]) -> list[float]:
    """Trivial — homogeneous soil reads the same ρ_a at every spacing."""
    return [rho_ohm_m for _ in spacings_m]


def apparent_resistivity_two_layer(
    rho1_ohm_m: float,
    rho2_ohm_m: float,
    h_layer1_m: float,
    spacings_m: Iterable[float],
    *,
    n_terms: int = 50,
) -> list[float]:
    """Forward model: predict ρ_a at each Wenner spacing for a 2-layer earth.

    Parameters
    ----------
    rho1_ohm_m : resistivity of upper layer (surface to ``h``), Ω·m
    rho2_ohm_m : resistivity of lower half-space, Ω·m
    h_layer1_m : thickness of the upper layer, m
    spacings_m : iterable of Wenner spacings ``a`` to evaluate
    n_terms : truncation of the infinite reflection series

    Returns
    -------
    list[float] of ρ_apparent, one per input spacing.
    """
    if rho1_ohm_m <= 0 or rho2_ohm_m <= 0 or h_layer1_m <= 0:
        raise ValueError("rho1, rho2, h must be > 0")
    K = (rho2_ohm_m - rho1_ohm_m) / (rho2_ohm_m + rho1_ohm_m)
    out: list[float] = []
    for a in spacings_m:
        if a <= 0:
            out.append(0.0)
            continue
        beta = h_layer1_m / a
        ssum = 0.0
        K_pow = 1.0
        for n in range(1, n_terms + 1):
            K_pow *= K
            two_n_beta = 2.0 * n * beta
            term = (
                1.0 / math.sqrt(1.0 + two_n_beta * two_n_beta)
                - 1.0 / math.sqrt(4.0 + two_n_beta * two_n_beta)
            )
            ssum += K_pow * term
            if abs(K_pow * term) < 1e-12 and n > 5:
                break
        out.append(rho1_ohm_m * (1.0 + 4.0 * ssum))
    return out


def _rms_log_error(predicted: list[float], measured: list[float]) -> float:
    """Log-space RMS error so big and small ρ_a contribute equally to the
    fit (typical Wenner tests span ~1 order of magnitude in ρ_a)."""
    n = len(measured)
    if n == 0:
        return 0.0
    err2 = 0.0
    for p, m in zip(predicted, measured):
        if p <= 0 or m <= 0:
            return float("inf")
        d = math.log(p) - math.log(m)
        err2 += d * d
    return math.sqrt(err2 / n)


def fit_two_layer_grid_search(
    spacings_m: list[float],
    measured_rho_a_ohm_m: list[float],
    *,
    rho_min: float | None = None,
    rho_max: float | None = None,
    h_min: float = 0.3,
    h_max: float | None = None,
    n_grid: int = 30,
    n_refine: int = 2,
) -> dict:
    """Fit a 2-layer soil model to a Wenner sounding by direct grid search.

    Returns ``{"rho1", "rho2", "h", "rms_log_error", "predicted"}`` where
    ``predicted`` is the forward model evaluated at the input spacings
    using the best-fit parameters — useful for plotting fit quality.

    Strategy: log-spaced grid in (ρ₁, ρ₂) over [rho_min, rho_max] and
    linear grid in h over [h_min, h_max], evaluated at ``n_grid`` points
    per axis. After the coarse grid, ``n_refine`` rounds zoom in around
    the best candidate by a factor of 4 each. Total cost ≈ n_grid³ ·
    (1 + n_refine) forward evaluations (~108k at defaults), which runs
    in ~0.5 s for a typical 6-spacing test.

    No external dependency — keeps this engine importable without scipy.
    For production-grade fits with confidence intervals, the caller
    should re-run via scipy.optimize once it's installed.
    """
    if len(spacings_m) != len(measured_rho_a_ohm_m):
        raise ValueError("spacings and measurements must have equal length")
    if len(spacings_m) < 3:
        raise ValueError("need at least 3 measurements to fit a 2-layer model")

    rho_obs_min = min(measured_rho_a_ohm_m)
    rho_obs_max = max(measured_rho_a_ohm_m)
    if rho_min is None:
        rho_min = rho_obs_min * 0.3
    if rho_max is None:
        rho_max = rho_obs_max * 3.0
    if h_max is None:
        h_max = max(spacings_m) * 1.5

    def _log_grid(lo: float, hi: float, n: int) -> list[float]:
        if lo <= 0 or hi <= lo:
            raise ValueError("invalid log-grid bounds")
        log_lo, log_hi = math.log(lo), math.log(hi)
        return [math.exp(log_lo + i * (log_hi - log_lo) / (n - 1)) for i in range(n)]

    def _lin_grid(lo: float, hi: float, n: int) -> list[float]:
        return [lo + i * (hi - lo) / (n - 1) for i in range(n)]

    best = {"err": float("inf"), "rho1": None, "rho2": None, "h": None}

    # Coarse grid
    rho1_grid = _log_grid(rho_min, rho_max, n_grid)
    rho2_grid = _log_grid(rho_min, rho_max, n_grid)
    h_grid = _lin_grid(h_min, h_max, n_grid)

    def _scan(rho1s, rho2s, hs):
        for r1 in rho1s:
            for r2 in rho2s:
                for h in hs:
                    try:
                        pred = apparent_resistivity_two_layer(r1, r2, h, spacings_m)
                    except ValueError:
                        continue
                    err = _rms_log_error(pred, measured_rho_a_ohm_m)
                    if err < best["err"]:
                        best.update({"err": err, "rho1": r1, "rho2": r2, "h": h})

    _scan(rho1_grid, rho2_grid, h_grid)

    # Refinement zooms
    for _ in range(n_refine):
        r1c, r2c, hc = best["rho1"], best["rho2"], best["h"]
        zoom_rho = 0.25
        zoom_h = 0.25
        r1_lo = max(rho_min, r1c * (1.0 - zoom_rho))
        r1_hi = min(rho_max, r1c * (1.0 + zoom_rho))
        r2_lo = max(rho_min, r2c * (1.0 - zoom_rho))
        r2_hi = min(rho_max, r2c * (1.0 + zoom_rho))
        h_lo = max(h_min, hc * (1.0 - zoom_h))
        h_hi = min(h_max, hc * (1.0 + zoom_h))
        _scan(_log_grid(r1_lo, r1_hi, n_grid),
              _log_grid(r2_lo, r2_hi, n_grid),
              _lin_grid(h_lo, h_hi, n_grid))

    pred = apparent_resistivity_two_layer(
        best["rho1"], best["rho2"], best["h"], spacings_m
    )
    return {
        "rho1": best["rho1"],
        "rho2": best["rho2"],
        "h": best["h"],
        "rms_log_error": best["err"],
        "predicted": pred,
    }
