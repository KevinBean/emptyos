"""Clip profiles — what each MV clip shows, and whether two clips can be cut together.

    python scripts/mv/clip_profile.py screen <clip>... --ref her=a.jpg --ref him=b.jpg
    python scripts/mv/clip_profile.py draft <clip> --asset-id P:S008-A --location rehearsal-room --project P
    python scripts/mv/clip_profile.py record <rows.json>
    python scripts/mv/clip_profile.py joins <living-master dir>

Kevin 2026-09-29, on 〈在你說完以前〉 v7: half of his 18 shot notes were continuity
breaks that nothing recorded — a window style that changed, a top that changed
colour, the light across a cut, a traffic light on the other side of the road, a
stranger walking in. "We need this and this should be a standard for producing."

A profile is the ``profile`` object of the clip's asset row in the MV library
(SCHEMA §2a) — the library is the footage bin, keyed by sha256.

- ``screen`` measures: per-frame faces (who, size, x), identity against the refs,
  motion and jumps. Writes ``<clip>.screen.json`` + ``<clip>.sheet.jpg``.
- ``draft`` builds the asset row from the screen: people (faces that PASS identity)
  and their side, identity-fail and jump seconds as ``reject`` windows, everything
  else as usable. Light, wardrobe, set details and action are left EMPTY on
  purpose — the machine cannot see them. Only ``light.state`` is required to
  validate; the rest stays unknown until the eye pass fills it from the sheet and
  sets ``reviewed``. An empty value is "unknown", never "changed".
- ``record`` validates and upserts through ``mv_library.record``. For an asset that
  already exists it replaces only ``profile`` (and adds the project), keeping the
  row's status, origin and rights.
- ``joins`` walks a living master: each slot's source sha256 → its profile. At each
  cut (stills between two clips are skipped, not treated as a break): light (state,
  key source, colour) in the same place, one of two people switching L↔R, and a
  jump inside one source. Against the LAST appearance, however far
  back: the set version and set details of that place, and each person's
  wardrobe. Per slot: the used window touching a reject window or a defect.
  Exit 1 on any unwaived conflict, on a video slot with no profile, or on a
  draft (unverified is not a pass). Stills and storyboard cards are not profiled
  and are only counted. A deliberate break is waived in the plan:
  ``"join_ok": "<reason>"`` on the slot after the cut waives what changed at that
  cut; ``"change_ok": "<reason>"`` waives a change against an EARLIER shot (a
  costume change between acts). Neither waives a defect. It does NOT see a stranger
  or a face change on its own — those are caught only once the eye pass records
  them as ``defects``. Nothing runs it automatically; it is a step before review.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import living_master  # noqa: E402  — timeline loader + sha256_file
import mv_library as lib  # noqa: E402

SAMPLE_EVERY = 6         # frames between identity samples (4 per second at 24 fps)
SHEET_EVERY = 12
# Copied from identity_check (importing it loads the face models); pinned against its
# source by tests/test_unit_mv_clip_profile.py.
MIN_FACE_PX = 90         # identity_check.MIN_FACE_PX: below it ArcFace is noise
DRIFT = 0.35             # identity_check.DRIFT: below it the face is not the reference
PASS = 0.45              # identity_check.PASS: 0.35-0.45 is "review" — a lookalike overlaps there
JUMP_FLOOR, JUMP_X = 6.0, 4.0   # from the 在你說完以前 project tool tools/clean_windows.py (vault, not repo): a frame diff > max(6, 4x median) is a pop / morph
# Judgment values, not measurements; the eye pass corrects what they get wrong.
MIN_PRESENCE = 0.25      # seen in a quarter of the samples: a face in one sample is a detector misfire
MIN_USABLE_S = 1.0       # a clean gap under 1 s is too short for a slot (the same project's tools/check_cuts.py floor is 2 s; 1 s leaves room for short_ok accents)
JUMP_PAD_S = 0.1         # ± ~2 frames at 24 fps around a jump frame


# ── screen (needs cv2 + the face models; imported lazily) ────────────────────

def screen(path: Path, refs: dict[str, list[Path]], *, clip_label: str | None = None) -> dict:
    import cv2
    import numpy as np
    sys.path.insert(0, str(HERE / "lipsync"))
    import face_onnx
    from identity_check import ref_embedding

    embs = [(who, ref_embedding(p)) for who, ps in refs.items() for p in ps]
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    if not frames:
        raise SystemExit(f"cannot read any frame from {path}")
    h0, w0 = frames[0].shape[:2]
    g = [cv2.cvtColor(cv2.resize(f, (320, 180)), cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames]
    diff = [0.0] + [float(np.abs(g[i] - g[i - 1]).mean()) for i in range(1, len(g))]
    med = float(np.median(diff[1:])) if len(diff) > 1 else 0.0
    jumps = [i for i in range(1, len(diff)) if diff[i] > max(JUMP_FLOOR, JUMP_X * med)]
    samples = []
    for i in range(0, len(frames), SAMPLE_EVERY):
        faces = []
        for box, _, kps in face_onnx.detect(frames[i]):
            fh = float(box[3] - box[1])
            if fh < MIN_FACE_PX:
                continue
            v, _ = face_onnx.embed(frames[i], kps)
            who, score = max(((w, float(v @ r)) for w, r in embs), key=lambda t: t[1])
            faces.append({"x": round(float(box[0])), "w": round(float(box[2] - box[0])), "h": round(fh),
                          "who": who, "score": round(score, 3)})
        samples.append({"f": i, "t": round(i / fps, 2), "faces": faces,
                        "motion": round(float(np.median(diff[max(1, i - 3):i + 3])), 2)})
    out = {"clip": clip_label or path.as_posix(), "frames": len(frames), "fps": fps, "width": w0, "height": h0,
           "median_motion": round(med, 2), "jumps_s": [round(j / fps, 2) for j in jumps],
           "identity_fail_s": [s["t"] for s in samples if any(f["score"] < DRIFT for f in s["faces"])],
           "identity_review_s": [s["t"] for s in samples if any(DRIFT <= f["score"] < PASS for f in s["faces"])],
           "samples": samples}
    path.with_suffix(".screen.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    tiles = []
    for i in range(0, len(frames), SHEET_EVERY):
        t = cv2.resize(frames[i], (384, 216))
        s = min(samples, key=lambda x: abs(x["f"] - i))
        lab = f"{i / fps:.1f}s m{diff[i]:.1f} " + " ".join(f"{f['who']}{f['score']:.2f}" for f in s["faces"])
        bad = any(f["score"] < DRIFT for f in s["faces"]) or any(abs(i - j) <= 2 for j in jumps)
        cv2.rectangle(t, (0, 0), (384, 22), (0, 0, 0), -1)
        cv2.putText(t, lab, (4, 16), 0, 0.5, (0, 0, 255) if bad else (0, 255, 255), 1)
        tiles.append(t)
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    # cv2.imwrite fails silently on a non-ASCII Windows path: encode, then write bytes
    ok, buf = cv2.imencode(".jpg", np.vstack([np.hstack(tiles[r:r + 4]) for r in range(0, len(tiles), 4)]),
                           [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise SystemExit(f"could not encode the contact sheet for {path}")
    path.with_suffix(".sheet.jpg").write_bytes(buf.tobytes())
    return out


# ── draft (pure: screen dict in, asset row out) ──────────────────────────────

def side_of(center_x: float, width: float) -> str:
    return "L" if center_x < width / 3 else "R" if center_x > 2 * width / 3 else "C"


def merge_spans(spans: list[list]) -> list[list]:
    """Merge overlapping / touching [t0, t1, label] spans that share a label."""
    out: list[list] = []
    for t0, t1, lab in sorted(spans, key=lambda s: (s[2], s[0])):
        if out and out[-1][2] == lab and t0 <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], t1)
        else:
            out.append([t0, t1, lab])
    return sorted(out, key=lambda s: s[0])


def usable_between(rejects: list[list], duration: float, min_len: float = MIN_USABLE_S) -> list[list]:
    spans, t = [], 0.0
    for r0, r1, _ in sorted(rejects, key=lambda s: s[0]):
        if r0 - t >= min_len:
            spans.append([round(t, 2), round(r0, 2), ""])
        t = max(t, r1)
    if duration - t >= min_len:
        spans.append([round(t, 2), round(duration, 2), ""])
    return spans


def draft_profile(scr: dict, *, location: str) -> dict:
    fps = float(scr.get("fps") or 24.0)
    duration = round(scr["frames"] / fps, 3)
    step = SAMPLE_EVERY / fps
    width = scr.get("width")
    samples = scr.get("samples") or []
    seen: dict[str, list[float]] = {}
    for s in samples:
        for f in s["faces"]:
            if f["score"] >= PASS:
                cx = f["x"] + f.get("w", f["h"]) / 2
                seen.setdefault(f["who"], []).append(cx)
    people = []
    for who, xs in sorted(seen.items()):
        if len(xs) < MIN_PRESENCE * max(1, len(samples)):
            continue
        xs = sorted(xs)
        person: dict[str, Any] = {"who": who, "wardrobe": {}, "facing": "", "mouth": None, "framing": None}
        person["side"] = side_of(xs[len(xs) // 2], width) if width else None
        people.append(person)
    rejects = [[t, min(duration, round(t + step, 2)), "identity_drift"] for t in scr.get("identity_fail_s", [])]
    rejects += [[max(0.0, round(t - JUMP_PAD_S, 2)), min(duration, round(t + JUMP_PAD_S, 2)), "unmotivated_motion"]
                for t in scr.get("jumps_s", [])]
    rejects = merge_spans(rejects)
    return {"location": location, "set_version": None, "time_of_day": None,
            "light": {"state": "", "key_source": None, "colour": None}, "set_details": {},
            "people": people, "camera": {"motion": None, "framing": None},
            "action": {"entry_state": None, "exit_state": None, "travel": None},
            "windows": {"usable": usable_between(rejects, duration), "reject": rejects},
            "defects": [], "intensity": None, "mood": None,
            "evidence": {}, "reviewed": None}


def vault_rel(p: Path, vault: Path | None) -> str:
    try:
        return p.resolve().relative_to(vault.resolve()).as_posix() if vault else p.as_posix()
    except ValueError:
        return p.as_posix()


def draft_row(clip: Path, scr: dict, *, asset_id: str, location: str, project: str | None,
              vault: Path | None, platform: str | None = None, model: str | None = None) -> dict:
    if not scr.get("width"):          # screens written before width was recorded
        import cv2
        cap = cv2.VideoCapture(str(clip))
        scr = {**scr, "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None,
               "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None}
        cap.release()
    prof = draft_profile(scr, location=location)
    for kind in ("screen", "sheet"):
        side = clip.with_suffix(f".{kind}.json" if kind == "screen" else ".sheet.jpg")
        if side.exists():
            prof["evidence"][kind] = vault_rel(side, vault)
    fps = float(scr.get("fps") or 24.0)
    return {"asset_id": asset_id, "kind": "motion-plate", "path": vault_rel(clip, vault),
            "sha256": living_master.sha256_file(clip), "width": scr.get("width"), "height": scr.get("height"),
            "duration_seconds": round(scr["frames"] / fps, 3), "fps": fps,
            "origin": {"platform": platform, "model": model, "attempt_id": None}, "rights": {},
            "projects_used": [project] if project else [], "status": "candidate",
            "status_reason": "profiled from screen; awaiting eye pass", "profile": prof}


def merge_for_record(stored: list[dict], incoming: list[dict]) -> list[dict]:
    """A profile added to an asset that already exists replaces only ``profile`` and adds
    the project; the row's status, origin, rights and the rest are the library's, not the
    draft's (a draft always says ``candidate`` with an empty origin)."""
    by_id = {r.get("asset_id"): r for r in stored if isinstance(r, dict)}
    out = []
    for r in incoming:
        old = by_id.get(r.get("asset_id")) if isinstance(r, dict) else None
        if old is None:
            out.append(r)
            continue
        if old.get("sha256") and r.get("sha256") and old["sha256"] != r["sha256"]:
            raise ValueError(f"{r['asset_id']}: the file changed (sha {old['sha256'][:12]} → {r['sha256'][:12]}); "
                             "a profile is measured on one file — record the new file under a new asset_id")
        projects = list(dict.fromkeys(lib.as_list(old.get("projects_used")) + lib.as_list(r.get("projects_used"))))
        out.append({**old, "profile": r["profile"] if "profile" in r else old.get("profile"), "projects_used": projects})
    return out


