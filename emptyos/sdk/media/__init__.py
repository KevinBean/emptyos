"""EmptyOS Media Services — reusable video, audio, and subtitle utilities.

Available to any app via:
    from emptyos.sdk.media import stitch_audio, assemble_video, generate_srt, plan_scenes
"""

from emptyos.sdk.media.audio import stitch_audio
from emptyos.sdk.media.scenes import mechanical_scenes, plan_scenes
from emptyos.sdk.media.subtitles import compute_timings, generate_srt
from emptyos.sdk.media.video import assemble_video

__all__ = [
    "stitch_audio",
    "mechanical_scenes",
    "plan_scenes",
    "compute_timings",
    "generate_srt",
    "assemble_video",
]
