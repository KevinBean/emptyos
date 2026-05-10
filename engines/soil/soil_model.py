"""SoilModel — horizontally stratified earth representation."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class SoilModel:
    """An n-layer horizontally stratified soil.

    `resistivities` has length n (top → bottom), all > 0.
    `thicknesses` has length n-1 (the bottom layer is a half-space, infinite).

    Air is NEVER represented in this model. The air half-space above layer 1
    is implicit; its reflection coefficient against layer 1 is reported as -1
    by convention (see `air_top_reflection_coefficient`).
    """

    resistivities: tuple[float, ...]
    thicknesses: tuple[float, ...]

    def __post_init__(self) -> None:
        n = len(self.resistivities)
        if n < 1:
            raise ValueError("at least one layer required")
        if len(self.thicknesses) != n - 1:
            raise ValueError(
                f"thicknesses must have length n-1 = {n-1}, got {len(self.thicknesses)}"
            )
        if any(r <= 0 for r in self.resistivities):
            raise ValueError("all resistivities must be > 0")
        if any(h <= 0 for h in self.thicknesses):
            raise ValueError("all thicknesses must be > 0")
        if any(r >= 1e12 for r in self.resistivities):
            raise ValueError(
                "resistivity >= 1e12 Ω·m looks like air leaked into the soil model; "
                "air is implicit, do not include it"
            )

    @property
    def n_layers(self) -> int:
        return len(self.resistivities)

    def reflection_coefficients(self) -> tuple[float, ...]:
        """K_i at each soil-soil interface (length n-1).

        K_i = (ρ_{i+1} - ρ_i) / (ρ_{i+1} + ρ_i),  i = 1..n-1
        """
        rho = self.resistivities
        return tuple(
            (rho[i + 1] - rho[i]) / (rho[i + 1] + rho[i])
            for i in range(len(rho) - 1)
        )

    def contrast_ratios(self) -> tuple[float, ...]:
        """ρ_{i+1} / ρ_i at each interface (length n-1)."""
        rho = self.resistivities
        return tuple(rho[i + 1] / rho[i] for i in range(len(rho) - 1))

    @staticmethod
    def air_top_reflection_coefficient() -> float:
        """K at the implicit air–top-soil interface — always -1 by convention."""
        return -1.0
