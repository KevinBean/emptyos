"""Unit tests for emptyos/sdk/composite_score.py — daemon-free, no kernel boot.

Each test pins ONE decision, so a mutation to one constant or one branch turns
exactly one test red. Module-absence alone would prove nothing (see
`.claude/rules/audits.md` § "green because it checks nothing"), so the mutations
each test is designed to catch are named in its docstring.
"""

import math

import pytest

from emptyos.composite_score import (
    GEOMETRIC_FLOOR,
    NEUTRAL,
    CompositeError,
    composite_score,
    dimension_rollup,
    normalize_values,
    numeric_value,
)


# ── numeric_value ────────────────────────────────────────────────────


def test_bool_is_not_a_number():
    """Mutation caught: dropping the isinstance(bool) guard. float(True) == 1.0
    would otherwise make a flag column look like a measurement."""
    assert numeric_value(True) is None
    assert numeric_value(False) is None


def test_numeric_strings_are_accepted():
    """CSV/GeoJSON round-trips quote numbers; refusing them silently drops
    whole columns to null."""
    assert numeric_value("3.5") == 3.5
    assert numeric_value("  42 ") == 42.0
    assert numeric_value("") is None
    assert numeric_value("abc") is None


def test_non_finite_is_rejected():
    assert numeric_value(float("nan")) is None
    assert numeric_value(float("inf")) is None


# ── normalize_values ─────────────────────────────────────────────────


def test_constant_field_is_neutral_not_zero():
    """THE edge. Mutation caught: returning 0.0 for a zero-spread field.
    A criterion that discriminates nothing must not penalise everyone."""
    out = normalize_values([7, 7, 7])
    assert out == [NEUTRAL, NEUTRAL, NEUTRAL]
    assert NEUTRAL == 0.5
    assert out != [0.0, 0.0, 0.0]


def test_single_value_is_neutral():
    assert normalize_values([12]) == [NEUTRAL]


def test_minmax_spans_zero_to_one():
    assert normalize_values([0, 5, 10]) == [0.0, 0.5, 1.0]


def test_minmax_is_distance_based_not_rank_based():
    """Evenly-spaced inputs make minmax and mid-rank identical, so every
    earlier minmax test also passed with the branch swapped to _mid_ranks.
    Uneven spacing separates them: minmax -> 0.1, rank -> 0.5."""
    assert normalize_values([0, 1, 10], method="minmax") == [0.0, 0.1, 1.0]
    assert normalize_values([0, 1, 10], method="rank") == [0.0, 0.5, 1.0]


def test_direction_lower_flips_after_normalizing():
    """Mutation caught: flipping the raw input instead of the result, which
    breaks for rank/quantile/zscore."""
    assert normalize_values([0, 5, 10], direction="lower") == [1.0, 0.5, 0.0]


def test_rank_hits_the_endpoints_but_quantile_never_does():
    """The two are easy to swap. rank = (r-1)/(n-1) touches 0 and 1;
    quantile = (r-0.5)/n (Hazen) deliberately does not."""
    rank = normalize_values([1, 2, 3], method="rank")
    assert rank == [0.0, 0.5, 1.0]
    quantile = normalize_values([1, 2, 3], method="quantile")
    assert quantile[0] > 0.0 and quantile[-1] < 1.0
    # Pin the Hazen offset itself: any 0<c<1 in (r-c)/n satisfies the bounds
    # check above, so only literals distinguish 0.5 from, say, 0.3.
    assert [round(v, 4) for v in quantile] == [0.1667, 0.5, 0.8333]


def test_ties_share_a_mid_rank():
    """Mutation caught: naive ordinal ranking, which would give the two tied
    values different scores purely by input order."""
    out = normalize_values([5, 5, 9], method="rank")
    assert out[0] == out[1]
    assert out[2] > out[0]


def test_zscore_is_monotonic_and_centred():
    out = normalize_values([1, 2, 3, 4, 100], method="zscore")
    assert out == sorted(out)
    assert all(0.0 < v < 1.0 for v in out)


