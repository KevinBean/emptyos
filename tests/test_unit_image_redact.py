"""Unit tests for emptyos.sdk.media.image_redact — pure, no daemon, no network."""

from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from emptyos.sdk.media.image_redact import redact_image, resolve_box  # noqa: E402


def test_resolve_box_fraction_and_pixel():
    # fractions (<=1) map by dimension; pixels (>1) pass through; result is clamped + ordered
    assert resolve_box((0.0, 0.0, 0.5, 0.5), 200, 100) == (0, 0, 100, 50)
    assert resolve_box((10, 20, 190, 80), 200, 100) == (10, 20, 190, 80)
    assert resolve_box((0.5, 0.0, 0.0, 0.5), 200, 100) == (0, 0, 100, 50)  # unordered ok
    assert resolve_box((-5, -5, 5000, 5000), 200, 100) == (0, 0, 200, 100)  # clamped


def test_resolve_box_empty_region_raises():
    with pytest.raises(ValueError):
        resolve_box((0.5, 0.5, 0.5, 0.9), 200, 100)  # zero width


def _checkerboard(w, h):
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (255, 0, 0) if (x // 4 + y // 4) % 2 else (0, 0, 255)
    return im


def test_redact_crops_and_blurs(tmp_path):
    src = tmp_path / "in.png"
    dst = tmp_path / "out.png"
    _checkerboard(120, 240).save(src)

    # blur the top-left quarter (original coords), then crop to the top half
    w, h = redact_image(src, dst, blur=[(0.0, 0.0, 0.5, 0.25)], crop=(0, 0, 1.0, 0.5))
    assert (w, h) == (120, 120)  # crop to top half of a 240-tall image

    out = Image.open(dst).convert("RGB")
    # a blurred pixel is no longer a pure checkerboard color
    assert out.getpixel((30, 30)) not in {(255, 0, 0), (0, 0, 255)}
    # a pixel in the kept crop but outside the blur box is untouched
    ref = _checkerboard(120, 240).convert("RGB")
    assert out.getpixel((90, 90)) == ref.getpixel((90, 90))


def test_redact_blur_only_preserves_size(tmp_path):
    src = tmp_path / "in.png"
    dst = tmp_path / "out.png"
    _checkerboard(80, 80).save(src)
    w, h = redact_image(src, dst, blur=[(0.1, 0.1, 0.4, 0.4)])
    assert (w, h) == (80, 80)  # no crop → same dimensions
