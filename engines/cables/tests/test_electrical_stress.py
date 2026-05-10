"""Closed-form sanity for coaxial electric stress."""

import math

import pytest

from engines.cables.electrical_stress import (
    impulse_field_strength,
    nominal_field_strength,
)


class TestNominal:
    def test_known_case(self):
        # U0 = 12 kV, rc = 5 mm, R = 15 mm
        # ln(R/rc) = ln(3) ≈ 1.0986
        # E_max = 12 / (5 · 1.0986) ≈ 2.185 kV/mm
        # E_min = 12 / (15 · 1.0986) ≈ 0.728 kV/mm
        r = nominal_field_strength(12.0, 5.0, 15.0)
        assert abs(r.e_max - 12.0 / (5.0 * math.log(3.0))) < 1e-9
        assert abs(r.e_min - 12.0 / (15.0 * math.log(3.0))) < 1e-9
        assert r.mode == "nominal"

    def test_max_greater_than_min(self):
        r = nominal_field_strength(76.0, 8.0, 25.0)
        assert r.e_max > r.e_min
        # Ratio = R/rc
        assert abs(r.e_max / r.e_min - 25.0 / 8.0) < 1e-9

    def test_e_at_intermediate(self):
        r = nominal_field_strength(12.0, 5.0, 15.0, radius_at_mm=10.0)
        assert r.e_at is not None
        assert r.e_min < r.e_at < r.e_max

    def test_invalid_geometry_raises(self):
        with pytest.raises(ValueError):
            nominal_field_strength(12.0, 10.0, 5.0)  # R < rc

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            nominal_field_strength(-12.0, 5.0, 15.0)


class TestImpulse:
    def test_scales_linearly_with_voltage(self):
        nom = nominal_field_strength(12.0, 5.0, 15.0)
        imp = impulse_field_strength(75.0, 5.0, 15.0)
        ratio = 75.0 / 12.0
        assert abs(imp.e_max / nom.e_max - ratio) < 1e-9
        assert abs(imp.e_min / nom.e_min - ratio) < 1e-9
        assert imp.mode == "impulse"
