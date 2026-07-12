"""Unit tests for emptyos.sdk.media.normalize — pure, no daemon, no ffmpeg.

Pins the device-audio fix: a quiet hardware-mic clip (the AtomS3R sent peak
~2081) must be boosted toward full scale before STT, and real silence must be
rejected so we never amplify room noise into a hallucinated turn.
"""

import io
import math
import struct
import wave

import array
import pytest

from emptyos.sdk.media.normalize import normalize_wav_pcm16


def _wav(samples, rate=16000, channels=1, sampwidth=2):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        if sampwidth == 2:
            w.writeframes(struct.pack("<%dh" % len(samples), *samples))
        else:
            w.writeframes(bytes(samples))
    return b.getvalue()


def _sine(peak, n=16000, rate=16000, freq=440):
    return [int(peak * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]


def _peak_of(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    return max(abs(x) for x in a)


def test_quiet_clip_boosted_to_target():
    # Mirrors the device: peak ~2000 → should be lifted near full scale (~-3 dBFS).
    out, m = normalize_wav_pcm16(_wav(_sine(2000)))
    assert out is not None, "a quiet-but-real clip must not be rejected"
    assert m["gain"] > 5.0, f"expected a real boost, got gain {m['gain']}"
    assert 20000 <= _peak_of(out) <= 32767, "normalized peak should sit near full scale"


def test_real_silence_rejected():
    out, m = normalize_wav_pcm16(_wav(_sine(20)))   # ~ -67 dBFS rms
    assert out is None, "near-silent audio must be rejected (skip STT)"
    assert m["rms_dbfs"] < -55.0


def test_loud_clip_not_attenuated():
    # Already-hot audio: gain is capped at 1.0 (we only ever boost, never quieten).
    out, m = normalize_wav_pcm16(_wav(_sine(30000)))
    assert out is not None
    assert m["gain"] == pytest.approx(1.0)


def test_gain_capped():
    # Very quiet but above the floor → gain must not exceed max_gain.
    out, m = normalize_wav_pcm16(_wav(_sine(400)), max_gain=20.0)
    if out is not None:
        assert m["gain"] <= 20.0


def test_dc_offset_removed():
    # A constant offset (codec startup) must not survive into the output.
    biased = [12000 + v for v in _sine(1500)]
    out, m = normalize_wav_pcm16(_wav(biased))
    assert out is not None
    with wave.open(io.BytesIO(out), "rb") as w:
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    assert abs(sum(a) / len(a)) < 500, "DC mean should be removed"


def test_non_16bit_passthrough():
    # 8-bit input is left unchanged (fail-open, never break a turn).
    eight_bit = _wav(list(range(0, 256)) * 10, sampwidth=1)
    out, m = normalize_wav_pcm16(eight_bit)
    assert out == eight_bit
    assert "skipped" in m


def test_garbage_passthrough():
    out, m = normalize_wav_pcm16(b"not a wav at all")
    assert out == b"not a wav at all"
    assert "skipped" in m


def test_metrics_shape():
    _, m = normalize_wav_pcm16(_wav(_sine(8000)))
    for k in ("peak", "rms", "peak_dbfs", "rms_dbfs", "duration_s", "sample_rate", "channels", "gain"):
        assert k in m
    assert m["sample_rate"] == 16000
    assert m["duration_s"] == pytest.approx(1.0, abs=0.05)
