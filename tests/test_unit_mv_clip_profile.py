"""Unit pins for scripts/mv/clip_profile.py and mv_library.validate_profile.

Daemon-, vault- and model-free: ``screen`` (ONNX + cv2) is not exercised here;
``draft_profile``, ``joins`` and the validator are pure and take plain dicts.

Every conflict class ``joins`` reports has one test that produces exactly it,
because the check exists for continuity breaks a reviewer found by eye
(〈在你說完以前〉 v7: a window that changed style, a top that changed colour,
the light across a cut, a traffic light that moved sides).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "mv"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


lib = _load("mv_library", SCRIPTS / "mv_library.py")
cp = _load("clip_profile", SCRIPTS / "mv" / "clip_profile.py")

VOCAB = {"side": {"L", "C", "R"}, "framing": {"wide", "medium", "close"},
         "mouth": {"closed", "open", "singing", "speaking"},
         "codes": {"identity_drift", "unmotivated_motion", "unexpected_character", "mouth_open"}}


def profile(**over) -> dict:
    p = {
        "location": "rehearsal-room", "set_version": "P:tpe-2", "time_of_day": "afternoon",
        "light": {"state": "work-lights", "key_source": "window left", "colour": "warm"},
        "set_details": {"window": "4-pane steel"},
        "people": [{"who": "her", "side": "L", "wardrobe": {"top": "green sweater"},
                    "framing": "medium", "facing": "3/4 right", "mouth": "closed"}],
        "camera": {"motion": "push-in", "framing": "medium"},
        "action": {"entry_state": "standing", "exit_state": "turned", "travel": "none"},
        "windows": {"usable": [[0.0, 4.0, "wait"]], "reject": [[4.0, 8.0, "identity_drift"]]},
        "defects": [{"t0": 5.0, "t1": 5.5, "code": "unexpected_character", "note": "stranger"}],
        "intensity": 2, "mood": "tense", "evidence": {},
        "reviewed": {"by": "claude", "at": "2026-09-29T14:00:00+10:00"},
    }
    p.update(over)
    return p


def kinds(findings) -> list[str]:
    return [c for c, _ in findings]


# ── validate_profile ─────────────────────────────────────────────────────────

def test_good_profile_is_clean():
    assert lib.validate_profile(profile(), VOCAB, None, duration=8.0) == []


@pytest.mark.parametrize("over, code", [
    ({"location": ""}, "bad_profile"),
    ({"light": {"state": ""}}, "bad_profile"),
    ({"light": None}, "bad_profile"),
    ({"people": None}, "bad_profile"),
    ({"windows": None}, "bad_profile"),
    ({"intensity": 6}, "bad_profile"),
    ({"intensity": True}, "bad_profile"),
    ({"reviewed": {"by": "claude"}}, "bad_profile"),
    ({"set_details": ["window"]}, "bad_profile"),
    ({"windows": {"usable": [[0, 4]], "reject": []}}, "bad_profile"),
])
def test_structure_errors(over, code):
    assert code in kinds(lib.validate_profile(profile(**over), VOCAB, None, duration=8.0))


@pytest.mark.parametrize("field, value", [("side", "left"), ("framing", "tight"), ("mouth", "grin")])
def test_person_vocab(field, value):
    p = profile()
    p["people"][0][field] = value
    assert "bad_vocab" in kinds(lib.validate_profile(p, VOCAB, None, duration=8.0))


def test_camera_framing_vocab():
    assert "bad_vocab" in kinds(lib.validate_profile(profile(camera={"framing": "tight"}), VOCAB, None))


def test_window_past_duration():
    p = profile(windows={"usable": [[0.0, 8.02, "x"]], "reject": []})
    assert "bad_profile" in kinds(lib.validate_profile(p, VOCAB, None, duration=8.0))


def test_window_ending_on_last_frame_is_fine():
    p = profile(windows={"usable": [[0.0, 8.0, "x"]], "reject": []})
    assert lib.validate_profile(p, VOCAB, None, duration=8.0) == []


def test_reversed_window():
    p = profile(windows={"usable": [[4.0, 2.0, "x"]], "reject": []})
    assert "bad_profile" in kinds(lib.validate_profile(p, VOCAB, None, duration=8.0))


def test_reject_and_defect_codes_come_from_failure_codes():
    p = profile(windows={"usable": [], "reject": [[1.0, 2.0, "looks-off"]]})
    assert "bad_vocab" in kinds(lib.validate_profile(p, VOCAB, None))
    p = profile(defects=[{"t0": 1.0, "t1": 2.0, "code": "looks-off"}])
    assert "bad_vocab" in kinds(lib.validate_profile(p, VOCAB, None))


def test_empty_frame_is_valid():
    assert lib.validate_profile(profile(people=[]), VOCAB, None) == []


def test_a_fresh_draft_does_not_validate():
    """Honesty rule: the machine cannot see the light, so a draft cannot be recorded until someone looks."""
    scr = {"frames": 192, "fps": 24.0, "width": 1280, "samples": [], "identity_fail_s": [], "jumps_s": []}
    found = lib.validate_profile(cp.draft_profile(scr, location="x"), VOCAB, None, duration=8.0)
    assert any("light.state" in m for _, m in found)


# ── draft_profile ────────────────────────────────────────────────────────────

def scr_with(faces_by_t: dict[float, list[dict]], *, frames=192, jumps=(), fail=()):
    samples = [{"f": int(t * 24), "t": t, "faces": fs, "motion": 1.0} for t, fs in sorted(faces_by_t.items())]
    return {"frames": frames, "fps": 24.0, "width": 1200, "height": 720, "samples": samples,
            "jumps_s": list(jumps), "identity_fail_s": list(fail)}


def face(who, x, score=0.6, h=200, w=None):
    return {"x": x, "w": w or h, "h": h, "who": who, "score": score}


def test_draft_side_from_face_centre():
    s = scr_with({t / 4: [face("her", 100), face("him", 900)] for t in range(8)})
    p = cp.draft_profile(s, location="x")
    assert {x["who"]: x["side"] for x in p["people"]} == {"her": "L", "him": "R"}


def test_draft_centre_third_is_C():
    # face 380-580 px in a 1200 px frame: its centre (480) is in the middle third, its left edge is not,
    # and it is left of half — so this reads C only by thirds measured from the face centre
    s = scr_with({t / 4: [face("her", 380)] for t in range(8)})
    assert cp.draft_profile(s, location="x")["people"][0]["side"] == "C"


def test_draft_drops_a_fleeting_face():
    faces = {t / 4: [face("her", 100)] for t in range(8)}
    faces[0.0] = faces[0.0] + [face("him", 900)]
    assert [x["who"] for x in cp.draft_profile(scr_with(faces), location="x")["people"]] == ["her"]


def test_draft_ignores_drifted_faces_for_presence():
    s = scr_with({t / 4: [face("him", 900, score=0.2)] for t in range(8)})
    assert cp.draft_profile(s, location="x")["people"] == []


def test_draft_rejects_identity_fail_and_jumps():
    s = scr_with({0.0: []}, fail=[3.0, 3.25], jumps=[6.0])
    p = cp.draft_profile(s, location="x")
    rej = p["windows"]["reject"]
    assert [3.0, 3.5, "identity_drift"] in rej
    assert [5.9, 6.1, "unmotivated_motion"] in rej
    assert p["windows"]["usable"] == [[0.0, 3.0, ""], [3.5, 5.9, ""], [6.1, 8.0, ""]]


def test_usable_skips_short_gaps():
    assert cp.usable_between([[1.0, 2.0, "a"], [2.5, 3.0, "b"]], 3.0) == [[0.0, 1.0, ""]]


# ── joins ────────────────────────────────────────────────────────────────────

def slot(sid, sha, start=0, end=48):
    return {"stable_id": sid, "source_sha256": sha, "start_frame": start, "end_frame": end}


def row(sha, prof, fps=24.0):
    return {"asset_id": f"P:{sha}", "sha256": sha, "fps": fps, "profile": prof}


def run(pa, pb, *, plan=(), slots=None):
    tl = {"revision": 7, "fps": 24, "slots": slots or [slot("S1", "a"), slot("S2", "b")]}
    return cp.joins(tl, list(plan), [row("a", pa), row("b", pb)])


def open_kinds(res):
    return sorted(f["kind"] for f in res["findings"] if not f.get("waived"))


def test_identical_profiles_join_clean():
    p = profile(windows={"usable": [[0.0, 8.0, "x"]], "reject": []}, defects=[])
    res = run(p, copy.deepcopy(p))
    assert res["open"] == 0 and res["findings"] == []


def clean(**over):
    p = profile(windows={"usable": [[0.0, 8.0, "x"]], "reject": []}, defects=[])
    p.update(over)
    return p


def test_light_change_in_same_place():
    assert open_kinds(run(clean(), clean(light={"state": "stage-spot"}))) == ["light"]


def test_set_detail_change_in_same_place():
    assert open_kinds(run(clean(), clean(set_details={"window": "arched wood"}))) == ["set_detail"]


def test_set_version_change_in_same_place():
    assert open_kinds(run(clean(), clean(set_version="P:tpe-3"))) == ["set_version"]


def test_a_new_place_may_change_light_and_set():
    b = clean(location="street", light={"state": "street"}, set_details={"window": "none"}, set_version="P:st-1")
    assert open_kinds(run(clean(), b)) == []


def test_wardrobe_change_even_across_places():
    b = clean(location="street")
    b["people"][0]["wardrobe"] = {"top": "white shirt"}
    assert open_kinds(run(clean(), b)) == ["wardrobe"]


def couple(her_side, him_side, **over):
    p = clean(**over)
    p["people"] = [{"who": "her", "side": her_side, "wardrobe": {"top": "green sweater"}},
                   {"who": "him", "side": him_side, "wardrobe": {"top": "denim shirt"}}]
    return p


def test_side_flip():
    assert open_kinds(run(couple("L", "R"), couple("R", "L"))) == ["side_flip", "side_flip"]


def test_centre_to_side_is_not_a_flip():
    assert open_kinds(run(couple("C", "R"), couple("L", "R"))) == []


def test_a_lone_person_reframed_is_not_a_flip():
    """S037→S038: her alone in a close-up, framed on the other side — no 180° relation to break."""
    a, b = clean(), clean()
    b["people"][0]["side"] = "R"
    assert open_kinds(run(a, b)) == []


def three(pa, pb, pc, plan=()):
    tl = {"revision": 1, "fps": 24, "slots": [slot("S1", "a"), slot("S2", "b"), slot("S3", "c")]}
    return cp.joins(tl, list(plan), [row("a", pa), row("b", pb), row("c", pc)])


def him_only(**over):
    return clean(people=[{"who": "him", "side": "R", "wardrobe": {"top": "denim shirt"}}], **over)


def test_wardrobe_compared_with_last_appearance_not_just_the_neighbour():
    """S049: her top changed colour, but the shot before it showed only his arm."""
    c = clean()
    c["people"][0]["wardrobe"] = {"top": "white shirt"}
    res = three(clean(), him_only(), c)
    assert open_kinds(res) == ["wardrobe"] and res["findings"][0]["cut"] == "S1→S3"


def test_set_detail_compared_with_last_shot_in_that_place():
    elsewhere = clean(location="singing-space", set_details={"window": "none"})
    res = three(clean(), elsewhere, clean(set_details={"window": "security grille"}))
    assert open_kinds(res) == ["set_detail"] and res["findings"][0]["cut"] == "S1→S3"


def test_an_unknown_value_does_not_erase_what_was_seen():
    res = three(clean(), clean(set_details={"window": ""}), clean(set_details={"window": "security grille"}))
    assert open_kinds(res) == ["set_detail"]
    assert res["findings"][0]["cut"] == "S1→S3" and "4-pane steel" in res["findings"][0]["detail"]


def test_light_direction_change_in_same_place():
    b = clean(light={"state": "work-lights", "key_source": "spot upper right", "colour": "warm"})
    assert open_kinds(run(clean(), b)) == ["light"]


def test_light_colour_change_in_same_place():
    b = clean(light={"state": "work-lights", "key_source": "window left", "colour": "cold"})
    assert open_kinds(run(clean(), b)) == ["light"]


def test_jump_inside_one_source():
    slots = [slot("S1", "a", 0, 48), slot("S2", "a", 120, 168)]
    tl = {"revision": 1, "fps": 24, "slots": slots}
    assert open_kinds(cp.joins(tl, [], [row("a", clean())])) == ["jump_cut"]


def test_contiguous_windows_of_one_source_are_one_shot():
    slots = [slot("S1", "a", 0, 48), slot("S2", "a", 48, 96)]
    tl = {"revision": 1, "fps": 24, "slots": slots}
    assert open_kinds(cp.joins(tl, [], [row("a", clean())])) == []


def test_used_window_overlapping_a_reject():
    b = clean(windows={"usable": [], "reject": [[1.0, 1.5, "identity_drift"]]})
    assert open_kinds(run(clean(), b)) == ["uses_reject"]


def test_reject_outside_the_used_window_is_fine():
    b = clean(windows={"usable": [], "reject": [[2.0, 3.0, "identity_drift"]]})
    assert open_kinds(run(clean(), b)) == []           # slot uses frames 0-48 = 0-2 s


def test_used_window_overlapping_a_defect():
    b = clean(defects=[{"t0": 0.5, "t1": 0.8, "code": "unexpected_character", "note": "stranger"}])
    assert open_kinds(run(clean(), b)) == ["uses_defect"]


def test_used_window_honours_source_fps():
    b = clean(windows={"usable": [], "reject": [[1.8, 1.95, "identity_drift"]]})
    tl = {"revision": 1, "fps": 24, "slots": [slot("S1", "a"), slot("S2", "b", 0, 48)]}
    res = cp.joins(tl, [], [row("a", clean()), row("b", b, fps=30.0)])  # 48 frames @30 = 0-1.6 s
    assert open_kinds(res) == []


def test_missing_profile():
    tl = {"revision": 1, "fps": 24, "slots": [slot("S1", "a"), slot("S2", "zzz")]}
    assert open_kinds(cp.joins(tl, [], [row("a", clean())])) == ["no_profile"]


def test_draft_is_unverified_not_a_pass():
    assert open_kinds(run(clean(), clean(reviewed=None))) == ["unverified"]


def test_waiver_silences_only_its_cut():
    slots = [slot("S1", "a"), slot("S2", "b"), slot("S3", "c")]
    tl = {"revision": 1, "fps": 24, "slots": slots}
    rows = [row("a", clean()), row("b", clean(light={"state": "stage-spot"})), row("c", clean())]
    res = cp.joins(tl, [{"id": "S2", "join_ok": "the light change is the point"}], rows)
    waived = [f for f in res["findings"] if f.get("waived")]
    assert [f["cut"] for f in waived] == ["S1→S2"]
    assert open_kinds(res) == ["light"] and res["open"] == 1        # S2→S3 still open


def test_waiver_does_not_cover_slot_defects():
    b = clean(defects=[{"t0": 0.5, "t1": 0.8, "code": "unexpected_character"}])
    res = run(clean(), b, plan=[{"id": "S2", "join_ok": "deliberate"}])
    assert open_kinds(res) == ["uses_defect"]


def test_report_marks_open_and_waived():
    res = {"revision": 3, "slots": 2, "open": 1, "findings": [
        {"cut": "S1→S2", "slot": "S2", "kind": "light", "detail": "a → b"},
        {"cut": "S2→S3", "slot": "S3", "kind": "light", "detail": "b → a", "waived": "on purpose"}]}
    md = cp.report_md(res)
    assert "**S1→S2**" in md and "waived: on purpose" in md and "**S2→S3**" not in md


# ── review fixes (adversarial review 2026-09-29) ─────────────────────────────

def test_constants_match_identity_check():
    src = (SCRIPTS / "mv" / "lipsync" / "identity_check.py").read_text(encoding="utf-8")
    assert "PASS, REVIEW = 0.45, 0.35" in src and "DRIFT, CLONE = 0.35, 0.30" in src
    assert "MIN_FACE_PX = 90" in src
    assert (cp.PASS, cp.DRIFT, cp.MIN_FACE_PX) == (0.45, 0.35, 90)


def test_draft_review_band_face_is_not_a_person():
    """0.35-0.45 is identity_check's review band, where a lookalike overlaps."""
    s = scr_with({t / 4: [face("her", 100, score=0.40)] for t in range(8)})
    assert cp.draft_profile(s, location="x")["people"] == []


