"""Video assembly — images + audio + subtitles → MP4 via ffmpeg."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path


async def assemble_video(
    scenes: list[dict],
    image_paths: list[Path | None],
    audio_path: str,
    srt_path: str,
    output_path: str,
    resolution: tuple[int, int] = (1280, 720),
):
    """Assemble scene images + audio + subtitles into MP4.

    scenes: list of {start_ms, end_ms, ...}
    image_paths: list of Path (or None for missing scenes)
    audio_path: path to stitched audio MP3
    srt_path: path to SRT subtitle file
    output_path: where to write the MP4
    """
    w, h = resolution
    valid = [(s, p) for s, p in zip(scenes, image_paths, strict=False) if p and p.exists()]
    if not valid:
        return

    # Get audio duration to ensure slideshow covers all audio
    audio_dur = 0
    try:
        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            audio_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await probe.communicate()
        audio_dur = float(stdout.decode().strip()) if stdout else 0
    except Exception:
        pass

    # Build concat file for ffmpeg
    concat_file = Path(tempfile.gettempdir()) / f"concat_{int(time.time())}.txt"
    lines = []
    total_slide_dur = 0
    for scene, img in valid:
        dur = max((scene["end_ms"] - scene["start_ms"]) / 1000.0, 1.5)
        lines.append(f"file '{str(img)}'")
        lines.append(f"duration {dur}")
        total_slide_dur += dur

    # If slideshow is shorter than audio, extend last image to cover remaining audio
    if audio_dur > 0 and total_slide_dur < audio_dur:
        extra = audio_dur - total_slide_dur + 1.0  # +1s safety margin
        lines.append(f"file '{str(valid[-1][1])}'")
        lines.append(f"duration {extra}")

    # Repeat last for ffmpeg concat demuxer
    lines.append(f"file '{str(valid[-1][1])}'")
    concat_file.write_text("\n".join(lines))

    srt_esc = srt_path.replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-i",
        audio_path,
        "-vf",
        (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"subtitles='{srt_esc}':force_style="
            f"'FontSize=13,PrimaryColour=&Hffffff&,OutlineColour=&H000000&,Outline=2,MarginV=25'"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    concat_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[-300:]}")
