"""Unit tests for scripts/reformat_split_headings — pure line surgery. No daemon."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from reformat_split_headings import repair, drop_french_pages  # noqa: E402


# PyMuPDF splits a clause heading: number on one line, title on the next.
SPLIT = """\
4.1
Thermal resistance of the constituent parts ...... 10
4.1.1
General ............................................ 10
4.2
External thermal resistance T4 .................... 16
"""


def test_repair_rejoins_number_and_title():
    out, joined = repair(SPLIT)
    assert joined == 3
    assert "4.1 Thermal resistance of the constituent parts ...... 10" in out
    assert "4.1.1 General ............................................ 10" in out
    # the bare number lines are gone
    assert "\n4.1\n" not in "\n" + out + "\n"


def test_repair_leaves_non_heading_lines_untouched():
    text = "Some prose paragraph.\n2,7183\nMore prose.\n"
    out, joined = repair(text)
    assert joined == 0
    assert out == text.rstrip("\n")


def test_repair_does_not_join_number_followed_by_number_or_marker():
    text = "4.1\n4.2\nTitle here ....... 9\n"
    out, joined = repair(text)
    # 4.1 followed by 4.2 (another number) must NOT join; 4.2 joins with its title
    assert joined == 1
    assert "4.2 Title here ....... 9" in out
    assert out.startswith("4.1\n")


def test_repair_skips_html_comment_and_rule_continuations():
    text = "4.1\n<!-- Page 5 of 38 -->\n4.2\n---\n"
    out, joined = repair(text)
    assert joined == 0  # neither continuation is title-ish


# Bilingual IEC: French page (CEI:YYYY header, no IEC:YYYY) is dropped.
BILINGUAL = """\
<!-- Page 4 of 38 -->
60853-3  CEI:2002
SOMMAIRE
4.1 Description generale
<!-- Page 5 of 38 -->
60853-3  IEC:2002
CONTENTS
4.1 General description
<!-- Page 6 of 38 -->
Annex tables with no language header
"""


def test_drop_french_pages_keeps_english_and_neutral():
    out, kept, dropped = drop_french_pages(BILINGUAL)
    assert dropped == 1
    assert kept == 2
    assert "General description" in out
    assert "Description generale" not in out
    assert "Annex tables with no language header" in out  # neutral page kept


def test_drop_french_pages_noop_without_markers():
    text = "No page markers here.\n4.1 Scope ...... 7\n"
    out, kept, dropped = drop_french_pages(text)
    assert dropped == 0 and kept == 0
    assert out == text
