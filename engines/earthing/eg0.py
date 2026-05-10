"""ENA EG-0 / IEC 60479 — probabilistic earthing risk assessment.

The IEEE 80 deterministic check asks "is the touch voltage below the
single tolerable limit?". EG-0 instead asks "is the probability of
fatality at this location, given how often someone is actually here,
below the tolerable risk threshold?".

    P(fatality) = P_coincidence · P_fibrillation

P_coincidence = N_f · N_e · (t_f + t_e)         [Poisson, simplified]
P_fibrillation = f(I_B, t_f) from IEC 60479-1 time-current curves

Body current at the touch voltage uses the IEC 60479-1 voltage-
dependent body impedance Z_B(U), not the flat 1000 Ω of IEEE 80. The
IEC curves are calibrated against real asymmetric fault waveforms, so
the asymmetric-awareness IEEE 80 handles externally with D_f is built
in here — do *not* pre-multiply U_T with D_f for an EG-0 assessment.

References:
- ENA EG-0 (DOC 025-2022) — Power System Earthing Guide.
- AS/NZS 60479.1 — Effects of current on human beings and livestock.
- IEC 60479-1 Figures 11-12 (Z_B), Figure 20 (time-current curves).
"""

from __future__ import annotations

import math
from bisect import bisect_left

# IEC 60479-1 Z_B(U) — total body impedance hand-to-hand or hand-to-feet,
# 50% population not-exceeded, large surface contact, dry conditions.
# Pairs of (touch voltage V, body impedance Ω). Below 25 V impedance is
# voltage-independent; above 1000 V it asymptotes to ~575 Ω.
_ZB_50PCT_V_OHM = (
    (25.0, 3250.0),
    (50.0, 2625.0),
    (75.0, 2200.0),
    (100.0, 1875.0),
    (125.0, 1625.0),
    (220.0, 1350.0),
    (700.0, 1100.0),
    (1000.0, 1050.0),
)


def body_impedance(touch_voltage_v: float) -> float:
    """IEC 60479-1 body impedance Z_B(U), Ω, 50%-population not-exceeded.

    Linear interpolation between tabulated values; clamps at the table
    extremes. For substation safety design the 50% curve is the IEC
    convention; lower percentiles (5%, 95%) exist for specific use cases
    but are not exposed here.
    """
    u = abs(touch_voltage_v)
    if u <= _ZB_50PCT_V_OHM[0][0]:
        return _ZB_50PCT_V_OHM[0][1]
    if u >= _ZB_50PCT_V_OHM[-1][0]:
        return _ZB_50PCT_V_OHM[-1][1]
    voltages = [v for v, _ in _ZB_50PCT_V_OHM]
    i = bisect_left(voltages, u)
    v0, z0 = _ZB_50PCT_V_OHM[i - 1]
    v1, z1 = _ZB_50PCT_V_OHM[i]
    frac = (u - v0) / (v1 - v0)
    return z0 + frac * (z1 - z0)


# IEC 60479-1 Figure 20 — time-current zones (50/60 Hz AC, hand-to-feet).
# Each curve is parameterised by fault duration t_f; current values are
# in mA. c1 = 5% fibrillation probability threshold; c2 = 50%; c3 = >50%.
# Below 100 ms the curves go vertical (current-limited not duration-
# limited); above 10 s they asymptote to "let-go" thresholds.
_C1_T_S_I_MA = (
    (0.040, 500.0),
    (0.080, 400.0),
    (0.100, 250.0),
    (0.200, 200.0),
    (0.500, 100.0),
    (1.000, 60.0),
    (2.000, 50.0),
    (5.000, 50.0),
    (10.000, 40.0),
)
_C2_T_S_I_MA = (
    (0.040, 1000.0),
    (0.080, 800.0),
    (0.100, 500.0),
    (0.200, 400.0),
    (0.500, 250.0),
    (1.000, 150.0),
    (2.000, 100.0),
    (5.000, 80.0),
    (10.000, 80.0),
)
_C3_T_S_I_MA = (
    (0.040, 2000.0),
    (0.080, 1500.0),
    (0.100, 1000.0),
    (0.200, 800.0),
    (0.500, 500.0),
    (1.000, 300.0),
    (2.000, 200.0),
    (5.000, 150.0),
    (10.000, 150.0),
)


