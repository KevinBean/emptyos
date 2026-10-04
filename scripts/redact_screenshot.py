#!/usr/bin/env python3
"""Redact a screenshot for publishing — blur private regions + crop chrome.

Thin CLI over ``emptyos.sdk.media.redact_image`` (pure Pillow, no daemon). Blur
regions and the crop are given in the ORIGINAL image's coordinates; each value
is a fraction of the dimension when ``<= 1.0`` and a pixel otherwise. Blur runs
first, then the crop, so regions never shift.

Single image:

    python scripts/redact_screenshot.py in.png --out out.png \\
        --blur 0.05,0.15,0.98,0.21 --blur 0.62,0.20,0.98,0.36 \\
        --crop 0,0,1,0.90

Batch (re-runnable — always redact from clean originals):

    python scripts/redact_screenshot.py --spec redact.json

    # redact.json
    [
      {"src": "orig/a.png", "dst": "media/a.png",
       "blur": [[0.14,0.095,0.40,0.147]], "crop": [0,0,1,0.565]},
      {"src": "orig/b.png", "dst": "media/b.png",
       "blur": [[0.05,0.15,0.985,0.208]]}
    ]

``--json`` emits one ``{"ok", "code", "message", "data"}`` line (agent-cli
envelope); exit code mirrors ``ok``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emptyos.sdk.media.image_redact import redact_image  # noqa: E402


def _parse_box(s: str) -> list[float]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"box needs 4 comma-separated values, got {s!r}")
    return [float(p) for p in parts]


def _emit(ok, code, message, data, as_json):
    if as_json:
        print(json.dumps({"ok": ok, "code": code, "message": message, "data": data}))
    else:
        print(message)
    raise SystemExit(0 if ok else 1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Blur + crop a screenshot for publishing.")
    ap.add_argument("input", nargs="?", help="source image")
    ap.add_argument("--out", help="destination image (required without --spec)")
    ap.add_argument("--blur", action="append", type=_parse_box, default=[],
                    help="blur box x0,y0,x1,y1 (fraction<=1 or px); repeatable")
    ap.add_argument("--crop", type=_parse_box, help="crop box x0,y0,x1,y1 (fraction<=1 or px)")
    ap.add_argument("--pixel-block", type=int, default=24, help="pixelation coarseness (higher=more)")
    ap.add_argument("--gaussian", type=float, default=8.0, help="softening radius over pixelation")
    ap.add_argument("--spec", help="JSON list of {src,dst,blur?,crop?} for batch redaction")
    ap.add_argument("--json", dest="as_json", action="store_true", help="emit an agent-cli envelope")
    args = ap.parse_args()

    try:
        if args.spec:
            jobs = json.loads(Path(args.spec).read_text(encoding="utf-8"))
            out = []
            for j in jobs:
                w, h = redact_image(
                    j["src"], j["dst"],
                    blur=j.get("blur"), crop=j.get("crop"),
                    pixel_block=j.get("pixel_block", args.pixel_block),
                    gaussian=j.get("gaussian", args.gaussian),
                )
                out.append({"dst": j["dst"], "size": [w, h]})
            _emit(True, "ok", f"redacted {len(out)} image(s)", {"images": out}, args.as_json)
        else:
            if not args.input or not args.out:
                _emit(False, "invalid_args", "need <input> and --out (or --spec)", None, args.as_json)
            w, h = redact_image(
                args.input, args.out, blur=args.blur, crop=args.crop,
                pixel_block=args.pixel_block, gaussian=args.gaussian,
            )
            _emit(True, "ok", f"{args.out} — {w}x{h}", {"dst": args.out, "size": [w, h]}, args.as_json)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        _emit(False, "error", f"{type(e).__name__}: {e}", None, args.as_json)


if __name__ == "__main__":
    main()
