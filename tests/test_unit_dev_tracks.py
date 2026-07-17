"""Unit tests for emptyos/sdk/dev_tracks.py — pure parsers, no daemon.

Fixtures mirror the real shapes: _index.md rows whose col-3 prose embeds
pipes, DEFERRED-WORK.md rows with bold/struck features, brief bodies with
[blocked-human]-tagged threads.
"""

from __future__ import annotations

import datetime

from emptyos.sdk.dev_tracks import (
    DEFERRED_STATUS_VOCAB,
    TrackBrief,
    age_days,
    classify_deferred,
    classify_track,
    link_key,
    links_for_track,
    parse_date,
    parse_deferred_table,
    parse_devlog_meta,
    parse_themes,
    parse_track_brief,
    parse_track_index,
    remove_index_row,
    set_deferred_status,
    theme_index,
)

INDEX_MD = """---
type: next-session-index
written: 2026-06-21
---

# Next-session tracks

Some prose with an unrelated table:

| Col A | Col B |
|---|---|
| not | a track |

| Track | Last touched | Last session | File |
|---|---|---|---|
| [external-lab](external-lab.md) | 2026-07-17 | Prose with an embedded pipe | and `code | more` inside. | [[external-lab]] |
| [em-engines](em-engines.md) | 2026-07-01 | Plain prose. | [[em-engines]] |
| not-a-date-row | soon | should be skipped | [[nope]] |
| [old-track](old-track.md) | 2026-05-02 | Ancient. | [[old-track]] |

Trailing prose after the table.
"""

DEFERRED_MD = """# Deferred work

| Feature | Build / deploy trigger | Reference | Full verdict / source | Added | Status |
|---|---|---|---|---|---|
| **Effect ledger** — exactly-once side effects | A stage acquires a non-idempotent effect | `emptyos/sdk/pipeline.py` | memory `x` | 2026-07-17 | deferred |
| Plain feature row | trigger text | ref | src | 2026-05-01 | deferred |
| ~~Shipped thing~~ **SHIPPED** | was: trigger | ref | src | 2026-04-01 | built |
"""

BRIEF_BODY = """
# Next session — dogfood the spine

## Where things stand
All four phases shipped.

## Open threads
- **Dogfood, don't build.** Run the whole spine on a real substation.
- Production redeploy still the bottleneck [blocked-human]
not a bullet line

## Recommended starting move
Open `/substation-project/` and create a real project.

## Working-tree snapshot
```
 M apps/foo/app.py
```
"""

TODAY = datetime.date(2026, 7, 17)


class TestParseTrackIndex:
    def test_parses_only_real_track_rows(self):
        rows = parse_track_index(INDEX_MD)
        assert [r.name for r in rows] == ["external-lab", "em-engines", "old-track"]

    def test_prose_pipes_do_not_break_first_two_cells(self):
        rows = parse_track_index(INDEX_MD)
        assert rows[0].last_touched == "2026-07-17"
        assert rows[0].name == "external-lab"

    def test_unrelated_table_before_header_is_ignored(self):
        rows = parse_track_index(INDEX_MD)
        assert all(r.name != "not" for r in rows)

    def test_line_no_points_at_the_raw_line(self):
        rows = parse_track_index(INDEX_MD)
        lines = INDEX_MD.splitlines()
        for r in rows:
            assert lines[r.line_no] == r.raw_line


class TestParseTrackBrief:
    def test_frontmatter_and_sections(self):
        fm = {
            "track": "em-engines",
            "written": "2026-07-17 14:30",
            "last_session": "2026-07-17",
            "last_session_title": "Substation spine",
            "threads_cleared": "0",
            "threads_added": 4,
            "threads_carried": "4",
        }
        b = parse_track_brief(fm, BRIEF_BODY)
        assert isinstance(b, TrackBrief)
        assert b.track == "em-engines"
        assert b.threads_added == 4 and b.threads_carried == 4
        assert "Where things stand" in b.sections
        assert b.sections["Recommended starting move"].startswith("Open ")

    def test_blocked_human_detection(self):
        b = parse_track_brief({}, BRIEF_BODY)
        assert len(b.open_threads) == 2
        assert b.open_threads[0]["blocked_human"] is False
        assert b.open_threads[1]["blocked_human"] is True
        assert b.blocked_human is True

    def test_bad_counts_coerce_to_zero(self):
        b = parse_track_brief({"threads_carried": "n/a"}, "")
        assert b.threads_carried == 0


