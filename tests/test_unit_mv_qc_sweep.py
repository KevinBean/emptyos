"""scripts/mv/qc_sweep.py — the per-render identity + lip-sync sweep.

Daemon-free and model-free: the scorers (SyncNet via ``_score``, ArcFace via
``identity_sweep``) are replaced with fakes so the tests pin what the sweep
*decides* from their numbers — windowing, what counts as a slip, what gates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "mv"))

import qc_sweep as qc  # noqa: E402


# --- windows -----------------------------------------------------------------

def test_windows_cover_the_whole_slot_and_end_flush():
    starts = qc.windows(4.8)
    assert starts[0] == 0
    assert starts[-1] + qc.WIN == pytest.approx(4.8)          # the back half is always scored
    assert all(b - a <= qc.WIN / 2 + 1e-9 for a, b in zip(starts, starts[1:]))


def test_windows_do_not_duplicate_an_exact_fit():
    assert qc.windows(3.2) == [0.0, 0.8, 1.6]


def test_slot_no_longer_than_a_window_gets_no_windows():
    assert qc.windows(qc.WIN) == []
    assert qc.windows(1.0) == []


# --- median_offset / off_band -----------------------------------------------
# The band comes from a blind test: raw InfiniteTalk clips (windows -2..-4) passed
# the user's eye, the same clips padded to read 0/+1 were rejected.

def _w(*pairs):
    return [{"t": i * 0.8, "offset": o, "lse_c": c} for i, (o, c) in enumerate(pairs)]


def test_median_of_confident_windows():
    assert qc.median_offset(_w((-3, 8), (-3, 6), (-2, 6), (-4, 9))) == -3


def test_even_split_is_averaged_not_rounded():
    assert qc.median_offset(_w((-2, 6), (-3, 7))) == -2.5


def test_weak_windows_do_not_vote():
    # counted, the three weak 0s would outvote the two confident -3s
    assert qc.median_offset(_w((-3, 8), (0, 3), (0, 3), (0, 3), (-3, 9))) == -3


def test_one_confident_window_is_not_a_trend():
    assert qc.median_offset(_w((0, 5), (0, 3), (0, 3))) is None


def test_two_confident_windows_are_a_trend():
    assert qc.median_offset(_w((0, 6), (0, 7), (-3, 3))) == 0


@pytest.mark.parametrize("off", [-4, -3, -2])
def test_accepted_offsets_are_in_band(off):
    assert not qc.off_band(off)


@pytest.mark.parametrize("off", [-1, 0, 1, -5])       # -1 and -5 were never measured
def test_unmeasured_or_rejected_offsets_are_off_band(off):
    assert qc.off_band(off)


# --- lipsync_sweep assembly --------------------------------------------------

def _timeline(dur_frames=115):
    return {"fps": 24, "revision": 7, "slots": [
        {"stable_id": "S001", "destination_start_frame": 0, "destination_end_frame": 48, "source_kind": "video"},
        {"stable_id": "S002", "destination_start_frame": 480, "destination_end_frame": 480 + dur_frames,
         "source_kind": "video"}]}


def _fake_score(by_offset):
    """by_offset(t_in_slot) -> (offset, lse_c); whole-slot calls return an in-band -3."""
    def score(master, vocals, a, dur, tmp, tag):
        if not tag.endswith("-w"):
            return {"verdict": "pass", "own": {"offset": -3, "lse_c": 7.0}, "decoy": {"lse_c": 1.5}}
        o, c = by_offset(round(a - 20.0, 2))
        return {"verdict": "pass", "own": {"offset": o, "lse_c": c}, "decoy": {"lse_c": 1.5}}
    return score


def _sweep():
    [r] = qc.lipsync_sweep(_timeline(), [{"id": "S002", "sing": True}], Path("m.mp4"), Path("v.wav"))
    return r


def test_back_half_going_late_is_caught_behind_a_clean_whole_slot(monkeypatch):
    monkeypatch.setattr(qc, "_score", _fake_score(lambda t: (0, 6.0) if t >= 2.4 else (-3, 8.0)))
    r = _sweep()
    assert r["offset"] == -3                                   # whole-slot score looks fine
    assert r["slipped"] and all(w["t"] >= 2.4 for w in r["slipped"])


def test_the_accepted_raw_pattern_passes(monkeypatch):
    pattern = {0.0: -2, 0.8: -4, 1.6: -3, 2.4: -3}              # S025 raw, as measured on r183
    monkeypatch.setattr(qc, "_score", _fake_score(lambda t: (pattern.get(t, -3), 6.0)))
    r = _sweep()
    assert r["slipped"] == [] and r["median"] == -3


def test_a_median_at_the_band_edge_passes(monkeypatch):
    monkeypatch.setattr(qc, "_score", _fake_score(lambda t: (-4, 6.0)))   # S025's -4 window, held
    r = _sweep()
    assert r["median"] == -4 and r["slipped"] == []


def test_a_slot_further_early_than_measured_is_flagged(monkeypatch):
    monkeypatch.setattr(qc, "_score", _fake_score(lambda t: (-5, 6.0)))
    r = _sweep()
    assert r["median"] == -5 and len(r["slipped"]) == len(r["windows"])


def test_the_rejected_padded_pattern_fails(monkeypatch):
    monkeypatch.setattr(qc, "_score", _fake_score(lambda t: (0, 7.0)))   # every window 0, as on r180
    r = _sweep()
    assert r["median"] == 0 and len(r["slipped"]) == len(r["windows"])


def test_weak_windows_are_listed_for_the_eye_not_as_slips(monkeypatch):
    monkeypatch.setattr(qc, "_score", _fake_score(lambda t: (0, 2.9) if 1.0 < t < 2.5 else (-3, 8.0)))
    r = _sweep()
    assert r["slipped"] == []
    assert r["weak"] and all(w["lse_c"] < qc.CONFIDENT for w in r["weak"])


def _no_face(tag_suffix_all=False):
    def score(master, vocals, a, dur, tmp, tag):
        if tag.endswith("-w") or tag_suffix_all:
            return {"verdict": "fail", "own": {"error": "no face found"}, "decoy": {}}
        return {"verdict": "pass", "own": {"offset": -3, "lse_c": 7.0}, "decoy": {"lse_c": 1.5}}
    return score


def test_no_face_in_the_whole_slot_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(qc, "_score", _no_face(tag_suffix_all=True))
    [r] = qc.lipsync_sweep(_timeline(), [{"id": "S002", "sing": True}], Path("m.mp4"), Path("v.wav"))
    assert r["error"] == "no face found"


def test_no_face_in_a_window_is_unscored_not_a_crash(monkeypatch):
    monkeypatch.setattr(qc, "_score", _no_face())
    [r] = qc.lipsync_sweep(_timeline(), [{"id": "S002", "sing": True}], Path("m.mp4"), Path("v.wav"))
    assert r["windows"] == [] and r["unscored"] and r["slipped"] == []


def test_only_sung_slots_are_scored(monkeypatch):
    monkeypatch.setattr(qc, "_score", _fake_score(lambda t: (-3, 8.0)))
    out = qc.lipsync_sweep(_timeline(), [{"id": "S001"}, {"id": "S002", "sing": True}],
                           Path("m.mp4"), Path("v.wav"))
    assert [r["id"] for r in out] == ["S002"]


# --- main: what gates --------------------------------------------------------

def _run(tmp_path, monkeypatch, lip_rows, ident_rows=None):
    (tmp_path / qc.TIMELINE).write_text(json.dumps(_timeline()), encoding="utf-8")
    (tmp_path / "plan.json").write_text(json.dumps([{"id": "S002", "sing": True}]), encoding="utf-8")
    monkeypatch.setattr(qc, "identity_sweep", lambda *a: ident_rows or [])
    monkeypatch.setattr(qc, "lipsync_sweep", lambda *a: lip_rows)
    code = qc.main([str(tmp_path), "--ref", "her=x.jpg", "--vocals", "v.wav", "--video", "m.mp4"])
    return code, (tmp_path / "qc" / "qc-r7.md").read_text(encoding="utf-8")


def _row(**kw):
    base = {"id": "S002", "verdict": "pass", "offset": -3, "lse_c": 7.0, "decoy": 1.5,
            "windows": [], "unscored": [], "slipped": [], "median": -3.0, "weak": []}
    return {**base, **kw}


def test_clean_run_exits_zero(tmp_path, monkeypatch):
    code, md = _run(tmp_path, monkeypatch, [_row()])
    assert code == 0 and "0 flagged" in md


@pytest.mark.parametrize("bad", [
    {"slipped": [{"t": 2.4, "offset": 0, "lse_c": 6.0}]},
    {"offset": 0},                                         # mouth late (the rejected padded version)
    {"offset": -5},                                        # further early than anything measured
    {"unscored": [0.0, 0.8]},                              # windows exist but none measured
    {"verdict": "fail"},
    {"error": "lipsync_check wrote no result"},
])
def test_each_lip_defect_gates(tmp_path, monkeypatch, bad):
    code, md = _run(tmp_path, monkeypatch, [_row(**bad)])
    assert code == 1 and "**FLAG** S002" in md


@pytest.mark.parametrize("off", [-2, -4])
def test_whole_slot_in_band_does_not_gate(tmp_path, monkeypatch, off):
    code, _ = _run(tmp_path, monkeypatch, [_row(offset=off)])
    assert code == 0


def test_some_unscored_windows_are_reported_but_do_not_gate(tmp_path, monkeypatch):
    code, md = _run(tmp_path, monkeypatch, [_row(windows=[{"t": 0.0, "offset": -3, "lse_c": 7.0}],
                                                 unscored=[0.8])])
    assert code == 0 and "unscored windows" in md and "0.8s" in md


def test_vocals_with_no_sung_slot_is_an_error_not_clean(tmp_path, monkeypatch):
    (tmp_path / qc.TIMELINE).write_text(json.dumps(_timeline()), encoding="utf-8")
    (tmp_path / "plan.json").write_text(json.dumps([{"id": "S002"}]), encoding="utf-8")
    monkeypatch.setattr(qc, "identity_sweep", lambda *a: [])
    assert qc.main([str(tmp_path), "--ref", "her=x.jpg", "--vocals", "v.wav", "--video", "m.mp4"]) == 2


def test_weak_mouth_is_reported_but_does_not_gate(tmp_path, monkeypatch):
    code, md = _run(tmp_path, monkeypatch, [_row(weak=[{"t": 1.6, "offset": 0, "lse_c": 3.0}])])
    assert code == 0 and "weak mouth" in md and "1.6s" in md


def test_identity_flag_gates(tmp_path, monkeypatch):
    import numpy as np
    crop = np.zeros((112, 112, 3), np.uint8)
    flags = [{"t": 1.0, "face_px": 120, "best": "her", "score": 0.2, "crop": crop}] * 2
    code, md = _run(tmp_path, monkeypatch, [_row()],
                    [{"id": "S002", "checked": 5, "flags": flags, "skipped": {"blurred": 0, "profile": 0}}])
    assert code == 1 and "**S002**" in md
    assert (tmp_path / "qc" / "qc-r7-faces.jpg").is_file()


# --- identity_sweep ------------------------------------------------------------

@pytest.fixture
def fake_face(monkeypatch):
    """Stand-in detector/embedder.

    ``faces`` is a queue of face counts, one per detect() call; every face is an
    unknown (score 0.1), 100 px tall, frontal and sharp unless ``h``, ``kps`` or
    ``crop`` override it. detect() records the first pixel of each image it saw
    in ``seen`` so a video test can tell which frames were read.
    """
    import types
    import numpy as np
    rng = np.random.default_rng(0)
    state = {"faces": [], "seen": [], "h": 100, "kps": [(40, 50), (80, 50), (60, 70)],
             "crop": rng.integers(0, 255, (112, 112, 3), dtype=np.uint8)}     # Laplacian var >> MIN_SHARP

    def detect(img):
        state["seen"].append(int(img[0, 0, 0]))
        n = state["faces"].pop(0) if state["faces"] else 0
        return [((0, 0, 100, state["h"]), None, state["kps"]) for _ in range(n)]

    fo = types.SimpleNamespace(read_image=lambda p: np.zeros((720, 1280, 3), np.uint8), detect=detect,
                               embed=lambda img, kps: (np.array([0.1, 0.99]), state["crop"]))
    ic = types.SimpleNamespace(ref_embedding=lambda p: np.array([1.0, 0.0]))
    monkeypatch.setitem(sys.modules, "face_onnx", fo)
    monkeypatch.setitem(sys.modules, "identity_check", ic)
    return state


def _still_timeline(tmp_path):
    img = tmp_path / "s.jpg"
    img.write_bytes(b"x")
    return {"fps": 24, "slots": [{"stable_id": "S009", "source": str(img), "source_kind": "still",
                                  "start_frame": 0, "end_frame": 24}]}


def test_two_unknown_faces_flag_a_slot(tmp_path, fake_face):
    fake_face["faces"] = [2]
    [r] = qc.identity_sweep(_still_timeline(tmp_path), tmp_path, {"her": "x.jpg"}, 6)
    assert r["checked"] == 2 and len(r["flags"]) == 2


def test_one_unknown_face_is_detector_noise(tmp_path, fake_face):
    fake_face["faces"] = [1]
    [r] = qc.identity_sweep(_still_timeline(tmp_path), tmp_path, {"her": "x.jpg"}, 6)
    assert r["checked"] == 1 and r["flags"] == []


def test_faces_below_min_px_are_not_scored(tmp_path, fake_face):
    fake_face.update(faces=[2], h=qc.MIN_FACE_PX - 1)
    [r] = qc.identity_sweep(_still_timeline(tmp_path), tmp_path, {"her": "x.jpg"}, 6)
    assert r["checked"] == 0 and r["flags"] == []


def test_defocused_faces_are_counted_for_the_eye_not_flagged(tmp_path, fake_face):
    import numpy as np
    fake_face.update(faces=[2], crop=np.full((112, 112, 3), 128, np.uint8))    # Laplacian var 0
    [r] = qc.identity_sweep(_still_timeline(tmp_path), tmp_path, {"her": "x.jpg"}, 6)
    assert r["flags"] == [] and r["skipped"]["blurred"] == 2


def test_profile_faces_are_counted_for_the_eye_not_flagged(tmp_path, fake_face):
    fake_face.update(faces=[2], kps=[(40, 50), (80, 50), (95, 70)])
    [r] = qc.identity_sweep(_still_timeline(tmp_path), tmp_path, {"her": "x.jpg"}, 6)
    assert r["flags"] == [] and r["skipped"]["profile"] == 2


def test_video_reads_only_the_selected_window(tmp_path, fake_face):
    import cv2
    import numpy as np
    src = tmp_path / "clip.avi"
    w = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"MJPG"), 24, (64, 64))
    for i in range(48):
        w.write(np.full((64, 64, 3), i * 5, np.uint8))     # frame i carries pixel value 5*i
    w.release()
    tl = {"fps": 24, "slots": [{"stable_id": "S001", "source": str(src), "source_kind": "video",
                                "start_frame": 30, "end_frame": 42}]}
    [r] = qc.identity_sweep(tl, tmp_path, {"her": "x.jpg"}, 6)
    assert r["checked"] == 0
    frames = [round(v / 5) for v in fake_face["seen"]]
    assert frames == [30, 36]                                # MJPG is lossy: round to the frame index


def test_missing_source_is_reported_not_skipped(tmp_path, fake_face):
    tl = {"fps": 24, "slots": [{"stable_id": "S001", "source": str(tmp_path / "gone.mp4"),
                                "source_kind": "video", "start_frame": 0, "end_frame": 24}]}
    [r] = qc.identity_sweep(tl, tmp_path, {"her": "x.jpg"}, 6)
    assert "missing" in r["error"]


def test_undecodable_video_is_reported(tmp_path, fake_face):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    tl = {"fps": 24, "slots": [{"stable_id": "S001", "source": str(bad), "source_kind": "video",
                                "start_frame": 0, "end_frame": 24}]}
    [r] = qc.identity_sweep(tl, tmp_path, {"her": "x.jpg"}, 6)
    assert "decode" in r["error"]


def test_identity_error_gates(tmp_path, monkeypatch):
    code, md = _run(tmp_path, monkeypatch, [_row()],
                    [{"id": "S002", "checked": 0, "flags": [], "error": "source missing: x",
                      "skipped": {"blurred": 0, "profile": 0}}])
    assert code == 1 and "not checked" in md


# --- constants copied from the sibling checkers --------------------------------

@pytest.mark.parametrize("name, sibling, theirs", [
    ("MIN_FACE_PX", "identity_check.py", r"^MIN_FACE_PX\s*=\s*([\d.]+)"),
    ("UNKNOWN", "identity_check.py", r"^DRIFT,\s*CLONE\s*=\s*([\d.]+)"),
    ("CONFIDENT", "lipsync_check.py", r"^MIN_LSE_C\s*=\s*([\d.]+)"),
])
def test_copied_constants_match_their_source(name, sibling, theirs):
    import re
    src = (ROOT / "scripts" / "mv" / "lipsync" / sibling).read_text(encoding="utf-8")
    m = re.search(theirs, src, re.M)
    assert m, f"{sibling} no longer defines the constant qc_sweep copies as {name}"
    assert float(m.group(1)) == getattr(qc, name)


# --- small helpers -----------------------------------------------------------

def test_yaw_ratio_frontal_vs_profile():
    frontal = [(40, 50), (80, 50), (60, 70)]
    profile = [(40, 50), (80, 50), (90, 70)]
    assert qc.yaw_ratio(frontal) == pytest.approx(0)
    assert qc.yaw_ratio(profile) > qc.MAX_YAW


def test_rendered_master_picks_highest_revision_numerically(tmp_path):
    for n in ("living-master-r9.mp4", "living-master-r178.mp4", "living-master-r178-review.mp4"):
        (tmp_path / n).write_bytes(b"x")
    assert qc.rendered_master(tmp_path).name == "living-master-r178.mp4"
