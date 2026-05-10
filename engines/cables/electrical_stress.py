"""Coaxial cable electric-field stress — analytical.

Cylindrical coaxial geometry: conductor of radius r_c, insulation OD R.
Electric field at radius r (r_c ≤ r ≤ R) under voltage V:

    E(r) = V / (r · ln(R / r_c))

Maximum at the conductor surface (r = r_c); minimum at the insulation
outer surface (r = R). This is the standard sizing check for both
service voltage U₀ and basic insulation level (BIL) impulse withstand.

Ported from a prior standalone calculator. Behaviour-preserving
translation; same units (kV in, mm in, kV/mm out).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class StressResult:
    e_max: float          # at conductor surface, kV/mm
    e_min: float          # at insulation outer surface, kV/mm
    e_at: float | None    # at user-specified r, kV/mm  (None if not requested)
    voltage_kv: float
    radius_conductor_mm: float
    radius_insulation_mm: float
    mode: str             # "nominal" | "impulse"


def _check_geometry(r_c: float, R: float, V: float) -> None:
    if R <= r_c:
        raise ValueError("Insulation radius must exceed conductor radius")
    if any(v <= 0 for v in (r_c, R, V)):
        raise ValueError("All values must be positive")


def _field_at(V: float, r: float, r_c: float, R: float) -> float:
    return V / (r * math.log(R / r_c))


def nominal_field_strength(
    voltage_phase_kv: float,
    radius_conductor_mm: float,
    radius_insulation_mm: float,
    radius_at_mm: float | None = None,
) -> StressResult:
    """E-field under continuous service voltage U₀.

    `voltage_phase_kv` is U₀ — phase-to-ground rms, kV.
    """
    _check_geometry(radius_conductor_mm, radius_insulation_mm, voltage_phase_kv)
    e_max = _field_at(voltage_phase_kv, radius_conductor_mm,
                      radius_conductor_mm, radius_insulation_mm)
    e_min = _field_at(voltage_phase_kv, radius_insulation_mm,
                      radius_conductor_mm, radius_insulation_mm)
    e_at = (
        _field_at(voltage_phase_kv, radius_at_mm,
                  radius_conductor_mm, radius_insulation_mm)
        if radius_at_mm is not None and radius_conductor_mm <= radius_at_mm <= radius_insulation_mm
        else None
    )
    return StressResult(
        e_max=e_max, e_min=e_min, e_at=e_at,
        voltage_kv=voltage_phase_kv,
        radius_conductor_mm=radius_conductor_mm,
        radius_insulation_mm=radius_insulation_mm,
        mode="nominal",
    )


def impulse_field_strength(
    bil_kv: float,
    radius_conductor_mm: float,
    radius_insulation_mm: float,
    radius_at_mm: float | None = None,
) -> StressResult:
    """E-field under lightning-impulse withstand (BIL).

    Same coaxial formula; voltage is the BIL crest (kV).
    """
    _check_geometry(radius_conductor_mm, radius_insulation_mm, bil_kv)
    e_max = _field_at(bil_kv, radius_conductor_mm,
                      radius_conductor_mm, radius_insulation_mm)
    e_min = _field_at(bil_kv, radius_insulation_mm,
                      radius_conductor_mm, radius_insulation_mm)
    e_at = (
        _field_at(bil_kv, radius_at_mm,
                  radius_conductor_mm, radius_insulation_mm)
        if radius_at_mm is not None and radius_conductor_mm <= radius_at_mm <= radius_insulation_mm
        else None
    )
    return StressResult(
        e_max=e_max, e_min=e_min, e_at=e_at,
        voltage_kv=bil_kv,
        radius_conductor_mm=radius_conductor_mm,
        radius_insulation_mm=radius_insulation_mm,
        mode="impulse",
    )