class TestParseDeferredTable:
    def test_rows_and_struck_detection(self):
        rows = parse_deferred_table(DEFERRED_MD)
        assert len(rows) == 3
        assert rows[0].feature.startswith("Effect ledger")
        assert rows[0].status == "deferred"
        assert rows[2].struck is True and rows[2].status == "built"

    def test_header_and_separator_excluded(self):
        rows = parse_deferred_table(DEFERRED_MD)
        assert all(r.feature.lower() != "feature" for r in rows)

    def test_added_dates(self):
        rows = parse_deferred_table(DEFERRED_MD)
        assert rows[1].added == "2026-05-01"


class TestClassifiers:
    def test_track_boundaries(self):
        d = lambda days: (TODAY - datetime.timedelta(days=days)).isoformat()
        assert classify_track(d(6), False, TODAY) == "fresh"
        assert classify_track(d(7), False, TODAY) == "aging"
        assert classify_track(d(30), False, TODAY) == "aging"
        assert classify_track(d(31), False, TODAY) == "stale"

    def test_blocked_wins(self):
        assert classify_track("2026-07-17", True, TODAY) == "blocked"

    def test_unparseable_date_is_stale(self):
        assert classify_track("soon", False, TODAY) == "stale"

    def test_deferred_aging(self):
        assert classify_deferred("2026-05-01", TODAY) == "aging"
        assert classify_deferred("2026-07-01", TODAY) == "fresh"

    def test_age_days(self):
        assert age_days("2026-07-10", TODAY) == 7
        assert age_days("garbage", TODAY) is None

    def test_parse_date_loose_shapes(self):
        assert parse_date("~2026-07") == datetime.date(2026, 7, 1)
        assert parse_date("") is None


class TestLinking:
    def test_link_key_normalizes_flags(self):
        assert link_key("feature.cad-spacer.enabled") == "cad-spacer"
        assert link_key("CAD Spacer!") == "cad-spacer"

    def test_explicit_track_wins(self):
        items = [
            {"kind": "spec", "key": "totally-unrelated", "track": "em-engines"},
            {"kind": "spec", "key": "em-engines-extra", "track": "other-track"},
        ]
        linked = links_for_track("em-engines", items)
        assert len(linked) == 1
        assert linked[0]["key"] == "totally-unrelated"

    def test_fuzzy_substring_match(self):
        items = [
            {"kind": "flag", "key": "feature.em-engines.enabled"},
            {"kind": "flag", "key": "feature.unrelated.enabled"},
            {"kind": "brief", "key": "em-engines-phase2"},
        ]
        linked = links_for_track("em-engines", items)
        assert {i["key"] for i in linked} == {
            "feature.em-engines.enabled",
            "em-engines-phase2",
        }

    def test_short_keys_do_not_fuzzy_match(self):
        assert links_for_track("kb", [{"kind": "x", "key": "kb-butler"}]) == []


THEMES_TOML = """
# 6 goals, NIW-ordered
[[theme]]
order = 2
id = "personal-os"
purpose = "personal"
name = "Personal OS"
done_enough = "Daily driver."
tracks = ["beta-track", "archived-slug"]
milestones = [
  { text = "hub live", done = true },
]

[[theme]]
order = 1
id = "power-engineering"
purpose = "niw"
name = "Power Engineering"
done_enough = "Calculators conformant."
tracks = ["alpha-track", "em-engines"]
milestones = [
  { text = "cable rating", done = true },
  { text = "earthing", done = true },
  { text = "lightning", done = false },
]
"""


