"""scripts/mv post tools — shot planning, grade strings, text, ambience, upscale.

Daemon-free. ffmpeg, numpy and Pillow are optional: tests needing them skip.
Parity against the 〈說得太急〉 rescue-v18 outputs was checked separately (the
fixtures are the user's own footage and live in the vault).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "mv"))

import ambience  # noqa: E402
import burn_subtitles  # noqa: E402
import cards  # noqa: E402
import post  # noqa: E402
import render_shots as rs  # noqa: E402
import upscale_crops  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")


def _spec(tmp_path, shots, **over):
    raw = {"song_seconds": 12.0, "fps": 24, "end_picture": 10.0, "dissolve_frames": 12,
           "min_speed": 0.72, "audio": "song.wav", "clip_dirs": ["clips"], "shots": shots, **over}
    (tmp_path / "clips").mkdir(exist_ok=True)
    for s in shots:
        (tmp_path / "clips" / f"{s['clip']}.mp4").write_bytes(b"x")
    (tmp_path / "song.wav").write_bytes(b"x")
    p = tmp_path / "shots.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return rs.Spec.load(p)


def _shot(t, clip, a, b, tr=None, crop=None):
    return {"t": t, "clip": clip, "in": a, "out": b, "transition": tr, "why": "", "crop": crop}


# ── Planner ─────────────────────────────────────────────────────────────────

def test_cut_and_dissolve_arithmetic(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 5), _shot(4, "B", 0, 8, "dissolve"), _shot(7, "C", 0, 4, "cut")])
    segs = rs.plan(spec)
    # A: 96 frames + half the dissolve out; B: 72 + half in + nothing out; C: 72
    assert [s["record_frames"] for s in segs] == [102, 78, 72]
    assert [s["dissolve_in"] for s in segs] == [False, True, False]
    # A has 120 source frames for 102 → plays at 1.0, reading 3 frames of slack
    assert segs[0]["speed"] == 1.0 and segs[0]["src_out_f"] == 105
    # C has 96 for 72 → 1.0; B has 192 for 78 → 1.0
    assert all(s["speed"] == 1.0 for s in segs)


def test_short_source_plays_slowed_and_bounded(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 8.0)], end_picture=10.0)
    seg = rs.plan(spec)[0]
    assert seg["record_frames"] == 240
    assert seg["speed"] == round(192 / 240, 3)
    assert seg["src_out_f"] == 192          # never reads past the clean range


def test_too_slow_is_refused(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 5.0)], end_picture=10.0)   # 0.5x
    with pytest.raises(rs.PlanError, match="0.500x"):
        rs.plan(spec)


def test_same_framing_twice_is_refused(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 5), _shot(5, "A", 0, 5)])
    with pytest.raises(rs.PlanError, match="used twice"):
        rs.plan(spec)


def test_overlapping_source_time_is_refused_even_with_another_crop(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 5), _shot(5, "A", 4, 9, crop="400:225:0:0")])
    with pytest.raises(rs.PlanError, match="overlapping source"):
        rs.plan(spec)


def test_same_clip_disjoint_ranges_allowed(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 5), _shot(5, "A", 5, 10, crop="400:225:0:0")])
    assert len(rs.plan(spec)) == 2


def test_times_must_increase(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 5), _shot(0, "B", 0, 5)])
    with pytest.raises(rs.PlanError, match="increase"):
        rs.plan(spec)


# ── Command / grade strings ─────────────────────────────────────────────────

def test_letterbox_is_2_39_centred():
    assert post.letterbox(1920, 1080, 2.39) == "crop=1920:804:0:138,pad=1920:1080:0:138:black"


def test_command_carries_grade_zones_and_transitions(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 5), _shot(4, "M", 0, 8, "dissolve"), _shot(7, "D", 0, 2.5, "cut")],
                 grade={"memory": ["M"], "night_pull": ["D"], "letterbox": 2.39},
                 ambience="rain.wav")
    (tmp_path / "rain.wav").write_bytes(b"x")
    segs = rs.plan(spec)
    cmd = rs.build_command(spec, segs, tmp_path / "o.mp4", gains={0: 1.0, 1: 0.8, 2: 1.2})
    graph = cmd[cmd.index("-filter_complex") + 1]
    parts = graph.split(";")
    look = {i: next(p for p in parts if p.startswith(f"[pre{i}]")) for i in range(3)}
    assert post.MEMORY.split(",")[0] in look[1] and post.NIGHT.split(",")[0] in look[0]
    assert graph.count("gblur=sigma=3[") == 1                              # haze on memory only
    assert "gblur=sigma=3[hc1]" in graph
    pre2 = next(p for p in parts if p.endswith("[pre2]"))
    assert post.NIGHT_PULL in pre2 and "minterpolate" in pre2               # 60 src for 72 → slowed
    assert "minterpolate" not in next(p for p in parts if p.endswith("[pre0]"))
    assert f"xfade=transition=fade:duration={12 / 24:.6f}:offset={(102 - 12) / 24:.6f}" in graph
    assert "crop=1920:804:0:138" in graph
    assert "amix=inputs=2:normalize=0:duration=first" in graph
    assert cmd[cmd.index("-t") + 1] == "12"


def test_no_ambience_maps_the_song_directly(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 10)])
    cmd = rs.build_command(spec, rs.plan(spec), tmp_path / "o.mp4", gains={0: 1.0})
    assert "amix" not in cmd[cmd.index("-filter_complex") + 1]
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "1:a"


def test_crop_shot_needs_its_insert(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 10, crop="400:225:0:0")])
    with pytest.raises(rs.PlanError, match="upscale_crops"):
        rs.build_command(spec, rs.plan(spec), tmp_path / "o.mp4", gains={0: 1.0})
    insert = spec.crop_path("A", 0.0)
    insert.parent.mkdir(parents=True)
    insert.write_bytes(b"x")
    cmd = rs.build_command(spec, rs.plan(spec), tmp_path / "o.mp4", gains={0: 1.0})
    assert str(insert) in cmd
    assert "trim=start_frame=0:end_frame=" in cmd[cmd.index("-filter_complex") + 1]


@needs_ffmpeg
def test_shot_gain_brings_luma_to_target_and_clamps(tmp_path):
    pytest.importorskip("numpy")
    grey = tmp_path / "g.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x404040:size=320x180:rate=24:duration=1", "-pix_fmt", "yuv420p", str(grey)],
                   check=True)
    grade = post.Grade()
    g = post.shot_gain(str(grey), 5, grade)
    assert g == pytest.approx(0.165 / (0x40 / 255), rel=0.05)
    assert post.shot_gain(str(grey), 5, post.Grade(exposure_target=0.9)) == grade.gain_max


# ── Text ────────────────────────────────────────────────────────────────────

SRT = """1
00:00:01,000 --> 00:00:04,000
第一句
First line