def _interp_curve(table, t_s: float) -> float:
    """Log-log interpolation along an IEC time-current curve."""
    t = max(t_s, table[0][0])
    t = min(t, table[-1][0])
    times = [tt for tt, _ in table]
    i = bisect_left(times, t)
    if i == 0:
        return table[0][1]
    if i >= len(table):
        return table[-1][1]
    t0, i0 = table[i - 1]
    t1, i1 = table[i]
    if t0 == t1:
        return i0
    # Log-log because the curves are roughly straight on log axes.
    log_frac = (math.log(t) - math.log(t0)) / (math.log(t1) - math.log(t0))
    log_i = math.log(i0) + log_frac * (math.log(i1) - math.log(i0))
    return math.exp(log_i)


def fibrillation_thresholds_ma(t_f_s: float) -> dict:
    """Body current thresholds at fault duration t_f, mA.

    Returns ``{"c1": ..., "c2": ..., "c3": ...}`` — the boundaries of
    IEC 60479 zones AC-4.1 / AC-4.2 / AC-4.3.
    """
    if t_f_s <= 0:
        raise ValueError("t_f_s must be > 0")
    return {
        "c1": _interp_curve(_C1_T_S_I_MA, t_f_s),
        "c2": _interp_curve(_C2_T_S_I_MA, t_f_s),
        "c3": _interp_curve(_C3_T_S_I_MA, t_f_s),
    }


def fibrillation_probability(body_current_ma: float, t_f_s: float) -> dict:
    """Approximate P_fibrillation given body current and duration.

    Linear interpolation between the c1/c2/c3 zone boundaries:

      < c1            → 0.005   (below detectable, design as ~0)
      c1 to c2        → linear 0.05 → 0.50
      c2 to c3        → linear 0.50 → 0.90
      > c3            → 0.95   (very high; hard cap below 1.0 because
                                IEC 60479 curves don't certify 100%)

    Returns ``{"p_fib", "zone", "thresholds_ma"}``. The continuous
    interpolation is what EG-0 practice does in spreadsheet form;
    AS/NZS 60479.1 itself only publishes the discrete zone boundaries.
    """
    if body_current_ma < 0:
        raise ValueError("body_current_ma must be ≥ 0")
    th = fibrillation_thresholds_ma(t_f_s)
    c1, c2, c3 = th["c1"], th["c2"], th["c3"]
    i = body_current_ma

    if i < c1:
        # Map AC-3 (below 5% fibrillation) to a small but non-zero value
        # so risk products don't collapse to 0 for borderline cases.
        # IEC AC-3 says strong reactions but no fibrillation for >95%.
        p = 0.005 * (i / c1) if c1 > 0 else 0.0
        zone = "AC-3"
    elif i < c2:
        frac = (i - c1) / (c2 - c1)
        p = 0.05 + frac * (0.50 - 0.05)
        zone = "AC-4.1"
    elif i < c3:
        frac = (i - c2) / (c3 - c2)
        p = 0.50 + frac * (0.90 - 0.50)
        zone = "AC-4.2"
    else:
        p = 0.95
        zone = "AC-4.3"

    return {"p_fib": p, "zone": zone, "thresholds_ma": th}


def coincidence_probability(
    n_fault_per_year: float,
    n_exposure_per_year: float,
    fault_duration_s: float,
    exposure_duration_s: float,
) -> float:
    """P_coincidence = N_f · N_e · (t_f + t_e), per year.

    Simplified Poisson form valid when t_f, t_e are small compared with
    the year. EG-0 uses this as the standard form for substation work.
    """
    if min(n_fault_per_year, n_exposure_per_year) < 0:
        raise ValueError("rates must be ≥ 0")
    if min(fault_duration_s, exposure_duration_s) < 0:
        raise ValueError("durations must be ≥ 0")
    seconds_per_year = 365.25 * 24.0 * 3600.0
    return (
        n_fault_per_year
        * n_exposure_per_year
        * (fault_duration_s + exposure_duration_s)
        / seconds_per_year
    )


