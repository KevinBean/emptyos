"""ENA EG-0 / IEC 60479 probabilistic risk assessment."""

from __future__ import annotations

import pytest

from engines.earthing.eg0 import (
    alarp_band,
    body_impedance,
    coincidence_probability,
    fibrillation_probability,
    fibrillation_thresholds_ma,
    risk_assessment,
)


class TestBodyImpedance:
    def test_low_voltage_clamps(self):
        # Below 25 V — IEC 60479 doesn't define lower; we clamp.
        assert body_impedance(10) == body_impedance(25)

    def test_high_voltage_clamps(self):
        # Above 1000 V — clamp to terminal value.
        assert body_impedance(2000) == body_impedance(1000)

    def test_monotonic_decrease(self):
        # Z_B falls with rising touch voltage — IEC 60479 Figure 11/12.
        assert body_impedance(50) > body_impedance(100) > body_impedance(220) > body_impedance(700)

    def test_negative_handled(self):
        # Symmetric — magnitude only.
        assert body_impedance(-100) == body_impedance(100)


class TestFibrillationCurves:
    def test_thresholds_400ms(self):
        # IEC 60479-1 Figure 20 at t_f = 0.4 s — c1 ~100 mA, c2 ~250-300 mA, c3 ~500-600 mA.
        th = fibrillation_thresholds_ma(0.4)
        assert 80 < th["c1"] < 150
        assert 200 < th["c2"] < 350
        assert 450 < th["c3"] < 700
        assert th["c1"] < th["c2"] < th["c3"]

    def test_thresholds_decrease_with_duration(self):
        # Longer fault → lower body current to fibrillate.
        th_short = fibrillation_thresholds_ma(0.1)
        th_long = fibrillation_thresholds_ma(2.0)
        assert th_long["c2"] < th_short["c2"]

    def test_below_c1_low_p(self):
        th = fibrillation_thresholds_ma(0.4)
        # Just below c1 — should be very small probability.
        res = fibrillation_probability(th["c1"] * 0.5, 0.4)
        assert res["p_fib"] < 0.01
        assert res["zone"] == "AC-3"

    def test_at_c2_around_50pct(self):
        th = fibrillation_thresholds_ma(0.4)
        res = fibrillation_probability(th["c2"], 0.4)
        # Linear interpolation crosses 0.5 at the c2 boundary.
        assert 0.45 <= res["p_fib"] <= 0.55

    def test_above_c3_capped(self):
        th = fibrillation_thresholds_ma(0.4)
        res = fibrillation_probability(th["c3"] * 5, 0.4)
        assert res["p_fib"] == pytest.approx(0.95, abs=0.01)
        assert res["zone"] == "AC-4.3"


class TestCoincidence:
    def test_typical_public_footpath(self):
        # KB worked example: N_f=0.5/yr, N_e=50/yr, t_f=0.4s, t_e=5s
        # → P_coincidence ≈ 4.28e-6 /year.
        p = coincidence_probability(0.5, 50, 0.4, 5.0)
        assert p == pytest.approx(4.28e-6, rel=0.01)

    def test_zero_rate_zero_p(self):
        assert coincidence_probability(0, 50, 0.4, 5) == 0.0
        assert coincidence_probability(0.5, 0, 0.4, 5) == 0.0

    def test_validation(self):
        with pytest.raises(ValueError):
            coincidence_probability(-1, 50, 0.4, 5)
        with pytest.raises(ValueError):
            coincidence_probability(0.5, 50, -0.1, 5)


class TestALARPBand:
    def test_intolerable(self):
        b = alarp_band(2e-4)
        assert b["band"] == "intolerable"

    def test_alarp_high(self):
        b = alarp_band(5e-5)
        assert b["band"] == "alarp_high"

    def test_alarp_low(self):
        b = alarp_band(5e-6)
        assert b["band"] == "alarp_low"

    def test_acceptable(self):
        b = alarp_band(1e-7)
        assert b["band"] == "acceptable"

    def test_boundary_values(self):
        # Exactly at the 1e-6 boundary → still acceptable (≤, not <).
        assert alarp_band(1e-6)["band"] == "acceptable"
        # Exactly at 1e-4 → still alarp_high (the boundary above is intolerable).
        assert alarp_band(1e-4)["band"] == "alarp_high"


class TestRiskAssessmentEnd2End:
    def test_sydney_fence_kb_example(self):
        """KB worked example: 132/33 kV substation, U_T=850 V at fence."""
        r = risk_assessment(
            850.0,
            fault_duration_s=0.4,
            n_fault_per_year=0.5,
            n_exposure_per_year=50,
            exposure_duration_s=5.0,
        )
        # Body current ~700-900 mA — in or above c3 zone
        assert 600 < r["body_current_ma"] < 900
        # Coincidence matches KB note
        assert r["p_coincidence"] == pytest.approx(4.28e-6, rel=0.01)
        # Lands in ALARP band — KB note says "marginal, ALARP applies"
        assert r["band"] in ("alarp_low", "alarp_high")
        # All required fields present
        for key in ("touch_voltage_v", "z_body_ohm", "body_current_ma",
                    "p_fibrillation", "iec_zone", "p_coincidence",
                    "p_fatality_per_year", "band", "band_action"):
            assert key in r

    def test_safe_low_exposure_rural(self):
        """Same touch voltage, rural paddock fence — much lower exposure."""
        r = risk_assessment(
            850.0,
            fault_duration_s=0.4,
            n_fault_per_year=0.5,
            n_exposure_per_year=2,            # 2 visits/yr
            exposure_duration_s=5.0,
        )
        # Coincidence ~25× lower → fatality probability drops below 1e-6.
        assert r["p_fatality_per_year"] < 1e-6
        assert r["band"] == "acceptable"

    def test_surface_layer_lowers_current(self):
        """Adding 1500 Ω of foot resistance (rubber boots / crushed rock)
        materially lowers body current and risk band."""
        bare = risk_assessment(
            500.0,
            fault_duration_s=0.4,
            n_fault_per_year=1.0,
            n_exposure_per_year=20,
            exposure_duration_s=5.0,
        )
        with_layer = risk_assessment(
            500.0,
            fault_duration_s=0.4,
            n_fault_per_year=1.0,
            n_exposure_per_year=20,
            exposure_duration_s=5.0,
            additional_resistance_ohm=2000.0,
        )
        assert with_layer["body_current_ma"] < bare["body_current_ma"]
        assert with_layer["p_fatality_per_year"] <= bare["p_fatality_per_year"]

    def test_validation(self):
        kw = dict(fault_duration_s=0.4, n_fault_per_year=1, n_exposure_per_year=10, exposure_duration_s=1)
        with pytest.raises(ValueError):
            risk_assessment(-100, **kw)
        with pytest.raises(ValueError):
            risk_assessment(100, additional_resistance_ohm=-1, **kw)
