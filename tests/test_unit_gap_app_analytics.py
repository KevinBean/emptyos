"""Pure tests for the app-analytics productivity split
(apps/public/standard/app-analytics/productivity.py).

Closes gap `app-analytics-no-productive-tagging`: the app had per-app usage
counters but no notion of what any app is *for*, so it could never answer
the question every time-tracker (RescueTime, Rize, Timing) leads with —
"how much of this week was work?".

Daemon-free by construction: productivity.py is pure stdlib, so the
classification ladder (override > manifest key > inference) and the daily
roll-up are pinned here without booting a kernel. The routes, the state
read/write, and the manifest walk stay in app.py and are exercised live.

The wellbeing wheel is read as a silent refiner only (CLAUDE.md rule 16) —
`test_inference_never_leaks_a_dimension_name` is the regression pin that
no dimension name can reach an output the UI renders.
"""

from __future__ import annotations

import pytest

from helpers import load_app_module


@pytest.fixture(scope="module")
def pr():
    return load_app_module("app-analytics", "productivity")


# ── The vocabulary ───────────────────────────────────────────────────────


def test_exactly_three_classes_and_no_distracting_bucket(pr):
    """"distracting" is deliberately absent — see the module docstring."""
    assert pr.CLASSES == ("productive", "neutral", "personal")
    assert "distracting" not in pr.CLASSES


def test_normalize_class_refuses_anything_unknown(pr):
    assert pr.normalize_class("productive") == "productive"
    assert pr.normalize_class("  Personal  ") == "personal"
    # The write-boundary guard: a typo must never become a fourth bucket.
    assert pr.normalize_class("distracting") is None
    assert pr.normalize_class("prodcutive") is None
    assert pr.normalize_class("") is None
    assert pr.normalize_class(None) is None
    assert pr.normalize_class(["productive"]) is None


# ── Inference from real manifest metadata ────────────────────────────────


@pytest.mark.parametrize(
    "category,dimensions,expected",
    [
        # Tier A — the category is decisive on its own.
        ("engineering", None, "productive"),
        ("dev", None, "productive"),
        ("productivity", None, "productive"),
        ("personal", ["emotional"], "personal"),  # garden/vlog/countdown
        # Tier B — ambiguous category, refined by the declared dimension.
        # These four are real manifests, and they are the whole reason the
        # refiner exists: `core` holds both real work and pure chrome.
        ("core", ["occupational"], "productive"),   # task
        ("core", ["environmental"], "personal"),    # weather
        ("core", None, "neutral"),                  # settings / hub / store
        ("creative", ["intellectual"], "productive"),  # reader
        ("creative", None, "personal"),             # quotes
        ("meta", ["occupational"], "productive"),   # app-analytics itself
        ("ai", None, "productive"),                 # agent / assistant / rooms
        # Unknown or missing metadata must degrade, never raise.
        ("", None, "neutral"),
        (None, None, "neutral"),
        ("wat", None, "neutral"),
    ],
)
def test_infer_class_from_manifest_metadata(pr, category, dimensions, expected):
    assert pr.infer_class(category, dimensions) == expected


def test_a_decisive_category_outranks_a_conflicting_dimension(pr):
    """`personal` is an explicit statement about the app; an occupational
    dimension on top of it must not flip it to work."""
    assert pr.infer_class("personal", ["occupational"]) == "personal"
    assert pr.infer_class("engineering", ["emotional"]) == "productive"


def test_productive_lean_wins_when_an_app_declares_both(pr):
    """A work tool that also touches wellbeing is still a work tool."""
    assert pr.infer_class("core", ["emotional", "occupational"]) == "productive"


def test_infer_class_survives_malformed_dimensions(pr):
    for junk in ("occupational", 42, {"a": 1}, [None], [[]]):
        assert pr.infer_class("core", junk) in pr.CLASSES


def test_inference_never_leaks_a_dimension_name(pr):
    """Rule 16 pin: the wheel shapes the answer, it is never the answer.
    Every output of the classifier is one of the three classes — no
    dimension name may become something a surface could render."""
    for dim in pr.DIMENSION_LEAN:
        assert pr.infer_class("core", [dim]) in pr.CLASSES
        assert pr.infer_class("meta", [dim]) != dim


# ── The precedence ladder ────────────────────────────────────────────────


def test_manifest_key_beats_inference(pr):
    block = {"store_category": "personal", "productivity": "productive"}
    assert pr.classify_app(block) == ("productive", "manifest")


def test_user_override_beats_everything(pr):
    block = {"store_category": "engineering", "productivity": "productive"}
    assert pr.classify_app(block, override="personal") == ("personal", "override")


