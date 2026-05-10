"""ElectrodeArray — 4-electrode configurations and their geometric factors.

A measurement is taken with two current electrodes (C1, C2) and two potential
electrodes (P1, P2) on the surface. The geometric factor K_g converts
measured resistance R = V_P1P2 / I to apparent resistivity:

    ρ_a = K_g · R
    K_g = 2π / G,   G = 1/r_C1P1 - 1/r_C1P2 - 1/r_C2P1 + 1/r_C2P2

The forward solver iterates over the four (sign, distance) pairs:
    (+1, r_C1P1), (-1, r_C1P2), (-1, r_C2P1), (+1, r_C2P2)
"""

from __future__ import annotations
from dataclasses import dataclass
from math import pi
from typing import Iterator, Literal


ArrayKind = Literal["wenner", "schlumberger", "dipole_dipole", "general"]


@dataclass(frozen=True)
class ElectrodeArray:
    """A single-spacing 4-electrode configuration.

    For each `kind`, `spacings` is interpreted as:
      - wenner:        (a,)                      — equal spacing a between adjacent electrodes
      - schlumberger:  (L, ell)                  — half current-spacing L, half potential-spacing ell
      - dipole_dipole: (a, n)                    — dipole length a, dipole separation n*a (n integer or float)
      - general:       (xC1, xC2, xP1, xP2)      — collinear positions on the x-axis
    """

    kind: ArrayKind
    spacings: tuple[float, ...]

    def positions(self) -> tuple[float, float, float, float]:
        """Return collinear (xC1, xC2, xP1, xP2) along x-axis."""
        if self.kind == "wenner":
            (a,) = self.spacings
            return (0.0, 3.0 * a, a, 2.0 * a)
        if self.kind == "schlumberger":
            L, ell = self.spacings
            return (-L, +L, -ell, +ell)
        if self.kind == "dipole_dipole":
            a, n = self.spacings
            # C1—C2 dipole then gap then P1—P2 dipole
            return (0.0, a, a * (1.0 + n), a * (2.0 + n))
        if self.kind == "general":
            xC1, xC2, xP1, xP2 = self.spacings
            return (xC1, xC2, xP1, xP2)
        raise ValueError(f"unknown array kind: {self.kind}")

    def electrode_pairs(self) -> Iterator[tuple[int, float]]:
        """Yield signed (sign, distance) pairs for the geometric sum

            G = 1/r_C1P1 - 1/r_C1P2 - 1/r_C2P1 + 1/r_C2P2
        """
        xC1, xC2, xP1, xP2 = self.positions()
        yield (+1, abs(xC1 - xP1))
        yield (-1, abs(xC1 - xP2))
        yield (-1, abs(xC2 - xP1))
        yield (+1, abs(xC2 - xP2))

    def geometric_factor(self) -> float:
        """K_g = 2π / G (closed-form for known arrays, computed for general)."""
        if self.kind == "wenner":
            (a,) = self.spacings
            return 2.0 * pi * a
        if self.kind == "schlumberger":
            L, ell = self.spacings
            # K_g = π (L^2 - ell^2) / (2 ell)
            return pi * (L * L - ell * ell) / (2.0 * ell)
        # general / dipole_dipole — compute from G
        g = 0.0
        for sign, r in self.electrode_pairs():
            if r <= 0:
                raise ValueError("electrodes must be distinct (non-zero distances)")
            g += sign * (1.0 / r)
        if g == 0.0:
            raise ValueError("degenerate geometry — geometric sum is zero")
        return 2.0 * pi / g
