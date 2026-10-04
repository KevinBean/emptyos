"""Burn a bilingual SRT into a finished picture as PNG overlays.

    python scripts/mv/burn_subtitles.py lyrics.srt in.mp4 out.mp4 [--bottom 10 --height 118]

Each cue is drawn with PIL (Chinese line, English beneath) and overlaid with
an enable window, rather than through ffmpeg's ``subtitles``/``drawtext``
filters: ``drawtext`` crashed on fontconfig during the 〈說得太急〉 rescue, and
the PIL strip renders identically on every machine. Overlapping cues are clipped, so only one lyric
is ever on screen. The default strip (118 px, 10 px from the bottom) sits inside
a 2.39:1 letterbox bar at 1080p.

Each SRT block is: index, ``a --> b``, the Chinese line, then an optional
English line (a third text line is ignored). Fonts come from ``[mv_tools.fonts]`` ``zh_sans`` and ``en_sans``.
The input is refused if it is a living-master render that still has placeholders.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import living_master  # noqa: E402
import mv_config  # noqa: E402
import text_render  # noqa: E402

GAP = 0.04   # seconds between a clipped cue and the next


def _ts(s: str) -> float:
    h, m, rest = s.strip().split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def parse_srt(text: str, gap: float = GAP) -> list[list]:
    """``[start, end, zh, en]`` per cue, each end clipped before the next start."""
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        a, z = (_ts(x) for x in lines[1].split("-->"))
        cues.append([a, z, lines[2], lines[3] if len(lines) > 3 else ""])
    for c, n in zip(cues, cues[1:]):
        c[1] = min(c[1], n[0] - gap)
    dropped = [c for c in cues if c[1] <= c[0]]
    for c in dropped:
        print(f"burn_subtitles: dropping cue at {c[0]:.3f}s — the next cue starts before it can show",
              file=sys.stderr)
    return [c for c in cues if c[1] > c[0]]


def probe_width(video: Path) -> int | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


def build_command(src: Path, out: Path, cues: list[list], pngs: list[Path], *, bottom: int) -> list[str]:
    inputs, parts, last = ["-i", str(src)], [], "0:v"
    for i, ((a, z, _zh, _en), png) in enumerate(zip(cues, pngs), 1):
        inputs += ["-i", str(png)]
        parts.append(f"[{last}][{i}:v]overlay=x=0:y=H-h-{bottom}:"
                     f"enable='between(t,{a:.3f},{z:.3f})'[s{i}]")
        last = f"s{i}"
    return ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts),
            "-map", f"[{last}]", "-map", "0:a", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("srt", type=Path)
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--bottom", type=int, default=10)
    ap.add_argument("--height", type=int, default=118)
    ap.add_argument("--width", type=int, help="strip width (default: the input's width)")
    ap.add_argument("--work", type=Path, help="where the cue PNGs go (default: <out dir>/subs)")
    args = ap.parse_args(argv)
    blocked = living_master.release_block_reason(args.src)
    if blocked:
        print(f"burn_subtitles: refusing input — {blocked}", file=sys.stderr)
        return 1
    cues = parse_srt(args.srt.read_text(encoding="utf-8-sig"))
    if not cues:
        print("burn_subtitles: no cues in the SRT", file=sys.stderr)
        return 1
    width = args.width or probe_width(args.src)
    if not width:
        print("burn_subtitles: cannot read the input's width; pass --width", file=sys.stderr)
        return 1
    zh_font, en_font = mv_config.require("font.zh_sans"), mv_config.require("font.en_sans")
    work = args.work or args.out.parent / "subs"
    pngs = [text_render.render_cue(work / f"cue{i:02d}.png", zh, en, width=width,
                                   height=args.height, zh_font=zh_font, en_font=en_font)
            for i, (_a, _z, zh, en) in enumerate(cues, 1)]
    r = subprocess.run(build_command(args.src, args.out, cues, pngs, bottom=args.bottom),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(r.stderr[-1500:], file=sys.stderr)
        return 1
    print(f"{len(cues)} cues → {args.out}")
    if living_master.write_derived_sidecar(args.out, args.src, "burn_subtitles"):
        print("  lineage: derived from a final render (sidecar written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
