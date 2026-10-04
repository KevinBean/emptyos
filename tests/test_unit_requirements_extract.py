"""Unit tests — requirements extraction helpers + KB registered-document citations.

Pure functions only (no daemon): the MoSCoW inference, evidence coercion, clause
chunking, the KB alias citation grammar, and the project-first lookup ladder.
"""

from types import SimpleNamespace

import pytest
from helpers import load_app_module

req = load_app_module("requirements", "app")
kb_shared = load_app_module("kb", "shared")
kb_indexes = load_app_module("kb", "indexes", preload=("shared",))


class TestInferPriority:
    @pytest.mark.parametrize("statement, expected", [
        ("The Contractor shall submit the design.", "must"),
        ("Trenches must be backfilled.", "must"),
        ("Joints are not permitted below the water table.", "must"),
        ("The Supplier should provide type-test reports.", "should"),
        ("The Designer may adopt a higher rating.", "could"),
        ("Definitions apply throughout.", "should"),
    ])
    def test_verb_drives_priority(self, statement, expected):
        assert req._infer_priority(statement) == expected

    def test_shall_beats_should_when_both_present(self):
        # The stronger verb wins: 'shall' is an obligation even if 'should' appears.
        assert req._infer_priority("The report should note that the cable shall be armoured.") == "must"

    @pytest.mark.parametrize("statement", [
        "The Principal will provide access to site.",
        "The Superintendent will review the design within 10 days.",
        "Cables can be installed in ducts or direct buried.",
    ])
    def test_will_and_can_are_not_obligation_verbs(self, statement):
        # ISO/IEC/IEEE 29148 vocabulary: 'will' is a statement of fact or the
        # other party's intent; 'can' is possibility. Neither is `must`.
        assert req._infer_priority(statement) != "must"
        assert req._infer_priority(statement) != "could" or "can" not in statement


class TestNormClauseNo:
    @pytest.mark.parametrize("raw, expected", [
        ("2.1", "2.1"), ("§2.1", "2.1"), ("Clause 2.1", "2.1"), ("cl. 2.1", "2.1"),
        ("2.1.", "2.1"), ("3.4 (a)", "3.4"), ("Section 7", "7"), ("", ""), (None, ""), ("General", ""),
    ])
    def test_model_labels_normalise(self, raw, expected):
        assert req._norm_clause_no(raw) == expected


class TestCoerceEvidence:
    def test_string_split_on_newlines_only(self):
        # One pointer per line — a URL may carry commas, so a comma never splits.
        out = req._coerce_evidence("10_Projects/p/assets/a.pdf\nhttps://x.example/r?a=1,2\n10_Projects/p/docs/b.md")
        assert out == ["10_Projects/p/assets/a.pdf", "https://x.example/r?a=1,2", "10_Projects/p/docs/b.md"]

    def test_list_passthrough_dedupes_and_strips(self):
        assert req._coerce_evidence([" a.md ", "a.md", "", "b.md"]) == ["a.md", "b.md"]

    def test_none_blank_and_dict_are_empty(self):
        assert req._coerce_evidence(None) == []
        assert req._coerce_evidence("  \n  ") == []
        assert req._coerce_evidence({"a": 1}) == []
        assert req._coerce_evidence(["ok.md", {"a": 1}, None]) == ["ok.md"]


