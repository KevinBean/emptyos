"""References — IEC 60228 conductor R20 + soil ρ_T loaders."""

from __future__ import annotations

import pytest

from engines.thermal.references import (
    backfill_materials,
    iec60228_max_r20,
    iec_60287_default_soil_resistivity,
    soil_thermal_resistivity,
    soil_types,
)


# ── IEC 60228 ────────────────────────────────────────────────────


class TestIEC60228:
    def test_canonical_class2_cu_240mm2(self):
        # IEC 60228:2004 Table 2 — Cu Class 2 240 mm² → 0.0754 Ω/km
        assert iec60228_max_r20(240, "Cu", 2) == 0.0754

    def test_canonical_class2_al_400mm2(self):
        # Al Class 2 400 mm² → 0.0778 Ω/km
        assert iec60228_max_r20(400, "Al", 2) == 0.0778

    def test_class5_higher_than_class2_for_same_csa(self):
        """Flexible (Class 5) generally has slightly higher R20 than stranded
        (Class 2) due to finer wire stranding. The gap is largest at small
        CSAs (25 mm² Cu: Class 2 = 0.727, Class 5 = 0.780); at some sizes
        rounding flips the order so we sample where the ordering is clear."""
        c2 = iec60228_max_r20(25, "Cu", 2)
        c5 = iec60228_max_r20(25, "Cu", 5)
        assert c5 > c2

    def test_unknown_csa_returns_none(self):
        assert iec60228_max_r20(123.4, "Cu", 2) is None

    def test_unknown_material_returns_none(self):
        # Class 5 has no Al entries in this dataset — flexibles are usually Cu only
        assert iec60228_max_r20(50, "Al", 5) is None

    def test_unknown_class_returns_none(self):
        assert iec60228_max_r20(240, "Cu", 99) is None

    def test_float_csa_normalises_to_integer_key(self):
        """240.0 must resolve the same as 240."""
        assert iec60228_max_r20(240.0, "Cu", 2) == iec60228_max_r20(240, "Cu", 2)

    def test_class_str_or_int_both_work(self):
        assert iec60228_max_r20(95, "Cu", 2) == iec60228_max_r20(95, "Cu", "2")

    def test_r20_decreases_with_csa(self):
        """Monotonically: bigger conductor → lower resistance."""
        sizes = [25, 50, 95, 185, 400, 630]
        r_values = [iec60228_max_r20(s, "Cu", 2) for s in sizes]
        assert all(r_values[i] > r_values[i + 1] for i in range(len(sizes) - 1))


# ── Soil ρ_T ────────────────────────────────────────────────────


class TestSoilResistivity:
    def test_iec_60287_default_is_one(self):
        assert iec_60287_default_soil_resistivity() == 1.0

    def test_moist_clay_typical_in_published_range(self):
        rho = soil_thermal_resistivity("moist_clay", prefer_range="typical")
        assert 1.0 < rho < 1.5

    def test_dry_sand_higher_than_wet_sand(self):
        """Dry-out raises ρ_T — the load-bearing reason cables fail by
        runaway thermal failure during drought."""
        wet = soil_thermal_resistivity("wet_sand")
        dry = soil_thermal_resistivity("dry_sand")
        assert dry > 2 * wet  # roughly 2.5x

    def test_lower_range_below_typical_below_upper(self):
        lower = soil_thermal_resistivity("moist_clay", prefer_range="lower")
        typical = soil_thermal_resistivity("moist_clay", prefer_range="typical")
        upper = soil_thermal_resistivity("moist_clay", prefer_range="upper")
        assert lower <= typical <= upper

    def test_unknown_soil_returns_none(self):
        assert soil_thermal_resistivity("unobtainium") is None

    def test_backfill_resolves_via_same_function(self):
        """CBS backfill is in the backfill_materials table — soil lookup
        should still find it (single lookup interface for callers)."""
        rho = soil_thermal_resistivity("cbs_thermal")
        assert rho is not None and rho < 1.0  # CBS is engineered low-rho

    def test_native_soil_returns_none_without_specific_choice(self):
        """native_soil is a placeholder — caller must pick a real soil_type."""
        assert soil_thermal_resistivity("native_soil") is None

    def test_soil_types_list_includes_clay_variants(self):
        types = soil_types()
        assert "moist_clay" in types
        assert "wet_clay" in types
        assert "dry_clay" in types

    def test_backfill_list_includes_cbs(self):
        assert "cbs_thermal" in backfill_materials()
