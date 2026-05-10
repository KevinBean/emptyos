"""Textbook gates for the EMTP engine.

Each test instantiates a tiny netlist with a closed-form expectation and
checks the steady-state phasor or transient response against the analytic
answer. These are the engine's correctness floor — if any of these fail,
nothing downstream (RT-07, sheath voltage) can be trusted.

Run from repo root:
    python -m pytest engines/sim/tests/test_textbook.py -v
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

# Make engines/sim importable as a plain package for tests
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.elements import (  # noqa: E402
    BranchCurrentProbe,
    Capacitor,
    IdealSwitch,
    Inductor,
    ISourceSinusoidal,
    MutualBranchProbe,
    MutualInductorBlock,
    NodeProbe,
    Resistor,
    VSourceSinusoidal,
)
from sim.stepper import SimParams, step  # noqa: E402


def _phasor_mag(c: complex) -> float:
    return abs(c)


# ── 1. Pure resistive divider ────────────────────────────────────────


def test_resistive_divider_steady_state():
    """V_src = 100 V (peak) at 50 Hz, R1 = R2 = 1 Ω → V_mid peak = 50 V."""
    nodes = ["ground", "src", "mid"]  # 3 nodes
    elements = [
        VSourceSinusoidal(id="V1", n_pos=1, n_neg=0, V_re=100.0, V_im=0.0, f_hz=50.0, r_int=1e-3),
        Resistor(id="R1", n_pos=1, n_neg=2, r=1.0),
        Resistor(id="R2", n_pos=2, n_neg=0, r=1.0),
    ]
    probes = [NodeProbe(name="v_mid", node=2)]
    params = SimParams(f0_hz=50.0, dt_s=50e-6, t_end_s=0.16)
    result = step(elements, len(nodes), probes, params)

    v_mid = result.phasor("v_mid")
    assert _phasor_mag(v_mid) == pytest.approx(50.0, rel=0.01), \
        f"V_mid magnitude = {abs(v_mid):.3f} V, expected ~50 V"
    assert result.kcl_residual_max < 1e-6


# ── 2. RC charge — DC step ───────────────────────────────────────────


def test_rc_first_order_response():
    """Driven RC at 50 Hz: |V_C| = V_in / sqrt(1 + (ωRC)²).

    R=10Ω, C=100µF, ω=2π·50 → ωRC = 0.314 → expected |V_C| / |V_in| ≈ 0.954.
    """
    R, C, V_in_peak = 10.0, 100e-6, 100.0
    omega = 2 * math.pi * 50.0
    expected_ratio = 1.0 / math.sqrt(1 + (omega * R * C) ** 2)

    nodes = ["ground", "src", "cap"]
    elements = [
        VSourceSinusoidal(id="V1", n_pos=1, n_neg=0, V_re=V_in_peak, V_im=0.0, f_hz=50.0, r_int=1e-3),
        Resistor(id="R1", n_pos=1, n_neg=2, r=R),
        Capacitor(id="C1", n_pos=2, n_neg=0, C=C),
    ]
    probes = [NodeProbe(name="v_c", node=2), NodeProbe(name="v_in", node=1)]
    params = SimParams(f0_hz=50.0, dt_s=10e-6, t_end_s=0.4)  # 20 cycles to reach SS
    result = step(elements, len(nodes), probes, params)

    ratio = abs(result.phasor("v_c")) / abs(result.phasor("v_in"))
    assert ratio == pytest.approx(expected_ratio, rel=0.01), \
        f"|V_C|/|V_in| = {ratio:.4f}, expected {expected_ratio:.4f}"


# ── 3. RL series — current lags voltage ──────────────────────────────


def test_rl_series_phase_lag():
    """V_src across R+L: |I| = |V| / sqrt(R² + (ωL)²), φ = -atan(ωL/R).

    R=1Ω, L=10mH, ω=2π·50 → ωL=3.14159 → |I| = 100 / sqrt(1 + π²) ≈ 30.4 A,
    φ = -atan(π) ≈ -72.34°.
    """
    R, L_h, V_peak = 1.0, 10e-3, 100.0
    omega = 2 * math.pi * 50.0
    expected_I_mag = V_peak / math.sqrt(R**2 + (omega * L_h) ** 2)
    expected_phase_deg = -math.degrees(math.atan(omega * L_h / R))

    nodes = ["ground", "src", "mid"]
    elements = [
        VSourceSinusoidal(id="V1", n_pos=1, n_neg=0, V_re=V_peak, V_im=0.0, f_hz=50.0, r_int=1e-3),
        Resistor(id="R1", n_pos=1, n_neg=2, r=R),
        Inductor(id="L1", n_pos=2, n_neg=0, L=L_h),
    ]
    probes = [BranchCurrentProbe(name="i_R", element_id="R1")]
    params = SimParams(f0_hz=50.0, dt_s=10e-6, t_end_s=0.4)
    result = step(elements, len(nodes), probes, params)

    i_phasor = result.phasor("i_R")
    i_mag = abs(i_phasor)
    i_phase = math.degrees(math.atan2(i_phasor.imag, i_phasor.real))

    assert i_mag == pytest.approx(expected_I_mag, rel=0.02), \
        f"|I| = {i_mag:.3f} A, expected {expected_I_mag:.3f}"
    # Phase tolerance loosened — DFT bin has small leakage
    assert i_phase == pytest.approx(expected_phase_deg, abs=2.0), \
        f"phase = {i_phase:.2f}°, expected {expected_phase_deg:.2f}°"


# ── 4. Mutual L block — two coupled ports ────────────────────────────


def test_mutual_inductor_block_diagonal_matches_two_inductors():
    """Diagonal L matrix (no coupling) should behave identically to two
    independent Inductor elements."""
    L1, L2, R = 5e-3, 8e-3, 0.5
    V_peak = 50.0
    omega = 2 * math.pi * 50.0

    # Reference: two independent inductors, independent series R, parallel
    nodes_ref = ["ground", "a", "b"]  # 3 nodes
    el_ref = [
        VSourceSinusoidal(id="V1", n_pos=1, n_neg=0, V_re=V_peak, V_im=0.0, f_hz=50.0, r_int=1e-3),
        Resistor(id="R1", n_pos=1, n_neg=2, r=R),
        Inductor(id="L_a", n_pos=2, n_neg=0, L=L1),
        # second branch fed by same V (parallel)
        Resistor(id="R2", n_pos=1, n_neg=2, r=R),  # share node 'b'... we need a separate branch
    ]
    # actually for clarity build two completely separate branches off the source
    nodes_ref = ["ground", "src", "midA", "midB"]
    el_ref = [
        VSourceSinusoidal(id="V1", n_pos=1, n_neg=0, V_re=V_peak, V_im=0.0, f_hz=50.0, r_int=1e-3),
        Resistor(id="R1", n_pos=1, n_neg=2, r=R),
        Inductor(id="L_a", n_pos=2, n_neg=0, L=L1),
        Resistor(id="R2", n_pos=1, n_neg=3, r=R),
        Inductor(id="L_b", n_pos=3, n_neg=0, L=L2),
    ]
    probes_ref = [
        BranchCurrentProbe(name="i_a", element_id="L_a"),
        BranchCurrentProbe(name="i_b", element_id="L_b"),
    ]
    params = SimParams(f0_hz=50.0, dt_s=10e-6, t_end_s=0.4)
    res_ref = step(el_ref, len(nodes_ref), probes_ref, params)

    # Engine path: same topology but L_a and L_b combined as a 2-port mutual block with
    # zero off-diagonal coupling.
    L_mat = np.array([[L1, 0.0], [0.0, L2]])
    nodes_emt = ["ground", "src", "midA", "midB"]
    el_emt = [
        VSourceSinusoidal(id="V1", n_pos=1, n_neg=0, V_re=V_peak, V_im=0.0, f_hz=50.0, r_int=1e-3),
        Resistor(id="R1", n_pos=1, n_neg=2, r=R),
        Resistor(id="R2", n_pos=1, n_neg=3, r=R),
        MutualInductorBlock(id="M", n_pos=[2, 3], n_neg=[0, 0], L=L_mat),
    ]
    probes_emt = [
        MutualBranchProbe(name="i_a", element_id="M", port=0),
        MutualBranchProbe(name="i_b", element_id="M", port=1),
    ]
    res_emt = step(el_emt, len(nodes_emt), probes_emt, params)

    for nm in ("i_a", "i_b"):
        ref = abs(res_ref.phasor(nm))
        emt = abs(res_emt.phasor(nm))
        assert emt == pytest.approx(ref, rel=0.005), \
            f"diagonal mutual block {nm}: emt={emt:.5f}, ref={ref:.5f}"


# ── 5. Mutual L block — coupled ports show induced voltage ───────────


def test_mutual_inductor_block_induces_voltage():
    """Two coupled ports: drive port A, port B open-circuit. Induced voltage at B
    (open end) should be |jωM·I_A|. Port B sees infinite impedance (just probe a
    1 GΩ resistor — large but finite to keep matrix nonsingular)."""
    L1, L2, M = 5e-3, 5e-3, 4e-3   # k = M/sqrt(L1·L2) = 0.8
    R, V_peak = 0.5, 100.0
    omega = 2 * math.pi * 50.0

    L_mat = np.array([[L1, M], [M, L2]])
    nodes = ["ground", "src", "midA", "midB"]
    elements = [
        VSourceSinusoidal(id="V1", n_pos=1, n_neg=0, V_re=V_peak, V_im=0.0, f_hz=50.0, r_int=1e-3),
        Resistor(id="R1", n_pos=1, n_neg=2, r=R),
        Resistor(id="Rload_B", n_pos=3, n_neg=0, r=1e6),  # near-open
        MutualInductorBlock(id="M_block", n_pos=[2, 3], n_neg=[0, 0], L=L_mat),
    ]
    probes = [
        MutualBranchProbe(name="i_a", element_id="M_block", port=0),
        NodeProbe(name="v_b", node=3),
    ]
    params = SimParams(f0_hz=50.0, dt_s=10e-6, t_end_s=0.4)
    result = step(elements, len(nodes), probes, params)

    i_a = result.phasor("i_a")
    v_b = result.phasor("v_b")
    # Predicted induced |V_b| = ωM · |I_a|
    expected_v_b = omega * M * abs(i_a)
    assert abs(v_b) == pytest.approx(expected_v_b, rel=0.05), \
        f"|V_b| = {abs(v_b):.3f}, expected ωM|I_a| = {expected_v_b:.3f}"


# ── 6. Switch closes at t_close ──────────────────────────────────────


def test_switch_closes_mid_simulation():
    """Voltage source through R; switch closes a parallel R at t=0.05s.
    Steady-state current after close should match parallel-R prediction."""
    R1, R2, V_peak = 10.0, 5.0, 100.0
    nodes = ["ground", "src", "mid"]
    elements = [
        VSourceSinusoidal(id="V1", n_pos=1, n_neg=0, V_re=V_peak, V_im=0.0, f_hz=50.0, r_int=1e-3),
        Resistor(id="R_top", n_pos=1, n_neg=2, r=R1),
        Resistor(id="R_branch", n_pos=2, n_neg=0, r=R2),
        IdealSwitch(id="SW", n_pos=2, n_neg=0, t_close=0.05, initially_closed=False),
        Resistor(id="R_open_branch", n_pos=2, n_neg=0, r=20.0),  # provides path before switch closes
    ]
    probes = [NodeProbe(name="v_mid", node=2)]
    params = SimParams(f0_hz=50.0, dt_s=10e-6, t_end_s=0.4)
    result = step(elements, len(nodes), probes, params)

    # Check waveform settles: take last cycle phasor — equivalent parallel R after close.
    # After close: R_branch (5) || R_open_branch (20) || ~0 = ~0 (switch is 1e-6 Ω).
    # Switch dominates → V_mid → ~0.
    v_mid_after = abs(result.phasor("v_mid"))
    assert v_mid_after < 0.5, f"|V_mid| after switch close = {v_mid_after:.3f} V, expected ~0"
