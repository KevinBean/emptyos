"""Tests for ElectrodeArray geometric factors."""

import pytest
from math import pi
from engines.soil.geometry import ElectrodeArray


def test_wenner_geometric_factor_closed_form():
    """K_g^Wenner = 2π·a."""
    arr = ElectrodeArray(kind="wenner", spacings=(2.0,))
    assert arr.geometric_factor() == pytest.approx(2 * pi * 2.0)


def test_wenner_geometric_factor_via_pairs_matches_closed_form():
    """Computing K_g from electrode_pairs() should agree with the closed form."""
    arr = ElectrodeArray(kind="wenner", spacings=(4.0,))
    g = sum(sign / r for sign, r in arr.electrode_pairs())
    kg_via_pairs = 2 * pi / g
    assert kg_via_pairs == pytest.approx(arr.geometric_factor(), rel=1e-12)


def test_wenner_apparent_resistivity_from_reference_case():
    """From RS_TUT1.F09 row 1: a=2 m, R=15.1197 Ω → ρ_a = 2π·2·15.1197 ≈ 190 Ω·m."""
    arr = ElectrodeArray(kind="wenner", spacings=(2.0,))
    rho_a = arr.geometric_factor() * 15.1197
    assert rho_a == pytest.approx(190.0, abs=0.5)


def test_schlumberger_geometric_factor():
    """K_g^Schl = π(L² − ℓ²)/(2ℓ)."""
    arr = ElectrodeArray(kind="schlumberger", spacings=(10.0, 1.0))
    expected = pi * (100.0 - 1.0) / 2.0
    assert arr.geometric_factor() == pytest.approx(expected)


def test_general_array_matches_wenner_when_positions_equivalent():
    """A 'general' array with Wenner positions should reproduce Wenner K_g."""
    a = 3.0
    wenner = ElectrodeArray(kind="wenner", spacings=(a,))
    # Wenner positions: C1=0, P1=a, P2=2a, C2=3a
    general = ElectrodeArray(kind="general", spacings=(0.0, 3 * a, a, 2 * a))
    assert general.geometric_factor() == pytest.approx(wenner.geometric_factor())


def test_electrode_pairs_yields_four():
    arr = ElectrodeArray(kind="wenner", spacings=(1.0,))
    assert len(list(arr.electrode_pairs())) == 4
