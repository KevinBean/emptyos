"""Unit tests for kb-gap-miner's pure mining helpers.

Pure in-process — no daemon, no kernel. Imports the app module directly by
path (apps/ aren't an installable package), exercising the module-level
functions that do the question filtering, signature grouping, coverage
matching, and lifecycle transitions.

Run standalone (root conftest skips suites when :9000 is down):
    python -m pytest tests/test_unit_kb_gap_miner.py --noconftest -v
"""

from __future__ import annotations

import importlib.util
import time

from helpers import app_path

_APP = app_path("kb-gap-miner") / "app.py"
_spec = importlib.util.spec_from_file_location("kb_gap_miner_app", _APP)
gm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gm)


class TestLooksLikeQuestion:
    def test_english_question_mark(self):
        assert gm.looks_like_question("How is cable ampacity derated for soil resistivity?")

    def test_english_interrogative_opener_no_mark(self):
        assert gm.looks_like_question("how do I size an earthing grid for a substation")

    def test_chinese_marker(self):
        assert gm.looks_like_question("电缆载流量是怎么随土壤热阻系数变化的")

    def test_too_short_rejected(self):
        assert not gm.looks_like_question("what is this?")

    def test_statement_rejected(self):
        assert not gm.looks_like_question("the cable rating report is finished and filed away")

    def test_slash_command_rejected(self):
        assert not gm.looks_like_question("/remind 2h check the cable rating report?")

    def test_mention_rejected(self):
        assert not gm.looks_like_question("@claude what do you think about this approach?")

    def test_do_token_rejected(self):
        assert not gm.looks_like_question('what about [DO:task.add({"text":"x"})] this one?')

    def test_giant_paste_rejected(self):
        assert not gm.looks_like_question("why does this fail? " + "x" * 700)


class TestNormalizeQuestion:
    def test_numbers_and_quotes_collapse(self):
        a = gm.normalize_question("How do I derate a 33kV cable at 1.2 m depth?")
        b = gm.normalize_question("How do I derate a 11kV cable at 0.9 m depth?")
        assert a == b
        assert gm.gap_hash(a) == gm.gap_hash(b)

    def test_trailing_punctuation_ignored(self):
        assert gm.normalize_question("what is rho?") == gm.normalize_question("What is rho")

    def test_distinct_questions_stay_distinct(self):
        a = gm.normalize_question("how do I size an earthing grid")
        b = gm.normalize_question("how do I size a cable trench")
        assert gm.gap_hash(a) != gm.gap_hash(b)

    def test_truncates(self):
        assert len(gm.normalize_question("why " * 200)) <= 200


class TestQuestionTerms:
    def test_drops_stopwords_keeps_content(self):
        terms = gm.question_terms("How does the soil resistivity affect cable ampacity?")
        assert "soil" in terms and "resistivity" in terms and "ampacity" in terms
        assert "how" not in terms and "the" not in terms

    def test_cjk_runs_extracted(self):
        terms = gm.question_terms("土壤热阻系数怎么影响载流量")
        assert any("载流量" in t for t in terms)


class TestCoverage:
    CORPUS = [
        {"slug": "iec-60287-soil", "text": "soil thermal resistivity derating cable ampacity per iec 60287"},
        {"slug": "earthing-grid", "text": "earthing grid sizing touch voltage step voltage ieee 80"},
    ]

    def test_covered_question(self):
        terms = gm.question_terms("how does soil resistivity affect cable ampacity")
        slug, ratio, hits = gm.coverage_best(terms, self.CORPUS)
        assert slug == "iec-60287-soil"
        assert gm.is_covered(ratio, hits)

    def test_uncovered_question(self):
        terms = gm.question_terms("what harmonics limits apply to inverter exports")
        slug, ratio, hits = gm.coverage_best(terms, self.CORPUS)
        assert not gm.is_covered(ratio, hits)

    def test_single_incidental_hit_not_covered(self):
        # One shared word ("cable") must not count as coverage.
        terms = gm.question_terms("what cable colours code applies in australia wiring rules")
        slug, ratio, hits = gm.coverage_best(terms, self.CORPUS)
        assert not gm.is_covered(ratio, hits)

    def test_empty_terms(self):
        assert gm.coverage_best(set(), self.CORPUS) == ("", 0.0, 0)


