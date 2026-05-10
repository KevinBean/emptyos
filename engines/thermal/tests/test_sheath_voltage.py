"""Induced sheath standing voltage — IEC 60287-1-1 §4.3 / Annex C.

Three bonding regimes, three voltage profiles. These tests lock the
closed-form behaviour and check ordering/scaling invariants that any
real cable engineer would recognise.
"""

from __future__ import annotations

import math

import pytest

from engines.thermal.iec60287 import (
    induced_field_per_metre,
    standing_voltage,
    standing_voltage_cross_bonded,
    standing_voltage_single_point,
)


# ── Field per metre ──────────────────────────────────────────────────


class TestInducedFieldTrefoil:
    """Trefoil: M = 2e-7 · ln(2s/d_s); E = ω·M·I, same for all three cables."""

    def _params(self):
        # 132 kV TB-880-shaped cable: D_s ≈ 67 mm, sheath_radius ≈ 0.0335,
        # touching trefoil → centre spacing ≈ overall_diameter ≈ 75 mm.
        return dict(
            current_a=800.0,
            centre_spacing_m=0.0755,
            sheath_mean_radius_m=0.0335,
        )

    def test_trefoil_returns_uniform_e(self):
        f = induced_field_per_metre(formation="trefoil", **self._params())
        # All three cables see the same magnitude in trefoil.
        assert f["E"] == f["E_middle"] == f["E_outer"]

    def test_trefoil_scales_linearly_with_current(self):
        a = induced_field_per_metre(
            formation="trefoil",
            current_a=400.0, centre_spacing_m=0.0755, sheath_mean_radius_m=0.0335,
        )
        b = induced_field_per_metre(
            formation="trefoil",
            current_a=800.0, centre_spacing_m=0.0755, sheath_mean_radius_m=0.0335,
        )
        assert abs(b["E"] - 2.0 * a["E"]) < 1e-9

    def test_trefoil_zero_inputs(self):
        f = induced_field_per_metre(
            formation="trefoil", current_a=0.0,
            centre_spacing_m=0.0755, sheath_mean_radius_m=0.0335,
        )
        assert f["E"] == 0.0

    def test_trefoil_closed_form_value(self):
        # Hand-calc: ω = 2π·50, M = 2e-7·ln(2·0.0755/0.067) ≈ 2e-7·ln(2.254)
        # = 2e-7 · 0.8124 ≈ 1.625e-7 H/m. E = 314.16 · 1.625e-7 · 800 ≈ 40.8e-3 V/m.
        f = induced_field_per_metre(formation="trefoil", **self._params())
        omega = 2 * math.pi * 50.0
        M_expected = 2e-7 * math.log(2 * 0.0755 / 0.067)
        E_expected = omega * M_expected * 800.0
        assert abs(f["E"] - E_expected) < 1e-9
        assert abs(f["M"] - M_expected) < 1e-12

    def test_wider_spacing_increases_field(self):
        tight = induced_field_per_metre(
            formation="trefoil",
            current_a=800.0, centre_spacing_m=0.0755, sheath_mean_radius_m=0.0335,
        )
        wide = induced_field_per_metre(
            formation="trefoil",
            current_a=800.0, centre_spacing_m=0.300, sheath_mean_radius_m=0.0335,
        )
        # Wider spacing → larger ln(2s/d_s) → larger M → larger E.
        assert wide["E"] > tight["E"]


class TestInducedFieldFlat:
    """Flat: outer cables see larger M than middle (sqrt of squared sum vs. plain log)."""

    def _params(self):
        return dict(
            current_a=800.0,
            centre_spacing_m=0.150,  # wider for flat
            sheath_mean_radius_m=0.0335,
        )

    def test_flat_outer_greater_than_middle(self):
        f = induced_field_per_metre(formation="flat", **self._params())
        assert f["E_outer"] > f["E_middle"] > 0
        # Worst-case `E` returned is outer for SVL sizing.
        assert f["E"] == f["E_outer"]

    def test_flat_middle_matches_trefoil_at_same_spacing(self):
        # Middle-cable M_middle = 2e-7·ln(2s/d_s) — identical to trefoil M.
        flat = induced_field_per_metre(formation="flat", **self._params())
        tref = induced_field_per_metre(formation="trefoil", **self._params())
        assert abs(flat["E_middle"] - tref["E"]) < 1e-9


# ── Bonding-specific helpers ─────────────────────────────────────────