def test_record_keeps_the_rows_status_origin_and_rights():
    old = {"asset_id": "P:S1", "status": "approved", "status_reason": "Kevin ok",
           "origin": {"platform": "google-flow", "model": "veo-3.1-fast", "attempt_id": "P:video:S1:1"},
           "rights": {"watermark": "synthid"}, "projects_used": ["P", "Q"], "profile": None}
    new = {"asset_id": "P:S1", "status": "candidate", "origin": {"platform": None}, "rights": {},
           "projects_used": ["R"], "profile": clean()}
    out = cp.merge_for_record([old], [new])[0]
    assert out["status"] == "approved" and out["origin"] == old["origin"] and out["rights"] == old["rights"]
    assert out["projects_used"] == ["P", "Q", "R"] and out["profile"] == clean()


def test_record_passes_a_new_asset_through():
    new = {"asset_id": "P:S9", "status": "candidate", "profile": clean()}
    assert cp.merge_for_record([], [new]) == [new]


def test_stills_and_cards_are_counted_not_checked():
    slots = [slot("S1", "a"), {**slot("S2", "card"), "source_kind": "still"},
             {**slot("S3", "c"), "asset_state": "placeholder"}, slot("S4", "b")]
    tl = {"revision": 1, "fps": 24, "slots": slots}
    res = cp.joins(tl, [], [row("a", clean()), row("b", clean())])
    assert res["open"] == 0 and res["not_footage"] == 2