class TestExtractQaPairs:
    ROWS = [
        {"surface": "assistant", "session_id": "s1", "role": "user",
         "text": "How is cable ampacity derated for soil resistivity?", "ts": "2026-06-10T10:00:00+00:00"},
        {"surface": "assistant", "session_id": "s1", "role": "assistant",
         "text": "Per IEC 60287 the external thermal resistance term scales with rho.", "ts": "2026-06-10T10:00:05+00:00"},
        {"surface": "assistant", "session_id": "s1", "role": "user",
         "text": "thanks, sounds right to me overall", "ts": "2026-06-10T10:01:00+00:00"},
        {"surface": "rooms", "session_id": "r1", "role": "user",
         "text": "why does my earthing grid design keep failing touch voltage?", "ts": "2026-06-10T11:00:00+00:00"},
    ]

    def test_pairs_question_with_next_reply(self):
        pairs = gm.extract_qa_pairs(self.ROWS)
        assert len(pairs) == 2
        assert pairs[0]["question"].startswith("How is cable ampacity")
        assert "IEC 60287" in pairs[0]["answer"]

    def test_unanswered_question_has_empty_answer(self):
        pairs = gm.extract_qa_pairs(self.ROWS)
        assert pairs[1]["surface"] == "rooms"
        assert pairs[1]["answer"] == ""

    def test_answer_never_crosses_sessions(self):
        rows = [
            {"surface": "assistant", "session_id": "s1", "role": "user",
             "text": "what derating factor applies for grouped cables in air?", "ts": "t1"},
            {"surface": "assistant", "session_id": "s2", "role": "assistant",
             "text": "unrelated reply from another session", "ts": "t2"},
        ]
        pairs = gm.extract_qa_pairs(rows)
        assert pairs[0]["answer"] == ""


class TestMergeGaps:
    def _grouped(self, ts_epochs, question="how do I derate a cable for depth"):
        sig = gm.normalize_question(question)
        return {gm.gap_hash(sig): {
            "signature": sig, "question": question, "ask_ts": ts_epochs,
            "first_asked": "2026-06-01T00:00:00+00:00", "last_asked": "2026-06-10T00:00:00+00:00",
            "sources": [{"surface": "assistant", "session_id": "s1", "ts": "2026-06-10T00:00:00+00:00"}],
            "answer_sample": "an answer",
        }}

    def test_new_gap_detected_open(self):
        now = time.time()
        merged, new = gm.merge_gaps({}, self._grouped([now - 100, now - 50]), now)
        assert len(new) == 1
        g = merged[new[0]]
        assert g["status"] == "open"
        assert g["ask_count"] == 2
        assert g["score"] == gm.score_gap(2, 2)

    def test_lifecycle_fields_preserved(self):
        now = time.time()
        grouped = self._grouped([now - 100])
        h = next(iter(grouped))
        prev = {h: {"status": "proposed", "triage": "durable-knowledge",
                    "proposed_actions": ["act-123"], "proposed_at": "x",
                    "covered_by": [], "resolved_at": ""}}
        merged, new = gm.merge_gaps(prev, grouped, now)
        assert not new
        assert merged[h]["status"] == "proposed"
        assert merged[h]["triage"] == "durable-knowledge"
        assert merged[h]["proposed_actions"] == ["act-123"]

    def test_stale_unproposed_gap_dropped(self):
        now = time.time()
        prev = {"deadbeef0000": {"status": "open", "proposed_actions": []}}
        merged, _ = gm.merge_gaps(prev, {}, now)
        assert "deadbeef0000" not in merged

    def test_stale_proposed_gap_kept(self):
        now = time.time()
        prev = {"deadbeef0000": {"status": "proposed", "proposed_actions": ["act-1"]}}
        merged, _ = gm.merge_gaps(prev, {}, now)
        assert "deadbeef0000" in merged


class TestApplyCoverage:
    def test_open_becomes_covered(self):
        gap = {"status": "open"}
        t = gm.apply_coverage(gap, "iec-60287-soil", 0.8, 4)
        assert t == "covered" and gap["status"] == "covered"
        assert gap["covered_by"] == ["iec-60287-soil"]

    def test_covered_reverts_to_open(self):
        gap = {"status": "covered", "covered_by": ["x"]}
        t = gm.apply_coverage(gap, "", 0.0, 0)
        assert t == "uncovered" and gap["status"] == "open"
        assert gap["covered_by"] == []

    def test_proposed_resolves_when_covered(self):
        gap = {"status": "proposed"}
        t = gm.apply_coverage(gap, "new-note", 0.9, 5)
        assert t == "resolved" and gap["status"] == "resolved"
        assert gap["resolved_at"]

    def test_resolved_reopens_on_reask_while_uncovered(self):
        gap = {"status": "resolved", "resolved_at": "2026-06-01T00:00:00+00:00",
               "last_ts": gm.iso_to_epoch("2026-06-10T00:00:00+00:00")}
        t = gm.apply_coverage(gap, "", 0.0, 0)
        assert t == "reopened" and gap["status"] == "reopened"

    def test_resolved_stays_without_reask(self):
        gap = {"status": "resolved", "resolved_at": "2026-06-10T00:00:00+00:00",
               "last_ts": gm.iso_to_epoch("2026-06-01T00:00:00+00:00")}
        t = gm.apply_coverage(gap, "", 0.0, 0)
        assert t == "" and gap["status"] == "resolved"

    def test_below_floor_hits_not_covered(self):
        gap = {"status": "open"}
        t = gm.apply_coverage(gap, "x", 1.0, 1)  # ratio fine, hits floor fails
        assert t == "" and gap["status"] == "open"


