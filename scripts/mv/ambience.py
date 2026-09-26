"""Build an ambience bed (rain, street, room tone) from a real recording.

    python scripts/mv/ambience.py rain.mp3 bed.wav --duration 177.12 \\
        --segment 0:17.5:20:1.5:4 --segment 156:end:60:4:1.6 --dbfs -31

Each ``--segment at:end:from:fade_in:fade_out`` places a stretch of the source,
starting at ``from`` seconds into it, at ``[at, end)`` of the song (``end`` may
be the word ``end``). It is levelled to ``--dbfs`` RMS, then faded in over
``fade_in`` seconds and out over ``fade_out`` (0 means a hard edge). Use a different ``from`` per
segment so the loops do not repeat audibly. The output is 16-bit stereo, for
mixing under the song with ``amix normalize=0`` so the song stays at its
mastered level.

Why a recording and not synthesis: during the 〈說得太急〉 rescue a
synthesised bed (filtered pink noise plus ticks) read as running water. In the
2 kHz+ band it measured kurtosis 5.6, barely above Gaussian noise (3); real
rain measured 14-24. Rain is individual impacts, not a smooth hiss. Download
3-4 candidates, preview each under the song's opening, and let the user choose
by ear; downloading needs the user's go-ahead.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import wave
from pathlib import Path


def read_stretch(src: Path, start: float, length: float, sr: int):
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(length), "-i", str(src),
                          "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def build_bed(src: Path, duration: float, segments: list[tuple], *, dbfs: float = -31.0,
              sr: int = 48000):
    """Mono float bed of ``duration`` seconds. Each segment is
    ``(at, end_or_None, source_from, fade_in, fade_out)``."""
    import numpy as np

    n = int(sr * duration)
    out = np.zeros(n)
    env = np.zeros(n)
    t = np.arange(n) / sr
    for at, end, frm, fi, fo in segments:
        end = duration if end is None else end
        if not 0 <= at < end <= duration:
            raise ValueError(f"segment {at}-{end} is outside the song (0-{duration})")
        x = read_stretch(src, frm, end - at, sr)
        if len(x) < int((end - at) * sr) - 1:
            raise ValueError(f"the recording is too short: needs {end - at:.2f}s from {frm}s, "
                             f"has {len(x) / sr:.2f}s")
        rms = np.sqrt(np.mean(x ** 2)) if len(x) else 0.0
        if rms <= 0:
            raise ValueError(f"source is silent or too short at {frm}s")
        x = x * 10 ** (dbfs / 20) / rms
        s0 = int(at * sr)
        out[s0:s0 + len(x)] += x[:n - s0]
        inside = (t >= at) & (t < end)
        rise = np.clip((t - at) / fi, 0, 1) if fi > 0 else inside.astype(float)
        fall = np.clip((end - t) / fo, 0, 1) if fo > 0 else inside.astype(float)
        env += rise * fall
    return out * env


def write_wav(path: Path, mono, sr: int = 48000) -> None:
    import numpy as np

    st = np.clip(np.stack([mono, mono], 1), -0.99, 0.99)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((st * 32767).astype("<i2").tobytes())


def parse_segment(text: str) -> tuple:
    parts = text.split(":")
    if len(parts) != 5:
        raise argparse.ArgumentTypeError("segment is at:end:from:fade_in:fade_out")
    at, end, frm, fi, fo = parts
    try:
        return (float(at), None if end == "end" else float(end), float(frm), float(fi), float(fo))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--duration", type=float, required=True, help="song length in seconds")
    ap.add_argument("--segment", type=parse_segment, action="append", required=True)
    ap.add_argument("--dbfs", type=float, default=-31.0)
    ap.add_argument("--rate", type=int, default=48000)
    args = ap.parse_args(argv)
    try:
        bed = build_bed(args.source, args.duration, args.segment, dbfs=args.dbfs, sr=args.rate)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"ambience: {exc}", file=sys.stderr)
        return 1
    write_wav(args.out, bed, args.rate)
    print(f"{args.out} ({args.duration}s, {len(args.segment)} segment(s), {args.dbfs} dBFS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
