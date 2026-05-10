"""Diagnostics — equivalence-problem detection and warning generation.

The Jacobian SVD diagnostic implements DESIGN.md §11.4: at the converged
solution we already have J = ∂r_i/∂x_j (LM produced it for free on its
last step). Its singular values reveal which parameter combinations the
data actually constrain — if σ_max / σ_min > 100, some combination is
unresolved and the user should be told which one.

Each unresolved direction is reported by name: e.g. "log(rho_2) - log(h_1)"
means the data only fixes the difference; ρ₂ and h₁ can co-vary along that
ridge with negligible RMS change. This is the classic ρ·h equivalence for
thin conductive layers (Maillet 1947).
"""

from __future__ import annotations
from dataclasses import dataclass

import numpy as np


# Threshold above which we declare a parameter combination "unresolved".
# 100 is the conventional rule of thumb (cf. Menke 2018 ch. 5).
CONDITION_NUMBER_THRESHOLD = 100.0


@dataclass(frozen=True)
class UnresolvedDirection:
    """One direction in log-parameter space that the data poorly constrains."""

    singular_value: float                 # the small σ
    description: str                      # human-readable: "log(rho_2) + log(h_1)"
    components: tuple[tuple[str, float], ...]   # (param_name, weight) pairs, |weight| > 0.2


@dataclass(frozen=True)
class JacobianDiagnostic:
    condition_number: float
    singular_values: tuple[float, ...]
    is_well_conditioned: bool
    unresolved_directions: tuple[UnresolvedDirection, ...]


def jacobian_diagnostic(
    jacobian: np.ndarray,
    free_param_names: list[str],
    threshold: float = CONDITION_NUMBER_THRESHOLD,
) -> JacobianDiagnostic:
    """Compute condition number + identify unresolved parameter combinations.

    `jacobian` shape (M, p) where M = #measurements, p = #free parameters
    (rows = residuals, columns = log-parameters).

    `free_param_names` length p, ordered to match the columns of J.
    Names like "log(rho_1)", "log(h_2)" — used to describe ridges.
    """
    if jacobian.size == 0 or len(free_param_names) == 0:
        return JacobianDiagnostic(
            condition_number=1.0,
            singular_values=(),
            is_well_conditioned=True,
            unresolved_directions=(),
        )

    # SVD: J = U Σ V^T. Right-singular vectors V give parameter combinations;
    # the j-th column of V is the combination with singular value σ_j.
    _, sigma, vt = np.linalg.svd(jacobian, full_matrices=False)
    sigma_max = float(sigma[0])
    sigma_min = float(sigma[-1])
    cond = sigma_max / sigma_min if sigma_min > 0 else float("inf")

    well_conditioned = cond <= threshold

    unresolved: list[UnresolvedDirection] = []
    if not well_conditioned:
        # Each σ_j with σ_max/σ_j > threshold flags one unresolved direction.
        # vt has shape (p, p); row j is the j-th right-singular vector.
        for j, s in enumerate(sigma):
            if s <= 0 or sigma_max / s > threshold:
                v = vt[j]  # the right-singular vector for σ_j
                # Pick out components with substantial weight (|v_k| > 0.2)
                comps = []
                for k, w in enumerate(v):
                    if abs(w) > 0.2:
                        comps.append((free_param_names[k], float(w)))
                # Build a human-readable description
                parts = []
                for name, w in comps:
                    sign = "+" if w >= 0 else "-"
                    if not parts and sign == "+":
                        parts.append(name)
                    else:
                        parts.append(f"{sign} {name}")
                description = " ".join(parts) if parts else "(diffuse direction)"
                unresolved.append(
                    UnresolvedDirection(
                        singular_value=float(s),
                        description=description,
                        components=tuple(comps),
                    )
                )

    return JacobianDiagnostic(
        condition_number=float(cond),
        singular_values=tuple(float(s) for s in sigma),
        is_well_conditioned=well_conditioned,
        unresolved_directions=tuple(unresolved),
    )


def free_param_names(
    n_layers: int,
    lock_rho: tuple[bool, ...],
    lock_h: tuple[bool, ...],
) -> list[str]:
    """Names of the free log-parameters in optimiser column order.

    Matches the packing in inverse._pack: ρ_1..ρ_n unlocked first, then h_1..h_{n-1}.
    """
    names = []
    for i in range(n_layers):
        if not lock_rho[i]:
            names.append(f"log(rho_{i+1})")
    for i in range(n_layers - 1):
        if not lock_h[i]:
            names.append(f"log(h_{i+1})")
    return names
