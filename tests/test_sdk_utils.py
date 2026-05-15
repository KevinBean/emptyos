"""Unit tests for emptyos.sdk.utils helpers.

Pure functions — no daemon needed at the function level, but the autouse
``server_health`` fixture in conftest.py will skip the suite if the daemon
is down (project convention).
"""

from __future__ import annotations

import re
from datetime import date

from emptyos.sdk import (
    csv_to_rows,
    format_markdown_table,
    parse_markdown_table,
    rows_to_csv,
    today_iso,
    unique_slug,
)
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
