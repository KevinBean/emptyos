"""Subtitle and timing utilities."""

from __future__ import annotations

from pathlib import Path


def compute_timings(seg_paths: list[Path], script: list[dict], gap_ms: int = 400) -> list[dict]:
    """Compute start/end ms for each segment from audio file durations."""
    from pydub import AudioSegment
    timings = []
    offset = 0
    for i, p in enumerate(seg_paths):
        try:
            dur = len(AudioSegment.from_file(str(p)))
        except Exception:
            dur = 3000
        timings.append({
            "start_ms": offset,
            "end_ms": offset + dur,
            "speaker": script[i]["speaker"] if i < len(script) else "A",
            "text": script[i]["text"] if i < len(script) else "",
        })
        offset += dur + gap_ms
    return timings


def generate_srt(timings: list[dict], output_path: str):
    """Generate SRT subtitle file from segment timings."""
    def _fmt(ms: int) -> str:
        h, ms = divmod(int(ms), 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, t in enumerate(timings):
        speaker = "Host A" if t["speaker"] == "A" else "Host B"
        lines.extend([
            str(i + 1),
            f"{_fmt(t['start_ms'])} --> {_fmt(t['end_ms'])}",
            f"{speaker}: {t['text']}",
            "",
        ])
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
