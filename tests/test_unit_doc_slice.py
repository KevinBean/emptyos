"""Unit tests for emptyos.sdk.doc_slice — pure verbatim-clause slicer. No daemon."""

from emptyos.sdk.doc_slice import slice_clause_text, _heading_num, _parse_range, parse_contents


CONTENTS = """\
Contents
Summary ..........................................................................1
1. Power Cable Design ............................................................ 13
1.1. Purpose ..................................................................... 13
1.7. Ratings...................................................................... 17
1.7.1. General ................................................................... 17
1.10. Conductors ................................................................. 19
2. Accessory Requirements ........................................................ 33

1. Power Cable Design
Body text begins here, a sentence with 5 widgets and no leader.
1.7. Ratings
Real body, not a contents row.
"""


def test_parse_contents_extracts_sections():
    rows = parse_contents(CONTENTS)
    nos = [r["no"] for r in rows]
    assert nos == ["1", "1.1", "1.7", "1.7.1", "1.10", "2"]  # "Summary" (no number) skipped
    by = {r["no"]: r for r in rows}
    assert by["1.7"]["title"] == "Ratings" and by["1.7"]["page"] == 17 and by["1.7"]["level"] == 2
    assert by["1.7.1"]["level"] == 3 and by["1.7.1"]["chapter"] == "1"
    assert by["1"]["level"] == 1 and by["2"]["title"] == "Accessory Requirements"


def test_parse_contents_ignores_body_and_dedups():
    # The body "1.7. Ratings" line (no dot-leader+page) must NOT be a section,
    # and the duplicate "1." chapter rows collapse to one.
    rows = parse_contents(CONTENTS)
    assert sum(1 for r in rows if r["no"] == "1.7") == 1
    assert sum(1 for r in rows if r["no"] == "1") == 1


def test_parse_contents_empty():
    assert parse_contents("") == []
    assert parse_contents("just prose, no toc here") == []


# A miniature standard with a TOC block + a body, mimicking the real .txt shape.
DOC = """\
Contents
1. Power Cable Design .......................................... 13
1.7 Ratings ................................................... 17
1.7.1 General ................................................. 17
1.8 Design Life .............................................. 19

1. Power Cable Design
Intro to chapter one.

1.7. Ratings
The cable rating depends on the project.

1.7.1. General
General ratings text.

1.7.2. Load Ratings
Load ratings text.

1.8. Design Life
Design life is 40 years.

2. Accessory Requirements
Accessories intro.
"""


def test_slices_clause_with_subclauses_included():
    out = slice_clause_text(DOC, "1.7")
    assert out.startswith("1.7. Ratings")
    assert "The cable rating depends" in out
    assert "General ratings text." in out      # 1.7.1 child included
    assert "Load ratings text." in out         # 1.7.2 child included
    assert "Design life" not in out            # stops before 1.8


def test_toc_row_not_mistaken_for_heading():
    # The TOC "1.7 Ratings ..... 17" must NOT be the slice start.
    out = slice_clause_text(DOC, "1.7")
    assert ".......... 17" not in out
    assert "..." not in out.splitlines()[0]


def test_subclause_slice():
    out = slice_clause_text(DOC, "1.7.1")
    assert out.startswith("1.7.1. General")
    assert "General ratings text." in out
    assert "Load ratings text." not in out     # stops before sibling 1.7.2


def test_chapter_boundary_stops_at_next_chapter():
    out = slice_clause_text(DOC, "1.8")
    assert "Design life is 40 years." in out
    assert "Accessory Requirements" not in out  # stops at chapter 2


def test_range_slice_spans_lo_to_after_hi():
    out = slice_clause_text(DOC, "1.7-1.7.2")
    assert "The cable rating depends" in out
    assert "Load ratings text." in out
    assert "Design life" not in out             # 1.8 > 1.7.2 ends the range


def test_missing_clause_returns_empty():
    assert slice_clause_text(DOC, "9.9") == ""
    assert slice_clause_text("", "1.7") == ""
    assert slice_clause_text(DOC, "") == ""


def test_page_marked_and_markdown_headings():
    md = (
        "<!-- Page 5 of 40 -->\n"
        "## 3.4 Clearances\n"
        "Minimum clearances apply.\n"
        "## 3.5 Earthing\n"
        "Earthing text.\n"
    )
    out = slice_clause_text(md, "3.4")
    assert "Minimum clearances apply." in out
    assert "Earthing text." not in out


def test_heading_num_helpers():
    assert _heading_num("1.7. Ratings") == "1.7"
    assert _heading_num("## 3.4 Clearances") == "3.4"
    assert _heading_num("1.7 Ratings ........... 17") is None  # TOC row
    assert _parse_range("7.4-7.20") == ("7.4", "7.20")
    assert _parse_range("§1.7") == ("1.7", "1.7")
