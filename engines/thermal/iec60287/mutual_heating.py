"""Inter-cable mutual heating Δθ_P — IEC 60287-2-1 §4.2.3.2.

For groups of cables with **unequal losses per cable** (e.g. flat
solid-bonded with per-cable λ₁), the standard rating equation gains an
extra term Δθ_P in the numerator that subtracts the temperature rise at
the rated cable due to heat from its neighbours, computed via the
method of images:

    Δθ_P,p = Σ_{k≠p} F_kp · W_k

    F_kp = (ρ_T / 2π) · ln(d'_kp / d_kp)

where d_kp is the direct distance from cable k to rated cable p, and
d'_kp is the distance from k's mirror image (across the ground surface
at depth 0) to p.

The :func:`mutual_heating_coefficients` function takes arbitrary cable
positions ``(x, depth)`` and works for any formation — flat-N, trefoil,
multi-circuit arrays, asymmetric layouts. Convenience builders
:func:`flat_positions` and :func:`trefoil_positions` produce the
position dicts for the two common cases.

The original ``flat_3_geometry_factors`` and
``delta_theta_p_coefficients_flat_3`` entry points are kept as thin
wrappers over the generic implementation so existing callers
(``core.py``) don't need to change.

Cross-check: TB 880 Case 0-3 (flat 3 PVC ducts in concrete, s=165mm,
L=1000mm, ρ_backfill=0.8): F_lag-mid = F_lead-mid = 0.318,
F_lead-lag = 0.231 K·m/W, giving Δθ_P,lag ≈ 17 K — closes the case-4
strict residual.
"""

from __future__ import annotations

import math


# ── Core: arbitrary positions ─────────────────────────────────────


def _pair_F(rho_t: float, d_direct: float, d_image: float) -> float:
    """Geometry factor F_kp = (ρ_T / 2π) · ln(d'_kp / d_kp)."""
    if d_direct <= 0 or d_image <= 0:
        return 0.0
    return (rho_t / (2.0 * math.pi)) * math.log(d_image / d_direct)


def geometry_factors(
    positions: dict[str, tuple[float, float]],
    rho_t: float,
) -> dict[str, dict[str, float]]:
    """Pair-wise geometry factors F_kp for arbitrary cable positions.

    Parameters
    ----------
    positions : ``{cable_id: (x, depth)}`` — depth is the **positive**
        burial depth below the ground surface, in metres. The image of
        cable *k* is automatically placed at ``(x_k, -depth_k)``.
    rho_t : thermal resistivity of the medium between cables (K·m/W).

    Returns
    -------
    Nested dict ``factors[rated_id][source_id] = F_kp`` for every
    ordered pair of distinct cables. Self-pairs are excluded (self
    heating is in T4, not Δθ_P).

    Raises
    ------
    ValueError if any cable is at non-positive depth (above ground —
    out of model scope) or if two cables share the same position.
    """
    for cid, (x, y) in positions.items():
        if y <= 0:
            raise ValueError(
                f"cable '{cid}' at depth {y} m — depths must be positive"
            )

    out: dict[str, dict[str, float]] = {cid: {} for cid in positions}
    ids = list(positions.keys())
    for i, p_id in enumerate(ids):
        x_p, y_p = positions[p_id]
        for k_id in ids:
            if k_id == p_id:
                continue
            x_k, y_k = positions[k_id]
            dx = x_k - x_p
            d_direct = math.hypot(dx, y_k - y_p)
            d_image = math.hypot(dx, y_k + y_p)
            if d_direct <= 0:
                raise ValueError(
                    f"cables '{p_id}' and '{k_id}' share position {(x_k, y_k)}"
                )
            out[p_id][k_id] = _pair_F(rho_t, d_direct, d_image)
    return out


def mutual_heating_coefficients(
    positions: dict[str, tuple[float, float]],
    rho_t: float,
    R_ac: float,
    lambda1_per_cable: dict[str, float],
    W_d: float,
) -> dict[str, tuple[float, float]]:
    """Per-cable (a_p, b_p) such that ``Δθ_P,p(I) = a_p · I² + b_p``.

    Substituting ``W_k = I² · R_ac · (1 + λ1_k) + W_d`` into
    ``Δθ_P,p = Σ_{k≠p} F_kp · W_k`` collects an I²-coefficient (a_p)
    and a constant (b_p), letting the caller solve the rating equation
    in closed form: ``I² · (denom_R + a_p) = numerator_T - b_p``.

    Parameters mirror :func:`geometry_factors` plus the loss inputs.
    ``lambda1_per_cable`` must contain a key for every cable_id in
    ``positions``.

    Returns
    -------
    ``{cable_id: (a_p, b_p)}`` for every cable in ``positions``.

    Raises
    ------
    ValueError on missing λ₁ keys.
    """
    F = geometry_factors(positions, rho_t)
    out: dict[str, tuple[float, float]] = {}
    for rated, neighbours in F.items():
        a = 0.0
        b = 0.0
        for src, F_kp in neighbours.items():
            if src not in lambda1_per_cable:
                raise ValueError(f"lambda1_per_cable missing key '{src}'")
            lam_k = lambda1_per_cable[src]
            a += F_kp * R_ac * (1.0 + lam_k)
            b += F_kp * W_d
        out[rated] = (a, b)
    return out


