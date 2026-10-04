"""Unit tests for emptyos.sdk.utils helpers.

Pure functions — no daemon needed at the function level, but the autouse
``server_health`` fixture in conftest.py will skip the suite if the daemon
is down (project convention).
"""

from __future__ import annotations

import io
import re
from datetime import date

import pytest

from emptyos.sdk import (
    csv_download_response,
    csv_to_rows,
    fm_scalar,
    format_markdown_table,
    parse_frontmatter,
    parse_json_fence,
    normalize_clock_time,
    normalize_relative_date,
    parse_markdown_table,
    read_upload_text,
    rows_to_csv,
    slug_from_path,
    slugify,
    slugify_unicode,
    sniff_columns,
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


# ── slugify_unicode ────────────────────────────────────────────────────


def test_slugify_unicode_keeps_non_ascii():
    # The whole reason this exists: slugify() drops CJK entirely, so every
    # Chinese title would collapse onto the fallback word and note ids would
    # not be unique.
    assert slugify("机器学习", fallback="untitled") == "untitled"
    assert slugify_unicode("机器学习", fallback="untitled") == "机器学习"
    assert slugify_unicode("Café au Lait") == "café-au-lait"


def test_slugify_unicode_kebab_cases_like_its_ascii_sibling():
    assert slugify_unicode("Hello World!") == "hello-world"
    assert slugify_unicode("  Spaced_Out  Title  ") == "spaced-out-title"


def test_slugify_unicode_preserves_dash_runs():
    # Differs from slugify(), which collapses any non-alphanumeric run.
    # "-" is inside the kept class, so only the surrounding spaces become
    # dashes and the literal run survives: " " + "--" + " " → 4 dashes.
    assert slugify_unicode("a -- b") == "a----b"
    assert slugify("a -- b") == "a-b"
    assert slugify_unicode("2026-08-16 Review") == "2026-08-16-review"


def test_slugify_unicode_no_truncation_by_default():
    # Default max_len=None — a note id must not change when its title grows.
    assert slugify_unicode("a" * 80) == "a" * 80
    assert slugify_unicode("a" * 80, max_len=60) == "a" * 60


def test_slugify_unicode_fallback_on_empty():
    assert slugify_unicode("", fallback="recipe") == "recipe"
    assert slugify_unicode("!!!", fallback="untitled") == "untitled"
    assert slugify_unicode(None, fallback="untitled") == "untitled"
    # no fallback → empty string (items/places `_to_kebab` shape)
    assert slugify_unicode("!!!") == ""


def test_slugify_unicode_truncation_before_fallback():
    assert slugify_unicode("", max_len=2, fallback="untitled") == "untitled"


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


@pytest.mark.parametrize("payload", ["=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(A1:A9)", "\t=1+1"])
def test_rows_to_csv_defangs_formula_prefixes(payload):
    """CSV formula injection (OWASP) — a leading =/+/-/@/tab/CR makes Excel/
    Sheets/LibreOffice execute the cell as a formula rather than render it
    as text. Any user-controlled string (a title, an editable label) must
    come out prefixed with a single quote so it renders as literal text."""
    out = rows_to_csv([{"text": payload}])
    assert out.splitlines()[1] == "'" + payload


def test_rows_to_csv_leaves_ordinary_text_and_numbers_untouched():
    out = rows_to_csv([{"a": 1, "b": "ordinary text", "c": -0.0}])
    assert out.splitlines()[1] == "1,ordinary text,-0.0"


def test_csv_to_rows_basic():
    text = "a,b\n1,2\n3,4\n"
    assert csv_to_rows(text) == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_csv_to_rows_quoted():
    assert csv_to_rows('a\n"hello, world"\n') == [{"a": "hello, world"}]


def test_csv_to_rows_empty():
    assert csv_to_rows("") == []
    assert csv_to_rows("   ") == []


_SNIFF_ALIASES = {
    "date": ["transaction date", "date"],
    "amount": ["amount", "value"],
    "description": ["description", "narrative"],
}


def test_sniff_columns_substring_case_insensitive():
    m = sniff_columns(["TRANSACTION DATE", "Debit Amount (AUD)", "narrative"], _SNIFF_ALIASES)
    assert m == {"date": "TRANSACTION DATE", "amount": "Debit Amount (AUD)",
                 "description": "narrative"}


def test_sniff_columns_one_header_claims_one_field():
    # "Amount" contains "amount"; a second field listing the same alias must
    # not re-claim the column already taken by the first.
    aliases = {"debit": ["amount"], "amount": ["amount", "value"]}
    m = sniff_columns(["Amount"], aliases)
    assert m == {"debit": "Amount"}


def test_sniff_columns_alias_order_is_field_order():
    # A more specific alias listed first wins over the generic one.
    aliases = {"first_name": ["first name"], "name": ["name"]}
    m = sniff_columns(["First Name", "Name"], aliases)
    assert m == {"first_name": "First Name", "name": "Name"}
    # Reverse the field order and the generic alias swallows the specific column.
    m2 = sniff_columns(["First Name", "Name"], {"name": ["name"], "first_name": ["first name"]})
    assert m2 == {"name": "First Name"}


def test_sniff_columns_alias_priority_beats_header_position():
    # The generic header comes FIRST in the file; the field's first alias is
    # the specific one, so it must still win. A header-major rewrite (first
    # field with any matching alias, per header) picks "Date" here.
    m = sniff_columns(["Date", "Transaction Date"], {"date": ["transaction date", "date"]})
    assert m == {"date": "Transaction Date"}


def test_sniff_columns_first_alias_hit_stops_the_field():
    # Once "transaction date" matched, the field must not go on to its second
    # alias and also claim "Date" — that header belongs to the next field.
    aliases = {"date": ["transaction date", "date"], "posted": ["date"]}
    m = sniff_columns(["Transaction Date", "Date"], aliases)
    assert m == {"date": "Transaction Date", "posted": "Date"}


def test_sniff_columns_missing_fields_absent_and_none_header_tolerated():
    assert sniff_columns(["Foo", None, ""], _SNIFF_ALIASES) == {}
    assert sniff_columns([], _SNIFF_ALIASES) == {}


class _Upload:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


class _Req:
    """The slice of a starlette Request that read_upload_text touches."""

    def __init__(self, ctype, form=None, json=None):
        self.headers = {"content-type": ctype}
        self._form, self._json = form, json

    async def form(self):
        return self._form

    async def json(self):
        return self._json


_MP = "multipart/form-data; boundary=x"


@pytest.mark.asyncio
async def test_read_upload_text_multipart_strips_bom():
    req = _Req(_MP, form={"file": _Upload(b"\xef\xbb\xbfa,b\n1,2\n")})
    assert await read_upload_text(req, max_bytes=100) == ("a,b\n1,2\n", "")


@pytest.mark.asyncio
async def test_read_upload_text_multipart_bad_byte_is_replaced_not_raised():
    # A Latin-1 statement (é as one byte) must import with U+FFFD, not 500.
    req = _Req(_MP, form={"file": _Upload(b"caf\xe9,1\n")})
    assert await read_upload_text(req, max_bytes=100) == ("caf�,1\n", "")


@pytest.mark.asyncio
async def test_read_upload_text_multipart_missing_or_non_file():
    assert await read_upload_text(_Req(_MP, form={}), max_bytes=100) == ("", "no file uploaded")
    # A plain form field named "file" is not an upload — it has no .read().
    req = _Req(_MP, form={"file": "not-an-upload"})
    assert await read_upload_text(req, max_bytes=100) == ("", "no file uploaded")


@pytest.mark.asyncio
async def test_read_upload_text_multipart_empty():
    req = _Req(_MP, form={"file": _Upload(b"")})
    assert await read_upload_text(req, max_bytes=100) == ("", "the file is empty")


@pytest.mark.asyncio
async def test_read_upload_text_size_cap_is_inclusive():
    at_cap = _Req(_MP, form={"file": _Upload(b"x" * 8)})
    one_over = _Req(_MP, form={"file": _Upload(b"x" * 9)})
    assert await read_upload_text(at_cap, max_bytes=8) == ("x" * 8, "")
    assert await read_upload_text(one_over, max_bytes=8) == ("", "file is too large")


@pytest.mark.asyncio
async def test_read_upload_text_json_body():
    ok = _Req("application/json", json={"csv_content": "a,b\n"})
    assert await read_upload_text(ok, max_bytes=100) == ("a,b\n", "")
    for body in ({}, {"csv_content": ""}, {"other": "x"}):
        req = _Req("application/json", json=body)
        assert await read_upload_text(req, max_bytes=100) == ("", "csv_content required")


@pytest.mark.asyncio
async def test_read_upload_text_json_key_is_configurable():
    ics = _Req("application/json", json={"ics_content": "BEGIN:VCALENDAR"})
    assert await read_upload_text(ics, max_bytes=100, json_key="ics_content") == ("BEGIN:VCALENDAR", "")
    wrong_key = _Req("application/json", json={"csv_content": "x"})
    assert await read_upload_text(wrong_key, max_bytes=100, json_key="ics_content") == ("", "ics_content required")


def test_markdown_to_csv_round_trip():
    md = "| name | age |\n|------|-----|\n| a    | 1   |\n| b    | 2   |"
    rows = parse_markdown_table(md)
    assert csv_to_rows(rows_to_csv(rows)) == rows


def test_csv_download_response_shape():
    resp = csv_download_response(
        [{"a": 1, "b": 2}], ["a", "b"], "export.csv")
    assert resp.media_type == "text/csv"
    assert resp.headers["content-disposition"] == 'attachment; filename="export.csv"'
    assert resp.body.decode().splitlines() == ["a,b", "1,2"]


def test_csv_download_response_writes_header_when_rows_empty():
    """Unlike rows_to_csv, an empty download must still open as a valid
    (header-only) spreadsheet — never a blank/absent file."""
    resp = csv_download_response([], ["a", "b"], "empty.csv")
    assert resp.body.decode().splitlines() == ["a,b"]


def test_csv_download_response_none_becomes_empty_field():
    resp = csv_download_response([{"a": 1, "b": None}], ["a", "b"], "x.csv")
    assert resp.body.decode().splitlines()[1] == "1,"


def test_csv_download_response_ignores_extra_row_keys():
    resp = csv_download_response(
        [{"a": 1, "b": 2, "extra": "dropped"}], ["a", "b"], "x.csv")
    assert resp.body.decode().splitlines() == ["a,b", "1,2"]


def test_csv_download_response_defangs_formula_prefixes():
    """csv_download_response hand-rolls its own DictWriter loop rather than
    calling rows_to_csv, so it needs the same CSV-formula-injection defense
    independently — this is the direct browser-download path apps use."""
    resp = csv_download_response([{"label": "=1+1"}], ["label"], "x.csv")
    assert resp.body.decode().splitlines()[1] == "'=1+1"


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

    def test_chinese_day_words_and_day_after_tomorrow(self):
        # The phone bridge hears 中文 and the model copies the user's word into
        # `due`; before 2026-09-30 明天 landed a task undated.
        from datetime import date, timedelta

        from emptyos.sdk.utils import relative_day_offset

        today = date.today()
        assert normalize_relative_date("明天") == (today + timedelta(days=1)).isoformat()
        assert normalize_relative_date("明早") == (today + timedelta(days=1)).isoformat()
        assert normalize_relative_date("后天") == (today + timedelta(days=2)).isoformat()
        assert normalize_relative_date("今晚") == today.isoformat()
        assert normalize_relative_date("day after tomorrow") == (today + timedelta(days=2)).isoformat()
        # The scorer rebases the same table onto a fixed date: one table, not two.
        assert relative_day_offset(" Tomorrow ") == 1
        assert relative_day_offset("后天") == 2
        assert relative_day_offset("2026-10-01") is None
        assert relative_day_offset("") is None


class TestNormalizeRelativeOffset:
    """'in 30 minutes' / '半小时后' → the (day, HH:MM) a reminder stores."""

    from datetime import datetime, timedelta

    NOW = datetime(2026, 9, 30, 23, 45)

    def test_english_offsets(self):
        from emptyos.sdk.utils import normalize_relative_offset as off

        assert off("in 30 minutes", self.NOW) == ("2026-10-01", "00:15")  # crosses midnight
        assert off("in 10 min", self.NOW) == ("2026-09-30", "23:55")
        assert off("in an hour", self.NOW) == ("2026-10-01", "00:45")
        assert off("in 1.5 hours", self.NOW) == ("2026-10-01", "01:15")
        assert off("2 hrs from now", self.NOW) == ("2026-10-01", "01:45")
        assert off("half an hour", self.NOW) == ("2026-10-01", "00:15")

    def test_chinese_offsets(self):
        from emptyos.sdk.utils import normalize_relative_offset as off

        assert off("半小时后", self.NOW) == ("2026-10-01", "00:15")
        assert off("半个小时后", self.NOW) == ("2026-10-01", "00:15")
        assert off("30分钟后", self.NOW) == ("2026-10-01", "00:15")
        assert off("十分钟之后", self.NOW) == ("2026-09-30", "23:55")
        assert off("二十分钟以后", self.NOW) == ("2026-10-01", "00:05")
        assert off("两小时后", self.NOW) == ("2026-10-01", "01:45")
        assert off("一个半小时后", self.NOW) == ("2026-10-01", "01:15")

    def test_not_an_offset_is_empty(self):
        from emptyos.sdk.utils import normalize_relative_offset as off

        for s in ("", None, "tomorrow", "明天", "9am", "in 0 minutes", "in minutes", "garbage",
                  "in 999999999999999 hours"):  # regex-valid, but no datetime can hold it
            assert off(s, self.NOW) == ("", ""), s

    def test_default_clock_is_now(self):
        from datetime import datetime, timedelta

        from emptyos.sdk.utils import normalize_relative_offset as off

        before = datetime.now()
        day, hm = off("in 2 hours")
        assert day and hm
        landed = datetime.fromisoformat(f"{day}T{hm}")
        assert timedelta(minutes=118) <= landed - before.replace(second=0, microsecond=0) <= timedelta(minutes=121)


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


def test_an_empty_scalar_reads_as_a_string_wherever_it_sits():
    """An empty value must not depend on what follows it in the block.

    A key with no inline value opens a pending block list. The mid-loop flush
    wrote that list back as `[]` while the *final* flush wrote `""`, so the
    same `key: ""` parsed as a list or a string purely by position — only the
    last one in the frontmatter came back right. `VaultLibrary._coerce` then
    ran `str([])` over the earlier ones and produced the *truthy* string
    "[]", which is how a scenario reached its issuable state carrying an
    engineer named "[]" while a `if not selected_by` guard watched it happen.
    """
    note = ('---\n'
            'tags:\n  - bid-scenario\n'
            'selected_by: ""\n'          # quoted empty, followed by more keys
            'bare_empty:\n'              # bare empty, followed by more keys
            'title: "Case"\n'
            'trailing_empty: ""\n'       # the position that always worked
            '---\nbody\n')
    fm = parse_frontmatter(note)
    assert fm["tags"] == ["bid-scenario"], "a real block list must stay a list"
    assert fm["title"] == "Case"
    for key in ("selected_by", "bare_empty", "trailing_empty"):
        assert fm[key] == "", f"{key} parsed as {fm[key]!r}, not an empty string"
        assert not fm[key], f"{key} is falsy-by-contract; str({fm[key]!r}) must not be truthy"


def test_there_is_only_one_frontmatter_parser():
    """The strongest form this can take: not "they agree", but "there is one".

    There used to be two — this one and `vault_index._parse_fm`, the read path
    behind `vault_query`. Near-verbatim copies, and they drifted three separate
    ways, each found only after the previous fix: empty scalars typed by their
    position in the block, quoted empties, and where the block ends. Every fix
    was correct and left the copies further apart, because a test asserting
    equal *output* only covers the inputs someone thought to list, and each
    drift lived in an input nobody had.

    An identity assertion has no such gap. If a fourth divergence is ever
    possible, this test fails at import rather than on the one note that
    happens to trigger it.
    """
    from emptyos.frontmatter import parse_frontmatter as canonical
    from emptyos.runtime.vault_index import _parse_fm

    assert parse_frontmatter is canonical, "sdk.utils must re-export, not redefine"
    assert _parse_fm is canonical, "vault_index must re-export, not redefine"


def test_one_syntax_reads_as_one_type():
    """The contract the copies kept breaking, stated as a table.

    Each row is a *syntax*, and each syntax has exactly one resulting type
    regardless of where the key sits in the block. `key:` used to be a list
    mid-block and a string at the end; `key: []` used to be a string here and
    a list in the twin, which is the same instability inside one syntax.
    """
    def fm(body: str, key: str = "notes"):
        return parse_frontmatter(f"---\n{body}\n---\nbody\n")[key]

    # Empty scalars — "" wherever they sit.
    assert fm("notes:\ntitle: x") == ""
    assert fm("title: x\nnotes:") == ""
    assert fm('notes: ""\ntitle: x') == ""
    assert fm('title: x\nnotes: ""') == ""
    assert fm("notes: ''\ntitle: x") == ""

    # Inline arrays — a list, empty or not. `[]` is an author writing an empty
    # list on purpose; returning "" for it would reintroduce, inside one
    # syntax, the type instability this whole contract removes.
    assert fm("notes: [a, b]\ntitle: x") == ["a", "b"]
    assert fm("notes: []\ntitle: x") == []

    # Block lists — a list, and a bare key with no children is not one.
    assert fm("notes:\n  - a\n  - b\ntitle: x") == ["a", "b"]

    # A quoted value that merely looks like an array stays a string.
    assert fm('notes: "[{\\"x\\": 1}]"\ntitle: x') == '[{"x": 1}]'

    # Every empty form is falsy — what `if not value` guards downstream rely on.
    for body in ("notes:\ntitle: x", 'notes: ""\ntitle: x', "notes: []\ntitle: x"):
        assert not fm(body), body


def test_a_delimiter_inside_a_value_does_not_end_the_block():
    """All three frontmatter helpers must agree where the block ends.

    `content.find("---", 3)` matches the delimiter anywhere, including inside a
    value, so `title: A---B` ended the block mid-value. The three helpers each
    did their own scan, so fixing one and not the others is worse than leaving
    all three wrong: read and write then disagree about the splice point.

    The write path is where it stops being a misread and becomes corruption —
    `set_frontmatter_field` rewrote the note into a malformed block carrying a
    duplicate of the key it had just set, and destroyed the value that
    contained the delimiter.
    """
    from emptyos.sdk.utils import set_frontmatter_field, strip_frontmatter

    note = "---\ntitle: A---B\nowner: kevin\n---\nreal body\n"

    assert parse_frontmatter(note) == {"title": "A---B", "owner": "kevin"}
    assert strip_frontmatter(note) == "\nreal body\n"

    out = set_frontmatter_field(note, "owner", "sam")
    assert out == "---\ntitle: A---B\nowner: sam\n---\nreal body\n"
    # The round trip is the real assertion: whatever the block boundary is,
    # every helper must use the same one.
    assert parse_frontmatter(out) == {"title": "A---B", "owner": "sam"}
    assert strip_frontmatter(out) == "\nreal body\n"
    assert out.count("owner:") == 1, "the write duplicated the key it set"


# ── image_to_data_url ────────────────────────────────────────────────────────
# Extracted from operate at its second consumer (nutrition's photo meal log).
# It is the encode half of the `see` capability contract: the `webcam` provider
# hands back a file path, `browser-webcam` hands back a data URL, and anything
# feeding `think(images=[...])` has to end up at the URL form.


def _write_png(path, size):
    from PIL import Image

    Image.new("RGB", size, (120, 30, 30)).save(path, format="PNG")
    return path


def test_image_to_data_url_round_trips_through_parse_data_url(tmp_path):
    """The two helpers are inverses — encode then decode must give real bytes
    back, not a string that merely looks like a data URL."""
    from emptyos.sdk.utils import image_to_data_url, parse_data_url

    src = _write_png(tmp_path / "meal.png", (64, 48))
    url = image_to_data_url(src)
    assert url and url.startswith("data:image/")
    mime, raw = parse_data_url(url)
    assert mime.startswith("image/")
    assert len(raw) > 0


def test_image_to_data_url_downscales_past_max_width(tmp_path):
    """Downscaling is the point, not a nicety — an un-resized camera frame is
    megabytes, and several of them blow a vision request past what providers
    accept."""
    from PIL import Image

    from emptyos.sdk.utils import image_to_data_url, parse_data_url

    src = _write_png(tmp_path / "wide.png", (2400, 1200))
    mime, raw = parse_data_url(image_to_data_url(src, max_width=320))
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(raw)) as im:
        assert im.width == 320, f"not downscaled: {im.width}"
        assert im.height == 160, "aspect ratio was not preserved"


