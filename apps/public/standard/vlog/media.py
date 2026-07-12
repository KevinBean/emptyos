"""Vlog — thin app-side wrappers over the shared video ffmpeg ops.

The raw ffmpeg/ffprobe shell-outs now live once in ``emptyos/sdk/media/video.py``
(consumed by vlog + the podcast/MV ``assemble_video`` pipeline). These wrappers
add only the app-specific concerns the SDK deliberately doesn't carry: syslog
on failure and int-rounding the duration. They take ``self`` so the existing
class bindings in app.py keep working; do not import from ``.app`` (cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk.media import video as _v

if TYPE_CHECKING:
    from .app import VlogApp  # noqa: F401 — for type hints only


# ─── Bind to VlogApp class as ────────────────────────────────────────
#   extract_thumbnail = _media.extract_thumbnail
#   extract_audio     = _media.extract_audio
#   probe_duration    = _media.probe_duration
#   compile_montage   = _media.compile_montage
# Adding a new helper here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


async def extract_thumbnail(self: VlogApp, src: Path, out: Path, *, at: float = 1.0) -> bool:
    ok = await _v.extract_thumbnail(src, out, at=at)
    if not ok:
        self.log(f"vlog thumbnail failed for {Path(src).name}", level="warn")
    return ok


async def extract_audio(self: VlogApp, src: Path, out: Path) -> bool:
    ok = await _v.extract_audio(src, out)
    if not ok:
        self.log(f"vlog audio extract skipped (no audio?) for {Path(src).name}", level="info")
    return ok


async def probe_duration(self: VlogApp, src: Path) -> int:
    """Clip duration in whole seconds (0 if unknown)."""
    return int(await _v.probe_duration(src))


async def compile_montage(
    self: VlogApp,
    clips: list[Path],
    out: Path,
    *,
    resolution: tuple[int, int] = (1280, 720),
) -> None:
    """Stitch the day clips into one montage MP4 (raises on ffmpeg failure)."""
    await _v.concat_clips(clips, out, resolution=resolution)
