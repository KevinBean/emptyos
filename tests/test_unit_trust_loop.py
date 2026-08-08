"""Unit tests: trust-loop's pure IEEE 80 engine (daemon-free).

The engine is the sole compute path behind /trust-loop/api/calc and the
conformance gate. These tests pin the anchor case the article publishes
(188.65 V touch / 262.48 V step within 0.5%), the surface-layer derating
factor, the body-weight factors, and the applicability-window refusals.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

_ENGINE = Path(__file__).resolve().parents[1] / "apps/public/standard/trust-loop/ieee80.py"
_spec = importlib.util.spec_from_file_location("trust_loop_ieee80", _ENGINE)
ieee80 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ieee80)


class TestAnchorCase:
    """The standard's published case — the conformance gate's ground truth."""

    def test_touch_matches_published(self):
        r = ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=50)
        assert r["e_touch_v"] == pytest.approx(188.65, rel=0.005)

    def test_step_matches_published(self):
        r = ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=50)
        assert r["e_step_v"] == pytest.approx(262.48, rel=0.005)

    def test_no_layer_means_cs_is_one(self):
        r = ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=50)
        assert r["c_s"] == 1.0
        assert r["rho_surface"] == 100.0

    def test_intermediates_multiply_out(self):
        r = ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=50)
        assert r["e_touch_v"] == pytest.approx(r["i_b_a"] * r["r_touch_ohm"], rel=1e-3)
        assert r["e_step_v"] == pytest.approx(r["i_b_a"] * r["r_step_ohm"], rel=1e-3)


class TestPhysics:
    def test_step_exceeds_touch(self):
        # 6x surface term vs 1.5x — step tolerable is always the higher limit.
        r = ieee80.tolerable_voltages(rho=250, t_s=1.0, body_kg=50)
        assert r["e_step_v"] > r["e_touch_v"]

    def test_70kg_body_tolerates_more(self):
        r50 = ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=50)
        r70 = ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=70)
        assert r70["e_touch_v"] == pytest.approx(r50["e_touch_v"] * 0.157 / 0.116, rel=1e-4)

    def test_shorter_fault_tolerates_more(self):
        fast = ieee80.tolerable_voltages(rho=100, t_s=0.1, body_kg=50)
        slow = ieee80.tolerable_voltages(rho=100, t_s=1.0, body_kg=50)
        assert fast["e_touch_v"] > slow["e_touch_v"]
        assert fast["e_touch_v"] == pytest.approx(slow["e_touch_v"] * math.sqrt(10), rel=1e-4)

    def test_monotonic_in_resistivity(self):
        lo = ieee80.tolerable_voltages(rho=50, t_s=0.5, body_kg=50)
        hi = ieee80.tolerable_voltages(rho=500, t_s=0.5, body_kg=50)
        assert hi["e_touch_v"] > lo["e_touch_v"]


class TestSurfaceLayer:
    def test_crushed_rock_derating(self):
        # The standard's well-known example: 2500 ohm-m rock, 0.1 m over 100 ohm-m soil.
        cs = ieee80.surface_derating(100, 2500, 0.1)
        assert cs == pytest.approx(0.7021, abs=1e-3)

    def test_layer_raises_tolerable_touch(self):
        bare = ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=50)
        rock = ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=50, rho_s=2500, h_s=0.1)
        assert rock["e_touch_v"] > bare["e_touch_v"]

    def test_same_resistivity_layer_is_no_layer(self):
        assert ieee80.surface_derating(100, 100, 0.1) == 1.0

    def test_conductive_layer_warns(self):
        r = ieee80.tolerable_voltages(rho=1000, t_s=0.5, body_kg=50, rho_s=100, h_s=0.1)
        assert r["warnings"]


class TestValidation:
    def test_rejects_nonpositive_rho(self):
        with pytest.raises(ValueError):
            ieee80.tolerable_voltages(rho=0, t_s=0.5, body_kg=50)

    def test_rejects_duration_outside_window(self):
        with pytest.raises(ValueError):
            ieee80.tolerable_voltages(rho=100, t_s=0.01, body_kg=50)
        with pytest.raises(ValueError):
            ieee80.tolerable_voltages(rho=100, t_s=5.0, body_kg=50)

    def test_rejects_unknown_body_weight(self):
        with pytest.raises(ValueError):
            ieee80.tolerable_voltages(rho=100, t_s=0.5, body_kg=60)


class TestDurationCurve:
    """The chart plots engine output — so the sweep is pinned like any answer."""

    def test_spans_the_whole_applicability_window(self):
        c = ieee80.duration_curve(rho=100, body_kg=50)
        assert c[0]["t_s"] == pytest.approx(ieee80.T_MIN_S)
        assert c[-1]["t_s"] == pytest.approx(ieee80.T_MAX_S)

    def test_every_point_is_inside_the_window(self):
        # Float drift at the log ends would make the engine raise, not clamp.
        c = ieee80.duration_curve(rho=100, body_kg=50, points=200)
        assert all(ieee80.T_MIN_S <= p["t_s"] <= ieee80.T_MAX_S for p in c)

    def test_monotonically_decreasing(self):
        c = ieee80.duration_curve(rho=100, body_kg=50)
        assert all(c[i]["e_touch_v"] >= c[i + 1]["e_touch_v"] for i in range(len(c) - 1))
        assert all(c[i]["e_step_v"] >= c[i + 1]["e_step_v"] for i in range(len(c) - 1))

    def test_curve_agrees_with_the_single_point_call(self):
        # The headline number and the chart must never disagree — same engine.
        c = ieee80.duration_curve(rho=250, body_kg=70, points=3)
        for p in c:
            one = ieee80.tolerable_voltages(rho=250, t_s=p["t_s"], body_kg=70)
            assert p["e_touch_v"] == pytest.approx(one["e_touch_v"], abs=0.02)

    def test_point_count_is_bounded(self):
        assert len(ieee80.duration_curve(rho=100, points=1)) == 2
        assert len(ieee80.duration_curve(rho=100, points=9999)) == 200

    def test_surface_layer_lifts_the_whole_curve(self):
        bare = ieee80.duration_curve(rho=100, points=6)
        rock = ieee80.duration_curve(rho=100, rho_s=2500, h_s=0.1, points=6)
        assert all(r["e_touch_v"] > b["e_touch_v"] for b, r in zip(bare, rock))
