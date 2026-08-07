"""Unit tests for emptyos.sdk.utils helpers.

Pure functions — no daemon needed at the function level, but the autouse
``server_health`` fixture in conftest.py will skip the suite if the daemon
is down (project convention).
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from emptyos.sdk import (
    csv_to_rows,
    fm_scalar,
    format_markdown_table,
    parse_frontmatter,
    parse_json_fence,
    normalize_clock_time,
    normalize_relative_date,
    parse_markdown_table,
    rows_to_csv,
    slug_from_path,
    slugify,
    today_iso,
    unique_slug,
)
from emptyos.sdk.utils import parse_llm_json, safe_path_segment
from emptyos.sdk.utils import today_iso as today_iso_direct
from emptyos.sdk.utils import unique_slug as unique_slug_direct


def test_today_iso_format():
    s = today_iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s), f"not ISO date: {s!r}"


def test_today_iso_matches_date_today():
    assert today_iso() == date.today().isoformat()


def test_today_iso_export_paths_agree():
    assert today_iso is today_iso_direct


# ── unique_slug ────────────────────────────────────────────────────────


def test_unique_slug_basic_lowercase_and_dash_join():
    assert unique_slug("Vic Substation 3", prefix="x") == "vic-substation-3"


def test_unique_slug_preserves_embedded_dashes():
    # Unlike slugify(), unique_slug keeps runs of dashes from the input.
    assert unique_slug("2026-05-15 Lightning", prefix="x") == "2026-05-15-lightning"


def test_unique_slug_strips_leading_trailing_dashes():
    assert unique_slug("  hello world  ", prefix="x") == "hello-world"


def test_unique_slug_falls_back_to_prefix_uuid_when_empty():
    s = unique_slug("", prefix="earthing")
    assert re.fullmatch(r"earthing-[0-9a-f]{8}", s), f"unexpected fallback: {s!r}"


def test_unique_slug_falls_back_when_input_has_no_alphanumerics():
    s = unique_slug("!!!---!!!", prefix="study")
    assert re.fullmatch(r"study-[0-9a-f]{8}", s), f"unexpected fallback: {s!r}"


def test_unique_slug_deterministic_for_same_input():
    assert unique_slug("same input", prefix="x") == unique_slug("same input", prefix="y")


def test_unique_slug_handles_none_input():
    s = unique_slug(None, prefix="layer")
    assert re.fullmatch(r"layer-[0-9a-f]{8}", s), f"unexpected fallback: {s!r}"


def test_unique_slug_export_paths_agree():
    assert unique_slug is unique_slug_direct


# ── slugify ────────────────────────────────────────────────────────────


def test_slugify_basic_and_default_cap():
    assert slugify("Hello World!") == "hello-world"
    # default max_len=60 truncates
    assert slugify("a" * 80) == "a" * 60


def test_slugify_max_len_none_disables_truncation():
    assert slugify("a" * 80, max_len=None) == "a" * 80


def test_slugify_fallback_on_empty():
    assert slugify("", fallback="course") == "course"
    assert slugify("!!!", fallback="deck") == "deck"
    assert slugify(None, fallback="untitled") == "untitled"
    # no fallback → empty string (pre-fallback behaviour preserved)
    assert slugify("!!!") == ""


def test_slugify_truncation_before_fallback():
    # cap applies to the slug, then fallback fires only if the slug is empty —
    # a fallback word is never truncated (rag-eval `s[:60] or "case"` shape)
    assert slugify("x" * 80, max_len=48, fallback="braindump") == "x" * 48
    assert slugify("", max_len=2, fallback="braindump") == "braindump"


# ── safe_path_segment ──────────────────────────────────────────────────


def test_safe_path_segment_accepts_plain_slugs():
    assert safe_path_segment("cad-ab12cd34") == "cad-ab12cd34"
    assert safe_path_segment("My_Model-2") == "My_Model-2"
    assert safe_path_segment("test-inbox") == "test-inbox"


def test_safe_path_segment_rejects_traversal_and_separators():
    for bad in ["../x", "..\\x", "a/b", "a\\b", "..", ".hidden", "x y", "", "a/../b"]:
        assert safe_path_segment(bad) == "", bad


def test_safe_path_segment_handles_none():
    assert safe_path_segment(None) == ""


def test_safe_path_segment_rejects_not_transforms():
    # Unlike slugify/unique_slug, it REJECTS unsafe input (returns "") rather
    # than coercing "../etc" into a slug — that's the whole point of the guard.
    assert safe_path_segment("../etc") == ""


# ── markdown tables ────────────────────────────────────────────────────


def test_parse_markdown_table_basic():
    text = "| name | age |\n|------|-----|\n| a    | 1   |\n| b    | 2   |\n"
    assert parse_markdown_table(text) == [
        {"name": "a", "age": "1"},
        {"name": "b", "age": "2"},
    ]


def test_parse_markdown_table_no_outer_pipes():
    text = "name | age\n--- | ---\na | 1\nb | 2"
    assert parse_markdown_table(text) == [
        {"name": "a", "age": "1"},
        {"name": "b", "age": "2"},
    ]


def test_parse_markdown_table_with_alignment_markers():
    text = "| a | b |\n|:--|--:|\n| 1 | 2 |"
    assert parse_markdown_table(text) == [{"a": "1", "b": "2"}]


def test_parse_markdown_table_stops_at_blank_line():
    text = "| a |\n|---|\n| 1 |\n| 2 |\n\nNot a table line"
    assert parse_markdown_table(text) == [{"a": "1"}, {"a": "2"}]


def test_parse_markdown_table_skips_text_before():
    text = "Some preamble\n\n| col |\n|-----|\n| val |\n"
    assert parse_markdown_table(text) == [{"col": "val"}]


def test_parse_markdown_table_no_table_returns_empty():
    assert parse_markdown_table("just prose with no pipes") == []
    assert parse_markdown_table("") == []
    assert parse_markdown_table(None) == []  # type: ignore[arg-type]


def test_parse_markdown_table_pads_short_rows():
    text = "| a | b | c |\n|---|---|---|\n| 1 | 2 |\n"
    assert parse_markdown_table(text) == [{"a": "1", "b": "2", "c": ""}]


def test_format_markdown_table_basic():
    rows = [{"name": "alice", "age": 30}, {"name": "bob", "age": 25}]
    out = format_markdown_table(rows)
    lines = out.splitlines()
    assert lines[0] == "| name  | age |"
    assert lines[1] == "| ----- | --- |"
    assert lines[2] == "| alice | 30  |"
    assert lines[3] == "| bob   | 25  |"


def test_format_markdown_table_explicit_column_order():
    rows = [{"a": 1, "b": 2, "c": 3}]
    out = format_markdown_table(rows, columns=["c", "a"])
    assert out.splitlines()[0] == "| c | a |"
    assert "b" not in out


def test_format_markdown_table_missing_keys_render_empty():
    rows = [{"a": 1, "b": 2}, {"a": 3}]
    out = format_markdown_table(rows)
    assert out.splitlines()[3] == "| 3 |   |"


def test_format_markdown_table_empty_returns_empty_string():
    assert format_markdown_table([]) == ""


def test_format_markdown_table_none_renders_empty():
    out = format_markdown_table([{"a": 1, "b": None}])
    assert out.splitlines()[2] == "| 1 |   |"


def test_rows_to_csv_basic():
    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    lines = rows_to_csv(rows).splitlines()
    assert lines == ["a,b", "1,2", "3,4"]


def test_rows_to_csv_quotes_commas_and_quotes():
    rows = [{"text": 'hello, "world"'}]
    out = rows_to_csv(rows)
    assert '"hello, ""world"""' in out


