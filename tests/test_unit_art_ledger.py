"""Unit tests for emptyos/sdk/art_ledger.py — the MV pipeline's cross-song
memory. Pure file I/O, no daemon, no kernel."""

import json

import pytest

from emptyos.sdk.art_ledger import (
    log_art_verdict,
    read_rows,
    recurring_failures,
    recurring_failures_block,
    song_key,
)


def _log(root, song, code, *, scene=1, stage="art-review",
         verdict="regenerate", clause=""):
    log_art_verdict(root, song, scene, stage=stage, verdict=verdict,
                    hard_codes=[code], contract_clause=clause)


class TestLogging:
    def test_row_round_trips(self, tmp_path):
        _log(tmp_path, "song-a", "subject_scale_mismatch",
             clause="faces are never in close-up")
        rows = read_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["song"] == "song-a"
        assert rows[0]["codes"] == ["subject_scale_mismatch"]
        assert rows[0]["contract_clause"] == "faces are never in close-up"
        assert rows[0]["ts"]

    def test_never_raises_on_bad_root(self):
        # A ledger write must never affect a render — the whole point of the
        # best-effort posture.
        log_art_verdict("\x00/nonexistent", "s", 1, stage="art-review",
                        verdict="pass")

    def test_missing_ledger_reads_empty(self, tmp_path):
        assert read_rows(tmp_path) == []

    def test_corrupt_line_does_not_blind_the_ledger(self, tmp_path):
        _log(tmp_path, "song-a", "style_mismatch")
        led = tmp_path / "data" / "art_direction" / "ledger.jsonl"
        led.write_text(led.read_text(encoding="utf-8") + "not json\n",
                       encoding="utf-8")
        _log(tmp_path, "song-b", "style_mismatch")
        assert len(read_rows(tmp_path)) == 2


class TestRecurringFailures:
    def test_one_song_is_not_recurring(self, tmp_path):
        # Eight failures on one song is one lesson about that song. Seeding it
        # into an unrelated song would import a bias that song never earned.
        for i in range(8):
            _log(tmp_path, "song-a", "palette_mismatch", scene=i + 1)
        assert recurring_failures(tmp_path) == []

    def test_two_different_songs_graduate(self, tmp_path):
        _log(tmp_path, "song-a", "palette_mismatch")
        _log(tmp_path, "song-b", "palette_mismatch")
        out = recurring_failures(tmp_path)
        assert [r["code"] for r in out] == ["palette_mismatch"]
        assert out[0]["songs"] == 2

    def test_passes_are_ignored(self, tmp_path):
        _log(tmp_path, "song-a", "style_mismatch", verdict="pass")
        _log(tmp_path, "song-b", "style_mismatch", verdict="pass")
        assert recurring_failures(tmp_path) == []

    def test_exclude_song_drops_the_run_under_review(self, tmp_path):
        # A song must never be graded against its own in-progress mistakes.
        _log(tmp_path, "song-a", "occupancy_mismatch")
        _log(tmp_path, "song-b", "occupancy_mismatch")
        assert recurring_failures(tmp_path, exclude_song="song-b") == []
        assert recurring_failures(tmp_path, exclude_song="other")

    def test_sorted_by_breadth_then_code(self, tmp_path):
        for s in ("a", "b", "c"):
            _log(tmp_path, s, "wide_code")
        for s in ("a", "b"):
            _log(tmp_path, s, "narrow_code")
        out = recurring_failures(tmp_path)
        assert [r["code"] for r in out] == ["wide_code", "narrow_code"]

    def test_example_clause_is_the_most_common(self, tmp_path):
        _log(tmp_path, "a", "c1", clause="one light source only")
        _log(tmp_path, "b", "c1", clause="one light source only")
        _log(tmp_path, "c", "c1", clause="something else")
        assert recurring_failures(tmp_path)[0]["example_clause"] == (
            "one light source only")

    def test_limit_is_honoured(self, tmp_path):
        for n in range(10):
            for s in ("a", "b"):
                _log(tmp_path, s, f"code_{n}")
        assert len(recurring_failures(tmp_path, limit=3)) == 3


class TestPromptBlock:
    def test_empty_ledger_yields_empty_string(self, tmp_path):
        # Must stay "" so the reviewer prompt is byte-identical to the
        # pre-ledger prompt on a fresh install — and stays prefix-cacheable.
        assert recurring_failures_block(tmp_path) == ""

    def test_single_song_yields_empty_string(self, tmp_path):
        _log(tmp_path, "song-a", "style_mismatch")
        assert recurring_failures_block(tmp_path) == ""

    def test_block_names_code_and_clause(self, tmp_path):
        _log(tmp_path, "a", "subject_scale_mismatch", clause="no close-up faces")
        _log(tmp_path, "b", "subject_scale_mismatch", clause="no close-up faces")
        block = recurring_failures_block(tmp_path)
        assert "subject_scale_mismatch" in block
        assert "no close-up faces" in block
        assert "2 songs" in block

    def test_block_does_not_instruct_rejection(self, tmp_path):
        # Seeded history is a thing to look for, never grounds to reject an
        # image that complies with its own song's contract.
        _log(tmp_path, "a", "palette_mismatch")
        _log(tmp_path, "b", "palette_mismatch")
        assert "NOT as a reason to reject" in recurring_failures_block(tmp_path)


class TestSongIdentity:
    """One song must not clear a two-song bar by wearing two names.

    Measured 2026-07-31: the live ledger carried both `梦幻泡影` and
    `2026-02-23__梦幻泡影`, and `recurring_failures` thresholds on the count of
    *distinct* songs — so a lesson from one song was being seeded into every
    other song's reviewer as though two had independently confirmed it.
    """

    def test_dated_folder_prefix_is_stripped(self):
        assert song_key("2026-02-23__梦幻泡影") == song_key("梦幻泡影")

    def test_case_and_spacing_are_normalized(self):
        assert song_key("Porch-Light-Low") == song_key("porch-light-low")
        assert song_key("  08 · Log  Out ") == song_key("08 · Log Out")

    def test_genuinely_different_songs_stay_distinct(self):
        # The normalizer must not over-merge: these are separate songs and a
        # lesson from one has not been confirmed by the other.
        assert song_key("一念之间") != song_key("梦幻泡影")
        assert song_key("08 · Log Out") != song_key("07 · Pāramitā Protocol")

    def test_blank_is_safe(self):
        assert song_key("") == ""
        assert song_key(None) == ""

    def test_two_names_for_one_song_do_not_reach_the_threshold(self, tmp_path):
        _log(tmp_path, "梦幻泡影", "subject_scale_mismatch")
        _log(tmp_path, "2026-02-23__梦幻泡影", "subject_scale_mismatch")
        assert recurring_failures(tmp_path) == [], (
            "one song under two names is still one song"
        )

    def test_two_real_songs_still_reach_it(self, tmp_path):
        _log(tmp_path, "梦幻泡影", "subject_scale_mismatch")
        _log(tmp_path, "Porch-Light-Low", "subject_scale_mismatch")
        assert [r["code"] for r in recurring_failures(tmp_path)] == [
            "subject_scale_mismatch",
        ]

    def test_exclude_song_matches_across_naming_variants(self, tmp_path):
        # A run must never be graded against its own in-progress mistakes, and
        # that has to hold whichever name the run carries.
        _log(tmp_path, "2026-02-23__梦幻泡影", "style_mismatch")
        _log(tmp_path, "Porch-Light-Low", "style_mismatch")
        assert recurring_failures(tmp_path, exclude_song="梦幻泡影") == []
