"""Assemble UI-walk frame screenshots into a small animated GIF — the
"replay" evidence for a step the walk judged `fail` or `confusing`.

This is the Playwright-MCP half of the eos-ui-walk GIF-on-failure approach
(claude-in-chrome has a native ``gif_creator`` tool and doesn't need this):
the walker re-drives the failing flow taking a burst of ordered screenshots
(`uc1-f01.png`, `uc1-f02.png`, ...), then this script downscales and stitches
them into one looping GIF whose path goes into the step-log's ``gif`` field.
``ui_walk_report.py`` base64-embeds it next to the step's still.

Pure file I/O + Pillow — no kernel import, no browser work. Fail-soft: a
missing Pillow or unreadable frame prints a clear message and exits non-zero;
the report renders fine without the GIF (the still remains the evidence).

Usage:
  python scripts/ui_walk_gif.py --out data/ui-walk/usecases/<run>/uc1-fail.gif \
      data/ui-walk/usecases/<run>/uc1-f01.png data/ui-walk/usecases/<run>/uc1-f02.png ...

Options:
  --width 800    max frame width (downscale keeps the GIF small; default 800)
  --delay 900    ms per frame (default 900; the last frame holds 2x so the
                 end-state reads before the loop restarts)
"""
from __future__ import annotations

import argparse
from pathlib import Path

MAX_FRAMES = 12  # discipline cap — a replay is a clip, not a recording


def build_gif(frames: list[Path], out: Path, *, width: int = 800, delay_ms: int = 900) -> int:
    try:
        from PIL import Image
    except ImportError:
        print("ERROR: Pillow not installed (pip install pillow) — skipping GIF; the still screenshot is the evidence.")
        return 2

    if len(frames) > MAX_FRAMES:
        print(f"NOTE: {len(frames)} frames given; keeping the first {MAX_FRAMES} (cap).")
        frames = frames[:MAX_FRAMES]

    images = []
    for f in frames:
        if not f.exists():
            print(f"WARN: frame missing, skipped: {f}")
            continue
        try:
            im = Image.open(f).convert("RGB")
        except Exception as e:  # noqa: BLE001 — one bad frame must not kill the clip
            print(f"WARN: unreadable frame, skipped: {f} ({e})")
            continue
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)))
        images.append(im.quantize(colors=128))

    if len(images) < 2:
        print(f"ERROR: need >=2 readable frames for a replay GIF, got {len(images)}.")
        return 2

    durations = [delay_ms] * len(images)
    durations[-1] = delay_ms * 2  # hold the end state so it reads before looping
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(out, save_all=True, append_images=images[1:],
                   duration=durations, loop=0, optimize=True)
    print(f"GIF -> {out}  ({len(images)} frames, {out.stat().st_size // 1024} KB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Stitch ordered frame screenshots into a looping replay GIF.")
    ap.add_argument("frames", nargs="+", help="ordered frame image paths (first to last)")
    ap.add_argument("--out", required=True, help="output .gif path")
    ap.add_argument("--width", type=int, default=800, help="max frame width (default 800)")
    ap.add_argument("--delay", type=int, default=900, help="ms per frame (default 900)")
    args = ap.parse_args()
    return build_gif([Path(f) for f in args.frames], Path(args.out),
                     width=args.width, delay_ms=args.delay)


if __name__ == "__main__":
    raise SystemExit(main())
