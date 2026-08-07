"""IEEE Std 80 tolerable touch/step voltages — pure closed forms.

Clean-room implementation of the published standard's closed-form equations
(IEEE Std 80, tolerable body-current limits + series body-circuit resistance).
Pure module: no model call, no I/O, no kernel import — unit-testable without
a daemon (tests/test_unit_trust_loop.py) and the sole compute path behind the
trust-loop app. The UI layer is not allowed to compute anything.

Symbols follow the standard:
    rho     native soil resistivity (ohm-m)
    rho_s   surface-layer resistivity (ohm-m), e.g. crushed rock
    h_s     surface-layer thickness (m)
    t_s     fault/shock duration (s)
    C_s     surface-layer derating factor (1.0 when no layer)
    k       body-current factor: 0.116 (50 kg) or 0.157 (70 kg)

    I_B      = k / sqrt(t_s)                      tolerable body current (A)
    E_touch  = (1000 + 1.5 * C_s * rho_s) * I_B   tolerable touch voltage (V)
    E_step   = (1000 + 6.0 * C_s * rho_s) * I_B   tolerable step voltage (V)

Anchor case (published in the standard, frozen as this app's conformance
gate): rho = 100 ohm-m, no surface layer, t_s = 0.5 s, 50 kg body ->
E_touch = 188.65 V, E_step = 262.48 V.
"""

from __future__ import annotations

import math

# Body-current factors from the standard's tolerable-current equations.
BODY_FACTOR = {50: 0.116, 70: 0.157}

# The body-current basis holds for shock durations in this window (s).
T_MIN_S = 0.03
T_MAX_S = 3.0


def surface_derating(rho: float, rho_s: float, h_s: float) -> float:
    """C_s — surface-layer derating factor (empirical closed form).

    With no surface layer (zero thickness, or layer resistivity equal to the
    native soil) the factor is exactly 1.0 and rho_s collapses to rho.
    """
    if h_s <= 0 or rho_s <= 0 or math.isclose(rho_s, rho, rel_tol=1e-9):
        return 1.0
    return 1.0 - (0.09 * (1.0 - rho / rho_s)) / (2.0 * h_s + 0.09)


def tolerable_voltages(
    *,
    rho: float,
    t_s: float,
    body_kg: int = 50,
    rho_s: float | None = None,
    h_s: float = 0.0,
) -> dict:
    """Tolerable touch/step voltages with every intermediate shown.

    Returns the working, not just the answers — the report an engineer can
    check line by line is the same dict the UI renders:

        {c_s, rho_surface, i_b_a, r_touch_ohm, r_step_ohm,
         e_touch_v, e_step_v, body_kg, t_s, warnings}

    Raises ValueError on inputs outside the standard's applicability window.
    """
    if rho <= 0:
        raise ValueError("soil resistivity rho must be positive (ohm-m)")
    if not (T_MIN_S <= t_s <= T_MAX_S):
        raise ValueError(
            f"shock duration t_s must be within {T_MIN_S}-{T_MAX_S} s "
            "(the standard's body-current basis)"
        )
    if body_kg not in BODY_FACTOR:
        raise ValueError("body_kg must be 50 or 70")

    has_layer = rho_s is not None and rho_s > 0 and h_s > 0
    rho_surface = float(rho_s) if has_layer else float(rho)
    c_s = surface_derating(rho, rho_surface, h_s) if has_layer else 1.0

    k = BODY_FACTOR[body_kg]
    i_b = k / math.sqrt(t_s)
    r_touch = 1000.0 + 1.5 * c_s * rho_surface
    r_step = 1000.0 + 6.0 * c_s * rho_surface

    warnings: list[str] = []
    if has_layer and rho_surface < rho:
        warnings.append(
            "surface layer is more conductive than the native soil — "
            "a protective layer is normally the more resistive material"
        )

    return {
        "c_s": round(c_s, 4),
        "rho_surface": round(rho_surface, 2),
        "i_b_a": round(i_b, 4),
        "r_touch_ohm": round(r_touch, 2),
        "r_step_ohm": round(r_step, 2),
        "e_touch_v": round(i_b * r_touch, 2),
        "e_step_v": round(i_b * r_step, 2),
        "body_kg": body_kg,
        "t_s": t_s,
        "warnings": warnings,
    }
