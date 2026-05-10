"""IEEE 80 — Sverak Rg + tolerable touch / step voltages + mesh/step potentials."""

from __future__ import annotations

import math

import pytest

from engines.earthing.ieee80 import (
    irregularity_factor_ki,
    mesh_geometric_factor_km,
    mesh_voltage,
    step_geometric_factor_ks,
    step_voltage,
    sverak_grid_resistance,
    tolerable_step_voltage,
    tolerable_touch_voltage,
)


# ── Sverak grid resistance ─────────────────────────────────────


class TestSverakRg:
    def test_canonical_70x70_grid(self):
        """IEEE 80-2013 Annex B example: 70×70 m grid with 21×21 mesh
        (L_T ≈ 2940 m), h = 0.5 m, ρ = 400 Ω·m → Rg ≈ 2.7-2.8 Ω."""
        rg = sverak_grid_resistance(
            rho_soil_ohm_m=400.0,
            grid_total_length_m=2940.0,
            grid_area_m2=70.0 * 70.0,
            burial_depth_m=0.5,
        )
        assert 2.5 < rg < 3.0

    def test_rg_decreases_with_grid_length(self):
        """More buried conductor → lower Rg."""
        rg_short = sverak_grid_resistance(100.0, 200.0, 400.0, 0.5)
        rg_long = sverak_grid_resistance(100.0, 800.0, 400.0, 0.5)
        assert rg_long < rg_short

    def test_rg_decreases_with_grid_area(self):
        """Bigger grid footprint → lower Rg."""
        rg_small = sverak_grid_resistance(100.0, 500.0, 100.0, 0.5)
        rg_large = sverak_grid_resistance(100.0, 500.0, 10000.0, 0.5)
        assert rg_large < rg_small

    def test_rg_scales_linearly_with_resistivity(self):
        """Rg ∝ ρ — doubling soil resistivity doubles Rg."""
        rg1 = sverak_grid_resistance(100.0, 500.0, 1000.0, 0.5)
        rg2 = sverak_grid_resistance(200.0, 500.0, 1000.0, 0.5)
        assert rg2 == pytest.approx(2.0 * rg1, rel=1e-9)

    def test_negative_inputs_raise(self):
        with pytest.raises(ValueError):
            sverak_grid_resistance(-100.0, 500.0, 1000.0, 0.5)
        with pytest.raises(ValueError):
            sverak_grid_resistance(100.0, 0.0, 1000.0, 0.5)


# ── Tolerable touch / step voltages ────────────────────────────


class TestTolerableVoltages:
    def test_50kg_touch_no_surface_layer(self):
        """E_touch_50 = (1000 + 1.5·ρ) · 0.116 / √t.
        ρ = 400, t = 0.5 → (1000 + 600) · 0.116 / √0.5 ≈ 262 V."""
        v = tolerable_touch_voltage(0.5, 400.0)
        assert v == pytest.approx(262.4, rel=0.01)

    def test_70kg_higher_than_50kg(self):
        """Larger body → more allowable current → higher tolerable voltage."""
        v_50 = tolerable_touch_voltage(0.5, 400.0, body_weight_kg=50)
        v_70 = tolerable_touch_voltage(0.5, 400.0, body_weight_kg=70)
        assert v_70 > v_50

    def test_step_higher_than_touch(self):
        """Step voltage allows higher levels than touch (foot-foot vs
        hand-foot circuit)."""
        v_touch = tolerable_touch_voltage(0.5, 400.0)
        v_step = tolerable_step_voltage(0.5, 400.0)
        assert v_step > v_touch

    def test_surface_layer_raises_tolerable_voltage(self):
        """Crushed-rock surface layer raises both touch and step limits
        — that's why it's used in substation yards."""
        v_bare = tolerable_touch_voltage(0.5, 200.0)
        v_with_rock = tolerable_touch_voltage(
            0.5, 200.0,
            rho_surface_ohm_m=2500.0,
            surface_layer_thickness_m=0.10,
        )
        assert v_with_rock > 2 * v_bare  # rock layer roughly triples it

    def test_voltage_inverse_sqrt_time(self):
        """Tolerable V ∝ 1/√t — halving the fault duration raises the
        limit by √2."""
        v_05 = tolerable_touch_voltage(0.5, 200.0)
        v_025 = tolerable_touch_voltage(0.25, 200.0)
        assert v_025 / v_05 == pytest.approx(math.sqrt(2.0), rel=1e-6)

    def test_invalid_body_weight_raises(self):
        with pytest.raises(ValueError):
            tolerable_touch_voltage(0.5, 200.0, body_weight_kg=60)

    def test_zero_fault_duration_raises(self):
        with pytest.raises(ValueError):
            tolerable_touch_voltage(0.0, 200.0)
        with pytest.raises(ValueError):
            tolerable_step_voltage(-1.0, 200.0)


# ── Mesh + step potentials inside the yard (IEEE 80 §16) ───────


# Annex B reference: 70m × 70m square grid, D = 7 m mesh spacing →
# 11 conductors per side, L_C = 22·70 = 1540 m, perimeter = 280 m,
# so n_a = 2·1540/280 = 11. Square grid → n_b = n_c = n_d = 1.
ANNEX_B = dict(
    rho_a_ohm_m=400.0,
    fault_current_a=3000.0,
    grid_length_m=70.0,
    grid_width_m=70.0,
    grid_total_length_m=1540.0,
    spacing_m=7.0,
    burial_depth_m=0.5,
    conductor_diameter_m=0.01,
)


