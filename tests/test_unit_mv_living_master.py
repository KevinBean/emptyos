"""scripts/mv/living_master.py — v0 timeline, swap, final gate, release guard.

Pure checks run everywhere. Render tests need ffmpeg and Pillow and build a
two-second song from lavfi, so the whole round trip stays daemon-free.
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
sys.path.insert(0, str(ROOT / "scripts"))

import living_master as lm  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_media = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


def _timeline(duration=2.0, starts=(0, 0.5, 1.25), fps=24):
    plan = [{"start": s, "title": f"shot {i}"} for i, s in enumerate(starts)]
    return lm.build_timeline(plan, audio_path="a.wav", audio_sha256="x",
                             duration_seconds=duration, fps=fps, width=160, height=90)


# ── Pure timeline shape ─────────────────────────────────────────────────────

def test_slots_tile_the_whole_song_half_open():
    t = _timeline(duration=2.01)
    frames = [(s["destination_start_frame"], s["destination_end_frame"]) for s in t["slots"]]
    assert frames == [(0, 12), (12, 30), (30, 49)]   # last end = ceil(2.01*24) = 49
    assert t["total_frames"] == 49
    assert lm.structure_errors(t) == []


def test_every_slot_starts_as_an_unapproved_placeholder():
    t = _timeline()
    assert lm.unapproved(t) == ["S001", "S002", "S003"]


@pytest.mark.parametrize("starts,msg", [
    ((0.5, 1.0), "start at 0"),
    ((0, 1.0, 0.5), "empty or out of order"),
])
def test_bad_plans_refused(starts, msg):
    with pytest.raises(lm.TimelineError, match=msg):
        _timeline(starts=starts)


def test_duplicate_ids_refused():
    with pytest.raises(lm.TimelineError, match="duplicate"):
        lm.build_timeline([{"start": 0, "id": "A"}, {"start": 1, "id": "A"}],
                          audio_path="a", audio_sha256="x", duration_seconds=2)


def test_gap_overlap_and_retime_are_structure_errors():
    t = _timeline()
    t["slots"][1]["destination_start_frame"] = 13
    assert any("gap or overlap" in e for e in lm.structure_errors(t))
    t = _timeline()
    t["slots"][0]["end_frame"] = 20
    assert any("no retiming" in e for e in lm.structure_errors(t))
    t = _timeline()
    t["slots"][-1]["destination_end_frame"] -= 1
    t["slots"][-1]["end_frame"] -= 1
    assert any("song is" in e for e in lm.structure_errors(t))


def test_source_hash_mismatch_is_a_file_error(tmp_path):
    t = _timeline()
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"audio")
    t["audio_sha256"] = lm.sha256_file(audio)
    for s in t["slots"]:
        card = tmp_path / f"{s['stable_id']}.png"
        card.write_bytes(s["stable_id"].encode())
        s["source"], s["source_sha256"] = card.name, lm.sha256_file(card)
    assert lm.file_errors(t, tmp_path) == []
    (tmp_path / "S002.png").write_bytes(b"edited in place")
    assert lm.file_errors(t, tmp_path) == ["S002: source changed since it was placed (sha256 mismatch)"]


# ── Release guard ───────────────────────────────────────────────────────────

def _sidecar(master: Path, *, final: bool, placeholders: int):
    lm.sidecar_for(master).write_text(json.dumps({
        "master": master.name, "master_sha256": lm.sha256_file(master),
        "final": final, "placeholder_slots": placeholders}), encoding="utf-8")


def test_guard_blocks_review_render_by_name(tmp_path):
    f = tmp_path / "living-master-r007.mp4"
    f.write_bytes(b"v")
    assert "review render" in lm.release_block_reason(f)


def test_guard_blocks_renamed_hardlink_of_placeholder_master(tmp_path):
    mv = tmp_path / "mv"
    (mv / "living-master").mkdir(parents=True)
    (mv / "release").mkdir()
    master = mv / "living-master" / "living-master-r003.mp4"
    master.write_bytes(b"review cut")
    _sidecar(master, final=False, placeholders=4)
    release = mv / "release" / "Song-MV-1080p.mp4"
    shutil.copy2(master, release)
    assert "4 placeholder" in lm.release_block_reason(release)


def test_guard_passes_final_master_and_unrelated_video(tmp_path):
    mv = tmp_path / "mv"
    (mv / "living-master").mkdir(parents=True)
    (mv / "release").mkdir()
    final = mv / "living-master" / "living-master-final-r009.mp4"
    final.write_bytes(b"final cut")
    _sidecar(final, final=True, placeholders=0)
    release = mv / "release" / "Song-MV-1080p.mp4"
    shutil.copy2(final, release)
    assert lm.release_block_reason(release) is None
    other = mv / "release" / "Song-static.mp4"
    other.write_bytes(b"static visualizer")
    assert lm.release_block_reason(other) is None


def test_youtube_push_refuses_placeholder_master_before_connecting(tmp_path, monkeypatch, capsys):
    import youtube_push_song as push

    rel = tmp_path / "release"
    rel.mkdir()
    (rel / "release-package.md").write_text(
        "- **Title:** Test\n- **Privacy:** private\n", encoding="utf-8")
    (rel / "living-master-r002.mp4").write_bytes(b"v")

    def no_network():
        raise AssertionError("connected to YouTube before the placeholder guard")

    monkeypatch.setattr(push.yt, "load_client", no_network)
    monkeypatch.setattr(sys, "argv", ["youtube_push_song.py", str(tmp_path), "--skip-qa", "--force"])
    assert push.main() == 5
    assert "REFUSING" in capsys.readouterr().out


# ── Round trip with real ffmpeg ─────────────────────────────────────────────

def _song(folder: Path, seconds=2.0) -> Path:
    audio = folder / "song.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=330:duration={seconds}", str(audio)], check=True)
    return audio


def _clip(path: Path, seconds: float):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=blue:size=160x90:rate=24:duration={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def _frame_rgb(video: Path, n: int, x: int, y: int):
    from PIL import Image

    png = video.with_name(f"f{n}.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
                    f"select=eq(n\\,{n})", "-frames:v", "1", str(png)], check=True)
    return Image.open(png).convert("RGB").getpixel((x, y))


@pytest.fixture
def v0(tmp_path):
    pytest.importorskip("PIL")
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    folder = tmp_path / "lm"
    audio = _song(tmp_path)
    plan = [{"start": 0, "title": "open"}, {"start": 1.0, "title": "close"}]
    lm.init(folder, audio, plan, fps=24, width=320, height=180, font_path=None)
    return folder


@needs_media
def test_v0_renders_full_length_with_placeholders_counted(v0):
    master = lm.render(v0)
    assert master.name == "living-master-r001.mp4"
    assert lm.asyncio.run(lm._probe_frames(master)) == 48
    car = json.loads(lm.sidecar_for(master).read_text(encoding="utf-8"))
    assert car["placeholder_slots"] == 2 and car["final"] is False
    assert car["master_sha256"] == lm.sha256_file(master)


@needs_media
def test_final_refused_until_every_slot_approved(v0, tmp_path):
    with pytest.raises(lm.FinalRefused):
        lm.render(v0, final=True)
    assert lm.main(["render", str(v0), "--final"]) == 2
    clip = tmp_path / "c.mp4"
    _clip(clip, 1.5)
    lm.swap(v0, "S001", clip, approve=True)
    with pytest.raises(lm.FinalRefused, match="S002"):
        lm.render(v0, final=True)


@needs_media
def test_swap_refuses_a_clip_too_short_for_the_slot(v0, tmp_path):
    clip = tmp_path / "short.mp4"
    _clip(clip, 0.5)                        # 12 frames; S001 needs 24
    before = (v0 / lm.TIMELINE_NAME).read_text(encoding="utf-8")
    with pytest.raises(lm.TimelineError, match="needs 24 frames"):
        lm.swap(v0, "S001", clip)
    assert (v0 / lm.TIMELINE_NAME).read_text(encoding="utf-8") == before
    with pytest.raises(lm.TimelineError, match="from frame 10"):
        lm.swap(v0, "S001", tmp_path / "short.mp4", in_frame=10)


@needs_media
def test_swap_keeps_identity_and_bumps_revision(v0, tmp_path):
    clip = tmp_path / "c.mp4"
    _clip(clip, 1.5)
    t = lm.swap(v0, "S002", clip, in_frame=6)
    slot = t["slots"][1]
    assert slot["stable_id"] == "S002"
    assert (slot["destination_start_frame"], slot["destination_end_frame"]) == (24, 48)
    assert (slot["start_frame"], slot["end_frame"]) == (6, 30)
    assert slot["production_approved"] is False
    assert t["revision"] == 2
    assert (v0 / "history" / "living-master-timeline-r001.json").is_file()


@needs_media
def test_badges_are_frame_exact_and_absent_from_the_final(v0, tmp_path):
    clip = tmp_path / "c.mp4"
    _clip(clip, 1.5)
    lm.swap(v0, "S001", clip, approve=True)
    review = lm.render(v0)
    red = lambda px: px[0] > 150 and px[1] < 90 and px[2] < 90  # noqa: E731
    # badge sits top-right; S001 (0-23) approved, S002 (24-47) not
    x, y = 320 - 24 - 4, 24 + 4
    assert not red(_frame_rgb(review, 23, x, y))
    assert red(_frame_rgb(review, 24, x, y))
    assert red(_frame_rgb(review, 47, x, y))
    lm.swap(v0, "S002", clip, approve=True)
    final = lm.render(v0, final=True)
    assert final.name.startswith("living-master-final-")
    assert not red(_frame_rgb(final, 47, x, y))
    car = json.loads(lm.sidecar_for(final).read_text(encoding="utf-8"))
    assert car["final"] is True and car["placeholder_slots"] == 0
    assert lm.release_block_reason(final) is None
    assert "placeholder" in lm.release_block_reason(review) or "review render" in lm.release_block_reason(review)


@needs_media
def test_init_never_overwrites_an_existing_timeline(v0, tmp_path):
    with pytest.raises(lm.TimelineError, match="already exists"):
        lm.init(v0, _song(tmp_path), [{"start": 0}], font_path=None)


def test_import_does_not_load_the_kernel():
    code = (f"import sys; sys.path.insert(0, r'{ROOT / 'scripts' / 'mv'}'); import living_master; "
            "print(any(m.startswith('emptyos.kernel') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# ── Review-round fixes ──────────────────────────────────────────────────────

def test_zero_placeholder_review_sidecar_does_not_block(tmp_path):
    """An all-approved review cut is byte-identical to the final — never block it."""
    mv = tmp_path / "mv"
    (mv / "living-master").mkdir(parents=True)
    (mv / "release").mkdir()
    review = mv / "living-master" / "living-master-r009.mp4"
    review.write_bytes(b"same bytes")
    _sidecar(review, final=False, placeholders=0)
    final = mv / "living-master" / "living-master-final-r009.mp4"
    final.write_bytes(b"same bytes")
    _sidecar(final, final=True, placeholders=0)
    release = mv / "release" / "Song-MV.mp4"
    shutil.copy2(final, release)
    assert lm.release_block_reason(release) is None


def test_extra_search_root_finds_a_sidecar_outside_the_grandparent(tmp_path):
    song = tmp_path / "song"
    (song / "work" / "lm").mkdir(parents=True)
    rel = tmp_path / "elsewhere" / "deep" / "release"
    rel.mkdir(parents=True)
    master = song / "work" / "lm" / "living-master-r001.mp4"
    master.write_bytes(b"cut")
    _sidecar(master, final=False, placeholders=2)
    copy = rel / "Song.mp4"
    shutil.copy2(master, copy)
    assert lm.release_block_reason(copy) is None
    assert "2 placeholder" in lm.release_block_reason(copy, search_roots=[song])


def test_youtube_push_refuses_a_renamed_copy_by_sha(tmp_path, monkeypatch, capsys):
    import youtube_push_song as push

    lmdir = tmp_path / "living-master"
    lmdir.mkdir()
    review = lmdir / "living-master-r004.mp4"
    review.write_bytes(b"review cut")
    _sidecar(review, final=False, placeholders=3)
    rel = tmp_path / "release"
    rel.mkdir()
    shutil.copy2(review, rel / "Song-MV-1080p.mp4")
    (rel / "release-package.md").write_text("- **Title:** Test\n- **Privacy:** private\n", encoding="utf-8")

    def no_network():
        raise AssertionError("connected to YouTube before the placeholder guard")

    monkeypatch.setattr(push.yt, "load_client", no_network)
    monkeypatch.setattr(sys, "argv", ["youtube_push_song.py", str(tmp_path), "--skip-qa", "--force"])
    assert push.main() == 5
    assert "3 placeholder" in capsys.readouterr().out


@needs_media
def test_swap_refuses_other_frame_rates_and_mismatched_kinds(v0, tmp_path):
    clip30 = tmp_path / "c30.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:size=160x90:rate=30:duration=2",
                    "-pix_fmt", "yuv420p", str(clip30)], check=True)
    with pytest.raises(lm.TimelineError, match="30.0 fps"):
        lm.swap(v0, "S001", clip30)
    with pytest.raises(lm.TimelineError, match="cannot be used as a still"):
        lm.swap(v0, "S001", clip30, kind="still")


@needs_media
def test_verify_catches_a_slot_reading_past_its_video(v0, tmp_path):
    clip = tmp_path / "c.mp4"
    _clip(clip, 1.5)
    lm.swap(v0, "S001", clip, in_frame=10)          # reads frames 10..34 of 36
    t = lm.load(v0)
    t["slots"][0]["start_frame"], t["slots"][0]["end_frame"] = 20, 44
    lm.save(v0, t, bump=False)
    assert lm.main(["verify", str(v0), "--json"]) == 1


@needs_media
def test_failed_final_leaves_no_final_named_master(v0, tmp_path, monkeypatch):
    clip = tmp_path / "c.mp4"
    _clip(clip, 1.5)
    lm.swap(v0, "S001", clip, approve=True)
    lm.swap(v0, "S002", clip, approve=True)

    real = lm._probe_frames

    async def wrong_count(path):
        return 1 if path.parent.name == ".render" else await real(path)

    monkeypatch.setattr(lm, "_probe_frames", wrong_count)
    with pytest.raises(lm.TimelineError, match="rendered 1 frames"):
        lm.render(v0, final=True)
    assert not list(v0.glob("living-master-final-*.mp4"))


# ── Phase 4 review: lineage and card approval ───────────────────────────────

def _lm_project(tmp_path):
    """An MV folder with living-master/<timeline> and release/ beside it."""
    mv = tmp_path / "mv"
    (mv / "living-master").mkdir(parents=True)
    (mv / "release").mkdir()
    (mv / "living-master" / lm.TIMELINE_NAME).write_text("{}", encoding="utf-8")
    return mv


def test_in_a_living_master_project_an_untraced_file_is_blocked(tmp_path):
    mv = _lm_project(tmp_path)
    loose = mv / "release" / "Song-MV.mp4"
    loose.write_bytes(b"made some other way")
    assert "does not trace back" in lm.release_block_reason(loose)


def test_outside_a_living_master_project_an_unknown_file_is_allowed(tmp_path):
    (tmp_path / "song" / "release").mkdir(parents=True)
    f = tmp_path / "song" / "release" / "static.mp4"
    f.write_bytes(b"visualizer")
    assert lm.release_block_reason(f) is None


def test_derived_sidecar_carries_lineage_only_from_a_clean_render(tmp_path):
    mv = _lm_project(tmp_path)
    final = mv / "living-master" / "living-master-final-r007.mp4"
    final.write_bytes(b"final cut")
    _sidecar(final, final=True, placeholders=0)
    finished = mv / "release" / "Song-MV-subtitled.mp4"
    finished.write_bytes(b"final cut + subtitles")
    assert lm.write_derived_sidecar(finished, final, "burn_subtitles") is True
    assert lm.release_block_reason(finished) is None
    review = mv / "living-master" / "living-master-r006.mp4"
    review.write_bytes(b"review cut")
    _sidecar(review, final=False, placeholders=3)
    other = mv / "release" / "from-review.mp4"
    other.write_bytes(b"review cut + cards")
    assert lm.write_derived_sidecar(other, review, "cards") is False
    assert "does not trace back" in lm.release_block_reason(other)


@needs_media
def test_a_storyboard_card_can_never_be_approved(v0):
    card = v0 / "cards" / "S001.png"
    with pytest.raises(lm.TimelineError, match="storyboard card"):
        lm.swap(v0, "S001", card, approve=True)
    lm.swap(v0, "S001", card)                      # re-placing a card is fine
    with pytest.raises(lm.TimelineError, match="storyboard card"):
        lm.approve(v0, "S001")


def test_cards_and_subtitles_pass_lineage_on(tmp_path, monkeypatch):
    import burn_subtitles
    import cards

    mv = _lm_project(tmp_path)
    final = mv / "living-master" / "living-master-final-r001.mp4"
    final.write_bytes(b"final")
    _sidecar(final, final=True, placeholders=0)

    def fake_run(cmd, *a, **k):
        Path(cmd[-1]).write_bytes(b"out " + cmd[-1].encode())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cards, "fonts_from_config", lambda: {})
    monkeypatch.setattr(cards, "render_pngs", lambda *a, **k: {"cards": [], "title": None, "watermark": None})
    monkeypatch.setattr(cards.subprocess, "run", fake_run)
    spec = tmp_path / "cards.json"
    spec.write_text("{}", encoding="utf-8")
    carded = mv / "release" / "carded.mp4"
    assert cards.main([str(spec), str(final), str(carded)]) == 0
    assert lm.release_block_reason(carded) is None

    monkeypatch.setattr(burn_subtitles.mv_config, "require", lambda key, *a, **k: Path("font"))
    monkeypatch.setattr(burn_subtitles, "probe_width", lambda v: 1920)
    monkeypatch.setattr(burn_subtitles.text_render, "render_cue", lambda out, *a, **k: out)
    monkeypatch.setattr(burn_subtitles.subprocess, "run", fake_run)
    srt = tmp_path / "l.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n一\n", encoding="utf-8")
    subbed = mv / "release" / "subbed.mp4"
    assert burn_subtitles.main([str(srt), str(carded), str(subbed)]) == 0
    assert lm.release_block_reason(subbed) is None