def test_image_to_data_url_leaves_a_small_image_alone(tmp_path):
    from PIL import Image

    from emptyos.sdk.utils import image_to_data_url, parse_data_url

    src = _write_png(tmp_path / "small.png", (200, 100))
    _, raw = parse_data_url(image_to_data_url(src, max_width=1280))
    with Image.open(io.BytesIO(raw)) as im:
        assert (im.width, im.height) == (200, 100), "an under-width image was resized"


def test_image_to_data_url_returns_none_instead_of_raising(tmp_path):
    """Callers treat None as "no usable frame" and degrade. If this raised
    instead, a missing or corrupt capture would 500 the endpoint that captured
    it rather than reporting a soft failure."""
    from emptyos.sdk.utils import image_to_data_url

    assert image_to_data_url(tmp_path / "does-not-exist.png") is None

    junk = tmp_path / "notanimage.png"
    junk.write_bytes(b"this is not a PNG")
    assert image_to_data_url(junk) is None


# --- extract_wikilinks: code is not a link -----------------------------------
# 11 call sites read this (kb graph, rooms, vault-graph, shadowing, app-builder),
# and it counted `[[Note]]` inside code samples as real links. Measured on the
# live vault: the three most-referenced targets overall were `${block.reference}`,
# `" + block.reference +"` and `${refId}` — JS template literals in fences.


