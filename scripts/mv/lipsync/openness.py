"""Mouth-openness proxy (Haar face + dark-pixel ratio in the mouth box). Report only.

    python scripts/mv/lipsync/openness.py <video> --wav <wav> [--lag-window=-200,320] [--region left|right]
    python scripts/mv/lipsync/openness.py <video> --closed-windows 0-4.5,9.2-12

Two uses:
- With ``--wav``: correlation of mouth openness with the audio envelope, with a
  lag sweep. This is the old first-pass sync measure. It **cannot judge a line
  shorter than ~8 s** — it scored a wrong sentence as high as the right one —
  so never use it as a verdict. The verdict is ``lipsync_check.py``.
- With ``--closed-windows``: for a shot that must NOT sing (an ACT shot), mean
  and p90 openness inside the given seconds, against the rest of the clip.
  It's useful for spotting a mouth that moves where it should not, then
  confirming by eye.

The default lag window stays ±600 ms so old numbers compare. It is too
permissive on 2-3 s clips; the short-drama test used -200,320 (write it
as ``--lag-window=-200,320``: a leading minus otherwise reads as a flag).
Deps: numpy, cv2, soundfile (only with --wav).
"""
from __future__ import annotations

import argparse
import sys


def openness_series(video: str, region: str | None = None):
    """Per-frame openness (NaN where no face) and the video's fps."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    prof = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")

    def in_region(f, width):
        if not region:
            return True
        cx = f[0] + f[2] / 2
        return cx < width / 2 if region == "left" else cx >= width / 2

    op = []
    while True:
        r, fr = cap.read()
        if not r:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        W = g.shape[1]
        f = [t for t in det.detectMultiScale(g, 1.1, 5, minSize=(80, 80)) if in_region(t, W)]
        if not len(f):
            f = [t for t in prof.detectMultiScale(g, 1.1, 5, minSize=(80, 80)) if in_region(t, W)]
        if not len(f):
            ff = prof.detectMultiScale(cv2.flip(g, 1), 1.1, 5, minSize=(80, 80))
            ff = [t for t in [(W - x - w, y, w, h) for (x, y, w, h) in ff] if in_region(t, W)]
            if len(ff):
                f = [max(ff, key=lambda t: t[2] * t[3])]
        if len(f):
            x, y, w, h = max(f, key=lambda t: t[2] * t[3])
            m = g[y + int(h * 0.60):y + int(h * 0.98), x + int(w * 0.20):x + int(w * 0.80)]
            if m.size:
                mr = cv2.resize(m, (48, 32))
                thr = max(20, int(np.percentile(mr, 12)) + 12)
                op.append(float((mr < thr).mean()))
                continue
        op.append(np.nan)
    cap.release()
    return np.array(op, float), fps


def envelope_correlation(op, fps: float, wav: str, lag_ms=(-600, 600)) -> dict:
    """r at lag 0 and the best r over the lag window, clamped to frames the audio covers."""
    import numpy as np
    import soundfile as sf

    a, sr = sf.read(wav)
    a = a if a.ndim == 1 else a.mean(1)
    n = min(len(op), int(len(a) / sr * fps))
    op = op[:n]
    env = np.array([float(np.sqrt((a[int(k * sr / fps):int((k + 1) * sr / fps)] ** 2).mean() + 1e-12))
                    for k in range(n)])
    idx, good = np.arange(n), ~np.isnan(op)
    if not good.any():
        return {"frames": n, "faces_pct": 0.0}
    opi = np.interp(idx, idx[good], op[good])
    lags = range(round(lag_ms[0] / 1000 * fps), round(lag_ms[1] / 1000 * fps) + 1)
    best = max(lags, key=lambda L: np.corrcoef(np.roll(opi, L), env)[0, 1])
    return {"frames": n, "faces_pct": float(good.mean()), "r0": float(np.corrcoef(opi, env)[0, 1]),
            "best_r": float(np.corrcoef(np.roll(opi, best), env)[0, 1]), "best_lag_ms": best / fps * 1000}


def window_openness(op, fps: float, windows: list[tuple[float, float]]) -> dict:
    """Mean/p90 openness inside the windows vs outside them (NaN frames ignored)."""
    import numpy as np

    t = np.arange(len(op)) / fps
    inside = np.zeros(len(op), bool)
    for a, b in windows:
        inside |= (t >= a) & (t < b)

    def stats(mask):
        v = op[mask & ~np.isnan(op)]
        return {"frames": int(v.size), "mean": float(v.mean()) if v.size else None,
                "p90": float(np.percentile(v, 90)) if v.size else None}

    return {"inside": stats(inside), "outside": stats(~inside)}


def parse_windows(text: str) -> list[tuple[float, float]]:
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        lo, hi = float(a), float(b)
        if hi <= lo:
            raise ValueError(f"window {part} is empty")
        out.append((lo, hi))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("video")
    ap.add_argument("--wav")
    ap.add_argument("--region", choices=["left", "right"])
    ap.add_argument("--lag-window", default="-600,600")
    ap.add_argument("--closed-windows", help="seconds, e.g. 0-4.5,9.2-12")
    a = ap.parse_args(argv)
    if not a.wav and not a.closed_windows:
        ap.error("give --wav, --closed-windows, or both")
    op, fps = openness_series(a.video, a.region)
    if a.wav:
        lo, hi = (int(v) for v in a.lag_window.split(","))
        r = envelope_correlation(op, fps, a.wav, (lo, hi))
        if "r0" not in r:
            print("no face found")
            return 1
        print(f"frames={r['frames']} faces={r['faces_pct']:.0%}  r@0={r['r0']:+.3f}  "
              f"best r={r['best_r']:+.3f} @ {r['best_lag_ms']:+.0f} ms   (proxy, not a verdict)")
    if a.closed_windows:
        w = window_openness(op, fps, parse_windows(a.closed_windows))
        for k in ("inside", "outside"):
            s = w[k]
            print(f"{k:8} frames={s['frames']:4}  mean={s['mean'] if s['mean'] is None else round(s['mean'], 3)}"
                  f"  p90={s['p90'] if s['p90'] is None else round(s['p90'], 3)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
