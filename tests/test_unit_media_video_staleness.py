"""A failed frame extraction must not report success holding an old frame.

`extract_last_frame` is the handoff primitive for chained image-to-video: clip
N's last frame becomes clip N+1's start image. Callers write to a deterministic
per-scene name, so when both ffmpeg invocations fail and a file from an earlier
attempt is still sitting at that path, `out.exists()` is True and the function
returns success — handing the next generation a plate from a *different* shot.
The failure then surfaces as a scene that silently continues the wrong thing,
which is `.claude/rules/dev-gotchas.md`'s "a media path is not a media version".

These tests stub the ffmpeg runner, so they need no ffmpeg and no GPU.
"""

from __future__ import annotations

import asyncio

import pytest

from emptyos.sdk.media import video as V


@pytest.fixture
def failing_ffmpeg(monkeypatch):
    """Every ffmpeg invocation fails and writes nothing."""
    calls: list[list[str]] = []

    async def _fail(cmd, *a, **k):
        calls.append(cmd)
        return 1, "boom"

    monkeypatch.setattr(V, "_run", _fail)
    return calls


def test_last_frame_failure_does_not_pass_off_a_previous_attempt(tmp_path, failing_ffmpeg):
    out = tmp_path / "_atmosphere-plate-02.png"
    out.write_bytes(b"frame from an earlier generation")

    ok = asyncio.run(V.extract_last_frame(tmp_path / "clip.mp4", out))

    assert ok is False, "reported success for a frame this call never produced"
    assert not out.exists(), (
        "the stale plate survived — the next clip in the chain would be "
        "conditioned on a frame from a different shot"
    )


def test_thumbnail_failure_does_not_pass_off_a_previous_attempt(tmp_path, failing_ffmpeg):
    out = tmp_path / "thumb.jpg"
    out.write_bytes(b"thumbnail of something else")

    assert asyncio.run(V.extract_thumbnail(tmp_path / "clip.mp4", out)) is False
    assert not out.exists()


def test_success_is_still_reported_when_ffmpeg_writes_a_frame(tmp_path, monkeypatch):
    """The guard must not turn a working extraction into a failure."""
    out = tmp_path / "plate.png"

    async def _writes(cmd, *a, **k):
        out.write_bytes(b"a genuinely new frame")
        return 0, ""

    monkeypatch.setattr(V, "_run", _writes)

    assert asyncio.run(V.extract_last_frame(tmp_path / "clip.mp4", out)) is True
    assert out.read_bytes() == b"a genuinely new frame"
