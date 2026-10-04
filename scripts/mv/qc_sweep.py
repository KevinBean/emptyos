"""Living-master QC sweep — identity drift and sung-mouth offset, on what is actually cut in.

Run after every render, before a review page goes to the user:

    python scripts/mv/qc_sweep.py <living-master dir> --ref her=HER.jpg --ref him=HIM.jpg \
        --vocals analysis/.../vocals.wav [--every 6] [--video living-master-rNNN.mp4]

Identity: for every slot, the frames of the selected source window (not the
whole source clip) are sampled every ``--every`` frames. Each detected face at
least ``MIN_FACE_PX`` tall is compared with every reference; a face whose best
match is under ``UNKNOWN`` is flagged as someone the film has not approved.
Per-frame, not per-clip: a clip that drifts only in its last seconds (faces
growing as people walk up to camera) has a healthy median and one bad tail,
which is exactly what a whole-clip median hides.

Lip-sync: every slot the plan marks ``sing`` is cut out of the rendered master
and scored with ``lipsync_check.py`` against its own vocal-stem window and a
decoy window of the same length 20 s earlier, then again in overlapping
``WIN``-second windows. Flags a non-pass verdict, a whole-slot or confident-window
offset outside the eye-accepted band ``GOOD_OFFSETS`` —
a whole-slot score hides a back-half slip behind a well-synced opening.

Writes ``<dir>/qc/qc-r<rev>.md`` + ``.json`` and a crop sheet of flagged faces.
Exit 0 clean, 1 anything flagged, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lipsync"))

# Copied from the sibling checkers, not imported (importing them loads the ONNX
# models); tests/test_unit_mv_qc_sweep.py pins each against the sibling's source.
MIN_FACE_PX = 90         # identity_check.MIN_FACE_PX
UNKNOWN = 0.35           # identity_check.DRIFT: below it the face is not the reference
CONFIDENT = 4.5          # lipsync_check.MIN_LSE_C: a window below it cannot place its offset

# Tuned on 〈在你說完以前〉 r158-r178 (2026-09-28), where the untuned sweep flagged
# healthy slots; each value is the smallest that cleared those false positives:
MIN_FLAGS = 2            # flagged frames a slot needs (single-frame detector misfires)
MIN_SHARP = 60.0         # Laplacian variance of the 112 px aligned crop; below = deliberately defocused
MAX_YAW = 0.45           # |nose - eye midpoint| / eye distance; above = near profile
WIN = 1.6                # lip-sync window, s: lipsync_check needs >= 1 s of speech; 1.6 s localises a
                         # slip to half a sung line
# SyncNet offset (frames at 25 fps, negative = picture leads) that LOOKS right on
# InfiniteTalk output is not 0. Blind test on that MV, 2026-09-28: the same
# excerpt with only the sung slots changed. Raw clips (every window -2..-4) passed
# the user's eye; the same clips padded to read 0/+1 were rejected as out of sync.
# So every confident window is judged against the measured band, never against 0.
# The evidence is thin: 3 slots, 13 windows, one singer, one MV, all medians -3.
# Anything outside what was seen is flagged for the eye rather than assumed fine.
# (An earlier version of this file called -1/-2 windows "slips"; those readings came
# from clips already padded toward 0, so they share no reference point with these.)
GOOD_OFFSETS = (-4, -2)  # inclusive; the accepted windows' full range


def off_band(offset: float) -> bool:
    """True when an offset falls outside the eye-accepted range (late above it, early below)."""
    return not GOOD_OFFSETS[0] <= offset <= GOOD_OFFSETS[1]
DECOY_SHIFT = 20.0       # s; decoy = the same voice this far away, so the words differ


def yaw_ratio(kps) -> float:
    """0 for a frontal face, rising toward profile (5-point landmarks: eyes, nose, mouth)."""
    le, re, nose = kps[0], kps[1], kps[2]
    eye_d = max(1.0, abs(float(re[0] - le[0])))
    return abs(float(nose[0] - (le[0] + re[0]) / 2)) / eye_d
TIMELINE = "living-master-timeline.json"
_REV = re.compile(r"^living-master-r(\d+)\.mp4$", re.IGNORECASE)


def rendered_master(folder: Path) -> Path | None:
    best = None
    for p in folder.glob("living-master-r*.mp4"):
        m = _REV.match(p.name)
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), p)
    return best[1] if best else None


def identity_sweep(timeline: dict, folder: Path, refs: dict, every: int) -> list[dict]:
    import cv2
    import face_onnx
    from identity_check import ref_embedding

    emb = {k: ref_embedding(Path(v)) for k, v in refs.items()}
    fps = timeline["fps"]
    out = []
    for s in timeline["slots"]:
        src = Path(s.get("source") or "")
        if not src.is_absolute():
            src = folder / src
        if not src.is_file():
            if s.get("source_kind") in ("video", "still"):   # a placeholder has no source to check
                out.append({"id": s["stable_id"], "checked": 0, "flags": [], "error": f"source missing: {src}",
                            "skipped": {"blurred": 0, "profile": 0}})
            continue
        if s.get("source_kind") != "video":
            if src.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            frames = [(0, face_onnx.read_image(src))]
        else:
            cap = cv2.VideoCapture(str(src))
            if not cap.isOpened():
                out.append({"id": s["stable_id"], "checked": 0, "flags": [], "error": f"cannot decode: {src}",
                            "skipped": {"blurred": 0, "profile": 0}})
                continue
            frames = []
            for i in range(int(s["start_frame"]), int(s["end_frame"]), every):
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                ok, fr = cap.read()
                if ok:
                    frames.append((i - int(s["start_frame"]), fr))
            cap.release()
        checked, flags, skipped = 0, [], {"blurred": 0, "profile": 0}
        for rel, img in frames:
            for box, _, kps in face_onnx.detect(img):
                h = float(box[3] - box[1])
                if h < MIN_FACE_PX:
                    continue
                v, crop = face_onnx.embed(img, kps)
                # A deliberately defocused face and a side profile both embed badly even
                # when the person is right, so they are counted for the human eye, not flagged.
                if cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() < MIN_SHARP:
                    skipped["blurred"] += 1
                    continue
                if yaw_ratio(kps) > MAX_YAW:
                    skipped["profile"] += 1
                    continue
                scores = {k: float(v @ r) for k, r in emb.items()}
                best = max(scores, key=scores.get)
                checked += 1
                if scores[best] < UNKNOWN:
                    flags.append({"t": round(rel / fps, 2), "face_px": round(h), "best": best,
                                  "score": round(scores[best], 3), "crop": crop})
        if len(flags) < MIN_FLAGS:  # one odd frame is detector noise, not a different person
            flags = []
        out.append({"id": s["stable_id"], "checked": checked, "flags": flags, "skipped": skipped})
    return out


def _score(master: Path, vocals: Path, a: float, dur: float, tmp: Path, tag: str) -> dict | None:
    # Decoy: the same voice DECOY_SHIFT away. Nothing guarantees different words — a
    # decoy landing on a repeated chorus line scores like the real one and reads as
    # a margin failure; check such a FLAG by eye before regenerating.
    d0 = a - DECOY_SHIFT if a >= DECOY_SHIFT else a + DECOY_SHIFT
    clip, own, dec, js = (tmp / f"{tag}{x}" for x in (".mp4", ".wav", "-d.wav", ".json"))
    js.unlink(missing_ok=True)
    cut = ["ffmpeg", "-v", "error", "-y"]
    subprocess.run(cut + ["-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(master), "-an",
                          "-c:v", "libx264", "-crf", "16", str(clip)], check=True)
    for start, wav in ((a, own), (d0, dec)):
        subprocess.run(cut + ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(vocals),
                              "-ac", "1", "-ar", "16000", str(wav)], check=True)
    subprocess.run([sys.executable, str(HERE / "lipsync" / "lipsync_check.py"), str(clip), str(own),
                    str(dec), "--frames", str(round(dur * 25)), "--json", str(js)],
                   capture_output=True, text=True)
    return json.loads(js.read_text(encoding="utf-8")) if js.is_file() else None


def windows(dur: float) -> list[float]:
    """Window starts covering [0, dur): WIN long, WIN/2 apart, the last flush with the end."""
    if dur <= WIN:
        return []
    starts = [k * WIN / 2 for k in range(int((dur - WIN) / (WIN / 2)) + 1)]
    if dur - WIN - starts[-1] > 0.05:
        starts.append(dur - WIN)
    return starts


def median_offset(win: list[dict]) -> float | None:
    """Median offset of the confident windows, or None when fewer than two (one is not a trend)."""
    offs = sorted(w["offset"] for w in win if w["lse_c"] >= CONFIDENT)
    if len(offs) < 2:
        return None
    return (offs[(len(offs) - 1) // 2] + offs[len(offs) // 2]) / 2



def lipsync_sweep(timeline: dict, plan: list[dict], master: Path, vocals: Path) -> list[dict]:
    """Whole-slot verdict plus a sliding-window offset trace.

    The whole-slot score is dominated by the strongest phrase — usually the
    opening — so a mouth that slips a frame in the back half still reads as
    in band. Viewers see the slip exactly there, so every confident window must
    sit inside ``GOOD_OFFSETS``; the median is reported, not gated (a median
    outside the band implies a window outside it).
    """
    fps = timeline["fps"]
    sing = {str(p.get("id") or f"S{i + 1:03d}") for i, p in enumerate(plan) if p.get("sing")}
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for s in (s for s in timeline["slots"] if s["stable_id"] in sing):
            a = s["destination_start_frame"] / fps
            dur = (s["destination_end_frame"] - s["destination_start_frame"]) / fps
            r = _score(master, vocals, a, dur, tmp, s["stable_id"])
            if r is None or r.get("own", {}).get("offset") is None:
                # no JSON, or SyncNet found no face: an unmeasured slot, never a clean one
                why = (r or {}).get("own", {}).get("error") or "lipsync_check wrote no result"
                out.append({"id": s["stable_id"], "error": why})
                continue
            win, unscored = [], []
            for w0 in windows(dur):
                wr = _score(master, vocals, a + w0, WIN, tmp, f"{s['stable_id']}-w")
                if wr is None or wr.get("own", {}).get("offset") is None:
                    unscored.append(round(w0, 2))
                    continue
                win.append({"t": round(w0, 2), "offset": wr["own"]["offset"],
                            "lse_c": round(wr["own"].get("lse_c", 0), 2)})
            med = median_offset(win)
            out.append({"id": s["stable_id"], "verdict": r.get("verdict"), "offset": r["own"]["offset"],
                        "lse_c": round(r["own"].get("lse_c", 0), 2), "decoy": round(r["decoy"].get("lse_c", 0), 2),
                        "windows": win, "unscored": unscored,
                        "slipped": [w for w in win if off_band(w["offset"]) and w["lse_c"] >= CONFIDENT],
                        "median": med,
                        "weak": [w for w in win if w["lse_c"] < CONFIDENT]})
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir", type=Path)
    ap.add_argument("--ref", action="append", required=True, help="name=path (repeat)")
    ap.add_argument("--vocals", type=Path, help="vocal stem for the lip-sync check (omit to skip it)")
    ap.add_argument("--video", type=Path, help="rendered master (default: newest living-master-rNNN.mp4)")
    ap.add_argument("--every", type=int, default=6)
    args = ap.parse_args(argv)
    try:
        import cv2
        import numpy as np

        folder = args.dir
        timeline = json.loads((folder / TIMELINE).read_text(encoding="utf-8"))
        plan_p = folder / "plan.json"
        plan = json.loads(plan_p.read_text(encoding="utf-8")) if plan_p.is_file() else []
        refs = dict(r.split("=", 1) for r in args.ref)
        ident = identity_sweep(timeline, folder, refs, args.every)
        lips = []
        if args.vocals:
            master = args.video or rendered_master(folder)
            if master is None:
                raise FileNotFoundError("no rendered living-master-rNNN.mp4; pass --video")
            if not any(p.get("sing") for p in plan):
                raise ValueError(f"--vocals given but {folder / 'plan.json'} marks no slot \"sing\": true")
            lips = lipsync_sweep(timeline, plan, master, args.vocals)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"qc sweep: {exc}", file=sys.stderr)
        return 2

    rev = timeline.get("revision")
    qc = folder / "qc"
    qc.mkdir(exist_ok=True)
    bad_id = [r for r in ident if r["flags"] or r.get("error")]
    # A slot with windows but none scored has only the whole-slot number — the blind spot.
    bad_lip = [r for r in lips if r.get("error") or r.get("verdict") != "pass" or off_band(r["offset"])
               or r.get("slipped") or (r.get("unscored") and not r.get("windows"))]
    lines = [f"# QC r{rev}", "",
             f"Identity: {sum(r['checked'] for r in ident)} faces ≥{MIN_FACE_PX}px checked in "
             f"{len(ident)} slots; {len(bad_id)} slot(s) with a face that matches no reference (< {UNKNOWN}).", ""]
    for r in bad_id:
        if r.get("error"):
            lines.append(f"- **{r['id']}**: not checked — {r['error']}")
            continue
        worst = min(r["flags"], key=lambda f: f["score"])
        lines.append(f"- **{r['id']}**: {len(r['flags'])}/{r['checked']} faces unknown; worst at "
                     f"{worst['t']} s ({worst['face_px']} px, best {worst['best']} {worst['score']})")
    eye = [f"{r['id']}（側臉 {r['skipped']['profile']}、失焦 {r['skipped']['blurred']}）"
           for r in ident if r["skipped"]["profile"] + r["skipped"]["blurred"] >= MIN_FLAGS]
    if eye:
        lines += ["", "Judge by eye (profile / defocused faces are not scored): " + "、".join(eye)]
    lines += ["", f"Lip-sync: {len(lips)} sung slot(s); {len(bad_lip)} flagged.", ""]
    for r in lips:
        mark = "**FLAG** " if r in bad_lip else ""
        detail = r.get("error") or (f"{r['verdict']}, offset {r['offset']:+d}, "
                                    f"LSE-C {r['lse_c']} vs decoy {r['decoy']}")
        lines.append(f"- {mark}{r['id']}: {detail}")
        if r.get("windows"):
            trace = "  ".join(f"{w['t']}s {w['offset']:+d}/{w['lse_c']}" for w in r["windows"])
            lines.append(f"  - windows ({WIN}s, offset/LSE-C): {trace}")
            if r.get("median") is not None:
                lines.append(f"  - confident median {r['median']:+.1f} (good {GOOD_OFFSETS[0]}..{GOOD_OFFSETS[1]})")
        if r.get("unscored"):
            lines.append(f"  - unscored windows (no face / no result): " + ", ".join(f"{t}s" for t in r["unscored"]))
        if r.get("weak"):
            lines.append(f"  - weak mouth (LSE-C < {CONFIDENT}, judge by eye): "
                         + ", ".join(f"{w['t']}s" for w in r["weak"]))
    (qc / f"qc-r{rev}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (qc / f"qc-r{rev}.json").write_text(json.dumps(
        {"identity": [{**r, "flags": [{k: v for k, v in f.items() if k != "crop"} for f in r["flags"]]} for r in ident],
         "lipsync": lips}, ensure_ascii=False, indent=1), encoding="utf-8")
    crops = [cv2.putText(cv2.resize(f["crop"], (160, 160)), f"{r['id']} {f['t']}s", (4, 16), 0, 0.45, (0, 255, 255), 1)
             for r in bad_id for f in r["flags"][:6]]
    if crops:
        rows = [np.hstack(crops[i:i + 8] + [np.zeros_like(crops[0])] * (8 - len(crops[i:i + 8])))
                for i in range(0, len(crops), 8)]
        cv2.imwrite(str(qc / f"qc-r{rev}-faces.jpg"), np.vstack(rows))
    print("\n".join(lines))
    return 1 if bad_id or bad_lip else 0


if __name__ == "__main__":
    sys.exit(main())
