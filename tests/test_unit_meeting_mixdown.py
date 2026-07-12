"""Unit tests for the pure meeting-capture mixdown (no daemon, no real audio).

Covers emptyos.sdk.audio.mix_streams_to_wav — the function the meeting-capture
plugin uses to combine loopback + mic PCM into one WAV. Kept pure so this runs
in plain pytest without the kernel or an audio device.
"""
from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from emptyos.sdk.audio import mix_streams_to_wav


def _read(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        rate = w.getframerate()
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return rate, frames


def test_single_stream_roundtrips():
    src = np.array([0, 100, -100, 32000, -32000], dtype=np.int16)
    rate, out = _read(mix_streams_to_wav([src], samplerate=16000))
    assert rate == 16000
    np.testing.assert_array_equal(out, src)


def test_two_streams_sum():
    a = np.array([1000, 2000, 3000], dtype=np.int16)
    b = np.array([500, 500, 500], dtype=np.int16)
    _, out = _read(mix_streams_to_wav([a, b]))
    np.testing.assert_array_equal(out, np.array([1500, 2500, 3500], dtype=np.int16))


def test_shorter_stream_is_zero_padded():
    a = np.array([100, 200, 300, 400], dtype=np.int16)
    b = np.array([10, 20], dtype=np.int16)  # mic dropped out early
    _, out = _read(mix_streams_to_wav([a, b]))
    np.testing.assert_array_equal(out, np.array([110, 220, 300, 400], dtype=np.int16))


def test_summing_clips_not_wraps():
    a = np.array([30000, -30000], dtype=np.int16)
    b = np.array([30000, -30000], dtype=np.int16)
    _, out = _read(mix_streams_to_wav([a, b]))
    # 60000 must clip to 32767, not wrap to a negative int16
    np.testing.assert_array_equal(out, np.array([32767, -32768], dtype=np.int16))


def test_accepts_raw_bytes():
    a = np.array([7, 8, 9], dtype="<i2").tobytes()
    _, out = _read(mix_streams_to_wav([a]))
    np.testing.assert_array_equal(out, np.array([7, 8, 9], dtype=np.int16))


def test_empty_input_yields_valid_empty_wav():
    for streams in ([], [None], [b""], [np.array([], dtype=np.int16)]):
        rate, out = _read(mix_streams_to_wav(streams, samplerate=16000))
        assert rate == 16000
        assert out.size == 0


def test_custom_samplerate_preserved():
    rate, _ = _read(mix_streams_to_wav([np.array([1, 2], dtype=np.int16)], samplerate=48000))
    assert rate == 48000
