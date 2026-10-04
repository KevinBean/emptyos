"""Unit tests for the HTML→MP4 recorder (emptyos.sdk.media.html_record).

Pure control-flow tests — no real browser, no ffmpeg. A fake playwright plugin
records the navigate/eval/screenshot/close calls and writes a stub frame file on
each screenshot; `frames_to_mp4` is monkeypatched so the test runs anywhere.
The genuine browser + ffmpeg path is exercised live against the daemon.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from emptyos.sdk.media import html_record


class FakePlugin:
    """Minimal stand-in for the playwright plugin's public surface.

    The recorder steps animations purely via the WAAPI seek (a per-frame
    ``getAnimations().currentTime`` eval); ``self.seeks`` records the seeked
    times parsed from those evals.
    """

    def __init__(self, *, available=True, duration_value=0.0,
                 scene_duration_value=0.0):
        self._available = available
        self._duration_value = duration_value
        self._scene_duration_value = scene_duration_value
        self.calls = []
        self.screenshots = []
        self.closed = []
        self.seeks = []

    async def available(self):
        return self._available

    async def navigate(self, url, *, wait="load", context_id=None):
        self.calls.append(("navigate", url, wait, context_id))
        return {"url": url}

    async def eval(self, expression, *, context_id=None):
        self.calls.append(("eval", expression, context_id))
        if "SCENE_DURATION_MS" in expression:  # explicit duration hint probe
            return {"value": self._scene_duration_value}
        if "getComputedTiming" in expression:  # the WAAPI duration-inference probe
            return {"value": self._duration_value}
        if "currentTime=" in expression:       # a per-frame WAAPI seek
            m = re.search(r"currentTime=([0-9.]+)", expression)
            if m:
                self.seeks.append(float(m.group(1)))
        return {"value": None}

    async def screenshot(self, *, path=None, context_id=None, **kw):
        Path(path).write_bytes(b"\x89PNG\r\n")  # stub frame
        self.screenshots.append(path)
        return {"path": path}

    async def close_context(self, context_id=None):
        self.closed.append(context_id)
        return {"ok": True}


def test_seek_expr_pauses_and_seeks():
    expr = html_record._seek_expr(500.0)
    assert "pause()" in expr
    assert "currentTime=500.0" in expr
    assert "getAnimations()" in expr


@pytest.mark.asyncio
async def test_none_plugin_returns_error():
    res = await html_record.record_html_to_mp4(None, "x.html", "out.mp4")
    assert res["ok"] is False
    assert "not available" in res["error"]


@pytest.mark.asyncio
async def test_unavailable_plugin_returns_error(tmp_path):
    res = await html_record.record_html_to_mp4(
        FakePlugin(available=False), tmp_path / "x.html", tmp_path / "o.mp4"
    )
    assert res["ok"] is False
    assert "not installed" in res["error"]


@pytest.mark.asyncio
async def test_infer_duration_parses_value():
    p = FakePlugin(duration_value=4.5)
    assert await html_record.infer_duration_s(p, "c") == 4.5


@pytest.mark.asyncio
async def test_infer_duration_prefers_scene_hint():
    # Explicit window.SCENE_DURATION_MS wins over the WAAPI probe — the only
    # signal a JS-driven (Anime.js / rAF) page can offer. The fake returns the
    # JS expression's already-computed seconds value (the real expr divides ms
    # by 1000), matching how duration_value models the WAAPI probe.
    p = FakePlugin(scene_duration_value=8.0, duration_value=2.0)
    assert await html_record.infer_duration_s(p, "c") == 8.0


@pytest.mark.asyncio
async def test_infer_duration_zero_on_error():
    class Boom(FakePlugin):
        async def eval(self, expression, *, context_id=None):
            raise RuntimeError("no page")

    assert await html_record.infer_duration_s(Boom(), "c") == 0.0


@pytest.mark.asyncio
async def test_record_captures_expected_frame_count(tmp_path, monkeypatch):
    captured = {}

    async def fake_frames_to_mp4(pattern, out, *, fps=24, **kw):
        captured["pattern"] = pattern
        captured["fps"] = fps
        Path(out).write_bytes(b"\x00\x00\x00\x18ftypmp4")  # stub mp4
        return True

    monkeypatch.setattr(html_record, "frames_to_mp4", fake_frames_to_mp4)

    src = tmp_path / "scene.html"
    src.write_text("<html><body><div>hi</div></body></html>", encoding="utf-8")
    out = tmp_path / "scene.mp4"

    p = FakePlugin()
    res = await html_record.record_html_to_mp4(
        p, src, out, fps=10, duration_s=2.0, settle_ms=0, keep_frames=False
    )

    assert res["ok"] is True
    assert res["frames"] == 20            # 10 fps * 2.0 s
    assert res["fps"] == 10
    assert res["path"] == str(out)
    assert len(p.screenshots) == 20       # one screenshot per frame
    assert captured["fps"] == 10
    assert "f_%05d.png" in captured["pattern"]
    assert p.closed == [f"htmlrec-{abs(hash(str(src))) % 10**8}"]  # context cleaned up


@pytest.mark.asyncio
async def test_record_seeks_waapi_each_frame(tmp_path, monkeypatch):
    """The recorder steps via the WAAPI seek (getAnimations().currentTime) once
    per frame, monotonically from 0 — the only stepping mechanism after the
    non-working virtual-clock path was removed (2026-06-20). navigate is the
    first browser call; no clock methods are touched."""
    async def fake_frames_to_mp4(pattern, out, *, fps=24, **kw):
        Path(out).write_bytes(b"\x00\x00\x00\x18ftypmp4")
        return True

    monkeypatch.setattr(html_record, "frames_to_mp4", fake_frames_to_mp4)
    src = tmp_path / "scene.html"
    src.write_text("<html><body><div>hi</div></body></html>", encoding="utf-8")

    p = FakePlugin()
    res = await html_record.record_html_to_mp4(
        p, src, tmp_path / "scene.mp4", fps=10, duration_s=1.0, settle_ms=0
    )

    assert res["ok"] is True
    assert res["frames"] == 10
    verbs = [c[0] for c in p.calls]
    assert verbs[0] == "navigate"          # nothing precedes navigate
    assert "clock_install" not in verbs    # the removed clock path is gone
    assert "clock_pause_at" not in verbs
    # one WAAPI seek per frame, strictly increasing, starting at 0.
    assert len(p.seeks) == 10
    assert p.seeks[0] == 0.0
    assert p.seeks == sorted(p.seeks)
    assert p.seeks[-1] == pytest.approx(900.0)  # (9/10)*1000


@pytest.mark.asyncio
async def test_record_clamps_to_max_duration(tmp_path, monkeypatch):
    async def fake_frames_to_mp4(pattern, out, *, fps=24, **kw):
        Path(out).write_bytes(b"x")
        return True

    monkeypatch.setattr(html_record, "frames_to_mp4", fake_frames_to_mp4)
    src = tmp_path / "s.html"
    src.write_text("<html></html>", encoding="utf-8")

    res = await html_record.record_html_to_mp4(
        FakePlugin(), src, tmp_path / "o.mp4",
        fps=5, duration_s=999, max_duration_s=3, settle_ms=0,
    )
    assert res["ok"] is True
    assert res["duration_s"] == 3.0
    assert res["frames"] == 15            # 5 fps * 3 s (clamped)


@pytest.mark.asyncio
async def test_record_falls_back_to_default_duration(tmp_path, monkeypatch):
    async def fake_frames_to_mp4(pattern, out, *, fps=24, **kw):
        Path(out).write_bytes(b"x")
        return True

    monkeypatch.setattr(html_record, "frames_to_mp4", fake_frames_to_mp4)
    src = tmp_path / "s.html"
    src.write_text("<html></html>", encoding="utf-8")

    # duration omitted + page reports 0 → 6.0s default
    res = await html_record.record_html_to_mp4(
        FakePlugin(duration_value=0.0), src, tmp_path / "o.mp4",
        fps=1, settle_ms=0,
    )
    assert res["ok"] is True
    assert res["duration_s"] == 6.0
    assert res["frames"] == 6


@pytest.mark.asyncio
async def test_frames_to_mp4_failure_propagates(tmp_path, monkeypatch):
    async def fake_frames_to_mp4(pattern, out, *, fps=24, **kw):
        return False  # ffmpeg missing / failed

    monkeypatch.setattr(html_record, "frames_to_mp4", fake_frames_to_mp4)
    src = tmp_path / "s.html"
    src.write_text("<html></html>", encoding="utf-8")

    res = await html_record.record_html_to_mp4(
        FakePlugin(), src, tmp_path / "o.mp4", fps=2, duration_s=1, settle_ms=0
    )
    assert res["ok"] is False
    assert "ffmpeg" in res["error"]