class TestSinglePointBonding:
    """U_end = E · L. Linear in cable length — load-bearing constraint."""

    def test_zero_length_zero_voltage(self):
        assert standing_voltage_single_point(40e-3, 0.0) == 0.0

    def test_zero_field_zero_voltage(self):
        assert standing_voltage_single_point(0.0, 1000.0) == 0.0

    def test_linear_in_length(self):
        u500 = standing_voltage_single_point(40e-3, 500.0)
        u1000 = standing_voltage_single_point(40e-3, 1000.0)
        assert abs(u1000 - 2.0 * u500) < 1e-9

    def test_typical_400m_under_iec60840_limit(self):
        # Common 132 kV: E ~ 40 mV/m at rated current → 400 m route gives 16 V,
        # well under IEC 60840's 65 V accessible-touch limit. Sanity check that
        # the typical short-route case doesn't trigger an SVL.
        u = standing_voltage_single_point(40e-3, 400.0)
        assert 10.0 < u < 25.0


class TestCrossBonding:
    """Symmetric cross-bond → U_peak = E · L_minor / √3 mid-section."""

    def test_zero_inputs(self):
        assert standing_voltage_cross_bonded(0.0, 500.0) == 0.0
        assert standing_voltage_cross_bonded(40e-3, 0.0) == 0.0

    def test_symmetric_uses_sqrt3_divisor(self):
        u = standing_voltage_cross_bonded(40e-3, 500.0, symmetric=True)
        expected = 40e-3 * 500.0 / math.sqrt(3)
        assert abs(u - expected) < 1e-9

    def test_asymmetric_uses_full_length(self):
        u = standing_voltage_cross_bonded(40e-3, 500.0, symmetric=False)
        assert abs(u - 40e-3 * 500.0) < 1e-9

    def test_asymmetric_higher_than_symmetric(self):
        sym = standing_voltage_cross_bonded(40e-3, 500.0, symmetric=True)
        asym = standing_voltage_cross_bonded(40e-3, 500.0, symmetric=False)
        # Conservative bound is √3× higher than ideal symmetric peak.
        assert abs(asym - sym * math.sqrt(3)) < 1e-9

    def test_cross_bonded_lower_than_single_point_for_same_total_route(self):
        # 1500 m route: cross-bonded as 3 × 500 m minor sections vs single-point
        # to one end. Cross-bonding is the whole point — much lower U.
        E = 40e-3
        single_point = standing_voltage_single_point(E, 1500.0)
        cross = standing_voltage_cross_bonded(E, 500.0, symmetric=True)
        assert cross < single_point / 5  # roughly 1/(3·√3) ≈ 1/5.2


# ── Aggregate dispatcher ─────────────────────────────────────────────


class TestStandingVoltageDispatcher:
    """`standing_voltage(bonding=..., ...)` routes to the right helper."""

    def _common(self):
        return dict(
            current_a=800.0,
            centre_spacing_m=0.0755,
            sheath_mean_radius_m=0.0335,
        )

    def test_solid_bonding_returns_zero(self):
        out = standing_voltage(bonding="solidly_bonded", **self._common())
        assert out["U_v"] == 0.0
        assert out["regime"] == "solid"
        # E is still computed for inspection even though U is clamped.
        assert out["E_v_per_m"] > 0

    def test_single_point_requires_cable_length(self):
        with pytest.raises(ValueError, match="cable_length_m"):
            standing_voltage(bonding="single_point", **self._common())

    def test_single_point_returns_e_times_length(self):
        out = standing_voltage(
            bonding="single_point", cable_length_m=500.0, **self._common(),
        )
        assert out["U_v"] == pytest.approx(out["E_v_per_m"] * 500.0)
        assert out["regime"] == "single-point end"
        assert out["length_m"] == 500.0

    def test_cross_bonded_requires_minor_length(self):
        with pytest.raises(ValueError, match="minor_section_length_m"):
            standing_voltage(bonding="cross_bonded", **self._common())

    def test_cross_bonded_uses_minor_section(self):
        out = standing_voltage(
            bonding="cross_bonded", minor_section_length_m=500.0,
            **self._common(),
        )
        assert out["U_v"] == pytest.approx(
            out["E_v_per_m"] * 500.0 / math.sqrt(3),
        )
        assert out["regime"] == "cross-bonded peak"

    def test_unknown_bonding_raises(self):
        with pytest.raises(ValueError, match="unknown bonding"):
            standing_voltage(bonding="weird", cable_length_m=500.0, **self._common())

    def test_flat_formation_uses_outer_cable(self):
        # Flat outer carries the worst-case E, so U should be larger than
        # what trefoil would give at the same spacing.
        common = dict(
            current_a=800.0,
            centre_spacing_m=0.150,
            sheath_mean_radius_m=0.0335,
            cable_length_m=500.0,
            bonding="single_point",
        )
        flat = standing_voltage(formation="flat", **common)
        tref = standing_voltage(formation="trefoil", **common)
        assert flat["U_v"] > tref["U_v"]