def test_zscore_is_the_normal_cdf_not_merely_a_sigmoid():
    """Monotonic-and-bounded is satisfied by a logistic sigmoid too, so the
    erf-based CDF has to be pinned to literals or it can be swapped silently."""
    out = normalize_values([1, 2, 3], method="zscore")
    assert [round(v, 4) for v in out] == [0.1103, 0.5, 0.8897]


def test_missing_entries_stay_missing_and_do_not_shift_statistics():
    """A null must not be imputed to the mean, nor counted in min/max."""
    out = normalize_values([0, None, 10, "junk"])
    assert out[1] is None and out[3] is None
    assert out[0] == 0.0 and out[2] == 1.0


def test_unknown_method_and_direction_raise():
    with pytest.raises(CompositeError):
        normalize_values([1, 2], method="nope")
    with pytest.raises(CompositeError):
        normalize_values([1, 2], direction="sideways")


# ── composite_score ──────────────────────────────────────────────────


def test_weights_are_relative_not_preformed():
    """Mutation caught: dividing by a hardcoded 1.0 instead of the actual
    present weight. 2:1:1 unnormalised must equal 0.5:0.25:0.25."""
    comps = {"a": 1.0, "b": 0.0, "c": 0.0}
    assert composite_score(comps, {"a": 2, "b": 1, "c": 1}) == 50.0
    assert composite_score(comps, {"a": 0.5, "b": 0.25, "c": 0.25}) == 50.0


def test_geometric_floor_prevents_a_single_zero_from_zeroing_everything():
    """THE other edge. Mutation caught: GEOMETRIC_FLOOR = 0 (math domain error
    or a hard 0), which would tie every subject that bottoms out on any one
    criterion — exactly what min-max guarantees for the worst subject."""
    strong = composite_score(
        {"a": 0.0, "b": 0.9}, {"a": 1, "b": 1}, mean="geometric"
    )
    weak = composite_score(
        {"a": 0.0, "b": 0.1}, {"a": 1, "b": 1}, mean="geometric"
    )
    assert strong is not None and weak is not None
    assert strong > weak > 0.0
    # LITERAL, deliberately. Recomputing this with the imported GEOMETRIC_FLOOR
    # would move with the constant and pass for any nonzero value — the
    # back-filled-fixture trap in .claude/rules/audits.md. 9.49 is
    # exp((ln(0.01)+ln(0.9))/2)*100 and pins the magnitude 0.01 itself.
    assert GEOMETRIC_FLOOR == 0.01
    assert strong == pytest.approx(9.49, abs=0.01)


def test_geometric_penalises_imbalance_more_than_arithmetic():
    lopsided = {"a": 0.1, "b": 0.9}
    arith = composite_score(lopsided, {"a": 1, "b": 1})
    geo = composite_score(lopsided, {"a": 1, "b": 1}, mean="geometric")
    assert geo < arith


def test_drop_and_renormalize_genuinely_diverge():
    """If these two ever agree the policy argument is dead code."""
    comps = {"a": 0.8, "b": None}
    weights = {"a": 1, "b": 1}
    assert composite_score(comps, weights, null_policy="drop") is None
    assert composite_score(comps, weights, null_policy="renormalize") == 80.0


def test_zero_weighted_component_may_be_missing_without_penalty():
    """Ours, not GeoLibre's. Mutation caught: testing `components.get(k) is
    None` without the `weight > 0` guard, which would make a weighted-out
    criterion able to void the whole subject under drop."""
    comps = {"a": 0.6, "ignored": None}
    weights = {"a": 1, "ignored": 0}
    assert composite_score(comps, weights, null_policy="drop") == 60.0


def test_negative_weight_is_clamped_not_subtracted():
    # The components must DIFFER. With both at 1.0 the unclamped weighted mean
    # is also 1.0, so the test passed with the clamp deleted. Here: clamped
    # -> 100.0, unclamped -> (1*1.0 + -5*0.0)/(1-5) = -25.0.
    comps = {"a": 1.0, "b": 0.0}
    assert composite_score(comps, {"a": 1, "b": -5}) == 100.0


