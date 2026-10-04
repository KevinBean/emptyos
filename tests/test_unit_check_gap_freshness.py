"""Both-direction pins for scripts/check_gap_freshness.py grade derivation.

Per .claude/skills/eos-graduate-audit: a graduated checker ships with tests
pinning BOTH "fires on the real regression" and "silent on healthy notes".

Why grade derivation exists (2026-08-06): `grade:` was hand-authored in every
gap-analysis note and read by nothing — so it drifted silently. The registry
held 72 -> "B" (writing-editor) alongside 72 -> "B+" (earthing), and a 91 -> "A"
ranked *above* a 95 -> "A-". Deriving it from `score` ends that permanently.

`TestBandsMatchTheSettledRegistry` is the load-bearing pin: the bands were
chosen to reproduce the letter the registry had already settled on, so if
someone retunes them, the churn shows up here as a failing table rather than as
39 silently-renamed notes.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))  # sibling imports: check_common, kb_paths, md_frontmatter
SCRIPT = REPO / "scripts" / "check_gap_freshness.py"
_spec = importlib.util.spec_from_file_location("check_gap_freshness", SCRIPT)
cgf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cgf)


NOTE = """---
tags:
  - gap-analysis
app: demo
track: public/standard
market: demo-market
competitors:
  - Someone
score: {score}
grade: {grade}
last_reviewed: 2026-08-06
open_gaps: 2
author: ai
---
# demo — gap analysis

Body stays untouched. A `grade:` mention down here must not be rewritten.
"""


class TestGradeFor:
    """The derived letter — boundaries and the non-numeric escape."""

    @pytest.mark.parametrize(
        "score,expected",
        [
            (120, "A"), (101, "A"), (96, "A"),
            (95, "A-"), (84, "A-"),
            (83, "B+"), (72, "B+"),
            (71, "B"), (60, "B"),
            (59, "C"), (40, "C"),
            (39, "D"), (0, "D"),
        ],
    )
    def test_bands(self, score, expected):
        assert cgf.grade_for(score) == expected

    def test_accepts_string_scores(self):
        """Frontmatter scalars arrive as strings from parse_fm."""
        assert cgf.grade_for("82") == "B+"
        assert cgf.grade_for(" 91 ") == "A-"

    @pytest.mark.parametrize("bad", ["", None, "n/a", "—", []])
    def test_non_numeric_yields_no_grade(self, bad):
        """No score -> no derived letter, so a note without one is never 'drifted'."""
        assert cgf.grade_for(bad) == ""

    def test_monotonic(self):
        """A higher score can never earn a worse letter — the 91>'A' vs 95>'A-'
        inversion that motivated this is impossible by construction."""
        order = ["D", "C", "B", "B+", "A-", "A"]
        seen = [cgf.grade_for(s) for s in range(0, 121)]
        idx = [order.index(g) for g in seen]
        assert idx == sorted(idx)


class TestBandsMatchTheSettledRegistry:
    """The bands reproduce what the registry had already converged on.

    Sampled from the 39 notes as of 2026-08-06. Only writing-editor (72 "B")
    disagreed, and it was the drift being corrected — its partner at the same
    score (earthing) already said "B+".
    """

    @pytest.mark.parametrize(
        "app,score,settled",
        [
            ("vault-graph", 29, "D"), ("weather", 31, "D"), ("garden", 33, "D"),
            ("note", 44, "C"), ("store", 50, "C"), ("focus", 59, "C"),
            ("hub", 63, "B"), ("quick-action", 67, "B"), ("expense", 71, "B"),
            ("earthing", 72, "B+"), ("task", 73, "B+"), ("publish", 79, "B+"),
            ("journal", 82, "B+"),
            ("boards", 84, "A-"), ("cad", 89, "A-"), ("kb", 95, "A-"),
            ("projects", 96, "A"), ("rooms", 100, "A"), ("assistant", 101, "A"),
        ],
    )
    def test_reproduces_settled_letter(self, app, score, settled):
        assert cgf.grade_for(score) == settled, f"{app} would be renamed"


class TestNormaliseGrade:
    """Rewrites exactly one frontmatter line, or nothing at all."""

    def test_fires_on_drift(self):
        out = cgf.normalise_grade(NOTE.format(score=72, grade="B"), "B+")
        assert "grade: B+" in out
        assert "\ngrade: B\n" not in out

    def test_silent_when_already_correct(self):
        text = NOTE.format(score=82, grade="B+")
        assert cgf.normalise_grade(text, "B+") == text

    def test_idempotent(self):
        once = cgf.normalise_grade(NOTE.format(score=72, grade="B"), "B+")
        assert cgf.normalise_grade(once, "B+") == once

    def test_leaves_body_and_other_fields_alone(self):
        out = cgf.normalise_grade(NOTE.format(score=72, grade="B"), "B+")
        assert "A `grade:` mention down here must not be rewritten." in out
        for keep in ("app: demo", "score: 72", "open_gaps: 2", "  - gap-analysis", "  - Someone"):
            assert keep in out

    def test_no_frontmatter_block_is_untouched(self):
        text = "# just a heading\n\ngrade: B\n"
        assert cgf.normalise_grade(text, "B+") == text

    def test_note_without_a_grade_line_gains_nothing(self):
        """Derivation caches a value; it never invents frontmatter."""
        text = "---\napp: demo\nscore: 72\n---\nbody\n"
        assert cgf.normalise_grade(text, "B+") == text

    def test_empty_derived_is_a_no_op(self):
        text = NOTE.format(score="n/a", grade="B")
        assert cgf.normalise_grade(text, "") == text
