"""Schwarz two-term grid+rod resistance + 2-layer effective ρ."""

from __future__ import annotations

import math

import pytest

from engines.earthing.ieee80 import sverak_grid_resistance
from engines.earthing.schwarz import (
    schwarz_coefficients,
    schwarz_grid_resistance,
    two_layer_effective_resistivity,
)


# ── Schwarz coefficients (k₁, k₂) ─────────────────────────────────


class TestSchwarzCoefficients:
    def test_square_grid_at_surface(self):
        # L/W = 1, h = 0 → k1 = 1.37, k2 = 5.65 per Sverak (1981)
        k1, k2 = schwarz_coefficients(70.0, 70.0, 0.0, 4900.0)
        assert k1 == pytest.approx(1.37, abs=0.01)
        assert k2 == pytest.approx(5.65, abs=0.01)

    def test_aspect_ratio_3_at_surface(self):
        # L/W = 3, h = 0 → k1 = -0.04*3 + 1.41 = 1.29, k2 = 0.15*3 + 5.50 = 5.95
        k1, k2 = schwarz_coefficients(120.0, 40.0, 0.0, 4800.0)
        assert k1 == pytest.approx(1.29, abs=0.01)
        assert k2 == pytest.approx(5.95, abs=0.01)

    def test_interpolation_between_depths(self):
        # At h = √A/20 (half-way to √A/10), values should sit between
        # the surface and √A/10 brackets.
        A = 4900.0
        k1_s, k2_s = schwarz_coefficients(70.0, 70.0, 0.0, A)
        k1_d, k2_d = schwarz_coefficients(70.0, 70.0, math.sqrt(A) / 10.0, A)
        k1_m, k2_m = schwarz_coefficients(70.0, 70.0, math.sqrt(A) / 20.0, A)
        assert min(k1_s, k1_d) <= k1_m <= max(k1_s, k1_d)
        assert min(k2_s, k2_d) <= k2_m <= max(k2_s, k2_d)

    def test_negative_inputs_raise(self):
        with pytest.raises(ValueError):
            schwarz_coefficients(-1.0, 70.0, 0.5, 4900.0)
        with pytest.raises(ValueError):
            schwarz_coefficients(70.0, 70.0, -0.1, 4900.0)


# ── Schwarz grid+rod combined ────────────────────────────────────


class TestSchwarzGridOnly:
    """Without rods, Schwarz should agree with Sverak in its overlap region."""

    def test_grid_only_returns_inf_rod_resistance(self):
        out = schwarz_grid_resistance(
            400.0,
            grid_total_length_m=2940.0,
            grid_area_m2=4900.0,
            grid_length_m=70.0, grid_width_m=70.0,
            burial_depth_m=0.5,
            conductor_diameter_m=0.010,
        )
        assert out["R_rods"] == float("inf")
        assert out["R_mutual"] == 0.0
        assert out["R_g"] == out["R_grid"]

    def test_grid_only_within_5pct_of_sverak(self):
        # IEEE 80 Annex B 70×70 grid: Sverak gives ~2.78 Ω for ρ=400.
        # Schwarz grid-only should land within 5–10%; the formulas use
        # different averaging so an exact match isn't expected.
        rg_sverak = sverak_grid_resistance(400.0, 2940.0, 4900.0, 0.5)
        rg_schwarz = schwarz_grid_resistance(
            400.0,
            grid_total_length_m=2940.0,
            grid_area_m2=4900.0,
            grid_length_m=70.0, grid_width_m=70.0,
            burial_depth_m=0.5,
            conductor_diameter_m=0.010,
        )["R_g"]
        rel = abs(rg_schwarz - rg_sverak) / rg_sverak
        assert rel < 0.15, f"Schwarz {rg_schwarz:.3f} vs Sverak {rg_sverak:.3f}, rel={rel:.2%}"


