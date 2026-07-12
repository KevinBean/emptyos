"""Shared audio/speech metrics for practice apps.

Used by: voice-review, interview-studio, speaking, and future voice apps.

Usage:
    from emptyos.sdk.audio import compute_speech_metrics
    metrics = compute_speech_metrics("Hello um I think basically...", duration_seconds=30)
    # {"word_count": 6, "wpm": 12, "filler_count": 2, "filler_words": ["um", "basically"], ...}
"""

from __future__ import annotations

import re

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
