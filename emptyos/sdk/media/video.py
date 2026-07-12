"""Video ffmpeg ops — the shared home for every video shell-out.

probe duration, extract a thumbnail frame, strip audio, concat clips into a
montage, and assemble an image+audio+subtitle slideshow. Consumers: the
podcast / MV staged pipelines (``assemble_video``) and ``apps/vlog``
(``probe_duration`` / ``extract_thumbnail`` / ``extract_audio`` /
``concat_clips``). App-specific concerns (logging, vault paths, int rounding)
stay in the app; the raw ffmpeg invocation lives here once.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


async def probe_duration(path: str | Path) -> float:
    """Media duration in seconds via ffprobe (0.0 if unknown / no ffprobe).

    Single source of truth for the ffprobe duration probe. Consumers:
    ``assemble_video`` (slideshow length vs audio) and ``apps/vlog`` (clip
    length). Never raises.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return float((stdout or b"0").decode().strip() or 0)
    except Exception:
        return 0.0


async def _run(cmd: list[str]) -> tuple[int, str]:
    """Run an ffmpeg/ffprobe subprocess; return (returncode, stderr-tail).
    Never raises — a missing binary returns (127, ...)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        return proc.returncode or 0, (stderr.decode("utf-8", "replace")[-400:] if stderr else "")
    except FileNotFoundError:
        return 127, "ffmpeg not found on PATH"
    except Exception as e:  # pragma: no cover - defensive
        return 1, str(e)[-400:]


async def has_audio_stream(path: str | Path) -> bool:
    """True when the media file has at least one audio stream. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return bool((stdout or b"").strip())
    except Exception:
        return False


async def extract_thumbnail(src: str | Path, out: str | Path, *, at: float = 1.0, width: int = 480) -> bool:
    """Grab one frame at ``at`` seconds as a JPEG (scaled to ``width`` px wide).
    Retries at t=0 for clips shorter than ``at``. Returns True if a file landed."""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale={width}:-1"
    rc, _ = await _run(["ffmpeg", "-y", "-ss", str(at), "-i", str(src),
                        "-vframes", "1", "-vf", vf, "-q:v", "4", str(out)])
    if rc != 0 and not out.exists():
        await _run(["ffmpeg", "-y", "-i", str(src),
                    "-vframes", "1", "-vf", vf, "-q:v", "4", str(out)])
    return out.exists()


async def extract_audio(src: str | Path, out: str | Path, *, bitrate: str = "128k") -> bool:
    """Extract the audio track to ``out`` (AAC). Returns False when the source
    has no usable audio stream (caller skips transcribe/montage audio)."""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    await _run(["ffmpeg", "-y", "-i", str(src), "-vn",
                "-acodec", "aac", "-b:a", bitrate, str(out)])
    return out.exists() and out.stat().st_size > 0


