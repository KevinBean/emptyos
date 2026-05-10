"""Tests for the Stefanesco recursion and kernel function."""

import pytest
from engines.soil.kernel import stefanesco_recursion, kernel


def test_uniform_soil_T_equals_rho():
    """For ρ_1 = ρ_2, T_1(λ) = ρ for all λ."""
    rho = (100.0, 100.0)
    h = (5.0,)
    for lam in (1e-3, 1e-1, 1.0, 10.0, 100.0):
        assert stefanesco_recursion(lam, rho, h) == pytest.approx(100.0, rel=1e-12)


def test_uniform_soil_kernel_is_zero():
    """K(λ) = 0 everywhere for a uniform half-space."""
    rho = (50.0,) * 4
    h = (1.0, 2.0, 3.0)
    for lam in (1e-3, 1e-1, 1.0, 10.0, 100.0):
        assert kernel(lam, rho, h) == pytest.approx(0.0, abs=1e-12)


def test_single_layer_T_equals_rho():
    """A 1-layer model is just a uniform half-space."""
    assert stefanesco_recursion(1.0, (123.0,), ()) == pytest.approx(123.0)


def test_low_lh_limit_T_to_rho_bottom():
    """λ → 0 (1/λ → ∞) probes deep, so T_1 → ρ_n.

    Recursion: tanh(λh) → 0, so T_i = T_{i+1} unchanged — the top layer is
    transparent and the bottom layer is "seen" through it.
    """
    rho = (100.0, 1e6)
    h = (10.0,)
    # λ·h = 1e-6
    T = stefanesco_recursion(1e-7, rho, h)
    assert T == pytest.approx(1e6, rel=1e-2)


def test_high_lh_limit_T_to_rho_top():
    """λ → ∞ (1/λ → 0) probes shallow, so T_1 → ρ_1.

    Recursion: tanh(λh) → 1, so T_i = ρ_i — the top layer dominates.
    """
    rho = (100.0, 500.0)
    h = (10.0,)
    # λ·h = 100, top layer dominates
    T = stefanesco_recursion(10.0, rho, h)
    assert T == pytest.approx(100.0, rel=1e-3)


def test_kernel_high_contrast_resistive_bottom_K_positive():
    """ρ_2 >> ρ_1, λ·h moderate → K > 0 (and bounded by +1)."""
    rho = (10.0, 10000.0)
    h = (1.0,)
    k = kernel(1.0, rho, h)
    assert 0.0 < k < 1.0


def test_kernel_high_contrast_conductive_bottom_K_negative():
    """ρ_2 << ρ_1, λ·h moderate → K < 0 (and bounded by -1)."""
    rho = (10000.0, 10.0)
    h = (1.0,)
    k = kernel(1.0, rho, h)
    assert -1.0 < k < 0.0


def test_tanh_saturation_does_not_blow_up():
    """λ·h = 1e6 would produce inf via tanh(1e6) without the guard.

    High λh limit → T → ρ_1 = 100 (top layer dominates).
    """
    rho = (100.0, 200.0)
    h = (1.0,)
    T = stefanesco_recursion(1e6, rho, h)
    assert T == pytest.approx(100.0, rel=1e-12)
