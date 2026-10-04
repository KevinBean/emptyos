"""Unit tests for scripts/atomize_standard helpers — pure. No daemon."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from atomize_standard import _norm_edition  # noqa: E402


def test_cosmetic_parenthetical_collapses():
    # The exact trap that created a duplicate §4.1 note.
    assert _norm_edition("2015 (2.1)") == _norm_edition("2015")


def test_whitespace_and_case_insensitive():
    assert _norm_edition("  Rev 1.0 ") == _norm_edition("rev 1.0")
    assert _norm_edition("2019") == _norm_edition("2019")


def test_genuine_revisions_stay_distinct():
    assert _norm_edition("Rev 0.2") != _norm_edition("Rev 1.0")
    assert _norm_edition("1989") != _norm_edition("2002")


def test_none_and_empty():
    assert _norm_edition(None) == ""
    assert _norm_edition("") == ""
    assert _norm_edition("  ") == ""
