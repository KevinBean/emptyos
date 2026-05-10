"""Forward solver: uniform-soil sanity test.

This test exercises the forward solver without needing filter coefficients,
because the uniform-soil fast-path returns ρ_1 directly (K(λ) ≡ 0).
"""

import pytest
from engines.soil.forward import forward_apparent_resistivity
from engines.soil.geometry import ElectrodeArray
from engines.soil.soil_model import SoilModel


def test_uniform_soil_returns_rho_for_wenner():
    soil = SoilModel(resistivities=(123.0,), thicknesses=())
    for a in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0):
        arr = ElectrodeArray(kind="wenner", spacings=(a,))
        assert forward_apparent_resistivity(soil, arr) == pytest.approx(123.0, rel=1e-12)


def test_uniform_soil_returns_rho_for_schlumberger():
    soil = SoilModel(resistivities=(50.0,), thicknesses=())
    arr = ElectrodeArray(kind="schlumberger", spacings=(10.0, 1.0))
    assert forward_apparent_resistivity(soil, arr) == pytest.approx(50.0, rel=1e-12)


def test_uniform_soil_returns_rho_for_general():
    soil = SoilModel(resistivities=(75.0,), thicknesses=())
    arr = ElectrodeArray(kind="general", spacings=(0.0, 9.0, 3.0, 6.0))
    assert forward_apparent_resistivity(soil, arr) == pytest.approx(75.0, rel=1e-12)


def test_two_layer_uniform_resistivity_returns_rho():
    """ρ_1 = ρ_2: still uniform, fast-path applies."""
    soil = SoilModel(resistivities=(100.0, 100.0), thicknesses=(5.0,))
    arr = ElectrodeArray(kind="wenner", spacings=(2.0,))
    assert forward_apparent_resistivity(soil, arr) == pytest.approx(100.0, rel=1e-12)


def test_two_layer_non_uniform_returns_finite_resistivity():
    """Non-uniform soil hits the DLF path; with Guptasarma–Singh 61-pt loaded,
    forward returns a finite ρ_a in the expected range."""
    soil = SoilModel(resistivities=(190.0, 105.5163), thicknesses=(4.733190,))
    arr = ElectrodeArray(kind="wenner", spacings=(2.0,))
    rho_a = forward_apparent_resistivity(soil, arr)
    # At a=2 m (small relative to h=4.7 m), ρ_a should be close to ρ_1=190.
    # Reference RS_TUT1.F09 has ρ_meas=190 at this spacing; computed value is in (180, 195).
    assert 175.0 < rho_a < 200.0
