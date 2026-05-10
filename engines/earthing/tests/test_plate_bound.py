"""IEEE 80 Eq. 55 — plate-bound R_g feasibility check."""

from __future__ import annotations

import math

import pytest

from engines.earthing.ieee80 import (
    plate_grid_resistance,
    sverak_grid_resistance,
)


class TestPlateBound:
    def test_kb_worked_example(self):
        # 100 m × 60 m grid in 250 Ω·m soil → ~1.43 Ω.
        rg = plate_grid_resistance(250.0, 6000.0)
        assert rg == pytest.approx(1.43, abs=0.01)

    def test_doubling_area_halves_rg_by_sqrt2(self):
        # R_g ∝ 1/√A.
        a1 = plate_grid_resistance(100, 1000)
        a2 = plate_grid_resistance(100, 4000)
        assert a2 == pytest.approx(a1 / 2, rel=0.001)

    def test_linear_in_rho(self):
        rg1 = plate_grid_resistance(100, 5000)
        rg2 = plate_grid_resistance(300, 5000)
        assert rg2 == pytest.approx(3 * rg1, rel=1e-6)

    def test_below_sverak(self):
        # Plate is the lower bound — Sverak with finite L_T must give ≥ plate.
        rho, A, h = 250.0, 6000.0, 0.5
        for L_T in [500, 1500, 5000, 50000]:
            sverak = sverak_grid_resistance(rho, L_T, A, h)
            plate = plate_grid_resistance(rho, A)
            assert sverak >= plate * 0.99  # tiny numerical slack

    def test_sverak_approaches_plate_as_lt_grows(self):
        rho, A, h = 250.0, 6000.0, 0.5
        plate = plate_grid_resistance(rho, A)
        # Very long L_T → 1/L_T term in Sverak vanishes → approach plate value.
        sverak_huge = sverak_grid_resistance(rho, 1_000_000, A, h)
        # Sverak's depth term differs slightly from the pure plate; allow ~10%.
        assert sverak_huge == pytest.approx(plate, rel=0.10)

    def test_validation(self):
        with pytest.raises(ValueError):
            plate_grid_resistance(0, 1000)
        with pytest.raises(ValueError):
            plate_grid_resistance(100, 0)
        with pytest.raises(ValueError):
            plate_grid_resistance(-10, 1000)
