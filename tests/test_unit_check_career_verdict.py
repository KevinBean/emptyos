"""Pins check_career_verdict's term narrowing in BOTH directions.

The first run of this gate reported 343 matches for "Power Control Engineers",
334 of them false — `power` and `engineers` are stopwords, which left `control`
as a lone "distinctive word" that then substring-matched nearly every career
note. A scanner at zero on a healthy tree has proved nothing until you have
watched it go red for each failure shape it claims to cover
(`.claude/rules/audits.md`), so these tests pin the quiet direction *and* the
loud one.

Pure stdlib + pytest. No vault, no daemon, no network.
"""
from __future__ import annotations

import importlib.util
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_career_verdict", REPO_ROOT / "scripts" / "check_career_verdict.py"
)
ccv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccv)


# ── terms: the narrowing that separates signal from noise ──────────────────

def test_multiword_name_does_not_emit_a_bare_stopword_survivor():
    """The regression that produced 334 false positives.

    "Power Control Engineers" must NOT yield "control" as a search term just
    because its siblings are stopwords.
    """
    terms = ccv.candidate_terms("Power Control Engineers")
    assert "control" not in terms
    assert "power" not in terms
    assert "engineers" not in terms
    assert "power control engineers" in terms
    assert "pce" in terms          # acronym is how the tracker also writes it


def test_single_word_company_is_its_own_term():
    for name in ("Acme", "Globex", "Initech", "Umbrella"):
        assert name.lower() in ccv.candidate_terms(name), name


def test_single_word_stopword_is_not_a_term():
    """'Energy' alone must not become a search term."""
    assert ccv.candidate_terms("Energy") == []


def test_domain_yields_stem():
    terms = ccv.candidate_terms("pceng.com.au")
    assert "pceng" in terms


def test_url_is_stripped():
    terms = ccv.candidate_terms("https://www.fyfe.com.au/")
    assert "fyfe" in terms


def test_person_name_is_searchable():
    assert "chelsea baigent" in ccv.candidate_terms("Chelsea Baigent")


# ── scan: matching + recency ───────────────────────────────────────────────

@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    career = tmp_path / ccv.CAREER_DIR
    career.mkdir(parents=True)
    return tmp_path


def test_scan_finds_a_tracker_row(vault: Path):
    (vault / ccv.CAREER_DIR / "Job-Application-Tracker.md").write_text(
        "| 35 | **Aug 21, 2026** | **Power Control Engineers** (Chelsea) | chat |\n",
        encoding="utf-8",
    )
    hits = ccv.scan(vault, ["power control engineers"], wide=False, recent_days=7)
    assert len(hits) == 1
    assert hits[0]["file"].endswith("Job-Application-Tracker.md")
    assert hits[0]["latest_date"] == "2026-08-21"


def test_scan_is_quiet_on_an_unrelated_vault(vault: Path):
    (vault / ccv.CAREER_DIR / "notes.md").write_text(
        "Reviewed the control system and the power engineers roster.\n",
        encoding="utf-8",
    )
    assert ccv.scan(vault, ccv.candidate_terms("Power Control Engineers"),
                    wide=False, recent_days=7) == []


def test_recent_flag_marks_a_parallel_session(vault: Path):
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=90)).isoformat()
    (vault / ccv.CAREER_DIR / "t.md").write_text(
        f"Fyfe inbound {today}\nFyfe older note {old}\n", encoding="utf-8"
    )
    hits = ccv.scan(vault, ["fyfe"], wide=False, recent_days=7)
    assert [h["recent"] for h in hits] == [True, False]


def test_acronym_needs_a_word_boundary(vault: Path):
    """'pce' must not match inside 'spceng' or 'apce'."""
    (vault / ccv.CAREER_DIR / "t.md").write_text(
        "spceng apce\nPCE row\n", encoding="utf-8"
    )
    hits = ccv.scan(vault, ["pce"], wide=False, recent_days=7)
    assert len(hits) == 1
    assert hits[0]["line"] == 2


# ── location: the hard geographic filter ───────────────────────────────────

_GEO_MEMORY = """---
name: Job search — Sydney only
---
The acceptable corridor is **North Shore + CBD**: North Sydney, Chatswood,
St Leonards, Macquarie Park, and the CBD.

**Explicitly out of scope:**
- Adelaide (regardless of company quality or salary)
- **Western Sydney** — Seven Hills, Huntingwood, Parramatta, Blacktown and similar.
- **Southern Sydney** and any other off-corridor suburb
- Any role that would require relocation away from Sydney
"""


def test_excluded_places_extracts_the_suburb_run():
    """The 2026-08-27 miss: Seven Hills and Huntingwood are named, in a
    dash-tail after 'Western Sydney', and must both be extracted."""
    places = [p.lower() for p in ccv._excluded_places(_GEO_MEMORY)]
    for expect in ("adelaide", "seven hills", "huntingwood", "parramatta", "blacktown"):
        assert expect in places, expect


def test_excluded_places_skips_the_any_role_catchall():
    places = [p.lower() for p in ccv._excluded_places(_GEO_MEMORY)]
    assert not any(p.startswith("any ") for p in places)


