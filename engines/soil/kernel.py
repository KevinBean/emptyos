"""Stefanesco resistivity-transform recursion.

Computes T_1(λ), the surface value of the resistivity transform for a
horizontally layered earth, using the bottom-up recursion (Slichter / Pekeris):

    T_n(λ) = ρ_n
    T_i(λ) = (T_{i+1} + ρ_i tanh(λ h_i)) / (1 + (T_{i+1}/ρ_i) tanh(λ h_i))

The kernel function used in the Hankel transform is

    K(λ) = (T_1(λ) / ρ_1 - 1) / 2

K(λ) = 0 for a uniform half-space; K(λ) → ±1 in the high-contrast / large-λh limit.

The recursion takes only SOIL layers — air is implicit, never passed in.
"""

from __future__ import annotations
from math import tanh
from typing import Sequence


# Beyond this argument tanh saturates to 1.0 within float64 precision and the
# expression `inf · 0` would otherwise contaminate the recursion when ρ_i is
# very large and T_{i+1} is finite.
_TANH_SATURATION = 50.0


def stefanesco_recursion(
    lam: float,
    resistivities: Sequence[float],
    thicknesses: Sequence[float],
) -> float:
    """Return T_1(λ) for the given layered soil.

    `resistivities` length n, `thicknesses` length n-1.
    """
    n = len(resistivities)
    if n == 0:
        raise ValueError("at least one layer required")
    if len(thicknesses) != n - 1:
        raise ValueError("thicknesses must have length len(resistivities) - 1")

    T = float(resistivities[-1])
    # iterate from layer n-1 down to layer 1 (zero-indexed: n-2 down to 0)
    for i in range(n - 2, -1, -1):
        rho_i = float(resistivities[i])
        x = lam * float(thicknesses[i])
        t = 1.0 if x > _TANH_SATURATION else tanh(x)
        T = (T + rho_i * t) / (1.0 + (T / rho_i) * t)
    return T


def kernel(
    lam: float,
    resistivities: Sequence[float],
    thicknesses: Sequence[float],
) -> float:
    """K(λ) = (T_1(λ) / ρ_1 - 1) / 2."""
    T1 = stefanesco_recursion(lam, resistivities, thicknesses)
    return 0.5 * (T1 / float(resistivities[0]) - 1.0)