def test_extract_wikilinks_finds_prose_links():
    from emptyos.sdk.utils import extract_wikilinks

    assert extract_wikilinks("see [[Alpha]] and [[b/c/Beta]]") == {"Alpha", "b/c/Beta"}


def test_extract_wikilinks_strips_anchor_and_alias():
    from emptyos.sdk.utils import extract_wikilinks

    assert extract_wikilinks("[[Alpha#Section]] [[Beta|shown]]") == {"Alpha", "Beta"}


def test_extract_wikilinks_ignores_fenced_code():
    from emptyos.sdk.utils import extract_wikilinks

    assert extract_wikilinks("[[Real]]\n```js\nconst q = `[[Fake]]`;\n```\n") == {"Real"}


def test_extract_wikilinks_ignores_inline_code():
    from emptyos.sdk.utils import extract_wikilinks

    assert extract_wikilinks("[[Real]] but not `[[Fake]]`") == {"Real"}


def test_extract_wikilinks_ignores_an_unclosed_fence():
    from emptyos.sdk.utils import extract_wikilinks

    assert extract_wikilinks("[[Real]]\n```\n[[Fake]]\n") == {"Real"}


def test_extract_wikilinks_empty_input():
    from emptyos.sdk.utils import extract_wikilinks

    assert extract_wikilinks("") == set()
    assert extract_wikilinks(None) == set()
