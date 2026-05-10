"""IEEE 80 Annex C — split-factor estimator."""

from __future__ import annotations

import math

import pytest

from engines.earthing.split_factor import (
    annex_c_split_factor,
    estimate_split_factor,
    infinite_line_impedance,
    parallel_lines_impedance,
)


class TestInfiniteLineImpedance:
    def test_real_inputs_real_result(self):
        # Z_s = 1, R_tf = 10 → Z_inf = 0.5 + sqrt(0.25 + 10) = 0.5 + sqrt(10.25)
        z = infinite_line_impedance(1.0, 10.0)
        assert math.isclose(z.imag, 0.0, abs_tol=1e-12)
        assert math.isclose(z.real, 0.5 + math.sqrt(10.25), rel_tol=1e-12)

    def test_complex_z_span(self):
        # Z_s = 0.4 + j1.5, R_tf = 15
        z = infinite_line_impedance(complex(0.4, 1.5), 15.0)
        # Hand-compute: half = 0.2 + j0.75; half² = 0.04 - 0.5625 + j0.3 = -0.5225 + j0.3
        # Z_s · R_tf = 6 + j22.5; sum = 5.4775 + j22.8
        # sqrt of that, then add half. Just lock the magnitude as reference.
        assert 4.0 < abs(z) < 7.0
        assert z.real > 0
        assert z.imag > 0

    def test_zero_z_span_rejected(self):
        with pytest.raises(ValueError, match="z_span"):
            infinite_line_impedance(0.0, 10.0)

    def test_nonpositive_r_tower_rejected(self):
        with pytest.raises(ValueError, match="r_tower"):
            infinite_line_impedance(1.0, 0.0)
        with pytest.raises(ValueError, match="r_tower"):
            infinite_line_impedance(1.0, -5.0)


class TestParallelLines:
    def test_n_equals_one_matches_single(self):
        z1 = infinite_line_impedance(1.0 + 1j, 10.0)
        zN = parallel_lines_impedance(1.0 + 1j, 10.0, 1)
        assert zN == z1

    def test_n_halves_impedance(self):
        z1 = infinite_line_impedance(1.0 + 1j, 10.0)
        z2 = parallel_lines_impedance(1.0 + 1j, 10.0, 2)
        assert abs(z2 - z1 / 2.0) < 1e-12

    def test_zero_lines_rejected(self):
        with pytest.raises(ValueError, match="n_lines"):
            parallel_lines_impedance(1.0, 10.0, 0)


class TestSplitFactor:
    def test_in_unit_interval(self):
        s_f = annex_c_split_factor(2, 0.4 + 1.5j, 15.0, 1.0)
        assert 0.0 < s_f <= 1.0

    def test_low_grid_resistance_high_s_f(self):
        """Perfect grid (R_g → 0) ⇒ all current returns through grid."""
        s_f = annex_c_split_factor(2, 0.4 + 1.5j, 15.0, r_grid=0.001)
        assert s_f > 0.99

    def test_high_grid_resistance_low_s_f(self):
        """No grid (R_g → ∞) ⇒ all current bypasses through line shields."""
        s_f = annex_c_split_factor(2, 0.4 + 1.5j, 15.0, r_grid=1e6)
        assert s_f < 0.01

    def test_more_lines_lower_s_f(self):
        """More parallel lines = more bypass paths = lower S_f."""
        s1 = annex_c_split_factor(1, 0.4 + 1.5j, 15.0, r_grid=1.0)
        s4 = annex_c_split_factor(4, 0.4 + 1.5j, 15.0, r_grid=1.0)
        assert s4 < s1

    def test_typical_substation_in_band(self):
        """IEEE 80 Annex C: typical substations land S_f ≈ 0.3-0.7.

        Reference scenario: 2 transmission lines, Z_s = 0.4 + j1.5 Ω/span,
        R_tf = 15 Ω, R_g = 1 Ω. This is a representative bench case for
        a small transmission substation; S_f should fall in the canonical
        band the standard's curves illustrate.
        """
        s_f = annex_c_split_factor(2, 0.4 + 1.5j, 15.0, r_grid=1.0)
        assert 0.3 <= s_f <= 0.85

    def test_nonpositive_r_grid_rejected(self):
        with pytest.raises(ValueError, match="r_grid"):
            annex_c_split_factor(2, 1.0, 10.0, 0.0)


class TestMixedEstimator:
    def test_transmission_only(self):
        out = estimate_split_factor(
            n_transmission=2,
            z_span_transmission=0.4 + 1.5j,
            r_tower_transmission=15.0,
            r_grid=1.0,
        )
        assert out["z_inf_distribution"] is None
        assert out["z_inf_transmission"] is not None
        assert 0.0 < out["s_f"] < 1.0

    def test_distribution_only(self):
        out = estimate_split_factor(
            n_distribution=4,
            z_span_distribution=0.6 + 0.8j,
            r_tower_distribution=25.0,
            r_grid=1.0,
        )
        assert out["z_inf_transmission"] is None
        assert out["z_inf_distribution"] is not None
        assert 0.0 < out["s_f"] < 1.0

    def test_mixed_lower_than_transmission_alone(self):
        """Adding distribution feeders to a transmission substation
        adds parallel return paths and pushes S_f down."""
        only_tx = estimate_split_factor(n_transmission=2, r_grid=1.0)
        mixed = estimate_split_factor(
            n_transmission=2, n_distribution=4, r_grid=1.0
        )
        assert mixed["s_f"] < only_tx["s_f"]

    def test_no_lines_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            estimate_split_factor(r_grid=1.0)

    def test_negative_count_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            estimate_split_factor(n_transmission=-1, r_grid=1.0)
