"""Shared audio/speech metrics for practice apps.

Used by: voice-review, interview-studio, speaking, speaking-practice and the
dictionary's "Talk about it" (``score_fluency``), and future voice apps.

Usage:
    from emptyos.sdk.audio import compute_speech_metrics
    metrics = compute_speech_metrics("Hello um I think basically...", duration_seconds=30)
    # {"word_count": 6, "wpm": 12, "filler_count": 2, "filler_words": ["um", "basically"], ...}
"""

from __future__ import annotations

import re
import unicodedata

# Common English filler words
FILLER_WORDS = {"um", "uh", "like", "basically", "actually", "so", "well", "right", "okay"}


def compute_speech_metrics(transcript: str, duration_seconds: float = 0) -> dict:
    """Compute speech metrics from transcript text.

    Args:
        transcript: Raw transcript text.
        duration_seconds: Recording duration in seconds (for WPM calculation).

    Returns:
        dict with: word_count, wpm, filler_count, filler_words,
                   pause_count, avg_words_per_sentence, sentence_count.
    """
    words = transcript.split()
    word_count = len(words)
    wpm = round(word_count / (duration_seconds / 60)) if duration_seconds > 0 else 0

    # Filler word detection
    lower_words = [w.lower().strip(".,!?;:\"'()") for w in words]
    filler_found = [w for w in lower_words if w in FILLER_WORDS]
    filler_count = len(filler_found)

    # Sentence analysis
    sentences = [s.strip() for s in re.split(r"[.!?]+", transcript) if s.strip()]
    sentence_count = len(sentences)
    pause_count = max(0, sentence_count - 1)
    avg_words = round(word_count / max(1, sentence_count), 1)

    return {
        "word_count": word_count,
        "wpm": wpm,
        "filler_count": filler_count,
        "filler_words": filler_found,
        "pause_count": pause_count,
        "sentence_count": sentence_count,
        "avg_words_per_sentence": avg_words,
    }


# A word is a run of letters/digits, optionally with one apostrophe part
# ("don't"). Hyphens and spaces both separate words, so "e-scooter" and
# "e scooter" are the same two words.
_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?")


