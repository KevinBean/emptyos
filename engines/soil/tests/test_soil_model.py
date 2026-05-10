"""Tests for SoilModel — construction, K, contrast, validation."""

import pytest
from engines.soil.soil_model import SoilModel


def test_two_layer_construction():
    m = SoilModel(resistivities=(190.0, 105.5163), thicknesses=(4.733190,))
    assert m.n_layers == 2


def test_reflection_coefficient_matches_reference_case():
    """RS_TUT1.F09 reports K = -0.28589 for ρ1=190, ρ2=105.5163."""
    m = SoilModel(resistivities=(190.0, 105.5163), thicknesses=(4.733190,))
    (k,) = m.reflection_coefficients()
    assert k == pytest.approx(-0.28589, abs=1e-4)


def test_contrast_ratio_matches_reference_case():
    """RS_TUT1.F09 reports contrast = 0.55535."""
    m = SoilModel(resistivities=(190.0, 105.5163), thicknesses=(4.733190,))
    (c,) = m.contrast_ratios()
    assert c == pytest.approx(0.55535, abs=1e-4)


def test_air_top_reflection_coefficient_is_minus_one():
    assert SoilModel.air_top_reflection_coefficient() == -1.0


def test_three_layer_lengths():
    m = SoilModel(resistivities=(100.0, 50.0, 200.0), thicknesses=(2.0, 5.0))
    assert m.n_layers == 3
    assert len(m.reflection_coefficients()) == 2
    assert len(m.contrast_ratios()) == 2


def test_reject_air_resistivity():
    """Air must never leak into the soil model — it's implicit (see DESIGN §6.2)."""
    with pytest.raises(ValueError, match="air"):
        SoilModel(resistivities=(1e18, 100.0), thicknesses=(1.0,))


def test_reject_thickness_count_mismatch():
    with pytest.raises(ValueError, match="thicknesses"):
        SoilModel(resistivities=(100.0, 200.0), thicknesses=(1.0, 2.0))


def test_reject_nonpositive_resistivity():
    with pytest.raises(ValueError, match="resistivit"):
        SoilModel(resistivities=(100.0, 0.0), thicknesses=(1.0,))


def test_reject_nonpositive_thickness():
    with pytest.raises(ValueError, match="thickness"):
        SoilModel(resistivities=(100.0, 200.0), thicknesses=(0.0,))