# ── joins (pure: timeline + plan + assets in, findings out) ──────────────────

def _overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 < b1 and b0 < a1


def slot_findings(slot: dict, row: dict | None, fps: float) -> list[dict]:
    sid = slot["stable_id"]
    if row is None or not isinstance(row.get("profile"), dict):
        return [{"cut": None, "slot": sid, "kind": "no_profile", "detail": f"no profile for source sha {str(slot.get('source_sha256'))[:12]}"}]
    p = row["profile"]
    out = []
    if not p.get("reviewed"):
        out.append({"cut": None, "slot": sid, "kind": "unverified", "detail": "profile is a draft (no eye pass)"})
    src_fps = float(row.get("fps") or fps)
    u0, u1 = slot["start_frame"] / src_fps, slot["end_frame"] / src_fps
    for t0, t1, code in (p.get("windows") or {}).get("reject", []):
        if _overlaps(u0, u1, t0, t1):
            out.append({"cut": None, "slot": sid, "kind": "uses_reject",
                        "detail": f"used {u0:.2f}-{u1:.2f}s overlaps reject {t0}-{t1}s ({code})"})
    for d in p.get("defects") or []:
        if _overlaps(u0, u1, d["t0"], d["t1"]):
            out.append({"cut": None, "slot": sid, "kind": "uses_defect",
                        "detail": f"used {u0:.2f}-{u1:.2f}s overlaps defect {d['t0']}-{d['t1']}s ({d['code']}: {d.get('note', '')})"})
    return out


