"""Measure an ACE-Step section repaint: did the window change, and did the
rest of the take survive?

    python scripts/measure_repaint.py SOURCE REPAINTED START_S END_S

Both failure modes named in the deferred-work row are real and this is how you
tell them apart -- a repaint that changes nothing (the pack's own 0.01 variance
default) and one that changes everything.

THIS TOOL CANNOT SEE LYRICS. log-mel compares spectral shape, so a window that
sings entirely different words at the same pitch and rhythm still scores ~0.83
and passes here. That happened: a repaint that had replaced the vocal was
reported as verified on 2026-08-15. For any take with singing in the window,
run `scripts/check_repaint_lyrics.py` as well -- this tool answers "is the
music intact", that one answers "are these still the words".

THE METRIC IS THE HARD PART, and getting it wrong reverses the verdict.
Measured 2026-08-15 on a 60s take with 20-40s repainted:

    raw waveform Pearson r, untouched head : -0.25   ("the whole track died")
    log-mel  r,             untouched head : +0.97   ("the track is intact")

Same audio, opposite conclusions. The kept region is not a copy -- the pipeline
encodes the whole track to latents, replaces only the masked frames, and
decodes everything -- so it is a neural-VAE round-trip, which reconstructs the
waveform without preserving phase, and shifted this file by 197 samples
(4.1 ms). Raw-waveform correlation collapses to zero on either. So: estimate
and remove global lag, then compare log-mel MAGNITUDE spectrograms, which are
phase-invariant and are what "is this still the same music" actually means.
Never verify the kept region with a hash; a mismatch there is expected.

Read the per-second profile, not the segment averages. The profile is what
shows the edit landing on the seconds you asked for -- and only the WITHIN-run
gap between kept and window is controlled, because lag alignment differs
between runs.

Needs numpy + soundfile + librosa. The ComfyUI embedded interpreter has all
three:
    D:/ComfyUI_windows_portable/python_embeded/python.exe scripts/measure_repaint.py ...
"""
from __future__ import annotations

import sys

import numpy as np
import soundfile as sf

# Pass bands. Deliberately asymmetric: the kept region must be nearly intact,
# while "changed" is a gap from the kept baseline rather than an absolute
# number -- a repaint is a variation on the section, not a different song, so
# an absolute window threshold flags healthy runs as failures.
KEPT_MIN = 0.85

# Three bands, not two, because a single threshold conflates "subtle" with
# "nothing" and the difference is the whole question. Measured separations:
#
#   variance 0.01 (the pack's no-op)          -0.013
#   variance 0.30 (our default, two seeds)    +0.034, +0.044   <- real, subtle
#   variance 0.50                             +0.128, +0.145
#   variance 0.95                             +0.329
#
# At 0.30 the window demonstrably changed and the words survived (0.720-0.778
# word-similarity), so a hard FAIL there would be the tool calling a correct
# repaint broken. Only a separation at or below zero is unambiguous, and per
# .claude/rules/audits.md an ambiguous signal must not gate.
NOOP_SEPARATION = 0.0     # at or below this the window is indistinguishable
CLEAR_SEPARATION = 0.05   # above this the change is obvious without listening


def mono(path: str):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


def corr(x, y) -> float:
    x = x - x.mean()
    y = y - y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / d) if d > 0 else float("nan")


