"""Video encoder selection — CPU x264 vs NVIDIA NVENC, in one place.

Every ffmpeg shell-out in EmptyOS hardcoded ``-c:v libx264`` until now, so a
box with a capable GPU still encoded music-video clips, Ken Burns pans and the
final subtitle burn-in on the CPU. This module is the single place that decides
which encoder a caller gets, so the choice can be flipped per app without nine
call sites drifting apart.

**Emits codec + rate-control tokens ONLY — never geometry.** No ``-vf``, no
``-s``, no ``scale``/``pad``/``crop``. That is not a style preference: the MV
release QA gate (``apps/personal/music-studio/release.py``) hard-fails unless
the master is exactly 1920x1080 (or 1080x1920 for shorts), so an encoder helper
that could touch frame size would be able to break that gate silently. Keeping
geometry out by construction makes the guarantee testable rather than hopeful
(see ``tests/test_unit_media_encode.py``).

Pure + kernel-free, so it unit-tests without a daemon. The dark-flag read lives
at the app caller (``emptyos/sdk/media/`` deliberately has no config access) and
arrives here as the ``gpu=`` argument.
"""

from __future__ import annotations

from emptyos.sdk.proc import run_command

CPU_CODEC = "libx264"
NVENC_CODEC = "h264_nvenc"

#: x264 preset name -> NVENC p-preset (p1 fastest ... p7 best quality).
#: NVENC does **not** accept x264 preset names — passing ``medium`` through
#: verbatim fails the encode outright, which is exactly what a naive
#: find-and-replace of ``libx264`` -> ``h264_nvenc`` would have shipped.
NVENC_PRESETS = {
    "ultrafast": "p1",
    "superfast": "p1",
    "veryfast": "p2",
    "faster": "p3",
    "fast": "p4",
    "medium": "p5",
    "slow": "p7",
    "slower": "p7",
    "veryslow": "p7",
}
_NVENC_DEFAULT_PRESET = "p5"

_PROBE_TIMEOUT_S = 5.0
_PROBE_ARGV = ["ffmpeg", "-hide_banner", "-encoders"]

# Resolved once per process. Deliberately not per-call: assembler.concat_clips
# stitches with the concat demuxer and ``-c copy``, so every clip in a run must
# come from the same encoder. A mid-run driver hiccup flipping the answer would
# produce a half-x264/half-NVENC clip set and a broken output stream.
_nvenc_cache: bool | None = None


def video_args(
    *,
    crf: int = 20,
    preset: str = "medium",
    gpu: bool = False,
    pix_fmt: str | None = "yuv420p",
) -> list[str]:
    """Encoder + rate-control flags for an ffmpeg output.

    ``crf`` carries the same meaning on both branches (lower = better); on the
    NVENC branch it becomes ``-cq``. ``preset`` is always given in x264 names
    and is translated for NVENC via :data:`NVENC_PRESETS`.

    ``pix_fmt=None`` omits the flag entirely — for callers that already pin the
    pixel format inside their filter chain (``format=yuv420p``), as every site
    in ``video.py`` does. Passing it twice is harmless but makes the emitted
    command differ from what those callers ship today, so the default is opt-out
    rather than forced.

    Never emits geometry flags. See the module docstring for why that matters.
    """
    args = ["-c:v", NVENC_CODEC if gpu else CPU_CODEC]
    if pix_fmt:
        args += ["-pix_fmt", pix_fmt]
    if not gpu:
        return args + ["-preset", preset, "-crf", str(crf)]
    return args + [
        "-preset", NVENC_PRESETS.get(preset, _NVENC_DEFAULT_PRESET),
        "-tune", "hq",
        # h264_nvenc defaults to Main; x264 defaults to High. Without this the
        # GPU branch silently ships a lower-profile stream (no 8x8 transform,
        # worse compression at the same nominal quality) than the CPU branch it
        # replaces — verified by ffprobe, not assumed.
        "-profile:v", "high",
        "-rc", "vbr",
        "-cq", str(crf),
        # Load-bearing: without an explicit unlimited bitrate ffmpeg applies a
        # ~2 Mbps default cap and silently ignores -cq, shipping a visibly worse
        # master with no error anywhere.
        "-b:v", "0",
    ]


async def nvenc_available() -> bool:
    """True when this ffmpeg build exposes the NVENC H.264 encoder.

    Probes ``ffmpeg -encoders`` once per process and caches the answer. Never
    raises — a missing binary, a non-zero exit, or a timeout all resolve to
    False, so a broken probe degrades to CPU encoding rather than to no video.

    Concurrent first calls may each probe; the result is identical either way,
    so the extra subprocess is wasted work rather than a correctness problem.
    """
    global _nvenc_cache
    if _nvenc_cache is not None:
        return _nvenc_cache
    res = await run_command(_PROBE_ARGV, timeout=_PROBE_TIMEOUT_S)
    _nvenc_cache = bool(res.ok and NVENC_CODEC in (res.stdout or ""))
    return _nvenc_cache


async def video_args_resolved(
    *,
    crf: int = 20,
    preset: str = "medium",
    gpu: bool = False,
    pix_fmt: str | None = "yuv420p",
) -> list[str]:
    """:func:`video_args` with the GPU request checked against the real build.

    ``gpu=True`` is a *request*, not an assertion — it falls back to x264 when
    NVENC isn't there. Call sites pass the app's dark flag straight in.
    """
    use_gpu = bool(gpu) and await nvenc_available()
    return video_args(crf=crf, preset=preset, gpu=use_gpu, pix_fmt=pix_fmt)


def reset_probe_cache() -> None:
    """Forget the cached NVENC probe. For tests; not used at runtime."""
    global _nvenc_cache
    _nvenc_cache = None
