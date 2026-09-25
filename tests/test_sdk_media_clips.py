"""SDK media clip helpers against real ffmpeg: still_to_clip,
fit_clip_to_duration, mux_audio — plus generate_srt's single-narrator mode and
the recorder's viewport passthrough.

The ffmpeg cases skip where ffmpeg is absent (CI's bare container). Expected
durations are what the helper was ASKED for, never read back from the helper.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from emptyos.sdk.media import (
    fit_clip_to_duration,
    generate_srt,
    mux_audio,
    record_html_to_mp4,
    still_to_clip,
)
from emptyos.sdk.media import html_record

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not on PATH",
)


async def _ffmpeg(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    assert proc.returncode == 0, err.decode(errors="replace")


async def _probe(path: Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    data = json.loads(out or b"{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "width": video.get("width"),
        "height": video.get("height"),
        "audio": bool(audio),
    }


@needs_ffmpeg
@pytest.mark.asyncio
async def test_still_to_clip_holds_for_the_requested_duration(tmp_path):
    img = tmp_path / "frame.png"
    await _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=640x480", "-frames:v", "1", str(img))
    out = tmp_path / "still.mp4"
    await still_to_clip(img, out, 1.5, resolution=(1280, 720))
    info = await _probe(out)
    assert info["duration"] == pytest.approx(1.5, abs=0.1)
    assert (info["width"], info["height"]) == (1280, 720)
    assert info["audio"] is False


async def _mean_rgb(path: Path, at_s: float) -> tuple[int, int, int]:
    """Whole-frame average colour at ``at_s`` (the frame scaled to one pixel)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-ss", f"{at_s}", "-i", str(path), "-frames:v", "1",
        "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    assert len(out) >= 3, f"no frame decoded at {at_s}s"
    return out[0], out[1], out[2]


@needs_ffmpeg
@pytest.mark.asyncio
async def test_fit_extends_a_short_clip_by_holding_its_last_frame(tmp_path):
    # 0.25 s red then 0.25 s blue. Held (tpad clone): blue at 0.6 s and 1.8 s.
    # Black padding would be dark at 1.8 s; a 4x time-stretch would still be
    # red at 0.6 s — both are wrong implementations this must reject.
    src = tmp_path / "short.mp4"
    await _ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=320x240:d=0.25:r=30",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=0.25:r=30",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
        "-pix_fmt", "yuv420p", str(src),
    )
    out = tmp_path / "long.mp4"
    await fit_clip_to_duration(src, out, 2.0, resolution=(1280, 720))
    info = await _probe(out)
    assert info["duration"] == pytest.approx(2.0, abs=0.1)
    assert (info["width"], info["height"]) == (1280, 720)
    r, g, b = await _mean_rgb(out, 0.1)
    assert r > b + 40, f"expected the red opening at 0.1 s, got {(r, g, b)}"
    for t in (0.6, 1.8):
        r, g, b = await _mean_rgb(out, t)
        assert b > 90 and b > r + 40, f"expected the held blue final frame at {t} s, got {(r, g, b)}"


@needs_ffmpeg
@pytest.mark.asyncio
async def test_fit_trims_a_long_clip(tmp_path):
    src = tmp_path / "long.mp4"
    await _ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=3:r=30", "-pix_fmt", "yuv420p", str(src))
    out = tmp_path / "trimmed.mp4"
    await fit_clip_to_duration(src, out, 1.0)
    assert (await _probe(out))["duration"] == pytest.approx(1.0, abs=0.1)


@needs_ffmpeg
@pytest.mark.asyncio
async def test_mux_lays_audio_and_ends_with_the_shorter_stream(tmp_path):
    picture = tmp_path / "picture.mp4"
    await _ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=3:r=30", "-pix_fmt", "yuv420p", str(picture))
    voice = tmp_path / "voice.wav"
    await _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", str(voice))
    srt = tmp_path / "c.srt"
    generate_srt([{"start_ms": 0, "end_ms": 1000, "text": "Hello there."}], str(srt), speaker_labels=False)
    out = tmp_path / "final.mp4"
    await mux_audio(picture, voice, out, srt_path=str(srt))
    info = await _probe(out)
    assert info["audio"] is True
    assert (info["width"], info["height"]) == (320, 240), "the picture must survive the mux"
    assert info["duration"] == pytest.approx(1.2, abs=0.15)


