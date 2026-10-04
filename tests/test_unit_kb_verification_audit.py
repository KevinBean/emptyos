"""scripts/kb_verification_audit.py — numeric clause/case notes by verification tier.

Runs on a tmp KB + a tmp manifests tree, so nothing depends on the vault. Each
fixture note isolates one exclusion (kind, numeric content, verification key,
engine-backing route), so the listed set differs between a correct scanner and
a broken one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import kb_verification_audit as audit  # noqa: E402

FM = "---\ntags:\n  - kb\nkind: {kind}\ntitle: {slug}\n{extra}---\n"


def _write(kb: Path, folder: str, slug: str, kind: str, body: str, **fm):
    extra = "".join(
        (f"{k}:\n" + "".join(f"  - {x}\n" for x in v)) if isinstance(v, list) else f"{k}: {v}\n"
        for k, v in fm.items()
    )
    (kb / folder).mkdir(parents=True, exist_ok=True)
    (kb / folder / f"{slug}.md").write_text(FM.format(kind=kind, slug=slug, extra=extra) + body, encoding="utf-8")


TABLE = "\n## Substance\n\n| K | β |\n|---|---|\n| 226 | 234.5 |\n"
EQUATION = "\n## Substance\n\nε = √( 1 + F·A·√(t/S) )\n"
FENCE = "\n## Substance\n\n```\nX = F·A\n```\n"
PROSE = "\n## Substance\n\nThis clause defines the scope of the standard.\n"


@pytest.fixture
def corpus(tmp_path):
    kb = tmp_path / "vault" / "30_Resources" / "EmptyOS" / "kb"
    apps = tmp_path / "apps"
    _write(kb, "sources", "backed-impl", "clause", TABLE, implemented_in=["engines/x.py"])
    _write(kb, "sources", "backed-manifest", "clause", EQUATION)
    _write(kb, "sources", "plain-numeric", "clause", FENCE)
    _write(kb, "sources", "verified-pdf", "clause", TABLE, implemented_in=["engines/x.py"],
           verified_against_pdf="2026-09-30")
    _write(kb, "sources", "verified-fulltext", "case", TABLE, verified_against_fulltext="2026-09-14")
    _write(kb, "sources", "prose-only", "clause", PROSE, implemented_in=["engines/x.py"])
    _write(kb, "notes", "concept-with-table", "concept", TABLE, implemented_in=["engines/x.py"])
    _write(kb, "sources", "malformed-date", "clause", TABLE, verified_against_pdf="soon")
    (apps / "public" / "calc").mkdir(parents=True)
    (apps / "public" / "calc" / "manifest.toml").write_text(
        '[app]\nid = "calc"\n\n[[provides.conformance.x]]\ncase_id = "c"\n'
        'inputs_fn = "a"\nexpected_fn = "b"\nreferences = ["[[backed-manifest]]"]\n',
        encoding="utf-8",
    )
    (apps / "_retired" / "old").mkdir(parents=True)
    (apps / "_retired" / "old" / "manifest.toml").write_text(
        '[app]\nid = "old"\nreferences = ["[[plain-numeric]]"]\n', encoding="utf-8")
    return kb, apps


def _scan(corpus):
    kb, apps = corpus
    return audit.scan(kb, apps, audit.load_note_verification())


class TestPopulation:
    def test_only_numeric_clause_case_notes_count(self, corpus):
        r = _scan(corpus)
        # prose-only (no numbers) and the concept (wrong kind) are out
        assert r["population"] == 6
        assert r["by_tier"] == {"transcribed": 4, "pdf": 1, "fulltext": 1}

    def test_malformed_verification_date_is_transcribed(self, corpus):
        assert "malformed-date" in {t["slug"] for t in _scan(corpus)["transcribed"]}

    def test_each_numeric_shape_is_detected(self):
        for body in (TABLE, EQUATION, FENCE):
            assert audit.NUMERIC_RE.search(body), body
        assert not audit.NUMERIC_RE.search(PROSE)


class TestEngineBacked:
    def test_listed_only_when_an_engine_or_manifest_leans_on_it(self, corpus):
        r = _scan(corpus)
        listed = {t["slug"]: t for t in r["engine_backed_transcribed"]}
        assert set(listed) == {"backed-impl", "backed-manifest"}
        assert listed["backed-impl"]["reasons"] == ["implemented_in: engines/x.py"]
        assert listed["backed-manifest"]["reasons"] == ["manifest references: apps/public/calc/manifest.toml"]
        assert listed["backed-impl"]["path"] == "sources/backed-impl.md"

    def test_verified_engine_backed_note_is_not_listed(self, corpus):
        assert "verified-pdf" not in {t["slug"] for t in _scan(corpus)["engine_backed_transcribed"]}

    def test_retired_manifests_are_ignored(self, corpus):
        r = _scan(corpus)
        assert "plain-numeric" not in {t["slug"] for t in r["engine_backed_transcribed"]}
        assert r["manifest_referenced_slugs"] == 1


class TestCli:
    def test_text_output_counts_and_lists(self, corpus, capsys):
        kb, apps = corpus
        assert audit.main(["--kb-root", str(kb), "--manifests-root", str(apps)]) == 0
        out = capsys.readouterr().out
        assert "6 numeric clause/case notes" in out and "transcribed 4" in out
        assert "- backed-impl" in out and "- plain-numeric" not in out

    def test_all_lists_every_transcribed_note(self, corpus, capsys):
        kb, apps = corpus
        audit.main(["--kb-root", str(kb), "--manifests-root", str(apps), "--all"])
        assert "- plain-numeric" in capsys.readouterr().out

    def test_sample_is_reproducible_and_bounded(self, corpus, capsys):
        kb, apps = corpus
        args = ["--kb-root", str(kb), "--manifests-root", str(apps), "--sample", "1", "--seed", "7"]
        audit.main(args); first = capsys.readouterr().out
        audit.main(args); second = capsys.readouterr().out
        assert first == second and "1 note(s) for eos-citation-verify" in first

    def test_json_envelope(self, corpus, capsys):
        kb, apps = corpus
        assert audit.main(["--kb-root", str(kb), "--manifests-root", str(apps), "--json"]) == 0
        env = json.loads(capsys.readouterr().out)
        assert env["ok"] is True and env["data"]["population"] == 6
        assert {t["slug"] for t in env["data"]["listed"]} == {"backed-impl", "backed-manifest"}

    def test_missing_kb_root_is_advisory_not_a_failure(self, tmp_path, capsys):
        assert audit.main(["--kb-root", str(tmp_path / "nope"), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
