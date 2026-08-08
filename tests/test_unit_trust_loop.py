"""Unit tests: trust-loop's pure fault-current engine (daemon-free).

The engine is the sole compute path behind /trust-loop/api/calc and the
conformance gate. These tests pin the published anchor and every structural
property ALGORITHM.md section 6 nominates.

Anchor: ABB Technical Application Paper No. 2 cl. 2.2 worked example,
published result 14 943 A = 14.95 kA. Openly downloadable:
https://library.e.abb.com/public/2c522f583c884a4fbdf3968e1fdf1481/1SDC007101G0202.pdf
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

_ENGINE = Path(__file__).resolve().parents[1] / "apps/public/standard/trust-loop/fault_current.py"
_spec = importlib.util.spec_from_file_location("trust_loop_fault_current", _ENGINE)
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)

# Published inputs, verbatim from the paper.
PUBLISHED = {
    "u_net": 20000.0, "i_k_net": 14400.0, "c": 1.1,
    "r_mv": 0.360, "x_mv": 0.335,
    "s_n": 400000.0, "u_2n": 400.0, "vk_pct": 4.0, "pk_pct": 3.0,
    "r_lv": 0.000388, "x_lv": 0.000395,
}
PUBLISHED_A = 14943.0


class TestPublishedCase:
    """The anchor — ALGORITHM.md section 6.1."""

    def test_reproduces_published_current(self):
        r = fc.prospective_fault_current(**PUBLISHED)
        assert r["i_k3_a"] == pytest.approx(PUBLISHED_A, rel=0.005)

    def test_lands_just_below_the_printed_figure(self):
        # The paper rounds its intermediates, so full precision sits slightly
        # under. Pinning the direction stops a "fix" that chases the digits.
        r = fc.prospective_fault_current(**PUBLISHED)
        assert r["i_k3_a"] < PUBLISHED_A
        assert abs(r["i_k3_a"] - PUBLISHED_A) / PUBLISHED_A < 0.002

    def test_intermediate_impedances_match_the_printed_values(self):
        # Read off the source page: R_Tk 0.01256, X_Tk 0.01147, Z_Tk 0.017.
        # Bands are 1e-5 because the source TRUNCATES rather than rounds — its
        # own summands give 0.0125668, printed as 0.01256.
        r = fc.prospective_fault_current(**PUBLISHED)
        assert r["r_total_ohm"] == pytest.approx(0.01256, abs=1e-5)
        assert r["x_total_ohm"] == pytest.approx(0.01147, abs=1e-5)
        assert r["z_total_ohm"] == pytest.approx(0.017, abs=5e-5)

    def test_the_residual_is_the_sources_own_rounding(self):
        """The 0.06 % gap is explained, not tolerated.

        The paper divides by its printed Z_Tk = 0.017 ohm. Feeding that same
        rounded value through the final step reproduces 14 943 A exactly, so
        the deviation is the source's rounding and nothing else.
        """
        r = fc.prospective_fault_current(**PUBLISHED)
        from_rounded = 1.1 * 400.0 / (math.sqrt(3.0) * 0.017)
        assert from_rounded == pytest.approx(PUBLISHED_A, abs=1.0)
        # ...and full precision sits just below it, by less than a tenth of a percent.
        assert 0 < (PUBLISHED_A - r["i_k3_a"]) / PUBLISHED_A < 0.001

    def test_network_split_follows_the_stated_relationships(self):
        # The source states X = 0.995*Z and R = 0.1*X, not R = sqrt(Z^2-X^2).
        r = fc.prospective_fault_current(**PUBLISHED)
        net = r["contributions"][0]
        assert net["name"] == "Supply network"
        # rel=1e-4, not tighter: both are rounded to 9 dp on the way out, which
        # costs the last significant digit of a 3.5e-05 value.
        assert net["r_ohm"] == pytest.approx(net["x_ohm"] * 0.1, rel=1e-4)

    def test_transformer_rated_current(self):
        r = fc.prospective_fault_current(**PUBLISHED)
        assert r["transformer_i_2n_a"] == pytest.approx(577.0, abs=1.0)

    def test_referral_ratio(self):
        assert fc.prospective_fault_current(**PUBLISHED)["ratio"] == pytest.approx(50.0)


class TestStructuralProperties:
    """Hold for every input, not one point — ALGORITHM.md section 6.2."""

    def test_transformer_dominates_this_topology(self):
        r = fc.prospective_fault_current(**PUBLISHED)
        assert r["dominant"] == "Transformer"
        shares = {c["name"]: c["share_pct"] for c in r["contributions"]}
        assert shares["Transformer"] > 90
        assert all(v < shares["Transformer"] for k, v in shares.items() if k != "Transformer")

    def test_pythagoras_on_the_totals(self):
        r = fc.prospective_fault_current(**PUBLISHED)
        # rel=1e-6, not tighter: the returned values are rounded to 9 dp, so
        # hypot of the rounded parts cannot equal the rounded whole exactly.
        assert r["z_total_ohm"] == pytest.approx(
            math.hypot(r["r_total_ohm"], r["x_total_ohm"]), rel=1e-6)

    def test_more_impedance_never_raises_the_current(self):
        base = fc.prospective_fault_current(**PUBLISHED)["i_k3_a"]
        for key in ("r_mv", "x_mv", "r_lv", "x_lv", "vk_pct"):
            worse = fc.prospective_fault_current(**{**PUBLISHED, key: PUBLISHED[key] * 2})
            assert worse["i_k3_a"] <= base, f"raising {key} raised the current"

    def test_voltage_factor_is_linear(self):
        a = fc.prospective_fault_current(**{**PUBLISHED, "c": 1.0})["i_k3_a"]
        b = fc.prospective_fault_current(**{**PUBLISHED, "c": 2.0})["i_k3_a"]
        # c scales both the network impedance and the driving voltage, so the
        # ratio is not exactly 2 — but it must rise, and stay well-behaved.
        assert b > a
        assert 1.5 < b / a < 2.5

    def test_referral_round_trips(self):
        z = 0.36
        assert fc._referred(z, 50.0) * 50.0**2 == pytest.approx(z, rel=1e-12)

    def test_shares_sum_to_about_one_hundred(self):
        r = fc.prospective_fault_current(**PUBLISHED)
        total = sum(c["share_pct"] for c in r["contributions"])
        # Magnitudes of complex parts, so not exactly 100 — but close, because
        # every element here is dominated by one of R or X.
        assert 95 < total < 110


class TestNetworkImpedanceForm:
    """The paper uses the current form; the printed power form disagrees."""

    def test_current_form_value(self):
        z = fc.network_impedance(c=1.1, u_net=20000, i_k_net=14400)
        assert z == pytest.approx(0.882063, abs=1e-5)

    def test_power_form_would_not_match(self):
        # c^2*U^2/S with S = 500 MVA — the form the paper also prints.
        z_current = fc.network_impedance(c=1.1, u_net=20000, i_k_net=14400)
        z_power = (1.1**2) * (20000**2) / 500e6
        assert z_power != pytest.approx(z_current, rel=0.01)


class TestRefusals:
    """ALGORITHM.md section 6.3 — legal in, illegal refused."""

    @pytest.mark.parametrize("key", ["u_net", "i_k_net", "s_n", "u_2n", "vk_pct", "c"])
    def test_nonpositive_refused(self, key):
        with pytest.raises(ValueError):
            fc.prospective_fault_current(**{**PUBLISHED, key: 0})

    @pytest.mark.parametrize("key", ["r_mv", "x_mv", "r_lv", "x_lv"])
    def test_negative_impedance_refused(self, key):
        with pytest.raises(ValueError):
            fc.prospective_fault_current(**{**PUBLISHED, key: -1})

    def test_inconsistent_transformer_test_data_refused(self):
        # Huge load loss makes R_T exceed Z_T, so X_T would be imaginary.
        with pytest.raises(ValueError, match="inconsistent"):
            fc.prospective_fault_current(**{**PUBLISHED, "pk_pct": 90.0})

    def test_legal_inputs_accepted(self):
        assert fc.prospective_fault_current(**PUBLISHED)["i_k3_a"] > 0


class TestSensitivity:
    def test_sweep_is_monotonic_in_impedance(self):
        pts = fc.sensitivity(base=PUBLISHED, element="transformer", points=12)
        assert len(pts) > 5
        currents = [p["i_k3_ka"] for p in pts]
        assert currents == sorted(currents, reverse=True), "more impedance must mean less current"

    def test_unknown_element_refused(self):
        with pytest.raises(ValueError, match="unknown element"):
            fc.sensitivity(base=PUBLISHED, element="nonsense")


class TestCalculationReport:
    """Stage 6 — a sheet a reviewing engineer can check line by line."""

    def test_runs_from_inputs_to_result(self):
        steps = fc.prospective_fault_current(**PUBLISHED)["steps"]
        groups = []
        for s in steps:
            if s["group"] not in groups:
                groups.append(s["group"])
        assert groups == ["Given", "Supply network", "MV cable",
                          "Transformer", "LV cable", "Total and result"]

    def test_final_step_is_the_answer(self):
        r = fc.prospective_fault_current(**PUBLISHED)
        last = r["steps"][-1]
        assert last["symbol"] == "I_k3"
        # abs=0.01: the report keeps full precision, the headline is rounded
        # to 2 dp. They must agree to that rounding and no further.
        assert last["value"] == pytest.approx(r["i_k3_a"], abs=0.01)

    def test_every_step_is_checkable(self):
        # A line without a formula, a value or a unit-bearing symbol is not a
        # report line — it is decoration.
        for s in fc.prospective_fault_current(**PUBLISHED)["steps"]:
            assert s["symbol"] and s["formula"], s
            assert isinstance(s["value"], (int, float)), s
            assert s["value_str"], s

    def test_substitutions_carry_real_numbers(self):
        # The point of the report is that the reader can redo the arithmetic,
        # so a derived step must show the numbers that went in.
        derived = [s for s in fc.prospective_fault_current(**PUBLISHED)["steps"]
                   if s["substitution"] != "given"]
        assert len(derived) >= 14
        for s in derived:
            assert any(ch.isdigit() for ch in s["substitution"]), s

    def test_totals_show_their_summands(self):
        steps = {s["symbol"]: s for s in fc.prospective_fault_current(**PUBLISHED)["steps"]}
        # Four elements summed, so three plus signs in the substitution.
        assert steps["R_Tk"]["substitution"].count("+") == 3
        assert steps["X_Tk"]["substitution"].count("+") == 3

    def test_report_tracks_the_inputs(self):
        other = fc.prospective_fault_current(**{**PUBLISHED, "s_n": 630000.0})
        given = {s["symbol"]: s["value"] for s in other["steps"] if s["group"] == "Given"}
        assert given["S_n"] == 630000.0
