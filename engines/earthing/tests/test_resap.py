"""Wenner / RESAP — forward model + 2-layer fit."""

from __future__ import annotations

import math

import pytest

from engines.earthing.resap import (
    apparent_resistivity_homogeneous,
    apparent_resistivity_two_layer,
    fit_two_layer_grid_search,
    wenner_resistance_to_apparent_rho,
)


# ── Wenner basics ───────────────────────────────────────────────


def test_wenner_resistance_to_rho_canonical():
    """ρ_a = 2π·a·R; a=10m, R=1.59 Ω → ρ_a ≈ 100 Ω·m"""
    rho = wenner_resistance_to_apparent_rho(spacing_m=10.0, resistance_ohm=1.5915)
    assert rho == pytest.approx(100.0, rel=1e-3)


def test_homogeneous_returns_constant():
    rho = apparent_resistivity_homogeneous(150.0, [1, 2, 5, 10, 20, 50])
    assert rho == [150.0] * 6


# ── Two-layer forward model ────────────────────────────────────


class TestTwoLayerForward:
    def test_uniform_when_rho1_equals_rho2(self):
        """Degenerate case: ρ₁ = ρ₂ → 2-layer reduces to homogeneous."""
        spacings = [1.0, 5.0, 20.0, 100.0]
        out = apparent_resistivity_two_layer(100.0, 100.0, 2.0, spacings)
        for v in out:
            assert v == pytest.approx(100.0, rel=1e-6)

    def test_small_spacing_approaches_rho1(self):
        """At very small a (a ≪ h), ρ_a → ρ₁ — the probe sees only the
        upper layer."""
        out = apparent_resistivity_two_layer(50.0, 500.0, 5.0, [0.5, 0.1])
        assert all(v == pytest.approx(50.0, rel=0.05) for v in out)

    def test_large_spacing_approaches_rho2(self):
        """At very large a (a ≫ h), ρ_a → ρ₂ — the probe samples the
        lower half-space."""
        out = apparent_resistivity_two_layer(50.0, 500.0, 1.0, [200.0, 1000.0])
        # Approaches ρ₂ but never quite reaches it for finite a
        assert all(200.0 < v < 500.0 for v in out)

    def test_monotonic_when_rho2_greater(self):
        """ρ₂ > ρ₁ → ρ_a monotonically increases with a (typical
        underlying-bedrock scenario)."""
        spacings = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
        out = apparent_resistivity_two_layer(50.0, 500.0, 3.0, spacings)
        assert all(out[i] < out[i + 1] for i in range(len(out) - 1))

    def test_monotonic_when_rho2_less(self):
        """ρ₂ < ρ₁ → ρ_a monotonically decreases (dry surface, wet below)."""
        spacings = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
        out = apparent_resistivity_two_layer(500.0, 50.0, 3.0, spacings)
        assert all(out[i] > out[i + 1] for i in range(len(out) - 1))

    def test_negative_inputs_raise(self):
        with pytest.raises(ValueError):
            apparent_resistivity_two_layer(-1.0, 100.0, 2.0, [1.0])
        with pytest.raises(ValueError):
            apparent_resistivity_two_layer(100.0, 100.0, -1.0, [1.0])


# ── Two-layer inversion ────────────────────────────────────────


class TestTwoLayerFit:
    def test_recovers_synthetic_model_within_10pct(self):
        """Generate synthetic ρ_a from a known (ρ₁, ρ₂, h), fit it back,
        check parameters are recovered within 10% (grid resolution)."""
        spacings = [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
        true_rho1, true_rho2, true_h = 80.0, 400.0, 3.0
        synthetic = apparent_resistivity_two_layer(
            true_rho1, true_rho2, true_h, spacings
        )
        fit = fit_two_layer_grid_search(spacings, synthetic, n_grid=20, n_refine=2)
        assert fit["rho1"] == pytest.approx(true_rho1, rel=0.10)
        assert fit["rho2"] == pytest.approx(true_rho2, rel=0.10)
        assert fit["h"] == pytest.approx(true_h, rel=0.20)
        assert fit["rms_log_error"] < 0.05

    def test_fit_handles_decreasing_curve(self):
        """ρ₁ > ρ₂ scenario — dry topsoil over wet clay."""
        spacings = [1.0, 2.0, 5.0, 10.0, 20.0, 40.0]
        synthetic = apparent_resistivity_two_layer(300.0, 30.0, 2.0, spacings)
        fit = fit_two_layer_grid_search(spacings, synthetic, n_grid=20, n_refine=2)
        # We measured a decreasing curve, so fit must have ρ₂ < ρ₁
        assert fit["rho2"] < fit["rho1"]
        assert fit["rms_log_error"] < 0.10

    def test_fit_too_few_measurements_raises(self):
        with pytest.raises(ValueError):
            fit_two_layer_grid_search([1.0, 2.0], [100.0, 110.0])

    def test_fit_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            fit_two_layer_grid_search([1.0, 2.0, 5.0], [100.0, 110.0])

    def test_fit_returns_predicted_at_spacings(self):
        """The returned 'predicted' array must have one value per spacing."""
        spacings = [1.0, 2.0, 5.0, 10.0]
        synthetic = apparent_resistivity_two_layer(100.0, 200.0, 2.0, spacings)
        fit = fit_two_layer_grid_search(spacings, synthetic, n_grid=15, n_refine=1)
        assert len(fit["predicted"]) == len(spacings)