LIGHT_FIELDS = ("state", "key_source", "colour")


def _place(p: dict) -> str:
    """Location as compared: free text, so case and stray spaces must not split one place in two."""
    return " ".join(str(p.get("location") or "").casefold().split())


def _people(p: dict) -> dict[str, dict]:
    return {x["who"]: x for x in p.get("people") or [] if isinstance(x, dict) and x.get("who")}


def _diff(da: dict, db: dict) -> list[tuple[str, str, str]]:
    """Keys both sides filled in, with different values. An empty value is unknown, never a change."""
    return [(k, da[k], db[k]) for k in sorted(da.keys() & db.keys()) if da[k] and db[k] and da[k] != db[k]]


def cut_findings(a: dict, pa: dict, b: dict, pb: dict) -> list[dict]:
    """What changes at the cut itself: light in the same place, a person switching sides,
    and a jump inside one source (two windows of the same clip that do not meet)."""
    cut = f"{a['stable_id']}→{b['stable_id']}"
    out = []

    def add(kind, detail):
        out.append({"cut": cut, "slot": b["stable_id"], "kind": kind, "detail": detail})

    if a.get("source_sha256") and a.get("source_sha256") == b.get("source_sha256") \
            and b.get("start_frame") != a.get("end_frame"):
        add("jump_cut", f"same source, frame {a.get('end_frame')} then {b.get('start_frame')}")
    if _place(pa) != _place(pb):
        return out                     # a new place: light and screen sides may change
    people_a, people_b = _people(pa), _people(pb)
    shared = people_a.keys() & people_b.keys()
    # Screen side only means something relative to someone else: a lone close-up
    # framed left then right is not a 180° break, two people swapping sides is.
    if len(shared) >= 2:
        for who in sorted(shared):
            sa, sb = people_a[who].get("side"), people_b[who].get("side")
            if {sa, sb} == {"L", "R"}:
                add("side_flip", f"{who} {sa} → {sb}")
    for k, x, y in _diff(pa.get("light") or {}, pb.get("light") or {}):
        if k in LIGHT_FIELDS:
            add("light", f"{k}: {x!r} → {y!r}")
    return out