def test_rows_to_csv_none_becomes_empty_field():
    out = rows_to_csv([{"a": 1, "b": None}])
    assert out.splitlines()[1] == "1,"


def test_rows_to_csv_empty_returns_empty_string():
    assert rows_to_csv([]) == ""


def test_csv_to_rows_basic():
    text = "a,b\n1,2\n3,4\n"
    assert csv_to_rows(text) == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_csv_to_rows_quoted():
    assert csv_to_rows('a\n"hello, world"\n') == [{"a": "hello, world"}]


def test_csv_to_rows_empty():
    assert csv_to_rows("") == []
    assert csv_to_rows("   ") == []


def test_markdown_to_csv_round_trip():
    md = "| name | age |\n|------|-----|\n| a    | 1   |\n| b    | 2   |"
    rows = parse_markdown_table(md)
    assert csv_to_rows(rows_to_csv(rows)) == rows


def test_csv_to_markdown_round_trip():
    rows = csv_to_rows("name,age\nalice,30\nbob,25\n")
    assert parse_markdown_table(format_markdown_table(rows)) == rows


# ── config_flag ─────────────────────────────────────────────────────────


def test_config_flag_bool_and_string_shapes():
    from emptyos.sdk.utils import config_flag

    class _Cfg:
        def __init__(self, v):
            self._v = v

        def get(self, key, default=None):
            return self._v

    assert config_flag(_Cfg(True), "k") is True
    assert config_flag(_Cfg(False), "k") is False
    for s in ("true", "1", "yes", "on", " TRUE "):
        assert config_flag(_Cfg(s), "k") is True
    for s in ("false", "0", "off", "", "maybe"):
        assert config_flag(_Cfg(s), "k") is False
    # Non-bool, non-string shapes read as False.
    assert config_flag(_Cfg(1), "k") is False


