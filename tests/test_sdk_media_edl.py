"""emptyos.sdk.media.edl — frame-native EDL assembly and exact muxing.

Command-shape tests inject a fake runner; the real-ffmpeg tests build tiny
lavfi fixtures and skip when ffmpeg is absent.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from emptyos.sdk.media import edl

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


async def _no_args(**_kw):
    return []


def _capture():
    seen: dict = {}

    async def fake_run(*args):
        seen["args"] = list(args)
        if "-filter_complex_script" in args:
            graph = Path(args[args.index("-filter_complex_script") + 1])
            seen["graph"] = graph.read_text(encoding="utf-8")
        return True

    return seen, fake_run


def _assemble(edits, out, run, **kw):
    return asyncio.run(edl.assemble_frame_native_edl(
        edits, out, run=run, video_args=_no_args, **kw))


@pytest.mark.parametrize("name,extra", [
    ("missing.mp4", {}),                                 # source not on disk
    ("a.mp4", {"start_frame": 10, "end_frame": 10}),     # empty window
    ("a.mp4", {"source_kind": "card"}),                  # unknown kind
    ("a.mp4", {"framing": "zoom-200"}),                  # unknown framing
])
def test_malformed_edit_refused_without_running(tmp_path, name, extra):
    (tmp_path / "a.mp4").write_bytes(b"x")
    edit = {"source": tmp_path / name, "start_frame": 0, "end_frame": 10, **extra}
    seen, run = _capture()
    assert _assemble([edit], tmp_path / "o.mp4", run) is False
    assert "args" not in seen


def test_empty_edit_list_refused(tmp_path):
    seen, run = _capture()
    assert _assemble([], tmp_path / "o.mp4", run) is False
    assert "args" not in seen


def test_still_is_looped_at_native_fps_and_trimmed(tmp_path):
    still = tmp_path / "s.png"
    still.write_bytes(b"x")
    seen, run = _capture()
    assert _assemble([{"source": still, "source_kind": "still",
                       "start_frame": 0, "end_frame": 48}],
                     tmp_path / "o.mp4", run, fps=24) is True
    args = seen["args"]
    i = args.index("-loop")
    assert args[i:i + 4] == ["-loop", "1", "-framerate", "24"]
    assert "trim=start_frame=0:end_frame=48" in seen["graph"]
    assert "setpts=N/(24*TB)" in seen["graph"]
    assert not (tmp_path / "o.filtergraph.txt").exists()


def test_punch_in_crops_to_delivery_size(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    seen, run = _capture()
    _assemble([{"source": src, "start_frame": 0, "end_frame": 5,
                "framing": "punch-in-106"}], tmp_path / "o.mp4", run,
              width=1280, height=720)
    assert "force_original_aspect_ratio=increase" in seen["graph"]
    assert "crop=1280:720" in seen["graph"]


def test_probe_missing_file_is_zero(tmp_path):
    assert asyncio.run(edl.probe_video_stream(tmp_path / "none.mp4")) == (0.0, 0)


def _lavfi_video(path: Path, seconds: float, size="160x90"):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=24:duration={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def _lavfi_audio(path: Path, seconds: float):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", str(path)], check=True)


def _streams(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames",
                          "-show_entries", "stream=codec_type,nb_read_frames,duration",
                          "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


@needs_ffmpeg
def test_real_assembly_is_frame_exact_across_video_and_still(tmp_path):
    vid = tmp_path / "v.mp4"
    _lavfi_video(vid, 2)
    still = tmp_path / "s.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:size=100x100", "-frames:v", "1", str(still)], check=True)
    out = tmp_path / "cut.mp4"
    ok = asyncio.run(edl.assemble_frame_native_edl([
        {"source": vid, "start_frame": 5, "end_frame": 17},
        {"source": still, "source_kind": "still", "start_frame": 0, "end_frame": 30},
        {"source": vid, "start_frame": 0, "end_frame": 7, "framing": "punch-in-106"},
    ], out, width=160, height=90, fps=24))
    assert ok is True
    assert asyncio.run(edl.probe_video_stream(out))[1] == 12 + 30 + 7


@needs_ffmpeg
@pytest.mark.parametrize("audio_seconds", [1.5, 3.0])
def test_mux_exact_keeps_every_frame_and_matches_audio(tmp_path, audio_seconds):
    vid = tmp_path / "v.mp4"
    _lavfi_video(vid, 3)       # 72 frames: the mux must bound it to 48
    aud = tmp_path / "a.wav"
    _lavfi_audio(aud, audio_seconds)
    out = tmp_path / "m.mp4"
    assert asyncio.run(edl.mux_exact(vid, aud, out, frames=48, fps=24)) is True
    streams = _streams(out)
    assert int(streams["video"]["nb_read_frames"]) == 48
    assert float(streams["audio"]["duration"]) == pytest.approx(2.0, abs=0.03)


def test_mux_exact_refuses_bad_input(tmp_path):
    seen, run = _capture()
    assert asyncio.run(edl.mux_exact(tmp_path / "v", tmp_path / "a", tmp_path / "o.mp4",
                                     frames=48, run=run)) is False
    assert "args" not in seen


def test_import_does_not_load_the_kernel():
    code = ("import sys, emptyos.sdk.media.edl; "
            "print(any(m.startswith('emptyos.kernel') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
