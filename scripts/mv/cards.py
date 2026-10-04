"""Lay in-picture text cards, the title card and the watermark over a finished picture.

    python scripts/mv/cards.py cards.json picture.mp4 out.mp4

Lifted from an MV rescue (rescue-v18). A card says what neither picture nor
lyric does (who left, which bus, the turn at the end), sits where nothing is
sung, and is typeset unlike the subtitles: vertical serif Chinese in the shot's
own dark space, a small English line beneath, soft fades.

Spec (all text lives here, never in code):

    {"width": 1920, "height": 1080, "fps": 24, "fade": 0.8,
     "cards": [{"start": 5.4, "end": 9.4, "columns": ["<column 1>", "<column 2>"],
                "english": "...", "x": 1770, "top": 190, "align": "right", "size": 50}],
     "title": {"start": 1.0, "end": 5.0, "zh": "...", "en": "...", "fade": 0.7},
     "watermark": ["© <year> <channel>", "<artist>"]}

Fonts come from ``[mv_tools.fonts]`` (``zh_serif`` for cards, ``en_serif_italic``
for their English line, ``zh_sans`` for title and watermark). The input is
refused if it is a living-master render that still has placeholders.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import living_master  # noqa: E402
import mv_config  # noqa: E402
import text_render  # noqa: E402


def render_pngs(spec: dict, work: Path, fonts: dict) -> dict:
    w, h = int(spec.get("width", 1920)), int(spec.get("height", 1080))
    cards = [text_render.render_vertical_card(
        work / f"card{i:02d}.png", c["columns"], c.get("english", ""), cx=int(c["x"]),
        top=int(c["top"]), align=c.get("align", "left"), width=w, height=h,
        size=int(c.get("size", 50)), zh_font=fonts["zh_serif"], en_font=fonts["en_serif_italic"])
        for i, c in enumerate(spec.get("cards") or [])]
    t = spec.get("title")
    title = text_render.render_title(work / "title.png", t["zh"], t["en"], width=w, height=h,
                                     font=fonts["zh_sans"]) if t else None
    marks = spec.get("watermark")
    mark = text_render.render_watermark(work / "watermark.png", marks, width=w, height=h,
                                        font=fonts["zh_sans"]) if marks else None
    return {"cards": cards, "title": title, "watermark": mark}


def build_command(spec: dict, src: Path, out: Path, pngs: dict) -> list[str]:
    fps, fade = int(spec.get("fps", 24)), float(spec.get("fade", 0.8))
    inputs, parts, last = ["-i", str(src)], [], "0:v"
    k = 0
    for k, (c, png) in enumerate(zip(spec.get("cards") or [], pngs["cards"]), start=1):
        a, dur = float(c["start"]), float(c["end"]) - float(c["start"])
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", str(png)]
        parts.append(f"[{k}:v]format=rgba,fade=t=in:st=0:d={fade}:alpha=1,"
                     f"fade=t=out:st={dur - fade:.3f}:d={fade}:alpha=1,setpts=PTS+{a}/TB[c{k}]")
        parts.append(f"[{last}][c{k}]overlay=0:0:eof_action=pass[o{k}]")
        last = f"o{k}"
    if pngs["title"]:
        k += 1
        t = spec["title"]
        a, dur, tf = float(t["start"]), float(t["end"]) - float(t["start"]), float(t.get("fade", 0.7))
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", str(pngs["title"])]
        parts.append(f"[{k}:v]format=rgba,fade=t=in:st=0:d={tf}:alpha=1,"
                     f"fade=t=out:st={dur - tf:.3f}:d={tf}:alpha=1,setpts=PTS+{a}/TB[ct]")
        parts.append(f"[{last}][ct]overlay=0:0:eof_action=pass[ot]")
        last = "ot"
    if pngs["watermark"]:
        k += 1
        inputs += ["-i", str(pngs["watermark"])]
        parts.append(f"[{last}][{k}:v]overlay=0:0[ow]")
        last = "ow"
    return ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts),
            "-map", f"[{last}]", "-map", "0:a", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)]


def fonts_from_config() -> dict:
    return {role: mv_config.require(f"font.{role}")
            for role in ("zh_serif", "en_serif_italic", "zh_sans")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("spec", type=Path)
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--work", type=Path, help="where the card PNGs go (default: <out dir>/cards)")
    args = ap.parse_args(argv)
    blocked = living_master.release_block_reason(args.src)
    if blocked:
        print(f"cards: refusing input — {blocked}", file=sys.stderr)
        return 1
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    pngs = render_pngs(spec, args.work or args.out.parent / "cards", fonts_from_config())
    r = subprocess.run(build_command(spec, args.src, args.out, pngs),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(r.stderr[-1500:], file=sys.stderr)
        return 1
    print(f"{len(pngs['cards'])} cards{' + title' if pngs['title'] else ''}"
          f"{' + watermark' if pngs['watermark'] else ''} → {args.out}")
    if living_master.write_derived_sidecar(args.out, args.src, "cards"):
        print("  lineage: derived from a final render (sidecar written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