def estimate_lag(a, b, n) -> int:
    """Global lag from FFT cross-correlation over a kept span."""
    fa, fb = np.fft.rfft(a[:n], 2 * n), np.fft.rfft(b[:n], 2 * n)
    cc = np.fft.irfft(fa * np.conj(fb))
    cc = np.concatenate([cc[-(n - 1):], cc[:n]])
    return int(np.argmax(np.abs(cc)) - (n - 1))


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    src_p, new_p = sys.argv[1], sys.argv[2]
    w0, w1 = float(sys.argv[3]), float(sys.argv[4])

    a, sr = mono(src_p)
    b, sr_b = mono(new_p)
    if sr != sr_b:
        print(f"sample rate mismatch: {sr} vs {sr_b} — cannot compare")
        return 2
    n = min(len(a), len(b))
    if len(a) != len(b):
        print(f"length differs by {abs(len(a)-len(b))} samples "
              f"({abs(len(a)-len(b))/sr*1000:.1f} ms); comparing common {n/sr:.2f}s")
    a, b = a[:n], b[:n]

    head = int(min(w0, 15) * sr) or int(min(5.0, n / sr) * sr)
    lag = estimate_lag(a, b, head)
    print(f"estimated lag on kept head: {lag} samples ({lag/sr*1000:+.2f} ms)")
    print(f"waveform r on kept head   : {corr(a[:head], b[:head]):+.4f}  "
          f"(phase-sensitive — informational only)")
    if lag > 0:
        a, b = a[lag:], b[:len(b) - lag]
    elif lag < 0:
        a, b = a[:len(a) + lag], b[-lag:]
    m = min(len(a), len(b))
    a, b = a[:m], b[:m]

    import librosa
    HOP = 512

    def logmel(x):
        s = librosa.feature.melspectrogram(
            y=x, sr=sr, n_fft=2048, hop_length=HOP, n_mels=96)
        return librosa.power_to_db(s, ref=np.max)

    Ma, Mb = logmel(a), logmel(b)
    f = min(Ma.shape[1], Mb.shape[1])
    Ma, Mb = Ma[:, :f], Mb[:, :f]
    fps = sr / HOP

    print("\nper-second log-mel correlation  (| = requested window)")
    for s in range(int(f / fps)):
        sl = slice(int(s * fps), int((s + 1) * fps))
        v = corr(Ma[:, sl].ravel(), Mb[:, sl].ravel())
        mark = "|" if w0 <= s < w1 else " "
        print(f"{s:4d}s {mark} {v:+.3f} {'#' * int(max(0.0, v) * 40)}")

    def seg(t0, t1):
        if t1 <= t0:
            return float("nan")
        sl = slice(int(t0 * fps), int(t1 * fps))
        return corr(Ma[:, sl].ravel(), Mb[:, sl].ravel())

    dur = f / fps
    before, window, after = seg(0, w0), seg(w0, w1), seg(w1, dur)
    kept = [v for v in (before, after) if not np.isnan(v)]
    kept_min = min(kept) if kept else float("nan")
    sep = kept_min - window

    print("\nsegment log-mel correlation")
    print(f"  before  [0, {w0:g})        r = {before:+.4f}")
    print(f"  WINDOW  [{w0:g}, {w1:g})       r = {window:+.4f}")
    print(f"  after   [{w1:g}, {dur:.0f})       r = {after:+.4f}")

    kept_ok = kept_min > KEPT_MIN
    if sep <= NOOP_SEPARATION:
        changed = "FAIL"
    elif sep >= CLEAR_SEPARATION:
        changed = "PASS"
    else:
        changed = "SUBTLE"

    print("\nverdict (log-mel, phase-invariant)")
    print(f"  kept region survived : {'PASS' if kept_ok else 'FAIL'} "
          f"(min r={kept_min:+.4f}, need > {KEPT_MIN})")
    print(f"  window changed       : {changed} (separation {sep:+.4f})")
    if changed == "FAIL":
        print("  -> the window is indistinguishable from the audio around it. "
              "Raise `variance`; at 0.01 the pipeline skips 99% of its steps.")
    elif changed == "SUBTLE":
        print("  -> the window changed, but not by much. Expected at the "
              "lyric-safe strengths (0.15-0.30 measure +0.03 to +0.05); this "
              "is not a failure. Judge it by ear, and note that the words "
              "surviving is what buys the small number.")
    if not kept_ok:
        print("  -> check the window bounds before believing this: a repaint "
              "covering the whole track leaves no kept region to compare.")
    # SUBTLE exits 0: it is a legitimate outcome of the shipped default, and a
    # non-zero exit there would fail every correct lyric-preserving repaint.
    return 0 if (kept_ok and changed != "FAIL") else 1


if __name__ == "__main__":
    sys.exit(main())
