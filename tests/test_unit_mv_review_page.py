"""scripts/mv/review_page.py — the living-master review page and its standalone copy.

Daemon-free: a fake living-master folder in a temp dir (three slots, a few
bytes standing in for the mp4 — the builder never decodes video — and one real
JPEG for the thumbnail re-encode).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "mv"))

import review_page as rp  # noqa: E402

FPS = 24
# Frames: S001 [0,24) S002 [24,60) S003 [60,96) -> 0-1 s, 1-2.5 s, 2.5-4 s.
FRAMES = [("S001", 0, 24, "still", "candidate"), ("S002", 24, 60, "video", "candidate"),
          ("S003", 60, 96, "still", "placeholder")]


def _slots_by_id(page: str) -> dict[str, dict]:
    out = {}
    for m in re.finditer(r'<li class="slot" data-id="([^"]+)" data-a="([^"]+)" data-b="([^"]+)">'
                         r'<button type="button" onclick="seek\(([^)]+)\)">(.*?)</li>', page, re.S):
        sid, a, b, seek, body = m.groups()
        lyr = re.search(r'<span class="lyr">(.*?)</span></span></button>', body, re.S).group(1)
        out[sid] = {"a": float(a), "b": float(b), "seek": float(seek), "body": body,
                    "lyrics": re.findall(r"<span>(.*?)</span>", lyr)}
    return out


@pytest.fixture
def project(tmp_path: Path) -> Path:
    Image = pytest.importorskip("PIL.Image")
    lm = tmp_path / "living-master-v1"
    lm.mkdir()
    (tmp_path / "stills").mkdir()
    Image.new("RGB", (800, 450), (120, 60, 30)).save(tmp_path / "stills" / "S001.jpg", quality=95)
    Image.new("RGB", (320, 180), (20, 20, 20)).save(lm / "card-S003.png")
    slots = []
    for sid, a, b, kind, state in FRAMES:
        slots.append({"stable_id": sid, "title": f"timeline {sid}", "note": "",
                      "destination_start_frame": a, "destination_end_frame": b,
                      "source": "card-S003.png" if sid == "S003" else f"src/{sid}.mp4",
                      "source_kind": kind, "start_frame": 0, "end_frame": b - a,
                      "asset_state": state, "production_approved": False})
    (lm / "living-master-timeline.json").write_text(json.dumps(
        {"schema_version": 1, "revision": 3, "fps": FPS, "total_frames": 96, "slots": slots}),
        encoding="utf-8")
    (lm / "plan.json").write_text(json.dumps([
        {"id": "S001", "start": 0, "title": "【Room】first", "note": "n1", "sing": False},
        {"id": "S002", "start": 1, "title": "【Stage】second", "note": "n2", "sing": True},
        {"id": "S003", "start": 2.5, "title": "third", "note": "n3"}]), encoding="utf-8")
    (lm / "living-master-r003-review.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42 fake")
    (lm / "cut-check.json").write_text(json.dumps({"rows": [
        {"id": "S002", "cut": "shallow", "cut_db": -12.0}, {"id": "S003", "cut": "silence"}]}),
        encoding="utf-8")
    an = tmp_path / "analysis"
    an.mkdir()
    # Unmeasured, L2 sits in S001 (start 0.3, mid 0.65); measured (1.2 → 1.5,
    # mid 1.35) it moves to S002 — the case phrase bounds exist for.
    (an / "lyric-timeline-v1.json").write_text(json.dumps({"lines": [
        {"id": "L1", "text": "line-one", "start": 0.1, "end": 0.5},
        {"id": "L2", "text": "line-two", "start": 0.3, "end": 1.0},
        {"id": "L3", "text": "line-three", "start": 3.0, "end": 3.6}]}), encoding="utf-8")
    (an / "phrase-bounds-v2.json").write_text(json.dumps({"boundaries": [
        {"after": "L1", "before": "L2", "t": 1.2}, {"after": "L2", "before": "L3", "t": 1.5}]}),
        encoding="utf-8")
    (lm / "review.json").write_text(json.dumps({
        "title": "Test review", "thumbs": ["../stills/{id}.jpg"],
        "sections": [[0, "Intro"], [2.5, "V1"]], "acts": [[0, "Act I"], [2.5, "Act II"]],
        "cards": [{"title": "Decide", "ordered": True, "items": ["**Watch** it <b>raw</b>"]}]}),
        encoding="utf-8")
    return lm


def test_standalone_embeds_everything_and_links_nothing(project):
    res = rp.build(project, standalone=True)
    page = res["standalone"].read_text(encoding="utf-8")
    assert 'src="../' not in page
    assert page.count("data:video/mp4;base64,") == 1
    assert re.search(r'<img src="data:image/jpeg;base64,', page)
    assert res["bytes"] == res["standalone"].stat().st_size
    assert not res["warnings"]


def test_local_page_links_by_relative_path(project):
    res = rp.build(project)
    page = res["local"].read_text(encoding="utf-8")
    assert res["standalone"] is None
    assert 'src="../living-master-v1/living-master-r003-review.mp4"' in page
    assert 'src="../stills/S001.jpg"' in page
    assert "data:" not in page


def test_slot_count_and_seek_data_follow_the_timeline(project):
    slots = _slots_by_id(rp.build(project)["local"].read_text(encoding="utf-8"))
    assert list(slots) == ["S001", "S002", "S003"]
    assert [(s["a"], s["b"], s["seek"]) for s in slots.values()] == [
        (0.0, 1.0, 0.0), (1.0, 2.5, 1.0), (2.5, 4.0, 2.5)]


def test_lyrics_land_in_the_slot_their_measured_midpoint_falls_in(project):
    slots = _slots_by_id(rp.build(project)["local"].read_text(encoding="utf-8"))
    assert slots["S001"]["lyrics"] == ["line-one"]
    assert slots["S002"]["lyrics"] == ["line-two"]
    assert slots["S003"]["lyrics"] == ["line-three"]


def test_midpoint_uses_both_measured_ends():
    lines = [{"id": "A", "text": "a", "start": 0.0, "end": 9.0},
             {"id": "B", "text": "b", "start": 0.0, "end": 9.0},
             {"id": "C", "text": "c", "start": None, "end": 1.0}]
    bounds = [{"after": "A", "before": "B", "t": 2.0}, {"after": "B", "before": "C", "t": 4.0}]
    assert [m["mid"] for m in rp.lyric_midpoints(lines, bounds)] == [1.0, 3.0, 2.5]
    # Unmeasured: the line's own start, and an unknown start stays unplaced.
    assert [m["mid"] for m in rp.lyric_midpoints(lines[:1] + [{"text": "x", "start": None}], None)] \
        == [0.0, None]


def test_without_phrase_bounds_the_line_start_anchors_it(project):
    (project.parent / "analysis" / "phrase-bounds-v2.json").unlink()
    slots = _slots_by_id(rp.build(project)["local"].read_text(encoding="utf-8"))
    assert slots["S001"]["lyrics"] == ["line-one", "line-two"]
    assert slots["S002"]["lyrics"] == []


def test_slot_tags_titles_and_source_kind(project):
    slots = _slots_by_id(rp.build(project)["local"].read_text(encoding="utf-8"))
    s1, s2, s3 = slots["S001"]["body"], slots["S002"]["body"], slots["S003"]["body"]
    assert "【Room】first" in s1 and '<span class="place">Room</span>' in s1  # plan title wins
    assert "SING" in s2 and "SING" not in s1 and "SING" not in s3
    assert "靜圖（未核准）" in s1 and "影片" in s2 and "分鏡卡" in s3
    assert "剪點：淺，待耳聽" in s2 and "剪點：靜音" in s3
    assert "<em>Intro</em>" in s1 and "<em>V1</em>" in s3


def test_acts_cards_and_shallow_cut_checks(project):
    page = rp.build(project)["local"].read_text(encoding="utf-8")
    assert re.findall(r'<li class="act">(.*?)</li>', page) == ["Act I", "Act II"]
    assert "<li><b>Watch</b> it &lt;b&gt;raw&lt;/b&gt;</li>" in page  # **bold** only; html escaped
    assert page.index("<h2>Decide</h2>") < page.index("耳聽核對")
    assert "S002 的剪點" in page and "S003 的剪點" not in page
    assert "onclick=\"seek(0)\">▶ 0:01.00" in page   # 2 s pre-roll, clamped at 0


def test_thumbnail_falls_back_to_the_still_source(project):
    res = rp.build(project, thumbs=[])
    page = res["local"].read_text(encoding="utf-8")
    assert 'src="../living-master-v1/card-S003.png"' in page
    assert "stills/S001.jpg" not in page


def test_first_existing_thumb_pattern_wins(project, tmp_path):
    (tmp_path / "stills" / "S001-v2.jpg").write_bytes((tmp_path / "stills" / "S001.jpg").read_bytes())
    res = rp.build(project, thumbs=[str(tmp_path / "stills" / "{id}-v2.jpg"),
                                    str(tmp_path / "stills" / "{id}.jpg")])
    assert 'src="../stills/S001-v2.jpg"' in res["local"].read_text(encoding="utf-8")


def test_standalone_thumbnail_is_shrunk(project):
    Image = pytest.importorskip("PIL.Image")
    import base64
    import io
    page = rp.build(project, standalone=True)["standalone"].read_text(encoding="utf-8")
    b64 = re.search(r'<img src="data:image/jpeg;base64,([^"]+)"', page).group(1)
    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        assert im.width == rp.THUMB_WIDTH


def test_video_from_another_revision_warns(project):
    (project / "living-master-r002-review.mp4").write_bytes(b"old")
    res = rp.build(project, video=project / "living-master-r002-review.mp4")
    assert any("revision 2" in w for w in res["warnings"])
    assert rp.build(project)["video"].name == "living-master-r003-review.mp4"


def test_oversize_standalone_warns(project, monkeypatch):
    monkeypatch.setattr(rp, "STANDALONE_WARN_BYTES", 100)
    assert any("16 MB" in w for w in rp.build(project, standalone=True)["warnings"])


def test_standalone_every_src_is_a_data_uri(project):
    page = rp.build(project, standalone=True)["standalone"].read_text(encoding="utf-8")
    srcs = re.findall(r'\ssrc="([^"]*)"', page)
    assert len(srcs) >= 2  # the video and at least one thumbnail were actually checked
    assert all(s.startswith("data:") for s in srcs), [s[:40] for s in srcs if not s.startswith("data:")]


def test_plan_without_ids_matches_by_position_like_living_master(project):
    plan = json.loads((project / "plan.json").read_text(encoding="utf-8"))
    for p in plan:
        del p["id"]  # the documented plan.json shape: living_master init numbers them S001…
    (project / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    slots = _slots_by_id(rp.build(project)["local"].read_text(encoding="utf-8"))
    assert "【Room】first" in slots["S001"]["body"]
    assert "SING" in slots["S002"]["body"] and "SING" not in slots["S001"]["body"]


def test_timeline_and_plan_text_is_escaped(project):
    plan = json.loads((project / "plan.json").read_text(encoding="utf-8"))
    plan[0].update(title='<img src=x onerror=alert(1)>"', note="a & b <i>")
    (project / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    tl = json.loads((project / "living-master-timeline.json").read_text(encoding="utf-8"))
    tl["slots"][1]["stable_id"] = 'S0"2'
    (project / "living-master-timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    page = rp.build(project)["local"].read_text(encoding="utf-8")
    assert "<img src=x" not in page and "&lt;img src=x onerror=alert(1)&gt;&quot;" in page
    assert "a &amp; b &lt;i&gt;" in page
    assert 'data-id="S0&quot;2"' in page


def test_review_copy_beats_the_original_at_the_same_revision(project):
    (project / "living-master-r003.mp4").write_bytes(b"x" * 5000)
    assert rp.build(project)["video"].name == "living-master-r003-review.mp4"
    (project / "living-master-r003-review.mp4").unlink()
    assert rp.build(project)["video"].name == "living-master-r003.mp4"


def test_two_review_copies_pick_the_smaller_deterministically(project):
    (project / "living-master-r003-review-big.mp4").write_bytes(b"x" * 5000)
    assert rp.build(project)["video"].name == "living-master-r003-review.mp4"


def test_timecode_never_prints_sixty_seconds():
    assert rp.tc(59.999) == "1:00.00"
    assert rp.tc(119.996) == "2:00.00"
    assert rp.tc(61.5) == "1:01.50"


def test_seek_value_never_lands_before_the_slot_start():
    a = 1 / 30  # frame 1 at 30 fps
    assert float(rp._num(a)) >= a


def test_labels_do_not_depend_on_table_order():
    assert rp.label_at(15, [[10, "V1"], [0, "Intro"]]) == "V1"


def test_measured_line_missing_one_edge_is_still_placed():
    lines = [{"id": "L1", "text": "first", "start": 0.0, "end": None},
             {"id": "L2", "text": "last", "start": None, "end": None}]
    bounds = [{"after": "L1", "before": "L2", "t": 2.0}]
    assert [m["mid"] for m in rp.lyric_midpoints(lines, bounds)] == [1.0, 2.0]


def test_non_finite_plan_start_is_refused():
    with pytest.raises(ValueError):
        rp.build_rows(None, [{"id": "S001", "start": "nan"}], lyrics=[], cuts={},
                      sections=[], acts=[], end=10.0)


def test_cli_exit_codes(project, tmp_path, capsys):
    assert rp.main([str(project), "--standalone"]) == 0
    assert "send this one" in capsys.readouterr().out
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rp.main([str(empty)]) == 1
