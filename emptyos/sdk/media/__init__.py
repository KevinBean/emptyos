"""EmptyOS Media Services — reusable video, audio, and subtitle utilities.

Available to any app via:
    from emptyos.sdk.media import stitch_audio, assemble_video, generate_srt, plan_scenes
"""

from emptyos.sdk.media.audio import change_tempo, clean_audio, stitch_audio
from emptyos.sdk.media.encode import nvenc_available, video_args, video_args_resolved
from emptyos.sdk.media.html_record import record_html_to_mp4
from emptyos.sdk.media.image_redact import redact_image, resolve_box
from emptyos.sdk.media.review import MediaVerdict, review_audio, review_video
from emptyos.sdk.media.scenes import mechanical_scenes, plan_scenes
from emptyos.sdk.media.subtitles import (
    audio_duration_ms,
    compute_timings,
    generate_srt,
    proportional_timings,
)
from emptyos.sdk.media.video import (
    assemble_video,
    fit_clip_to_duration,
    frames_to_mp4,
    mux_audio,
    still_to_clip,
)

__all__ = [
    "stitch_audio",
    "change_tempo",
    "clean_audio",
    "mechanical_scenes",
    "plan_scenes",
    "compute_timings",
    "proportional_timings",
    "audio_duration_ms",
    "generate_srt",
    "assemble_video",
    "still_to_clip",
    "fit_clip_to_duration",
    "mux_audio",
    "frames_to_mp4",
    "record_html_to_mp4",
    "review_audio",
    "review_video",
    "MediaVerdict",
    "redact_image",
    "resolve_box",
    "video_args",
    "video_args_resolved",
    "nvenc_available",
]
