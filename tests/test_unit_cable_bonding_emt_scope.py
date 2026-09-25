"""LIM-EMT-SINGLE — the EMT modal build is one cable, and says so.

The app's scope is a three-phase single-point bonded circuit, flat or trefoil
(ALGORITHM.md section 1). `_emt` places a single `CablePosition`, so the modal
build carries two conductors and none of the circuit's inter-phase modes
(Ametani 2021 section 3.4.2). That is a stated limitation, not a defect — but
only while it is stated. These tests execute the build rather than reading the
source. They are a tripwire: adding a second cable fails them whether or not
the docs moved, which forces whoever does it to revisit LIM-EMT-SINGLE. The
note check is a presence pin (the id and the headline sentence), not a proof
that the note is fully accurate.

Daemon-free: the app module is loaded standalone.
"""
from __future__ import annotations

import pytest

from test_unit_cable_bonding_report_partial import FULL, _mod

pytest.importorskip("scipy")

EMT_INPUTS = {
    # 10 us front -> 50 kHz, inside the ~100 kHz bound, so no bandwidth warning
    # muddies what these tests read.
    "front_time_us": 10.0,
    "emt_r_core_outer_m": 0.0150, "emt_r_sheath_inner_m": 0.0320,
    "emt_r_sheath_outer_m": 0.0330, "emt_r_outer_m": 0.0370,
    "emt_rho_core_ohm_m": 1.7241e-8, "emt_rho_sheath_ohm_m": 21.4e-8,
    "emt_eps_r_insulation": 2.5,
}


def _build(**over):
    m = _mod()
    return m._emt(m._coerce({**FULL, **EMT_INPUTS, **over}))


def test_the_build_is_one_cable_not_the_circuit():
    """Pins the conductor count: labels name conductors, not modes."""
    r = _build()
    assert r["labels"] == ["a.core", "a.sheath"]
    assert len(r["modal_surge_impedance_ohm"]) == 2


def test_formation_and_spacing_do_not_reach_the_build():
    """The limitation's own claim: flat vs trefoil and a changed spacing leave
    the modal constants untouched, because only one cable is placed."""
    flat = _build(formation="flat", spacing_m=0.2)
    tre = _build(formation="trefoil", spacing_m=0.5)
    assert flat["modal_surge_impedance_ohm"] == tre["modal_surge_impedance_ohm"]
    assert flat["modal_velocity_m_per_s"] == tre["modal_velocity_m_per_s"]


def test_the_result_tells_the_caller():
    note = _build()["note"]
    assert "LIM-EMT-SINGLE" in note
    assert "One cable is modelled" in note
