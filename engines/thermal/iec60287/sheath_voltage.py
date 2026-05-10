"""Induced sheath standing voltage — IEC 60287-1-1 §4.3 / Annex C.

Distinct from `sheath_losses.py` (which returns λ₁, a loss-factor
multiplier on conductor losses). This module returns the induced
*voltage* on the cable sheath in volts — the safety-relevant quantity
for sizing sheath-voltage limiters (SVLs) and verifying touch-voltage
limits at link boxes.

Three bonding strategies, three voltage profiles:

- **Solidly bonded** — sheath shorted to earth at both ends; no
  standing voltage but circulating current → λ₁' loss.
- **Single-point bonded** — one earth reference, other end open;
  standing voltage grows linearly along the cable: U_end = E · L.
- **Cross-bonded** — three minor sections with phase-rotated bonding;
  standing voltage cancels at every major-section junction (ideal),
  peaks mid-minor-section at U_max ≈ E · L_minor / √3 for symmetric
  layouts; conservative practice uses E · L_minor.

The induced field per metre E [V/m] is `induced_field_per_metre()`;
the bonding-specific helpers wrap it with the relevant length.
"""

from __future__ import annotations

import math


def induced_field_per_metre(
    current_a: float,
    centre_spacing_m: float,
    sheath_mean_radius_m: float,
    formation: str = "trefoil",
    frequency_hz: float = 50.0,
) -> dict:
    """Induced sheath field E [V/m] per cable.

    Per IEC 60287-1-1:2014 §4.3 eq. (47):

        E = ω · M · I    where M = 2·10⁻⁷ · ln(2s / d_s)   [H/m]

    For trefoil all three cables see the same magnitude; for flat
    formation outer and middle differ (Annex E):

        M_middle = 2·10⁻⁷ · ln(2s / d_s)
        M_outer  = 2·10⁻⁷ · √( ln²(2s/d_s) + (3/4)·ln²(2) )

    Returns dict with per-cable values + worst-case (outer for flat).
    Caller picks the relevant value for SVL sizing.
    """
    if current_a <= 0 or centre_spacing_m <= 0 or sheath_mean_radius_m <= 0:
        return {"E": 0.0, "E_middle": 0.0, "E_outer": 0.0, "M": 0.0}

    omega = 2 * math.pi * frequency_hz
    d_s = 2 * sheath_mean_radius_m
    ln_2s_ds = math.log(2 * centre_spacing_m / d_s)

    if formation == "flat":
        M_middle = 2e-7 * ln_2s_ds
        M_outer = 2e-7 * math.sqrt(ln_2s_ds**2 + 0.75 * math.log(2) ** 2)
        E_middle = omega * M_middle * current_a
        E_outer = omega * M_outer * current_a
        return {
            "E": E_outer,  # worst case for SVL sizing
            "E_middle": E_middle,
            "E_outer": E_outer,
            "M_middle": M_middle,
            "M_outer": M_outer,
        }

    # Trefoil: symmetric, M same for all three cables.
    M = 2e-7 * ln_2s_ds
    E = omega * M * current_a
    return {"E": E, "E_middle": E, "E_outer": E, "M": M}


def standing_voltage_single_point(
    induced_field_per_m: float,
    cable_length_m: float,
) -> float:
    """Standing voltage at the open end of a single-point-bonded cable.

    U_end = E · L (volts).

    Grows linearly with route length — the load-bearing constraint on
    single-point bonding for long routes. IEC 60840 limits induced
    voltage to typically 65–300 V depending on accessibility; this is
    what SVLs are sized against.
    """
    if induced_field_per_m <= 0 or cable_length_m <= 0:
        return 0.0
    return induced_field_per_m * cable_length_m


def standing_voltage_cross_bonded(
    induced_field_per_m: float,
    minor_section_length_m: float,
    *,
    symmetric: bool = True,
) -> float:
    """Maximum standing voltage within a cross-bonded minor section.

    For a perfectly symmetric cross-bond (equal minor section lengths,
    ideal phase rotation), the residual voltage at each major-section
    junction is theoretically zero; the peak occurs mid-minor-section
    at U_peak = E · L_minor / √3.

    Real installations are rarely perfectly symmetric — unequal
    section lengths or transposition errors leave residual voltage at
    junctions. Pass ``symmetric=False`` for the conservative bound
    U_peak = E · L_minor used when the layout isn't audited.

    `minor_section_length_m` is the length of one minor section, NOT
    the major-section (3 × minor) length.
    """
    if induced_field_per_m <= 0 or minor_section_length_m <= 0:
        return 0.0
    if symmetric:
        return induced_field_per_m * minor_section_length_m / math.sqrt(3.0)
    return induced_field_per_m * minor_section_length_m


def standing_voltage_solidly_bonded(*_args, **_kwargs) -> float:
    """Solidly-bonded sheath has no standing voltage (clamped to earth).

    Stub for symmetry with the other bonding helpers — caller can
    dispatch on bonding string without a special case.
    """
    return 0.0


def standing_voltage(
    bonding: str,
    current_a: float,
    centre_spacing_m: float,
    sheath_mean_radius_m: float,
    *,
    cable_length_m: float | None = None,
    minor_section_length_m: float | None = None,
    formation: str = "trefoil",
    frequency_hz: float = 50.0,
    symmetric_cross_bond: bool = True,
) -> dict:
    """Aggregate sheath standing voltage for the given bonding.

    Returns dict with E [V/m], U_max [V], and the inputs needed to
    interpret the value (which length was used, which formation).
    """
    field = induced_field_per_metre(
        current_a, centre_spacing_m, sheath_mean_radius_m,
        formation=formation, frequency_hz=frequency_hz,
    )
    E = field["E"]

    if bonding == "solidly_bonded":
        U = 0.0
        length_used = 0.0
        regime = "solid"
    elif bonding == "single_point":
        if cable_length_m is None:
            raise ValueError("single_point bonding requires cable_length_m")
        U = standing_voltage_single_point(E, cable_length_m)
        length_used = cable_length_m
        regime = "single-point end"
    elif bonding == "cross_bonded":
        if minor_section_length_m is None:
            raise ValueError("cross_bonded bonding requires minor_section_length_m")
        U = standing_voltage_cross_bonded(
            E, minor_section_length_m, symmetric=symmetric_cross_bond,
        )
        length_used = minor_section_length_m
        regime = "cross-bonded peak"
    else:
        raise ValueError(f"unknown bonding: {bonding!r}")

    return {
        "E_v_per_m": E,
        "U_v": U,
        "length_m": length_used,
        "regime": regime,
        "formation": formation,
        "field": field,
    }
