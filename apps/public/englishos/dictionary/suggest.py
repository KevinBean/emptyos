"""dictionary — local word suggestions: autocomplete and did-you-mean.

Replaces the Datamuse calls (editions M10, Kevin 2026-09-28). Datamuse needed
every keystroke to leave the machine outside the consent gate, would require
an API key from 2027-01-01, and asks public apps to credit it. The vocabulary
is the definition pack's headwords, ranked by `wordfreq` (already a dependency
of the reading layer, `word_bar.py`); without a pack it falls back to wordfreq's
own list. Nothing leaves the machine.

Pure functions only — no `self`, no I/O beyond importing wordfreq once. The
routes in `lookup.py` hold one `WordIndex` per app instance.
"""

from __future__ import annotations

import bisect
import difflib
import re

#: How many of wordfreq's most frequent words to index. 50,000 covers everyday
#: English several times over; wordfreq's own list stops being words well
#: before its tail.
VOCAB_SIZE = 50_000

_WORD = re.compile(r"^[a-z][a-z'-]*[a-z]$")
_VOWELS = re.compile(r"[aeiouy]+")
_DOUBLES = re.compile(r"(.)\1+")


def load_vocabulary(headwords: list[str] | None = None, size: int = VOCAB_SIZE) -> list[str]:
    """The suggestion vocabulary, most frequent first.

    Prefers `headwords` — the definition pack's, which are real dictionary
    spellings — ordered by wordfreq rank. Without a pack, wordfreq's own list
    is the vocabulary; it is corpus-derived, so common misspellings (`recieve`,
    `thier`) count as known words there. Empty when neither is available: the
    routes then answer with no suggestions, as the Datamuse version did offline.
    """
    try:
        from wordfreq import top_n_list  # noqa: PLC0415 — optional `english` extra
        ranked = [w for w in top_n_list("en", size) if _WORD.match(w)]
    except ImportError:
        ranked = []
    words = [w for w in (headwords or []) if _WORD.match(w)]
    if not words:
        return ranked
    order = {w: i for i, w in enumerate(ranked)}
    return sorted(words, key=lambda w: (order.get(w, len(order)), w))


def skeleton(word: str) -> str:
    """A rough sound shape: doubled letters collapsed, vowel runs merged.

    Learners misspell by ear (`recieve`, `occured`, `definately`); comparing
    skeletons catches a sound-alike spelling whose letters differ a lot.
    """
    w = _DOUBLES.sub(r"\1", word.lower().replace("ph", "f"))
    return w[:1] + _VOWELS.sub("*", w[1:])


class WordIndex:
    """A frequency-ranked vocabulary answering prefix and near-miss queries."""

    def __init__(self, words: list[str], *, spellings_trusted: bool = True):
        # spellings_trusted: every word is a real spelling (the pack's
        # headwords), so a word in the list needs no correction. A corpus list
        # holds common misspellings, so there membership proves nothing and
        # did_you_mean still offers the nearest other words.
        self._trusted = spellings_trusted
        # rank: position in the frequency list (0 = most frequent).
        self._rank: dict[str, int] = {}
        for i, w in enumerate(words):
            self._rank.setdefault(w.lower(), i)
        self._sorted = sorted(self._rank)
        self._by_skeleton: dict[str, list[str]] = {}
        for w in self._sorted:
            self._by_skeleton.setdefault(skeleton(w), []).append(w)

    def __len__(self) -> int:
        return len(self._sorted)

    def complete(self, prefix: str, limit: int = 8) -> list[str]:
        """Words starting with `prefix`, most frequent first."""
        p = (prefix or "").strip().lower()
        if len(p) < 2:
            return []
        lo = bisect.bisect_left(self._sorted, p)
        hi = bisect.bisect_left(self._sorted, p + "￿")
        hits = self._sorted[lo:hi]
        return sorted(hits, key=self._rank.__getitem__)[:limit]

    def did_you_mean(self, query: str, limit: int = 5) -> list[str]:
        """Known words close to `query` in spelling or sound, best first.

        Empty when `query` is itself a known word of a trusted vocabulary —
        there is nothing to correct. Never suggests `query` itself.
        """
        q = (query or "").strip().lower()
        if len(q) < 2 or (self._trusted and q in self._rank):
            return []
        # Spelling: difflib over words of a similar length (a typo rarely
        # changes the length by more than two letters).
        near = [w for w in self._sorted if abs(len(w) - len(q)) <= 2 and w != q]
        scored: dict[str, float] = {
            w: difflib.SequenceMatcher(None, q, w).ratio()
            for w in difflib.get_close_matches(q, near, n=limit * 4, cutoff=0.72)
        }
        # Transposition: two neighbouring letters swapped (`teh`, `adn`) is the
        # commonest typo, and difflib scores it below the cutoff in short words.
        for i in range(len(q) - 1):
            w = q[:i] + q[i + 1] + q[i] + q[i + 2:]
            if w != q and w in self._rank:
                scored[w] = max(scored.get(w, 0.0), 0.99)
        # Sound: a word sharing the query's skeleton is a strong match, still
        # ordered among its peers by spelling (wierd -> weird before word).
        for w in self._by_skeleton.get(skeleton(q), []):
            if w == q:
                continue
            ratio = difflib.SequenceMatcher(None, q, w).ratio()
            scored[w] = max(scored.get(w, 0.0), 0.85 + 0.1 * ratio)
        ranked = sorted(scored, key=lambda w: (-round(scored[w], 2), self._rank[w]))
        return ranked[:limit]
