"""Dielectric losses — IEC 60287-1-1 §4.2.

W_d = ω · C · U₀² · tan δ          [W/m]

Significant only above ~33 kV for XLPE; below that, often neglected
(but Phase A computes it always for honesty — caller can ignore).
"""

from __future__ import annotations

import math


# Typical loss tangents and εr for cable insulations (IEC 60287-1-1
# Table 3). Caller can override per-cable.
INSULATION_DEFAULTS = {
    # name : (eps_r, tan_delta)
    "XLPE": (2.5, 0.001),
    "EPR":  (3.0, 0.005),
    "PVC":  (8.0, 0.1),
    "Paper":(4.0, 0.01),
    "PILC": (4.0, 0.01),
    "Other":(3.0, 0.005),
}


def capacitance_per_metre(
    insulation_inner_diameter_m: float,
    insulation_outer_diameter_m: float,
    eps_r: float,
) -> float:
    """C = ε / (18·ln(D_i/d_c)) × 10⁻⁹ F/m  — IEC 60287-1-1 eq. (38).

    Equivalent to coaxial capacitance C = 2πε₀εr / ln(D/d).
    """
    if insulation_outer_diameter_m <= insulation_inner_diameter_m:
        return 0.0
    return (eps_r / (18.0 * math.log(insulation_outer_diameter_m / insulation_inner_diameter_m))) * 1e-9


def dielectric_loss_per_metre(
    capacitance_per_m: float,
    phase_voltage_v: float,
    frequency_hz: float,
    tan_delta: float,
) -> float:
    """W_d = ω · C · U₀² · tan δ, W/m."""
    omega = 2 * math.pi * frequency_hz
    return omega * capacitance_per_m * (phase_voltage_v ** 2) * tan_delta
