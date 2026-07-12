"""Audio utilities — stitch segments, change tempo."""

from __future__ import annotations

import asyncio
import os
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


async def clean_audio(
    path: str | Path,
    *,
    highpass: int = 60,
    lowpass: int | None = None,
    denoise: bool = False,
    bitrate: str = "192k",
) -> bool:
    """De-rumble (and optionally denoise) an audio file IN PLACE, pitch-neutral.

    By default only applies a ``highpass`` to strip sub-bass rumble — a linear
    filter that cannot introduce artifacts. Spectral denoise (``afftdn``) is
    OPT-IN via ``denoise=True`` because, while it cleans real acoustic hiss well,
    it can produce audible "musical noise" warble when run against the *synthetic*
    hiss of cloned-reference TTS (VibeVoice). Returns True when applied, False on
    no-op (no filters) or failure (ffmpeg missing / encode error) — the original
    file is always left intact on failure. ffmpeg is located via pydub's resolver
    (same as ``change_tempo``), so no new dependency.
    """
    from pydub.utils import which

    ff = which("ffmpeg")
    if not ff:
        return False

    filters: list[str] = []
    if highpass:
        filters.append(f"highpass=f={int(highpass)}")
    if lowpass:
        filters.append(f"lowpass=f={int(lowpass)}")
    if denoise:
        filters.append("afftdn=nf=-25")
    if not filters:
        return False
    af = ",".join(filters)

    p = Path(path)
    tmp = p.with_name(p.stem + "_clean" + p.suffix)
    try:
        proc = await asyncio.create_subprocess_exec(
            ff, "-y", "-i", str(p), "-af", af, "-b:a", bitrate, str(tmp),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(str(tmp), str(p))
            return True
    except Exception:
        pass
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    return False


async def change_tempo(path: str | Path, speed: float, bitrate: str = "192k") -> bool:
    """Time-stretch an audio file IN PLACE, pitch-preserving (ffmpeg ``atempo``).

    ``speed`` < 1.0 slows down, > 1.0 speeds up; clamped to ffmpeg's single-
    filter atempo range [0.5, 2.0]. Returns True when applied, False on no-op
    (``speed`` ~= 1.0) or failure (ffmpeg missing / encode error) — the original
    file is always left intact on failure. ffmpeg is located via pydub's resolver
    (the same one ``stitch_audio`` relies on), so no new dependency.
    """
    if not speed or abs(speed - 1.0) < 0.01:
        return False
    speed = max(0.5, min(2.0, speed))

    from pydub.utils import which

    ff = which("ffmpeg")
    if not ff:
        return False
    p = Path(path)
    tmp = p.with_name(p.stem + "_spd" + p.suffix)
    try:
        proc = await asyncio.create_subprocess_exec(
            ff, "-y", "-i", str(p), "-filter:a", f"atempo={speed:.3f}",
            "-b:a", bitrate, str(tmp),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(str(tmp), str(p))
            return True
    except Exception:
        pass
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    return False
