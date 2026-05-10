"""IEC 60287-2-1 §4.2.3.2 — generic mutual-heating coefficients."""

from __future__ import annotations

import math

import pytest

from engines.thermal.iec60287.mutual_heating import (
    delta_theta_p_coefficients_flat_3,
    flat_3_geometry_factors,
    flat_positions,
    geometry_factors,
    mutual_heating_coefficients,
    trefoil_positions,
)


# ── Position builders ──────────────────────────────────────────────


class TestFlatPositions:
    def test_canonical_ids_for_three(self):
        pos = flat_positions(3, 0.165, 1.0)
        assert set(pos.keys()) == {"lag", "mid", "lead"}
        # Centred on x=0
        assert pos["lag"][0] == pytest.approx(-0.165)
        assert pos["mid"][0] == pytest.approx(0.0)
        assert pos["lead"][0] == pytest.approx(0.165)
        # All at burial depth
        assert all(p[1] == 1.0 for p in pos.values())

    def test_arbitrary_n(self):
        pos = flat_positions(5, 0.2, 0.8)
        assert len(pos) == 5
        # Centred — sum of x-coords = 0
        assert sum(p[0] for p in pos.values()) == pytest.approx(0.0)

    def test_custom_ids(self):
        pos = flat_positions(2, 0.3, 1.0, ids=["A", "B"])
        assert set(pos.keys()) == {"A", "B"}

    def test_rejects_bad_inputs(self):
        with pytest.raises(ValueError):
            flat_positions(0, 0.1, 1.0)
        with pytest.raises(ValueError):
            flat_positions(3, -0.1, 1.0)
        with pytest.raises(ValueError):
            flat_positions(3, 0.1, 0.0)
        with pytest.raises(ValueError):
            flat_positions(3, 0.1, 1.0, ids=["only-one"])


class TestTrefoilPositions:
    def test_three_equidistant(self):
        s = 0.1
        L = 1.0
        pos = trefoil_positions(s, L)
        assert len(pos) == 3
        ids = list(pos.keys())
        # All pairwise distances should equal s.
        for i in range(3):
            for j in range(i + 1, 3):
                xi, yi = pos[ids[i]]
                xj, yj = pos[ids[j]]
                d = math.hypot(xi - xj, yi - yj)
                assert d == pytest.approx(s, rel=1e-9)

    def test_point_up_apex_shallower(self):
        pos = trefoil_positions(0.1, 1.0, orientation="point_up")
        # Apex (third cable) should be at shallower depth than base pair.
        depths = sorted(p[1] for p in pos.values())
        # Two cables at base depth (deeper), one at apex (shallower)
        assert depths[0] < depths[1] == pytest.approx(depths[2])

    def test_point_down_apex_deeper(self):
        pos = trefoil_positions(0.1, 1.0, orientation="point_down")
        depths = sorted(p[1] for p in pos.values())
        # One cable at apex (deeper), two at top (shallower)
        assert depths[0] == pytest.approx(depths[1])
        assert depths[1] < depths[2]

    def test_rejects_bad_orientation(self):
        with pytest.raises(ValueError, match="orientation"):
            trefoil_positions(0.1, 1.0, orientation="sideways")


# ── Geometry factors ──────────────────────────────────────────────


class TestGeometryFactors:
    def test_pairwise_symmetry(self):
        # F_kp = F_pk because |k_image-p| == |p_image-k| when
        # depths are equal (image flip is symmetric in y).
        pos = flat_positions(3, 0.165, 1.0)
        F = geometry_factors(pos, rho_t=0.8)
        assert F["lag"]["mid"] == pytest.approx(F["mid"]["lag"])
        assert F["mid"]["lead"] == pytest.approx(F["lead"]["mid"])
        assert F["lag"]["lead"] == pytest.approx(F["lead"]["lag"])

    def test_excludes_self(self):
        pos = flat_positions(3, 0.165, 1.0)
        F = geometry_factors(pos, rho_t=0.8)
        for cid, neighbours in F.items():
            assert cid not in neighbours

    def test_far_separation_factor_falls(self):
        # Distant neighbours contribute less than near ones.
        pos_close = flat_positions(2, 0.1, 1.0)
        pos_far = flat_positions(2, 5.0, 1.0)
        F_close = geometry_factors(pos_close, rho_t=1.0)
        F_far = geometry_factors(pos_far, rho_t=1.0)
        # At s=5m, depth=1m: source near surface ⇒ image close to source
        # ⇒ ln(d'/d) → 0 ⇒ F → 0.
        f_close = list(F_close["c1"].values())[0]
        f_far = list(F_far["c1"].values())[0]
        assert f_close > f_far
        assert f_far < 0.05

    def test_rejects_zero_depth(self):
        with pytest.raises(ValueError, match="depth"):
            geometry_factors({"a": (0.0, 0.0), "b": (0.1, 1.0)}, rho_t=1.0)

    def test_rejects_coincident_cables(self):
        with pytest.raises(ValueError, match="share position"):
            geometry_factors(
                {"a": (0.0, 1.0), "b": (0.0, 1.0)}, rho_t=1.0,
            )