# ── Position builders ─────────────────────────────────────────────


def flat_positions(
    n_cables: int,
    spacing_m: float,
    burial_depth_m: float,
    ids: list[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Cable positions for a horizontal flat formation of N cables.

    Cables are centred on x=0 with constant spacing ``s``. For the
    canonical 3-cable case the IDs default to ``("lag", "mid", "lead")``
    so the result is interchangeable with :func:`flat_3_geometry_factors`.
    For other N the IDs default to ``c1``, ``c2``, ….
    """
    if n_cables < 1:
        raise ValueError("n_cables must be >= 1")
    if spacing_m <= 0 or burial_depth_m <= 0:
        raise ValueError("spacing_m and burial_depth_m must be positive")

    if ids is None:
        if n_cables == 3:
            ids = ["lag", "mid", "lead"]
        else:
            ids = [f"c{i+1}" for i in range(n_cables)]
    if len(ids) != n_cables:
        raise ValueError(f"need {n_cables} ids, got {len(ids)}")

    # Centre on x=0: positions are (i - (n-1)/2) · s
    offset = (n_cables - 1) / 2.0
    return {
        ids[i]: ((i - offset) * spacing_m, burial_depth_m)
        for i in range(n_cables)
    }


def trefoil_positions(
    spacing_m: float,
    burial_depth_m: float,
    orientation: str = "point_up",
    ids: tuple[str, str, str] = ("a", "b", "c"),
) -> dict[str, tuple[float, float]]:
    """Cable positions for an equilateral-triangle trefoil.

    ``spacing_m`` is the centre-to-centre distance between any two
    cables (equal on all three sides). The triangle is centred on x=0
    at the requested burial depth.

    Orientations:

    - ``"point_up"`` — one cable above two (apex above centroid). Used
      when phases are stacked vertically.
    - ``"point_down"`` — two above one. Common when laid in a trench
      with the third cable below the pair.

    Cables are equidistant by construction → mutual-heating factors are
    symmetric, so ``Δθ_P`` is the same for all three when the per-cable
    losses are equal. The function still returns three distinct
    positions so callers that genuinely have unequal losses (e.g. mixed
    sheath bonding) can use the generic coefficients.
    """
    if spacing_m <= 0 or burial_depth_m <= 0:
        raise ValueError("spacing_m and burial_depth_m must be positive")
    if orientation not in ("point_up", "point_down"):
        raise ValueError("orientation must be 'point_up' or 'point_down'")
    if len(ids) != 3:
        raise ValueError("trefoil needs exactly 3 ids")

    s = spacing_m
    L = burial_depth_m
    # Centroid-to-vertex distance for equilateral triangle of side s.
    r = s / math.sqrt(3.0)
    # Half-side and apex offsets.
    half = s / 2.0
    apex = r           # vertical distance centroid → apex
    base = r / 2.0     # vertical distance centroid → base midpoint

    if orientation == "point_up":
        # apex above centroid → smaller depth (closer to surface).
        return {
            ids[0]: (-half, L + base),
            ids[1]: ( half, L + base),
            ids[2]: (  0.0, L - apex),
        }
    # point_down: apex below centroid → larger depth.
    return {
        ids[0]: (-half, L - base),
        ids[1]: ( half, L - base),
        ids[2]: (  0.0, L + apex),
    }


# ── Backward-compatible flat-3 wrappers ────────────────────────────


def flat_3_geometry_factors(
    spacing_m: float,
    burial_depth_m: float,
    rho_t: float,
) -> dict[str, dict[str, float]]:
    """Geometry factors for a flat 3-cable formation (lag/mid/lead).

    Thin wrapper over :func:`geometry_factors` preserved for
    backward compatibility. New code should call the generic API.
    """
    if spacing_m <= 0 or burial_depth_m <= 0:
        return {"lag": {}, "mid": {}, "lead": {}}
    positions = flat_positions(3, spacing_m, burial_depth_m)
    return geometry_factors(positions, rho_t)


def delta_theta_p_coefficients_flat_3(
    spacing_m: float,
    burial_depth_m: float,
    rho_t: float,
    R_ac: float,
    lambda1_per_cable: dict[str, float],
    W_d: float,
) -> dict[str, tuple[float, float]]:
    """(a_p, b_p) coefficients for a flat 3-cable formation.

    Thin wrapper over :func:`mutual_heating_coefficients` preserved for
    backward compatibility. New code should call the generic API.
    """
    positions = flat_positions(3, spacing_m, burial_depth_m)
    return mutual_heating_coefficients(
        positions, rho_t, R_ac, lambda1_per_cable, W_d,
    )
