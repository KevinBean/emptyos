"""EmptyOS Media Services — reusable video, audio, and subtitle utilities.

Available to any app via:
    from emptyos.sdk.media import stitch_audio, assemble_video, generate_srt, plan_scenes
"""

from emptyos.sdk.media.audio import stitch_audio
from emptyos.sdk.media.video import assemble_video
from emptyos.sdk.media.subtitles import generate_srt, compute_timings
from emptyos.sdk.media.scenes import plan_scenes, mechanical_scenes