class TestTrefoilSymmetry:
    def test_factors_nearly_uniform_when_depth_dominates_spacing(self):
        # In an equilateral-triangle trefoil with burial_depth >> spacing,
        # the direct distances are exactly equal (=s), and the image
        # distances are nearly equal (depths differ by ~s/√3, dwarfed
        # by 2L). So F factors should be nearly uniform.
        pos = trefoil_positions(0.05, 2.0, ids=("a", "b", "c"))
        F = geometry_factors(pos, rho_t=0.8)
        all_F = [F[r][s] for r in F for s in F[r]]
        # Spread should be a small fraction of the mean.
        spread = (max(all_F) - min(all_F)) / (sum(all_F) / len(all_F))
        assert spread < 0.05

    def test_direct_distances_exactly_equal(self):
        # Even when image-plane asymmetry breaks F uniformity, the
        # direct cable-to-cable distance is exactly s for all three
        # pairs. That's the geometric invariant of trefoil.
        s = 0.1
        pos = trefoil_positions(s, 0.5, ids=("a", "b", "c"))
        ids = list(pos.keys())
        for i in range(3):
            for j in range(i + 1, 3):
                xi, yi = pos[ids[i]]
                xj, yj = pos[ids[j]]
                assert math.hypot(xi - xj, yi - yj) == pytest.approx(s, rel=1e-12)


# ── Mutual heating coefficients ────────────────────────────────────


class TestCoefficients:
    def test_identical_lambdas_yield_identical_a(self):
        pos = flat_positions(3, 0.165, 1.0)
        lams = {"lag": 0.3, "mid": 0.3, "lead": 0.3}
        coeffs = mutual_heating_coefficients(
            pos, rho_t=0.8, R_ac=1e-4, lambda1_per_cable=lams, W_d=0.0,
        )
        # Outer cables (lag, lead) see one near + one far neighbour;
        # mid sees two near neighbours. So a_mid > a_outer when
        # lambdas are identical.
        a_mid = coeffs["mid"][0]
        a_lag = coeffs["lag"][0]
        a_lead = coeffs["lead"][0]
        assert a_lag == pytest.approx(a_lead, rel=1e-9)
        assert a_mid > a_lag

    def test_zero_lambdas_zero_W_d_gives_zero_b(self):
        pos = flat_positions(3, 0.165, 1.0)
        coeffs = mutual_heating_coefficients(
            pos, rho_t=0.8, R_ac=1e-4,
            lambda1_per_cable={"lag": 0, "mid": 0, "lead": 0},
            W_d=0.0,
        )
        for (a, b) in coeffs.values():
            assert b == 0.0
            assert a > 0  # I² coefficient still drives Δθ_P from I²R losses

    def test_missing_lambda_key_rejected(self):
        pos = flat_positions(3, 0.165, 1.0)
        with pytest.raises(ValueError, match="lambda1_per_cable"):
            mutual_heating_coefficients(
                pos, rho_t=0.8, R_ac=1e-4,
                lambda1_per_cable={"lag": 0, "mid": 0},  # 'lead' missing
                W_d=0.0,
            )

    def test_flat_5_outermost_smallest_a(self):
        # For 5 cables in a row with uniform lambdas, the outermost
        # cables see fewer near neighbours than the central one.
        pos = flat_positions(5, 0.2, 1.0)
        lams = {cid: 0.2 for cid in pos}
        coeffs = mutual_heating_coefficients(
            pos, rho_t=0.8, R_ac=1e-4, lambda1_per_cable=lams, W_d=0.0,
        )
        a_values = {cid: coeffs[cid][0] for cid in coeffs}
        # Centre cable c3 has two close neighbours on each side.
        assert a_values["c3"] > a_values["c1"]
        assert a_values["c3"] > a_values["c5"]
        # Outer cables symmetric.
        assert a_values["c1"] == pytest.approx(a_values["c5"], rel=1e-9)


# ── Backward compatibility ─────────────────────────────────────────


class TestFlat3BackwardCompat:
    """The wrappers must produce the same numbers as the prior
    hardcoded implementation. The brief locks F values for TB 880
    Case 0-3: F_lag-mid = F_lead-mid ≈ 0.318, F_lead-lag ≈ 0.231 K·m/W
    at s=165mm, L=1000mm, ρ=0.8 K·m/W.
    """

    def test_tb880_reference_factors(self):
        F = flat_3_geometry_factors(0.165, 1.0, 0.8)
        assert F["lag"]["mid"] == pytest.approx(0.318, abs=0.005)
        assert F["lead"]["mid"] == pytest.approx(0.318, abs=0.005)
        assert F["lead"]["lag"] == pytest.approx(0.231, abs=0.005)

    def test_wrapper_matches_generic(self):
        F_old = flat_3_geometry_factors(0.165, 1.0, 0.8)
        F_new = geometry_factors(flat_positions(3, 0.165, 1.0), 0.8)
        for r in ("lag", "mid", "lead"):
            for s in ("lag", "mid", "lead"):
                if r == s:
                    continue
                assert F_old[r][s] == pytest.approx(F_new[r][s], rel=1e-12)

    def test_coefficients_wrapper_matches_generic(self):
        lams = {"lag": 0.3, "mid": 0.5, "lead": 0.4}
        old = delta_theta_p_coefficients_flat_3(
            0.165, 1.0, 0.8, R_ac=1.5e-4, lambda1_per_cable=lams, W_d=0.5,
        )
        new = mutual_heating_coefficients(
            flat_positions(3, 0.165, 1.0),
            rho_t=0.8, R_ac=1.5e-4, lambda1_per_cable=lams, W_d=0.5,
        )
        for cid in ("lag", "mid", "lead"):
            assert old[cid][0] == pytest.approx(new[cid][0], rel=1e-12)
            assert old[cid][1] == pytest.approx(new[cid][1], rel=1e-12)
