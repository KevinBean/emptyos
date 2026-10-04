"""Audio utilities — stitch segments, change tempo, measure speech onset."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


async def stitch_audio(
    seg_paths: list[Path],
    output_dir: Path,
    gap_ms: int = 400,
    tail_ms: int = 1500,
    bitrate: str = "192k",
    filename_prefix: str = "stitched",
) -> str:
    """Concatenate audio files into one MP3. Returns relative filename."""
    import time

    def _do():
        from pydub import AudioSegment

        combined = AudioSegment.empty()
        gap = AudioSegment.silent(duration=gap_ms)
        for p in seg_paths:
            try:
                seg = AudioSegment.from_file(str(p))
                if len(combined) > 0:
                    combined += gap
                combined += seg
            except Exception:
                continue
        if len(combined) == 0:
            return ""
        combined += AudioSegment.silent(duration=tail_ms)
        out_name = f"{filename_prefix}_{int(time.time())}.mp3"
        out_path = output_dir / out_name
        combined.export(str(out_path), format="mp3", bitrate=bitrate)
        return out_name

    return await asyncio.to_thread(_do)


async def clean_audio(
    path: str | Path,
    *,
    highpass: int = 60,
    lowpass: int | None = None,
    denoise: bool = False,
    bitrate: str = "192k",
) -> bool:
    """De-rumble (and optionally denoise) an audio file IN PLACE, pitch-neutral.

    By default only applies a ``highpass`` to strip sub-bass rumble — a linear
    filter that cannot introduce artifacts. Spectral denoise (``afftdn``) is
    OPT-IN via ``denoise=True`` because, while it cleans real acoustic hiss well,
    it can produce audible "musical noise" warble when run against the *synthetic*
    hiss of cloned-reference TTS (VibeVoice). Returns True when applied, False on
    no-op (no filters) or failure (ffmpeg missing / encode error) — the original
    file is always left intact on failure. ffmpeg is located via pydub's resolver
    (same as ``change_tempo``), so no new dependency.
    """
    from pydub.utils import which

    ff = which("ffmpeg")
    if not ff:
        return False

    filters: list[str] = []
    if highpass:
        filters.append(f"highpass=f={int(highpass)}")
    if lowpass:
        filters.append(f"lowpass=f={int(lowpass)}")
    if denoise:
        filters.append("afftdn=nf=-25")
    if not filters:
        return False
    af = ",".join(filters)

    p = Path(path)
    tmp = p.with_name(p.stem + "_clean" + p.suffix)
    try:
        proc = await asyncio.create_subprocess_exec(
            ff, "-y", "-i", str(p), "-af", af, "-b:a", bitrate, str(tmp),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(str(tmp), str(p))
            return True
    except Exception:
        pass
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    return False


async def change_tempo(path: str | Path, speed: float, bitrate: str = "192k") -> bool:
    """Time-stretch an audio file IN PLACE, pitch-preserving (ffmpeg ``atempo``).

    ``speed`` < 1.0 slows down, > 1.0 speeds up; clamped to ffmpeg's single-
    filter atempo range [0.5, 2.0]. Returns True when applied, False on no-op
    (``speed`` ~= 1.0) or failure (ffmpeg missing / encode error) — the original
    file is always left intact on failure. ffmpeg is located via pydub's resolver
    (the same one ``stitch_audio`` relies on), so no new dependency.
    """
    if not speed or abs(speed - 1.0) < 0.01:
        return False
    speed = max(0.5, min(2.0, speed))

    from pydub.utils import which

    ff = which("ffmpeg")
    if not ff:
        return False
    p = Path(path)
    tmp = p.with_name(p.stem + "_spd" + p.suffix)
    try:
        proc = await asyncio.create_subprocess_exec(
            ff, "-y", "-i", str(p), "-filter:a", f"atempo={speed:.3f}",
            "-b:a", bitrate, str(tmp),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(str(tmp), str(p))
            return True
    except Exception:
        pass
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    return False


# ─── Speech onset ────────────────────────────────────────────────────
#
# Relative, not absolute: the threshold is the recording's own noise floor
# plus a margin. A fixed silence level (ffmpeg silencedetect at -35 dB) reads
# a fan or a TV as "sound from the first moment" and so reports every answer
# as instant — measured, which is why this does not use it.

ONSET_RATE = 16000          # decode rate; plenty for a loudness envelope
ONSET_FRAME_S = 0.02        # 20 ms frames
ONSET_MARGIN_DB = 15.0      # speech must stand this far above the floor
ONSET_MIN_DB = -50.0        # ...and above this absolute level (digital silence)
ONSET_SUSTAIN_FRAMES = 3    # 60 ms, so a click or a breath is not speech
# The quietest tenth of the clip is taken as the room. That assumes at least a
# tenth of the recording is not voiced — true of speech, which is full of short
# gaps between words and syllables, but not of a steady tone or of noise alone,
# which is why those read as "nothing stands out".
_FLOOR_PERCENTILE = 0.10


def pcm_levels_db(pcm: bytes, *, rate: int = ONSET_RATE,
                  frame_s: float = ONSET_FRAME_S) -> list[float]:
    """Mono signed 16-bit PCM → RMS level per frame, in dBFS."""
    import math
    from array import array

    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    n = max(1, int(rate * frame_s))
    out = []
    for start in range(0, len(samples) - n + 1, n):
        chunk = samples[start:start + n]
        rms = math.sqrt(sum(x * x for x in chunk) / n)
        out.append(20 * math.log10(rms / 32768) if rms > 0 else -120.0)
    return out


def _speech_threshold(levels_db: list[float], margin_db: float, min_db: float) -> float:
    """The room's noise floor (its quietest tenth) plus a margin, never below
    an absolute level — shared by onset and pause detection so both agree on
    what counts as speech."""
    ranked = sorted(levels_db)
    floor = ranked[int(len(ranked) * _FLOOR_PERCENTILE)]
    return max(floor + margin_db, min_db)


def _speech_mask(levels_db: list[float], margin_db: float, min_db: float,
                 sustain: int) -> list[bool]:
    """Per frame: is this inside a sustained stretch of sound above the room?
    Runs shorter than ``sustain`` (a click, a breath) are not speech."""
    if len(levels_db) < sustain:
        return []
    threshold = _speech_threshold(levels_db, margin_db, min_db)
    loud = [lv >= threshold for lv in levels_db]
    speech = [False] * len(loud)
    i = 0
    while i < len(loud):
        if loud[i]:
            j = i
            while j < len(loud) and loud[j]:
                j += 1
            if j - i >= sustain:
                speech[i:j] = [True] * (j - i)
            i = j
        else:
            i += 1
    return speech


def speech_span(levels_db: list[float], *, frame_s: float = ONSET_FRAME_S,
                margin_db: float = ONSET_MARGIN_DB, min_db: float = ONSET_MIN_DB,
                sustain: int = ONSET_SUSTAIN_FRAMES) -> tuple[float, float] | None:
    """(start, end) seconds of the first and last sustained sound, or ``None``
    when nothing stands out. The time actually spent talking — without the
    wait before the first word or after the last, which would dilute pace."""
    speech = _speech_mask(levels_db, margin_db, min_db, sustain)
    if not any(speech):
        return None
    first = speech.index(True)
    last = len(speech) - 1 - speech[::-1].index(True)
    return round(first * frame_s, 3), round((last + 1) * frame_s, 3)


def pauses_from_levels(levels_db: list[float], *, frame_s: float = ONSET_FRAME_S,
                       margin_db: float = ONSET_MARGIN_DB,
                       min_db: float = ONSET_MIN_DB,
                       sustain: int = ONSET_SUSTAIN_FRAMES) -> list[float] | None:
    """Silent gaps (seconds) between stretches of speech, in order.

    Leading and trailing quiet is not a pause — only the gaps between the
    first and last sustained sound. ``None`` when no speech stands out of the
    room at all (steady noise, or nothing said): the pauses are then
    unmeasured, which a caller must not report as "no pauses".
    """
    speech = _speech_mask(levels_db, margin_db, min_db, sustain)
    if not any(speech):
        return None
    first = speech.index(True)
    last = len(speech) - 1 - speech[::-1].index(True)
    gaps, gap = [], 0
    for flag in speech[first:last + 1]:
        if flag:
            if gap:
                gaps.append(round(gap * frame_s, 3))
            gap = 0
        else:
            gap += 1
    return gaps


async def decode_levels(path: str | Path, *, timeout_s: float = 20.0) -> list[float] | None:
    """Per-frame loudness (dBFS) of a recording, or ``None`` if it cannot be read."""
    from pydub.utils import which

    ff = which("ffmpeg")
    if not ff:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ff, "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-ac", "1", "-ar", str(ONSET_RATE), "-f", "s16le", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            pcm, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None
    except Exception:
        return None
    if proc.returncode != 0 or not pcm:
        return None
    return await asyncio.to_thread(pcm_levels_db, pcm)


def onset_from_levels(levels_db: list[float], *, frame_s: float = ONSET_FRAME_S,
                      margin_db: float = ONSET_MARGIN_DB,
                      min_db: float = ONSET_MIN_DB,
                      sustain: int = ONSET_SUSTAIN_FRAMES) -> float | None:
    """Seconds before the first sustained sound clearly above the room.

    ``None`` when nothing stands out — silence throughout, steady noise
    throughout, or talking from start to end with no quiet to measure the room
    by. Never a made-up 0: a caller timing a reaction must be able to tell
    "instant" from "unmeasured".
    """
    if len(levels_db) < sustain:
        return None
    threshold = _speech_threshold(levels_db, margin_db, min_db)
    run = 0
    for i, level in enumerate(levels_db):
        run = run + 1 if level >= threshold else 0
        if run >= sustain:
            return round((i - sustain + 1) * frame_s, 3)
    return None


async def speech_onset(path: str | Path, *, timeout_s: float = 20.0) -> float | None:
    """Seconds of quiet before speech starts in a recording, or ``None``.

    ffmpeg decodes the file to mono PCM (:func:`decode_levels`); the level
    analysis is pure Python (:func:`onset_from_levels`). Fails open to
    ``None`` when ffmpeg is missing or errors. A hesitation sound ("um") is a
    sound: it counts as the onset.
    """
    levels = await decode_levels(path, timeout_s=timeout_s)
    return None if levels is None else onset_from_levels(levels)