class TestExtractPreviewWithFakeThink:
    """`extract_preview` end to end with `think` and `kb` stubbed — no daemon."""

    CLAUSES = {
        "ok": True, "title": "Client Spec", "standard_id": "CSPEC-9", "standard": "CSPEC-9",
        "edition": "Rev B", "project": "job",
        "clauses": [
            {"slug": "job-cspec-9-rev-b-2-1", "clause": "2.1", "clause_title": "Earthing",
             "body": "The Contractor shall submit the earthing design for review."},
            {"slug": "job-cspec-9-rev-b-2-2", "clause": "2.2", "clause_title": "Trenching",
             "body": "Trenches should be backfilled with sand."},
        ],
    }

    def _app(self, think_reply, *, raise_think=False):
        import asyncio  # noqa: F401 — used by callers via _run

        app = req.RequirementsApp.__new__(req.RequirementsApp)

        async def call_app(app_id, method, **kw):
            assert (app_id, method) == ("kb", "source_clauses")
            return self.CLAUSES if kw.get("reference_slug") == "src" else {"error": "source not found"}

        async def think(prompt, **kw):
            assert kw.get("system") == req.EXTRACT_REQUIREMENTS_SYSTEM
            assert 0.1 <= kw.get("temperature", 0) <= 0.3
            # The chain's 30 s default kills claude-cli on a real chunk (AS 2067,
            # 2026-10-01); the call must carry its own budget, the declared one.
            assert kw.get("timeout_s") == req._EXTRACT_THINK_TIMEOUT_S >= 120
            if raise_think:
                raise RuntimeError("no provider")
            return think_reply

        async def existing():
            return {"job": {"earthing design submitted"}}

        app.call_app = call_app
        app.think = think
        app._existing_titles_by_project = existing
        app.last_provenance = lambda: {"mode": "local", "provider": "ollama"}
        return app

    @staticmethod
    def _run(coro):
        import asyncio
        return asyncio.run(coro)

    def test_rows_trace_priority_and_source_clause(self):
        reply = ('[{"clause": "Clause 2.1", "statement": "The Contractor shall submit the earthing '
                 'design for review.", "title": "Earthing design submitted"},'
                 ' {"clause": "2.2.", "statement": "Trenches should be backfilled with sand.", "title": "Backfill"}]')
        out = self._run(self._app(reply).extract_preview("job", "src"))
        assert "error" not in out
        rows = out["rows"]
        assert [r["clause"] for r in rows] == ["2.1", "2.2"]           # labels normalised
        assert rows[0]["references"] == "CSPEC-9 Rev B §2.1"
        assert rows[0]["source_clause_slug"] == "job-cspec-9-rev-b-2-1"
        assert rows[0]["priority"] == "must" and rows[1]["priority"] == "should"
        assert rows[0]["duplicate"] is True and rows[1]["duplicate"] is False
        assert out["summary"]["skipped"] == 0 and out["provenance"]["provider"] == "ollama"
        assert out["providers"] == ["ollama"]

    def test_dict_reply_and_fenced_prose_are_read(self):
        reply = ('Here you go:\n```json\n{"requirements": [{"clause": "§2.1", "statement": "The Contractor '
                 'shall submit the earthing design for review.", "title": "T"}]}\n```')
        out = self._run(self._app(reply).extract_preview("job", "src"))
        assert len(out["rows"]) == 1 and out["rows"][0]["clause"] == "2.1"

    def test_unknown_clause_and_blank_statement_are_skipped(self):
        reply = ('[{"clause": "9.9", "statement": "Not in the source.", "title": "x"},'
                 ' {"clause": "2.1", "statement": "", "title": "y"}]')
        out = self._run(self._app(reply).extract_preview("job", "src"))
        assert out["rows"] == [] and out["summary"]["skipped"] == 2

    def test_empty_or_non_json_reply_yields_no_rows(self):
        for reply in ("", "Sorry, no obligations here.", None):
            out = self._run(self._app(reply).extract_preview("job", "src"))
            assert "error" not in out and out["rows"] == []

    def test_provider_failure_is_in_band(self):
        out = self._run(self._app("[]", raise_think=True).extract_preview("job", "src"))
        assert "error" in out and "model" in out["error"]

    def test_unknown_source_is_in_band(self):
        out = self._run(self._app("[]").extract_preview("job", "nope"))
        assert out == {"error": "source not found"}


class TestChunkClauses:
    def test_splits_by_size_and_keeps_order(self):
        clauses = [{"clause": str(i), "body": "x" * 1200} for i in range(5)]
        chunks = req.RequirementsApp._chunk_clauses(clauses)
        assert [c["clause"] for ch in chunks for c in ch] == ["0", "1", "2", "3", "4"]
        assert all(sum(len(c["body"]) + 80 for c in ch) <= req._EXTRACT_CHUNK_CHARS for ch in chunks)
        assert len(chunks) >= 2

    def test_single_oversized_clause_is_its_own_chunk(self):
        chunks = req.RequirementsApp._chunk_clauses([{"clause": "1", "body": "y" * 9000}])
        assert len(chunks) == 1 and chunks[0][0]["clause"] == "1"


