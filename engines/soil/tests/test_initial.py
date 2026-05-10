"""Tests for the auto initial-estimate engine."""

import pytest
from engines.soil.initial import auto_initial_estimate


def test_two_layer_estimate_brackets_top_and_bottom():
    """For the F09 monotonic-decreasing case, ρ_top ≈ ρ_a(min spacing), ρ_bot ≈ ρ_a(max)."""
    s = [2.0, 4.0, 8.0, 16.0, 32.0]
    r = [190.0, 183.0, 147.0, 118.0, 107.0]
    g = auto_initial_estimate(s, r, n_layers=2)
    assert g.resistivities[0] == pytest.approx(190.0)
    assert g.resistivities[1] == pytest.approx(107.0)
    # h should be in the spacing range, not at an endpoint
    assert s[0] < g.thicknesses[0] < s[-1]


def test_two_layer_estimate_h_near_geometric_mean_crossing():
    """h_1 ≈ spacing where ρ_a crosses sqrt(ρ_top·ρ_bot)."""
    s = [2.0, 4.0, 8.0, 16.0, 32.0]
    r = [190.0, 183.0, 147.0, 118.0, 107.0]
    import math
    target = math.sqrt(190.0 * 107.0)  # ≈ 142.6, which sits between rows 2 and 3
    g = auto_initial_estimate(s, r, n_layers=2)
    # bracket is between a=4 (ρ=183) and a=8 (ρ=147) — actually 147 > 142.6 > 118 so a in (8,16)
    assert 8.0 <= g.thicknesses[0] <= 16.0


def test_three_layer_estimate_lengths():
    s = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
    r = [200.0, 180.0, 80.0, 50.0, 90.0, 150.0]
    g = auto_initial_estimate(s, r, n_layers=3)
    assert g.n_layers == 3
    assert len(g.thicknesses) == 2


def test_single_layer_estimate_uses_geometric_mean():
    s = [1.0, 2.0, 5.0]
    r = [100.0, 100.0, 100.0]
    g = auto_initial_estimate(s, r, n_layers=1)
    assert g.resistivities[0] == pytest.approx(100.0)
    assert g.thicknesses == ()


def test_reject_mismatched_lengths():
    with pytest.raises(ValueError):
        auto_initial_estimate([1.0, 2.0], [100.0], n_layers=2)


def test_reject_too_few_measurements():
    with pytest.raises(ValueError):
        auto_initial_estimate([1.0], [100.0], n_layers=2)
