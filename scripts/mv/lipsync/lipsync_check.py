"""Lip-sync verdict for one sung/spoken shot: its own line against a decoy line.

    python scripts/mv/lipsync/lipsync_check.py <video.mp4> <own.wav> <decoy.wav>
        [--frames N] [--region left|right] [--json out.json]

A SyncNet score alone cannot say "in sync": on a short clip wrong audio can
reach LSE-C 4.5. So the shot is scored twice, against its own line and against
a different line (a decoy, same voice if possible), and passes only when all
of these hold:

    own LSE-C >= 4.5
    own LSE-C - decoy LSE-C >= 2.0
    |offset| <= 6 frames, and the best offset is not at the search edge

It is ``unmeasurable`` (not a fail) when the line spans under 1 s of speech — a
known-good clip scores anywhere from 2.2 to 6.8 at that length — when the decoy
has less speech than that (a near-silent decoy scores low and would make the
margin meaningless), or when a face is found in under half the frames. No face
at all is a fail: a face that melted or vanished is a real defect. Faces under
~120 px have never passed; that is a warning, not decided here.

Long lines (at least two 81-frame InfiniteTalk windows) are also scored window
by window, and every full window must reach LSE-C 3.5, so sync that decays
across windows is caught. Windows reuse the whole clip's smoothed face crops
(the short-drama test re-cropped each window), so per-window numbers differ
from that test by about 0.1: S4 read 8.886 / 9.777 / 8.382 here against
8.957 / 9.684 / 8.291 recorded.

Inputs must be the **isolated vocal** (a stem), not the mix: a music bed within
~16 dB of the voice counts as speech, and SyncNet's features were never checked
on a mix. The thresholds were calibrated on spoken drama lines (the 2026-09-19
short-drama test); on singing they are unverified.

Both tracks are padded or trimmed to ``--frames`` at 25 fps. Pass the
**source** frame count: InfiniteTalk pads its output to whole windows, and
scoring against the padded length adds silent windows that drag the score down.
With no ``--frames``, the video's own length is used, with a warning.

Exit code: 0 pass, 1 fail, 3 unmeasurable, 2 could not run (bad input, missing
dependency or model) — a crash never reads as a fail.
Thresholds come from the 2026-09-19 short-drama capability test.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import syncnet  # noqa: E402

MIN_LSE_C = 4.5
MIN_MARGIN = 2.0
MAX_OFFSET = 6
MIN_SPEECH_S = 1.0
MIN_FACES = 0.5
FACE_FLOOR_PX = 120
WINDOW = 81
MIN_WINDOW_LSE_C = 3.5


def pad_audio(src: Path, frames: int, out: Path, fps: int = syncnet.FPS) -> Path:
    """Pad with silence or trim so the track is exactly ``frames`` frames long."""
    import numpy as np
    import soundfile as sf

    a, sr = sf.read(str(src))
    n = int(round(frames / fps * sr))
    o = np.zeros((n,) + a.shape[1:])
    o[:min(n, len(a))] = a[:n]
    sf.write(str(out), o, sr, subtype="PCM_16")
    return out


def speech_seconds(path: Path, *, win_s: float = 0.04, rel: float = 0.15) -> float:
    """Span of speech: first to last 40 ms window louder than ``rel`` of the loudest.

    A span, not a count of loud windows, so pauses and soft consonants inside
    the line are included, as the test spec measures it.
    """
    import numpy as np
    import soundfile as sf

    a, sr = sf.read(str(path), always_2d=True)
    a = a.mean(1)
    w = max(1, int(win_s * sr))
    n = len(a) // w
    if n == 0:
        return 0.0
    rms = np.sqrt((a[:n * w].reshape(n, w) ** 2).mean(1))
    if rms.max() <= 0:
        return 0.0
    loud = np.flatnonzero(rms > rel * rms.max())
    return float((loud[-1] - loud[0] + 1) * win_s)


def verdict(own: dict, decoy: dict, speech_s: float, decoy_speech_s: float | None = None,
            windows: list[dict] | None = None) -> tuple[str, list[str]]:
    """``(pass|fail|unmeasurable, reasons)`` from two syncnet results."""
    if "error" in own:
        return "fail", [own["error"]]
    reasons = []
    if speech_s < MIN_SPEECH_S:
        return "unmeasurable", [f"{speech_s:.2f}s of speech (< {MIN_SPEECH_S}s)"]
    if decoy_speech_s is not None and decoy_speech_s < min(speech_s, MIN_SPEECH_S):
        return "unmeasurable", [f"the decoy has only {decoy_speech_s:.2f}s of speech; use a longer line"]
    if own["faces_pct"] < MIN_FACES:
        return "unmeasurable", [f"face found in {own['faces_pct']:.0%} of frames"]
    if own["lse_c"] < MIN_LSE_C:
        reasons.append(f"own LSE-C {own['lse_c']:.3f} < {MIN_LSE_C}")
    margin = own["lse_c"] - decoy.get("lse_c", float("inf"))
    if margin < MIN_MARGIN:
        reasons.append(f"own − decoy {margin:.3f} < {MIN_MARGIN}")
    if abs(own["offset"]) > MAX_OFFSET:
        reasons.append(f"offset {own['offset']:+d} beyond ±{MAX_OFFSET}")
    if own["edge"]:
        reasons.append("best offset at the search edge")
    for i, w in enumerate(windows or []):
        if w.get("lse_c", -9) < MIN_WINDOW_LSE_C:
            reasons.append(f"window {i} LSE-C {w.get('lse_c', float('nan')):.3f} < {MIN_WINDOW_LSE_C}")
    return ("fail" if reasons else "pass"), reasons


def check(video: Path, own: Path, decoy: Path, *, frames: int | None = None,
          region: str | None = None) -> dict:
    vframes = syncnet.read_frames(video)
    warnings = []
    if frames is None:
        frames = len(vframes)
        warnings.append("no --frames: scored against the video's own length; pass the source "
                        "frame count for InfiniteTalk output")
    net = syncnet.load_model()
    crops = syncnet.face_crops(vframes, region)            # detect once, score twice
    windows = []
    with tempfile.TemporaryDirectory() as tmp:
        own_w = pad_audio(own, frames, Path(tmp) / "own.wav")
        dec_w = pad_audio(decoy, frames, Path(tmp) / "decoy.wav")
        r_own = syncnet.measure(video, own_w, region=region, net=net, frames=vframes, crops=crops)
        r_dec = syncnet.measure(video, dec_w, region=region, net=net, frames=vframes, crops=crops)
        speech, dec_speech = speech_seconds(own_w), speech_seconds(dec_w)
        if crops[0] is not None and min(frames, len(vframes)) >= 2 * WINDOW:
            windows = score_windows(net, crops[0], own_w, min(frames, len(vframes)), Path(tmp))
    v, reasons = verdict(r_own, r_dec, speech, dec_speech, windows)
    if r_own.get("face_h_px", 0) and r_own["face_h_px"] < FACE_FLOOR_PX:
        warnings.append(f"face {r_own['face_h_px']}px is under {FACE_FLOOR_PX}px, where no shot has passed yet")
    return {"verdict": v, "reasons": reasons, "warnings": warnings, "frames": frames,
            "speech_s": round(speech, 2), "decoy_speech_s": round(dec_speech, 2),
            "own": r_own, "decoy": r_dec, "windows": windows}


def score_windows(net, crops, own_wav: Path, frames: int, tmp: Path) -> list[dict]:
    """LSE on each full ``WINDOW``-frame stretch of a long line."""
    import soundfile as sf

    a, sr = sf.read(str(own_wav))
    out = []
    for k, start in enumerate(range(0, frames - WINDOW + 1, WINDOW)):
        seg = tmp / f"w{k}.wav"
        s0, s1 = int(round(start / syncnet.FPS * sr)), int(round((start + WINDOW) / syncnet.FPS * sr))
        sf.write(str(seg), a[s0:s1], sr, subtype="PCM_16")
        out.append({"start_frame": start,
                    **syncnet.score(net, crops[start:start + WINDOW], syncnet.load_audio(seg))})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("video", type=Path)
    ap.add_argument("own", type=Path)
    ap.add_argument("decoy", type=Path)
    ap.add_argument("--frames", type=int, help="source frame count at 25 fps")
    ap.add_argument("--region", choices=["left", "right"])
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    try:
        r = check(a.video, a.own, a.decoy, frames=a.frames, region=a.region)
    except Exception as exc:   # any failure to measure is "could not run", never a fail
        print(f"lipsync_check: could not measure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    o, d = r["own"], r["decoy"]
    if "lse_c" in o:
        print(f"own   LSE-C {o['lse_c']:.3f}  offset {o['offset']:+d}  face {o['face_h_px']}px  "
              f"faces {o['faces_pct']:.0%}")
    if "lse_c" in d:
        print(f"decoy LSE-C {d['lse_c']:.3f}  offset {d['offset']:+d}")
    for i, w in enumerate(r.get("windows") or []):
        print(f"  window {i} (from frame {w['start_frame']}) LSE-C {w['lse_c']:.3f}")
    print(f"speech {r['speech_s']}s (decoy {r.get('decoy_speech_s', '?')}s) → {r['verdict'].upper()}" + (f": {'; '.join(r['reasons'])}" if r["reasons"] else ""))
    for w in r["warnings"]:
        print(f"  ! {w}")
    if a.json:
        a.json.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"pass": 0, "fail": 1, "unmeasurable": 3}[r["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
