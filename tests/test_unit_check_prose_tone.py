"""Unit tests for scripts/check_prose_tone.py — the `spoken: true` marker + corpus.

The marker is load-bearing in an unusual way: it is what turns the spoken-register
family from "a flag someone remembers to pass" into an unattended preflight run.
If ``is_spoken`` silently returns False for a legitimately marked note, the runner
still exits 0 — it just scans nothing, and nobody finds out. So both directions
are pinned here, including the frontmatter shapes that actually occur in the vault.

Run: python -m pytest tests/test_unit_check_prose_tone.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from check_prose_tone import is_spoken, vault_corpus  # noqa: E402


# ── the marker is recognised ────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    "---\nspoken: true\n---\n\nSaid aloud.\n",
    "---\ntags:\n  - interview-prep\nspoken: true\n---\n\nSaid aloud.\n",
    '---\nspoken: "true"\n---\n\nQuoted is still true.\n',
    "---\nspoken: True\n---\n\nCapitalised.\n",
])
def test_marked_notes_detected(body: str):
    assert is_spoken(body) is True


# ── and everything else is left alone ───────────────────────────────────────

@pytest.mark.parametrize("body", [
    "---\nspoken: false\n---\n\nRead, not said.\n",
    "---\ntags:\n  - cv\n---\n\nNo marker at all.\n",
    "No frontmatter whatsoever, just prose.\n",
    "",
    "---\nspoken:\n---\n\nEmpty value is not an opt-in.\n",
])
def test_unmarked_notes_not_detected(body: str):
    assert is_spoken(body) is False


def test_malformed_frontmatter_is_not_spoken():
    """A broken note must fail closed, not raise — the scan walks a whole vault."""
    assert is_spoken("---\nthis: is: not: yaml\nspoken\n") is False


# ── corpus selection stays narrow ───────────────────────────────────────────

def test_vault_corpus_selects_only_posts_and_marked(tmp_path: Path):
    posts = tmp_path / "30_Resources" / "Published" / "posts"
    posts.mkdir(parents=True)
    (posts / "a-post.md").write_text("A published post.\n", encoding="utf-8")
    (posts / "_draft-style.md").write_text("Underscore files are skipped.\n", encoding="utf-8")

    career = tmp_path / "20_Areas" / "Career"
    career.mkdir(parents=True)
    (career / "answers.md").write_text("---\nspoken: true\n---\n\nSaid.\n", encoding="utf-8")
    # The false-positive flood this marker exists to prevent.
    (career / "cv.md").write_text("---\ntags:\n  - cv\n---\n\nNot spoken.\n", encoding="utf-8")
    (career / "tracker.md").write_text("A tracker, not spoken.\n", encoding="utf-8")

    found_posts, spoken = vault_corpus(tmp_path)
    assert [p.name for p in found_posts] == ["a-post.md"]
    assert [p.name for p in spoken] == ["answers.md"]


def test_vault_corpus_survives_an_undecodable_file(tmp_path: Path):
    """A vault holds files in other encodings; one must not abort the scan."""
    career = tmp_path / "20_Areas"
    career.mkdir(parents=True)
    (career / "good.md").write_text("---\nspoken: true\n---\n\nSaid.\n", encoding="utf-8")
    (career / "legacy.md").write_bytes(b"\x93not utf-8 at all\x94")

    _, spoken = vault_corpus(tmp_path)
    assert [p.name for p in spoken] == ["good.md"]


def test_vault_corpus_handles_missing_posts_dir(tmp_path: Path):
    posts, spoken = vault_corpus(tmp_path)
    assert posts == [] and spoken == []