class TestMeshStepPotentials:
    def test_n_eff_square_grid(self):
        """Square 70×70 grid with L_C = 1540 m → n = 11 exactly."""
        km = mesh_geometric_factor_km(
            spacing_m=7.0, burial_depth_m=0.5, conductor_diameter_m=0.01,
            n_eff=11.0, rods_on_perimeter=False,
        )
        # K_ii = 1/(22)^(2/11) ≈ 0.5701 ; K_h = √1.5 ≈ 1.2247
        assert km["K_ii"] == pytest.approx(0.5701, rel=0.01)
        assert km["K_h"] == pytest.approx(1.2247, rel=0.001)

    def test_km_no_perimeter_rods_matches_hand_calc(self):
        """Hand calc for D=7, h=0.5, d=0.01, n=11, rods inside:
        K_m ≈ 0.887 (from ieee80 §16 closed form)."""
        km = mesh_geometric_factor_km(
            spacing_m=7.0, burial_depth_m=0.5, conductor_diameter_m=0.01,
            n_eff=11.0, rods_on_perimeter=False,
        )
        assert km["K_m"] == pytest.approx(0.887, rel=0.02)

    def test_km_perimeter_rods_lower_than_inside_rods(self):
        """K_ii = 1.0 when rods are on perimeter (vs <1 for inside).
        That makes the second log term *more negative*, so K_m drops."""
        inside = mesh_geometric_factor_km(
            7.0, 0.5, 0.01, n_eff=11.0, rods_on_perimeter=False,
        )
        perim = mesh_geometric_factor_km(
            7.0, 0.5, 0.01, n_eff=11.0, rods_on_perimeter=True,
        )
        assert perim["K_ii"] == 1.0
        assert perim["K_m"] < inside["K_m"]

    def test_ki_linear_in_n(self):
        """K_i = 0.644 + 0.148·n (eq. 89)."""
        assert irregularity_factor_ki(11.0) == pytest.approx(2.272, rel=1e-6)
        assert irregularity_factor_ki(5.0) == pytest.approx(1.384, rel=1e-6)

    def test_em_scales_linearly_with_rho_and_ig(self):
        """E_m ∝ ρ · I_G — doubling either doubles E_m."""
        base = mesh_voltage(**ANNEX_B)
        doubled_rho = mesh_voltage(**{**ANNEX_B, "rho_a_ohm_m": 800.0})
        doubled_ig = mesh_voltage(**{**ANNEX_B, "fault_current_a": 6000.0})
        assert doubled_rho["E_m_v"] == pytest.approx(2.0 * base["E_m_v"], rel=1e-9)
        assert doubled_ig["E_m_v"] == pytest.approx(2.0 * base["E_m_v"], rel=1e-9)

    def test_em_annex_b_reference(self):
        """Annex B-style 70×70 m, ρ=400, I_G=3000, D=7, h=0.5, d=0.01,
        no rods → E_m ≈ 1570 V (within 5% of hand calc 1571)."""
        out = mesh_voltage(**ANNEX_B)
        assert out["E_m_v"] == pytest.approx(1571.0, rel=0.05)
        assert out["n_eff"] == pytest.approx(11.0, rel=1e-6)
        assert out["L_M_m"] == pytest.approx(1540.0, rel=1e-9)

    def test_em_with_perimeter_rods_lower(self):
        """Adding 20 rods of 7.5 m on the perimeter both lowers K_m
        (K_ii=1) and raises L_M, so E_m drops."""
        without = mesh_voltage(**ANNEX_B)
        with_rods = mesh_voltage(
            **ANNEX_B, n_rods=20, rod_length_m=7.5, rods_on_perimeter=True,
        )
        assert with_rods["L_M_m"] > without["L_M_m"]
        assert with_rods["E_m_v"] < without["E_m_v"]

    def test_es_annex_b_reference(self):
        """Hand calc for the Annex B grid: K_s ≈ 0.406, K_i = 2.272,
        L_S = 0.75·1540 = 1155 → E_s ≈ 959 V."""
        out = step_voltage(
            rho_a_ohm_m=400.0, fault_current_a=3000.0,
            grid_length_m=70.0, grid_width_m=70.0,
            grid_total_length_m=1540.0,
            spacing_m=7.0, burial_depth_m=0.5,
        )
        assert out["E_s_v"] == pytest.approx(959.0, rel=0.05)
        assert out["L_S_m"] == pytest.approx(1155.0, rel=1e-9)
        assert out["K_s"] == pytest.approx(0.406, rel=0.02)

    def test_es_lower_with_deeper_burial(self):
        """Step formula is dominated by 1/(2h); deeper grid → lower E_s.
        Halving 1/(2h) (h: 0.5 → 1.5) drops K_s noticeably."""
        shallow = step_voltage(
            rho_a_ohm_m=400.0, fault_current_a=3000.0,
            grid_length_m=70.0, grid_width_m=70.0,
            grid_total_length_m=1540.0,
            spacing_m=7.0, burial_depth_m=0.5,
        )
        deep = step_voltage(
            rho_a_ohm_m=400.0, fault_current_a=3000.0,
            grid_length_m=70.0, grid_width_m=70.0,
            grid_total_length_m=1540.0,
            spacing_m=7.0, burial_depth_m=1.5,
        )
        assert deep["E_s_v"] < shallow["E_s_v"]

    def test_em_negative_inputs_raise(self):
        with pytest.raises(ValueError):
            mesh_voltage(**{**ANNEX_B, "rho_a_ohm_m": -1.0})
        with pytest.raises(ValueError):
            mesh_voltage(**{**ANNEX_B, "fault_current_a": 0.0})
