"""Unit tests for emptyos.sdk.svg_raster — pure parts (no Playwright, no daemon)."""
import os
import time

import pytest

from emptyos.sdk.svg_raster import stale_svg_pairs, svg_size

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 860 520"></svg>'


class TestSvgSize:
    def test_viewbox(self):
        assert svg_size(SVG) == (860, 520)

    def test_viewbox_preferred_over_width_height(self):
        s = '<svg width="100" height="50" viewBox="0 0 860 520"></svg>'
        assert svg_size(s) == (860, 520)

    def test_width_height_fallback(self):
        assert svg_size('<svg width="320" height="240"></svg>') == (320, 240)

    def test_no_dimensions_raises(self):
        with pytest.raises(ValueError):
            svg_size("<svg></svg>")


class TestStaleSvgPairs:
    def test_missing_root_returns_empty(self, tmp_path):
        assert stale_svg_pairs(tmp_path / "nope") == []

    def test_missing_png_is_stale(self, tmp_path):
        svg = tmp_path / "a.svg"
        svg.write_text(SVG)
        assert stale_svg_pairs(tmp_path) == [svg]

    def test_fresh_png_not_stale(self, tmp_path):
        svg = tmp_path / "a.svg"
        svg.write_text(SVG)
        png = tmp_path / "a.png"
        png.write_bytes(b"png")
        now = time.time()
        os.utime(svg, (now - 100, now - 100))
        os.utime(png, (now, now))
        assert stale_svg_pairs(tmp_path) == []

    def test_older_png_is_stale(self, tmp_path):
        svg = tmp_path / "a.svg"
        svg.write_text(SVG)
        png = tmp_path / "a.png"
        png.write_bytes(b"png")
        now = time.time()
        os.utime(png, (now - 100, now - 100))
        os.utime(svg, (now, now))
        assert stale_svg_pairs(tmp_path) == [svg]

    def test_recursive_and_sorted(self, tmp_path):
        (tmp_path / "sub").mkdir()
        b = tmp_path / "sub" / "b.svg"
        a = tmp_path / "a.svg"
        b.write_text(SVG)
        a.write_text(SVG)
        assert stale_svg_pairs(tmp_path) == sorted([a, b])

    def test_unpaired_png_ignored(self, tmp_path):
        (tmp_path / "loose.png").write_bytes(b"png")
        assert stale_svg_pairs(tmp_path) == []