def test_config_flag_degrades_on_minimal_config():
    from emptyos.sdk.utils import config_flag

    class _NoGet:
        pass

    assert config_flag(_NoGet(), "k") is False
    assert config_flag(_NoGet(), "k", default=True) is True


def test_config_flag_export_paths_agree():
    from emptyos.sdk import config_flag as via_pkg
    from emptyos.sdk.utils import config_flag as via_mod

    assert via_pkg is via_mod


# ── slug_from_path ───────────────────────────────────────────────────────


def test_slug_from_path_posix_strips_dir_and_md():
    assert slug_from_path("10_Projects/foo/bar-baz.md") == "bar-baz"


def test_slug_from_path_windows_separators():
    # Path(path).name would NOT split on backslash on POSIX hosts — this must.
    assert slug_from_path(r"D:\Vault\sub\note.md") == "note"


def test_slug_from_path_no_md_extension():
    assert slug_from_path("dir/some-name") == "some-name"


def test_slug_from_path_bare_filename():
    assert slug_from_path("only.md") == "only"


def test_slug_from_path_empty_and_none():
    assert slug_from_path("") == ""
    assert slug_from_path(None) == ""


def test_slug_from_path_export_paths_agree():
    from emptyos.sdk import slug_from_path as via_pkg
    from emptyos.sdk.utils import slug_from_path as via_mod

    assert via_pkg is via_mod


class TestNormalizeRelativeDate:
    """Shared voice-intent due parser (task.voice_add_task, reminders.voice_add_reminder)."""

    def test_relative_tokens(self):
        from datetime import date, timedelta

        assert normalize_relative_date("today") == date.today().isoformat()
        assert normalize_relative_date("Tonight") == date.today().isoformat()
        assert normalize_relative_date("tomorrow") == (date.today() + timedelta(days=1)).isoformat()

    def test_iso_passthrough_and_datetime_trim(self):
        assert normalize_relative_date("2026-08-01") == "2026-08-01"
        assert normalize_relative_date("2026-08-01T09:00:00") == "2026-08-01"

    def test_unparseable_returns_empty(self):
        assert normalize_relative_date("") == ""
        assert normalize_relative_date(None) == ""
        assert normalize_relative_date("next tuesday") == ""
        assert normalize_relative_date("08/01/2026") == ""


class TestNormalizeClockTime:
    """Shared voice-intent time-of-day parser (reminders.voice_add_reminder)."""

    def test_plain_hour(self):
        assert normalize_clock_time("9") == "09:00"
        assert normalize_clock_time("17") == "17:00"

    def test_am_pm(self):
        assert normalize_clock_time("9am") == "09:00"
        assert normalize_clock_time("9pm") == "21:00"
        assert normalize_clock_time("12am") == "00:00"
        assert normalize_clock_time("12pm") == "12:00"

    def test_minutes_and_spacing(self):
        assert normalize_clock_time("9:30 pm") == "21:30"
        assert normalize_clock_time("17:00") == "17:00"

    def test_unparseable_returns_empty(self):
        assert normalize_clock_time("") == ""
        assert normalize_clock_time(None) == ""
        assert normalize_clock_time("noon") == ""
        assert normalize_clock_time("25:00") == ""
        assert normalize_clock_time("9:99") == ""


# ── parse_llm_json ─────────────────────────────────────────────────────
#
# 116 files call this and it had no direct test until 2026-07-31, when a
# model-bench grader exposed that an unfenced array with any preamble returned
# its FIRST ELEMENT as a dict: the old scanner looked for every `{...}` before
# trying `[...]` at all. Resolution is now largest-structure-wins.


def test_parse_llm_json_bare_and_fenced():
    assert parse_llm_json('{"a":1}') == {"a": 1}
    assert parse_llm_json("[1,2,3]") == [1, 2, 3]
    assert parse_llm_json('```json\n{"a":1}\n```') == {"a": 1}
    assert parse_llm_json('```\n[{"a":1}]\n```') == [{"a": 1}]


def test_parse_llm_json_unfenced_array_survives_prose():
    """The 2026-07-31 regression. Each of these returned `{"a": 1}` before."""
    payload = [{"a": 1}, {"a": 2}, {"a": 3}]
    body = '[{"a":1},{"a":2},{"a":3}]'
    assert parse_llm_json(f"Here are my picks:\n\n{body}") == payload
    assert parse_llm_json(f"{body}\n\nThat is my answer.") == payload
    assert parse_llm_json(f"Picks:\n{body}\nHope that helps.") == payload


