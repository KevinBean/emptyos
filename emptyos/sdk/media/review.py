"""Media output self-review — a validity gate for *generated* audio/video.

The deterministic, no-LLM analogue of the geometry validity gate in
``engines/shape_validation/`` (``.claude/rules/model-ability.md`` § "Validity
gate"). A generation pipeline produces a media artifact; this module answers
"is the output actually non-broken?" *before* it ships — catching the silent
podcast, the all-black video, the clipped audio that an endpoint test never
notices.

Borrowed 2026-06-21 from OpenMontage's post-render self-review (AGPLv3 → idea
only, no code). Posture mirrors shape-validation exactly:

- a :class:`MediaVerdict` carries ``hard`` (must regenerate) + ``soft``
  (degraded, annotate-and-pass) findings — the shape of ``CompileReport``'s
  ``errors``/``warnings``, so it flows through a pipeline the same way;
- **fail-open** — if ffprobe/ffmpeg is missing or the output is unparseable,
  the verdict is ``ok`` with a soft "review skipped" note. A review tool that
  can't run must never block a real artifact (same posture as the cad
  compile-retry loop's soft smells, and as context-packing's "never block on
  uncertainty").

Binaries are resolved via ``pydub.utils.which`` (the same resolver the rest of
``emptyos/sdk/media`` relies on) so this adds no new dependency — ffprobe/ffmpeg
already ship with the FFmpeg the media stack uses.

The parsing + decision logic is split into pure functions (``_parse_ffprobe``,
``_parse_astats``, ``_audio_verdict``) so it unit-tests against canned tool
output without ffmpeg installed; the async ``review_audio`` is the thin
subprocess wrapper.

First consumer: the podcast pipeline's ``audio`` stage (behind
``[apps.podcast] feature.output-review.enabled``). ``review_video`` gained the
frame-sampling slice 2026-07-03 (ffmpeg ``freezedetect`` + ``blackdetect`` —
catches the frozen-end-state / all-black clip a container probe can't see);
its first consumer is viz's Export-MP4 self-check (behind
``[apps.viz] feature.output-review.enabled``).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# --- thresholds (overridable per call) -------------------------------------

SILENCE_RMS_DB = -70.0   # overall RMS below this (or -inf) = silent → hard fail
LOW_RMS_DB = -45.0       # between silence and this = quiet → soft note
CLIP_PEAK_DB = -0.1      # peak at/above this ≈ 0 dBFS = possible clipping → soft


@dataclass(frozen=True)
class MediaVerdict:
    """Outcome of reviewing one media artifact.

    ``ok`` is ``True`` iff there are no ``hard`` findings — a soft-only verdict
    still passes (degraded but shippable). ``metrics`` carries the raw numbers
    (duration, RMS/peak dB) for logging into ``run.json`` / a panel."""

    ok: bool
    hard: tuple[str, ...] = ()
    soft: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)


def _verdict(hard, soft, metrics) -> MediaVerdict:
    hard = tuple(hard)
    return MediaVerdict(ok=not hard, hard=hard, soft=tuple(soft), metrics=dict(metrics or {}))


# --- pure parsers (unit-tested without ffmpeg) -----------------------------

def _parse_ffprobe(json_str: str) -> dict:
    """``ffprobe -print_format json -show_format -show_streams`` → minimal facts.

    Returns ``has_audio`` (True/False, or **None** when unparseable so the
    caller fails open rather than treating "couldn't tell" as "no audio") and
    ``duration`` (positive float, else None)."""
    try:
        data = json.loads(json_str)
    except (ValueError, TypeError):
        return {"has_audio": None, "duration": None}
    streams = data.get("streams") or []
    has_audio = any((s or {}).get("codec_type") == "audio" for s in streams)
    fmt = data.get("format") or {}
    duration = None
    candidates = [fmt.get("duration")] + [(s or {}).get("duration") for s in streams]
    for src in candidates:
        try:
            d = float(src)
        except (TypeError, ValueError):
            continue
        if d > 0:
            duration = d
            break
    return {"has_audio": has_audio, "duration": duration}


def _to_db(token: str | None) -> float | None:
    if token is None:
        return None
    t = token.strip()
    if t in ("-inf", "inf", "nan"):
        return float("-inf") if t == "-inf" else (float("inf") if t == "inf" else None)
    try:
        return float(t)
    except ValueError:
        return None


def _parse_astats(stderr: str) -> dict:
    """ffmpeg ``astats`` stderr → overall ``rms_db`` / ``peak_db`` / ``flat_factor``.

    astats prints per-channel sections then an ``Overall`` section; we read the
    Overall block (last "Overall" onward). For a mono file with no separate
    Overall header, the whole output is used. Missing fields → None (fail-open
    upstream). Handles the ``-inf`` that a fully silent track reports."""
    if not stderr:
        return {"rms_db": None, "peak_db": None, "flat_factor": None}
    block = stderr.rsplit("Overall", 1)[1] if "Overall" in stderr else stderr

    def grab(label: str) -> float | None:
        m = re.search(rf"{re.escape(label)}:\s*(-?inf|nan|-?\d+(?:\.\d+)?)", block)
        return _to_db(m.group(1)) if m else None

    return {
        "rms_db": grab("RMS level dB"),
        "peak_db": grab("Peak level dB"),
        "flat_factor": grab("Flat factor"),
    }


def _audio_verdict(
    probe: dict,
    stats: dict,
    *,
    min_duration_s: float = 0.0,
    silence_rms_db: float = SILENCE_RMS_DB,
    low_rms_db: float = LOW_RMS_DB,
    clip_peak_db: float = CLIP_PEAK_DB,
) -> MediaVerdict:
    """Pure decision: probe facts + astats levels → :class:`MediaVerdict`.

    Hard (regenerate): no audio stream, non-positive/too-short duration, or a
    silent track. Soft (annotate): low level, or a peak at ≈0 dBFS (possible
    clipping). Unknown values (None) are skipped — never penalised."""
    hard: list[str] = []
    soft: list[str] = []
    metrics: dict = {}

    if probe.get("has_audio") is False:
        hard.append("no audio stream")

    dur = probe.get("duration")
    metrics["duration_s"] = dur
    if dur is not None and dur <= min_duration_s:
        hard.append(f"duration {dur:.2f}s <= {min_duration_s:g}s minimum")

    rms = stats.get("rms_db")
    peak = stats.get("peak_db")
    metrics["rms_db"] = rms
    metrics["peak_db"] = peak
    metrics["flat_factor"] = stats.get("flat_factor")

    if rms is not None:
        if rms == float("-inf") or rms < silence_rms_db:
            shown = "-inf" if rms == float("-inf") else f"{rms:.1f}"
            hard.append(f"silent (RMS {shown} dB < {silence_rms_db:g} dB)")
        elif rms < low_rms_db:
            soft.append(f"low level (RMS {rms:.1f} dB)")

    if peak is not None and peak != float("-inf") and peak >= clip_peak_db:
        soft.append(f"near 0 dBFS, possible clipping (peak {peak:.2f} dB)")

    return _verdict(hard, soft, metrics)


def _parse_freeze_black(stderr: str, duration: float) -> dict:
    """ffmpeg ``freezedetect``+``blackdetect`` stderr → seconds frozen / black.

    freezedetect emits ``freeze_start`` / ``freeze_duration`` pairs; a clip
    still frozen at EOF emits a final ``freeze_start`` with no duration, which
    counts to the end of the clip (that's exactly the frozen-end-state shape).
    blackdetect emits self-contained ``black_duration`` values. Missing/garbled
    output → zeros (fail-open upstream)."""
    freeze_s = 0.0
    black_s = 0.0
    if not stderr:
        return {"freeze_s": 0.0, "black_s": 0.0}
    open_start: float | None = None
    for line in stderr.splitlines():
        m = re.search(r"freezedetect\.freeze_start:\s*(-?\d+(?:\.\d+)?)", line)
        if m:
            open_start = float(m.group(1))
            continue
        m = re.search(r"freezedetect\.freeze_duration:\s*(-?\d+(?:\.\d+)?)", line)
        if m:
            freeze_s += max(0.0, float(m.group(1)))
            open_start = None
            continue
        m = re.search(r"black_duration:\s*(-?\d+(?:\.\d+)?)", line)
        if m:
            black_s += max(0.0, float(m.group(1)))
    if open_start is not None and duration > open_start:
        freeze_s += duration - open_start
    return {"freeze_s": min(freeze_s, duration), "black_s": min(black_s, duration)}


def _frame_findings(
    duration: float,
    freeze_s: float,
    black_s: float,
    *,
    hard_frac: float = 0.9,
    soft_frac: float = 0.5,
) -> tuple[list[str], list[str]]:
    """Pure decision: frozen/black coverage fractions → (hard, soft) findings.

    ≥ ``hard_frac`` of the clip frozen or black = a broken artifact
    (regenerate); ≥ ``soft_frac`` = degraded (annotate-and-pass)."""
    hard: list[str] = []
    soft: list[str] = []
    if duration <= 0:
        return hard, soft
    for label, covered in (("frozen (static frames)", freeze_s), ("black frames", black_s)):
        frac = covered / duration
        if frac >= hard_frac:
            hard.append(f"{label}: {frac:.0%} of the clip")
        elif frac >= soft_frac:
            soft.append(f"{label}: {frac:.0%} of the clip")
    return hard, soft


# --- async wrappers (thin subprocess shells) -------------------------------

async def _run_stdout(binary: str, args: list[str]) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return out.decode("utf-8", "replace")
    except Exception:
        return None


async def _run_stderr(binary: str, args: list[str]) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        return err.decode("utf-8", "replace")
    except Exception:
        return None


async def review_audio(
    path: str | Path,
    *,
    min_duration_s: float = 0.0,
    silence_rms_db: float = SILENCE_RMS_DB,
    low_rms_db: float = LOW_RMS_DB,
    clip_peak_db: float = CLIP_PEAK_DB,
) -> MediaVerdict:
    """Review a generated audio file. Hard-fails a silent/empty/too-short track,
    soft-flags low level / clipping, and **fails open** (ok + soft note) when
    ffprobe/ffmpeg are missing or the output is unparseable. Never raises."""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return _verdict(["file missing or empty"], [], {"path": str(p)})

    from pydub.utils import which

    ffprobe = which("ffprobe")
    ffmpeg = which("ffmpeg")
    probe = {"has_audio": None, "duration": None}
    stats = {"rms_db": None, "peak_db": None, "flat_factor": None}
    notes: list[str] = []

    if ffprobe:
        out = await _run_stdout(ffprobe, [
            "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(p),
        ])
        if out is not None:
            probe = _parse_ffprobe(out)
    else:
        notes.append("review partial: ffprobe not found")

    if ffmpeg:
        err = await _run_stderr(ffmpeg, [
            "-hide_banner", "-nostats", "-i", str(p),
            "-af", "astats=measure_perchannel=none:"
                   "measure_overall=Peak_level+RMS_level+Flat_factor",
            "-f", "null", "-",
        ])
        if err is not None:
            stats = _parse_astats(err)
    else:
        notes.append("review partial: ffmpeg not found (level not analyzed)")

    verdict = _audio_verdict(
        probe, stats,
        min_duration_s=min_duration_s, silence_rms_db=silence_rms_db,
        low_rms_db=low_rms_db, clip_peak_db=clip_peak_db,
    )

    learned_nothing = probe["has_audio"] is None and stats["rms_db"] is None
    if learned_nothing and not verdict.hard:
        return _verdict([], notes or ["review skipped: no analyzable data"], verdict.metrics)
    if notes:
        return _verdict(list(verdict.hard), list(verdict.soft) + notes, verdict.metrics)
    return verdict


async def review_video(
    path: str | Path,
    *,
    min_duration_s: float = 0.0,
    require_audio: bool = True,
    sample_frames: bool = True,
    frozen_hard_frac: float = 0.9,
    frozen_soft_frac: float = 0.5,
) -> MediaVerdict:
    """Video review — probe facts (non-empty file, positive duration, optional
    audio track) plus, when ``sample_frames`` is on, a frame-sampling pass via
    ffmpeg ``freezedetect`` + ``blackdetect``: a clip that is frozen or black
    for ≥ ``frozen_hard_frac`` of its duration hard-fails (the frozen-end-state
    a recorder produces when it can't seek the timeline), ≥ ``frozen_soft_frac``
    soft-flags.

    Fails open exactly like :func:`review_audio`."""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return _verdict(["file missing or empty"], [], {"path": str(p)})

    from pydub.utils import which

    ffprobe = which("ffprobe")
    if not ffprobe:
        return _verdict([], ["review skipped: ffprobe not found"], {"path": str(p)})

    out = await _run_stdout(ffprobe, [
        "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(p),
    ])
    if out is None:
        return _verdict([], ["review skipped: ffprobe failed"], {"path": str(p)})

    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return _verdict([], ["review skipped: unparseable ffprobe output"], {"path": str(p)})

    streams = data.get("streams") or []
    has_video = any((s or {}).get("codec_type") == "video" for s in streams)
    has_audio = any((s or {}).get("codec_type") == "audio" for s in streams)
    dur = _parse_ffprobe(out)["duration"]

    hard: list[str] = []
    soft: list[str] = []
    if not has_video:
        hard.append("no video stream")
    if dur is not None and dur <= min_duration_s:
        hard.append(f"duration {dur:.2f}s <= {min_duration_s:g}s minimum")
    if require_audio and not has_audio:
        soft.append("no audio track")

    metrics: dict = {"duration_s": dur, "has_video": has_video, "has_audio": has_audio}

    ffmpeg = which("ffmpeg")
    if sample_frames and has_video and dur and dur > 0:
        if not ffmpeg:
            soft.append("review partial: ffmpeg not found (frames not sampled)")
        else:
            # freezedetect: n = per-pixel noise tolerance, d = min freeze
            # length — scaled down for short clips so a fully static 3s clip
            # still registers.
            freeze_d = min(2.0, dur / 4)
            err = await _run_stderr(ffmpeg, [
                "-hide_banner", "-nostats", "-i", str(p),
                "-vf", f"freezedetect=n=0.003:d={freeze_d:.3g},"
                       f"blackdetect=d={freeze_d:.3g}:pix_th=0.10",
                "-an", "-f", "null", "-",
            ])
            if err is None:
                soft.append("review partial: frame sampling failed")
            else:
                cov = _parse_freeze_black(err, dur)
                metrics.update(cov)
                fh, fs = _frame_findings(
                    dur, cov["freeze_s"], cov["black_s"],
                    hard_frac=frozen_hard_frac, soft_frac=frozen_soft_frac,
                )
                hard.extend(fh)
                soft.extend(fs)

    return _verdict(hard, soft, metrics)