class TestThemes:
    def test_parse_sorted_by_order_with_shipped_totals(self):
        themes = parse_themes(THEMES_TOML)
        assert [t.id for t in themes] == ["power-engineering", "personal-os"]
        pe = themes[0]
        assert pe.purpose == "niw" and pe.shipped == 2 and pe.total == 3
        assert pe.tracks == ["alpha-track", "em-engines"]

    def test_stale_slugs_are_kept_verbatim(self):
        themes = parse_themes(THEMES_TOML)
        assert "archived-slug" in themes[1].tracks  # caller tolerates no live row

    def test_bad_toml_fails_soft(self):
        assert parse_themes("not [ valid toml") == []
        assert parse_themes("") == []

    def test_theme_index_first_wins(self):
        themes = parse_themes(THEMES_TOML)
        idx = theme_index(themes)
        assert idx["alpha-track"] == "power-engineering"
        assert idx["beta-track"] == "personal-os"
        # duplicate declaration: first (lowest-order) theme keeps the slug
        themes[1].tracks.append("alpha-track")
        assert theme_index(themes)["alpha-track"] == "power-engineering"


class TestParseDevlogMeta:
    def test_dated_with_tracks(self):
        m = parse_devlog_meta({"tracks": ["alpha-track", "em-engines"]}, "2026-07-17")
        assert m == {"date": "2026-07-17", "title": "", "tracks": ["alpha-track", "em-engines"]}

    def test_legacy_devlog_without_tracks(self):
        m = parse_devlog_meta({"type": "dev-session"}, "2026-06-27-harness-round3")
        assert m["date"] == "2026-06-27"
        assert m["title"] == "harness round3"
        assert m["tracks"] == []

    def test_tracks_as_string_coerces(self):
        assert parse_devlog_meta({"tracks": "alpha-track"}, "2026-07-01")["tracks"] == ["alpha-track"]

    def test_non_dated_filename_is_none(self):
        assert parse_devlog_meta({}, "_index") is None
        assert parse_devlog_meta({}, "_themes") is None


class TestRemoveIndexRow:
    def test_removes_exactly_one_line(self):
        out = remove_index_row(INDEX_MD, "em-engines")
        assert out is not None
        assert len(out.splitlines()) == len(INDEX_MD.splitlines()) - 1
        assert "[em-engines](em-engines.md)" not in out
        # Other rows and the unrelated prose table survive byte-identical.
        assert "[external-lab](external-lab.md)" in out
        assert "| not | a track |" in out

    def test_unknown_track_returns_none(self):
        assert remove_index_row(INDEX_MD, "no-such-track") is None

    def test_result_still_parses(self):
        out = remove_index_row(INDEX_MD, "old-track")
        assert [r.name for r in parse_track_index(out)] == [
            "external-lab",
            "em-engines",
        ]


class TestSetDeferredStatus:
    def test_touches_only_the_final_cell(self):
        out = set_deferred_status(DEFERRED_MD, "Plain feature row", "building")
        assert out is not None
        before = DEFERRED_MD.splitlines()
        after = out.splitlines()
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert len(changed) == 1
        assert after[changed[0]].rstrip().endswith("| building |")
        assert "| trigger text | ref | src | 2026-05-01 |" in after[changed[0]]

    def test_rejects_unknown_status(self):
        assert set_deferred_status(DEFERRED_MD, "Plain feature row", "wontfix") is None
        assert "wontfix" not in DEFERRED_STATUS_VOCAB

    def test_rejects_unknown_feature(self):
        assert set_deferred_status(DEFERRED_MD, "No such feature", "built") is None

    def test_rejects_struck_row(self):
        assert set_deferred_status(DEFERRED_MD, "Shipped thing", "deferred") is None

    def test_bold_feature_matches_parsed_text(self):
        out = set_deferred_status(
            DEFERRED_MD, "Effect ledger — exactly-once side effects", "triggered"
        )
        assert out is not None
        assert "| triggered |" in out
