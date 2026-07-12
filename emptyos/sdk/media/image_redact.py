"""Screenshot redaction — blur private regions + crop away chrome.

The image sibling of ``review.py`` / ``normalize.py``: a pure, kernel-free media
helper for turning a raw phone/desktop screenshot into a *publishable* one. Two
lossless-intent operations, composed in one pass:

- **blur** one or more rectangles (pixelate + Gaussian soften) so a value is
  unreadable but its *shape* — a number, a chart legend, a chat bubble — is
  still legibly "there". This is the propose-not-hide posture: the figure still
  demonstrates the mechanism, only the private payload is gone.
- **crop** a single rectangle to drop the parts that carry no argument — the iOS
  keyboard, the predictive-paste bar, empty page margins.

Order is fixed: **blur first, then crop**, and every box is given in the
*original* image's coordinates. So you can eyeball regions against the full
screenshot once and the crop never shifts them.

Boxes are ``(x0, y0, x1, y1)``. Each value is read as a **fraction** of the
image dimension when ``<= 1.0`` and as an absolute **pixel** otherwise, so
``(0.05, 0.15, 0.98, 0.21)`` and ``(64, 420, 1264, 588)`` both work (don't mix
the two conventions within one box). Out-of-range values are clamped.

Pure Pillow (an optional dep — the ``desktop``/``devices`` extras pull it in),
lazy-imported with an actionable error, so this module imports without Pillow
present and unit-tests without a daemon.

Built directly in ``sdk/media/`` on its first consumer — ahead of the usual
2-consumer extraction rule (CLAUDE.md rule 9) — because "redact a screenshot for
publishing" is obviously cross-cutting (every future post with screenshots needs
it) and it is a pure utility with no app-specific logic. Same rationale as
``sdk/pdf.py``, and it sits naturally beside the other ``sdk/media/`` primitives.
Distinct from the ``eos-screenshot`` skill, which blurs *DOM selectors on a live
capture* (browser-side CSS ``filter: blur``); this operates on *pixels of an
existing raster*, so there is nothing to share between them.

First consumer: redacting the Telegram/expense phone screenshots for the
"Chat App as Pocket Console" post (``30_Resources/Published``). CLI wrapper:
``scripts/redact_screenshot.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

Box = Sequence[float]  # (x0, y0, x1, y1), each fraction (<=1) or pixel (>1)


def _load_pil():
    """Import Pillow lazily with an install hint (Pillow is an optional extra)."""
    try:
        from PIL import Image, ImageFilter  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - exercised only without Pillow
        raise RuntimeError(
            "image redaction needs Pillow — install it with:  pip install 'Pillow>=10'"
        ) from e
    return Image, ImageFilter


def resolve_box(box: Box, w: int, h: int) -> tuple[int, int, int, int]:
    """Turn a fraction/pixel box into clamped integer pixel bounds (x0,y0,x1,y1)."""
    if box is None or len(box) != 4:
        raise ValueError(f"box must be (x0, y0, x1, y1), got {box!r}")
    x0, y0, x1, y1 = box

    def px(v: float, span: int) -> int:
        return int(round(v * span)) if abs(v) <= 1.0 else int(round(v))

    bx0, bx1 = sorted((px(x0, w), px(x1, w)))
    by0, by1 = sorted((px(y0, h), px(y1, h)))
    bx0, bx1 = max(0, bx0), min(w, bx1)
    by0, by1 = max(0, by0), min(h, by1)
    if bx1 <= bx0 or by1 <= by0:
        raise ValueError(f"box {box!r} resolves to an empty region on {w}x{h}")
    return bx0, by0, bx1, by1


def redact_image(
    src: str | Path,
    dst: str | Path,
    *,
    blur: Sequence[Box] | None = None,
    crop: Box | None = None,
    pixel_block: int = 24,
    gaussian: float = 8.0,
) -> tuple[int, int]:
    """Blur ``blur`` regions then ``crop``; write to ``dst``. Returns (w, h) written.

    - ``blur``    — regions to redact, in the ORIGINAL image's coordinates.
    - ``crop``    — final keep-region, also in ORIGINAL coordinates.
    - ``pixel_block`` — pixelation coarseness (region is downscaled by this
      divisor before a NEAREST upscale). Higher = blockier = more redacted.
    - ``gaussian`` — softening radius applied over the pixelation.
    """
    Image, ImageFilter = _load_pil()
    im = Image.open(src).convert("RGB")
    w, h = im.size

    for box in blur or []:
        bx0, by0, bx1, by1 = resolve_box(box, w, h)
        region = im.crop((bx0, by0, bx1, by1))
        rw, rh = region.size
        small = region.resize(
            (max(1, rw // pixel_block), max(1, rh // pixel_block)), Image.BILINEAR
        )
        region = small.resize((rw, rh), Image.NEAREST)
        if gaussian > 0:
            region = region.filter(ImageFilter.GaussianBlur(gaussian))
        im.paste(region, (bx0, by0, bx1, by1))

    if crop is not None:
        im = im.crop(resolve_box(crop, w, h))

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst)
    return im.size
