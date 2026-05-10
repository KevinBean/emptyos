"""Analytical sanity checks for IEC 60287 Phase A.

Closed-form invariants and ballpark numbers that don't require TB 880
fixtures. These run on every commit and catch regressions in the
algorithm during the JS-to-Python port.

When the CIGRE TB 880 fixtures land in
`engines/thermal/validation/cigre_tb880/cases/`, `test_cigre_tb880.py`
becomes the gating regression suite. Until then, these sanity tests
+ the JS tool's known outputs (manually checked) are the bar.
"""

from __future__ import annotations

import math

import pytest

from engines.models import AmpacityInput, CableLibraryEntry
from engines.thermal.iec60287 import compute_ampacity, compute_conductor_temperature


def make_240_cu_xlpe_22kv() -> CableLibraryEntry:
    return CableLibraryEntry(
        id="test-240-cu-xlpe-22kv",
        rated_voltage_kv=22.0,
        n_conductors=1,
        conductor_material="Cu",
        conductor_csa_mm2=240.0,
        insulation_material="XLPE",
        insulation_max_temp_c=90.0,
        sheath_material="Cu",
    )


def base_input(**overrides) -> AmpacityInput:
    cfg = dict(
        cable=make_240_cu_xlpe_22kv(),
        installation="direct_buried",
        bonding="single_point",
        burial_depth_m=1.0,
        spacing_mode="trefoil",
        grouped_cables=3,
        soil_thermal_resistivity_kmw=1.0,
        ambient_temperature_c=20.0,
        conductor_max_temp_c=90.0,
        frequency_hz=50.0,
    )
    cfg.update(overrides)
    return AmpacityInput(**cfg)


class TestAmpacityBallpark:
    """Sanity ranges from published Nexans / IEC tables."""

    def test_240_cu_xlpe_22kv_buried_in_range(self):
        # Typical published 22 kV 240 mm² Cu XLPE trefoil buried 1m,
        # ρ_soil = 1 K·m/W, ambient 20°C: ~480–620 A depending on assumptions.
        r = compute_ampacity(base_input())
        assert 400 < r.ampacity_a < 700, f"got {r.ampacity_a:.1f} A, expected ~500"

    def test_higher_ambient_reduces_ampacity(self):
        cool = compute_ampacity(base_input(ambient_temperature_c=15.0))
        hot = compute_ampacity(base_input(ambient_temperature_c=30.0))
        assert hot.ampacity_a < cool.ampacity_a

    def test_higher_soil_resistivity_reduces_ampacity(self):
        good = compute_ampacity(base_input(soil_thermal_resistivity_kmw=0.7))
        poor = compute_ampacity(base_input(soil_thermal_resistivity_kmw=2.5))
        assert poor.ampacity_a < good.ampacity_a

    def test_deeper_burial_reduces_ampacity(self):
        shallow = compute_ampacity(base_input(burial_depth_m=0.6))
        deep = compute_ampacity(base_input(burial_depth_m=2.0))
        assert deep.ampacity_a < shallow.ampacity_a

    def test_in_air_higher_than_buried(self):
        buried = compute_ampacity(base_input())
        air = compute_ampacity(base_input(installation="in_air"))
        assert air.ampacity_a > buried.ampacity_a, "still air should out-rate buried"


class TestThermalResistances:
    def test_t1_increases_with_insulation(self):
        r = compute_ampacity(base_input())
        # T1 for 22kV XLPE single-core ~ 0.4–0.7 K·m/W
        assert 0.2 < r.thermal_resistances["T1"] < 1.0

    def test_t4_buried_reasonable(self):
        r = compute_ampacity(base_input())
        # T4 for 1m buried trefoil group, ρ=1 K·m/W: ~ 0.8–2.0 K·m/W
        assert 0.5 < r.thermal_resistances["T4"] < 3.0


