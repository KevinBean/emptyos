"""Unit tests for the media output self-review gate (emptyos.sdk.media.review).

Pure-function tests over canned ffprobe/ffmpeg output — no ffmpeg required, so
they run anywhere. One integration test exercises the real subprocess path but
self-skips when ffmpeg/ffprobe aren't on PATH (and asserts fail-open either way).
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from emptyos.sdk.media import review as R
from emptyos.sdk.media import MediaVerdict, review_audio

# --- canned tool output ----------------------------------------------------

PROBE_OK = '{"streams":[{"codec_type":"audio","duration":"12.34"}],"format":{"duration":"12.34"}}'
PROBE_NO_AUDIO = '{"streams":[{"codec_type":"video","duration":"5.0"}],"format":{"duration":"5.0"}}'
PROBE_ZERO_DUR = '{"streams":[{"codec_type":"audio"}],"format":{"duration":"0"}}'
PROBE_GARBAGE = 'not json at all'

ASTATS_OK = """[Parser_astats_0 @ 0x55] Channel: 1
[Parser_astats_0 @ 0x55] RMS level dB: -25.0
[Parser_astats_0 @ 0x55] Overall
[Parser_astats_0 @ 0x55] Peak level dB: -3.5
[Parser_astats_0 @ 0x55] RMS level dB: -22.1
[Parser_astats_0 @ 0x55] Flat factor: 0.000000
"""
ASTATS_SILENT = """[Parser_astats_0 @ 0x55] Overall
[Parser_astats_0 @ 0x55] Peak level dB: -inf
[Parser_astats_0 @ 0x55] RMS level dB: -inf
[Parser_astats_0 @ 0x55] Flat factor: 0.000000
"""
ASTATS_CLIP = """[Parser_astats_0 @ 0x55] Overall
[Parser_astats_0 @ 0x55] Peak level dB: 0.000000
[Parser_astats_0 @ 0x55] RMS level dB: -8.0
[Parser_astats_0 @ 0x55] Flat factor: 12.0
"""
ASTATS_QUIET = """[Parser_astats_0 @ 0x55] Overall
[Parser_astats_0 @ 0x55] Peak level dB: -40.0
[Parser_astats_0 @ 0x55] RMS level dB: -55.0
[Parser_astats_0 @ 0x55] Flat factor: 0.0
"""

# --- _parse_ffprobe --------------------------------------------------------

def test_parse_ffprobe_ok():
    p = R._parse_ffprobe(PROBE_OK)
    assert p["has_audio"] is True
    assert p["duration"] == pytest.approx(12.34)


def test_parse_ffprobe_no_audio():
    p = R._parse_ffprobe(PROBE_NO_AUDIO)
    assert p["has_audio"] is False


def test_parse_ffprobe_garbage_is_fail_open():
    # unparseable → has_audio None (not False) so the caller doesn't treat
    # "couldn't tell" as "no audio".
    p = R._parse_ffprobe(PROBE_GARBAGE)
    assert p["has_audio"] is None
    assert p["duration"] is None


# --- _parse_astats ---------------------------------------------------------

def test_parse_astats_reads_overall_block():
    s = R._parse_astats(ASTATS_OK)
    assert s["rms_db"] == pytest.approx(-22.1)   # Overall, not the -25 channel
    assert s["peak_db"] == pytest.approx(-3.5)


def test_parse_astats_silent_is_neg_inf():
    s = R._parse_astats(ASTATS_SILENT)
    assert s["rms_db"] == float("-inf")
    assert s["peak_db"] == float("-inf")


def test_parse_astats_empty():
    s = R._parse_astats("")
    assert s == {"rms_db": None, "peak_db": None, "flat_factor": None}


# --- _audio_verdict (pure decision) ----------------------------------------

def test_verdict_good_audio_passes_clean():
    v = R._audio_verdict(R._parse_ffprobe(PROBE_OK), R._parse_astats(ASTATS_OK))
    assert v.ok is True
    assert v.hard == ()
    assert v.soft == ()


def test_verdict_silent_is_hard():
    v = R._audio_verdict(R._parse_ffprobe(PROBE_OK), R._parse_astats(ASTATS_SILENT))
    assert v.ok is False
    assert any("silent" in h for h in v.hard)


def test_verdict_no_audio_stream_is_hard():
    v = R._audio_verdict(R._parse_ffprobe(PROBE_NO_AUDIO), {"rms_db": None, "peak_db": None})
    assert v.ok is False
    assert any("no audio stream" in h for h in v.hard)


def test_verdict_too_short_is_hard():
    v = R._audio_verdict(R._parse_ffprobe(PROBE_OK), R._parse_astats(ASTATS_OK),
                         min_duration_s=1.0)
    assert v.ok is True  # 12.34s clears 1.0s
    v2 = R._audio_verdict({"has_audio": True, "duration": 0.3}, R._parse_astats(ASTATS_OK),
                          min_duration_s=1.0)
    assert v2.ok is False
    assert any("duration" in h for h in v2.hard)


def test_verdict_clipping_is_soft_not_hard():
    v = R._audio_verdict(R._parse_ffprobe(PROBE_OK), R._parse_astats(ASTATS_CLIP))
    assert v.ok is True  # clipping degrades but ships
    assert any("clipping" in s for s in v.soft)


def test_verdict_low_level_is_soft():
    v = R._audio_verdict(R._parse_ffprobe(PROBE_OK), R._parse_astats(ASTATS_QUIET))
    assert v.ok is True
    assert any("low level" in s for s in v.soft)


def test_verdict_unknown_values_never_penalised():
    # all None → nothing to fault → ok
    v = R._audio_verdict({"has_audio": None, "duration": None},
                         {"rms_db": None, "peak_db": None, "flat_factor": None})
    assert v.ok is True
    assert v.hard == ()


# --- review_audio (async wrapper, fail-open) -------------------------------

@pytest.mark.asyncio
async def test_review_audio_missing_file_is_hard(tmp_path):
    v = await review_audio(tmp_path / "nope.mp3")
    assert v.ok is False
    assert any("missing" in h for h in v.hard)


@pytest.mark.asyncio
async def test_review_audio_empty_file_is_hard(tmp_path):
    f = tmp_path / "empty.mp3"
    f.write_bytes(b"")
    v = await review_audio(f)
    assert v.ok is False


def _write_wav(path: Path, *, seconds: float, amplitude: int, rate: int = 8000):
    n = int(seconds * rate)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(struct.pack("<h", amplitude if (k % 2) else -amplitude) for k in range(n))
        w.writeframes(frames)


@pytest.mark.asyncio
async def test_review_audio_real_silent_fails_when_ffmpeg_present(tmp_path):
    from pydub.utils import which
    f = tmp_path / "silent.wav"
    _write_wav(f, seconds=2.0, amplitude=0)
    v = await review_audio(f, min_duration_s=1.0)
    assert isinstance(v, MediaVerdict)
    if which("ffmpeg") and which("ffprobe"):
        # a truly silent track must hard-fail
        assert v.ok is False
        assert any("silent" in h for h in v.hard)
    else:
        # fail-open: no tools → ok with a skip/partial note, never a false block
        assert v.ok is True


@pytest.mark.asyncio
async def test_review_audio_real_tone_passes_when_ffmpeg_present(tmp_path):
    from pydub.utils import which
    f = tmp_path / "tone.wav"
    _write_wav(f, seconds=2.0, amplitude=8000)  # ~ -12 dBFS square-ish
    v = await review_audio(f, min_duration_s=1.0)
    assert v.ok is True  # audible tone — passes (with or without ffmpeg)
    if not (which("ffmpeg") and which("ffprobe")):
        assert any("partial" in s or "skipped" in s for s in v.soft)

# --- frame-sampling slice (freezedetect + blackdetect) ----------------------

FREEZE_FULL = """[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 0.04
[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 11.5
[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 11.54
"""
FREEZE_OPEN_ENDED = """[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 1.0
"""
BLACK_PARTIAL = """[blackdetect @ 0x2] black_start:0 black_end:7.0 black_duration:7.0
"""


def test_parse_freeze_full_coverage():
    cov = R._parse_freeze_black(FREEZE_FULL, 12.0)
    assert cov["freeze_s"] == pytest.approx(11.5)
    assert cov["black_s"] == 0.0


def test_parse_freeze_open_ended_counts_to_eof():
    # frozen-at-EOF emits a start with no duration — counts to the clip end
    cov = R._parse_freeze_black(FREEZE_OPEN_ENDED, 12.0)
    assert cov["freeze_s"] == pytest.approx(11.0)


def test_parse_black_partial():
    cov = R._parse_freeze_black(BLACK_PARTIAL, 12.0)
    assert cov["black_s"] == pytest.approx(7.0)
    assert cov["freeze_s"] == 0.0


def test_parse_freeze_black_empty_is_zero():
    assert R._parse_freeze_black("", 12.0) == {"freeze_s": 0.0, "black_s": 0.0}


def test_frame_findings_frozen_clip_is_hard():
    hard, soft = R._frame_findings(12.0, 11.5, 0.0)
    assert any("frozen" in h for h in hard)
    assert not soft


def test_frame_findings_half_black_is_soft():
    hard, soft = R._frame_findings(12.0, 0.0, 7.0)
    assert not hard
    assert any("black" in s for s in soft)


def test_frame_findings_clean_clip_is_silent():
    assert R._frame_findings(12.0, 0.5, 0.0) == ([], [])


def test_frame_findings_zero_duration_never_divides():
    assert R._frame_findings(0.0, 1.0, 1.0) == ([], [])


@pytest.mark.asyncio
async def test_review_video_real_static_clip_fails_when_ffmpeg_present(tmp_path):
    """Synthesize a fully static clip with ffmpeg and assert the frame-sampling
    pass hard-fails it; self-skips (asserting fail-open) without ffmpeg."""
    import asyncio
    from pydub.utils import which
    from emptyos.sdk.media import review_video

    ffmpeg = which("ffmpeg")
    if not (ffmpeg and which("ffprobe")):
        f = tmp_path / "missing-tools.mp4"
        f.write_bytes(b"x" * 64)
        v = await review_video(f, require_audio=False)
        assert v.ok is True  # fail-open
        return

    f = tmp_path / "static.mp4"
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=4:r=12",
        "-pix_fmt", "yuv420p", str(f),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    v = await review_video(f, require_audio=False)
    assert v.ok is False
    assert any("frozen" in h for h in v.hard)


@pytest.mark.asyncio
async def test_review_video_real_moving_clip_passes_when_ffmpeg_present(tmp_path):
    import asyncio
    from pydub.utils import which
    from emptyos.sdk.media import review_video

    ffmpeg = which("ffmpeg")
    if not (ffmpeg and which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")

    f = tmp_path / "moving.mp4"
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=s=64x64:d=4:r=12",
        "-pix_fmt", "yuv420p", str(f),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    v = await review_video(f, require_audio=False)
    assert v.ok is True
    assert not v.hard