def test_location_excluded_end_to_end(monkeypatch, tmp_path):
    mem = tmp_path / "user_geographic_sydney_only.md"
    mem.write_text(_GEO_MEMORY, encoding="utf-8")
    monkeypatch.setattr(ccv, "geo_memory", lambda: (mem, _GEO_MEMORY))

    for loc in ("Seven Hills", "48 Huntingwood Drive", "Parramatta NSW 2150"):
        assert ccv.check_location(loc)["status"] == "excluded", loc


def test_oncorridor_location_is_not_excluded(monkeypatch, tmp_path):
    mem = tmp_path / "user_geographic_sydney_only.md"
    monkeypatch.setattr(ccv, "geo_memory", lambda: (mem, _GEO_MEMORY))
    for loc in ("Chatswood", "North Sydney", "Clarence St, Sydney CBD"):
        assert ccv.check_location(loc)["status"] == "on_corridor", loc


def test_corridor_places_are_extracted_from_the_allowlist_sentence():
    allowed = [a.lower() for a in ccv._corridor_places(ccv._corridor_line(_GEO_MEMORY))]
    for expect in ("north sydney", "chatswood", "st leonards", "macquarie park"):
        assert expect in allowed, expect


def test_unnamed_far_location_is_off_corridor_not_passed(monkeypatch, tmp_path):
    """The bug live-verify caught: a denylist can never be complete.

    'Newcastle' is nowhere in the exclusion list, so a denylist-only check
    passed it. It is not on the corridor either, and the corridor is the
    operative rule — so it must fail, not pass.
    """
    mem = tmp_path / "user_geographic_sydney_only.md"
    monkeypatch.setattr(ccv, "geo_memory", lambda: (mem, _GEO_MEMORY))
    assert ccv.check_location("Newcastle")["status"] == "off_corridor"


def test_bare_city_name_is_off_corridor(monkeypatch, tmp_path):
    """The memory says 'Sydney is not granular enough' — so failing a bare
    city name is correct behaviour, not a false positive."""
    mem = tmp_path / "user_geographic_sydney_only.md"
    monkeypatch.setattr(ccv, "geo_memory", lambda: (mem, _GEO_MEMORY))
    assert ccv.check_location("Sydney")["status"] == "off_corridor"


def test_corridor_suburbs_and_remote_pass(monkeypatch, tmp_path):
    mem = tmp_path / "user_geographic_sydney_only.md"
    monkeypatch.setattr(ccv, "geo_memory", lambda: (mem, _GEO_MEMORY))
    for loc in ("Chatswood", "St Leonards NSW 2065", "fully remote from Sydney"):
        assert ccv.check_location(loc)["status"] == "on_corridor", loc


def test_exclusion_beats_corridor(monkeypatch, tmp_path):
    """An explicitly-excluded place stays 'excluded', never downgraded."""
    mem = tmp_path / "user_geographic_sydney_only.md"
    monkeypatch.setattr(ccv, "geo_memory", lambda: (mem, _GEO_MEMORY))
    assert ccv.check_location("Seven Hills")["status"] == "excluded"


def test_no_geo_memory_is_reported_not_silently_passed(monkeypatch):
    """Absence of the memory must be visible, never a silent 'fine'."""
    monkeypatch.setattr(ccv, "geo_memory", lambda: None)
    assert ccv.check_location("Seven Hills")["status"] == "no_memory"


def test_no_vault_configured_is_a_skip_not_a_failure(monkeypatch):
    """A gate that breaks a fresh clone gets disabled — it must exit 0."""
    monkeypatch.setattr(ccv, "vault_root", lambda: None)
    with pytest.raises(SystemExit) as e:
        ccv.main(["Fyfe"])
    assert e.value.code == 0


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / ".eos-personal").exists(),
    reason=".eos-personal is private and absent from public clones",
)
def test_career_gate_sources_carry_no_personal_patterns():
    """CLAUDE.md rule 13 — no personal data in git-tracked code.

    Regression pin for the leak that shipped with the career-gate commits: a
    real employer name sat in a comment in the module *and* in a fixture in
    this file, so `scripts/check-personal.py` — a hard preflight gate — went
    red on a clean checkout.

    Scans by loading `.eos-personal` rather than naming the offender, for two
    reasons. Naming it would reintroduce the very string this test guards
    against (the test would fail on itself), and reading the pattern file means
    a *future* addition to it is covered here for free instead of needing a new
    test. Illustrative company names in these two files should therefore be
    generic placeholders, not real employers.
    """
    repo = Path(__file__).resolve().parent.parent
    raw = (repo / ".eos-personal").read_text(encoding="utf-8")
    patterns = [
        re.compile(line.strip())
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert patterns, ".eos-personal produced no patterns — the guard would be vacuous"

    targets = [
        repo / "scripts" / "check_career_verdict.py",
        repo / "tests" / "test_unit_check_career_verdict.py",
    ]
    hits = []
    for target in targets:
        for line_no, line in enumerate(
            target.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for pat in patterns:
                if pat.search(line):
                    hits.append(f"{target.name}:{line_no} matches /{pat.pattern}/")

    assert not hits, "personal data in tracked career-gate source:\n  " + "\n  ".join(hits)