async def frames_to_mp4(
    pattern: str | Path,
    out: str | Path,
    *,
    fps: int = 24,
    start_number: int = 0,
    crf: int = 20,
    preset: str = "fast",
) -> bool:
    """Encode a numbered image sequence into an MP4 via the ffmpeg image2 demuxer.

    ``pattern`` is an ffmpeg printf-style path (e.g. ``/tmp/rec/f_%05d.png``);
    the caller owns the frame directory + naming. Output is yuv420p + faststart
    so it plays in a browser ``<video>``; odd frame dimensions are padded to
    even (libx264 yuv420p requirement). This is the ffmpeg side of the HTML→MP4
    recorder (``emptyos.sdk.media.html_record``), reusable by any pipeline that
    captures frames itself. Returns True if the file landed. Never raises.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rc, _ = await _run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-start_number", str(start_number),
        "-i", str(pattern),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-movflags", "+faststart", str(out),
    ])
    return out.exists() and out.stat().st_size > 0


async def concat_clips(
    clips: list[str | Path],
    out: str | Path,
    *,
    resolution: tuple[int, int] = (1280, 720),
) -> None:
    """Stitch ``clips`` (in order) into one MP4 via the ffmpeg concat filter.

    Each input is scaled+padded to a common frame so heterogeneous phone/webcam
    clips line up. Audio is kept only when *every* clip has an audio stream (the
    concat filter needs uniform streams); otherwise the output is video-only.
    Raises RuntimeError on ffmpeg failure.
    """
    clips = [Path(c) for c in clips if Path(c).exists()]
    if not clips:
        raise RuntimeError("no clips to concat")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution

    with_audio = True
    for c in clips:
        if not await has_audio_stream(c):
            with_audio = False
            break

    cmd: list[str] = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]

    n = len(clips)
    parts: list[str] = []
    concat_inputs = ""
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"
    )
    for i in range(n):
        parts.append(f"[{i}:v]{vf}[v{i}]")
        if with_audio:
            parts.append(f"[{i}:a]aresample=async=1:first_pts=0[a{i}]")
            concat_inputs += f"[v{i}][a{i}]"
        else:
            concat_inputs += f"[v{i}]"

    if with_audio:
        parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[v]")
        maps = ["-map", "[v]"]

    cmd += ["-filter_complex", ";".join(parts), *maps,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23"]
    if with_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", str(out)]

    rc, err = await _run(cmd)
    if rc != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg concat failed: {err}")


def _scene_fit_filter(w: int, h: int, fit: str, pad_color: str) -> str:
    """Per-image ffmpeg filter to a uniform WxH frame.

    ``contain`` (default) fits the whole image inside and pads the remainder —
    never crops, so information-bearing images (diagrams/charts) stay legible.
    ``cover`` fills the frame and crops the overflow — for full-bleed scenes.
    ``setsar=1`` keeps the concat filter happy across heterogeneous aspect
    ratios; ``fps=30`` + ``yuv420p`` make it a browser-playable CFR stream.
    """
    if fit == "cover":
        geom = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        geom = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={pad_color}")
    return f"{geom},setsar=1,fps=30,format=yuv420p"


async def assemble_video(
    scenes: list[dict],
    image_paths: list[Path | None],
    audio_path: str,
    srt_path: str,
    output_path: str,
    resolution: tuple[int, int] = (1280, 720),
    pad_color: str = "black",
):
    """Assemble scene images + audio (+ optional subtitles) into MP4.

    scenes: list of {start_ms, end_ms, fit?, ...} — ``fit`` is ``contain``
        (default, letterbox — never crop) or ``cover`` (full-bleed).
    image_paths: list of Path (or None for missing scenes)
    audio_path: path to stitched audio MP3
    srt_path: path to an SRT subtitle file, or ``""`` to skip subtitle burn-in
    output_path: where to write the MP4
    pad_color: letterbox fill for ``contain`` scenes

    Uses the concat *filter* (one ``-loop 1 -t`` input per scene, each scaled+
    padded to a uniform frame first), NOT the concat demuxer. The demuxer
    mistimes its ``duration`` directives when the scene images differ in size —
    which happens the moment a pipeline mixes wide diagrams/graphs with square
    AI scenes — collapsing the video stream to ~1/N its length and freezing on
    the last frame while audio plays on. Padding to a uniform frame BEFORE
    concat sidesteps that entirely. See `.claude/rules/dev-gotchas.md` § Media.
    """
    w, h = resolution
    valid = [(s, p) for s, p in zip(scenes, image_paths, strict=False) if p and p.exists()]
    if not valid:
        return

    # Ensure the slideshow covers the full audio (extend the last scene).
    audio_dur = await probe_duration(audio_path)
    durs = [max((s["end_ms"] - s["start_ms"]) / 1000.0, 1.5) for s, _ in valid]
    total_slide_dur = sum(durs)
    if audio_dur > 0 and total_slide_dur < audio_dur:
        durs[-1] += audio_dur - total_slide_dur + 1.0  # +1s safety margin

    cmd: list[str] = ["ffmpeg", "-y"]
    for dur, (_, img) in zip(durs, valid, strict=False):
        cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(img)]
    cmd += ["-i", audio_path]

    n = len(valid)
    fc = [
        f"[{i}:v]{_scene_fit_filter(w, h, (valid[i][0].get('fit') or 'contain'), pad_color)}[v{i}]"
        for i in range(n)
    ]
    fc.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat]")

    if srt_path and Path(srt_path).exists():
        srt_esc = srt_path.replace("\\", "/").replace(":", "\\:")
        fc.append(
            f"[cat]subtitles='{srt_esc}':force_style="
            f"'FontSize=13,PrimaryColour=&Hffffff&,OutlineColour=&H000000&,Outline=2,MarginV=25'[v]"
        )
        vmap = "[v]"
    else:
        vmap = "[cat]"

    cmd += [
        "-filter_complex", ";".join(fc),
        "-map", vmap, "-map", f"{n}:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-shortest",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[-300:]}")