# EG-0 / Australian risk-management bands — annual fatality probability.
# Aligned with broader ALARP practice (HSE et al.); EG-0's specific
# numbers track these.
RISK_INTOLERABLE = 1.0e-4
RISK_ACCEPTABLE = 1.0e-6


def alarp_band(p_fatality_per_year: float) -> dict:
    """Map a fatality probability to its EG-0 risk band.

    Returns ``{"band", "label", "action"}``.
      > 1e-4 → intolerable (must mitigate)
      1e-5 to 1e-4 → ALARP zone, action required (high)
      1e-6 to 1e-5 → ALARP zone, action recommended (low)
      < 1e-6 → broadly acceptable
    """
    if p_fatality_per_year < 0:
        raise ValueError("p_fatality must be ≥ 0")
    if p_fatality_per_year > RISK_INTOLERABLE:
        return {
            "band": "intolerable",
            "label": "Intolerable",
            "action": "Must mitigate before energising — risk exceeds 1e-4/year.",
        }
    if p_fatality_per_year > 1.0e-5:
        return {
            "band": "alarp_high",
            "label": "ALARP zone (high)",
            "action": "Mitigation required unless demonstrably impractical or grossly disproportionate in cost.",
        }
    if p_fatality_per_year > RISK_ACCEPTABLE:
        return {
            "band": "alarp_low",
            "label": "ALARP zone (low)",
            "action": "Mitigation recommended; document residual risk and ALARP justification.",
        }
    return {
        "band": "acceptable",
        "label": "Broadly acceptable",
        "action": "No further action required; design satisfies broadly-acceptable threshold.",
    }


def risk_assessment(
    touch_voltage_v: float,
    *,
    fault_duration_s: float,
    n_fault_per_year: float,
    n_exposure_per_year: float,
    exposure_duration_s: float,
    additional_resistance_ohm: float = 0.0,
) -> dict:
    """End-to-end EG-0 risk assessment for a single location.

    additional_resistance_ohm covers shoes, surface layer, gloves —
    anything in series with the body during contact. Bare-feet on
    bare soil = 0; rubber boots ≈ 1000 Ω; standing on 100 mm of dry
    crushed rock ≈ 1000-3000 Ω depending on resistivity.

    The touch voltage U_T is the symmetric-rms HIFREQ output. Do *not*
    pre-multiply by D_f for EG-0 work — the IEC curves already account
    for fault asymmetry.
    """
    if touch_voltage_v < 0:
        raise ValueError("touch_voltage_v must be ≥ 0")
    if additional_resistance_ohm < 0:
        raise ValueError("additional_resistance_ohm must be ≥ 0")

    z_b = body_impedance(touch_voltage_v)
    z_total = z_b + additional_resistance_ohm
    i_b_a = touch_voltage_v / z_total if z_total > 0 else 0.0
    i_b_ma = i_b_a * 1000.0

    fib = fibrillation_probability(i_b_ma, fault_duration_s)
    p_coin = coincidence_probability(
        n_fault_per_year, n_exposure_per_year,
        fault_duration_s, exposure_duration_s,
    )
    p_fatality = p_coin * fib["p_fib"]
    band = alarp_band(p_fatality)

    return {
        "touch_voltage_v": touch_voltage_v,
        "z_body_ohm": z_b,
        "additional_r_ohm": additional_resistance_ohm,
        "body_current_ma": i_b_ma,
        "p_fibrillation": fib["p_fib"],
        "iec_zone": fib["zone"],
        "thresholds_ma": fib["thresholds_ma"],
        "p_coincidence": p_coin,
        "p_fatality_per_year": p_fatality,
        "band": band["band"],
        "band_label": band["label"],
        "band_action": band["action"],
    }
