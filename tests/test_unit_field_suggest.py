"""Unit tests for the field-suggest parse helper — pure, no daemon.

Covers emptyos/sdk/base_app.py::_parse_suggestions, the JSON-reply parser
behind BaseApp.suggest_field + the ✨ field-suggest affordance
(.claude/rules/field-suggest.md). The think() call and vault grounding need a
kernel and are exercised in the sandbox; this pins the pure parse/cap/dedup.
"""

from __future__ import annotations

from emptyos.sdk.base_app import _parse_suggestions


def test_documented_object_shape():
    assert _parse_suggestions('{"suggestions": ["a", "b", "c"]}', 5) == ["a", "b", "c"]


def test_bare_list():
    assert _parse_suggestions('["x", "y"]', 5) == ["x", "y"]


def test_fenced_json_is_stripped():
    raw = '```json\n{"suggestions": ["q"]}\n```'
    assert _parse_suggestions(raw, 5) == ["q"]


def test_garbage_yields_empty_never_raises():
    assert _parse_suggestions("not json at all", 5) == []
    assert _parse_suggestions("", 5) == []
    assert _parse_suggestions(None, 5) == []


def test_dedup_case_insensitive_preserves_order():
    raw = '{"suggestions": ["Foo", "bar", "foo", "BAR", "baz"]}'
    assert _parse_suggestions(raw, 10) == ["Foo", "bar", "baz"]


def test_cap_to_count():
    raw = '{"suggestions": ["a", "b", "c", "d", "e"]}'
    assert _parse_suggestions(raw, 2) == ["a", "b"]


def test_count_floor_is_one():
    # count<=0 must still return at least one, never an empty slice by accident.
    assert _parse_suggestions('["only"]', 0) == ["only"]


def test_single_key_object_fallback():
    # reader's endpoint returns {"premises": [...]} — tolerate any single list value.
    assert _parse_suggestions('{"premises": ["p1", "p2"]}', 5) == ["p1", "p2"]


def test_already_parsed_dict_and_list_pass_through():
    assert _parse_suggestions({"suggestions": ["a"]}, 5) == ["a"]
    assert _parse_suggestions(["a", "b"], 5) == ["a", "b"]


def test_non_string_items_coerced_and_blanks_dropped():
    raw = '{"suggestions": [1, "  ", "real", "  spaced  "]}'
    assert _parse_suggestions(raw, 5) == ["1", "real", "spaced"]