def test_waiver_on_an_id_less_plan_follows_position():
    b = clean(light={"state": "stage-spot"})
    res = run(clean(), b, plan=[{"start": 0}, {"start": 2, "join_ok": "the light change is the point"}],
              slots=[slot("S001", "a"), slot("S002", "b")])
    assert res["open"] == 0 and res["findings"][0]["waived"]


def test_duplicate_profiled_rows_for_one_source():
    tl = {"revision": 1, "fps": 24, "slots": [slot("S1", "a")]}
    rows = [{**row("a", clean()), "asset_id": "P:one"}, {**row("a", clean()), "asset_id": "P:two"}]
    assert open_kinds(cp.joins(tl, [], rows)) == ["duplicate_sha"]


def test_location_spelling_does_not_split_one_place():
    assert open_kinds(run(clean(location="Rehearsal-Room "), clean(light={"state": "stage-spot"}))) == ["light"]


def test_no_side_flip_across_a_change_of_place():
    assert open_kinds(run(couple("L", "R"), couple("R", "L", location="street"))) == []


# ── CLI wiring: main() decides what gates ────────────────────────────────────

MVLIB_TESTS = _load("test_unit_mv_library_fixtures", SCRIPTS.parent / "tests" / "test_unit_mv_library.py")


@pytest.fixture
def lib_env():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        vault = Path(d)
        library = vault / "lib"
        library.mkdir()
        (library / "SCHEMA.md").write_text(MVLIB_TESTS.SCHEMA, encoding="utf-8")
        (library / "failure-codes.md").write_text(MVLIB_TESTS.CODES, encoding="utf-8")
        (vault / "clip.mp4").write_bytes(b"x")
        yield vault, library


