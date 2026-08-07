"""Unit tests for emptyos/sdk/retry_policy.py — no daemon, no I/O.

Pins both directions: the informative early stops must fire when they should,
and must NOT fire on a converging loop (the false-positive half — a retry guard
that stops a loop which was about to succeed is worse than a flat ceiling).
"""

from __future__ import annotations

import pytest

from emptyos.sdk.retry_policy import (
    REASONS,
    RetryDecision,
    classify_retry,
    repeated_tag,
)


# ─── the termination guarantee ────────────────────────────────────────

def test_empty_history_continues():
    d = classify_retry([], max_attempts=3)
    assert d.stop is False
    assert d.reason == "converging"


def test_ceiling_is_non_bypassable():
    """A loop that never repeats and never churns still terminates."""
    hist = []
    for i in range(50):
        d = classify_retry(hist, max_attempts=3)
        if d.stop:
            break
        hist.append({f"defect-{i}"})  # always brand new, never shared
    assert d.stop is True
    assert len(hist) <= 3, "ceiling must bound the loop"


def test_ceiling_reason_when_nothing_else_fires():
    # Two attempts, disjoint sets, max 2 → not enough history for churn.
    d = classify_retry([{"A"}, {"B"}], max_attempts=2)
    assert d.stop is True
    assert d.reason == "ceiling"
    assert "2 attempts" in d.detail


def test_max_attempts_clamped_to_one():
    d = classify_retry([{"A"}], max_attempts=0)
    assert d.stop is True
    assert d.reason == "ceiling"


# ─── repeated_defect ──────────────────────────────────────────────────

def test_repeated_defect_stops_before_ceiling():
    """The whole point: stop at attempt 2 of 3, saving the third render."""
    d = classify_retry([{"FLAT"}, {"FLAT"}], max_attempts=3)
    assert d.stop is True
    assert d.reason == "repeated_defect"
    assert "FLAT" in d.detail


def test_repeated_defect_on_partial_overlap_that_did_not_shrink():
    # {FLAT} -> {FLAT, SMOOTH} shares FLAT and got worse.
    d = classify_retry([{"FLAT"}, {"FLAT", "SMOOTH"}], max_attempts=5)
    assert d.stop is True
    assert d.reason == "repeated_defect"


def test_repeated_defect_on_same_set_of_two():
    d = classify_retry([{"FLAT", "RANGE"}, {"RANGE", "FLAT"}], max_attempts=5)
    assert d.stop is True
    assert d.reason == "repeated_defect"


def test_repeated_defect_reports_lowest_shared_tag_deterministically():
    d1 = classify_retry([{"SMOOTH", "FLAT"}, {"FLAT", "SMOOTH"}], max_attempts=5)
    d2 = classify_retry([{"FLAT", "SMOOTH"}, {"SMOOTH", "FLAT"}], max_attempts=5)
    assert d1.detail == d2.detail, "tag choice must not depend on set ordering"
    assert d1.detail.startswith("FLAT")


# ─── the divergence from the reference: shrinking beats a shared tag ──

def test_shrinking_defect_set_is_not_a_repeated_defect():
    """{FLAT, SMOOTH} -> {FLAT} shares FLAT but is obvious progress.

    The reference implementation tolerates this only because a rising fidelity
    score outvotes the shared tag; unscored, the strict-subset guard is what
    keeps a converging loop alive. Regression-pins that guard.
    """
    d = classify_retry([{"FLAT", "SMOOTH"}, {"FLAT"}], max_attempts=5)
    assert d.stop is False
    assert d.reason == "converging"
    assert "shrinking" in d.detail


def test_shrinking_still_terminates_at_ceiling():
    d = classify_retry([{"A", "B", "C"}, {"A", "B"}], max_attempts=2)
    assert d.stop is True
    assert d.reason == "ceiling"


# ─── churn ────────────────────────────────────────────────────────────

def test_churn_needs_three_attempts():
    # Disjoint but only two attempts → churn cannot be observed yet.
    d = classify_retry([{"A"}, {"B"}], max_attempts=9)
    assert d.stop is False
    assert d.reason == "converging"


def test_churn_fires_on_double_turnover():
    d = classify_retry([{"A"}, {"B"}, {"C"}], max_attempts=9)
    assert d.stop is True
    assert d.reason == "churn"


def test_churn_not_reachable_at_three_attempts_ceiling_wins():
    """Documented consequence: at max_attempts=3 the useful signal is
    repeated_defect, because ceiling gets there before churn."""
    d = classify_retry([{"A"}, {"B"}, {"C"}], max_attempts=3)
    assert d.stop is True
    assert d.reason == "churn"  # informative-first ordering
    # ...but it would have stopped anyway:
    assert classify_retry([{"A"}, {"B"}, {"C"}], max_attempts=3).stop is True