@needs_ffmpeg
@pytest.mark.asyncio
async def test_mux_without_captions_copies_the_picture(tmp_path):
    picture = tmp_path / "picture.mp4"
    await _ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=3:r=30", "-pix_fmt", "yuv420p", str(picture))
    voice = tmp_path / "voice.wav"
    await _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", str(voice))
    out = tmp_path / "copied.mp4"
    await mux_audio(picture, voice, out)
    info = await _probe(out)
    assert info["audio"] is True
    assert (info["width"], info["height"]) == (320, 240)
    assert info["duration"] == pytest.approx(1.2, abs=0.15)


@needs_ffmpeg
@pytest.mark.asyncio
async def test_clip_helpers_raise_on_bad_input(tmp_path):
    with pytest.raises(RuntimeError):
        await still_to_clip(tmp_path / "missing.png", tmp_path / "x.mp4", 1.0)
    with pytest.raises(RuntimeError):
        await fit_clip_to_duration(tmp_path / "missing.mp4", tmp_path / "y.mp4", 1.0)
    with pytest.raises(RuntimeError):
        await mux_audio(tmp_path / "missing.mp4", tmp_path / "missing.wav", tmp_path / "z.mp4")


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [0, -1.5])
async def test_clip_helpers_refuse_a_non_positive_duration(tmp_path, seconds):
    # Refused before ffmpeg runs, so this needs no ffmpeg: `-t 0.000` can write
    # a zero-length file that `out.exists()` would otherwise accept.
    with pytest.raises(RuntimeError, match="positive duration"):
        await still_to_clip(tmp_path / "a.png", tmp_path / "a.mp4", seconds)
    with pytest.raises(RuntimeError, match="positive duration"):
        await fit_clip_to_duration(tmp_path / "b.mp4", tmp_path / "b2.mp4", seconds)


def test_srt_single_narrator_has_no_host_labels(tmp_path):
    out = tmp_path / "n.srt"
    generate_srt([{"start_ms": 0, "end_ms": 1500, "text": "Just the words."}], str(out), speaker_labels=False)
    text = out.read_text(encoding="utf-8")
    assert "Host" not in text
    assert "00:00:00,000 --> 00:00:01,500\nJust the words." in text


def test_srt_default_keeps_podcast_host_labels(tmp_path):
    out = tmp_path / "p.srt"
    generate_srt([{"start_ms": 0, "end_ms": 1000, "speaker": "B", "text": "Hi."}], str(out))
    assert "Host B: Hi." in out.read_text(encoding="utf-8")


class _ViewportPlugin:
    def __init__(self):
        self.navigate_kwargs = []

    async def available(self):
        return True

    async def navigate(self, url, **kwargs):
        self.navigate_kwargs.append(kwargs)
        return {"url": url}

    async def eval(self, expression, *, context_id=None):
        return {"value": None}

    async def screenshot(self, *, path=None, context_id=None, **kw):
        Path(path).write_bytes(b"\x89PNG\r\n")
        return {"path": path}

    async def close_context(self, context_id=None):
        return {"ok": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport,expected", [("1280x720", "1280x720"), (None, None)])
async def test_recorder_passes_viewport_only_when_given(tmp_path, monkeypatch, viewport, expected):
    async def fake_frames_to_mp4(pattern, out, *, fps=24, **kw):
        Path(out).write_bytes(b"\x00")
        return True

    monkeypatch.setattr(html_record, "frames_to_mp4", fake_frames_to_mp4)
    page = tmp_path / "scene.html"
    page.write_text("<html></html>", encoding="utf-8")
    plugin = _ViewportPlugin()
    res = await record_html_to_mp4(plugin, page, tmp_path / "o.mp4", fps=2, duration_s=1.0,
                                   settle_ms=0, viewport=viewport)
    assert res["ok"] is True
    assert plugin.navigate_kwargs[0].get("viewport") == expected
    # No viewport requested → the kwarg is not sent at all, so a plugin (or a
    # test double) whose navigate() predates the parameter is not handed it.
    assert ("viewport" in plugin.navigate_kwargs[0]) is (viewport is not None)
