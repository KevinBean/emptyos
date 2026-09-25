"""Unit tests for emptyos/sdk/art_ledger.py — the MV pipeline's cross-song
memory. Pure file I/O, no daemon, no kernel."""

from emptyos.sdk.art_ledger import (
    log_art_verdict,
    read_rows,
    recurring_failures,
    recurring_failures_block,
    song_key,
    song_keys,
)

SHA = "ab" * 32


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


class TestMangledSongName:
    """`2026-02-23__????` is `2026-02-23__梦幻泡影` written through a cp1252
    console (live ledger, row of 2026-07-29). Repaired at read time only."""

    def test_question_marks_resolve_to_the_dated_song_with_that_length(self, tmp_path):
        _log(tmp_path, "2026-02-23__梦幻泡影", "c1")
        _log(tmp_path, "2026-02-23__????", "c1")
        _log(tmp_path, "Porch-Light-Low", "c1")
        out = recurring_failures(tmp_path)
        assert [r["code"] for r in out] == ["c1"]
        assert out[0]["songs"] == 2, "a mangled name is not a second song"

    def test_evidence_names_the_repaired_identity_and_keeps_the_raw_name(self, tmp_path):
        _log(tmp_path, "2026-02-23__梦幻泡影", "c1", scene=10)
        _log(tmp_path, "2026-02-23__????", "c1", scene=13)
        _log(tmp_path, "Porch-Light-Low", "c1", scene=2)
        ev = recurring_failures(tmp_path)[0]["evidence"]
        assert {e["song_identity"] for e in ev} == {song_key("梦幻泡影"), song_key("Porch-Light-Low")}
        assert {e["song"] for e in ev} == {"2026-02-23__梦幻泡影", "2026-02-23__????", "Porch-Light-Low"}

    def test_excluding_an_unrelated_same_date_song_keeps_the_repair(self, tmp_path):
        _log(tmp_path, "2026-02-23__梦幻泡影", "c1")
        _log(tmp_path, "2026-02-23__????", "c1")
        _log(tmp_path, "Porch-Light-Low", "c1")
        out = recurring_failures(tmp_path, exclude_song="2026-02-23__一念之间")
        assert [r["code"] for r in out] == ["c1"] and out[0]["songs"] == 2

    def test_length_picks_between_songs_sharing_a_date(self):
        rows = [{"song": "2026-02-23__梦幻泡影"}, {"song": "2026-02-23__无所住"},
                {"song": "2026-02-23__???"}, {"song": "2026-02-23__????"}]
        keys = song_keys(rows)
        assert keys["2026-02-23__???"] == song_key("无所住")
        assert keys["2026-02-23__????"] == song_key("梦幻泡影")

    def test_ambiguous_or_unmatched_stays_its_own_song(self):
        rows = [{"song": "2026-02-23__梦幻泡影"}, {"song": "2026-02-23__一念之间"},
                {"song": "2026-02-23__????"}, {"song": "2026-03-01__??"}]
        keys = song_keys(rows)
        assert keys["2026-02-23__????"] == song_key("2026-02-23__????")
        assert keys["2026-03-01__??"] == song_key("2026-03-01__??")

    def test_an_undated_question_mark_name_is_left_alone(self):
        rows = [{"song": "2026-02-23__梦幻泡影"}, {"song": "????"}]
        assert song_keys(rows)["????"] == "????"

    def test_exclude_song_covers_the_mangled_row(self, tmp_path):
        _log(tmp_path, "2026-02-23__????", "style_mismatch")
        _log(tmp_path, "2026-02-23__梦幻泡影", "palette_mismatch")
        _log(tmp_path, "Porch-Light-Low", "style_mismatch")
        assert recurring_failures(tmp_path), "control: without the exclude the code recurs"
        assert recurring_failures(tmp_path, exclude_song="梦幻泡影") == []

    def test_a_mangled_exclude_name_excludes_the_song_it_stands_for(self, tmp_path):
        _log(tmp_path, "2026-02-23__梦幻泡影", "c1")
        _log(tmp_path, "Porch-Light-Low", "c1")
        _log(tmp_path, "潮痕", "c1")
        assert recurring_failures(tmp_path)[0]["songs"] == 3, "control"
        out = recurring_failures(tmp_path, exclude_song="2026-02-23__????")
        assert out[0]["songs"] == 2
        assert all(e["song_identity"] != song_key("梦幻泡影") for e in out[0]["evidence"])

    def test_reading_never_rewrites_the_stored_row(self, tmp_path):
        _log(tmp_path, "2026-02-23__梦幻泡影", "style_mismatch")
        _log(tmp_path, "2026-02-23__????", "style_mismatch")
        _log(tmp_path, "Porch-Light-Low", "style_mismatch")
        led = tmp_path / "data" / "art_direction" / "ledger.jsonl"
        before = led.read_bytes()
        assert recurring_failures(tmp_path)[0]["songs"] == 2
        assert led.read_bytes() == before
        assert read_rows(tmp_path)[1]["song"] == "2026-02-23__????"

    def test_attempt_id_is_capped(self, tmp_path):
        log_art_verdict(tmp_path, "a", 1, stage="human", verdict="reject",
                        hard_codes=["c"], attempt_id="x" * 500)
        assert len(read_rows(tmp_path)[0]["attempt_id"]) == 200