def test_geometric_refuses_components_outside_zero_to_one():
    """GEOMETRIC_FLOOR is meaningless off a 0..1 scale, so a 0..100 caller must
    be refused rather than handed an uninterpretable number. Arithmetic stays
    scale-free — that asymmetry is the whole point."""
    with pytest.raises(CompositeError):
        composite_score({"a": 60.0, "b": 70.0}, {"a": 1, "b": 1}, mean="geometric")
    # Both halves of `v < 0.0 or v > 1.0` — testing only the upper bound let a
    # mutation dropping the negative check pass.
    with pytest.raises(CompositeError):
        composite_score({"a": -0.5, "b": 0.5}, {"a": 1, "b": 1}, mean="geometric")
    assert composite_score(
        {"a": 60.0, "b": 70.0}, {"a": 1, "b": 1}, scale=1.0
    ) == 65.0


def test_rescaling_through_zero_to_one_is_not_value_preserving():
    """Pins the bug that a naive migration introduced: dividing by 100 and
    multiplying back moves results sitting on a rounding boundary. This is why
    `scale=1.0` with native-scale components exists."""
    native = composite_score(
        {"sustain": 60.0, "engage": 70.0, "accrete": 25.0},
        {"sustain": 0.35, "engage": 0.30, "accrete": 0.35},
        scale=1.0,
        decimals=1,
    )
    roundtripped = composite_score(
        {"sustain": 0.60, "engage": 0.70, "accrete": 0.25},
        {"sustain": 0.35, "engage": 0.30, "accrete": 0.35},
        scale=100.0,
        decimals=1,
    )
    assert native == 50.8
    assert roundtripped == 50.7
    assert native != roundtripped


def test_all_zero_weights_raise_rather_than_divide_by_zero():
    with pytest.raises(CompositeError):
        composite_score({"a": 1.0}, {"a": 0})


def test_every_component_missing_returns_none_not_zero():
    assert composite_score({"a": None}, {"a": 1}, null_policy="renormalize") is None


def test_scale_and_decimals_are_honoured():
    comps = {"a": 1.0, "b": 0.0}
    assert composite_score(comps, {"a": 1, "b": 1}, scale=1.0, decimals=4) == 0.5


def test_unknown_mean_and_policy_raise():
    with pytest.raises(CompositeError):
        composite_score({"a": 1.0}, {"a": 1}, mean="harmonic")
    with pytest.raises(CompositeError):
        composite_score({"a": 1.0}, {"a": 1}, null_policy="ignore")


# ── dimension_rollup ─────────────────────────────────────────────────


def test_rollup_matches_the_hand_rolled_sum_over_max():
    """Pins behaviour-preservation for the briefing/hub-life refactor."""
    dims = {
        "journal": {"score": 20, "max": 20},
        "english": {"score": 10, "max": 20},
        "exercise": {"score": 0, "max": 20},
    }
    out = dimension_rollup(dims)
    assert out["total"] == 30 and out["max"] == 60
    assert out["pct"] == 50


def test_rollup_keeps_the_dimensions_for_rendering():
    """A bare percentage with no visible components is not reviewable."""
    dims = {"a": {"score": 1, "max": 2, "note": "kept"}}
    assert dimension_rollup(dims)["dimensions"]["a"]["note"] == "kept"


def test_rollup_of_nothing_is_none_not_zero():
    """Mutation caught: returning pct 0 for an empty table, which renders as
    'you scored zero' rather than 'nothing was measured'."""
    assert dimension_rollup({})["pct"] is None
    assert dimension_rollup({"a": {"score": 5, "max": 0}})["pct"] is None


def test_rollup_clamps_a_score_above_its_ceiling():
    out = dimension_rollup({"a": {"score": 99, "max": 20}})
    assert out["total"] == 20 and out["pct"] == 100


def test_rollup_ignores_malformed_entries():
    out = dimension_rollup({"a": {"score": 5, "max": 10}, "b": "not a dict"})
    # `total` as well as `max` — checking only the ceiling let a mis-clamped
    # or double-counted total through.
    assert out["max"] == 10 and out["total"] == 5 and out["pct"] == 50


def test_omitting_null_policy_renormalizes_rather_than_dropping():
    """The parameter has a default, so `renormalize` is what a caller gets by
    omission — and both real consumers omit it. Untested until review caught
    that the docstring claimed there was no default at all."""
    comps = {"a": 0.8, "b": None}
    assert composite_score(comps, {"a": 1, "b": 1}) == 80.0