def memory_findings(slot: dict, p: dict, last_place: dict, last_person: dict) -> list[dict]:
    """What must match the LAST time this place or person was on screen, however far back:
    a set detail or set version of the same location, and a person's wardrobe. Each value
    remembers the slot it was seen in, so a finding names the shot to compare against.
    (Kevin's S049 note: her top changed colour, but the shot before it showed only his arm.)"""
    out = []

    def add(kind, prev, detail):
        out.append({"cut": f"{prev}→{slot['stable_id']}", "prev": prev, "slot": slot["stable_id"],
                    "kind": kind, "detail": detail})

    place = last_place.get(_place(p), {})
    if place.get("set_version") and p.get("set_version") and place["set_version"][1] != p["set_version"]:
        add("set_version", place["set_version"][0], f"{place['set_version'][1]} → {p['set_version']}")
    for k, v in sorted((p.get("set_details") or {}).items()):
        seen = (place.get("set_details") or {}).get(k)
        if v and seen and seen[1] != v:
            add("set_detail", seen[0], f"{k}: {seen[1]!r} → {v!r}")
    for who, person in sorted(_people(p).items()):
        worn = last_person.get(who, {})
        for k, v in sorted((person.get("wardrobe") or {}).items()):
            if v and k in worn and worn[k][1] != v:
                add("wardrobe", worn[k][0], f"{who} {k}: {worn[k][1]!r} → {v!r}")
    return out


