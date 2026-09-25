"""Shared accuracy/scoring utilities for practice apps.

Used by: shadowing (LCS + alignment events), audio-course (both), english
(pronunciation), and future assessment apps.

Usage:
    from emptyos.sdk.scoring import lcs_score, word_accuracy, alignment_to_events
    score = lcs_score("the quick brown fox", "the brown fox")  # 0.75
    result = word_accuracy("hello world", "hello word")  # {"accuracy": 50.0, "grade": "C", ...}
    events = alignment_to_events(await self.pronounce(audio, target))
"""

from __future__ import annotations

import re


def lcs_score(target: str, attempt: str) -> float:
    """Score 0.0-1.0 based on longest common subsequence of words.

    Measures how well the attempt preserves the order and content of the target.
    More forgiving than exact match — allows skipped words.

    Args:
        target: Reference text.
        attempt: User's attempt.

    Returns:
        Float 0.0-1.0 (1.0 = perfect match).
    """
    t_words = target.lower().split()
    a_words = attempt.lower().split()
    if not t_words:
        return 0.0
    n, m = len(t_words), len(a_words)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if t_words[i - 1] == a_words[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return round(dp[n][m] / n, 3)


def word_accuracy(target: str, spoken: str) -> dict:
    """Word-level accuracy scoring with letter grade.

    Compares words positionally (zip). Strips punctuation before comparing.

    Args:
        target: Reference text.
        spoken: User's spoken text.

    Returns:
        dict with: accuracy (0-100), grade (A/B/C/D), matches (int), total (int).
    """
    t_words = re.sub(r"[^\w\s]", "", target.lower()).split()
    s_words = re.sub(r"[^\w\s]", "", spoken.lower()).split()
    if not t_words:
        return {"accuracy": 0, "grade": "D", "matches": 0, "total": 0}
    matches = sum(1 for t, s in zip(t_words, s_words, strict=False) if t == s)
    accuracy = round(matches / len(t_words) * 100, 1)
    grade = "A" if accuracy >= 90 else "B" if accuracy >= 75 else "C" if accuracy >= 50 else "D"
    return {"accuracy": accuracy, "grade": grade, "matches": matches, "total": len(t_words)}


def alignment_to_events(payload: dict) -> list[dict]:
    """Project a ``pronounce`` payload into flat rows for
    ``dictionary.log_pronounce_events``.

    Keeps only the *misses* (sub / del / ins — matches are dropped) and labels
    each with the word it happened inside, so the analyzer can say "your DH→S
    miss happened in 'the' here, 'this' there" rather than reporting a bare
    phone.

    Extracted from shadowing's private ``_alignment_to_events`` when audio-course
    became the second consumer. shadowing lives in ``apps/personal/`` (gitignored),
    so a tracked app cannot import it from there — the SDK is the only shared home.

    Args:
        payload: A ``BaseApp.pronounce()`` result — ``{alignment, word_alignment, ...}``.
            Tolerates the ``{"unavailable": True}`` shape and any missing key.

    Returns:
        ``[{op, ref, hyp, confidence, word}, ...]`` — empty when there is nothing
        to report.
    """
    rows = payload.get("alignment") or []
    word_alignment = payload.get("word_alignment") or []

    # Label each alignment row with its owning word. The aligner emits one row
    # per ref phone plus one per inserted hyp, all in document order, so a
    # running offset over each word's phones_alignment length lines them up.
    word_lookup: dict[int, str] = {}
    scan = 0
    for w in word_alignment:
        consumed = w.get("phones_alignment") or []
        for i in range(len(consumed)):
            if scan + i < len(rows):
                word_lookup[scan + i] = w.get("word", "")
        scan += len(consumed)

    return [
        {
            "op": row.get("op"),
            "ref": row.get("ref"),
            "hyp": row.get("hyp"),
            "confidence": row.get("confidence"),
            "word": word_lookup.get(i, ""),
        }
        for i, row in enumerate(rows)
        if row.get("op") != "match"
    ]