def test_churn_ignores_empty_entries():
    """An attempt with no recorded findings is absence of evidence."""
    d = classify_retry([{"A"}, set(), {"C"}], max_attempts=9)
    assert d.reason != "churn"


# ─── informative-first ordering ───────────────────────────────────────

def test_repeated_defect_wins_over_ceiling():
    d = classify_retry([{"FLAT"}, {"FLAT"}], max_attempts=2)
    assert d.stop is True
    assert d.reason == "repeated_defect", "reason must be the actionable one"


def test_repeated_defect_wins_over_churn():
    # Last pair shares a tag, so churn's disjointness precondition fails anyway.
    d = classify_retry([{"A"}, {"B"}, {"B"}], max_attempts=9)
    assert d.reason == "repeated_defect"


# ─── input tolerance (a loop guard must not crash the loop) ───────────

@pytest.mark.parametrize("hist", [
    None,
    [],
    [None],
    [None, None],
    ["FLAT", "FLAT"],            # bare strings, not lists
    [["FLAT"], ("FLAT",)],       # mixed iterables
    [{"  FLAT  "}, {"FLAT"}],    # whitespace
    [{""}, {""}],                # empty tags
])
def test_never_raises_on_loose_input(hist):
    d = classify_retry(hist, max_attempts=3)
    assert isinstance(d, RetryDecision)
    assert d.reason in REASONS


def test_bare_string_is_one_tag_not_characters():
    """`"FLAT"` must not iterate into {F, L, A, T} — that would make two
    unrelated errors share tags and fire a bogus repeated_defect."""
    d = classify_retry(["FLAT", "SMOOTH"], max_attempts=9)
    assert d.stop is False, "F/L/A/T vs S/M/O/O/T/H share T if iterated"


def test_whitespace_normalised_so_repeat_is_detected():
    d = classify_retry([{" FLAT"}, {"FLAT "}], max_attempts=9)
    assert d.reason == "repeated_defect"


def test_decision_is_frozen():
    d = classify_retry([], max_attempts=3)
    with pytest.raises(Exception):
        d.stop = True  # type: ignore[misc]


def test_every_reason_is_declared():
    """Guards against a new branch returning an undeclared reason string."""
    seen = {
        classify_retry([], max_attempts=3).reason,
        classify_retry([{"A"}, {"A"}], max_attempts=9).reason,
        classify_retry([{"A"}, {"B"}, {"C"}], max_attempts=9).reason,
        classify_retry([{"A"}, {"B"}], max_attempts=2).reason,
    }
    assert seen == set(REASONS)


# ─── repeated_tag (the shared primitive) ──────────────────────────────

def test_repeated_tag_returns_the_survivor():
    assert repeated_tag({"FLAT"}, {"FLAT"}) == "FLAT"


def test_repeated_tag_none_when_disjoint():
    assert repeated_tag({"A"}, {"B"}) is None


def test_repeated_tag_none_when_either_side_empty():
    assert repeated_tag(set(), {"A"}) is None
    assert repeated_tag({"A"}, set()) is None
    assert repeated_tag(None, None) is None


def test_repeated_tag_none_when_shrinking():
    """Same guard as classify_retry — progress is not a repeat."""
    assert repeated_tag({"FLAT"}, {"FLAT", "SMOOTH"}) is None


def test_repeated_tag_fires_when_set_grew():
    assert repeated_tag({"FLAT", "SMOOTH"}, {"FLAT"}) == "FLAT"


def test_repeated_tag_stable_across_set_order():
    a = repeated_tag({"SMOOTH", "FLAT"}, {"FLAT", "SMOOTH"})
    b = repeated_tag({"FLAT", "SMOOTH"}, {"SMOOTH", "FLAT"})
    assert a == b == "FLAT"


def test_repeated_tag_accepts_bare_strings():
    assert repeated_tag("FLAT", "FLAT") == "FLAT"
    assert repeated_tag("FLAT", "SMOOTH") is None


def test_classify_retry_agrees_with_repeated_tag():
    """One definition of a repeat — the two must never disagree."""
    cases = [
        ({"FLAT"}, {"FLAT"}), ({"A"}, {"B"}), ({"FLAT"}, {"FLAT", "SMOOTH"}),
        ({"FLAT", "SMOOTH"}, {"FLAT"}), (set(), {"A"}), ({"A", "B"}, {"B", "C"}),
    ]
    for last, prev in cases:
        d = classify_retry([prev, last], max_attempts=9)
        assert (d.reason == "repeated_defect") is (
            repeated_tag(last, prev) is not None
        ), (last, prev, d.reason)
