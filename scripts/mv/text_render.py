"""PIL text rendering for MV overlays — cards and PLACEHOLDER badges.

Every piece of text in an MV is drawn here as a PNG and composited by ffmpeg
``overlay``. ffmpeg's ``drawtext`` crashed on fontconfig during the
〈說得太急〉 rescue, and PIL output is identical on every machine, which is what
lets a re-render be compared byte for byte. Pillow is imported lazily so importing this
module (and ``living_master``) costs nothing on a box without it.

Fonts are passed in as paths; the CLIs resolve them through ``mv_config``
(``font.zh_sans`` …). ``font_path=None`` falls back to Pillow's built-in font,
which renders ASCII only — fine for tests, wrong for Chinese titles.
"""
from __future__ import annotations

from pathlib import Path

BG = (17, 24, 32)
FG = (231, 229, 220)
MUTED = (166, 176, 184)
ACCENT = (231, 191, 124)
BADGE_BG = (190, 40, 40, 235)


def load_font(font_path: str | Path | None, size: int):
    from PIL import ImageFont

    if font_path:
        return ImageFont.truetype(str(font_path), size)
    return ImageFont.load_default(size)


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """Wrap by character so CJK (no spaces) wraps as well as English."""
    lines: list[str] = []
    for para in str(text).splitlines() or [""]:
        line = ""
        for ch in para:
            trial = line + ch
            if line and draw.textlength(trial, font=font) > max_width:
                lines.append(line)
                line = ch.lstrip()
            else:
                line = trial
        lines.append(line)
    return lines


