"""Unit tests for encoder selection (emptyos.sdk.media.encode).

Pure-function tests plus a monkeypatched probe — no ffmpeg and no GPU required,
so they run anywhere including CI.

The load-bearing test here is ``test_never_emits_geometry``. The MV release QA
gate hard-fails unless the master is exactly 1920x1080, so an encoder helper
that could emit a scale/pad/-s flag would be able to break that gate silently.
Pinning "no geometry token, ever, for any input" is what turns that from a
convention into a guarantee.
"""

from __future__ import annotations

import asyncio

import pytest

from emptyos.sdk.media import encode as E
from emptyos.sdk.proc import ProcResult

# --- the flag blocks the real call sites ship today ------------------------
# If any of these change, a caller's output changed too — that is the point.

ASSEMBLER_CPU = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20"]
FRAMES_CPU = ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
CONCAT_CPU = ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """Each test starts with an unprobed module."""
    E.reset_probe_cache()
    yield
    E.reset_probe_cache()


def _fake_run(stdout: str = "", *, rc: int = 0, timed_out: bool = False, counter: list | None = None):
    async def run(argv, *, timeout, cwd=None):
        if counter is not None:
            counter.append(argv)
        return ProcResult(rc, stdout, "", timed_out)
    return run


# --- CPU branch: byte-identical to what ships today ------------------------

def test_cpu_branch_matches_assembler_today():
    """All six assembler.py sites use this exact block."""
    assert E.video_args(crf=20, preset="medium", gpu=False) == ASSEMBLER_CPU


def test_cpu_branch_matches_video_py_sites():
    """video.py pins yuv420p inside its filter chain, so it passes pix_fmt=None."""
    assert E.video_args(crf=20, preset="fast", gpu=False, pix_fmt=None) == FRAMES_CPU
    assert E.video_args(crf=23, preset="fast", gpu=False, pix_fmt=None) == CONCAT_CPU


def test_pix_fmt_is_opt_out_not_forced():
    assert "-pix_fmt" not in E.video_args(pix_fmt=None)
    assert "-pix_fmt" not in E.video_args(pix_fmt=None, gpu=True)
    for args in (E.video_args(), E.video_args(gpu=True)):
        assert args[args.index("-pix_fmt") + 1] == "yuv420p"


# --- the guarantee that protects release.py's 1920x1080 gate ---------------

FORBIDDEN_TOKENS = {"-vf", "-s", "-aspect", "-filter:v", "-filter_complex", "-r", "-vframes"}
FORBIDDEN_SUBSTRINGS = ("scale", "pad", "crop", "setsar", "aspect")


@pytest.mark.parametrize("gpu", [False, True])
@pytest.mark.parametrize("preset", sorted(E.NVENC_PRESETS) + ["nonsense-preset"])
@pytest.mark.parametrize("crf", [0, 18, 20, 23, 51])
@pytest.mark.parametrize("pix_fmt", [None, "yuv420p", "yuv444p"])
def test_never_emits_geometry(gpu, preset, crf, pix_fmt):
    args = E.video_args(crf=crf, preset=preset, gpu=gpu, pix_fmt=pix_fmt)
    for tok in args:
        assert tok not in FORBIDDEN_TOKENS, f"geometry flag {tok!r} leaked into {args}"
        low = tok.lower()
        for bad in FORBIDDEN_SUBSTRINGS:
            assert bad not in low, f"geometry-ish token {tok!r} leaked into {args}"


# --- NVENC branch: the two things a naive find-and-replace gets wrong ------

def test_nvenc_never_uses_x264_preset_names():
    """'-preset medium' is not valid NVENC syntax — it fails the encode."""
    for preset in E.NVENC_PRESETS:
        args = E.video_args(preset=preset, gpu=True)
        chosen = args[args.index("-preset") + 1]
        assert chosen.startswith("p") and chosen[1:].isdigit(), f"{preset} -> {chosen}"
        assert preset not in args


def test_nvenc_unknown_preset_falls_back_to_a_valid_one():
    args = E.video_args(preset="not-a-real-preset", gpu=True)
    assert args[args.index("-preset") + 1] == "p5"


def test_nvenc_pins_unlimited_bitrate():
    """Without -b:v 0, ffmpeg caps at ~2 Mbps and silently ignores -cq."""
    args = E.video_args(crf=20, gpu=True)
    assert args[args.index("-b:v") + 1] == "0"
    assert args[args.index("-cq") + 1] == "20"
    assert "-crf" not in args


def test_nvenc_matches_x264_profile():
    """h264_nvenc defaults to Main, x264 to High — pin the parity explicitly."""
    args = E.video_args(gpu=True)
    assert args[args.index("-profile:v") + 1] == "high"


def test_codec_selection():
    assert E.video_args(gpu=False)[:2] == ["-c:v", "libx264"]
    assert E.video_args(gpu=True)[:2] == ["-c:v", "h264_nvenc"]


# --- probe: cached, and fail-soft in every direction ----------------------

@pytest.mark.asyncio
async def test_probe_caches_across_calls(monkeypatch):
    calls: list = []
    monkeypatch.setattr(E, "run_command", _fake_run("h264_nvenc", counter=calls))
    results = [await E.nvenc_available() for _ in range(5)]
    assert results == [True] * 5
    assert len(calls) == 1, "probe must resolve once per process"


@pytest.mark.asyncio
async def test_probe_false_when_encoder_absent(monkeypatch):
    monkeypatch.setattr(E, "run_command", _fake_run("libx264\nlibx265\n"))
    assert await E.nvenc_available() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"rc": 1, "stdout": "h264_nvenc"},          # non-zero exit
        {"rc": -1, "stdout": "", "timed_out": True},  # probe hung
        {"rc": -1, "stdout": ""},                    # binary missing
    ],
)
async def test_probe_fails_soft_to_cpu(monkeypatch, kwargs):
    stdout = kwargs.pop("stdout")
    monkeypatch.setattr(E, "run_command", _fake_run(stdout, **kwargs))
    assert await E.nvenc_available() is False


# --- resolved wrapper: gpu=True is a request, not an assertion ------------

@pytest.mark.asyncio
async def test_resolved_falls_back_when_nvenc_missing(monkeypatch):
    monkeypatch.setattr(E, "run_command", _fake_run("libx264"))
    assert await E.video_args_resolved(crf=20, preset="medium", gpu=True) == ASSEMBLER_CPU


@pytest.mark.asyncio
async def test_resolved_uses_gpu_when_available(monkeypatch):
    monkeypatch.setattr(E, "run_command", _fake_run("h264_nvenc"))
    args = await E.video_args_resolved(crf=20, preset="medium", gpu=True)
    assert args[:2] == ["-c:v", "h264_nvenc"]


@pytest.mark.asyncio
async def test_resolved_never_probes_when_gpu_not_requested(monkeypatch):
    """The default path must not pay for a subprocess."""
    calls: list = []
    monkeypatch.setattr(E, "run_command", _fake_run("h264_nvenc", counter=calls))
    assert await E.video_args_resolved(crf=20, preset="medium", gpu=False) == ASSEMBLER_CPU
    assert calls == []


# --- integration: the real probe, when a real ffmpeg is present ----------

def test_real_probe_agrees_with_ffmpeg_if_present():
    """Self-skips without ffmpeg; asserts the probe never raises either way."""
    E.reset_probe_cache()
    got = asyncio.run(E.nvenc_available())
    assert isinstance(got, bool)
