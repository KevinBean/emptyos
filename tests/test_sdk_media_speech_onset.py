"""speech_onset — seconds of quiet before speech, relative to the room.

The level analysis is pinned on synthetic frame levels; the real-ffmpeg tests
build audio with a gap we put there, including the case a fixed silence
threshold gets wrong: a noisy room, where "no silence found" must not read as
"spoke instantly".
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from emptyos.sdk.media.audio import (
    ONSET_FRAME_S,
    onset_from_levels,
    pcm_levels_db,
    speech_onset,
)

F = ONSET_FRAME_S


def _levels(quiet_frames, quiet_db, loud_frames, loud_db, tail_frames=0):
    return [quiet_db] * quiet_frames + [loud_db] * loud_frames + [quiet_db] * tail_frames


def test_onset_is_where_sound_rises_clear_of_the_room():
    assert onset_from_levels(_levels(50, -70, 40, -20)) == pytest.approx(50 * F)


def test_a_noisy_room_still_gives_the_real_onset_not_zero():
    # Room at -30 dB: an absolute -35 dB "silence" level would see no silence.
    assert onset_from_levels(_levels(60, -30, 40, -8)) == pytest.approx(60 * F)


def test_a_click_or_breath_shorter_than_the_sustain_is_not_speech():
    lv = [-70] * 20 + [-10] * 2 + [-70] * 30 + [-15] * 30
    assert onset_from_levels(lv) == pytest.approx(52 * F)


def test_nothing_standing_out_is_unknown():
    assert onset_from_levels([-70] * 100) is None      # silence throughout
    assert onset_from_levels([-30] * 100) is None      # steady noise throughout
    assert onset_from_levels([]) is None


def test_pcm_levels_reads_digital_silence_and_full_scale():
    import struct
    quiet = pcm_levels_db(b"\x00\x00" * 320)
    loud = pcm_levels_db(struct.pack("<h", 32767) * 320)
    assert quiet == [-120.0]
    assert loud[0] == pytest.approx(0.0, abs=0.01)


FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")


def _make(tmp_path, name, lead, main):
    clip = tmp_path / name
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", lead, "-f", "lavfi", "-i", main,
         "-filter_complex", "[0][1]concat=n=2:v=0:a=1", "-c:a", "libopus", str(clip)],
        check=True,
    )
    return clip


@needs_ffmpeg
def test_real_ffmpeg_finds_a_gap_we_put_there(tmp_path):
    clip = _make(tmp_path, "gap.webm", "anullsrc=r=48000:cl=mono:d=1.2",
                 "sine=frequency=440:sample_rate=48000:duration=1")
    assert asyncio.run(speech_onset(clip)) == pytest.approx(1.2, abs=0.1)


@needs_ffmpeg
def test_real_ffmpeg_in_a_noisy_room(tmp_path):
    # ~-31 dBFS of noise first — loud enough that silencedetect at -35 dB
    # reports no silence at all — then a tone at speaking level (~-12 dBFS).
    # (ffmpeg's default sine is only ~-21 dBFS, too close to this noise to be
    # a fair stand-in for a voice.)
    clip = _make(tmp_path, "noisy.webm",
                 "anoisesrc=r=48000:a=0.05:d=1.2",
                 "sine=frequency=440:sample_rate=48000:duration=1,volume=3")
    assert asyncio.run(speech_onset(clip)) == pytest.approx(1.2, abs=0.15)


@needs_ffmpeg
def test_real_ffmpeg_silence_or_steady_noise_throughout_is_unknown(tmp_path):
    silent = _make(tmp_path, "silent.webm", "anullsrc=r=48000:cl=mono:d=1",
                   "anullsrc=r=48000:cl=mono:d=1")
    noise = _make(tmp_path, "noise.webm", "anoisesrc=r=48000:a=0.05:d=1",
                  "anoisesrc=r=48000:a=0.05:d=1")
    assert asyncio.run(speech_onset(silent)) is None
    assert asyncio.run(speech_onset(noise)) is None


@needs_ffmpeg
def test_a_missing_file_is_unknown_not_instant(tmp_path):
    assert asyncio.run(speech_onset(tmp_path / "absent.webm")) is None


# ─── Pauses between stretches of speech ──────────────────────────────

from emptyos.sdk.media.audio import decode_levels, pauses_from_levels, speech_span  # noqa: E402


def test_pauses_are_the_gaps_between_speech_not_the_ends():
    lv = [-70] * 30 + [-15] * 40 + [-70] * 75 + [-15] * 40 + [-70] * 20 + [-15] * 30 + [-70] * 50
    assert pauses_from_levels(lv) == pytest.approx([75 * F, 20 * F])


def test_a_click_inside_a_pause_does_not_split_it():
    lv = [-15] * 30 + [-70] * 40 + [-10] * 1 + [-70] * 40 + [-15] * 30
    assert pauses_from_levels(lv) == pytest.approx([81 * F])


def test_no_speech_means_pauses_are_unmeasured_not_zero():
    assert pauses_from_levels([-70] * 200) is None
    assert pauses_from_levels([-30] * 200) is None      # steady noise
    assert pauses_from_levels([]) is None


def test_speech_span_excludes_the_wait_before_and_after():
    lv = [-70] * 100 + [-15] * 50 + [-70] * 20 + [-15] * 30 + [-70] * 150
    assert speech_span(lv) == pytest.approx((100 * F, 200 * F))
    assert speech_span([-70] * 50) is None


@needs_ffmpeg
def test_real_ffmpeg_measures_a_pause_we_put_there(tmp_path):
    clip = tmp_path / "pause.webm"
    tone = "sine=frequency=440:sample_rate=48000:duration=1,volume=3"
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=0.5",
         "-f", "lavfi", "-i", tone,
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1.5",
         "-f", "lavfi", "-i", tone,
         "-filter_complex", "[0][1][2][3]concat=n=4:v=0:a=1", "-c:a", "libopus", str(clip)],
        check=True,
    )
    levels = asyncio.run(decode_levels(clip))
    gaps = pauses_from_levels(levels)
    assert len(gaps) == 1 and gaps[0] == pytest.approx(1.5, abs=0.1)
