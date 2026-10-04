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
        timings.append(
            {
                "start_ms": offset,
                "end_ms": offset + dur,
                "speaker": script[i]["speaker"] if i < len(script) else "A",
                "text": script[i]["text"] if i < len(script) else "",
            }
        )
        offset += dur + gap_ms
    return timings


def audio_duration_ms(path: str | Path) -> int:
    """Total duration of an audio file in ms, or 0 if unreadable."""
    from pydub import AudioSegment

    try:
        return len(AudioSegment.from_file(str(path)))
    except Exception:
        return 0


def proportional_timings(total_ms: int, script: list[dict]) -> list[dict]:
    """Distribute a known total duration across script lines proportionally by
    text length.

    Used when per-turn audio files don't exist (a single combined file — e.g.
    VibeVoice single-pass dialogue) but the full duration IS known. Gives good-
    enough subtitle sync and a non-empty timeline so the slideshow can render.
    """
    if not script or total_ms <= 0:
        return []
    weights = [max(1, len((l.get("text") or ""))) for l in script]
    tot_w = sum(weights)
    timings = []
    offset = 0
    for i, line in enumerate(script):
        dur = (total_ms * weights[i]) // tot_w if tot_w else total_ms // len(script)
        end = total_ms if i == len(script) - 1 else offset + dur
        timings.append(
            {
                "start_ms": offset,
                "end_ms": end,
                "speaker": line.get("speaker", "A"),
                "text": line.get("text", ""),
            }
        )
        offset = end
    return timings


def generate_srt(timings: list[dict], output_path: str, *, speaker_labels: bool = True):
    """Generate SRT subtitle file from segment timings.

    ``speaker_labels`` prefixes each cue with ``Host A`` / ``Host B`` (the
    two-host podcast shape). A single-narrator video passes False, which writes
    the text alone and no longer requires a ``speaker`` key per timing.
    """

    def _fmt(ms: int) -> str:
        h, ms = divmod(int(ms), 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, t in enumerate(timings):
        if speaker_labels:
            speaker = "Host A" if t["speaker"] == "A" else "Host B"
            cue = f"{speaker}: {t['text']}"
        else:
            cue = t["text"]
        lines.extend(
            [
                str(i + 1),
                f"{_fmt(t['start_ms'])} --> {_fmt(t['end_ms'])}",
                cue,
                "",
            ]
        )
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
