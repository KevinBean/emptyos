"""Levenberg-Marquardt inverter for soil resistivity sounding.

Public API: `invert(measurements, config) -> InversionResult`.

Wraps `scipy.optimize.least_squares` with:
  - log-transformed parameters (log ρ_i, log h_i): enforces positivity, well-conditioned
  - bounded `trf` method (matches the spirit of damped LM)
  - parameter locking via boolean masks
  - per-point relative residuals  r_i = (forward_i - meas_i) / meas_i

Diagnostics returned: RMS error, average discrepancy, per-point discrepancy,
reflection coefficients, contrast ratios, warnings, convergence reason.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
from scipy.optimize import least_squares

from .diagnostics import (
    JacobianDiagnostic,
    free_param_names,
    jacobian_diagnostic,
)
from .forward import forward_apparent_resistivity
from .geometry import ElectrodeArray
from .soil_model import SoilModel


@dataclass(frozen=True)
class Measurement:
    """One sounding row: a probe configuration + a measured value."""

    array: ElectrodeArray
    apparent_resistivity: float
    active: bool = True
    comment: str = ""


@dataclass(frozen=True)
class InversionConfig:
    n_layers: int
    method: Literal["lm"] = "lm"
    max_iter: int = 500
    target_accuracy_pct: float = 2.5
    locked_resistivities: tuple[bool, ...] | None = None
    locked_thicknesses: tuple[bool, ...] | None = None
    initial_model: SoilModel | None = None
    bounds_resistivity: tuple[float, float] = (1.0, 1e6)
    bounds_thickness: tuple[float, float] = (1e-2, 1e3)


@dataclass(frozen=True)
class InversionResult:
    soil_model: SoilModel
    rms_error_pct: float
    average_discrepancy_pct: float
    per_point_discrepancy_pct: tuple[float, ...]
    reflection_coefficients: tuple[float, ...]
    contrast_ratios: tuple[float, ...]
    iterations: int
    convergence_reason: str
    warnings: tuple[str, ...]
    jacobian: JacobianDiagnostic | None = None


def _pack(model: SoilModel, lock_rho, lock_h) -> np.ndarray:
    """Pack unlocked log-parameters into a flat vector for the optimiser."""
    parts = []
    for i, rho in enumerate(model.resistivities):
        if not lock_rho[i]:
            parts.append(math.log(rho))
    for i, h in enumerate(model.thicknesses):
        if not lock_h[i]:
            parts.append(math.log(h))
    return np.array(parts, dtype=float)


def _unpack(
    x: np.ndarray,
    template: SoilModel,
    lock_rho: tuple[bool, ...],
    lock_h: tuple[bool, ...],
) -> SoilModel:
    """Reinsert locked values around the optimiser's free vector."""
    rhos = list(template.resistivities)
    hs = list(template.thicknesses)
    j = 0
    for i in range(len(rhos)):
        if not lock_rho[i]:
            rhos[i] = math.exp(x[j])
            j += 1
    for i in range(len(hs)):
        if not lock_h[i]:
            hs[i] = math.exp(x[j])
            j += 1
    return SoilModel(resistivities=tuple(rhos), thicknesses=tuple(hs))


def _compute_residuals(
    model: SoilModel,
    arrays: list[ElectrodeArray],
    measured: np.ndarray,
) -> np.ndarray:
    forward = np.array(
        [forward_apparent_resistivity(model, a) for a in arrays], dtype=float
    )
    return (forward - measured) / measured


