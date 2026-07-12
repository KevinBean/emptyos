"""Tests for emptyos.sdk.frame — HTML → PNG frame rendering (Pixelle borrow #2).

The ``caption_card_html`` template tests are pure (no browser). The render tests
are gated on a working Playwright Chromium and skipped otherwise; when present
they assert a real PNG of the exact requested dimensions is produced (one browser
for the batch).
"""

from __future__ import annotations

import struct

import pytest

from emptyos.sdk.frame import caption_card_html, render_html_frame, render_html_frames


def _png_size(path) -> tuple[int, int]:
    """(width, height) from a PNG's IHDR — avoids a Pillow dependency."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    # IHDR width/height are big-endian uint32 at byte offsets 16 and 20.
    width, height = struct.unpack(">II", data[16:24])
    return width, height


# --- pure template tests -------------------------------------------------


def test_caption_card_contains_escaped_text():
    html = caption_card_html("Tide & <Time>", subtitle="Verse 1")
    assert "Tide &amp; &lt;Time&gt;" in html  # HTML-escaped, not raw
    assert "Verse 1" in html
    assert "<!doctype html>" in html.lower()


def test_caption_card_omits_subtitle_div_when_empty():
    assert 'class="sub"' not in caption_card_html("Solo line")
    assert 'class="sub"' in caption_card_html("Line", subtitle="under")


def test_caption_card_theme_and_size_flow_through():
    light = caption_card_html("x", theme="light", width=1080, height=1920)
    assert "#f6f7f9" in light          # light bg
    assert "width:1080px" in light
    assert "height:1920px" in light
    dark = caption_card_html("x")       # default dark
    assert "#0b0d10" in dark


# --- render tests (gated on Chromium) ------------------------------------


@pytest.fixture(scope="module")
def _chromium():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Playwright Chromium unavailable: {e}")


def test_render_single_frame_has_requested_dimensions(_chromium, tmp_path):
    out = tmp_path / "card.png"
    html = caption_card_html("Hello frame", width=640, height=360)
    res = render_html_frame(html, out, width=640, height=360)
    assert res == out and out.is_file()
    assert out.stat().st_size > 500
    assert _png_size(out) == (640, 360)


def test_render_batch_uses_one_browser_for_many_frames(_chromium, tmp_path):
    items = [
        (caption_card_html(f"scene {n}", width=480, height=270), tmp_path / f"f{n}.png")
        for n in range(3)
    ]
    paths = render_html_frames(items, width=480, height=270)
    assert len(paths) == 3
    for p in paths:
        assert p.is_file()
        assert _png_size(p) == (480, 270)
