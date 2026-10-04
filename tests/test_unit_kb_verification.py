"""KB transcription-verification state (kb-fact-integrity T4).

`note_verification` is pure and derives a clause/case note's tier from dated
keys (`verified_against_pdf` > `verified_against_fulltext` > nothing); the
health rules are exercised through the same `_HealthApp` shim
tests/test_unit_kb_reference_freshness.py uses, with an injected `today`.

Kernel-free: kb/shared.py imports only emptyos.sdk.utils + vault_model.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import types

import pytest

from helpers import app_path

_KB = app_path("kb")
_pkg = types.ModuleType("eos_kb_pkg_ver")
_pkg.__path__ = [str(_KB)]
sys.modules["eos_kb_pkg_ver"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"eos_kb_pkg_ver.{name}", str(_KB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"eos_kb_pkg_ver.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


shared = _load("shared")
notes_mod = _load("notes")
note_verification = shared.note_verification

TODAY = dt.date(2026, 9, 30)


def _note(kind, slug="n", sections=(), **props):
    return {
        "path": f"30_Resources/EmptyOS/kb/sources/{slug}.md",
        "name": slug,
        "sections": list(sections),
        "properties": {"kind": kind, "title": slug, **props},
    }


# ── the pure classifier ───────────────────────────────────────────────


class TestNoteVerification:
    def test_clause_without_keys_is_transcribed(self):
        v = note_verification({"kind": "clause"})
        assert v == {"tier": "transcribed", "verified_at": "", "stale": False, "receipt": False}

    def test_pdf_key_wins_over_fulltext(self):
        v = note_verification({"kind": "clause", "verified_against_pdf": "2026-09-30",
                               "verified_against_fulltext": "2026-09-14"})
        assert (v["tier"], v["verified_at"]) == ("pdf", "2026-09-30")

    def test_fulltext_key_alone_is_fulltext(self):
        v = note_verification({"kind": "case", "verified_against_fulltext": "2026-09-14"})
        assert (v["tier"], v["verified_at"]) == ("fulltext", "2026-09-14")

    def test_malformed_date_is_not_a_verification(self):
        # `verified_against_pdf: soon` is a wish, not a record of a page read.
        assert note_verification({"kind": "clause", "verified_against_pdf": "soon"})["tier"] == "transcribed"

    def test_non_clause_kinds_do_not_apply(self):
        for kind in ("concept", "formula", "reference", "lesson", "moc"):
            assert note_verification({"kind": kind, "verified_against_pdf": "2026-09-30"})["tier"] == ""
        assert note_verification(None)["tier"] == ""

    def test_edited_after_verification_is_stale(self):
        base = {"kind": "clause", "verified_against_pdf": "2026-09-30"}
        assert note_verification({**base, "updated": "2026-10-02"})["stale"] is True
        assert note_verification({**base, "updated": "2026-09-30"})["stale"] is False
        assert note_verification({**base, "updated": "2026-09-01"})["stale"] is False
        assert note_verification({**base})["stale"] is False
        assert note_verification({"kind": "clause", "updated": "2026-10-02"})["stale"] is False

    def test_receipt_is_the_verification_section(self):
        assert note_verification({"kind": "clause"}, ["Substance", "Verification"])["receipt"] is True
        assert note_verification({"kind": "clause"}, ["substance", " verification "])["receipt"] is True
        # the 2026-09-14 pass dated its headings — those receipts are real
        assert note_verification({"kind": "case"}, ["Verification (2026-09-14)"])["receipt"] is True
        assert note_verification({"kind": "clause"}, ["Substance"])["receipt"] is False
        assert note_verification({"kind": "clause"}, ["Why verification matters"])["receipt"] is False
        assert note_verification({"kind": "clause"}, None)["receipt"] is False


# ── surfaced on the note API ──────────────────────────────────────────


class _HealthApp:
    kernel = types.SimpleNamespace(config=types.SimpleNamespace(path=str(_KB.parents[3] / "emptyos.toml")))

    def _supersession_enabled(self):
        return False

    def _summarize(self, note):
        return notes_mod._summarize(self, note)

    @staticmethod
    def _parse_impl_ref(ref):
        return "path", str(ref).split("::")[0], ""

    @staticmethod
    def setting_or_config(_key, default=None):
        return default


def _findings(notes, category):
    return {
        slug: record
        for cat, slug, record in notes_mod._iter_health_findings(_HealthApp(), notes, {}, today=TODAY)
        if cat == category
    }


class TestSurface:
    def test_summary_row_carries_verification(self):
        row = notes_mod._summarize(_HealthApp(), _note("clause", verified_against_pdf="2026-09-30"))
        assert row["verification"]["tier"] == "pdf"
        assert notes_mod._summarize(_HealthApp(), _note("concept"))["verification"]["tier"] == ""

    def test_buckets_are_declared_with_severity(self):
        for key in ("engine_backed_transcribed", "verification_stale", "verification_without_receipt"):
            assert notes_mod._HEALTH_SEVERITY[key] == "warn"


class TestHealthRules:
    def test_engine_backed_transcribed_fires_only_with_implemented_in(self):
        # engines/provenance.py exists in the repo, so the path resolves and
        # the broken_implemented_in rule stays quiet.
        backed = _note("clause", "backed", implemented_in=["engines/provenance.py"])
        unlinked = _note("clause", "unlinked")
        verified = _note("clause", "verified", implemented_in=["engines/provenance.py"],
                         verified_against_pdf="2026-09-30", sections=["Verification"])
        concept = _note("concept", "concept", implemented_in=["engines/provenance.py"])
        found = _findings([backed, unlinked, verified, concept], "engine_backed_transcribed")
        assert set(found) == {"backed"}
        assert found["backed"]["implemented_in"] == ["engines/provenance.py"]

    def test_verification_stale_fires_when_edited_after_the_date(self):
        stale = _note("case", "stale", verified_against_pdf="2026-09-01", updated="2026-09-20",
                      sections=["Verification"])
        fresh = _note("case", "fresh", verified_against_pdf="2026-09-20", updated="2026-09-20",
                      sections=["Verification"])
        found = _findings([stale, fresh], "verification_stale")
        assert set(found) == {"stale"}
        assert found["stale"]["verified_at"] == "2026-09-01"

    def test_verified_claim_without_receipt_is_flagged(self):
        no_receipt = _note("clause", "nr", verified_against_fulltext="2026-09-14")
        with_receipt = _note("clause", "wr", verified_against_fulltext="2026-09-14", sections=["Verification"])
        transcribed = _note("clause", "tr")
        assert set(_findings([no_receipt, with_receipt, transcribed], "verification_without_receipt")) == {"nr"}

    def test_health_bucket_names_match_severity_map(self):
        """health() must return every declared bucket; a bucket the modal never
        shows is a finding nobody sees."""
        import inspect

        src = inspect.getsource(notes_mod.health)
        for key in notes_mod._HEALTH_SEVERITY:
            assert f'"{key}"' in src, key
        assert '"unverified_clause"' in src


# ── the digest and the atomizer can never mark a note verified ────────


class TestProducersNeverVerify:
    def test_atomizer_output_carries_no_verification_key(self):
        from test_unit_standard_atomize import ARCHIVE

        from emptyos.sdk.standard_atomize import plan_atomization

        specs = plan_atomization(
            ARCHIVE, reference_slug="cdim", reference_title="CDIM", standard_id="D2020/03048",
            edition="Rev 1.0", domain="electrical-engineering", source_file="x.txt",
            existing_clauses=[], today="2026-09-30",
            extra_frontmatter={"voltage_levels": ["132 kV"]},
        )
        assert specs
        for spec in specs:
            bad = [k for k in spec.frontmatter if k.startswith("verified") or k == "source_pages"]
            assert not bad, (spec.slug, bad)

    def test_shared_key_list_is_the_two_dated_keys(self):
        assert shared.VERIFICATION_SOURCE_KEYS == ("verified_against_pdf", "verified_against_fulltext")
        assert shared.VERIFIED_KINDS == frozenset({"clause", "case"})
