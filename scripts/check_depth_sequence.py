"""Acceptance guard for a rendered blockout depth sequence.

Calibrated against the 2026-07-27 probe run (docs/GUIDED-GENERATION.md §9-10),
where an LLM got 2 of 3 first-attempt scenes wrong and both failures were the
same shape: *the camera move ends somewhere with nothing to look at*.

Five signals, three of which gate. Per `.claude/rules/audits.md` the confident
half gates and the ambiguous half stays advisory:

    FLAT   (gate)     a frame collapses to almost no grey levels
    RANGE  (gate)     a frame's depth spread (p95-p5) collapses — the view has
                      flattened onto one surface
    SMOOTH (gate)     a frame-to-frame spike, i.e. a camera jump
    DRIFT  (advisory) the mean depth marches across the shot
    VOID   (advisory) most of the frame is clamped at `far`; a night sky or a
                      fog bank legitimately looks like this

**DRIFT was a gate and was demoted, because it was a false positive on a whole
class of legitimate shot.** It was calibrated on two scenes — a corridor crane
(near/far roughly constant) and a dolly into a blank wall — where mean-shift
and range-collapse happen to coincide. A third scene falsified it: a crane-up
revealing a valley legitimately moves the mean by 57, and the loop rejected it
three times running while the frames were fine. Measured spread at the moment
of the fix:

    sequence                     mean-drift    min(p95-p5)
    street_crane  (good)                8.7            109
    alley_push    (good)                4.8             34
    plaza_orbit   (good)               14.4            223
    hillside_crane(good, rejected)     56.8            168
    room_dolly    (BAD)                74.3              3

Mean-shift cannot separate rows 4 and 5; depth spread separates them by 50x.
RANGE is what DRIFT always claimed to measure, so it replaces it as the gate
and DRIFT stays only as a note. Bad case sits at 3, nearest good case at 34.

Exit code is the number of GATING failures, so it is usable as a shell test.

The result is a ``MediaVerdict`` — the same hard/soft type ``review_audio`` and
``review_video`` return. A depth sequence is a control signal rather than a
rendered artifact, so none of the ffprobe machinery is shared, but the *verdict*
is the same claim in all three cases ("deterministic pre-flight, hard findings
block, soft findings annotate") and `.claude/rules/model-ability.md` already
names them one family. Sharing the type is what lets a caller treat every gate
in that family alike instead of learning three result shapes.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emptyos.sdk.media.review import MediaVerdict  # noqa: E402

FLAT_MIN_LEVELS = 24
RANGE_MIN_SPREAD = 20.0      # bad case 3, nearest good case 34
SMOOTH_MAX_RATIO = 3.0
DRIFT_ADVISORY = 40.0        # advisory only — see the module docstring
VOID_ADVISORY_PCT = 25.0


def analyse(directory: str) -> MediaVerdict:
    import numpy as np
    from PIL import Image

    files = sorted(glob.glob(os.path.join(directory, "*.png")))
    if len(files) < 2:
        # A sequence that does not exist is a hard finding, not a separate
        # error channel — the caller's one question is "may I spend GPU on
        # this", and the answer is the same no.
        return MediaVerdict(
            ok=False, hard=(f"EMPTY need >=2 PNGs, found {len(files)}",)
        )

    frames = [
        np.asarray(Image.open(f).convert("L")).astype(np.float32) for f in files
    ]
    levels = [int(len(np.unique(a.astype(np.uint8)))) for a in frames]
    means = [float(a.mean()) for a in frames]
    spreads = [
        float(np.percentile(a, 95) - np.percentile(a, 5)) for a in frames
    ]
    void = [float((a <= 2).mean() * 100.0) for a in frames]
    steps = np.array(
        [float(np.abs(frames[i + 1] - frames[i]).mean())
         for i in range(len(frames) - 1)]
    )
    median_step = float(np.median(steps)) if steps.size else 0.0
    ratio = float(steps.max() / median_step) if median_step > 1e-6 else 0.0

    fails, warns = [], []
    if min(levels) < FLAT_MIN_LEVELS:
        i = int(np.argmin(levels))
        fails.append(
            f"FLAT frame {i + 1} has {levels[i]} grey levels "
            f"(< {FLAT_MIN_LEVELS}) — the view has collapsed onto one surface"
        )
    if min(spreads) < RANGE_MIN_SPREAD:
        i = int(np.argmin(spreads))
        fails.append(
            f"RANGE frame {i + 1} spans only {spreads[i]:.0f} grey levels "
            f"p5..p95 (< {RANGE_MIN_SPREAD}) — the view has flattened onto "
            f"one surface"
        )
    drift = max(means) - min(means)
    if drift > DRIFT_ADVISORY:
        warns.append(
            f"DRIFT mean depth moves {drift:.1f} across the shot — expected "
            f"for a reveal, suspicious for a locked-off shot (advisory)"
        )
    if ratio > SMOOTH_MAX_RATIO:
        i = int(np.argmax(steps))
        fails.append(
            f"SMOOTH step {i + 1}->{i + 2} is {ratio:.2f}x the median "
            f"(> {SMOOTH_MAX_RATIO}) — the camera jumps"
        )
    if max(void) > VOID_ADVISORY_PCT:
        i = int(np.argmax(void))
        warns.append(
            f"VOID frame {i + 1} is {void[i]:.0f}% clamped at far — check the "
            f"background is not swinging out of frame (advisory)"
        )

    return MediaVerdict(
        ok=not fails,
        hard=tuple(fails),
        soft=tuple(warns),
        metrics={
            "frames": len(frames),
            "flat_min_levels": min(levels),
            "range_min_spread": round(min(spreads), 1),
            "smooth_ratio": round(ratio, 2),
            "drift": round(drift, 2),
            "void_max_pct": round(max(void), 1),
        },
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    v = analyse(args.directory)
    m = v.metrics
    if args.json:
        print(json.dumps(
            {"ok": v.ok, "hard": list(v.hard), "soft": list(v.soft), "metrics": m},
            indent=2,
        ))
    elif not m:
        print(f"ERROR {'; '.join(v.hard)}")
    else:
        print(
            f"frames={m['frames']}  FLAT={m['flat_min_levels']}  "
            f"RANGE={m['range_min_spread']}  SMOOTH={m['smooth_ratio']}x  "
            f"(drift={m['drift']} void={m['void_max_pct']}%)"
        )
        for s in v.soft:
            print(f"  warn  {s}")
        for h in v.hard:
            print(f"  FAIL  {h}")
        print("  -> ACCEPT" if v.ok else "  -> REJECT")
    return 0 if v.ok else max(1, len(v.hard))


if __name__ == "__main__":
    sys.exit(main())
