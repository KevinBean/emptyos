"""An app page and the sibling .js files it loads share ONE global scope.

`.claude/rules/multi-module-apps.md` § "Frontend counterpart" extracts a page's
inline `<script>` into a sibling file loaded at the same position — deliberately
NOT a module, so load order and globals are unchanged. The cost of that choice is
that a top-level `function foo()` in the sibling and a top-level `function foo()`
in the page are the same binding, and the one that parses LAST silently wins.

Nothing announces it. No console error, no failed request, no exception — the
wrong function simply runs. Measured on the dictionary page (2026-09-01):
`pictures.js` declared `loadReview()` for picture cards, `index.html` declared
`loadReview()` for the word flashcard deck, and `pictures.js` loads afterwards.
Opening Practice -> Flashcards therefore called the picture loader, which never
touches `#review-area`, so the word review rendered an empty box. It had been
dead since the pictures fold-in and looked exactly like "no cards are due".

Repo-wide this is 0 findings once fixed, so it gates rather than advises.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APPS = Path(__file__).parents[1] / "apps"

_FUNC = re.compile(r"^(?:async\s+)?function\s+(\w+)", re.M)
_VAR = re.compile(r"^(?:var|let|const)\s+(\w+)", re.M)
_INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
_SRC = re.compile(r'<script[^>]*\bsrc="([^"]+)"')


def _globals(text: str) -> set[str]:
    """Top-level declarations only — column 0, so anything nested is out of scope.

    Deliberately crude. A real parser would also catch a global assigned inside a
    block, but those are rare here and the cheap version is what stays green
    without a maintenance burden. Under-reporting is the safe direction for a
    gate: a missed collision is the status quo, a false one gets it disabled.
    """
    return set(_FUNC.findall(text)) | set(_VAR.findall(text))


def _pairs():
    """(page, sibling script) for every app page that loads one of its own files."""
    for page in sorted(APPS.rglob("pages/*.html")):
        html = page.read_text(encoding="utf-8", errors="replace")
        inline = "\n".join(_INLINE.findall(html))
        if not inline.strip():
            continue
        for src in _SRC.findall(html):
            sibling = page.parent / Path(src).name
            if sibling.suffix == ".js" and sibling.exists():
                yield page, sibling, inline


def test_there_are_pairs_to_check():
    """Guards the guard: a gate at zero findings cannot tell 'clean' from 'looked
    at nothing' (.claude/rules/audits.md)."""
    assert len(list(_pairs())) >= 20


def test_the_extractor_actually_finds_declarations():
    """The other half of guarding the guard, and the half that was missing.

    Counting PAIRS proves the walk works; it says nothing about the regexes. With
    `_FUNC` blinded the suite stayed fully green — every intersection was empty
    because both sides were empty, which is a gate that inspects nothing while
    reporting success. So name real declarations on both sides of a real pair.
    """
    # The inline half is resolved at RUNTIME rather than naming one page. It
    # used to read dictionary/pages/index.html, which broke the moment that
    # page was split into a sibling — the fixture moved while the regexes it
    # exercises had not changed at all. Retired and personal pages are skipped:
    # `apps/personal/` is gitignored, so naming one would pass here and fail in
    # a fresh clone.
    pages = [
        p for p in sorted(APPS.rglob("pages/*.html"))
        if "/_retired/" not in p.as_posix() and "/personal/" not in p.as_posix()
    ]
    biggest = max(pages, key=lambda p: len("\n".join(
        _INLINE.findall(p.read_text(encoding="utf-8", errors="replace")))))
    inline = "\n".join(_INLINE.findall(biggest.read_text(encoding="utf-8")))
    # Both regexes asserted SEPARATELY: a `var` collision is just as silent as a
    # `function` one, and asserting only the union let `_VAR` be blinded with the
    # suite still fully green.
    assert len(set(_FUNC.findall(inline))) >= 10, f"_FUNC blinded on {biggest.name}"
    assert len(set(_VAR.findall(inline))) >= 3, f"_VAR blinded on {biggest.name}"

    # The named half now lives in the sibling those declarations were split
    # into — same identifiers, same guarantee, a file that cannot be split again.
    page = APPS / "public/englishos/dictionary/pages/index.html"
    from helpers import public_snapshot

    if not page.exists() and public_snapshot():
        pytest.skip("dictionary app absent (public snapshot); the inline half above ran")
    moved = _globals((page.parent / "dictionary.js").read_text(encoding="utf-8"))
    assert {"switchTab", "loadVocab", "setDifficulty"} <= moved, sorted(moved)[:20]
    assert {"vocabFilter", "_vocabAll"} <= moved, "top-level `var`s must be seen too"

    sibling = page.parent / "pictures.js"
    sib = _globals(sibling.read_text(encoding="utf-8"))
    assert "loadPictureReview" in sib, "the picture loader must stay namespaced"
    assert "loadReview" not in sib, "the collision is back"
    assert "S" in sib, "pictures.js's top-level `var S` must be seen — vars collide too"
    assert len(sib) > 20, f"only {len(sib)} declarations found in pictures.js"


def _sibling_pairs():
    """(page, a, b) for every two of a page's OWN sibling scripts.

    The page-vs-sibling check below cannot see this shape: a page split into
    several siblings with no inline script left (portal: portal-agent.js,
    portal-verbs.js, portal-sidebar.js, portal.js) has nothing to pair against,
    yet its siblings share one scope exactly as a page and its sibling do.
    """
    for page in sorted(APPS.rglob("pages/*.html")):
        html = page.read_text(encoding="utf-8", errors="replace")
        sibs: list[Path] = []
        for src in _SRC.findall(html):
            sibling = page.parent / Path(src).name
            if sibling.suffix == ".js" and sibling.exists() and sibling not in sibs:
                sibs.append(sibling)
        for i, a in enumerate(sibs):
            for b in sibs[i + 1:]:
                yield page, a, b


def test_there_are_sibling_pairs_to_check():
    """Same guard-the-guard as above, and it names the page that motivated it."""
    pairs = list(_sibling_pairs())
    assert len(pairs) >= 50, f"only {len(pairs)} sibling pairs — the walk moved"
    portal = [(a.name, b.name) for p, a, b in pairs if p.parent.parent.name == "portal"]
    assert ("portal-agent.js", "portal.js") in portal, portal


@pytest.mark.parametrize(
    "page,a,b",
    list(_sibling_pairs()),
    ids=lambda v: v.name if isinstance(v, Path) else "",
)
def test_two_siblings_of_one_page_declare_no_shared_global(page, a, b):
    clash = sorted(
        _globals(a.read_text(encoding="utf-8", errors="replace"))
        & _globals(b.read_text(encoding="utf-8", errors="replace"))
    )
    assert not clash, (
        f"{page.parent.parent.name}/{page.name} loads {a.name} and {b.name}, which both declare "
        f"{clash} at top level in one global scope — the later script wins silently. "
        f"Namespace one copy (.claude/rules/multi-module-apps.md § frontend counterpart)."
    )


@pytest.mark.parametrize(
    "page,sibling,inline",
    list(_pairs()),
    ids=lambda v: v.name if isinstance(v, Path) else "",
)
def test_page_and_its_sibling_script_declare_no_shared_global(page, sibling, inline):
    clash = sorted(_globals(inline) & _globals(sibling.read_text(encoding="utf-8", errors="replace")))
    assert not clash, (
        f"{page.name} and {sibling.name} both declare {clash} at top level and share one "
        f"global scope; {sibling.name} loads later, so its version wins silently. "
        f"Namespace the sibling's copy (.claude/rules/multi-module-apps.md § 7)."
    )
