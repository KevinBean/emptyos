"""Unit tests for emptyos.sdk.standard_atomize (pure — no daemon)."""

from emptyos.sdk.standard_atomize import (
    plan_atomization, short_label, standard_name_of, _format_section_body,
)

ARCHIVE = """\
Contents
1. Power Cable Design ......... 1
1.1 Purpose ......... 1
1.7 Ratings ......... 17
2. Accessory Requirements ......... 30
2.9 Joints ......... 35

1. Power Cable Design
1.1. Purpose
This specifies the requirements for 66/132 kV cables.
1.7. Ratings
1.7.1. General
The cable rating is project specific.
1.7.2. Load Ratings
Continuous cyclic ratings apply.
2. Accessory Requirements
2.9. Joints
Joints shall be cross-bonded.
"""


def test_short_label_and_standard_name():
    t = "Transgrid Cable Design and Installation Manual (CDIM) — Rev 1.0"
    assert short_label(t) == "Transgrid CDIM"
    assert standard_name_of(t) == "Transgrid Cable Design and Installation Manual (CDIM)"


def test_format_section_body_drops_heading_and_promotes_subsections():
    body = _format_section_body("1.7", "1.7. Ratings\n1.7.1. General\nText here.\n1.7.2. Load Ratings\nMore.")
    assert "1.7. Ratings" not in body.splitlines()[0]  # own heading dropped
    assert "## §1.7.1 General" in body
    assert "## §1.7.2 Load Ratings" in body
    assert "Text here." in body


def test_plan_atomization_basic():
    specs = plan_atomization(
        ARCHIVE,
        reference_slug="cdim",
        reference_title="Transgrid Cable Design and Installation Manual (CDIM) — Rev 1.0",
        standard_id="D2020/03048",
        edition="Rev 1.0",
        domain="electrical-engineering",
        source_file="x.txt",
        existing_clauses=[],
        today="2026-06-25",
    )
    nos = [s.clause for s in specs]
    assert nos == ["1.1", "1.7", "2.9"]  # level-2 sections, in order
    s17 = next(s for s in specs if s.clause == "1.7")
    assert s17.slug == "cdim-1-7-ratings"
    assert s17.rel.endswith("cdim-1-7-ratings.md")
    fm = s17.frontmatter
    assert fm["kind"] == "clause"
    assert fm["standard_id"] == "D2020/03048"
    assert fm["edition"] == "Rev 1.0"
    assert fm["clause"] == "1.7"
    assert fm["clause_title"] == "Ratings"
    assert fm["chapter"] == "1"
    assert fm["parent"] == "[[cdim]]"
    assert fm["title"] == "Transgrid CDIM Rev 1.0 §1.7 — Ratings"
    # body: own heading dropped, subsections promoted, verbatim text kept
    assert s17.body.startswith("# §1.7 — Ratings")
    assert "## §1.7.1 General" in s17.body
    assert "project specific" in s17.body.lower()


def test_plan_atomization_skips_existing():
    specs = plan_atomization(
        ARCHIVE,
        reference_slug="cdim",
        reference_title="… (CDIM) — Rev 1.0",
        standard_id="D2020/03048",
        edition="Rev 1.0",
        existing_clauses=["1.7"],  # already curated — skip
        today="2026-06-25",
    )
    assert [s.clause for s in specs] == ["1.1", "2.9"]


def test_plan_atomization_inherits_extra_frontmatter():
    specs = plan_atomization(
        ARCHIVE, reference_slug="cdim", reference_title="(CDIM) — Rev 1.0",
        standard_id="D2020/03048", edition="Rev 1.0",
        extra_frontmatter={"voltage_levels": ["66 kV", "132 kV"]},
    )
    assert specs[0].frontmatter["voltage_levels"] == ["66 kV", "132 kV"]
