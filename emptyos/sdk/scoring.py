"""Shared accuracy/scoring utilities for practice apps.

Used by: shadowing (LCS), english (pronunciation), and future assessment apps.

Usage:
    from emptyos.sdk.scoring import lcs_score, word_accuracy
    score = lcs_score("the quick brown fox", "the brown fox")  # 0.75
    result = word_accuracy("hello world", "hello word")  # {"accuracy": 50.0, "grade": "C", ...}
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