def test_parse_llm_json_prose_brackets_do_not_beat_the_payload():
    """The case that makes largest-wins the right rule rather than earliest-wins.

    A citation `[1]` is valid JSON, so an earliest-wins scanner would return it
    and drop the object the caller asked for. Largest-wins keeps the payload."""
    assert parse_llm_json('Per the notes [1], here it is: {"verb":"task.add"}') == {
        "verb": "task.add"
    }
    assert parse_llm_json('Options [a, b, c] were considered. {"choice":"a"}') == {"choice": "a"}
    assert parse_llm_json('See [[some-note]] — result: {"ok":true}') == {"ok": True}


def test_parse_llm_json_is_string_aware():
    """Braces and brackets inside string literals are content, not structure."""
    assert parse_llm_json('Result: {"a":"has } brace"}') == {"a": "has } brace"}
    assert parse_llm_json('Result: {"a":"use [brackets] here"}') == {"a": "use [brackets] here"}
    assert parse_llm_json('Result: {"tpl":"{value}"}') == {"tpl": "{value}"}
    assert parse_llm_json('Note: {"a":"he said \\"hi\\""}') == {"a": 'he said "hi"'}


def test_parse_llm_json_falls_back_to_a_nested_structure():
    """When the outermost span won't parse, a complete inner one beats nothing."""
    assert parse_llm_json('[{"a":1}, {"b":2},]') == {"a": 1}  # trailing comma kills the array


def test_parse_llm_json_recovers_after_a_stray_closer():
    """Previously raised: the depth counter went negative and never recovered,
    so every structure after a stray `}` was discarded."""
    assert parse_llm_json('} then {"a":1}') == {"a": 1}


def test_parse_llm_json_two_siblings_largest_wins():
    """Documented consequence, pinned so the trade-off stays visible.

    With two sibling structures the bigger one wins rather than the earlier one.
    The case is ambiguous by nature — a model asked for one JSON value that emits
    two has already failed the instruction — and no caller can depend on it."""
    assert parse_llm_json('[1,2] and also [3,4,5,6]') == [3, 4, 5, 6]
    assert parse_llm_json('{"a":1} and also {"b":2}') == {"a": 1}  # equal length → earliest


def test_parse_llm_json_fallback_and_raise():
    assert parse_llm_json("no json here", fallback={}) == {}
    assert parse_llm_json("", fallback=[]) == []
    with pytest.raises(ValueError):
        parse_llm_json("no json here")
    with pytest.raises(ValueError):
        parse_llm_json('{"a":1')  # unclosed


# ── fm_scalar / parse_json_fence (extracted from replay + operate) ──────────
#
# Both halves of one contract: fm_scalar encodes what parse_frontmatter reads
# back, and parse_json_fence reads the body block that keeps machine JSON OUT
# of frontmatter entirely. Round-trip tests, because that pairing is the whole
# point — a change that breaks either direction breaks a committed note format.


def test_fm_scalar_leaves_plain_values_bare():
    assert fm_scalar("triage inbox") == "triage inbox"
    assert fm_scalar("recipe") == "recipe"


def test_fm_scalar_quotes_yaml_significant_and_padded_values():
    assert fm_scalar("a: b") == '"a: b"'
    assert fm_scalar("tags [x]") == '"tags [x]"'
    assert fm_scalar(" padded ") == '" padded "'
    assert fm_scalar("") == '""'


def test_fm_scalar_round_trips_through_parse_frontmatter():
    """The contract the encoder exists for. Apostrophes are the case that made
    the single-quote form unusable — parse_frontmatter never un-doubles ''."""
    for value in ("it's fine", 'he said "hi"', "a: b", "back\\slash", " padded ", ""):
        note = f"---\nname: {fm_scalar(value)}\n---\nbody\n"
        assert parse_frontmatter(note).get("name", "") == value


def test_parse_json_fence_extracts_the_body_block():
    body = 'Prose above.\n\n```json\n{"steps": [1, 2], "n": 3}\n```\n\nProse below.'
    assert parse_json_fence(body) == {"steps": [1, 2], "n": 3}


def test_parse_json_fence_degrades_to_empty_never_raises():
    """A hand-editable note with a broken fence must not crash the reader."""
    assert parse_json_fence("") == {}
    assert parse_json_fence(None) == {}  # the guard the "never raises" contract needs
    assert parse_json_fence("no fence here") == {}
    assert parse_json_fence("```json\n{not json,}\n```") == {}
    assert parse_json_fence("```json\n[1, 2]\n```") == {}  # top-level array → {}


def test_parse_json_fence_ignores_unlabelled_fences():
    """Stricter than parse_llm_json on purpose: this reads a committed format,
    so only an explicitly ```json-labelled block counts."""
    assert parse_json_fence('```\n{"a": 1}\n```') == {}
    assert parse_llm_json('```\n{"a": 1}\n```') == {"a": 1}  # the fuzzy sibling does