def _cli(vault, library, *args):
    return cp.main(["--vault", str(vault), "--library", str(library), *args])


def _plate(prof):
    return {"asset_id": "P:clip", "kind": "motion-plate", "path": "clip.mp4", "sha256": "a" * 64,
            "origin": {}, "rights": {}, "status": "candidate", "status_reason": "x",
            "duration_seconds": 8.0, "fps": 24.0, "profile": prof}


def _profile_ok():
    return {"location": "room", "light": {"state": "on"}, "people": [], "windows": {"usable": [], "reject": []},
            "reviewed": {"by": "t", "at": "2026-09-29"}}


def test_cli_record_refuses_an_invalid_profile(lib_env):
    vault, library = lib_env
    f = vault / "rows.json"
    f.write_text(json.dumps([_plate({**_profile_ok(), "light": {"state": ""}})]), encoding="utf-8")
    assert _cli(vault, library, "record", str(f)) == 1
    assert not (library / "assets.jsonl").exists() or "P:clip" not in (library / "assets.jsonl").read_text()


def _master(vault, sha, name="lm"):
    d = vault / name
    d.mkdir()
    tl = {"revision": 3, "fps": 24, "slots": [{"stable_id": "S1", "source_sha256": sha, "source_kind": "video",
                                               "start_frame": 0, "end_frame": 24}]}
    (d / "living-master-timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    return d


def test_cli_joins_exit_codes(lib_env):
    vault, library = lib_env
    f = vault / "rows.json"
    f.write_text(json.dumps([_plate(_profile_ok())]), encoding="utf-8")
    assert _cli(vault, library, "record", str(f)) == 0
    assert _cli(vault, library, "joins", str(_master(vault, "a" * 64))) == 0
    assert _cli(vault, library, "joins", str(_master(vault, "b" * 64, "lm2"))) == 1   # no profile → open
    assert (vault / "lm2" / "qc" / "joins-r003.md").exists()


def test_cli_record_onto_an_existing_row_keeps_its_status(lib_env):
    vault, library = lib_env
    f = vault / "rows.json"
    f.write_text(json.dumps([{**_plate(None), "status": "approved", "status_reason": "Kevin ok"}]), encoding="utf-8")
    del_profile = json.loads(f.read_text(encoding="utf-8"))
    del_profile[0].pop("profile")
    f.write_text(json.dumps(del_profile), encoding="utf-8")
    assert _cli(vault, library, "record", str(f)) == 0
    f.write_text(json.dumps([{**_plate(_profile_ok()), "status": "candidate", "status_reason": "draft"}]), encoding="utf-8")
    assert _cli(vault, library, "record", str(f)) == 0
    stored = [json.loads(x) for x in (library / "assets.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert stored[0]["status"] == "approved" and stored[0]["profile"]["location"] == "room"


# ── round-2 review fixes ─────────────────────────────────────────────────────

def test_memory_names_the_shot_where_the_value_was_seen():
    a = clean()
    a["people"][0]["wardrobe"] = {"top": "green sweater", "shoes": "black flats"}
    b = clean()
    b["people"][0]["wardrobe"] = {"top": "green sweater"}
    c = clean()
    c["people"][0]["wardrobe"] = {"shoes": "white trainers"}
    res = three(a, b, c)
    assert [(f["kind"], f["cut"]) for f in res["findings"]] == [("wardrobe", "S1→S3")]


def test_cut_waiver_does_not_silence_a_change_against_an_earlier_shot():
    c = clean(light={"state": "stage-spot"})
    c["people"][0]["wardrobe"] = {"top": "white shirt"}
    res = three(clean(), him_only(), c, plan=[{"id": "S3", "join_ok": "light change at the cut"}])
    assert open_kinds(res) == ["wardrobe"]


def test_change_ok_waives_a_change_against_an_earlier_shot_only():
    c = clean()
    c["people"][0]["wardrobe"] = {"top": "white shirt"}
    res = three(clean(), him_only(), c, plan=[{"id": "S3", "change_ok": "costume change for act two"}])
    assert open_kinds(res) == []
    b = clean(light={"state": "stage-spot"})
    res = run(clean(), b, plan=[{"id": "S2", "change_ok": "costume change"}])
    assert open_kinds(res) == ["light"]                  # change_ok does not waive the adjacent cut


def test_a_still_between_two_clips_does_not_hide_the_cut():
    slots = [slot("S1", "a"), {**slot("S2", "card"), "source_kind": "still"}, slot("S3", "b")]
    tl = {"revision": 1, "fps": 24, "slots": slots}
    res = cp.joins(tl, [], [row("a", clean()), row("b", clean(light={"state": "stage-spot"}))])
    assert open_kinds(res) == ["light"] and res["findings"][0]["cut"] == "S1→S3"


def test_record_refuses_a_changed_file_under_the_same_id():
    old = {"asset_id": "P:S1", "sha256": "a" * 64, "profile": None}
    with pytest.raises(ValueError):
        cp.merge_for_record([old], [{"asset_id": "P:S1", "sha256": "b" * 64, "profile": clean()}])


def test_record_without_a_profile_keeps_the_stored_one():
    old = {"asset_id": "P:S1", "sha256": "a" * 64, "profile": clean()}
    out = cp.merge_for_record([old], [{"asset_id": "P:S1", "sha256": "a" * 64, "projects_used": ["Q"]}])[0]
    assert out["profile"] == clean()


def test_slack_accepts_rounding_but_not_a_frame():
    ok = profile(windows={"usable": [[0.0, 8.01, "x"]], "reject": []})
    assert lib.validate_profile(ok, VOCAB, None, duration=8.0) == []


def test_positional_ids_match_living_master():
    src = (SCRIPTS / "mv" / "living_master.py").read_text(encoding="utf-8")
    assert 'sid = str(p.get("id") or f"S{i + 1:03d}")' in src


def test_an_unknown_light_value_is_not_a_change():
    assert open_kinds(run(clean(), clean(light={"state": "work-lights", "key_source": "", "colour": None}))) == []


def test_change_ok_does_not_cover_slot_defects():
    b = clean(defects=[{"t0": 0.5, "t1": 0.8, "code": "unexpected_character"}])
    res = run(clean(), b, plan=[{"id": "S2", "change_ok": "costume change"}])
    assert open_kinds(res) == ["uses_defect"]
