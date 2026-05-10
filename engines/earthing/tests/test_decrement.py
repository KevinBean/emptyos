"""IEEE 80 §15.10 decrement factor + §15.11 projection factor."""

from __future__ import annotations

import math

import pytest

from engines.earthing.decrement import (
    decrement_factor,
    projection_factor,
)


class TestDecrementFactor:
    def test_eq_84_60hz_value_matches_table_10(self):
        # IEEE 80 Table 10: t_f = 0.10 s, X/R = 20 → D_f ≈ 1.232
        res = decrement_factor(20.0, 0.10, freq_hz=60)
        assert not res["skipped"]
        assert res["d_f"] == pytest.approx(1.232, abs=0.005)
        # T_a = X / (ω·R) = (X/R)/ω at 60 Hz = 20 / (120π) ≈ 53 ms
        assert res["t_a_s"] == pytest.approx(20.0 / (2 * math.pi * 60), rel=1e-3)

    def test_eq_84_50hz(self):
        # 50 Hz makes T_a longer → slightly higher D_f for same X/R + t_f
        res_50 = decrement_factor(20.0, 0.10, freq_hz=50)
        res_60 = decrement_factor(20.0, 0.10, freq_hz=60)
        assert res_50["d_f"] > res_60["d_f"]

    def test_skip_rule_long_clearing(self):
        res = decrement_factor(20.0, 1.0)
        assert res["skipped"] is True
        assert res["d_f"] == 1.0
        assert "t_f" in res["reason"]

    def test_skip_rule_low_xr(self):
        res = decrement_factor(3.0, 0.1)
        assert res["skipped"] is True
        assert res["d_f"] == 1.0
        assert "X/R" in res["reason"]

    def test_d_f_always_ge_1(self):
        for xr in [5, 10, 20, 40]:
            for t_f in [0.05, 0.1, 0.3, 0.5]:
                d_f = decrement_factor(xr, t_f, freq_hz=60)["d_f"]
                assert d_f >= 1.0

    def test_validation(self):
        with pytest.raises(ValueError):
            decrement_factor(-1, 0.1)
        with pytest.raises(ValueError):
            decrement_factor(20, 0)
        with pytest.raises(ValueError):
            decrement_factor(20, 0.1, freq_hz=0)


class TestProjectionFactor:
    def test_simple_ratio(self):
        res = projection_factor(8500, 10200)
        assert res["c_p"] == pytest.approx(1.20)

    def test_no_growth(self):
        res = projection_factor(10000, 10000)
        assert res["c_p"] == 1.0

    def test_rejects_shrinking(self):
        with pytest.raises(ValueError):
            projection_factor(10000, 8000)

    def test_rejects_zero_present(self):
        with pytest.raises(ValueError):
            projection_factor(0, 10000)
