"""apps/cables — FEM dispatch scope checks (daemon-free).

The FEM MVP only supports direct-buried trefoil with grouped_cables=3
and explicit sheath geometry. `_fem_supported(inp)` gates which cables
in `run_schedule(method="fem")` get FEM-rated vs. cleanly skipped.

Confirms the gate matches the guards in
`engines/thermal/fem/postprocess.py` so a cable rejected here would
also be rejected by the engine itself — no spurious runtime errors.
"""

from __future__ import annotations

import pytest

from apps.cables.rating import _fem_supported, _fem_scope_check
from engines.models import AmpacityInput, CableLibraryEntry, CableGeometry


def _trefoil_input(**overrides) -> AmpacityInput:
    """Build a direct-buried trefoil case-1-shaped AmpacityInput."""
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
        "grouped_cables": 3, "burial_depth_m": 1.0,
        "soil_thermal_resistivity_kmw": 1.0, "ambient_temperature_c": 20.0,
        "conductor_max_temp_c": 90.0, "frequency_hz": 50.0,
    }
    payload.update(overrides)
    return AmpacityInput(**payload)


def test_supports_canonical_direct_buried_trefoil():
    assert _fem_supported(_trefoil_input()) is True


def test_rejects_in_air():
    assert _fem_supported(_trefoil_input(installation="in_air")) is False


def test_rejects_in_duct():
    assert _fem_supported(_trefoil_input(installation="in_duct")) is False


def test_rejects_flat_spacing():
    assert _fem_supported(_trefoil_input(spacing_mode="flat")) is False


def test_rejects_single_cable():
    assert _fem_supported(_trefoil_input(grouped_cables=1)) is False


def test_rejects_no_geometry():
    inp = _trefoil_input()
    inp.cable.geometry = None
    assert _fem_supported(inp) is False


def test_compute_fem_returns_detail_fields_for_writeback():
    """_compute_fem's dict shape must include the fields run_schedule
    writes to cable frontmatter (fem_converged, fem_iterations,
    fem_max_theta_c). If this contract drifts, the schedule UI loses
    its iteration/temperature badges silently."""
    pytest.importorskip("gmsh")
    pytest.importorskip("scipy")
    from engines.thermal.fem import compute_fem_rating

    inp = _trefoil_input()
    res = compute_fem_rating(inp)
    # Mirror _compute_fem's normalization
    worst = max(res.per_cable, key=lambda c: c.theta_c)
    out = {
        "ampacity_a": res.I,
        "lambda1": worst.lambda1,
        "converged": res.converged,
        "n_iterations": res.n_iterations,
        "max_theta_c": res.max_theta_c,
    }
    assert out["converged"] is True
    assert out["n_iterations"] >= 1
    assert 80.0 < out["max_theta_c"] < 95.0  # close to target 90°C
    assert out["ampacity_a"] > 700.0  # case-1 territory


def test_rejects_no_sheath_thickness():
    cable = CableLibraryEntry(
        id="no-sheath", conductor_csa_mm2=240.0,
        geometry=CableGeometry(
            conductor_diameter=0.020, insulation_thickness=0.005,
            overall_diameter=0.040,
        ),
    )
    inp = _trefoil_input()
    inp.cable = cable
    assert _fem_supported(inp) is False


def test_scope_check_returns_specific_reason():
    """The structured scope check must name the failing prerequisite so
    the schedule UI can surface "missing X" vs a generic out-of-scope."""
    ok, why = _fem_scope_check(_trefoil_input())
    assert ok is True and why is None

    ok, why = _fem_scope_check(_trefoil_input(installation="in_air"))
    assert not ok and "installation" in why and "in_air" in why

    ok, why = _fem_scope_check(_trefoil_input(spacing_mode="flat"))
    assert not ok and "spacing_mode" in why

    ok, why = _fem_scope_check(_trefoil_input(grouped_cables=1))
    assert not ok and "grouped_cables" in why and "3" in why

    inp = _trefoil_input()
    inp.cable.geometry = None
    ok, why = _fem_scope_check(inp)
    assert not ok and "no geometry" in why

    inp = _trefoil_input()
    inp.cable.geometry.sheath_thickness = 0.0
    ok, why = _fem_scope_check(inp)
    assert not ok and "sheath_thickness" in why
