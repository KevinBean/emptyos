#!/usr/bin/env python3
"""Probe a hybrid-video proof and emit reproducible technical QA JSON.

This script intentionally does not judge art, identity, useful local motion,
camera ownership, or physical plausibility. It requires ffprobe and ffmpeg.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any


FREEZE_START_RE = re.compile(r"freeze_start:\s*(?P<value>-?\d+(?:\.\d+)?)")
FREEZE_DURATION_RE = re.compile(
    r"freeze_duration:\s*(?P<value>\d+(?:\.\d+)?)"
)
FREEZE_END_RE = re.compile(r"freeze_end:\s*(?P<value>-?\d+(?:\.\d+)?)")
BLACK_RE = re.compile(
    r"black_start:(?P<start>-?\d+(?:\.\d+)?)\s+"
    r"black_end:(?P<end>-?\d+(?:\.\d+)?)\s+"
    r"black_duration:(?P<duration>\d+(?:\.\d+)?)"
)
SSIM_RE = re.compile(r"SSIM .* All:(?P<value>-?\d+(?:\.\d+)?)")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rational(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> dict[str, Any]:
    result = run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    payload = json.loads(result.stdout)
    streams = [
        item for item in payload.get("streams", [])
        if item.get("codec_type") == "video"
    ]
    if not streams:
        raise RuntimeError("no video stream")
    stream = streams[0]
    fmt = payload.get("format", {})
    duration_raw = stream.get("duration") or fmt.get("duration")
    frame_count = stream.get("nb_frames")
    return {
        "codec": stream.get("codec_name"),
        "pixel_format": stream.get("pix_fmt"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": rational(
            stream.get("avg_frame_rate") or stream.get("r_frame_rate")
        ),
        "nominal_fps": rational(stream.get("r_frame_rate")),
        "frame_count": (
            int(frame_count) if str(frame_count).isdigit() else None
        ),
        "duration_seconds": (
            float(duration_raw)
            if duration_raw not in {None, "N/A"}
            else None
        ),
        "audio_streams": sum(
            1 for item in payload.get("streams", [])
            if item.get("codec_type") == "audio"
        ),
    }


def parse_freeze_events(
    log: str,
    video_duration: float | None,
    minimum_duration: float,
) -> list[dict[str, float]]:
    """Parse both one-line and split-line FFmpeg freezedetect formats."""
    events: list[dict[str, float]] = []
    pending_start: float | None = None
    pending_duration: float | None = None
    for line in log.splitlines():
        start_match = FREEZE_START_RE.search(line)
        duration_match = FREEZE_DURATION_RE.search(line)
        end_match = FREEZE_END_RE.search(line)
        if start_match:
            pending_start = float(start_match.group("value"))
            pending_duration = None
        if duration_match:
            pending_duration = float(duration_match.group("value"))
        if end_match:
            end = float(end_match.group("value"))
            duration = (
                pending_duration
                if pending_duration is not None
                else max(0.0, end - float(pending_start or 0.0))
            )
            start = (
                pending_start
                if pending_start is not None
                else end - duration
            )
            events.append({
                "start": start,
                "end": end,
                "duration": duration,
            })
            pending_start = None
            pending_duration = None
    if pending_start is not None and video_duration is not None:
        duration = max(0.0, video_duration - pending_start)
        if duration >= minimum_duration:
            events.append({
                "start": pending_start,
                "end": video_duration,
                "duration": duration,
            })
    return events


def detect_intervals(
    path: Path,
    freeze_noise: str,
    freeze_duration: float,
    black_duration: float,
    video_duration: float | None,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    filters = (
        f"freezedetect=n={freeze_noise}:d={freeze_duration},"
        f"blackdetect=d={black_duration}:pix_th=0.10"
    )
    result = run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-vf", filters, "-an", "-f", "null", "-",
    ])
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip() or "ffmpeg interval detection failed"
        )
    freezes = parse_freeze_events(
        result.stderr,
        video_duration,
        freeze_duration,
    )
    black = [{
        "start": float(match.group("start")),
        "end": float(match.group("end")),
        "duration": float(match.group("duration")),
    } for match in BLACK_RE.finditer(result.stderr)]
    return freezes, black


def compare_ssim(baseline: Path, candidate: Path) -> float:
    result = run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(baseline),
        "-i", str(candidate), "-filter_complex", "[0:v][1:v]ssim",
        "-an", "-f", "null", "-",
    ])
    matches = list(SSIM_RE.finditer(result.stderr))
    if result.returncode or not matches:
        message = result.stderr.strip().splitlines()[-1:] or [
            "SSIM comparison failed"
        ]
        raise RuntimeError(message[0])
    return float(matches[-1].group("value"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--freeze-noise", default="-50dB")
    parser.add_argument("--freeze-duration", type=float, default=0.50)
    parser.add_argument("--black-duration", type=float, default=0.10)
    args = parser.parse_args()

    missing = [
        name for name in ("ffprobe", "ffmpeg")
        if shutil.which(name) is None
    ]
    if missing:
        parser.error("missing executable(s): " + ", ".join(missing))
    video = args.video.resolve()
    if not video.is_file():
        parser.error(f"video does not exist: {video}")
    if args.baseline and not args.baseline.resolve().is_file():
        parser.error(f"baseline does not exist: {args.baseline.resolve()}")

    try:
        stream = probe(video)
        freezes, black = detect_intervals(
            video,
            args.freeze_noise,
            args.freeze_duration,
            args.black_duration,
            stream.get("duration_seconds"),
        )
        comparison = None
        if args.baseline:
            baseline = args.baseline.resolve()
            comparison = {
                "baseline": str(baseline),
                "baseline_sha256": sha256(baseline),
                "ssim_all": compare_ssim(baseline, video),
                "interpretation": (
                    "difference indicator only; not a quality score"
                ),
            }
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"QA failed: {exc}", file=sys.stderr)
        return 2

    reasons: list[str] = []
    if black:
        reasons.append("black interval detected")
    if freezes:
        reasons.append("freeze/near-duplicate interval detected")
    report = {
        "schema_version": 1,
        "video": str(video),
        "sha256": sha256(video),
        "bytes": video.stat().st_size,
        "stream": stream,
        "freeze_events": freezes,
        "black_events": black,
        "comparison": comparison,
        "technical_status": "review" if reasons else "pass",
        "technical_reasons": reasons,
        "limits": {
            "art_review_required": True,
            "identity_review_required": True,
            "local_vs_global_motion_review_required": True,
            "note": "A pass proves basic container/interval health only.",
        },
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