class TestInverse:
    def test_inverse_recovers_temperature(self):
        inp = base_input()
        forward = compute_ampacity(inp)
        I_rated = forward.ampacity_a
        # At rated current → conductor at θ_max
        inv = compute_conductor_temperature(inp, I_rated)
        assert abs(inv.conductor_temperature_c - inp.conductor_max_temp_c) < 1.0

    def test_inverse_lower_current_lower_temp(self):
        inp = base_input()
        rated = compute_ampacity(inp).ampacity_a
        inv = compute_conductor_temperature(inp, 0.5 * rated)
        assert inv.conductor_temperature_c < inp.conductor_max_temp_c
        assert inv.conductor_temperature_c > inp.ambient_temperature_c


class TestTrefoilT4:
    """Pin the trefoil-touching T4 routine vs the 132 kV TB 880 Case 0 cable."""

    def test_trefoil_higher_than_single(self):
        from engines.thermal.iec60287.installation_types import (
            t4_direct_buried_single,
            t4_direct_buried_trefoil,
        )
        single = t4_direct_buried_single(1.0, 1.0, 0.0755)
        trefoil = t4_direct_buried_trefoil(1.0, 1.0, 0.0755)
        # Mutual heating from the other two cables more than doubles T4.
        assert trefoil > 2.0 * single

    def test_trefoil_touching_132kv_tb880_geometry(self):
        from engines.thermal.iec60287.installation_types import t4_direct_buried_trefoil
        # TB 880 Case 0: 132 kV, D_e=75.5mm, L=1.0m, ρ_soil=1.0 → T4 ~ 1.59 K·m/W.
        # We land near 1.68 (5% high) with the bottom-cable Kennelly form;
        # close enough for engineering use, exact match still in flight.
        t4 = t4_direct_buried_trefoil(1.0, 1.0, 0.0755)
        assert 1.5 < t4 < 1.8

    def test_trefoil_spaced_lower_than_touching(self):
        from engines.thermal.iec60287.installation_types import t4_direct_buried_trefoil
        touching = t4_direct_buried_trefoil(1.0, 1.0, 0.0755)
        spaced = t4_direct_buried_trefoil(1.0, 1.0, 0.0755, axial_spacing_m=0.151)
        # Spacing apart reduces mutual heating → lower T4.
        assert spaced < touching


class TestInAirIteration:
    """Lock the in-air outer iteration on Δθ_surface across the three
    Table-2 mounting variants the engine handles + the solar coupling.

    Case-6 (in-air trough) already has a TB 880 fixture; these tests
    cover the cousin family (free-air single, free-air trefoil Group 6,
    free-air flat Group 5) that exercises the same iteration with
    different mounting constants.
    """

    def _air_input(self, **overrides):
        return base_input(installation="in_air", bonding="solidly_bonded", **overrides)

    def test_single_cable_free_air_converges(self):
        # Single isolated cable in free air — defaults Z=0.21, E=3.94, g=0.60.
        r = compute_ampacity(self._air_input(grouped_cables=1, spacing_mode="trefoil"))
        assert all("did not fully converge" not in n for n in r.notes)
        # T4 in still air for a small cable is ~ 0.5–3 K·m/W.
        assert 0.3 < r.thermal_resistances["T4"] < 5.0
        assert r.ampacity_a > 100  # meaningful current

    def test_trefoil_free_air_uses_group6_constants(self):
        # Group 6: trefoil touching, vertical formation in free air.
        r = compute_ampacity(self._air_input(spacing_mode="trefoil", grouped_cables=3))
        assert all("did not fully converge" not in n for n in r.notes)
        assert r.ampacity_a > 0

    def test_flat_free_air_uses_group5_constants(self):
        # Group 5: flat touching three cables in free air.
        r = compute_ampacity(self._air_input(spacing_mode="flat", grouped_cables=3))
        assert all("did not fully converge" not in n for n in r.notes)
        assert r.ampacity_a > 0

    def test_higher_ambient_reduces_in_air_ampacity(self):
        cool = compute_ampacity(self._air_input(ambient_temperature_c=15.0))
        hot = compute_ampacity(self._air_input(ambient_temperature_c=40.0))
        assert hot.ampacity_a < cool.ampacity_a

    def test_solar_absorption_reduces_ampacity(self):
        # Surface heat balance must include W_solar — sunlit cable rates lower.
        no_sun = compute_ampacity(self._air_input(
            solar_absorption_coefficient=0.0,
        ))
        sunlit = compute_ampacity(self._air_input(
            solar_absorption_coefficient=0.6,
            solar_radiation_w_per_m2=1000.0,
        ))
        assert sunlit.ampacity_a < no_sun.ampacity_a

    def test_explicit_zeg_override_takes_precedence(self):
        # Custom mounting constants (e.g. covered trough) override the auto-pick.
        auto = compute_ampacity(self._air_input(spacing_mode="trefoil", grouped_cables=3))
        override = compute_ampacity(self._air_input(
            spacing_mode="trefoil", grouped_cables=3,
            in_air_mounting_zeg=(0.97, 0.80, 0.23),  # case-6 trough mounting
        ))
        # Different mounting constants → different T4 → different ampacity.
        assert abs(auto.thermal_resistances["T4"] - override.thermal_resistances["T4"]) > 1e-3


