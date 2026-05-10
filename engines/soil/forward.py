"""Forward apparent-resistivity solver for an n-layer horizontally stratified earth.

For each (sign, distance) pair from the electrode geometry, we evaluate

    partial_r = ∫_0^∞ K(λ) J_0(λ r) dλ ≈ (1/r) Σ w_j K(λ_j),  λ_j = exp(a + (j-1)s)/r

via a Hankel-transform DLF (engines.soil.filters), then accumulate

    acc = Σ_pairs sign · (1/r + 2 · partial_r)
    ρ_a = K_g · ρ_1 · acc / (2π)

Sanity check: uniform soil ⇒ K(λ) = 0 ⇒ acc = G ⇒ ρ_a = K_g · ρ_1 · G/(2π) = ρ_1.
"""

from __future__ import annotations
from math import pi

from .geometry import ElectrodeArray
from .kernel import kernel
from .soil_model import SoilModel
from .filters import HankelFilter, j0_filter


def forward_apparent_resistivity(
    model: SoilModel,
    array: ElectrodeArray,
    filt: HankelFilter | None = None,
) -> float:
    """Return computed apparent resistivity ρ_a (Ω·m) for `model` at `array`.

    Uniform-soil fast-path returns ρ_1 directly without needing filter coefficients,
    so this function is testable before any DLF data file is populated.
    """
    rho = model.resistivities
    h = model.thicknesses

    # Uniform-soil fast-path: K(λ) ≡ 0, so the integral term vanishes exactly.
    if all(r == rho[0] for r in rho):
        return float(rho[0])

    if filt is None:
        filt = j0_filter("default")

    Kg = array.geometric_factor()

    acc = 0.0
    for sign, r in array.electrode_pairs():
        if r <= 0:
            raise ValueError("electrode pair distance must be > 0")
        # partial = ∫_0^∞ K(λ) J_0(λ r) dλ via DLF
        partial = filt.evaluate(lambda lam: kernel(lam, rho, h), r)
        acc += sign * (1.0 / r + 2.0 * partial)

    return Kg * float(rho[0]) * acc / (2.0 * pi)
