"""WAV PCM-16 peak normalization for device-captured mic audio.

The in-pipeline *fixer* that complements ``review.py`` (the ffmpeg-based
analyser). A hardware mic's level is inherently variable — distance + loudness
swing it across takes — so a single analog gain can't keep it in range: loud
takes clip, quiet takes fall under whisper's internal VAD/confidence bar and
transcribe empty. Boosting the peak toward full scale *before* STT lifts a quiet
clip over that bar; a real-silence guard (RMS below a floor) returns ``None`` so
we never amplify room noise into a hallucinated turn.

Pure stdlib (``wave``/``array``/``math``) so it unit-tests without ffmpeg or a
daemon and runs cheaply on every device turn. The richer async ffprobe/astats
analysis stays in ``review.py``; this is the thing that actually changes the
bytes handed to ``listen()``.

First consumer: ``voice-assistant`` ``api_device_turn`` (the AtomS3R satellite),
behind ``[apps.voice-assistant] device_audio_normalize`` (default on).
"""

from __future__ import annotations

import array
import io
import math
import sys
import wave

DEFAULT_TARGET_PEAK_DBFS = -3.0    # boost so the loudest sample sits just under 0 dBFS
DEFAULT_SILENCE_RMS_DBFS = -55.0   # whole clip quieter than this ≈ real silence → reject
DEFAULT_MAX_GAIN = 20.0            # cap the boost (+26 dB) so near-silent noise can't blow up
_FULL_SCALE = 32767.0


_SILENCE_FLOOR_DB = -120.0   # finite (JSON-safe) stand-in for digital silence


def _dbfs(x: float) -> float:
    """Linear 16-bit amplitude → dBFS (0 dBFS = full scale). 0 → -120 (JSON-safe)."""
    if x <= 0:
        return _SILENCE_FLOOR_DB
    return 20.0 * math.log10(x / _FULL_SCALE)


def _clip16(v: int) -> int:
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


def normalize_wav_pcm16(
    data: bytes,
    *,
    target_peak_dbfs: float = DEFAULT_TARGET_PEAK_DBFS,
    silence_rms_dbfs: float = DEFAULT_SILENCE_RMS_DBFS,
    max_gain: float = DEFAULT_MAX_GAIN,
) -> tuple[bytes | None, dict]:
    """Peak-normalize a 16-bit PCM WAV after DC removal.

    Returns ``(normalized_wav_bytes, metrics)``. When the clip is below the
    silence floor (real silence / room noise) returns ``(None, metrics)`` — the
    caller should treat it as "too quiet" and skip STT. Any non-16-bit/unparseable
    input returns ``(data, {"skipped": ...})`` unchanged (fail-open — never break
    a turn over a format we don't normalize).

    ``metrics``: ``peak, rms, peak_dbfs, rms_dbfs, duration_s, sample_rate,
    channels, frames, gain`` (``gain`` is the linear factor applied, 1.0 if none).
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            sampwidth = w.getsampwidth()
            channels = w.getnchannels()
            rate = w.getframerate()
            nframes = w.getnframes()
            raw = w.readframes(nframes)
    except Exception as e:
        return data, {"skipped": f"unparseable: {e}"}

    if sampwidth != 2:
        return data, {"skipped": f"not 16-bit (sampwidth={sampwidth})"}

    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":   # WAV is little-endian; array uses native order
        samples.byteswap()

    n = len(samples)
    base = {
        "peak": 0, "rms": 0.0, "peak_dbfs": _SILENCE_FLOOR_DB, "rms_dbfs": _SILENCE_FLOOR_DB,
        "duration_s": round(nframes / rate, 2) if rate else 0.0,
        "sample_rate": rate, "channels": channels, "frames": nframes, "gain": 1.0,
    }
    if n == 0:
        return None, {**base, "skipped": "empty"}

    # DC removal — a codec startup offset would otherwise inflate peak + bias RMS.
    dc = int(round(sum(samples) / n))
    if dc:
        for i in range(n):
            samples[i] = _clip16(samples[i] - dc)

    peak = 0
    sumsq = 0.0
    for s in samples:
        a = -s if s < 0 else s
        if a > peak:
            peak = a
        sumsq += float(s) * float(s)
    rms = math.sqrt(sumsq / n)

    metrics = {
        **base, "peak": peak, "rms": round(rms, 1),
        "peak_dbfs": round(_dbfs(peak), 1), "rms_dbfs": round(_dbfs(rms), 1),
    }

    if peak == 0 or _dbfs(rms) < silence_rms_dbfs:
        return None, metrics

    target_peak = _FULL_SCALE * (10.0 ** (target_peak_dbfs / 20.0))
    gain = max(1.0, min(target_peak / peak, max_gain))   # only ever boost, capped
    if gain > 1.0:
        for i in range(n):
            samples[i] = _clip16(int(samples[i] * gain))
    metrics["gain"] = round(gain, 2)

    if sys.byteorder == "big":
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return out.getvalue(), metrics
