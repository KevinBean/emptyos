"""apps/cables — sheath standing voltage helper (daemon-free).

`_compute_sheath_voltage(inp, I, cable_fm)` wraps
`engines.thermal.iec60287.sheath_voltage.standing_voltage()` and is
called by `run_schedule` after each cable's ampacity is computed.
The wrapper is responsible for pulling sheath geometry off the
library entry, deciding the formation, falling back from
`minor_section_length_m` to `length_m / 3`, and returning ``None``
when prerequisites are missing rather than raising.
"""

from __future__ import annotations

import math

import pytest

from apps.cables.rating import _compute_sheath_voltage
from engines.models import AmpacityInput, CableGeometry, CableLibraryEntry


def _trefoil_input(**overrides) -> AmpacityInput:
    cable = CableLibraryEntry(
        id="test", rated_voltage_kv=132.0, conductor_csa_mm2=630.0,
        conductor_material="Cu", insulation_material="XLPE",
        sheath_material="Al",
        geometry=CableGeometry(
            conductor_diameter=0.0303,
            insulation_thickness=0.0155,
            sheath_thickness=0.0008,
            sheath_inner_diameter=0.0669,
            overall_diameter=0.0755,
        ),
    )
    payload = {
        "cable": cable, "installation": "direct_buried",
        "bonding": "single_point", "spacing_mode": "trefoil",
        "spacing_m": 0.20,
        "grouped_cables": 3, "burial_depth_m": 1.0,
        "soil_thermal_resistivity_kmw": 1.0, "ambient_temperature_c": 20.0,
        "conductor_max_temp_c": 90.0, "frequency_hz": 50.0,
    }
    payload.update(overrides)
    return AmpacityInput(**payload)


def test_solidly_bonded_returns_zero_with_no_other_inputs_required():
    inp = _trefoil_input(bonding="solidly_bonded")
    sv = _compute_sheath_voltage(inp, ampacity_a=800.0, cable_fm={})
    assert sv == {
        "sheath_voltage_v": 0.0,
        "sheath_voltage_regime": "solid",
        "sheath_voltage_e_per_m": 0.0,
    }


def test_single_point_grows_linearly_with_length():
    inp = _trefoil_input(bonding="single_point")
    sv_short = _compute_sheath_voltage(inp, 800.0, {"length_m": 500.0})
    sv_long = _compute_sheath_voltage(inp, 800.0, {"length_m": 1500.0})
    assert sv_short and sv_long
    # U_v scales linearly with L (helper rounds to 0.01 V, hence abs tol)
    assert sv_long["sheath_voltage_v"] == pytest.approx(
        3.0 * sv_short["sheath_voltage_v"], abs=0.05
    )
    # E [V/m] is the same at the two lengths
    assert sv_short["sheath_voltage_e_per_m"] == pytest.approx(
        sv_long["sheath_voltage_e_per_m"], rel=1e-6
    )
    assert sv_short["sheath_voltage_regime"] == "single-point end"


def test_cross_bonded_falls_back_to_length_div_3_when_minor_missing():
    inp = _trefoil_input(bonding="cross_bonded")
    explicit = _compute_sheath_voltage(
        inp, 800.0, {"length_m": 1500.0, "minor_section_length_m": 500.0},
    )
    fallback = _compute_sheath_voltage(inp, 800.0, {"length_m": 1500.0})
    assert explicit and fallback
    # 1500/3 == 500 → identical answers
    assert explicit["sheath_voltage_v"] == pytest.approx(fallback["sheath_voltage_v"])
    assert fallback["sheath_voltage_regime"] == "cross-bonded peak"


def test_cross_bonded_lower_than_single_point_for_same_total_route():
    """For symmetric cross-bonding, U_max < U_single_point on the same
    cable length — the load-bearing reason cross-bonding exists.
    """
    sp_inp = _trefoil_input(bonding="single_point")
    cb_inp = _trefoil_input(bonding="cross_bonded")
    sp = _compute_sheath_voltage(sp_inp, 800.0, {"length_m": 1500.0})
    cb = _compute_sheath_voltage(cb_inp, 800.0, {"length_m": 1500.0})
    assert sp["sheath_voltage_v"] > cb["sheath_voltage_v"]


def test_returns_none_when_no_spacing():
    inp = _trefoil_input(bonding="single_point", spacing_m=None, spacing_mode="touching")
    assert _compute_sheath_voltage(inp, 800.0, {"length_m": 1000.0}) is None


def test_returns_none_when_geometry_missing_sheath_fields():
    cable = CableLibraryEntry(
        id="bare", rated_voltage_kv=11.0, conductor_csa_mm2=240.0,
        conductor_material="Cu", insulation_material="XLPE",
        sheath_material="Al",
        geometry=CableGeometry(
            conductor_diameter=0.020,
            insulation_thickness=0.005,
            overall_diameter=0.040,
            # no sheath_thickness / sheath_inner_diameter
        ),
    )
    inp = AmpacityInput(
        cable=cable, installation="direct_buried",
        bonding="single_point", spacing_mode="trefoil",
        spacing_m=0.15, grouped_cables=3, burial_depth_m=1.0,
        soil_thermal_resistivity_kmw=1.0, ambient_temperature_c=20.0,
        conductor_max_temp_c=90.0, frequency_hz=50.0,
    )
    assert _compute_sheath_voltage(inp, 800.0, {"length_m": 1000.0}) is None


def test_returns_none_for_non_solid_bond_without_length():
    inp = _trefoil_input(bonding="single_point")
    assert _compute_sheath_voltage(inp, 800.0, {}) is None


def test_flat_formation_outer_voltage_higher_than_trefoil():
    """Outer cable in flat formation sees a higher induced field per IEC
    Annex E than the trefoil case at the same spacing.
    """
    tref = _trefoil_input(bonding="single_point", spacing_mode="trefoil")
    flat = _trefoil_input(bonding="single_point", spacing_mode="flat")
    sv_t = _compute_sheath_voltage(tref, 800.0, {"length_m": 1000.0})
    sv_f = _compute_sheath_voltage(flat, 800.0, {"length_m": 1000.0})
    assert sv_f["sheath_voltage_v"] > sv_t["sheath_voltage_v"]


def test_iec_60840_typical_range_for_2km_132kv_cable():
    """Sanity: a 2-km single-point bonded 132 kV trefoil at ~800 A should
    produce an induced voltage in the order of 100-300 V — the regime
    that drives SVL sizing.
    """
    inp = _trefoil_input(bonding="single_point")
    sv = _compute_sheath_voltage(inp, 800.0, {"length_m": 2000.0})
    assert sv is not None
    assert 50.0 < sv["sheath_voltage_v"] < 500.0