class TestAliasCitationGrammar:
    ALIASES = {"xyz-spec-001": "XYZ-SPEC-001", "ns 130": "NS 130", "ns130": "NS 130"}

    def test_published_standard_still_parses_without_aliases(self):
        assert kb_shared._parse_citation("IEC 60287-1-1:2023 §5.1.3") == ("IEC 60287-1-1", "2023", "5.1.3")

    def test_client_id_is_unparseable_without_registration(self):
        assert kb_shared._parse_citation("XYZ-SPEC-001 Rev C cl 3.4") is None

    @pytest.mark.parametrize("text, expected", [
        ("XYZ-SPEC-001 Rev C cl 3.4", ("XYZ-SPEC-001", "Rev C", "3.4")),
        ("XYZ-SPEC-001 §3.4", ("XYZ-SPEC-001", None, "3.4")),
        ("xyz-spec-001 clause 3.4.2", ("XYZ-SPEC-001", None, "3.4.2")),
        ("XYZ-SPEC-001", ("XYZ-SPEC-001", None, None)),
        ("NS130 §4.2", ("NS 130", None, "4.2")),
        ("NS 130 (2021) cl. 4.2–4.5", ("NS 130", "2021", "4.2")),
    ])
    def test_registered_id_parses(self, text, expected):
        assert kb_shared._parse_citation(text, self.ALIASES) == expected

    def test_alias_grammar_does_not_match_prose(self):
        assert kb_shared._parse_citation("the spec says cl 3.4", self.ALIASES) is None
        assert kb_shared._parse_citation("XYZ-SPEC-0010 cl 1", self.ALIASES) is None
        # A longer id is a different document, never a prefix match.
        assert kb_shared._parse_citation("XYZ-SPEC-001-A cl 3", self.ALIASES) is None

    @pytest.mark.parametrize("text, expected", [
        ("XYZ-SPEC-001 Revision 3 cl 2", ("XYZ-SPEC-001", "Revision 3", "2")),
        ("XYZ-SPEC-001 v2.1 cl 3", ("XYZ-SPEC-001", "v2.1", "3")),
        ("XYZ-SPEC-001 Rev C §3.4 (a)", ("XYZ-SPEC-001", "Rev C", "3.4")),
    ])
    def test_edition_spellings(self, text, expected):
        assert kb_shared._parse_citation(text, self.ALIASES) == expected

    def test_precompiled_pattern_matches_per_call_compile(self):
        pat = kb_shared._alias_pattern(self.ALIASES)
        for text in ("XYZ-SPEC-001 Rev C cl 3.4", "NS130 §4.2", "IEC 60287 §1"):
            assert kb_shared._parse_citation(text, self.ALIASES, pattern=pat) == \
                kb_shared._parse_citation(text, self.ALIASES)

    def test_ids_with_metachars_unicode_and_spaces_compile(self):
        aliases = {"c++ spec (draft)": "C++ SPEC (DRAFT)", "规范-001": "规范-001", "d2020/03048": "D 2020/03048"}
        assert kb_shared._parse_citation("C++ Spec (draft) cl 2", aliases) == ("C++ SPEC (DRAFT)", None, "2")
        assert kb_shared._parse_citation("规范-001 §3", aliases) == ("规范-001", None, "3")
        assert kb_shared._parse_citation("D2020/03048 cl 1.7", aliases) == ("D 2020/03048", None, "1.7")


class TestLookupLadder:
    def _app(self):
        return SimpleNamespace(
            _ref_index={("IEC 60287", None, None): "iec-60287", ("IEC 60287", "2023", "5.1"): "iec-60287-5-1"},
            _project_ref_index={"job-a": {("XYZ-SPEC-001", "Rev C", "3.4"): "job-a-xyz-3-4",
                                          ("IEC 60287", "2023", "5.1"): "job-a-own-copy"}},
        )

    def test_project_scope_wins_then_global(self):
        app = self._app()
        assert kb_indexes._lookup_ref(app, "IEC 60287", "2023", "5.1", project="job-a") == "job-a-own-copy"
        assert kb_indexes._lookup_ref(app, "IEC 60287", "2023", "5.1") == "iec-60287-5-1"

    def test_client_clause_never_answers_a_global_citation(self):
        app = self._app()
        assert kb_indexes._lookup_ref(app, "XYZ-SPEC-001", "Rev C", "3.4") is None
        assert kb_indexes._lookup_ref(app, "XYZ-SPEC-001", "Rev C", "3.4", project="job-a") == "job-a-xyz-3-4"
        assert kb_indexes._lookup_ref(app, "XYZ-SPEC-001", "Rev C", "3.4", project="job-b") is None

    def test_ladder_falls_back_edition_then_whole_standard(self):
        idx = {("AS 2067", "2016", "5.2"): "as2067-5-2", ("AS 2067", None, None): "as2067"}
        assert kb_indexes._lookup_in(idx, "AS 2067", None, "5.2") == "as2067-5-2"
        assert kb_indexes._lookup_in(idx, "AS 2067", "2016", "9.9") == "as2067"
        assert kb_indexes._lookup_in(idx, "AS 1234", None, None) is None
