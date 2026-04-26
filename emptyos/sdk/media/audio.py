"""Audio stitching — concatenate audio segments into one file."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def stitch_audio(
    seg_paths: list[Path],
    output_dir: Path,
    gap_ms: int = 400,
    tail_ms: int = 1500,
    bitrate: str = "192k",
    filename_prefix: str = "stitched",
) -> str:
    """Concatenate audio files into one MP3. Returns relative filename."""
    import time

    def _do():
        from pydub import AudioSegment
        combined = AudioSegment.empty()
        gap = AudioSegment.silent(duration=gap_ms)
        for p in seg_paths:
            try:
                seg = AudioSegment.from_file(str(p))
                if len(combined) > 0:
                    combined += gap
                combined += seg
            except Exception:
                continue
        if len(combined) == 0:
            return ""
        combined += AudioSegment.silent(duration=tail_ms)
        out_name = f"{filename_prefix}_{int(time.time())}.mp3"
        out_path = output_dir / out_name
        combined.export(str(out_path), format="mp3", bitrate=bitrate)
        return out_name

    return await asyncio.to_thread(_do)