class TestEligibleForProposal:
    def test_open_durable_unproposed(self):
        assert gm.eligible_for_proposal(
            {"status": "open", "triage": "durable-knowledge", "proposed_actions": []})

    def test_open_durable_already_proposed_blocked(self):
        assert not gm.eligible_for_proposal(
            {"status": "open", "triage": "durable-knowledge", "proposed_actions": ["act-1"]})

    def test_reopened_durable_reproposes_despite_prior_actions(self):
        assert gm.eligible_for_proposal(
            {"status": "reopened", "triage": "durable-knowledge", "proposed_actions": ["act-1"]})

    def test_transient_never_proposed(self):
        assert not gm.eligible_for_proposal(
            {"status": "open", "triage": "transient", "proposed_actions": []})

    def test_untriaged_never_proposed(self):
        assert not gm.eligible_for_proposal({"status": "open", "proposed_actions": []})

    def test_covered_and_dismissed_blocked(self):
        for status in ("covered", "proposed", "resolved", "dismissed"):
            assert not gm.eligible_for_proposal(
                {"status": status, "triage": "durable-knowledge", "proposed_actions": []}
            ), f"{status} must not auto-propose"


class TestIsoToEpoch:
    def test_parses_iso(self):
        assert gm.iso_to_epoch("2026-06-10T00:00:00+00:00") > 0

    def test_z_suffix(self):
        assert gm.iso_to_epoch("2026-06-10T00:00:00Z") == gm.iso_to_epoch("2026-06-10T00:00:00+00:00")

    def test_garbage_is_zero(self):
        assert gm.iso_to_epoch("not a date") == 0.0
        assert gm.iso_to_epoch("") == 0.0


class TestDeepResolve:
    """The deep-loop research path (consumer #2 of emptyos.sdk.deep_loop).
    Flag on → propose from a researched cited answer; off → byte-identical
    legacy answer_sample path. Stubs all I/O so it runs offline."""

    def _app(self, *, flag, research):
        import asyncio

        app = gm.KBGapMinerApp.__new__(gm.KBGapMinerApp)
        captured = {}
        gaps = {"h1": {"question": "What is selective coordination?",
                       "ask_count": 3, "answer_sample": "thin chat answer",
                       "sources": [{"surface": "rooms", "session_id": "s9"}]}}

        app._load_gaps = lambda: gaps
        app._save_gaps = lambda d: None
        app.app_config = lambda key, default=None: (flag if key == "feature.deep-resolve.enabled" else default)

        async def fake_research(question):
            captured["research_q"] = question
            return research

        async def fake_propose(summary, *, source_ref, content_label, max_candidates, author):
            captured.update(summary=summary, source_ref=source_ref,
                            content_label=content_label, author=author)
            return [{"id": "act-1"}]

        async def fake_emit(*a, **k):
            return None

        app._research_gap = fake_research
        app.propose_kb_extractions = fake_propose
        app.emit = fake_emit
        return app, captured, asyncio

    def test_flag_off_uses_legacy_answer_sample(self):
        app, cap, asyncio = self._app(flag=False, research=None)
        actions = asyncio.run(app._propose_gap("h1"))
        assert actions == [{"id": "act-1"}]
        assert "research_q" not in cap                       # _research_gap never called
        assert cap["content_label"] == "Q&A exchange"
        assert "The assistant's answer at the time:\nthin chat answer" in cap["summary"]
        assert cap["source_ref"].startswith("rooms session s9")

    def test_flag_on_proposes_from_researched_answer(self):
        research = {"answer": "Selective coordination ensures only the nearest "
                              "protective device trips [1].", "rounds": 4,
                    "sources": [{"title": "IEEE 242 overview", "url": "https://x/1"},
                                {"title": "Coordination basics", "url": "https://y/2"}],
                    "followups": [{"question": "fuse vs breaker curves?", "sources": 2}]}
        app, cap, asyncio = self._app(flag=True, research=research)
        actions = asyncio.run(app._propose_gap("h1"))
        assert actions == [{"id": "act-1"}]
        assert cap["research_q"] == "What is selective coordination?"
        assert cap["content_label"] == "researched Q&A"
        assert "Researched answer (open web, 4 round(s))" in cap["summary"]
        assert "only the nearest" in cap["summary"]
        assert "[1] IEEE 242 overview — https://x/1" in cap["summary"]
        assert cap["source_ref"] == "web research (2 sources, mined by kb-gap-miner)"

    def test_flag_on_but_research_empty_falls_back_to_legacy(self):
        # research returns None (e.g. all sources paywalled) → legacy path, not a crash
        app, cap, asyncio = self._app(flag=True, research=None)
        actions = asyncio.run(app._propose_gap("h1"))
        assert actions == [{"id": "act-1"}]
        assert cap["content_label"] == "Q&A exchange"        # graceful degrade to stub path
        assert "thin chat answer" in cap["summary"]
