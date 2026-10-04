"""Is this still the same person? ArcFace similarity of a shot's face to approved references.

    python scripts/mv/lipsync/identity_check.py --ref hero=hero.png [--ref other=other.png]
        [--expect hero | --expect-lr hero,other] [--every 5] [--sheet sheet.png] [--json out.json]
        target.png target.mp4 ...

Per target (still or clip, every Nth frame), the face is embedded and compared
by cosine similarity with each reference:

- ``--expect NAME``: the largest face should be NAME.
- ``--expect-lr A,B``: in a two-shot, the leftmost face should be A and the
  next B.
- Neither: report every reference for the largest face.

Verdict per expected face, from the median cosine to its own reference:

    pass     median >= 0.45
    review   0.35 <= median < 0.45   — a human reads the crop sheet
    fail     median < 0.35

Two flags ride on top. Either one turns a pass into an exit-3 review:

    drift        the lowest sampled frame is under 0.35
    clone risk   the median to ANOTHER reference is >= 0.30

A face under 90 px tall is ``report-only``; judge it by eye. A face found in
under half the sampled frames is ``review``: a single good frame must not
pass a clip.

Calibration — the short-drama test's table (TEST-SPEC.md, 2026-09-19), cosine
to the person's own reference:

    reference vs itself, mirrored          0.906 - 0.915
    same person, stills at MCU/CU          0.447 - 0.782
    same person, video after v2v           0.437 - 0.737  (min per frame 0.393)
    same person, wide shot (42-61 px)      0.157 - 0.368  — unreliable
    different person, same styling         0.298 - 0.408
    the two leads vs each other           -0.03 - 0.16

The same person and a lookalike overlap between about 0.40 and 0.45, so
``review`` is a real question: a lookalike can land there. Treat review as
"ask the user", never as a quiet pass. The set is small (about 12 same-person
and 4 different-person samples, all Gemini-drawn East Asian men), and a woman
or a different generator may need the bands re-checked.

On the 〈說得太急〉 MV, two text-redrawn identity masters scored 0.211 and 0.223
against the original face, and the image-constrained one scored 0.616
(``character-identity-repair-v14.md``).

The sheet shows, per face, crops sampled evenly across the clip plus its
lowest-scoring frame, each with a 2x jaw zoom. The embedding does not see a
beard or apparent age, so the eye check stays mandatory.

Exit code: 0 all pass (or report-only), 1 any fail, 3 any review / drift /
clone risk, 2 could not run (bad input, missing dependency or model).
Run with the main Python (numpy, cv2, onnxruntime). The insightface models are
non-commercial and never bundled.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import face_onnx  # noqa: E402

PASS, REVIEW = 0.45, 0.35
DRIFT, CLONE = 0.35, 0.30
MIN_FACE_PX = 90
MIN_FACES = 0.5
SHEET_CROPS = 5
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv")


def frames_of(path: Path, every: int):
    """Yield (index, BGR frame): every Nth frame of a clip, or the one image."""
    import cv2

    if path.suffix.lower() in VIDEO_SUFFIXES:
        cap, i = cv2.VideoCapture(str(path)), 0
        got = False
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            got = True
            if i % every == 0:
                yield i, fr
            i += 1
        cap.release()
        if not got:
            raise ValueError(f"cannot read {path}")
    else:
        yield 0, face_onnx.read_image(path)


def ref_embedding(path: Path):
    _, img = next(frames_of(path, 1))
    face = face_onnx.largest(face_onnx.detect(img))
    if face is None:
        raise ValueError(f"no face in reference {path}")
    return face_onnx.embed(img, face[2])[0]


def jaw_zoom(img, box):
    import cv2
    import numpy as np

    x1, y1, x2, y2 = [int(round(t)) for t in box]
    h = y2 - y1
    j = img[max(0, y1 + h // 2):min(img.shape[0], y2 + h // 6), max(0, x1):min(img.shape[1], x2)]
    return cv2.resize(j, (224, 112)) if j.size else np.zeros((112, 224, 3), np.uint8)


def verdict(slot: dict, who: str, refs: list[str]) -> str:
    """Verdict text for one expected face's stats (see module doc)."""
    if slot.get("face_h_px", 0) < MIN_FACE_PX:
        return "report-only (face < 90 px)"
    med, low = slot[f"{who}_median"], slot[f"{who}_min"]
    v = "pass" if med >= PASS else "review" if med >= REVIEW else "fail"
    if v == "pass" and slot.get("faces_pct", 1.0) < MIN_FACES:
        v = f"review (face in {slot['faces_pct']:.0%} of frames)"
    if low < DRIFT:
        v += " / drift"
    if any(slot[f"{o}_median"] >= CLONE for o in refs if o != who):
        v += " / clone risk"
    return v


def sheet_picks(scores: list[float], n: int = SHEET_CROPS) -> list[int]:
    """Indexes of the crops to show: ``n`` spread across the clip, plus the lowest."""
    if not scores:
        return []
    k = len(scores)
    picks = sorted({round(i * (k - 1) / max(1, n - 1)) for i in range(min(n, k))})
    worst = min(range(k), key=lambda i: scores[i])
    return picks if worst in picks else picks + [worst]


