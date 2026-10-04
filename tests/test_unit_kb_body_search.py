"""KB full-text search over note bodies (kb/notes.py::search_bodies).

Daemon-free: a fake app supplies the corpus, the search capability and the
body reader, so each rule — every term must be in the body, only KB notes
count, regex characters are literal, the search is pinned to grep — is pinned
without a vault.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from helpers import load_app_module

notes = load_app_module("kb", "notes", preload=("shared", "reference_coverage"))

VAULT = Path("/vault")
BODIES = {
    "kb/notes/cable-derating.md": "Grouping reduces ampacity.\nThe mutual heating factor dominates here.",
    "kb/notes/soil-model.md": "Two-layer soil with a thermal resistivity of 1.2 K.m/W.",
    "journal/2026-10-01.md": "mutual heating came up in the review today",   # not a KB note
}


class _App:
    def __init__(self):
        self.kernel = SimpleNamespace(config=SimpleNamespace(notes_path=VAULT))
        self.searched = []

    def _all_notes(self):
        return [{"path": p} for p in BODIES if p.startswith("kb/")]

    async def search(self, query, **kw):
        self.searched.append((query, kw))
        # Stand-in for ripgrep: literal, case-insensitive, over every file.
        lit = query.replace("\\", "")
        return [{"path": str(VAULT / p)} for p, b in BODIES.items() if lit.lower() in b.lower()]

    def vault_read_body(self, rel):
        return BODIES.get(rel, "")


def _run(q, app=None):
    app = app or _App()
    return asyncio.run(notes.search_bodies(app, q)), app


def test_finds_a_note_by_a_word_in_its_body():
    out, _ = _run("mutual heating")
    assert [h["slug"] for h in out["hits"]] == ["cable-derating"]
    assert "mutual heating" in out["hits"][0]["snippet"].lower()


def test_a_non_kb_file_with_the_words_is_not_a_hit():
    out, _ = _run("mutual heating")
    assert "2026-10-01" not in [h["slug"] for h in out["hits"]]


def test_every_term_must_be_in_the_body():
    out, _ = _run("heating resistivity")
    assert out["hits"] == []


def test_short_terms_search_nothing_and_never_call_search():
    out, app = _run("of a")
    assert out["hits"] == [] and app.searched == []


def test_regex_characters_reach_grep_escaped():
    _, app = _run("k.m/w")
    pattern, kw = app.searched[0]
    assert pattern == r"k\.m/w"


def test_search_is_pinned_to_grep():
    """An empty chain must never fall through to the human provider."""
    _, app = _run("thermal")
    assert app.searched[0][1].get("only_provider") == "grep"


def test_truncation_is_reported_when_the_hit_cap_is_reached(monkeypatch):
    monkeypatch.setattr(notes, "_BODY_SEARCH_LIMIT", 1)
    out, _ = _run("the")
    assert len(out["hits"]) == 1 and out["truncated"] is True


def test_no_truncation_when_everything_was_read():
    out, _ = _run("mutual heating")
    assert out["truncated"] is False


# ── end to end through the real ripgrep provider ──────────────────────
# The fake above matches literally whatever it is given, so it cannot see an
# escaping bug; this runs the real thing. A flag-shaped query is pinned at the
# provider (tests/test_unit_grep_option_injection.py): at this level the body
# re-check filters an injected file list back to the right answer, so a test
# here could never go red for it.

import shutil  # noqa: E402

import pytest  # noqa: E402

from emptyos.capabilities.providers.grep_search import GrepSearchProvider  # noqa: E402

_needs_rg = pytest.mark.skipif(not shutil.which("rg"), reason="ripgrep not installed")


class _RealApp:
    def __init__(self, root, files):
        self.kernel = SimpleNamespace(config=SimpleNamespace(notes_path=root))
        self._root, self._files = root, files
        self._grep = GrepSearchProvider(str(root))
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")

    def _all_notes(self):
        return [{"path": rel} for rel in self._files]

    async def search(self, query, only_provider=None, **kw):
        assert only_provider == "grep"
        await self._grep.available()
        return await self._grep.execute(query=query, **kw)

    def vault_read_body(self, rel):
        return (self._root / rel).read_text(encoding="utf-8")


@_needs_rg
def test_a_term_that_is_not_a_valid_regex_is_still_found(tmp_path):
    """What escaping buys is recall: unescaped, `f(x` is an unclosed group,
    ripgrep exits with an error, and the note that says it is never found.
    (A too-broad regex cannot be tested here — the body re-check filters it.)"""
    app = _RealApp(tmp_path, {"kb/a.md": "the loss is f(x) = k x^2\n",
                              "kb/b.md": "the loss is quadratic\n"})
    out = asyncio.run(notes.search_bodies(app, "f(x"))
    assert [h["slug"] for h in out["hits"]] == ["a"]


def test_a_failing_search_returns_no_hits():
    class _Broken(_App):
        async def search(self, query, **kw):
            raise RuntimeError("no provider")

    out, _ = _run("thermal", _Broken())
    assert out["hits"] == []