class TestEvidenceLinks:
    def test_attempt_id_and_sha_round_trip(self, tmp_path):
        log_art_verdict(tmp_path, "a", 1, stage="human", verdict="reject",
                        hard_codes=["style_mismatch"], attempt_id="a:still:S01:2",
                        output_sha256=SHA.upper())
        row = read_rows(tmp_path)[0]
        assert row["attempt_id"] == "a:still:S01:2"
        assert row["output_sha256"] == SHA

    def test_absent_links_are_not_written(self, tmp_path):
        _log(tmp_path, "a", "style_mismatch")
        assert "attempt_id" not in read_rows(tmp_path)[0]
        assert "output_sha256" not in read_rows(tmp_path)[0]

    def test_a_malformed_sha_is_not_written(self, tmp_path):
        log_art_verdict(tmp_path, "a", 1, stage="human", verdict="reject",
                        hard_codes=["c"], output_sha256="abc123")
        assert "output_sha256" not in read_rows(tmp_path)[0]

    def test_recurring_failures_points_at_the_rejections(self, tmp_path):
        log_art_verdict(tmp_path, "song-a", 3, stage="art-review", verdict="regenerate",
                        hard_codes=["palette_mismatch"], attempt_id="song-a:still:S03:1",
                        output_sha256=SHA)
        _log(tmp_path, "song-b", "palette_mismatch", scene=7)
        ev = recurring_failures(tmp_path)[0]["evidence"]
        assert {e["song"] for e in ev} == {"song-a", "song-b"}
        assert {e["song_identity"] for e in ev} == {"song-a", "song-b"}
        linked = [e for e in ev if e["song"] == "song-a"][0]
        assert linked["attempt_id"] == "song-a:still:S03:1"
        assert linked["output_sha256"] == SHA
        assert linked["scene"] == 3
        unlinked = [e for e in ev if e["song"] == "song-b"][0]
        assert "attempt_id" not in unlinked and unlinked["scene"] == 7

    def test_evidence_is_newest_first_and_capped(self, tmp_path):
        for i, s in enumerate(("a", "b", "c", "d")):
            _log(tmp_path, s, "wide_code", scene=i)
        ev = recurring_failures(tmp_path, evidence_limit=2)[0]["evidence"]
        assert [e["scene"] for e in ev] == [3, 2]

    def test_passes_are_not_evidence(self, tmp_path):
        _log(tmp_path, "a", "c1")
        _log(tmp_path, "b", "c1")
        log_art_verdict(tmp_path, "c", 9, stage="art-review", verdict="pass",
                        hard_codes=["c1"], attempt_id="c:still:S09:1")
        assert all(e.get("attempt_id") != "c:still:S09:1"
                   for e in recurring_failures(tmp_path)[0]["evidence"])

    def test_prompt_block_is_unchanged_by_links(self, tmp_path):
        # The reviewer prompt must stay byte-identical whether or not rows carry links.
        _log(tmp_path, "a", "palette_mismatch", clause="no teal")
        _log(tmp_path, "b", "palette_mismatch", clause="no teal")
        plain = recurring_failures_block(tmp_path)
        assert plain, "control: two songs make a block"
        log_art_verdict(tmp_path / "x", "a", 1, stage="art-review", verdict="regenerate",
                        hard_codes=["palette_mismatch"], contract_clause="no teal",
                        attempt_id="a:still:S01:1", output_sha256=SHA)
        log_art_verdict(tmp_path / "x", "b", 1, stage="art-review", verdict="regenerate",
                        hard_codes=["palette_mismatch"], contract_clause="no teal",
                        attempt_id="b:still:S01:1")
        assert recurring_failures_block(tmp_path / "x") == plain