2
00:00:03,500 --> 00:00:06,000
第二句

"""


def test_srt_cues_are_clipped_so_only_one_shows():
    cues = burn_subtitles.parse_srt(SRT)
    assert cues == [[1.0, 3.46, "第一句", "First line"], [3.5, 6.0, "第二句", ""]]


def test_subtitle_overlays_use_enable_windows():
    cues = burn_subtitles.parse_srt(SRT)
    cmd = burn_subtitles.build_command(Path("in.mp4"), Path("out.mp4"), cues,
                                       [Path("c1.png"), Path("c2.png")], bottom=10)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "enable='between(t,1.000,3.460)'" in graph and "y=H-h-10" in graph
    assert cmd[cmd.index("-c:a") + 1] == "copy"


def test_cards_command_layers_cards_then_title_then_watermark():
    spec = {"fps": 24, "fade": 0.8,
            "cards": [{"start": 5.0, "end": 9.0, "columns": ["a"], "x": 100, "top": 100}],
            "title": {"start": 1.0, "end": 5.0, "zh": "t", "en": "T", "fade": 0.7},
            "watermark": ["w"]}
    cmd = cards.build_command(spec, Path("in.mp4"), Path("out.mp4"),
                              {"cards": [Path("c.png")], "title": Path("t.png"), "watermark": Path("w.png")})
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.index("[c1]") < graph.index("[ct]") < graph.index("[ow]")
    assert "fade=t=out:st=3.200:d=0.8:alpha=1,setpts=PTS+5.0/TB" in graph
    assert cmd[cmd.index("-map") + 1] == "[ow]"


@pytest.mark.parametrize("tool", [cards, burn_subtitles])
def test_text_tools_refuse_a_placeholder_render(tmp_path, tool, capsys):
    src = tmp_path / "living-master-r003.mp4"
    src.write_bytes(b"v")
    args = ([str(tmp_path / "s.json"), str(src), str(tmp_path / "o.mp4")] if tool is cards
            else [str(tmp_path / "l.srt"), str(src), str(tmp_path / "o.mp4")])
    assert tool.main(args) == 1
    assert "refusing input" in capsys.readouterr().err


def test_text_renderers_draw_inside_their_canvas(tmp_path):
    pytest.importorskip("PIL")
    import text_render

    card = text_render.render_vertical_card(tmp_path / "c.png", ["ab", "c"], "two words", cx=300,
                                            top=100, align="left", width=640, height=360, size=20)
    cue = text_render.render_cue(tmp_path / "q.png", "zh", "en", width=640, height=60, zh_size=20, en_size=12)
    mark = text_render.render_watermark(tmp_path / "w.png", ["line"], width=640, height=360, size=12)
    from PIL import Image
    for p, size in ((card, (640, 360)), (cue, (640, 60)), (mark, (640, 360))):
        im = Image.open(p)
        assert im.size == size and im.mode == "RGBA"
        assert im.getchannel("A").getbbox() is not None      # something was drawn
    bbox = Image.open(mark).getchannel("A").getbbox()
    assert bbox[2] <= 640 - 48 + 2 and bbox[3] > 360 - 38 - 30  # bottom-right corner


# ── Ambience ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_bed_levels_and_fades_each_segment(tmp_path):
    np = pytest.importorskip("numpy")
    src = tmp_path / "noise.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:duration=10:amplitude=0.3", str(src)], check=True)
    sr = 8000
    bed = ambience.build_bed(src, 6.0, [(0.0, 2.0, 1.0, 0.5, 0.5), (4.0, None, 5.0, 0.5, 0.5)],
                             dbfs=-30.0, sr=sr)
    assert len(bed) == 6 * sr
    full = bed[int(0.6 * sr):int(1.4 * sr)]
    assert 20 * np.log10(np.sqrt(np.mean(full ** 2))) == pytest.approx(-30.0, abs=1.5)
    assert np.all(bed[int(2.0 * sr):int(4.0 * sr)] == 0)        # silent between segments
    assert abs(bed[0]) < 1e-9 and abs(bed[-1]) < 0.02            # faded at the edges


def test_segment_parsing_and_bounds():
    assert ambience.parse_segment("156:end:60:4:1.6") == (156.0, None, 60.0, 4.0, 1.6)
    with pytest.raises(Exception):
        ambience.parse_segment("1:2:3")
    with pytest.raises(ValueError, match="outside the song"):
        ambience.build_bed(Path("unused"), 5.0, [(4.0, 6.0, 0.0, 1.0, 1.0)])


# ── Upscale ─────────────────────────────────────────────────────────────────

def test_upscale_blend_command_weights_esrgan_over_lanczos():
    cmd = upscale_crops.blend_command(Path("l"), Path("e"), Path("o.mp4"), fps=24, width=1920,
                                      height=1080, blend=0.3)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[e][l]blend=all_mode=normal:all_opacity=0.3" in graph
    assert "scale=1920:1080:flags=lanczos" in graph


def test_upscale_rejects_bad_blend_and_skips_a_spec_without_crops(tmp_path, capsys):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 10)], upscale={"blend": 1.5})
    assert upscale_crops.main([str(spec.base / "shots.json")]) == 1
    spec = _spec(tmp_path, [_shot(0, "A", 0, 10)])
    assert upscale_crops.main([str(spec.base / "shots.json")]) == 0
    assert "no crop shots" in capsys.readouterr().out


# ── Review-round fixes ──────────────────────────────────────────────────────

@pytest.mark.parametrize("shots,over,msg", [
    ([_shot(0, "A", 0, 10)], {"dissolve_frames": 11}, "even number"),
    ([_shot(0, "A", 0, 10)], {"song_seconds": 9.0}, "shorter than end_picture"),
    ([_shot(0, "A", 0, 10)], {"grade": {"fade_out": 20.0}}, "fade_out"),
    ([_shot(1, "A", 0, 10)], {}, "first shot must start at 0"),
    ([_shot(0, "A", 0, 5), _shot(5, "B", 0, 6, "fade")], {}, "transition must be"),
    ([_shot(0, "A", 0, 10, crop="400:300:0:0")], {}, "would be stretched"),
    ([_shot(0, "A", 0, 5), _shot(5, "B", 0, 1, "dissolve"), _shot(5.2, "C", 0, 6, "dissolve")], {},
     "shorter than its dissolves"),
    ([_shot(0, "A", 0, 10)], {"end_picture": None}, "needs end_picture"),
])
def test_plan_refuses_malformed_specs(tmp_path, shots, over, msg):
    with pytest.raises(rs.PlanError, match=msg):
        rs.plan(_spec(tmp_path, shots, **over))


def test_near_16_9_crops_from_v18_are_accepted(tmp_path):
    shots = [_shot(0, "A", 0, 3, crop="420:236:840:110"), _shot(3, "B", 0, 3, crop="380:214:780:80"),
             _shot(6, "C", 0, 4.5, crop="520:292:0:100")]
    assert len(rs.plan(_spec(tmp_path, shots))) == 3


def test_edl_frame_count_comes_from_the_segments(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 5), _shot(4, "B", 0, 8, "dissolve")])
    rs.write_edl(spec, rs.plan(spec), None, tmp_path / "e.json")
    assert json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))["picture_frames"] == 240


def _real_clip(path, seconds, rate=24):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=gray:size=64x36:rate={rate}:duration={seconds}", "-pix_fmt", "yuv420p", str(path)],
                   check=True)


@needs_ffmpeg
def test_source_checks_catch_rate_length_stale_insert_and_placeholder(tmp_path):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 4), _shot(4, "B", 0, 6), _shot(6, "C", 0, 4, crop="64:36:0:0")])
    _real_clip(tmp_path / "clips" / "A.mp4", 5, rate=30)          # wrong rate
    _real_clip(tmp_path / "clips" / "B.mp4", 1)                    # too short
    _real_clip(tmp_path / "clips" / "C.mp4", 5)
    insert = spec.crop_path("C", 0.0)
    insert.parent.mkdir(parents=True)
    _real_clip(insert, 5)
    errors = " | ".join(rs.source_errors(spec, rs.plan(spec)))
    assert "A.mp4 runs at 30.0 fps" in errors
    assert "B.mp4 has 24 frames" in errors
    assert "made for different settings" in errors                  # no stamp yet
    insert.with_name(insert.name + ".json").write_text(json.dumps(rs.crop_stamp(spec, spec.get("shots")[2])))
    errors = " | ".join(rs.source_errors(spec, rs.plan(spec)))
    assert "different settings" not in errors
    two = tmp_path / "two"
    two.mkdir()
    review = two / "review.mp4"
    _real_clip(review, 11)
    import living_master
    living_master.sidecar_for(review).write_text(json.dumps({
        "master": "living-master-r002.mp4", "master_sha256": living_master.sha256_file(review),
        "placeholder_slots": 3}), encoding="utf-8")
    spec2 = _spec(two, [_shot(0, "D", 0, 10)], clip_paths={"D": str(review)})
    assert any("placeholder" in e for e in rs.source_errors(spec2, rs.plan(spec2)))


def test_render_reports_a_missing_clip_without_a_traceback(tmp_path, capsys):
    spec = _spec(tmp_path, [_shot(0, "A", 0, 10)])
    (tmp_path / "clips" / "A.mp4").unlink()
    assert rs.main(["render", str(spec.base / "shots.json"), str(tmp_path / "o.mp4")]) == 1
    assert "no source file for clip A" in capsys.readouterr().err


@needs_ffmpeg
def test_zero_fade_is_a_hard_edge_not_nan(tmp_path):
    np = pytest.importorskip("numpy")
    src = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:duration=4:amplitude=0.3", str(src)], check=True)
    bed = ambience.build_bed(src, 3.0, [(1.0, 2.0, 0.0, 0.0, 0.0)], sr=8000)
    assert not np.isnan(bed).any()
    assert np.all(bed[:8000] == 0) and np.any(bed[8000:16000] != 0) and np.all(bed[16000:] == 0)
    with pytest.raises(ValueError, match="too short"):
        ambience.build_bed(src, 10.0, [(0.0, 8.0, 0.0, 1.0, 1.0)], sr=8000)


def test_title_scales_with_the_frame(tmp_path):
    pytest.importorskip("PIL")
    import text_render
    from PIL import Image

    big = Image.open(text_render.render_title(tmp_path / "a.png", "ab", "AB", width=1920, height=1080))
    small = Image.open(text_render.render_title(tmp_path / "b.png", "ab", "AB", width=1280, height=720))
    ya = big.getchannel("A").getbbox()
    yb = small.getchannel("A").getbbox()
    assert abs((ya[1] + ya[3]) / 2 / 1080 - (yb[1] + yb[3]) / 2 / 720) < 0.02


def test_srt_accepts_dot_milliseconds_and_drops_an_unshowable_cue(capsys):
    srt = "1\n00:00:00.500 --> 00:00:02.000\n一\n\n2\n00:00:01,020 --> 00:00:03,000\n二\n\n3\n00:00:01,040 --> 00:00:04,000\n三\n"
    cues = burn_subtitles.parse_srt(srt)
    assert [c[2] for c in cues] == ["一", "三"]
    assert "dropping cue at 1.020s" in capsys.readouterr().err


@pytest.mark.parametrize("tool", [cards, burn_subtitles])
def test_text_tools_refuse_a_renamed_copy_of_a_placeholder_render(tmp_path, tool, capsys):
    import living_master

    render = tmp_path / "lm" / "living-master-r005.mp4"
    render.parent.mkdir()
    render.write_bytes(b"review cut")
    living_master.sidecar_for(render).write_text(json.dumps({
        "master": render.name, "master_sha256": living_master.sha256_file(render),
        "placeholder_slots": 2}), encoding="utf-8")
    renamed = tmp_path / "x" / "picture.mp4"
    renamed.parent.mkdir()
    shutil.copy2(render, renamed)
    args = ([str(tmp_path / "s.json"), str(renamed), str(tmp_path / "o.mp4")] if tool is cards
            else [str(tmp_path / "l.srt"), str(renamed), str(tmp_path / "o.mp4")])
    assert tool.main(args) == 1
    assert "2 placeholder" in capsys.readouterr().err


@needs_ffmpeg
def test_living_master_spec_requires_approved_clips_and_writes_a_final_sidecar(tmp_path):
    import living_master

    spec = _spec(tmp_path, [_shot(0, "A", 0, 10)], living_master="lm")
    _real_clip(tmp_path / "clips" / "A.mp4", 11)
    (tmp_path / "lm").mkdir()
    timeline = {"schema_version": 1, "revision": 1, "slots": [
        {"stable_id": "S001", "production_approved": False,
         "source_sha256": living_master.sha256_file(tmp_path / "clips" / "A.mp4")}]}
    (tmp_path / "lm" / living_master.TIMELINE_NAME).write_text(json.dumps(timeline), encoding="utf-8")
    errors = rs.source_errors(spec, rs.plan(spec))
    assert any("not an approved source" in e for e in errors)
    timeline["slots"][0]["production_approved"] = True
    (tmp_path / "lm" / living_master.TIMELINE_NAME).write_text(json.dumps(timeline), encoding="utf-8")
    assert rs.source_errors(spec, rs.plan(spec)) == []
    out = tmp_path / "picture.mp4"
    out.write_bytes(b"graded")
    rs.write_final_sidecar(spec, out)
    car = json.loads(living_master.sidecar_for(out).read_text(encoding="utf-8"))
    assert car["final"] is True and car["placeholder_slots"] == 0
    assert car["master_sha256"] == living_master.sha256_file(out)
