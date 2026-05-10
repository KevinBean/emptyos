"""IEEE Std 80-2013 §15.10 — Decrement factor D_f.

Eq. 84 — RMS-equivalent multiplier that converts the symmetric AC fault
current into the as-if-symmetric current that delivers the same I²t
energy as the real asymmetric pulse (AC + DC offset that decays with
time constant T_a = X/(ωR)).

    D_f = √[1 + (T_a / t_f) · (1 − exp(−2·t_f / T_a))]

The DC offset matters most for fast clearing in inductive systems
(transformers, generators). The standard's rule of thumb: skip D_f
(use 1.0) when t_f ≥ 1 s (DC has decayed) or X/R < 5 (small offset
to begin with).
"""

from __future__ import annotations

import math

# IEEE 80 §15.10 — drop D_f below these thresholds. Documented for
# downstream callers that want to surface "skipped, not 1.0 by accident".
SKIP_T_F_S = 1.0
SKIP_X_OVER_R = 5.0


def decrement_factor(
    x_over_r: float,
    fault_duration_s: float,
    *,
    freq_hz: float = 50.0,
) -> dict:
    """Compute D_f from system X/R and fault duration.

    Returns a dict with the factor plus the time constant T_a and a
    ``skipped`` flag that's True when one of the IEEE 80 skip rules
    applies (caller should still pass the returned ``d_f`` = 1.0 — the
    flag is for audit output, not control flow).
    """
    if x_over_r < 0:
        raise ValueError("x_over_r must be ≥ 0")
    if fault_duration_s <= 0:
        raise ValueError("fault_duration_s must be > 0")
    if freq_hz <= 0:
        raise ValueError("freq_hz must be > 0")

    omega = 2.0 * math.pi * freq_hz
    # T_a = L/R = X/(ωR) = (X/R)/ω
    t_a = x_over_r / omega if omega > 0 else 0.0

    if fault_duration_s >= SKIP_T_F_S or x_over_r < SKIP_X_OVER_R:
        return {
            "d_f": 1.0,
            "t_a_s": t_a,
            "skipped": True,
            "reason": (
                "t_f ≥ 1 s — DC offset has decayed"
                if fault_duration_s >= SKIP_T_F_S
                else f"X/R < {SKIP_X_OVER_R} — DC offset small to begin with"
            ),
        }

    if t_a <= 0:
        return {"d_f": 1.0, "t_a_s": 0.0, "skipped": True, "reason": "T_a = 0"}

    inner = 1.0 - math.exp(-2.0 * fault_duration_s / t_a)
    d_f = math.sqrt(1.0 + (t_a / fault_duration_s) * inner)
    return {"d_f": d_f, "t_a_s": t_a, "skipped": False, "reason": ""}


def projection_factor(
    present_3i0_a: float,
    future_3i0_a: float,
) -> dict:
    """C_p — IEEE 80 §15.11 projection factor for future system growth.

    C_p = I_F_future / I_F_present

    A simple ratio. Caller can also pass C_p directly as a kwarg in the
    upstream API; this helper exists so a UI form can derive C_p from
    an explicit "future fault level" input from system planning.
    """
    if present_3i0_a <= 0:
        raise ValueError("present_3i0_a must be > 0")
    if future_3i0_a < present_3i0_a:
        raise ValueError("future_3i0_a must be ≥ present_3i0_a (no shrinking)")
    return {
        "c_p": future_3i0_a / present_3i0_a,
        "present_3i0_a": present_3i0_a,
        "future_3i0_a": future_3i0_a,
    }