def _remember(slot: dict, p: dict, last_place: dict, last_person: dict) -> None:
    """Keep, per key, the last value seen and where. An empty value is unknown and keeps the old one."""
    sid = slot["stable_id"]
    loc = _place(p)
    if loc:
        place = last_place.setdefault(loc, {"set_version": None, "set_details": {}})
        if p.get("set_version"):
            place["set_version"] = (sid, p["set_version"])
        for k, v in (p.get("set_details") or {}).items():
            if v:
                place["set_details"][k] = (sid, v)
    for who, person in _people(p).items():
        worn = last_person.setdefault(who, {})
        for k, v in (person.get("wardrobe") or {}).items():
            if v:
                worn[k] = (sid, v)


def is_footage(slot: dict) -> bool:
    """Only video clips carry a profile; a still or a storyboard card is counted, not checked."""
    return (slot.get("source_kind") in (None, "video") and slot.get("asset_state") != "placeholder"
            and bool(slot.get("source_sha256")))


def joins(timeline: dict, plan: list[dict], assets: list[dict]) -> dict:
    by_sha: dict[str, list[dict]] = {}
    for r in assets:
        if isinstance(r, dict) and r.get("sha256") and isinstance(r.get("profile"), dict):
            by_sha.setdefault(r["sha256"], []).append(r)
    # living_master gives an id-less plan entry the id S{i+1:03d}; a waiver must follow it
    ids = [str(p.get("id") or f"S{i + 1:03d}") for i, p in enumerate(plan) if isinstance(p, dict)]
    entries = [p for p in plan if isinstance(p, dict)]
    cut_ok = {sid: p["join_ok"] for sid, p in zip(ids, entries) if p.get("join_ok")}
    change_ok = {sid: p["change_ok"] for sid, p in zip(ids, entries) if p.get("change_ok")}
    fps = float(timeline.get("fps") or 24)
    slots = timeline["slots"]
    found: list[dict] = []
    rows: list[dict | None] = []
    for s in slots:
        cands = by_sha.get(s.get("source_sha256")) or []
        if len(cands) > 1:
            found.append({"cut": None, "slot": s["stable_id"], "kind": "duplicate_sha",
                          "detail": f"{len(cands)} profiled rows share this source: {', '.join(c.get('asset_id', '?') for c in cands)}"})
        rows.append(cands[-1] if cands else None)
    profs = [r["profile"] if r else None for r in rows]
    footage = [is_footage(s) for s in slots]
    last_place: dict = {}
    last_person: dict = {}
    prev_sid: dict[str, str | None] = {}
    prev = None                    # the previous FOOTAGE slot: a still between two clips does not break the cut
    for i, (s, r) in enumerate(zip(slots, rows)):
        if not footage[i]:
            continue
        prev_sid[s["stable_id"]] = slots[prev]["stable_id"] if prev is not None else None
        found += slot_findings(s, r, fps)
        if profs[i] is not None:
            if prev is not None and profs[prev] is not None:
                found += [{**f, "prev": slots[prev]["stable_id"]} for f in cut_findings(slots[prev], profs[prev], s, profs[i])]
            found += memory_findings(s, profs[i], last_place, last_person)
            _remember(s, profs[i], last_place, last_person)
        prev = i
    for f in found:
        if not f["cut"]:
            continue                   # slot findings (defects, rejects, no profile) are never waived here
        if f.get("prev") == prev_sid.get(f["slot"]) and f["slot"] in cut_ok:
            f["waived"] = cut_ok[f["slot"]]
        elif f.get("prev") != prev_sid.get(f["slot"]) and f["slot"] in change_ok:
            f["waived"] = change_ok[f["slot"]]
    open_ = [f for f in found if not f.get("waived")]
    return {"revision": timeline.get("revision"), "slots": len(slots), "not_footage": footage.count(False),
            "findings": found, "open": len(open_)}


