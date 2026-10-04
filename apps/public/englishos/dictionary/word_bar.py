"""The bar a word must clear to be worth interrupting the reader for.

Frequency, not opinion — and that is the whole point. Asked to judge "would a B2
learner hesitate at this word", a model does not. Measured on a plain news page,
trivial for C1:

    qwen3.5 local   A2 []  ·  B1 []  ·  C1 []       — ignores the band entirely
    gpt-5.4-mini    A2/B1/C1 all flag: council, route, residents, noise, mayor

The local model flags nothing for a beginner; the stronger one flags everything for an
expert. Telling a model the reader's level is not a bar, it is a suggestion the model
discards — so a picker built on it would have been a control that did not control.

A frequency table cannot decline, cannot pad, and cannot be talked out of it. It also
makes the level TESTABLE, which is the part that matters: `council` is below a C1 bar
and above an A2 bar, and a unit test can say so.

Pure: no `self`, no I/O, no model. Consumers are `reading.py` (the reading bar) and
`vocab_loop.py` (the harvest bar) — both inside this app, so this is a module and not an
SDK extraction (CLAUDE.md rule 9).
"""

from __future__ import annotations

import re

# Zipf = log10(occurrences per billion words). `the` is 7.7, `council` 5.1,
# `promulgated` 3.0, `inchoate` 2.1. A word is ABOVE the bar — worth glossing — when its
# Zipf is BELOW the level's threshold: rarer than what a reader at that level reads
# fluently.
#
# The ladder is nested on purpose, and that gives monotonicity for free: an A2 shortlist
# is always a superset of a C1 one. No model achieved that; it is arithmetic here.
#
# Calibrated against six page shapes (dense legal / slang / tiny chunk / plain news /
# nav chrome / elementary), not chosen by taste. Re-measure before moving one.
ZIPF_BAR: dict[str, float] = {
    "A1": 5.6,
    "A2": 5.2,
    "B1": 4.8,
    "B2": 4.3,
    "C1": 3.7,
    "C2": 3.0,
}
DEFAULT_LEVEL = "C1"

# A word absent from EIGHT blended corpora is not rare vocabulary — it is a surname, a
# brand, a typo, or an artefact of the page. Flagging it would teach the reader nothing
# and spend a model call finding that out.
MIN_ZIPF = 0.5
MIN_LENGTH = 3

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'-]*")
# A capital after one of these is the start of a sentence, so its capital says nothing
# about whether it is a name.
_SENTENCE_END = re.compile(r"[.!?:;\"“”\n]\s*$")


def available() -> bool:
    """Is the frequency table installed? (`pip install 'emptyos[english]'`)

    Fail-soft, like duckdb in sdk/tabular.py: without it the reading layer falls back to
    asking the model to choose, which is what it did before — worse, but not broken.
    """
    try:
        import wordfreq  # noqa: F401, PLC0415

        return True
    except ImportError:
        return False


def _raw_zipf(word: str) -> float:
    try:
        from wordfreq import zipf_frequency  # noqa: PLC0415

        return float(zipf_frequency(str(word or "").lower(), "en"))
    except ImportError:
        return 0.0


# If you know `punk`, you know `punks`. Surface-form frequency does not know that: it
# rates every inflection on its own, so `punks`, `drafters` and `worried` all score as
# rarer than the words they are made of, and the bar flags vocabulary the reader already
# has. Worse, the interruption budget keeps the RAREST words, so these artefacts crowd out
# the ones that were actually worth glossing — measured, `tribunal's` displaced
# `promulgated`.
#
# So a word inherits the frequency of the base it is plainly derived from. Conservative:
# only real bases count (a wrong strip yields a non-word, which scores 0 and is ignored),
# and we take the MAX, so nothing is ever made rarer by the backoff.
_SUFFIXES = (
    ("ies", "y"), ("es", ""), ("s", ""),          # plurals / third person
    ("ed", ""), ("ed", "e"), ("d", ""),           # past
    ("ing", ""), ("ing", "e"),                    # progressive
    ("ly", ""), ("ly", "e"),                      # adverbs: culpably -> culpable
)


def zipf(word: str) -> float:
    """Frequency of a word, counting the base it is derived from.

    0.0 when the table is unavailable or the word is unknown to eight blended corpora.
    """
    base = str(word or "").lower()
    if not base:
        return 0.0
    best = _raw_zipf(base)
    for suffix, replacement in _SUFFIXES:
        if len(base) > len(suffix) + 2 and base.endswith(suffix):
            best = max(best, _raw_zipf(base[: -len(suffix)] + replacement))
    return best


def threshold_for(level: str) -> float:
    return ZIPF_BAR.get(str(level or "").strip().upper(), ZIPF_BAR[DEFAULT_LEVEL])


def is_above_bar(word: str, level: str) -> bool:
    """Rare enough for this reader to stumble on — and rare enough to be a real word."""
    score = zipf(word)
    return MIN_ZIPF <= score < threshold_for(level)


def candidates(text: str) -> list[str]:
    """Every word on the screen that could be flagged at all, lowercased and deduped.

    Drops proper nouns by SHAPE — a capital mid-sentence — rather than by asking a model,
    because `Tuesday` and `Sydney` are rare in the same way a hard word is, and neither is
    vocabulary worth learning. A capital at the start of a sentence says nothing, so it is
    not evidence.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _TOKEN.finditer(text or ""):
        word = match.group(0)
        # A possessive is not a word to learn: `tribunal's` scores as rare because that
        # exact string is rare, and it displaced `promulgated` from the shortlist.
        if word.lower().endswith("'s") or word.lower().endswith("’s"):
            word = word[:-2]
        word = word.strip("'’-")
        if len(word) < MIN_LENGTH:
            continue
        before = text[: match.start()]
        sentence_start = not before.strip() or bool(_SENTENCE_END.search(before))
        if word[0].isupper() and not sentence_start:
            continue  # a name, not a word to learn
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def shortlist(
    text: str,
    *,
    level: str = DEFAULT_LEVEL,
    known: set[str] | None = None,
    cap: int = 6,
) -> list[str]:
    """The words on this screen worth interrupting THIS reader for, hardest first.

    The count is an output, never an input: a screen with two hard words yields two, and
    one with none yields none. `cap` is the interruption budget (see `_cap_for` in
    reading.py) — a ceiling that trims the tail, not a quota that invents a head.
    """
    if cap <= 0 or not available():
        return []
    skip = {str(w).lower() for w in (known or set())}
    scored = [
        (zipf(word), word)
        for word in candidates(text)
        if word not in skip and is_above_bar(word, level)
    ]
    scored.sort()  # rarest first — if the budget trims, it trims the easiest
    return [word for _, word in scored[:cap]]
