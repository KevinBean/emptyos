"""worklog — CPEng competency tag parsing + evidence roll-up (daemon-free).

Pins the `#cN` convention, which is load-bearing precisely because it is *not*
a schema change: the tag rides inside an item's own text so it round-trips
through the markdown parser, the portable JSON, the import merge and the
standalone browser bundle untouched. A regression here is silent — a tag that
stops parsing looks exactly like a day you forgot to tag.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "apps/public/standard/worklog"


def _load_shared():
    """Import worklog.shared standalone (no kernel, no app package boot)."""
    pkg = types.ModuleType("wl_pkg")
    pkg.__path__ = [str(APP_DIR)]
    sys.modules["wl_pkg"] = pkg
    spec = importlib.util.spec_from_file_location("wl_pkg.shared", APP_DIR / "shared.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wl_pkg.shared"] = mod
    spec.loader.exec_module(mod)
    return mod


shared = _load_shared()
parse_competencies = shared.parse_competencies
strip_competency_tags = shared.strip_competency_tags


# ── the standard itself ─────────────────────────────────────────────────────

def test_sixteen_elements_across_four_areas():
    assert len(shared.COMPETENCIES) == 16
    assert sorted(shared.COMPETENCIES) == list(range(1, 17))
    flat = [n for nums in shared.COMPETENCY_AREAS.values() for n in nums]
    # Every element belongs to exactly one area — a duplicated or dropped
    # element would silently skew the roll-up's coverage count.
    assert sorted(flat) == list(range(1, 17))
    assert len(shared.COMPETENCY_AREAS) == 4


def test_focus_marks_the_two_failing_elements():
    # From the evidence inventory (2026-08-16): 11 judgement and 13 local
    # engineering knowledge are the two that can fail the application.
    assert shared.COMPETENCY_FOCUS[11] == "gap"
    assert shared.COMPETENCY_FOCUS[13] == "gap"
    assert shared.COMPETENCY_FOCUS[1] == "thin"


# ── parsing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Escalated the BYDA clash in writing #c11", [11]),
    ("#c13 Applied AS/NZS 7000 to the earthing layout", [13]),
    ("Reviewed the joint bay #c6 #c14", [6, 14]),
    ("dedup #c11 and again #c11", [11]),
    ("ascending #c14 #c2", [2, 14]),
    ("no tags here", []),
    ("", []),
])
def test_parse(text, expected):
    assert parse_competencies(text) == expected


def test_out_of_range_and_zero_ignored():
    # 0 and 17+ are not elements; a stray "#c99" must not invent one.
    assert parse_competencies("#c0 #c17 #c99 #c42") == []


def test_c1_and_c11_are_distinguished():
    # Greedy \d{1,2} + \b: "#c11" is eleven, never one-then-stray-1.
    assert parse_competencies("#c11") == [11]
    assert parse_competencies("#c1") == [1]
    assert parse_competencies("#c1 #c11") == [1, 11]


def test_not_matched_mid_word_or_double_hash():
    # A URL fragment or a plain word must not read as a competency tag.
    assert parse_competencies("see doc#c11") == []
    assert parse_competencies("abc11") == []
    assert parse_competencies("##c11") == []


# ── stripping ───────────────────────────────────────────────────────────────

def test_strip_removes_tags_and_tidies_spacing():
    assert strip_competency_tags("Escalated the clash #c11") == "Escalated the clash"
    assert strip_competency_tags("#c13 Applied AS/NZS 7000") == "Applied AS/NZS 7000"
    assert strip_competency_tags("Reviewed #c6 the bay #c14") == "Reviewed the bay"


def test_strip_is_a_noop_without_tags():
    assert strip_competency_tags("plain item text") == "plain item text"
    assert strip_competency_tags("") == ""


def test_strip_preserves_other_hashtags():
    # worklog items legitimately carry ordinary tags; only #cN is ours.
    assert strip_competency_tags("Site walk #site #c13") == "Site walk #site"


def test_tag_survives_a_markdown_item_round_trip():
    """The whole reason for a hashtag: the parser never has to know about it."""
    spec = importlib.util.spec_from_file_location("wl_pkg.parser", APP_DIR / "parser.py")
    parser = importlib.util.module_from_spec(spec)
    sys.modules["wl_pkg.parser"] = parser
    spec.loader.exec_module(parser)

    groups = [{"project": "Kingsford 33kV", "items": [
        {"text": "Rejected the deviation and required a redesign #c11", "status": "complete"},
    ]}]
    md = parser.render_work(groups)
    assert "#c11" in md
    back = parser.parse_day("## Work\n\n" + md)
    item = back["projects"][0]["items"][0]
    assert parse_competencies(item["text"]) == [11]