def _fold(word: str) -> str:
    """Lower-case with accents removed: "Café" → "cafe"."""
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _word_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, folded word) for every word in ``text``."""
    return [(m.start(), m.end(), _fold(m.group(0))) for m in _WORD_RE.finditer(text or "")]


def speech_tokens(text: str) -> list[str]:
    """Lower-case, accent-folded word tokens; contractions ("don't") stay whole."""
    return [w for _, _, w in _word_spans(text)]


def plural_forms(word: str) -> set[str]:
    """The regular English plurals of one word: cup→cups, dish→dishes,
    berry→berries, knife→knives, leaf→leaves. No irregulars (mouse/mice)."""
    forms = {word}
    if word.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(word + "es")
    elif word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        forms.add(word[:-1] + "ies")
    elif word.endswith("fe"):
        forms.add(word[:-2] + "ves")
    elif word.endswith("f"):
        forms.add(word[:-1] + "ves")
    else:
        forms.add(word + "s")
    return forms


def _name_matches(spans: list, names: list[str]) -> list[tuple[str, int, int]]:
    """(name, first span index, last span index) for each occurrence.

    Longer names claim their words first, so "chef knife" is one match and
    its "knife" is not also a match for the name "knife".
    """
    words = [w for _, _, w in spans]
    taken = [False] * len(words)
    found = []
    for name in sorted(names, key=lambda n: -len(speech_tokens(n))):
        toks = speech_tokens(name)
        if not toks:
            continue
        head, lasts, n = toks[:-1], plural_forms(toks[-1]), len(toks)
        for i in range(len(words) - n + 1):
            if any(taken[i:i + n]):
                continue
            if words[i:i + n - 1] == head and words[i + n - 1] in lasts:
                found.append((name, i, i + n - 1))
                taken[i:i + n] = [True] * n
    return found


def words_used(transcript: str, names: list[str]) -> list[str]:
    """Which of ``names`` (single or multi-word) appear in the transcript.

    Shared by the dictionary's "Talk about it" and Improv's word scenes —
    "did the learner produce this word", not a lemmatiser. Whole words, accents
    ignored ("cafe" is "café"), hyphens as spaces ("e scooter" is
    "e-scooter"); the last word may be a regular plural ("knives", "rice
    cookers"), but only a real plural ending, so "tapes" is not "tap".
    Returned in the order of ``names``.
    """
    hit = {name for name, _, _ in _name_matches(_word_spans(transcript), names)}
    return [n for n in names if n in hit]


def mask_words(text: str, names: list[str], mask: str = "…") -> str:
    """Replace every occurrence of ``names`` in ``text`` with ``mask``,
    matched exactly as :func:`words_used` matches them."""
    spans = _word_spans(text)
    cuts = sorted(((spans[a][0], spans[b][1]) for _, a, b in _name_matches(spans, names)),
                  reverse=True)
    for start, end in cuts:
        text = text[:start] + mask + text[end:]
    return text


def alignment_pauses(pronounce_payload: dict | None) -> list[float]:
    """Gaps (seconds) between consecutive aligned words in a pronounce payload."""
    words = (pronounce_payload or {}).get("word_alignment") or []
    pauses = []
    previous_end = None
    for row in words:
        start, end = row.get("start"), row.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if previous_end is not None and start > previous_end:
            pauses.append(round(float(start - previous_end), 3))
        previous_end = float(end)
    return pauses


# Longest restart counted: "I want to, I want to" is three words.
_MAX_RESTART_WORDS = 3
# Words English doubles on purpose ("that that", "had had", "very very",
# "no no") — said twice in a row they are grammar or emphasis, not a stutter.
_DELIBERATE_DOUBLES = {"that", "had", "very", "no", "yes", "bye", "so", "really", "ha"}


def immediate_repetitions(words: list[str]) -> int:
    """Stutters and restarts: a run of one to three words said again straight
    away — "the the", "I went I went", "I want to I want to".

    Only immediate repeats count. An earlier version counted any word pair
    appearing twice anywhere, which charged a fluent 30-second description
    for saying "it is" twice — ordinary language, not a false start. The
    longest matching run wins at each point, and scanning resumes right after
    it, so a stutter following a restart ("I went I went went") counts too.
    """
    count, i = 0, 1
    while i < len(words):
        for n in range(min(_MAX_RESTART_WORDS, i), 0, -1):
            if n == 1 and words[i] in _DELIBERATE_DOUBLES:
                continue
            if words[i:i + n] == words[i - n:i]:
                count += 1
                i += n
                break
        else:
            i += 1
    return count


# A silence this long inside an answer reads as losing the thread rather than
# a breath. The practice threshold Speaking Practice has scored with since it
# shipped; kept here so every consumer draws the line in the same place.
LONG_PAUSE_S = 1.2


def score_fluency(transcript: str, duration_seconds: float,
                  pronounce_payload: dict | None = None, *,
                  pauses: list[float] | None = None) -> dict:
    """0–5 oral-fluency practice score with every deduction spelled out.

    Pace outside 90–210 words per minute, filler words, repeated phrases or
    false starts, and long pauses each cost a bounded amount. Pauses come from
    the pronounce payload's word alignment unless ``pauses`` supplies them
    (e.g. measured from the audio when alignment is unavailable).

    Moved here from Speaking Practice (2026-09-22) when the dictionary's
    "Talk about it" became its second consumer. The one change made then:
    repetitions count only immediate repeats (:func:`immediate_repetitions`).
    """
    metrics = compute_speech_metrics(transcript, duration_seconds)
    repetitions = immediate_repetitions(speech_tokens(transcript))
    if pauses is None:
        pauses = alignment_pauses(pronounce_payload)
    long_pauses = [p for p in pauses if p >= LONG_PAUSE_S]
    score = 5.0
    wpm = metrics["wpm"]
    missed = []
    if duration_seconds <= 0:
        score = min(score, 1.0)
        missed.append("Measured recording duration was unavailable")
    if wpm and wpm < 90:
        score -= min(1.5, (90 - wpm) / 40)
        missed.append(f"Pace was below the 90 word-per-minute practice range ({wpm} wpm)")
    elif wpm > 210:
        score -= min(1.5, (wpm - 210) / 60)
        missed.append(f"Pace was above the 210 word-per-minute practice range ({wpm} wpm)")
    filler_ratio = metrics["filler_count"] / max(1, metrics["word_count"])
    score -= min(1.25, filler_ratio * 8)
    score -= min(1.0, repetitions * 0.25)
    score -= min(1.25, len(long_pauses) * 0.25)
    if metrics["filler_count"]:
        missed.append(f"{metrics['filler_count']} filler words")
    if repetitions:
        missed.append(f"{repetitions} repeated phrases or false starts")
    if long_pauses:
        missed.append(f"{len(long_pauses)} long pauses")
    if metrics["word_count"] < 3:
        score = min(score, 1.0)
    return {
        "score": round(max(0.0, min(5.0, score)), 2),
        "metrics": metrics,
        "repetitions_false_starts": repetitions,
        "timestamp_pauses": pauses,
        "long_pause_count": len(long_pauses),
        "matched": [f"Recorded pace: {wpm} words per minute"],
        "missed": missed,
    }


def mix_streams_to_wav(streams, samplerate: int = 16000) -> bytes:
    """Mix N mono 16-bit PCM streams into one 16-bit mono WAV (returns the WAV bytes).

    Each stream is either a 1-D numpy int16 array or raw int16 little-endian bytes
    (both are what a capture callback accumulates). Shorter streams are zero-padded
    to the longest; samples are summed in int32 and clipped to the int16 range so
    two overlapping talkers don't wrap. Pure — no I/O, no device access — so the
    meeting-capture plugin's mixdown is unit-testable without real audio.

    An empty / all-empty input yields a valid, zero-length WAV (not an error) so a
    discarded/silent capture still round-trips.
    """
    import io
    import wave

    import numpy as np

    arrs = []
    for s in streams or []:
        if s is None:
            continue
        if isinstance(s, (bytes, bytearray)):
            a = np.frombuffer(bytes(s), dtype=np.int16)
        else:
            a = np.asarray(s, dtype=np.int16)
        if a.size:
            arrs.append(a.astype(np.int32))

    if arrs:
        n = max(a.size for a in arrs)
        mix = np.zeros(n, dtype=np.int32)
        for a in arrs:
            if a.size < n:
                a = np.pad(a, (0, n - a.size))
            mix += a
        pcm = np.clip(mix, -32768, 32767).astype("<i2").tobytes()
    else:
        pcm = b""

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(samplerate))
        w.writeframes(pcm)
    return buf.getvalue()


def assess_pacing(wpm: int) -> dict:
    """Assess speaking pace. Ideal range: 130-160 WPM.

    Returns:
        dict with: rating ("slow"|"good"|"fast"), feedback (str).
    """
    if wpm == 0:
        return {"rating": "unknown", "feedback": "No pace data available."}
    if wpm < 110:
        return {
            "rating": "slow",
            "feedback": f"Speaking pace is slow ({wpm} WPM). Ideal: 130-160 WPM.",
        }
    if wpm <= 180:
        return {"rating": "good", "feedback": f"Good speaking pace ({wpm} WPM)."}
    return {"rating": "fast", "feedback": f"Speaking pace is fast ({wpm} WPM). Ideal: 130-160 WPM."}


def assess_fillers(filler_count: int, word_count: int) -> dict:
    """Assess filler word usage.

    Returns:
        dict with: rating ("good"|"moderate"|"high"), feedback (str), ratio (float).
    """
    if word_count == 0:
        return {"rating": "unknown", "feedback": "No data.", "ratio": 0}
    ratio = round(filler_count / word_count * 100, 1)
    if filler_count <= 1:
        return {"rating": "good", "feedback": "Minimal filler words.", "ratio": ratio}
    if ratio < 3:
        return {
            "rating": "moderate",
            "feedback": f"{filler_count} filler words ({ratio}%). Try pausing instead.",
            "ratio": ratio,
        }
    return {
        "rating": "high",
        "feedback": f"{filler_count} filler words ({ratio}%). Practice pausing between thoughts.",
        "ratio": ratio,
    }