def measure(target: Path, refs: dict, *, every: int = 5, expect: str | None = None,
            expect_lr: list[str] | None = None) -> tuple[dict, dict]:
    """Per-slot stats for one target, and per-slot sheet crops."""
    import cv2
    import numpy as np

    n_frames, per_slot, crops = 0, {}, {}
    first_ref = next(iter(refs))
    for _, img in frames_of(target, every):
        n_frames += 1
        faces = face_onnx.detect(img)
        if expect_lr:
            big = sorted(faces, key=lambda f: -(f[0][2] - f[0][0]) * (f[0][3] - f[0][1]))[:len(expect_lr)]
            chosen = list(zip(expect_lr, sorted(big, key=lambda f: f[0][0]))) if len(big) >= len(expect_lr) else []
        else:
            face = face_onnx.largest(faces)
            chosen = [(expect or "largest", face)] if face is not None else []
        for slot, (box, _, kps) in chosen:
            v, crop = face_onnx.embed(img, kps)
            row = {**{k: float(v @ r) for k, r in refs.items()}, "_face_h": float(box[3] - box[1])}
            per_slot.setdefault(slot, []).append(row)
            crops.setdefault(slot, []).append(np.hstack([cv2.resize(crop, (112, 112)), jaw_zoom(img, box)]))
    rec = {"target": str(target), "frames": n_frames, "slots": {}}
    sheet = {}
    for slot, rows in per_slot.items():
        s = {"faces_pct": round(len(rows) / n_frames, 3),
             "face_h_px": round(float(np.median([r["_face_h"] for r in rows])))}
        for k in refs:
            s[f"{k}_median"] = round(float(np.median([r[k] for r in rows])), 3)
            s[f"{k}_min"] = round(float(np.min([r[k] for r in rows])), 3)
        if slot in refs:
            s["verdict"] = verdict(s, slot, list(refs))
        rec["slots"][slot] = s
        own = slot if slot in refs else first_ref
        sheet[slot] = [crops[slot][i] for i in sheet_picks([r[own] for r in rows])]
    if not per_slot:
        rec["slots"] = {"(none)": {"faces_pct": 0.0,
                                   "verdict": "fail (no face)" if (expect or expect_lr) else "no face"}}
    return rec, sheet


def exit_code(results: list[dict]) -> int:
    verdicts = [s.get("verdict", "") for r in results for s in r["slots"].values()]
    if any(v.startswith("fail") for v in verdicts):
        return 1
    if any(v.startswith("review") or "/ drift" in v or "/ clone risk" in v for v in verdicts):
        return 3
    return 0


def write_sheet(path: Path, rows: list[tuple[str, list]]) -> None:
    import cv2
    import numpy as np

    blocks = []
    for label_text, crops in rows:
        row = np.hstack(crops)
        label = np.full((20, row.shape[1], 3), 255, np.uint8)
        # cv2 draws ASCII only; a Chinese file name would print as ???
        text = label_text.encode("ascii", "replace").decode()
        cv2.putText(label, text, (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
        blocks.append(np.vstack([label, row]))
    wmax = max(b.shape[1] for b in blocks)
    blocks = [np.hstack([b, np.full((b.shape[0], wmax - b.shape[1], 3), 255, np.uint8)]) for b in blocks]
    cv2.imencode(".png", np.vstack(blocks))[1].tofile(str(path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", action="append", required=True, help="name=path")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--expect")
    group.add_argument("--expect-lr")
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--sheet", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("targets", nargs="+", type=Path)
    a = ap.parse_args(argv)
    try:
        if a.every < 1:
            raise ValueError("--every must be at least 1")
        pairs = [r.split("=", 1) for r in a.ref]
        if any(len(p) != 2 for p in pairs):
            raise ValueError("--ref takes name=path")
        refs = {k: ref_embedding(Path(v)) for k, v in pairs}
        slots = a.expect_lr.split(",") if a.expect_lr else None
        for name in (slots or ([a.expect] if a.expect else [])):
            if name not in refs:
                raise ValueError(f"no --ref named {name}")
        results, rows = [], []
        for n, t in enumerate(a.targets, 1):
            rec, sheet = measure(t, refs, every=a.every, expect=a.expect, expect_lr=slots)
            results.append(rec)
            for slot, s in rec["slots"].items():
                cols = "  ".join(f"{k}: med {s[k + '_median']:+.3f} min {s[k + '_min']:+.3f}"
                                 for k in refs if k + "_median" in s)
                print(f"{t.name[:40]:40} {slot:8} faces={s['faces_pct']:.0%} "
                      f"h={s.get('face_h_px', 0):>4}px  {cols}  {s.get('verdict', '')}")
            for slot, crops in sheet.items():
                if crops:
                    rows.append((f"#{n} {t.name[:40]}  [{slot}]", crops))
        if a.json:
            a.json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        if a.sheet and rows:
            write_sheet(a.sheet, rows)
    except Exception as exc:   # any failure to measure is "could not run", never a fail
        print(f"identity_check: could not measure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