def report_md(res: dict) -> str:
    lines = [f"# Joins r{res['revision']}", "",
             f"{res['slots']} slots ({res.get('not_footage', 0)} stills/cards not checked); "
             f"{res['open']} open finding(s), "
             f"{sum(1 for f in res['findings'] if f.get('waived'))} waived.", ""]
    for f in res["findings"]:
        where = f["cut"] or f["slot"]
        tail = f" — waived: {f['waived']}" if f.get("waived") else ""
        lines.append(f"- {'' if f.get('waived') else '**'}{where}{'' if f.get('waived') else '**'} "
                     f"`{f['kind']}` {f['detail']}{tail}")
    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_refs(items: list[str]) -> dict[str, list[Path]]:
    refs: dict[str, list[Path]] = {}
    for it in items:
        name, _, p = it.partition("=")
        if not name or not p:
            raise SystemExit(f"--ref wants name=path, got {it!r}")
        refs.setdefault(name, []).append(Path(p))
    return refs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--library", default="")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("screen")
    sc.add_argument("clips", nargs="+")
    sc.add_argument("--ref", action="append", default=[], help="name=path (repeat; several per name allowed)")
    dr = sub.add_parser("draft")
    dr.add_argument("clip")
    dr.add_argument("--asset-id", required=True)
    dr.add_argument("--location", required=True)
    dr.add_argument("--project")
    dr.add_argument("--platform")
    dr.add_argument("--model")
    rc = sub.add_parser("record")
    rc.add_argument("file")
    jn = sub.add_parser("joins")
    jn.add_argument("dir")
    a = ap.parse_args(argv)
    vault, library = lib.resolve(a)

    if a.cmd == "screen":
        refs = _parse_refs(a.ref)
        if not refs:
            raise SystemExit("screen needs at least one --ref")
        for c in a.clips:
            p = Path(c)
            o = screen(p, refs, clip_label=vault_rel(p, vault))
            print(f"{o['clip']}  motion {o['median_motion']}  jumps {o['jumps_s']}  id-fail {o['identity_fail_s']}")
        return 0
    if a.cmd == "draft":
        clip = Path(a.clip)
        scr_path = clip.with_suffix(".screen.json")
        if not scr_path.exists():
            raise SystemExit(f"no {scr_path.name}: run `screen` first")
        row = draft_row(clip, json.loads(scr_path.read_text(encoding="utf-8")), asset_id=a.asset_id,
                        location=a.location, project=a.project, vault=vault, platform=a.platform, model=a.model)
        print(json.dumps(row, ensure_ascii=False, indent=1))
        return 0
    if library is None:
        raise SystemExit("no vault/library configured (emptyos.toml notes.path or --vault)")
    if a.cmd == "record":
        rows = lib.parse_rows(Path(a.file).read_text(encoding="utf-8"))
        try:
            rows = merge_for_record(lib.objects(lib.read_jsonl(library / lib.ASSETS)), rows)
        except ValueError as e:
            print(f"refused: {e}")
            return 1
        res = lib.record(library, vault, rows, kind="asset", update=True)
        print(json.dumps({k: res[k] for k in ("ok", "written", "added", "updated", "unchanged")}, ensure_ascii=False))
        for e in res["errors"]:
            print(f"  {e['id']}: {e['code']} {e['message']}")
        for c in res["conflicts"]:
            print(f"  conflict: {c}")
        return 0 if res["ok"] else 1
    folder = Path(a.dir)
    timeline = living_master.load(folder)
    plan_path = folder / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else []
    res = joins(timeline, plan, lib.objects(lib.read_jsonl(library / lib.ASSETS)))
    qc = folder / "qc"
    qc.mkdir(exist_ok=True)
    stem = f"joins-r{int(res['revision'] or 0):03d}"
    (qc / f"{stem}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (qc / f"{stem}.md").write_text(report_md(res), encoding="utf-8")
    print(report_md(res))
    return 1 if res["open"] else 0


if __name__ == "__main__":
    sys.exit(main())