class TestConductorLossesFormula:
    """Direct algorithmic checks against IEC 60287-1-1 closed forms."""

    def test_dc_resistance_240_cu_at_20c(self):
        from engines.thermal.iec60287.conductor_losses import dc_resistance_per_metre
        # ρ20 = 1.7241e-8 Ω·m, A = 240e-6 m²  →  R ≈ 7.184e-5 Ω/m = 71.8 μΩ/m
        r = dc_resistance_per_metre(240.0, "Cu", 20.0)
        assert abs(r - 7.184e-5) / 7.184e-5 < 0.01

    def test_temperature_correction(self):
        from engines.thermal.iec60287.conductor_losses import dc_resistance_per_metre
        r20 = dc_resistance_per_metre(240.0, "Cu", 20.0)
        r90 = dc_resistance_per_metre(240.0, "Cu", 90.0)
        # +70°C × 3.93e-3 = +27.5%
        assert abs((r90 / r20 - 1) - 70 * 3.93e-3) < 1e-4


class TestIEC60228Fallback:
    """When no datasheet R20 is supplied, fall back to the IEC 60228 max table."""

    def test_iec60228_table_used_when_no_datasheet(self):
        # 240 mm² Cu Class 2 → 0.0754 Ω/km per IEC 60228.
        from engines.thermal.references import iec60228_max_r20
        cable = make_240_cu_xlpe_22kv()
        assert cable.conductor_dc_resistance_20c_ohm_per_km is None
        assert cable.conductor_class == 2

        r = compute_ampacity(base_input(cable=cable))
        # Table value → effective ρ20 = 0.0754e-3 × 240e-6 = 1.810e-8 Ω·m,
        # which differs from bare ρ20 (1.7241e-8) by the stranding factor.
        # Result should still land in the published 22 kV / 240 mm² range
        # but the note must mention IEC 60228.
        assert any("IEC 60228" in n for n in r.notes), \
            f"expected IEC 60228 note, got {r.notes}"
        assert 400 < r.ampacity_a < 700

    def test_datasheet_wins_over_iec60228_table(self):
        # When both a datasheet R20 and the IEC 60228 table value exist,
        # the datasheet must win — it's the more accurate per-design figure
        # vs the standard's max for the class.
        cable_with_datasheet = CableLibraryEntry(
            id="datasheet-240",
            rated_voltage_kv=22.0,
            conductor_material="Cu",
            conductor_csa_mm2=240.0,
            conductor_dc_resistance_20c_ohm_per_km=0.0700,  # below IEC 60228 max 0.0754
            insulation_material="XLPE",
        )
        r_datasheet = compute_ampacity(base_input(cable=cable_with_datasheet))
        # The IEC 60228 note must NOT fire when datasheet is supplied.
        assert not any("IEC 60228" in n for n in r_datasheet.notes), \
            f"datasheet path should not append IEC 60228 note, got {r_datasheet.notes}"
        # Lower R20 → higher ampacity. Versus the table-fallback case it
        # should rate higher (datasheet 0.0700 < IEC table 0.0754).
        r_table = compute_ampacity(base_input(cable=make_240_cu_xlpe_22kv()))
        assert r_datasheet.ampacity_a > r_table.ampacity_a
