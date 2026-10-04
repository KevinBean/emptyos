"""Frame-native EDL assembly — the one editorial assembler every MV route shares.

Moved here from Music Studio's ``assembler.py`` so the external-generation
(Flow/Veo) route's ``scripts/mv/living_master.py`` and Music Studio's rough cut
assemble through the same code instead of two cadence rules drifting apart.

An *edit* is ``{"source": Path, "start_frame": int, "end_frame": int}`` with an
exclusive end, plus optional ``source_kind`` (``"video"`` default, or
``"still"`` for an image held at native fps) and ``framing`` (``"full"``
letterbox/pillarbox fit, or ``"punch-in-106"``). Every source timestamp is
rebuilt from its decoded frame index, so a clip carrying a stretched time base
cannot be converted to the delivery cadence twice.

``run`` and ``video_args`` are injectable so a caller can capture the ffmpeg
command in tests without running it.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from emptyos.sdk.media.encode import video_args_resolved

Runner = Callable[..., Awaitable[bool]]
VideoArgs = Callable[..., Awaitable[list[str]]]


async def run_ffmpeg(*args: str) -> bool:
    """Run one ffmpeg/ffprobe command; True on exit code 0."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def probe_video_stream(src: Path) -> tuple[float, int]:
    """Return container duration and decoded frame count via one ffprobe."""
    if not Path(src).is_file():
        return 0.0, 0
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-count_frames",
        "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_frames:format=duration",
        "-of", "json", str(src),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        payload = json.loads((stdout or b"{}").decode("utf-8", errors="ignore"))
        actual = float((payload.get("format") or {}).get("duration") or 0)
        source_frames = int(
            ((payload.get("streams") or [{}])[0]).get("nb_read_frames") or 0
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        actual = 0.0
        source_frames = 0
    return actual, source_frames


def _framing_filter(framing: str, width: int, height: int) -> str | None:
    if framing == "punch-in-106":
        punch_width = max(width, int(round(width * 1.067 / 2)) * 2)
        punch_height = max(height, int(round(height * 1.067 / 2)) * 2)
        return (
            f"scale={punch_width}:{punch_height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={int(width)}:{int(height)}:(iw-ow)/2:(ih-oh)/2"
        )
    if framing == "full":
        return (
            f"scale={int(width)}:{int(height)}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={int(width)}:{int(height)}:(ow-iw)/2:(oh-ih)/2"
        )
    return None


async def assemble_frame_native_edl(
    edits: list[dict],
    out: Path,
    *,
    width: int = 1280,
    height: int = 720,
    fps: int = 24,
    gpu: bool = False,
    run: Runner | None = None,
    video_args: VideoArgs | None = None,
) -> bool:
    """Assemble selected source-frame windows at one exact native cadence.

    ``source_kind="still"`` turns a reviewed image into an infinite native-fps
    hold inside this same assembler; the trim still gives it an exact finite
    frame window. That lets a rough cut and its later video replacements share
    one EDL instead of creating a parallel slideshow pipeline.

    Returns False without running anything when an edit is malformed: a missing
    source, ``end_frame <= start_frame``, or an unknown kind or framing.
    """
    run = run or run_ffmpeg
    video_args = video_args or video_args_resolved
    if not edits or fps <= 0 or width <= 0 or height <= 0:
        return False
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, edit in enumerate(edits):
        source = Path(edit.get("source") or "")
        try:
            start = max(0, int(edit.get("start_frame") or 0))
            end = int(edit.get("end_frame") or 0)
        except (TypeError, ValueError):
            return False
        if not source.is_file() or end <= start:
            return False
        source_kind = str(edit.get("source_kind") or "video").strip().lower()
        if source_kind == "still":
            inputs.extend([
                "-loop", "1",
                "-framerate", str(int(fps)),
                "-i", str(source),
            ])
        elif source_kind == "video":
            inputs.extend(["-i", str(source)])
        else:
            return False
        framing_filter = _framing_filter(str(edit.get("framing") or "full"), width, height)
        if framing_filter is None:
            return False
        label = f"v{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:v]trim=start_frame={start}:end_frame={end},"
            f"setpts=N/({int(fps)}*TB),"
            f"{framing_filter},"
            f"setsar=1,format=yuv420p[{label}]"
        )
    filters.append(
        "".join(labels)
        + f"concat=n={len(labels)}:v=1:a=0[vout]"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    # One input per shot is deliberate: the alternative (open each source once
    # and `split` it) makes the filtergraph buffer whole clips across branches,
    # which is far worse than decoding twice. What it does cost is command-line
    # length, and Windows caps that at 32767 characters — measured, a 120-shot
    # graph dies in CreateProcess with WinError 206 before ffmpeg ever runs.
    # The graph is ~60% of that budget, so it goes in a file instead.
    graph_path = out.with_suffix(".filtergraph.txt")
    graph_path.write_text(";\n".join(filters), encoding="utf-8")
    try:
        ok = await run(
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex_script", str(graph_path),
            "-map", "[vout]",
            *await video_args(crf=18, preset="medium", gpu=gpu),
            "-r", str(int(fps)),
            "-fps_mode", "cfr",
            "-an",
            str(out),
        )
    finally:
        try:
            graph_path.unlink()
        except OSError:
            pass
    return ok


async def mux_exact(
    video: Path,
    audio: Path,
    out: Path,
    *,
    frames: int,
    fps: int = 24,
    audio_bitrate: str = "320k",
    run: Runner | None = None,
) -> bool:
    """Attach audio to a frame-exact video without letting either stream cut the other.

    ``-shortest`` truncates the film when the audio is a few milliseconds
    shorter than the picture (the Spark living master lost its tail that way).
    Here the video is copied and bounded to exactly ``frames`` frames, and the
    audio is padded with silence and trimmed to the same duration.
    """
    run = run or run_ffmpeg
    if frames <= 0 or fps <= 0 or not Path(video).is_file() or not Path(audio).is_file():
        return False
    duration = f"{frames / fps:.6f}"
    out.parent.mkdir(parents=True, exist_ok=True)
    return await run(
        "ffmpeg", "-y", "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-frames:v", str(int(frames)),
        "-c:v", "copy",
        "-af", f"apad,atrim=end={duration}",
        "-c:a", "aac", "-b:a", audio_bitrate,
        str(out),
    )
