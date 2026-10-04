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

from emptyos.sdk.media.encode import video_args_resolved


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
    # Same reason as extract_last_frame: a leftover file from an earlier attempt
    # would make `out.exists()` report success for a frame this call never
    # produced. Milder here (a stale thumbnail is cosmetic, not a continuity
    # break) but it is the same lie.
    out.unlink(missing_ok=True)
    vf = f"scale={width}:-1"
    rc, _ = await _run(["ffmpeg", "-y", "-ss", str(at), "-i", str(src),
                        "-vframes", "1", "-vf", vf, "-q:v", "4", str(out)])
    if rc != 0 and not out.exists():
        await _run(["ffmpeg", "-y", "-i", str(src),
                    "-vframes", "1", "-vf", vf, "-q:v", "4", str(out)])
    return out.exists()


async def extract_last_frame(src: str | Path, out: str | Path) -> bool:
    """Grab the FINAL frame of a clip as a PNG. Returns True if a file landed.

    The handoff primitive for chained image-to-video: clip N's last frame
    becomes clip N+1's start image, so a scene longer than the model's clip
    ceiling is built from continuous generations instead of one short clip
    time-stretched to fit (which costs frame rate — a 2.28x stretch measured
    at ~11 unique fps against 24 generated).

    Seeks with ``-sseof`` (relative to end) rather than decoding the whole
    file, then falls back to a full decode for containers that cannot seek
    from the end.
    """
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Clear any previous attempt first. Callers write to a deterministic
    # per-scene name, so if both ffmpeg invocations fail and an older file is
    # still sitting there, `out.exists()` below is True and this returns success
    # holding a frame from a DIFFERENT generation. In chained I2V that plate
    # becomes the next clip's start image, so the failure surfaces as a scene
    # that silently continues the wrong shot — the shape .claude/rules/
    # dev-gotchas.md calls "a media path is not a media version".
    out.unlink(missing_ok=True)
    rc, _ = await _run(["ffmpeg", "-y", "-sseof", "-0.2", "-i", str(src),
                        "-update", "1", "-q:v", "2", str(out)])
    if rc != 0 or not out.exists() or out.stat().st_size == 0:
        # No end-relative seek: decode through and keep overwriting, so the
        # last frame written is the last frame of the clip.
        await _run(["ffmpeg", "-y", "-i", str(src),
                    "-update", "1", "-q:v", "2", str(out)])
    return out.exists() and out.stat().st_size > 0


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
    gpu: bool = False,
) -> bool:
    """Encode a numbered image sequence into an MP4 via the ffmpeg image2 demuxer.

    ``pattern`` is an ffmpeg printf-style path (e.g. ``/tmp/rec/f_%05d.png``);
    the caller owns the frame directory + naming. Output is yuv420p + faststart
    so it plays in a browser ``<video>``; odd frame dimensions are padded to
    even (libx264 yuv420p requirement). This is the ffmpeg side of the HTML→MP4
    recorder (``emptyos.sdk.media.html_record``), reusable by any pipeline that
    captures frames itself. Returns True if the file landed. Never raises.

    ``gpu=True`` requests NVENC and falls back to x264 when it isn't available.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # pix_fmt=None: the filter chain below already pins format=yuv420p.
    codec = await video_args_resolved(crf=crf, preset=preset, gpu=gpu, pix_fmt=None)
    rc, _ = await _run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-start_number", str(start_number),
        "-i", str(pattern),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        *codec,
        "-movflags", "+faststart", str(out),
    ])
    return out.exists() and out.stat().st_size > 0


async def concat_clips(
    clips: list[str | Path],
    out: str | Path,
    *,
    resolution: tuple[int, int] = (1280, 720),
    gpu: bool = False,
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

    # pix_fmt=None: `vf` above already pins format=yuv420p on every input.
    codec = await video_args_resolved(crf=23, preset="fast", gpu=gpu, pix_fmt=None)
    cmd += ["-filter_complex", ";".join(parts), *maps, *codec]
    if with_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", str(out)]

    rc, err = await _run(cmd)
    if rc != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg concat failed: {err}")


# Burned-in caption look shared by every assembler that takes an SRT.
_SRT_STYLE = "FontSize=13,PrimaryColour=&Hffffff&,OutlineColour=&H000000&,Outline=2,MarginV=25"


def _subtitles_filter(srt_path: str) -> str:
    """ffmpeg ``subtitles=`` filter for ``srt_path`` — the path escaping is the
    part that goes wrong on Windows (a drive-letter colon ends the option)."""
    srt_esc = str(srt_path).replace("\\", "/").replace(":", "\\:")
    return f"subtitles='{srt_esc}':force_style='{_SRT_STYLE}'"


async def still_to_clip(
    image: str | Path,
    out: str | Path,
    duration_s: float,
    *,
    resolution: tuple[int, int] = (1280, 720),
    fit: str = "contain",
    pad_color: str = "black",
    gpu: bool = False,
) -> None:
    """Hold one image for ``duration_s`` as a silent CFR clip at ``resolution``.

    The per-scene building block for a narrated video that mixes stills with
    recorded clips: every piece becomes a uniform clip first, so the final
    ``concat_clips`` never meets the mixed-size demuxer trap. Raises
    RuntimeError on ffmpeg failure.
    """
    if duration_s <= 0:
        raise RuntimeError(f"still_to_clip needs a positive duration, got {duration_s}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution
    codec = await video_args_resolved(crf=23, preset="fast", gpu=gpu, pix_fmt=None)
    rc, err = await _run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image),
        "-vf", _scene_fit_filter(w, h, fit, pad_color),
        "-an", *codec, "-t", f"{duration_s:.3f}",
        "-movflags", "+faststart", str(out),
    ])
    if rc != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg still→clip failed: {err}")


async def fit_clip_to_duration(
    clip: str | Path,
    out: str | Path,
    duration_s: float,
    *,
    resolution: tuple[int, int] = (1280, 720),
    pad_color: str = "black",
    gpu: bool = False,
) -> None:
    """Re-encode ``clip`` to exactly ``duration_s`` at ``resolution``, silent.

    A shorter clip holds its final frame (``tpad`` clone) rather than being
    time-stretched, so a recorded animation ends on its finished state while
    the narration for that scene plays out; a longer one is trimmed. Raises
    RuntimeError on ffmpeg failure.
    """
    if duration_s <= 0:
        raise RuntimeError(f"fit_clip_to_duration needs a positive duration, got {duration_s}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution
    vf = (
        f"{_scene_fit_filter(w, h, 'contain', pad_color)},"
        f"tpad=stop_mode=clone:stop_duration={duration_s:.3f}"
    )
    codec = await video_args_resolved(crf=23, preset="fast", gpu=gpu, pix_fmt=None)
    rc, err = await _run([
        "ffmpeg", "-y", "-i", str(clip), "-vf", vf,
        "-an", *codec, "-t", f"{duration_s:.3f}",
        "-movflags", "+faststart", str(out),
    ])
    if rc != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg clip fit failed: {err}")


async def mux_audio(
    video: str | Path,
    audio: str | Path,
    out: str | Path,
    *,
    srt_path: str = "",
    gpu: bool = False,
) -> None:
    """Lay ``audio`` under ``video`` (optionally burning in ``srt_path``).

    Without subtitles the video stream is copied untouched; with them it is
    re-encoded through the shared caption style. The output ends with the
    shorter stream. Raises RuntimeError on ffmpeg failure.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(video), "-i", str(audio)]
    if srt_path and Path(srt_path).exists():
        codec = await video_args_resolved(crf=23, preset="fast", gpu=gpu, pix_fmt=None)
        cmd += ["-vf", f"{_subtitles_filter(srt_path)},format=yuv420p", *codec]
    else:
        cmd += ["-c:v", "copy"]
    cmd += [
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(out),
    ]
    rc, err = await _run(cmd)
    if rc != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg mux failed: {err}")


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
    gpu: bool = False,
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
        fc.append(f"[cat]{_subtitles_filter(srt_path)}[v]")
        vmap = "[v]"
    else:
        vmap = "[cat]"

    # pix_fmt=None: _scene_fit_filter already pins format=yuv420p per scene.
    codec = await video_args_resolved(crf=23, preset="fast", gpu=gpu, pix_fmt=None)
    cmd += [
        "-filter_complex", ";".join(fc),
        "-map", vmap, "-map", f"{n}:a",
        *codec,
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
