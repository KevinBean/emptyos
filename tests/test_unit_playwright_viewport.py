"""Unit tests for the playwright plugin's per-navigate viewport parsing.

``parse_viewport`` is pure and the plugin imports Playwright only under
``TYPE_CHECKING``, so this needs no browser and no daemon.

Why it raises instead of falling back: a viewport that is silently ignored
produces a capture that looks entirely correct and is the wrong width. A
reviewer would then be commenting on a desktop layout while the record claims
it is mobile — a wrong answer wearing a right one's clothes. Every malformed
case below is therefore pinned as a raise, not as a default.
"""

from __future__ import annotations

import pytest

from plugins.playwright.plugin import parse_viewport


def test_none_leaves_the_context_viewport_alone():
    assert parse_viewport(None) is None


@pytest.mark.parametrize("raw", [
    "1440x900",
    "1440X900",
    " 1440 x 900 ",
    "1440×900",          # the unicode multiplication sign
])
def test_string_forms(raw):
    assert parse_viewport(raw) == {"width": 1440, "height": 900}


def test_dict_form():
    assert parse_viewport({"width": 390, "height": 844}) == {"width": 390, "height": 844}


def test_dict_form_coerces_numeric_strings():
    assert parse_viewport({"width": "390", "height": "844"}) == {"width": 390, "height": 844}


@pytest.mark.parametrize("raw", [
    "1440",                   # no separator
    "1440x900x2",             # too many parts
    "axb",
    "",
    "x",
    42,
    [1440, 900],
    {"width": 1440},          # missing height
    {"width": None, "height": 900},
    {"width": "wide", "height": 900},
])
def test_malformed_input_raises(raw):
    with pytest.raises(ValueError):
        parse_viewport(raw)


@pytest.mark.parametrize("raw", [
    {"width": True, "height": 900},
    {"width": 1440, "height": False},
])
def test_booleans_are_rejected(raw):
    """``isinstance(True, int)`` is True in Python, so a bool would otherwise
    become a 1px viewport rather than an error."""
    with pytest.raises(ValueError):
        parse_viewport(raw)


@pytest.mark.parametrize("raw", ["0x900", "1440x0", "-5x900", "10001x900", "1440x10001"])
def test_out_of_range_raises(raw):
    with pytest.raises(ValueError):
        parse_viewport(raw)


def test_boundaries_are_inclusive():
    assert parse_viewport("1x1") == {"width": 1, "height": 1}
    assert parse_viewport("10000x10000") == {"width": 10000, "height": 10000}