def render_card(
    out: Path,
    *,
    width: int,
    height: int,
    stable_id: str,
    title: str,
    note: str = "",
    timecode: str = "",
    font_path: str | Path | None = None,
) -> Path:
    """A storyboard card for one living-master slot: what the shot is for.

    The card itself says PLACEHOLDER, so a card is recognisable even in a frame
    grab that lost the burned-in badge.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    unit = max(12, height // 24)
    small, body, big = (load_font(font_path, s) for s in (unit, int(unit * 1.3), int(unit * 2)))
    margin = unit * 2
    draw.text((margin, margin), f"{stable_id}   {timecode}".strip(), font=small, fill=MUTED)
    draw.text((width - margin, margin), "PLACEHOLDER", font=small, fill=ACCENT, anchor="ra")
    y = margin + unit * 3
    for line in _wrap(draw, title, big, width - 2 * margin):
        draw.text((margin, y), line, font=big, fill=FG)
        y += int(unit * 2.6)
    y += unit
    for line in _wrap(draw, note, body, width - 2 * margin):
        draw.text((margin, y), line, font=body, fill=MUTED)
        y += int(unit * 1.8)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def render_badge(
    out: Path,
    text: str,
    *,
    frame_height: int,
    font_path: str | Path | None = None,
) -> Path:
    """A transparent PNG badge sized relative to the frame, for ffmpeg overlay."""
    from PIL import Image, ImageDraw

    size = max(12, frame_height // 30)
    font = load_font(font_path, size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    pad = size // 2
    img = Image.new("RGBA", (right - left + 2 * pad, bottom - top + 2 * pad), BADGE_BG)
    ImageDraw.Draw(img).text((pad - left, pad - top), text, font=font, fill=(255, 255, 255, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


# ── Finished-film text (lifted from the 〈說得太急〉 rescue-v18) ────────────
# Four layers, each typeset differently so no two are confused on screen:
# in-picture cards (vertical serif Chinese in the shot's own dark space), the
# title card, lyric subtitles (horizontal, in the letterbox bar) and a small
# watermark. All are full-frame RGBA PNGs for ffmpeg ``overlay``.

def _variable(font_path, size, variation: str | None):
    font = load_font(font_path, size)
    if variation:
        try:
            font.set_variation_by_name(variation)
        except Exception:   # not a variable font, or no such instance
            pass
    return font


def render_vertical_card(
    out: Path, columns: list[str], english: str, *, cx: int, top: int, align: str,
    width: int = 1920, height: int = 1080, size: int = 50,
    zh_font=None, zh_variation: str | None = "Light", en_font=None,
    en_size: int = 24, en_measure: int = 260,
) -> Path:
    """Vertical Chinese columns read right to left, English hanging beneath.

    ``cx``/``top`` place the block centre and top; ``align`` (left/right/center)
    sets which edge the English line keeps.
    """
    from PIL import Image, ImageDraw, ImageFilter

    zf = _variable(zh_font, size, zh_variation)
    en = load_font(en_font, en_size)
    step = int(zf.size * 1.32)             # vertical advance per character
    gap = int(zf.size * 1.7)               # column pitch
    n = len(columns)
    block_w = gap * (n - 1) + zf.size
    left = cx - block_w // 2
    glyphs = []                            # right-most column first (traditional reading order)
    for k, col in enumerate(columns):
        x = left + (n - 1 - k) * gap
        for j, ch in enumerate(col):
            glyphs.append((x, top + j * step, ch))
    bottom = top + max(len(c) for c in columns) * step
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # English wraps to a narrow measure so it stays in the same negative space as the column
    lines = [""]
    for w in english.split():
        trial = (lines[-1] + " " + w).strip()
        if lines[-1] and align != "center" and d.textlength(trial, font=en) > en_measure:
            lines.append(w)
        else:
            lines[-1] = trial
    en_rows = []
    for k, ln in enumerate(lines):
        ew = d.textlength(ln, font=en)
        ex = {"left": left, "right": left + block_w - ew, "center": cx - ew / 2}[align]
        en_rows.append((ex, bottom + 24 + k * 32, ln))
    sh = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for x, y, ch in glyphs:
        sd.text((x, y + 2), ch, font=zf, fill=(0, 0, 0, 210))
    for ex, ey, ln in en_rows:
        sd.text((ex, ey + 2), ln, font=en, fill=(0, 0, 0, 210))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(7)))
    d = ImageDraw.Draw(img)
    for x, y, ch in glyphs:
        d.text((x, y), ch, font=zf, fill=(242, 236, 222, 240))
    for ex, ey, ln in en_rows:
        d.text((ex, ey), ln, font=en, fill=(222, 214, 198, 205))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _spaced(d, xy_center, text, font, spacing, fill, stroke=0, stroke_fill=None):
    widths = [d.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = xy_center[0] - total / 2
    for ch, w in zip(text, widths):
        d.text((x, xy_center[1]), ch, font=font, fill=fill, anchor="lm",
               stroke_width=stroke, stroke_fill=stroke_fill)
        x += w + spacing


def render_title(
    out: Path, zh: str, en: str, *, width: int = 1920, height: int = 1080,
    font=None, zh_size: int | None = None, en_size: int | None = None,
    zh_y: int | None = None, en_y: int | None = None,
) -> Path:
    """Centred, letter-spaced title over its English name, with a soft shadow.

    Sizes and positions default to the 1080p house style (118/46 px at y 488/598)
    scaled to the frame height.
    """
    from PIL import Image, ImageDraw, ImageFilter

    k = height / 1080
    zh_size, en_size = zh_size or round(118 * k), en_size or round(46 * k)
    zh_y, en_y = zh_y if zh_y is not None else round(488 * k), en_y if en_y is not None else round(598 * k)
    zf, ef = load_font(font, zh_size), load_font(font, en_size)
    cx = width // 2
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sh = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    _spaced(sd, (cx, zh_y + 3), zh, zf, 14, (0, 0, 0, 160))
    _spaced(sd, (cx, en_y + 2), en, ef, 8, (0, 0, 0, 160))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    _spaced(d, (cx, zh_y), zh, zf, 14, (244, 244, 244, 255), 2, (16, 16, 16, 128))
    _spaced(d, (cx, en_y), en, ef, 8, (234, 234, 234, 255), 1, (16, 16, 16, 128))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def render_watermark(
    out: Path, lines: list[str], *, width: int = 1920, height: int = 1080,
    font=None, size: int = 25, right: int = 48, bottom: int = 38, leading: int = 30,
) -> Path:
    """Small right-aligned lines in the bottom-right corner, ~70% opaque."""
    from PIL import Image, ImageDraw

    f = load_font(font, size)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = height - bottom - len(lines) * leading
    for k, t in enumerate(lines):
        x = width - right - d.textlength(t, font=f)
        d.text((x, y + k * leading), t, font=f, fill=(242, 238, 232, 175),
               stroke_width=1, stroke_fill=(32, 40, 48, 110))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def render_cue(
    out: Path, zh: str, en: str, *, width: int = 1920, height: int = 118,
    zh_font=None, en_font=None, zh_size: int = 40, en_size: int = 24,
    zh_y: int = 10, en_y: int = 66,
) -> Path:
    """One bilingual subtitle cue as a strip, centred, outlined, soft shadow."""
    from PIL import Image, ImageDraw, ImageFilter

    rows = [(zh, load_font(zh_font, zh_size), zh_y), (en, load_font(en_font, en_size), en_y)]
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for text, font, y in rows:
        if text:
            x = (width - d.textlength(text, font=font)) / 2
            sd.text((x, y + 3), text, font=font, fill=(0, 0, 0, 200))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(4)))
    d = ImageDraw.Draw(img)
    for text, font, y in rows:
        if text:
            x = (width - d.textlength(text, font=font)) / 2
            d.text((x, y), text, font=font, fill=(245, 242, 235, 255),
                   stroke_width=2, stroke_fill=(0, 0, 0, 170))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