def test_a_junk_override_falls_through_rather_than_poisoning_the_class(pr):
    block = {"store_category": "engineering"}
    assert pr.classify_app(block, override="distracting") == ("productive", "inferred")


def test_classify_app_reports_where_the_answer_came_from(pr):
    """The UI shows this so the user can tell a guess from their own
    decision, and knows which rows are worth correcting."""
    assert pr.classify_app({"store_category": "dev"})[1] == "inferred"
    assert pr.classify_app({"productivity": "neutral"})[1] == "manifest"
    assert pr.classify_app({}, override="neutral")[1] == "override"


def test_classify_app_tolerates_a_missing_app_block(pr):
    for junk in (None, {}, "not-a-dict", 7):
        cls, source = pr.classify_app(junk)
        assert cls in pr.CLASSES and source == "inferred"


def test_classify_all_maps_every_app(pr):
    blocks = {
        "task": {"store_category": "core", "dimensions": ["occupational"]},
        "garden": {"store_category": "personal"},
        "settings": {"store_category": "core"},
    }
    got = pr.classify_all(blocks, {"settings": "productive"})
    assert got["task"] == {"class": "productive", "source": "inferred"}
    assert got["garden"] == {"class": "personal", "source": "inferred"}
    assert got["settings"] == {"class": "productive", "source": "override"}


# ── The daily roll-up ────────────────────────────────────────────────────


CLASSES_FIXTURE = {
    "task": {"class": "productive", "source": "inferred"},
    "garden": {"class": "personal", "source": "inferred"},
    "settings": {"class": "neutral", "source": "inferred"},
}


def _rows(*triples):
    return [{"bucket": b, "app": a, "kind": "view", "count": c} for b, a, c in triples]


def test_summarize_splits_totals_and_percentages(pr):
    out = pr.summarize_usage(
        _rows(
            ("2026-08-01", "task", 6),
            ("2026-08-01", "garden", 2),
            ("2026-08-02", "task", 4),
            ("2026-08-02", "settings", 8),
        ),
        CLASSES_FIXTURE,
    )
    assert out["totals"] == {"productive": 10, "neutral": 8, "personal": 2}
    assert out["total"] == 20
    assert out["productive_pct"] == 50.0
    assert out["personal_pct"] == 10.0


def test_summarize_builds_a_sorted_daily_series(pr):
    out = pr.summarize_usage(
        _rows(
            ("2026-08-02", "task", 4),
            ("2026-08-01", "garden", 2),
            ("2026-08-01", "task", 1),
        ),
        CLASSES_FIXTURE,
    )
    assert [d["date"] for d in out["daily"]] == ["2026-08-01", "2026-08-02"]
    assert out["daily"][0] == {
        "date": "2026-08-01", "productive": 1, "neutral": 0, "personal": 2,
    }


def test_an_unclassified_app_is_counted_not_dropped(pr):
    """Silently discarding usage would make the percentages lie, and a
    total that disagrees with the views shown elsewhere reads as a bug."""
    out = pr.summarize_usage(
        _rows(("2026-08-01", "task", 3), ("2026-08-01", "_unknown", 7)),
        CLASSES_FIXTURE,
    )
    assert out["total"] == 10
    assert out["totals"]["neutral"] == 7


def test_summarize_is_zero_safe_on_empty_input(pr):
    out = pr.summarize_usage([], CLASSES_FIXTURE)
    assert out["total"] == 0
    assert out["productive_pct"] == 0.0  # never a ZeroDivisionError
    assert out["daily"] == [] and out["apps"] == []
    assert out["totals"] == {"productive": 0, "neutral": 0, "personal": 0}


def test_summarize_skips_junk_rows_without_raising(pr):
    rows = [
        {"bucket": "2026-08-01", "app": "task", "count": "nope"},
        {"bucket": "2026-08-01", "app": "task", "count": None},
        {"bucket": "2026-08-01", "app": "task", "count": -5},
        {"bucket": "2026-08-01", "app": "task", "count": 2},
    ]
    out = pr.summarize_usage(rows, CLASSES_FIXTURE)
    assert out["total"] == 2


def test_summarize_ranks_apps_by_views_with_their_class(pr):
    out = pr.summarize_usage(
        _rows(
            ("2026-08-01", "settings", 1),
            ("2026-08-01", "task", 5),
            ("2026-08-02", "garden", 3),
        ),
        CLASSES_FIXTURE,
    )
    assert [r["app"] for r in out["apps"]] == ["task", "garden", "settings"]
    assert out["apps"][0] == {
        "app": "task", "views": 5, "class": "productive", "source": "inferred",
    }
