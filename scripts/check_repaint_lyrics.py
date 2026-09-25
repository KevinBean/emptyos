"""Did a repaint keep the song's words, in the right place?

    python scripts/check_repaint_lyrics.py SOURCE REPAINTED START_S END_S

WHY THIS EXISTS SEPARATELY FROM measure_repaint.py
--------------------------------------------------
`measure_repaint.py` compares log-mel spectrograms, and log-mel is structurally
blind to lyric identity: different words sung at the same pitch and rhythm score
~0.83, which reads as a healthy repaint. That is exactly how a repaint that had
replaced the vocal shipped as "verified" on 2026-08-15. Measured the next day on
a take whose lyrics were known, repainting 20-40s:

    lyrics passed   variance   log-mel r   word-similarity   verdict
    (none)            0.50       ~0.83         0.149         invented gibberish
    correct           0.50       ~0.83         0.235         restarts the verse
    correct           0.30         --          0.778         holds the line
    correct           0.15         --          0.612         smeared

Same spectral score, three different outcomes for a listener. Any A/B on
generated *singing* needs a lyric-aware instrument alongside the spectral one.

The SOURCE's own transcript is the reference, not the authored lyric text: both
sides pass through the same recogniser, so a systematic mis-hearing cancels and
only real divergence shows. Sung-lyric ASR is imperfect -- treat the number as a
comparator between runs, not an absolute score, and listen before shipping.

Needs faster-whisper (`pip install faster-whisper`).
"""
from __future__ import annotations

import difflib
import re
import sys

# Below this, the window is singing something else. Calibrated on the table
# above: 0.778 held the line, 0.235 had restarted the verse. Wide gap, so the
# threshold is not delicate.
HOLDS_LINE = 0.55


def transcribe(model, path: str, t0: float, t1: float):
    segments, _ = model.transcribe(path, language="en", beam_size=5)
    kept = [s.text.strip() for s in segments if not (s.end < t0 or s.start > t1)]
    text = " ".join(kept)
    return text, re.findall(r"[a-z']+", text.lower())


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    src_p, new_p = sys.argv[1], sys.argv[2]
    t0, t1 = float(sys.argv[3]), float(sys.argv[4])

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper not installed — pip install faster-whisper")
        return 2

    model = WhisperModel("base", device="cpu", compute_type="int8")
    src_text, src_words = transcribe(model, src_p, t0, t1)
    new_text, new_words = transcribe(model, new_p, t0, t1)

    print(f"window [{t0:g}s, {t1:g}s]\n")
    print(f"  source  : {src_text or '(nothing transcribed)'}")
    print(f"  repaint : {new_text or '(nothing transcribed)'}\n")

    if not src_words:
        print("no words heard in the source window — nothing to preserve.")
        print("If the section is instrumental, this check does not apply and "
              "variance is free to go high.")
        return 0

    ratio = difflib.SequenceMatcher(None, src_words, new_words).ratio()
    ok = ratio >= HOLDS_LINE
    print(f"word-similarity = {ratio:.3f}  (need >= {HOLDS_LINE})")
    print(f"vocal line held : {'PASS' if ok else 'FAIL'}")
    if not ok:
        print("  -> the window is singing different words, or the same words "
              "from a different part of the song. Lower `variance` toward 0.3, "
              "and confirm the FULL original lyrics were passed — an empty "
              "lyrics argument makes the model invent vocals.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
