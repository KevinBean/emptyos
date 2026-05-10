"""IEEE 80 Annex C — analytical split-factor estimator.

The split factor S_f is the fraction of the total symmetrical fault
current that returns to the source through the local ground grid
(rather than through overhead shield wires, neutral conductors, or
underground cable sheaths). IEEE 80 §15 takes S_f as an input; Annex C
gives an analytical estimator from the substation's connecting lines.

Model
-----
Each line connecting the substation (transmission or distribution) is
treated as a semi-infinite ladder network of:

- Z_s — span self-impedance of the shield wire / neutral / sheath, Ω
  per span (already lumped over one tower-to-tower span).
- R_tf — equivalent tower-footing (or pole/pad) earthing resistance,
  Ω (per tower).

The driving-point impedance of one infinite ladder is the standard
half-rung continued-fraction limit:

    Z_inf = Z_s/2 + sqrt((Z_s/2)² + Z_s · R_tf)

For N identical parallel lines connected to the substation bus the
combined remote-earth path impedance is Z_lines = Z_inf / N. The fault
current then divides between the local grid (R_g) and the lines:

    S_f = |Z_lines / (Z_lines + R_g)|

Limits:

- R_g → 0 (perfect grid)        ⇒ S_f → 1   (all current via grid)
- R_g → ∞ (no grid)             ⇒ S_f → 0   (all current via lines)
- N → ∞ (many parallel lines)   ⇒ S_f → 0
- Z_s → 0 (perfect shield wire) ⇒ Z_inf → 0, S_f → 0

Inputs may be real or complex; the absolute value is taken at the end.

Reference: IEEE Std 80-2013, Annex C ("Equivalent impedance of
transmission line shield wires and distribution feeder neutrals").
"""

from __future__ import annotations

import cmath
from typing import Union

Number = Union[int, float, complex]


def infinite_line_impedance(z_span: Number, r_tower: Number) -> complex:
    """Driving-point impedance of one semi-infinite ladder line, Ω.

    ``Z_inf = Z_s/2 + sqrt((Z_s/2)² + Z_s · R_tf)``

    Parameters
    ----------
    z_span : Z_s, span self-impedance of shield/neutral conductor (Ω/span).
        Real or complex.
    r_tower : R_tf, tower-footing earthing resistance (Ω). Real positive.

    Raises
    ------
    ValueError if ``r_tower`` is non-positive or ``z_span`` is zero
    (degenerate ladder — the half-rung formula collapses).
    """
    z = complex(z_span)
    r = complex(r_tower)
    if z == 0:
        raise ValueError("z_span must be non-zero")
    if r.real <= 0:
        raise ValueError("r_tower must be positive")
    half = z / 2.0
    return half + cmath.sqrt(half * half + z * r)


def parallel_lines_impedance(
    z_span: Number, r_tower: Number, n_lines: int
) -> complex:
    """Combined driving-point impedance of N identical parallel lines, Ω.

    ``Z_lines = Z_inf(z_span, r_tower) / N``
    """
    if n_lines <= 0:
        raise ValueError("n_lines must be >= 1")
    return infinite_line_impedance(z_span, r_tower) / n_lines


def annex_c_split_factor(
    n_lines: int,
    z_span: Number,
    r_tower: Number,
    r_grid: float,
) -> float:
    """IEEE 80 Annex C analytical split factor S_f, dimensionless.

    ``S_f = |Z_lines / (Z_lines + R_g)|`` with ``Z_lines`` from the
    parallel infinite-ladder model.

    Parameters
    ----------
    n_lines : count of identical parallel transmission/distribution
        lines bonded to the substation bus.
    z_span : Z_s, shield-wire span self-impedance (Ω/span). Real or
        complex.
    r_tower : R_tf, equivalent tower-footing resistance (Ω). Real
        positive.
    r_grid : R_g, substation grid resistance to remote earth (Ω). Real
        positive.

    Returns
    -------
    Float in (0, 1].
    """
    if r_grid <= 0:
        raise ValueError("r_grid must be positive")
    z_lines = parallel_lines_impedance(z_span, r_tower, n_lines)
    s_f = abs(z_lines / (z_lines + r_grid))
    # Numerical hygiene — clamp tiny overshoots from complex arithmetic.
    if s_f > 1.0:
        s_f = 1.0
    return s_f


def estimate_split_factor(
    *,
    n_transmission: int = 0,
    n_distribution: int = 0,
    z_span_transmission: Number = complex(0.4, 1.5),
    z_span_distribution: Number = complex(0.6, 0.8),
    r_tower_transmission: float = 15.0,
    r_tower_distribution: float = 25.0,
    r_grid: float,
) -> dict:
    """Mixed transmission + distribution estimator.

    Substations typically have N_t transmission lines and N_d
    distribution feeders with different shield/neutral characteristics.
    Their driving-point impedances combine in parallel:

        1/Z_lines = N_t / Z_inf,t  +  N_d / Z_inf,d

    Then ``S_f = |Z_lines / (Z_lines + R_g)|``.

    Defaults are typical IEEE 80 Annex C example values — callers
    designing for a specific site should pass measured/estimated
    impedances.

    Returns ``{"s_f": float, "z_inf_t": complex|None, "z_inf_d":
    complex|None, "z_lines": complex}``.
    """
    if r_grid <= 0:
        raise ValueError("r_grid must be positive")
    if n_transmission < 0 or n_distribution < 0:
        raise ValueError("line counts must be non-negative")
    if n_transmission + n_distribution <= 0:
        raise ValueError("need at least one transmission or distribution line")

    z_inf_t = (
        infinite_line_impedance(z_span_transmission, r_tower_transmission)
        if n_transmission > 0
        else None
    )
    z_inf_d = (
        infinite_line_impedance(z_span_distribution, r_tower_distribution)
        if n_distribution > 0
        else None
    )

    inv_z = complex(0.0)
    if z_inf_t is not None:
        inv_z += n_transmission / z_inf_t
    if z_inf_d is not None:
        inv_z += n_distribution / z_inf_d
    z_lines = 1.0 / inv_z

    s_f = abs(z_lines / (z_lines + r_grid))
    if s_f > 1.0:
        s_f = 1.0

    return {
        "s_f": s_f,
        "z_inf_transmission": z_inf_t,
        "z_inf_distribution": z_inf_d,
        "z_lines": z_lines,
    }
