"""Kennelly image-method sanity + cross-check vs IEC 60287 T4."""

from __future__ import annotations

import math

from engines.thermal.kennelly import (
    superposed_rise_at,
    temperature_grid,
    temperature_rise_at,
)
from engines.thermal.iec60287.installation_types import t4_direct_buried_single


class TestKennellyAnalytical:
    def test_zero_at_surface(self):
        # On the surface (y=0) directly above cable, d_real = d_image = h
        # → ln(d_image/d_real) = ln(1) = 0
        d = temperature_rise_at(0.0, 0.0, burial_depth_m=1.0,
                                losses_w_per_m=20.0,
                                soil_thermal_resistivity_kmw=1.0)
        assert abs(d) < 1e-12

    def test_symmetric_in_x(self):
        # Field is symmetric about x=0
        a = temperature_rise_at(+0.5, -0.8, 1.0, 20.0, 1.0)
        b = temperature_rise_at(-0.5, -0.8, 1.0, 20.0, 1.0)
        assert abs(a - b) < 1e-12

    def test_decays_with_distance(self):
        # Far away (deep) → small rise
        near = temperature_rise_at(0.0, -1.05, 1.0, 20.0, 1.0)  # 5cm below cable
        far = temperature_rise_at(5.0, -1.0, 1.0, 20.0, 1.0)
        assert near > far > 0

    def test_higher_resistivity_higher_rise(self):
        a = temperature_rise_at(0.0, -0.5, 1.0, 20.0, soil_thermal_resistivity_kmw=0.7)
        b = temperature_rise_at(0.0, -0.5, 1.0, 20.0, soil_thermal_resistivity_kmw=2.5)
        assert b > a > 0


class TestKennellyVsIEC60287T4:
    """Kennelly evaluated at the cable surface gives the surface-rise
    coefficient; IEC 60287 T4 is the same coefficient cast as a
    thermal resistance per metre. Cross-check that they agree.

    The two formulas are equivalent in the limit u = 2L/D_e >> 1:
        T4_iec = (ρ_T / 2π) · ln(2L/D_e)
        ΔT_kennelly_at_surface = (W·ρ_T / 2π) · ln(d_image / d_real)
    With the field point at the cable surface directly above (y = -L + r):
        d_real = r,  d_image = 2L - r  ≈ 2L
        ΔT/W = (ρ_T / 2π) · ln(2L / r) = (ρ_T / 2π) · ln(2·(2L/D_e))
    so Kennelly at surface gives T4 + ρ_T/(2π)·ln(2). Off by a constant
    log(2) which the full IEC form (ln(u + sqrt(u²-1))) absorbs.
    """

    def test_coefficient_form_matches(self):
        L = 1.0
        D_e = 0.06
        r = D_e / 2
        rho = 1.0
        W = 1.0  # unit losses → ΔT/W = thermal resistance

        # Kennelly evaluated on the cable surface, vertically above the cable
        delta_kennelly = temperature_rise_at(0.0, -L + r, L, W, rho)

        # IEC 60287 single-buried T4
        t4 = t4_direct_buried_single(rho, L, D_e)

        # The two should agree to within ρ/(2π)·ln(2) ≈ 0.110, which is
        # the difference between the simple-image form and the full
        # u + sqrt(u² - 1) form. Loosely: same magnitude.
        assert abs(delta_kennelly - t4) < 0.15
        assert delta_kennelly > 0
        assert t4 > 0


class TestSuperposition:
    def test_two_cables_additive(self):
        a = temperature_rise_at(0.0, -0.5, 1.0, 20.0, 1.0)
        b = temperature_rise_at(-0.3, -0.5, 1.0, 20.0, 1.0)
        s = superposed_rise_at(
            0.0, -0.5,
            cables=[
                {"x": 0.0, "depth": 1.0, "losses_w_per_m": 20.0},
                {"x": 0.3, "depth": 1.0, "losses_w_per_m": 20.0},
            ],
            soil_thermal_resistivity_kmw=1.0,
        )
        # The second cable contributes at (0,-0.5) what cable-at-(-0.3, -1.0)
        # contributes when the field point is at (0, -0.5) — equivalently
        # what cable-at-(0,-1.0) contributes at point (-0.3, -0.5):
        b_translated = temperature_rise_at(0.3, -0.5, 1.0, 20.0, 1.0)
        assert abs(s - (a + b_translated)) < 1e-9


class TestGrid:
    def test_grid_shape_and_zero_at_surface(self):
        g = temperature_grid(1.0, 20.0, 1.0, x_range=(-1, 1), y_range=(-2, 0), nx=11, ny=5)
        assert len(g.xs) == 11
        assert len(g.ys) == 5
        assert len(g.temps) == 5
        assert len(g.temps[0]) == 11
        # surface row (y = 0) → all zero rise
        surface_idx = g.ys.index(0.0)
        assert all(abs(d) < 1e-12 for d in g.delta[surface_idx])