def invert(
    measurements: Sequence[Measurement],
    config: InversionConfig,
) -> InversionResult:
    active = [m for m in measurements if m.active]
    M = len(active)
    n = config.n_layers
    n_free_total = 2 * n - 1

    if M < 1:
        raise ValueError("no active measurements")

    arrays = [m.array for m in active]
    measured = np.array([m.apparent_resistivity for m in active], dtype=float)

    lock_rho = config.locked_resistivities or tuple(False for _ in range(n))
    lock_h = config.locked_thicknesses or tuple(False for _ in range(n - 1))
    if len(lock_rho) != n or len(lock_h) != n - 1:
        raise ValueError("lock masks have wrong length for n_layers")

    n_free = sum(1 for x in lock_rho if not x) + sum(1 for x in lock_h if not x)
    if n_free > M:
        raise ValueError(
            f"under-determined: {n_free} free parameters, only {M} active measurements"
        )

    # Initial guess
    if config.initial_model is not None:
        guess = config.initial_model
    else:
        from .initial import auto_initial_estimate
        # extract Wenner spacings if uniform — else fall back to using the first electrode pair distance
        spacings: list[float] = []
        for a in arrays:
            if a.kind == "wenner":
                spacings.append(a.spacings[0])
            else:
                # Use the smallest electrode pair distance as a rough proxy
                spacings.append(min(r for _, r in a.electrode_pairs()))
        # sort by spacing, carry rho along
        idx = sorted(range(M), key=lambda i: spacings[i])
        s_sorted = [spacings[i] for i in idx]
        r_sorted = [float(measured[i]) for i in idx]
        guess = auto_initial_estimate(s_sorted, r_sorted, n_layers=n)

    if guess.n_layers != n:
        raise ValueError(
            f"initial_model has {guess.n_layers} layers, config wants {n}"
        )

    # If everything is locked, just compute forward + diagnostics; no optimisation.
    jac_at_optimum: np.ndarray | None = None
    if n_free == 0:
        final = guess
        iterations = 0
        reason = "all_parameters_locked"
    else:
        x0 = _pack(guess, lock_rho, lock_h)

        rho_lo, rho_hi = config.bounds_resistivity
        h_lo, h_hi = config.bounds_thickness
        lb = []
        ub = []
        for i, locked in enumerate(lock_rho):
            if not locked:
                lb.append(math.log(rho_lo))
                ub.append(math.log(rho_hi))
        for i, locked in enumerate(lock_h):
            if not locked:
                lb.append(math.log(h_lo))
                ub.append(math.log(h_hi))
        # Clip x0 into bounds (auto-estimate may sit on the boundary)
        x0 = np.clip(x0, np.array(lb) + 1e-9, np.array(ub) - 1e-9)

        def residuals(x: np.ndarray) -> np.ndarray:
            model = _unpack(x, guess, lock_rho, lock_h)
            return _compute_residuals(model, arrays, measured)

        result = least_squares(
            residuals,
            x0,
            bounds=(lb, ub),
            method="trf",
            ftol=1e-10,
            xtol=1e-10,
            gtol=1e-10,
            max_nfev=config.max_iter * max(n_free, 1),
        )
        final = _unpack(result.x, guess, lock_rho, lock_h)
        iterations = int(result.nfev)
        reason = _classify_termination(result.status)
        jac_at_optimum = np.asarray(result.jac, dtype=float)

    # Diagnostics
    res = _compute_residuals(final, arrays, measured)
    per_point_pct = tuple(float(abs(r) * 100.0) for r in res)
    rms_pct = float(math.sqrt(float(np.mean(res**2))) * 100.0)
    avg_pct = float(np.mean(per_point_pct))

    # Jacobian SVD condition (equivalence-problem detector) — DESIGN.md §11.4
    jdiag: JacobianDiagnostic | None = None
    if jac_at_optimum is not None:
        names = free_param_names(n, lock_rho, lock_h)
        jdiag = jacobian_diagnostic(jac_at_optimum, names)

    warnings = list(_build_warnings(
        rms_pct=rms_pct,
        config=config,
        arrays=arrays,
        final=final,
        jdiag=jdiag,
    ))

    return InversionResult(
        soil_model=final,
        rms_error_pct=rms_pct,
        average_discrepancy_pct=avg_pct,
        per_point_discrepancy_pct=per_point_pct,
        reflection_coefficients=final.reflection_coefficients(),
        contrast_ratios=final.contrast_ratios(),
        iterations=iterations,
        convergence_reason=reason,
        warnings=tuple(warnings),
        jacobian=jdiag,
    )


def _build_warnings(
    *,
    rms_pct: float,
    config: InversionConfig,
    arrays: list[ElectrodeArray],
    final: SoilModel,
    jdiag: JacobianDiagnostic | None,
):
    if rms_pct > config.target_accuracy_pct:
        yield (
            f"RMS {rms_pct:.2f}% exceeds target {config.target_accuracy_pct:.2f}%"
        )
    wenner_spacings = [a.spacings[0] for a in arrays if a.kind == "wenner"]
    if wenner_spacings and min(wenner_spacings) > 1.2:
        yield (
            "shortest spacing > 1.2 m; surface layer resolution may be unreliable"
        )
    rho_vals = final.resistivities
    if max(rho_vals) / min(rho_vals) > 1e6:
        yield "resistivity span > 6 orders of magnitude — cross-check"
    if jdiag is not None and not jdiag.is_well_conditioned:
        for d in jdiag.unresolved_directions:
            yield (
                f"unresolved parameter combination "
                f"({d.description}) — singular value {d.singular_value:.2e}, "
                f"condition number {jdiag.condition_number:.1f}"
            )


def _classify_termination(status: int) -> str:
    return {
        -1: "improper_input",
        0: "max_iter_no_convergence",
        1: "gtol_satisfied",
        2: "ftol_satisfied",
        3: "xtol_and_ftol_satisfied",
        4: "ftol_and_xtol_satisfied",
    }.get(status, f"scipy_status_{status}")
