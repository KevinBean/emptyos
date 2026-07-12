"""Unit tests for the slideshow-timing fallback.

Regression guard for the bug where VibeVoice single-pass episodes (one combined
audio file, empty per-turn ``audio``) produced an empty timeline, forcing
``has_slideshow`` to False and shipping audio-only podcasts. The fix distributes
the known full-audio duration across the script proportionally so the slideshow
still renders. Pure functions — no kernel boot, no daemon, no pydub for the
proportional path.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import types

from emptyos.sdk.media.subtitles import proportional_timings
from helpers import app_path
import pytest

_SCRIPT = [
    {"speaker": "A", "text": "x" * 80},
    {"speaker": "B", "text": "y" * 40},
    {"speaker": "A", "text": "z" * 120},
    {"speaker": "B", "text": "w" * 60},
]


def _load_pipeline():
    try:
        path = app_path("podcast") / "pipeline.py"
    except FileNotFoundError:
        pytest.skip("podcast app is not present in this checkout")
    spec = importlib.util.spec_from_file_location("podcast_pipeline_timings", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestProportionalTimings:
    def test_produces_one_timing_per_line(self):
        t = proportional_timings(200_000, _SCRIPT)
        assert len(t) == len(_SCRIPT)

    def test_last_end_equals_total(self):
        t = proportional_timings(267_867, _SCRIPT)
        assert t[-1]["end_ms"] == 267_867

    def test_monotonic_non_overlapping(self):
        t = proportional_timings(200_000, _SCRIPT)
        assert t[0]["start_ms"] == 0
        for a, b in zip(t, t[1:]):
            assert a["end_ms"] == b["start_ms"]
            assert a["end_ms"] > a["start_ms"]

    def test_longer_line_gets_more_time(self):
        t = proportional_timings(200_000, _SCRIPT)
        durs = [x["end_ms"] - x["start_ms"] for x in t]
        # line 2 (120 chars) is the longest → longest slice
        assert durs[2] == max(durs)

    def test_carries_speaker_and_text(self):
        t = proportional_timings(1000, _SCRIPT)
        assert t[0]["speaker"] == "A"
        assert t[0]["text"] == _SCRIPT[0]["text"]

    def test_zero_duration_is_empty(self):
        assert proportional_timings(0, _SCRIPT) == []

    def test_empty_script_is_empty(self):
        assert proportional_timings(200_000, []) == []

    def test_gate_flips_true(self):
        # has_slideshow = timings AND scenes AND any(images) — the exact gate.
        t = proportional_timings(200_000, _SCRIPT)
        scenes, images = [1, 1], ["a.svg", ""]
        assert bool(t and scenes and any(images)) is True


class TestSlideshowTimingsFallback:
    def test_uses_compute_timings_when_per_turn_audio_present(self, tmp_path):
        mod = _load_pipeline()
        # Per-turn audio present → compute_timings path; missing files fall back
        # to the 3000ms-per-segment default inside compute_timings.
        segs = [{"speaker": "A", "text": "hi", "audio": "/podcast/api/audio/seg_00.mp3"}]
        t = mod._slideshow_timings(tmp_path, segs, [{"speaker": "A", "text": "hi"}], "")
        assert len(t) == 1

    def test_empty_when_no_audio_at_all(self, tmp_path):
        mod = _load_pipeline()
        segs = [{"speaker": "A", "text": "hi", "audio": ""}]
        t = mod._slideshow_timings(tmp_path, segs, [{"speaker": "A", "text": "hi"}], "")
        assert t == []


# Caption (segment) timeline used for the scene-anchoring tests: 5 segments,
# uneven windows (like real proportional/exact timings), total 9000ms.
_TIMINGS = [
    {"start_ms": 0, "end_ms": 1000, "speaker": "A", "text": "a"},
    {"start_ms": 1000, "end_ms": 2500, "speaker": "B", "text": "b"},
    {"start_ms": 2500, "end_ms": 4000, "speaker": "A", "text": "c"},
    {"start_ms": 4000, "end_ms": 6000, "speaker": "B", "text": "d"},
    {"start_ms": 6000, "end_ms": 9000, "speaker": "A", "text": "e"},
]
_TOTAL = 9000
_BOUNDS = {t["start_ms"] for t in _TIMINGS} | {t["end_ms"] for t in _TIMINGS}


def _assert_tiles(windows, total):
    """Windows must contiguously tile [0, total] with no gaps/overlaps."""
    assert windows[0][0] == 0
    assert windows[-1][1] == total
    for (a, b), (c, d) in zip(windows, windows[1:]):
        assert b == c
        assert b >= a


class TestDistributeSceneWindows:
    def test_one_image_per_caption_aligns_to_boundaries(self):
        mod = _load_pipeline()
        w = mod._distribute_scene_windows(_TIMINGS, 5)
        assert len(w) == 5
        _assert_tiles(w, _TOTAL)
        for a, b in w:  # every boundary is a real caption boundary
            assert a in _BOUNDS and b in _BOUNDS

    def test_fewer_images_group_whole_captions(self):
        mod = _load_pipeline()
        w = mod._distribute_scene_windows(_TIMINGS, 3)  # n < m
        assert len(w) == 3
        _assert_tiles(w, _TOTAL)
        for a, b in w:
            assert a in _BOUNDS and b in _BOUNDS  # unions of whole captions

    def test_more_images_never_cross_a_caption_boundary(self):
        mod = _load_pipeline()
        w = mod._distribute_scene_windows(_TIMINGS, 10)  # n > m — the real 10-img/5-seg case
        assert len(w) == 10
        _assert_tiles(w, _TOTAL)
        # Every window must sit fully inside ONE caption (no straddling).
        for a, b in w:
            container = next(
                (t for t in _TIMINGS if t["start_ms"] <= a and b <= t["end_ms"]), None
            )
            assert container is not None, f"window ({a},{b}) straddles a caption boundary"

    def test_empty_timings_returns_empty(self):
        mod = _load_pipeline()
        assert mod._distribute_scene_windows([], 5) == []


class TestMergeScenesAnchored:
    def test_scenes_tile_and_align_to_captions(self):
        mod = _load_pipeline()
        # 10 article images over 5 captions — reproduces the desync report shape.
        article = [f"img_{k}.png" for k in range(10)]
        scenes, paths = mod._merge_scenes(article, [], [], _TIMINGS)
        assert len(scenes) == 10 and len(paths) == 10
        windows = [(s["start_ms"], s["end_ms"]) for s in scenes]
        _assert_tiles(windows, _TOTAL)
        for a, b in windows:  # no scene straddles a caption boundary
            assert any(t["start_ms"] <= a and b <= t["end_ms"] for t in _TIMINGS)

    def test_empty_timings_falls_back_without_crash(self):
        mod = _load_pipeline()
        article = ["img_0.png", "img_1.png"]
        scenes, paths = mod._merge_scenes(article, [], [], [])
        # Degraded (audio-only): still returns scenes, just even-sliced at 0.
        assert len(scenes) == 2 and len(paths) == 2
        assert all("start_ms" in s and "end_ms" in s for s in scenes)

    def test_article_graphs_lead_ai_images_fill(self):
        mod = _load_pipeline()
        # 3 meaningful diagrams + 2 AI filler images.
        article = ["d0.png", "d1.png", "d2.png"]
        ai_scenes = [{"summary": "abstract A"}, {"summary": "abstract B"}]
        ai_paths = ["ai0.png", "ai1.png"]
        scenes, paths = mod._merge_scenes(article, ai_scenes, ai_paths, _TIMINGS)
        sources = [s["source"] for s in scenes]
        # All article graphs come first, AI filler trails — no interleaving.
        assert sources == ["article", "article", "article", "ai", "ai"]
        assert paths[:3] == ["d0.png", "d1.png", "d2.png"]
        # Diagrams keep contain-fit (never cropped); AI stays full-bleed cover.
        assert all(s["fit"] == "contain" for s in scenes if s["source"] == "article")
        assert all(s["fit"] == "cover" for s in scenes if s["source"] == "ai")


def _script_json(n: int) -> str:
    return json.dumps({"segments": [{"speaker": "A", "text": f"line {i}"} for i in range(n)]})


class _FakeApp:
    """Minimal stand-in for the podcast app to drive _generate_script without a
    live model. Records the system prompts think() saw and replays canned JSON."""

    def __init__(self, mod, replies):
        self._replies = list(replies)
        self.systems = []
        self.warns = []
        # Bind the real bound-methods under test onto this fake self.
        self._continue_script = types.MethodType(mod._continue_script, self)
        self._generate_script = types.MethodType(mod._generate_script, self)

    async def think(self, msg, system=None, domain=None, temperature=None):
        self.systems.append(system or "")
        return self._replies.pop(0)

    def log_warn(self, m):
        self.warns.append(m)


def _prime_consts(mod):
    # _consts() does `from .app import ...`, which fails when pipeline.py is
    # loaded standalone. Pre-seed its cache so it skips the relative import.
    mod._app_consts = {
        "DURATION_PRESETS": {},
        "LANGUAGE_INSTRUCTIONS": {"en": "Speak English."},
        "SCRIPT_SYSTEM": "words={words} segments={segments} {language_instruction}",
        "VOICE_PAIRS": {},
    }


class TestScriptCoverage:
    def test_scales_segments_up_and_caps_length(self):
        mod = _load_pipeline()
        _prime_consts(mod)
        long_ctx = "word " * 6000  # long article → scaled to the ceiling
        app = _FakeApp(mod, [_script_json(40)])  # model over-delivers 40
        script = asyncio.run(app._generate_script("Topic", context=long_ctx, segments=6, words=60))
        assert len(script) == 24  # hard-capped at MAX_AUTO_SEGMENTS (no 10-min episodes)
        assert len(app.systems) == 1  # one think call (no continuation)
        assert "segments=24" in app.systems[0]  # target scaled up to the cap

    def test_under_delivery_triggers_continuation(self):
        mod = _load_pipeline()
        _prime_consts(mod)
        ctx = "word " * 100  # segments stays 10; threshold = 6
        app = _FakeApp(mod, [_script_json(3), _script_json(5)])  # 3 < 6 → continue
        script = asyncio.run(app._generate_script("Topic", context=ctx, segments=10, words=60))
        assert len(script) == 8  # 3 + 5 appended
        assert len(app.systems) == 2  # first pass + one continuation
        assert app.warns == []

    def test_full_delivery_skips_continuation(self):
        mod = _load_pipeline()
        _prime_consts(mod)
        ctx = "word " * 100
        app = _FakeApp(mod, [_script_json(10)])  # 10 >= threshold → done
        script = asyncio.run(app._generate_script("Topic", context=ctx, segments=10, words=60))
        assert len(script) == 10
        assert len(app.systems) == 1  # no continuation


class TestNormalizeScript:
    def test_fixes_mistyped_speaker_key(self):
        mod = _load_pipeline()
        # The real crash: model typoed "speaker" -> "seller" and killed the run.
        segs = [
            {"speaker": "A", "text": "one"},
            {"seller": "A", "text": "two"},          # typo → recovered, not dropped
            {"speaker": "B", "text": "three", "sidebar_text": "x"},  # extra key stripped
        ]
        out = mod._normalize_script(segs)
        assert len(out) == 3
        assert all(set(s.keys()) == {"speaker", "text"} for s in out)
        assert all(s["speaker"] in ("A", "B") for s in out)

    def test_missing_speaker_alternates(self):
        mod = _load_pipeline()
        segs = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
        out = mod._normalize_script(segs)
        assert [s["speaker"] for s in out] == ["A", "B", "A"]

    def test_start_idx_continues_cadence(self):
        mod = _load_pipeline()
        # First pass ended on index 3 (→ next should be "B").
        out = mod._normalize_script([{"text": "x"}], start_idx=3)
        assert out[0]["speaker"] == "B"

    def test_drops_textless_and_nondict(self):
        mod = _load_pipeline()
        segs = [{"speaker": "A", "text": ""}, "junk", {"speaker": "B", "text": "keep"}]
        out = mod._normalize_script(segs)
        assert out == [{"speaker": "B", "text": "keep"}]

    def test_maps_host_and_numeric_labels(self):
        mod = _load_pipeline()
        out = mod._normalize_script([
            {"speaker": "Host 1", "text": "a"},
            {"speaker": "2", "text": "b"},
            {"role": "Speaker A", "text": "c"},
        ])
        assert [s["speaker"] for s in out] == ["A", "B", "A"]
