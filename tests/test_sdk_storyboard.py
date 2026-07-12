"""Tests for emptyos.sdk.storyboard — the frame-by-frame plan model.

Pure SDK tests; no daemon. Covers JSON round-trip, ``.progress`` / ``.is_complete``
across frame status transitions, and the ``from_scenes`` / ``as_render_triple``
convenience pair that lets a consumer keep its loose ``(results, stills,
durations)`` render helpers unchanged.
"""

from __future__ import annotations

import json

from emptyos.sdk.storyboard import (
    CLIPPED,
    PLANNED,
    RENDERED,
    Storyboard,
    StoryboardFrame,
)


def _sample() -> Storyboard:
    return Storyboard.from_scenes(
        scenes=[
            {"scene": 1, "description": "a lighthouse at dusk", "mood": "wistful", "prompt": "p1"},
            {"scene": 2, "description": "waves on black rock", "mood": "tense", "no_character": True},
            {"scene": 3, "description": "dawn over the harbour", "mood": "hopeful"},
        ],
        durations=[4.0, 6.5, 5.0],
        song="Tides",
        mode="parallax",
        style="film",
        audio_path="/v/song/audio.mp3",
        srt_path="/v/song/subs.srt",
        audio_duration=15.5,
    )


def test_from_scenes_builds_planned_frames():
    sb = _sample()
    assert len(sb.frames) == 3
    assert [f.index for f in sb.frames] == [0, 1, 2]
    assert sb.frames[0].description == "a lighthouse at dusk"
    assert sb.frames[0].image_prompt == "p1"
    assert sb.frames[1].duration == 6.5
    # opaque scene payload rides verbatim on the frame
    assert sb.frames[1].scene["no_character"] is True
    assert all(f.status == PLANNED for f in sb.frames)


def test_progress_and_is_complete_track_clipped_status():
    sb = _sample()
    assert sb.progress == 0.0
    assert sb.is_complete is False

    sb.frames[0].status = RENDERED  # still ready, not yet clipped
    assert sb.progress == 0.0

    sb.frames[0].status = CLIPPED
    assert sb.progress == 1 / 3
    assert sb.is_complete is False

    for f in sb.frames:
        f.status = CLIPPED
    assert sb.progress == 1.0
    assert sb.is_complete is True


def test_empty_storyboard_progress_is_zero_not_complete():
    sb = Storyboard()
    assert sb.progress == 0.0
    assert sb.is_complete is False


def test_json_round_trip_is_lossless():
    sb = _sample()
    sb.frames[0].still_path = "/v/song/mv-x/scene-01.png"
    sb.frames[0].status = RENDERED
    sb.frames[2].clip_path = "/v/song/mv-x/clip-03.mp4"
    sb.frames[2].status = CLIPPED

    blob = json.dumps(sb.to_dict())  # must be json-serialisable
    back = Storyboard.from_dict(json.loads(blob))

    assert back.to_dict() == sb.to_dict()
    assert back.song == "Tides"
    assert back.frames[0].still_path == "/v/song/mv-x/scene-01.png"
    assert back.frames[2].status == CLIPPED
    assert back.frames[1].scene["no_character"] is True


def test_from_dict_tolerates_missing_and_messy_fields():
    sb = Storyboard.from_dict(
        {"song": "X", "frames": [{"index": 0}, {"index": 1, "duration": None, "scene": None}]}
    )
    assert sb.frames[0].description == ""
    assert sb.frames[0].status == PLANNED
    assert sb.frames[1].duration == 0.0
    assert sb.frames[1].scene == {}


def test_as_render_triple_returns_legacy_shape():
    sb = _sample()
    sb.frames[0].still_path = "/a/scene-01.png"
    sb.frames[1].still_path = "/a/scene-02.png"
    scenes, stills, durations = sb.as_render_triple()

    assert scenes[0]["description"] == "a lighthouse at dusk"
    assert scenes[1]["no_character"] is True  # full scene dict, not just description
    assert stills == ["/a/scene-01.png", "/a/scene-02.png", ""]
    assert durations == [4.0, 6.5, 5.0]


def test_frame_from_dict_round_trip():
    f = StoryboardFrame(index=2, description="d", duration=3.0, status=CLIPPED, scene={"k": "v"})
    assert StoryboardFrame.from_dict(f.to_dict()) == f