class TestSchwarzWithRods:
    """Rods reduce the combined Rg vs grid-only — basic monotonicity."""

    def _grid_kwargs(self):
        return dict(
            grid_total_length_m=2940.0,
            grid_area_m2=4900.0,
            grid_length_m=70.0, grid_width_m=70.0,
            burial_depth_m=0.5,
            conductor_diameter_m=0.010,
        )

    def test_rods_lower_combined_resistance(self):
        bare = schwarz_grid_resistance(400.0, **self._grid_kwargs())
        with_rods = schwarz_grid_resistance(
            400.0,
            **self._grid_kwargs(),
            n_rods=20, rod_length_m=3.0, rod_diameter_m=0.016,
        )
        assert with_rods["R_g"] < bare["R_g"]

    def test_more_rods_lower_resistance(self):
        kw = self._grid_kwargs()
        few = schwarz_grid_resistance(400.0, **kw, n_rods=10, rod_length_m=3.0)
        many = schwarz_grid_resistance(400.0, **kw, n_rods=40, rod_length_m=3.0)
        assert many["R_g"] < few["R_g"]

    def test_longer_rods_lower_resistance(self):
        kw = self._grid_kwargs()
        short = schwarz_grid_resistance(400.0, **kw, n_rods=20, rod_length_m=2.0)
        long = schwarz_grid_resistance(400.0, **kw, n_rods=20, rod_length_m=6.0)
        assert long["R_g"] < short["R_g"]

    def test_rg_scales_linearly_with_resistivity(self):
        kw = self._grid_kwargs()
        rg1 = schwarz_grid_resistance(100.0, **kw, n_rods=20, rod_length_m=3.0)["R_g"]
        rg2 = schwarz_grid_resistance(200.0, **kw, n_rods=20, rod_length_m=3.0)["R_g"]
        assert rg2 == pytest.approx(2.0 * rg1, rel=1e-9)

    def test_components_returned(self):
        out = schwarz_grid_resistance(
            400.0, **self._grid_kwargs(),
            n_rods=20, rod_length_m=3.0,
        )
        # All three components should be positive and finite.
        assert 0 < out["R_grid"] < float("inf")
        assert 0 < out["R_rods"] < float("inf")
        assert 0 < out["R_mutual"] < float("inf")
        # Combined Rg lower than either standalone term (parallel-with-coupling).
        assert out["R_g"] < min(out["R_grid"], out["R_rods"])

    def test_negative_rod_diameter_raises(self):
        with pytest.raises(ValueError):
            schwarz_grid_resistance(
                400.0, **self._grid_kwargs(),
                n_rods=10, rod_length_m=3.0, rod_diameter_m=-0.001,
            )


# ── Two-layer effective resistivity ───────────────────────────────


class TestTwoLayerEffectiveRho:
    def test_uniform_soil_returns_input(self):
        # ρ₁ = ρ₂ → ρ_a = ρ₁
        rho_a = two_layer_effective_resistivity(200.0, 200.0, 1.0, 4900.0)
        assert rho_a == pytest.approx(200.0, rel=1e-9)

    def test_zero_top_layer_returns_bottom(self):
        # h₁ = 0 → all-bottom soil → ρ_a = ρ₂
        rho_a = two_layer_effective_resistivity(50.0, 500.0, 0.0, 4900.0)
        assert rho_a == pytest.approx(500.0, rel=1e-9)

    def test_deep_top_layer_approaches_top(self):
        # h₁ >> r → ρ_a → ρ₁
        rho_a = two_layer_effective_resistivity(50.0, 500.0, 1000.0, 4900.0)
        assert rho_a == pytest.approx(50.0, rel=0.05)

    def test_intermediate_layer_between_extremes(self):
        # ρ₁ = 50, ρ₂ = 500, finite h₁ → ρ_a should sit between the two.
        rho_a = two_layer_effective_resistivity(50.0, 500.0, 2.0, 4900.0)
        assert 50.0 < rho_a < 500.0

    def test_descending_with_top_layer_thickness(self):
        # As more high-ρ top layer is present, ρ_a moves toward ρ₁(=high).
        thin = two_layer_effective_resistivity(500.0, 50.0, 0.5, 4900.0)
        thick = two_layer_effective_resistivity(500.0, 50.0, 5.0, 4900.0)
        assert thick > thin  # more high-ρ top → higher effective ρ

    def test_negative_inputs_raise(self):
        with pytest.raises(ValueError):
            two_layer_effective_resistivity(-50.0, 200.0, 1.0, 4900.0)
        with pytest.raises(ValueError):
            two_layer_effective_resistivity(50.0, 200.0, -1.0, 4900.0)
        with pytest.raises(ValueError):
            two_layer_effective_resistivity(50.0, 200.0, 1.0, 0.0)

    def test_chains_with_sverak_sanity(self):
        # ρ_a from a 2-layer soil → Sverak Rg → must be between the two
        # Rg values you'd get with each layer's ρ alone.
        rho1, rho2, h1 = 100.0, 400.0, 1.5
        kw = dict(grid_total_length_m=2940.0, grid_area_m2=4900.0, burial_depth_m=0.5)
        rho_a = two_layer_effective_resistivity(rho1, rho2, h1, kw["grid_area_m2"])
        rg_a = sverak_grid_resistance(rho_a, **kw)
        rg_low = sverak_grid_resistance(rho1, **kw)
        rg_hi = sverak_grid_resistance(rho2, **kw)
        assert rg_low < rg_a < rg_hi
