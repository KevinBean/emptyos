"""garden — themes as data.

Each theme is a dict carrying its complete visual identity: palette,
SVG filter defs, per-species grammar functions, plot-level ornaments
(seal, vertical text, caption), and composition mode. Adding a 6th
theme means adding a dict here; no other file changes.

A "species grammar" takes a Plant + position + palette + rng and returns
a list of SVG element strings. The renderer (`_render.py`) does the
iteration; the grammar owns the *visual language* for one species under
one theme.

Pure functions. No I/O. No app-state. Theme dict is the only export.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable

from . import _lsystem as L


# ─────────────────────────────────────────────────────────────────────
# Stage helpers (palette modulation per plant lifecycle)
# ─────────────────────────────────────────────────────────────────────

def _stage_opacity(stage: str) -> float:
    return {
        "seedling": 0.55,
        "budding": 0.95,
        "evergreen": 0.9,
        "wilting": 0.6,
        "dormant": 0.35,
    }.get(stage, 0.8)


def _stage_color(stage: str, alive: str, faded: str, dead: str) -> str:
    if stage in ("dormant",):
        return dead
    if stage in ("wilting",):
        return faded
    return alive


# ─────────────────────────────────────────────────────────────────────
# SUMI-E · 水墨留白 — Hiroshi Senju / 马远 一角
# ─────────────────────────────────────────────────────────────────────

_SUMI_PAL = {
    "bg": "#f1ebe0",       # 米底
    "ink": "#1a1a1a",      # 墨
    "accent": "#b13a2e",   # 朱砂
    "muted": "#5c5650",
    "wash": "#a8a39a",
    "leaf_alive": "#1a1a1a",
    "leaf_faded": "#5c5650",
    "leaf_dead": "#a8a39a",
}

_SUMI_DEFS = """
<filter id="sumi-paper" x="0" y="0" width="100%" height="100%">
  <feTurbulence baseFrequency="0.9" numOctaves="2" seed="3"/>
  <feColorMatrix values="0 0 0 0 0.6  0 0 0 0 0.5  0 0 0 0 0.4  0 0 0 0.06 0"/>
</filter>
<filter id="sumi-brush" x="-10%" y="-10%" width="120%" height="120%">
  <feTurbulence baseFrequency="0.7" numOctaves="2" seed="1"/>
  <feDisplacementMap in="SourceGraphic" scale="0.9"/>
</filter>
""".strip()


def _sumi_bg(width: int, height: int, slug: str) -> str:
    return (
        f'<rect width="{width}" height="{height}" fill="{_SUMI_PAL["bg"]}"/>'
        f'<rect width="{width}" height="{height}" filter="url(#sumi-paper)"/>'
        f'<path d="M 8 {height - 14} Q {width / 2:.0f} {height - 11} {width - 8} {height - 15}" '
        f'stroke="{_SUMI_PAL["wash"]}" stroke-width="0.6" fill="none" opacity="0.5"/>'
    )


def _sumi_fg(width: int, height: int, slug: str) -> str:
    glyphs = {"physical": "动", "social": "众", "intellectual": "思", "occupational": "事"}
    g = glyphs.get(slug, "空")
    return (
        f'<g transform="translate({width - 30},12)">'
        f'<rect width="20" height="20" fill="{_SUMI_PAL["accent"]}" rx="1"/>'
        f'<text x="10" y="15.5" text-anchor="middle" font-family="serif" '
        f'font-size="13" fill="{_SUMI_PAL["bg"]}" font-weight="bold">{g}</text>'
        f'</g>'
    )


def _sumi_bamboo(plant, x: float, base_y: float, pal: dict, rng: random.Random) -> list[str]:
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    ink = _stage_color(plant["stage"], pal["ink"], pal["muted"], pal["wash"])
    height = (55 + 55 * vigour)
    top_x = x + math.sin(droop) * height
    top_y = base_y - height * math.cos(droop)
    out = []
    # Thick wet-brush stem.
    out.append(
        f'<path stroke="{ink}" stroke-width="4.5" stroke-linecap="round" '
        f'fill="none" opacity="{op:.2f}" '
        f'd="M{x:.1f},{base_y:.1f} Q{(x + top_x) / 2:.1f},'
        f'{(base_y + top_y) / 2 - 3:.1f} {top_x:.1f},{top_y:.1f}"/>'
    )
    # Cross-stroke joints (3-4).
    segs = 4
    for k in range(1, segs):
        t = k / segs
        nx = x + (top_x - x) * t
        ny = base_y + (top_y - base_y) * t
        out.append(
            f'<line stroke="{ink}" stroke-width="3" stroke-linecap="round" '
            f'opacity="{op:.2f}" '
            f'x1="{nx - 8:.1f}" y1="{ny:.1f}" x2="{nx + 8:.1f}" y2="{ny - 0.5:.1f}"/>'
        )
        if k >= 1 and rng.random() < 0.65 and plant["stage"] != "dormant":
            side = 1 if rng.random() > 0.5 else -1
            leaf_d = L.filled_leaf(nx, ny, side, 18 + vigour * 8, width=0.45)
            out.append(f'<path fill="{ink}" opacity="{op:.2f}" d="{leaf_d}"/>')
    return out


def _sumi_wildflower(plant, x: float, base_y: float, pal: dict, rng: random.Random) -> list[str]:
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    ink = _stage_color(plant["stage"], pal["ink"], pal["muted"], pal["wash"])
    height = 24 + 22 * vigour
    bend = math.sin(droop) * height + rng.uniform(-4, 4)
    tip_x = x + bend
    tip_y = base_y - height + (math.cos(droop) * 0 if droop == 0 else 0)
    out = [
        f'<path stroke="{ink}" stroke-width="1.4" stroke-linecap="round" '
        f'fill="none" opacity="{op:.2f}" '
        f'd="M{x:.1f},{base_y:.1f} Q{x + bend * 0.4:.1f},'
        f'{base_y - height * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>'
    ]
    # Black ink dab; one vermillion accent per cluster (handled by renderer
    # — but here we mark "alive" flowers with hint).
    r = 2.4 + vigour * 1.4
    use_accent = plant.get("_accent", False)
    fill = pal["accent"] if use_accent and plant["stage"] in ("budding", "evergreen") else ink
    out.append(
        f'<circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="{r:.1f}" fill="{fill}" opacity="{op:.2f}"/>'
    )
    return out


def _sumi_sapling(plant, x: float, base_y: float, pal: dict, rng: random.Random) -> list[str]:
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    ink = _stage_color(plant["stage"], pal["ink"], pal["muted"], pal["wash"])
    height = 50 + 25 * vigour
    branches = L.branch_tree(rng, x, base_y, -math.pi / 2, height, depth=3)
    out = []
    for (x1, y1, x2, y2, w) in branches:
        out.append(
            f'<line stroke="{ink}" stroke-width="{max(0.9, w):.1f}" '
            f'stroke-linecap="round" opacity="{op:.2f}" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
        )
    return out


def _sumi_grass(plant, x: float, base_y: float, pal: dict, rng: random.Random) -> list[str]:
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    ink = _stage_color(plant["stage"], pal["ink"], pal["muted"], pal["wash"])
    n = 3 + int(vigour * 4)
    out = []
    for _ in range(n):
        sx = x + rng.uniform(-9, 9)
        h = 8 + 12 * vigour + rng.uniform(-2, 2)
        bend = rng.uniform(-2, 2)
        tip_x = sx + bend
        tip_y = base_y - h
        # Filled flame
        out.append(
            f'<path fill="{ink}" opacity="{op:.2f}" '
            f'd="M{sx - 0.6:.1f},{base_y:.1f} '
            f'Q{sx + bend * 0.4:.1f},{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f} '
            f'Q{sx + bend * 0.4 + 0.4:.1f},{base_y - h * 0.55:.1f} '
            f'{sx + 0.6:.1f},{base_y:.1f} Z"/>'
        )
    return out


# ─────────────────────────────────────────────────────────────────────
# APPLETON · 数字花园 — Maggie Appleton
# ─────────────────────────────────────────────────────────────────────

_APP_PAL = {
    "bg": "#e8dec8",
    "ink": "#3d2817",
    "leaf_alive": "#7a8b56",
    "leaf_faded": "#a09060",
    "leaf_dead": "#8c7d6a",
    "bloom": "#b56b3e",
    "bloom_alt": "#c89548",
    "muted": "#8c7d6a",
}

_APP_DEFS = """
<filter id="app-paper" x="0" y="0" width="100%" height="100%">
  <feTurbulence baseFrequency="1.2" numOctaves="2" seed="7"/>
  <feColorMatrix values="0 0 0 0 0.55  0 0 0 0 0.45  0 0 0 0 0.3  0 0 0 0.08 0"/>
</filter>
<filter id="app-rough" x="-10%" y="-10%" width="120%" height="120%">
  <feTurbulence baseFrequency="0.03" numOctaves="3" seed="2"/>
  <feDisplacementMap in="SourceGraphic" scale="1.3"/>
</filter>
""".strip()


def _app_bg(w, h, slug):
    return (
        f'<rect width="{w}" height="{h}" fill="{_APP_PAL["bg"]}"/>'
        f'<rect width="{w}" height="{h}" filter="url(#app-paper)"/>'
        f'<path d="M 8 {h - 12} L {w - 8} {h - 12}" stroke="{_APP_PAL["ink"]}" '
        f'stroke-width="0.8" stroke-dasharray="2 3" fill="none" opacity="0.5"/>'
    )


def _app_fg(w, h, slug):
    roman = {"physical": "i", "social": "ii", "intellectual": "iii", "occupational": "iv"}
    r = roman.get(slug, "—")
    return (
        f'<text x="{w - 8}" y="{h - 2}" text-anchor="end" '
        f'font-family="Georgia, serif" font-size="9" '
        # 0.45 composited to 2.4:1 against the card — an ornament you couldn't
        # read. 0.7 keeps it subtle and clears the legibility floor.
        f'fill="{_APP_PAL["ink"]}" opacity="0.7" font-style="italic">— {r} —</text>'
    )


def _app_bamboo(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    leaf = _stage_color(plant["stage"], pal["leaf_alive"], pal["leaf_faded"], pal["leaf_dead"])
    h = 55 + 40 * vigour
    top_x = x + math.sin(droop) * h
    top_y = base_y - h * math.cos(droop)
    ctl_x = (x + top_x) / 2
    ctl_y = (base_y + top_y) / 2 - 4
    out = [
        # Main stem
        f'<path stroke="{pal["ink"]}" stroke-width="2.2" stroke-linecap="round" '
        f'fill="none" opacity="{op:.2f}" '
        f'd="M{x:.1f},{base_y:.1f} Q{ctl_x:.1f},{ctl_y:.1f} '
        f'{top_x:.1f},{top_y:.1f}"/>',
        # Offset underdrawing
        f'<path stroke="{pal["ink"]}" stroke-width="0.6" opacity="0.4" '
        f'stroke-linecap="round" fill="none" '
        f'd="M{x + 1.5:.1f},{base_y:.1f} Q{ctl_x + 1.5:.1f},{ctl_y:.1f} '
        f'{top_x + 1.5:.1f},{top_y:.1f}"/>',
    ]
    segs = 4
    for k in range(1, segs):
        t = k / segs
        nx = x + (top_x - x) * t
        ny = base_y + (top_y - base_y) * t
        if rng.random() < 0.6 and plant["stage"] != "dormant":
            side = 1 if rng.random() > 0.5 else -1
            ln = 16 + vigour * 6
            tip_x = nx + side * ln
            tip_y = ny - 3
            out.append(
                f'<path fill="{leaf}" stroke="{pal["ink"]}" stroke-width="0.8" '
                f'opacity="{op:.2f}" '
                f'd="M{nx:.1f},{ny:.1f} Q{nx + side * 11:.1f},{ny - 5:.1f} '
                f'{tip_x:.1f},{tip_y:.1f} Q{nx + side * 9:.1f},{ny + 3:.1f} '
                f'{nx:.1f},{ny + 1:.1f} Z"/>'
            )
            out.append(
                f'<path stroke="{pal["ink"]}" stroke-width="0.4" fill="none" '
                f'opacity="0.5" d="M{nx + side * 2:.1f},{ny:.1f} '
                f'L{tip_x - side * 2:.1f},{tip_y - 0.5:.1f}"/>'
            )
    # Inline italic annotation for high-vigour plants
    if plant["stage"] == "budding" and plant.get("_show_annot", False):
        days = plant.get("days_since", 0)
        if days >= 0:
            out.append(
                f'<text x="{top_x + 6:.1f}" y="{top_y + 8:.1f}" '
                f'font-family="Georgia, serif" font-size="9" '
                f'fill="{pal["muted"]}" opacity="0.6" font-style="italic">~{days}d</text>'
            )
    return out


def _app_wildflower(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    h = 18 + 22 * vigour + rng.uniform(-4, 4)
    bend = rng.uniform(-5, 5) + math.sin(droop) * 6
    tip_x = x + bend
    tip_y = base_y - h
    out = [
        f'<path stroke="{pal["ink"]}" stroke-width="1.2" fill="none" '
        f'opacity="{op:.2f}" '
        f'd="M{x:.1f},{base_y:.1f} Q{x + bend * 0.4:.1f},'
        f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>'
    ]
    bloom = _stage_color(plant["stage"], pal["bloom"], pal["bloom_alt"], pal["muted"])
    if plant["stage"] in ("budding", "evergreen", "wilting"):
        r = 1.8 + vigour * 1.0
        for (dx, dy) in ((0, -r * 1.7), (-r * 1.7, 0), (r * 1.7, 0), (0, r * 1.7)):
            out.append(
                f'<circle cx="{tip_x + dx:.1f}" cy="{tip_y + dy:.1f}" '
                f'r="{r:.1f}" fill="{bloom}" stroke="{pal["ink"]}" '
                f'stroke-width="0.5" opacity="{op:.2f}"/>'
            )
        out.append(
            f'<circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="1.1" '
            f'fill="{pal["bloom_alt"]}" opacity="{op:.2f}"/>'
        )
    else:
        out.append(
            f'<ellipse cx="{tip_x:.1f}" cy="{tip_y:.1f}" rx="1.8" ry="2.4" '
            f'fill="{pal["leaf_faded"]}" stroke="{pal["ink"]}" '
            f'stroke-width="0.4" opacity="{op:.2f}"/>'
        )
    return out


def _app_sapling(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    leaf = _stage_color(plant["stage"], pal["leaf_alive"], pal["leaf_faded"], pal["leaf_dead"])
    h = 45 + 30 * vigour
    branches = L.branch_tree(rng, x, base_y, -math.pi / 2, h, depth=3)
    out = []
    for (x1, y1, x2, y2, w) in branches:
        out.append(
            f'<line stroke="{pal["ink"]}" stroke-width="{w:.1f}" '
            f'stroke-linecap="round" opacity="{op:.2f}" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
        )
    if plant["stage"] != "dormant":
        terms = [(b[2], b[3]) for b in branches if b[4] < 1.5]
        for (lx, ly) in rng.sample(terms, min(3 + int(vigour * 2), len(terms))):
            out.append(
                f'<ellipse cx="{lx:.1f}" cy="{ly:.1f}" rx="3" ry="1.6" '
                f'fill="{leaf}" stroke="{pal["ink"]}" stroke-width="0.5" '
                f'opacity="{op:.2f}" '
                f'transform="rotate({rng.uniform(-40, 40):.0f} {lx:.1f} {ly:.1f})"/>'
            )
    return out


def _app_grass(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    n = 4 + int(vigour * 4)
    out = []
    for _ in range(n):
        sx = x + rng.uniform(-9, 9)
        h = 7 + 11 * vigour
        bend = rng.uniform(-3, 3)
        tip_x = sx + bend
        tip_y = base_y - h
        out.append(
            f'<path stroke="{pal["ink"]}" stroke-width="0.9" fill="none" '
            f'opacity="{op:.2f}" stroke-linecap="round" '
            f'd="M{sx:.1f},{base_y:.1f} Q{sx + bend * 0.4:.1f},'
            f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>'
        )
    return out


# ─────────────────────────────────────────────────────────────────────
# SCROLL · 中式古籍图鉴 — 本草纲目 / 宋花鸟册页
# ─────────────────────────────────────────────────────────────────────

_SCR_PAL = {
    "bg": "#f0e6cd",
    "ink": "#2b1d10",
    "wash": "#c4ce9a",          # 浅绿染
    "bloom": "#d4a8a0",         # 浅赭红
    "accent": "#a23a2a",        # 朱印
    "muted": "#735634",
    "leaf_alive": "#c4ce9a",
    "leaf_faded": "#b0a070",
    "leaf_dead": "#8c7d4a",
}

_SCR_DEFS = """
<filter id="scr-paper" x="0" y="0" width="100%" height="100%">
  <feTurbulence baseFrequency="1.1" numOctaves="2" seed="9"/>
  <feColorMatrix values="0 0 0 0 0.6  0 0 0 0 0.5  0 0 0 0 0.35  0 0 0 0.05 0"/>
</filter>
<filter id="scr-fine" x="-10%" y="-10%" width="120%" height="120%">
  <feTurbulence baseFrequency="0.05" numOctaves="2" seed="4"/>
  <feDisplacementMap in="SourceGraphic" scale="0.3"/>
</filter>
""".strip()

_SCR_SEASONS = ("立春 · 一候", "雨水 · 二候", "惊蛰 · 三候", "春分 · 七日",
                "清明 · 初候", "谷雨 · 中候", "立夏 · 余日", "小满 · 微凉")
_SCR_GLYPHS = {"physical": "动", "social": "众", "intellectual": "思", "occupational": "事"}


def _scr_bg(w, h, slug):
    return (
        f'<rect width="{w}" height="{h}" fill="{_SCR_PAL["bg"]}"/>'
        f'<rect width="{w}" height="{h}" filter="url(#scr-paper)"/>'
        f'<rect x="14" y="8" width="{w - 28}" height="{h - 16}" '
        f'fill="none" stroke="{_SCR_PAL["ink"]}" stroke-width="0.4" opacity="0.18"/>'
    )


def _scr_fg(w, h, slug):
    idx = {"physical": 0, "social": 1, "intellectual": 2, "occupational": 3}.get(slug, 0)
    season = _SCR_SEASONS[idx]
    glyph = _SCR_GLYPHS.get(slug, "空")
    return (
        # Vertical glyph top-right
        f'<text x="{w - 8}" y="22" font-family="\'Songti SC\', \'STSong\', serif" '
        f'font-size="13" fill="{_SCR_PAL["ink"]}" opacity="0.6" text-anchor="end">{glyph}</text>'
        # Red seal lower-left
        f'<g transform="translate(20,{h - 28})">'
        f'<rect width="16" height="16" fill="{_SCR_PAL["accent"]}"/>'
        f'<text x="8" y="12" text-anchor="middle" font-family="\'Songti SC\', serif" '
        f'font-size="10" fill="{_SCR_PAL["bg"]}" font-weight="bold">空</text>'
        f'</g>'
        # Bottom-center season caption
        f'<text x="{w / 2:.0f}" y="{h - 4}" text-anchor="middle" '
        f'font-family="\'Songti SC\', \'STSong\', serif" font-size="8" '
        f'fill="{_SCR_PAL["ink"]}" opacity="0.55" letter-spacing="2">{season}</text>'
    )


def _scr_bamboo(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    h = 55 + 40 * vigour
    top_x = x + math.sin(droop) * h
    top_y = base_y - h * math.cos(droop)
    out = [
        # Wash underlay (broad green)
        f'<path stroke="{pal["wash"]}" stroke-width="3.8" opacity="0.45" '
        f'fill="none" stroke-linecap="round" '
        f'd="M{x - 1.5:.1f},{base_y:.1f} Q{(x + top_x) / 2 - 1.5:.1f},'
        f'{(base_y + top_y) / 2:.1f} {top_x - 1.5:.1f},{top_y:.1f}"/>',
        # Fine outline
        f'<path stroke="{pal["ink"]}" stroke-width="1.1" stroke-linecap="round" '
        f'fill="none" opacity="{op:.2f}" '
        f'd="M{x:.1f},{base_y:.1f} Q{(x + top_x) / 2:.1f},'
        f'{(base_y + top_y) / 2:.1f} {top_x:.1f},{top_y:.1f}"/>',
    ]
    segs = 5
    for k in range(1, segs):
        t = k / segs
        nx = x + (top_x - x) * t
        ny = base_y + (top_y - base_y) * t
        out.append(
            f'<path stroke="{pal["ink"]}" stroke-width="0.7" fill="none" '
            f'opacity="{op:.2f}" '
            f'd="M{nx - 5:.1f},{ny:.1f} Q{nx:.1f},{ny - 1.5:.1f} {nx + 5:.1f},{ny:.1f}"/>'
        )
        if rng.random() < 0.55 and plant["stage"] != "dormant":
            side = 1 if rng.random() > 0.5 else -1
            ln = 16 + vigour * 5
            tip_x = nx + side * ln
            tip_y = ny - 3
            out.append(
                f'<path fill="{pal["wash"]}" stroke="{pal["ink"]}" stroke-width="0.5" '
                f'opacity="0.85" '
                f'd="M{nx:.1f},{ny:.1f} Q{nx + side * 11:.1f},{ny - 5:.1f} '
                f'{tip_x:.1f},{tip_y:.1f} Q{nx + side * 9:.1f},{ny + 3:.1f} '
                f'{nx:.1f},{ny + 1:.1f} Z"/>'
            )
    return out


def _scr_wildflower(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    h = 22 + 22 * vigour
    bend = rng.uniform(-4, 4) + math.sin(droop) * 6
    tip_x = x + bend
    tip_y = base_y - h
    out = [
        f'<path stroke="{pal["ink"]}" stroke-width="0.9" fill="none" '
        f'opacity="{op:.2f}" '
        f'd="M{x:.1f},{base_y:.1f} Q{x + bend * 0.4:.1f},'
        f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>'
    ]
    if plant["stage"] not in ("dormant",):
        petal_n = 6 if plant["stage"] in ("budding", "evergreen") else 5
        step = 360 / petal_n
        bloom = _stage_color(plant["stage"], pal["bloom"], pal["leaf_faded"], pal["leaf_dead"])
        for p in range(petal_n):
            out.append(
                f'<ellipse cx="0" cy="-3.5" rx="1.8" ry="3.2" '
                f'fill="{bloom}" stroke="{pal["ink"]}" stroke-width="0.4" '
                f'opacity="0.85" '
                f'transform="translate({tip_x:.1f},{tip_y:.1f}) rotate({p * step:.0f})"/>'
            )
        out.append(
            f'<circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="1.3" '
            f'fill="{pal["accent"]}" opacity="{op:.2f}"/>'
        )
    else:
        out.append(
            f'<circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="1.8" '
            f'fill="{pal["leaf_dead"]}" opacity="{op:.2f}"/>'
        )
    return out


def _scr_sapling(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    h = 45 + 28 * vigour
    branches = L.branch_tree(rng, x, base_y, -math.pi / 2, h, depth=3)
    out = []
    for (x1, y1, x2, y2, w) in branches:
        out.append(
            f'<line stroke="{pal["wash"]}" stroke-width="{max(1.3, w * 1.3):.1f}" '
            f'opacity="0.4" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
        )
        out.append(
            f'<line stroke="{pal["ink"]}" stroke-width="0.8" stroke-linecap="round" '
            f'opacity="{op:.2f}" '
            f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
        )
    if plant["stage"] != "dormant":
        terms = [(b[2], b[3]) for b in branches if b[4] < 1.5]
        leaf = _stage_color(plant["stage"], pal["wash"], pal["leaf_faded"], pal["leaf_dead"])
        for (lx, ly) in rng.sample(terms, min(3 + int(vigour * 2), len(terms))):
            out.append(
                f'<ellipse cx="{lx:.1f}" cy="{ly:.1f}" rx="2.8" ry="1.5" '
                f'fill="{leaf}" stroke="{pal["ink"]}" stroke-width="0.4" '
                f'opacity="0.8" '
                f'transform="rotate({rng.uniform(-30, 30):.0f} {lx:.1f} {ly:.1f})"/>'
            )
    return out


def _scr_grass(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    n = 3 + int(vigour * 4)
    out = []
    for _ in range(n):
        sx = x + rng.uniform(-9, 9)
        h = 8 + 10 * vigour
        bend = rng.uniform(-2, 2)
        tip_x = sx + bend
        tip_y = base_y - h
        # paired: wash + fine outline
        out.append(
            f'<path stroke="{pal["wash"]}" stroke-width="1.6" fill="none" '
            f'opacity="0.5" '
            f'd="M{sx:.1f},{base_y:.1f} Q{sx + bend * 0.4:.1f},'
            f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>'
        )
        out.append(
            f'<path stroke="{pal["ink"]}" stroke-width="0.5" fill="none" '
            f'opacity="{op:.2f}" '
            f'd="M{sx:.1f},{base_y:.1f} Q{sx + bend * 0.4:.1f},'
            f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>'
        )
    return out


# ─────────────────────────────────────────────────────────────────────
# COTTAGE · 英式乡村标本册 — Beatrix Potter / Morris / Victorian herbarium
# ─────────────────────────────────────────────────────────────────────

_COT_PAL = {
    "bg": "#f5ebe0",         # 旧纸
    "ink": "#4a5859",        # 板岩灰
    "leaf_alive": "#9caf88", # sage
    "leaf_faded": "#a8a878",
    "leaf_dead": "#8a8270",
    "bloom": "#d4a5a5",      # dusty rose
    "bloom_alt": "#c9a96e",  # antique gold
    "muted": "#7a7268",
    "wash": "#d8dcc6",
}

_COT_DEFS = """
<filter id="cot-paper" x="0" y="0" width="100%" height="100%">
  <feTurbulence baseFrequency="0.95" numOctaves="2" seed="6"/>
  <feColorMatrix values="0 0 0 0 0.5  0 0 0 0 0.42  0 0 0 0 0.32  0 0 0 0.06 0"/>
</filter>
<filter id="cot-soft" x="-10%" y="-10%" width="120%" height="120%">
  <feGaussianBlur stdDeviation="0.2"/>
</filter>
""".strip()

_COT_LATIN = {
    "physical":     ("Herba salutis",   "i"),
    "social":       ("Flora amicitiae", "ii"),
    "intellectual": ("Arbor sapientis", "iii"),
    "occupational": ("Bambusa operis",  "iv"),
}


def _cot_bg(w, h, slug):
    return (
        f'<rect width="{w}" height="{h}" fill="{_COT_PAL["bg"]}"/>'
        f'<rect width="{w}" height="{h}" filter="url(#cot-paper)"/>'
        # decorative border
        f'<rect x="6" y="6" width="{w - 12}" height="{h - 12}" fill="none" '
        f'stroke="{_COT_PAL["muted"]}" stroke-width="0.4" opacity="0.5"/>'
        f'<rect x="9" y="9" width="{w - 18}" height="{h - 18}" fill="none" '
        f'stroke="{_COT_PAL["muted"]}" stroke-width="0.3" opacity="0.3"/>'
    )


def _cot_fg(w, h, slug):
    latin, num = _COT_LATIN.get(slug, ("Planta", "—"))
    return (
        # Latin copperplate caption centered below plot
        f'<text x="{w / 2:.0f}" y="{h - 4}" text-anchor="middle" '
        f'font-family="\'EB Garamond\', \'Cormorant Garamond\', Georgia, serif" '
        f'font-size="9" font-style="italic" fill="{_COT_PAL["ink"]}" '
        f'opacity="0.7" letter-spacing="0.5">{latin} · {num}</text>'
        # Tiny morris-style corner ornament (top-right)
        f'<g transform="translate({w - 20},14)" opacity="0.5">'
        f'<circle cx="0" cy="0" r="2" fill="none" stroke="{_COT_PAL["leaf_alive"]}" stroke-width="0.5"/>'
        f'<circle cx="0" cy="0" r="0.8" fill="{_COT_PAL["bloom"]}"/>'
        f'<path d="M-4,0 Q-2,-3 0,-2 Q2,-3 4,0" fill="none" stroke="{_COT_PAL["leaf_alive"]}" stroke-width="0.5"/>'
        f'</g>'
    )


def _cot_bamboo(plant, x, base_y, pal, rng):
    # Cottage doesn't really do bamboo — render as a slender garden reed
    # with sage culm + rosy bracts where leaves would be.
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    h = 55 + 35 * vigour
    top_x = x + math.sin(droop) * h
    top_y = base_y - h * math.cos(droop)
    leaf = _stage_color(plant["stage"], pal["leaf_alive"], pal["leaf_faded"], pal["leaf_dead"])
    out = [
        # soft sage stem
        f'<path stroke="{leaf}" stroke-width="2.4" stroke-linecap="round" '
        f'fill="none" opacity="{op:.2f}" '
        f'd="M{x:.1f},{base_y:.1f} Q{(x + top_x) / 2:.1f},'
        f'{(base_y + top_y) / 2:.1f} {top_x:.1f},{top_y:.1f}"/>',
        # ink outline (very thin)
        f'<path stroke="{pal["ink"]}" stroke-width="0.5" fill="none" '
        f'opacity="0.6" stroke-linecap="round" '
        f'd="M{x:.1f},{base_y:.1f} Q{(x + top_x) / 2:.1f},'
        f'{(base_y + top_y) / 2:.1f} {top_x:.1f},{top_y:.1f}"/>',
    ]
    segs = 4
    for k in range(1, segs + 1):
        t = k / (segs + 0.5)
        nx = x + (top_x - x) * t
        ny = base_y + (top_y - base_y) * t
        if plant["stage"] != "dormant" and rng.random() < 0.7:
            side = 1 if rng.random() > 0.5 else -1
            ln = 12 + vigour * 4
            tip_x = nx + side * ln
            tip_y = ny - 2
            out.append(
                f'<ellipse cx="{(nx + tip_x) / 2:.1f}" cy="{(ny + tip_y) / 2:.1f}" '
                f'rx="{ln / 2 - 1:.1f}" ry="2.4" fill="{leaf}" '
                f'stroke="{pal["ink"]}" stroke-width="0.5" opacity="{op * 0.85:.2f}" '
                f'transform="rotate({rng.uniform(-25, 25):.0f} '
                f'{(nx + tip_x) / 2:.1f} {(ny + tip_y) / 2:.1f})"/>'
            )
    return out


def _cot_wildflower(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    h = 22 + 22 * vigour
    bend = rng.uniform(-4, 4) + math.sin(droop) * 6
    tip_x = x + bend
    tip_y = base_y - h
    leaf = _stage_color(plant["stage"], pal["leaf_alive"], pal["leaf_faded"], pal["leaf_dead"])
    out = [
        # sage stem
        f'<path stroke="{leaf}" stroke-width="1.4" fill="none" '
        f'opacity="{op:.2f}" '
        f'd="M{x:.1f},{base_y:.1f} Q{x + bend * 0.4:.1f},'
        f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>',
        # ink trace
        f'<path stroke="{pal["ink"]}" stroke-width="0.4" fill="none" '
        f'opacity="0.5" '
        f'd="M{x:.1f},{base_y:.1f} Q{x + bend * 0.4:.1f},'
        f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>',
    ]
    # one or two leaves on the stem
    if vigour > 0.45:
        mid_x = x + bend * 0.5
        mid_y = base_y - h * 0.5
        out.append(
            f'<ellipse cx="{mid_x:.1f}" cy="{mid_y:.1f}" rx="3" ry="1.4" '
            f'fill="{leaf}" stroke="{pal["ink"]}" stroke-width="0.4" '
            f'opacity="{op:.2f}" transform="rotate(35 {mid_x:.1f} {mid_y:.1f})"/>'
        )
    if plant["stage"] not in ("dormant",):
        # 5-petal pressed flower with stamens
        bloom = _stage_color(plant["stage"], pal["bloom"], pal["bloom_alt"], pal["muted"])
        for p in range(5):
            angle = p * 72
            out.append(
                f'<ellipse cx="0" cy="-3.2" rx="1.8" ry="2.6" '
                f'fill="{bloom}" stroke="{pal["ink"]}" stroke-width="0.3" '
                f'opacity="{op * 0.9:.2f}" '
                f'transform="translate({tip_x:.1f},{tip_y:.1f}) rotate({angle})"/>'
            )
        # gold stamen center
        out.append(
            f'<circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="1.1" '
            f'fill="{pal["bloom_alt"]}" opacity="{op:.2f}"/>'
        )
        # 3 stamen dots
        for ang in (0, 120, 240):
            sx = tip_x + math.cos(math.radians(ang)) * 0.6
            sy = tip_y + math.sin(math.radians(ang)) * 0.6
            out.append(
                f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="0.35" '
                f'fill="{pal["ink"]}" opacity="{op:.2f}"/>'
            )
    return out


def _cot_sapling(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    leaf = _stage_color(plant["stage"], pal["leaf_alive"], pal["leaf_faded"], pal["leaf_dead"])
    h = 45 + 28 * vigour
    trunk_top = base_y - h * 0.4
    canopy_r = 14 + vigour * 8
    out = [
        # trunk
        f'<line stroke="{pal["ink"]}" stroke-width="1.8" stroke-linecap="round" '
        f'opacity="{op:.2f}" '
        f'x1="{x:.1f}" y1="{base_y:.1f}" x2="{x:.1f}" y2="{trunk_top:.1f}"/>',
        # canopy: soft watercolor wash
        f'<circle cx="{x:.1f}" cy="{trunk_top - canopy_r * 0.6:.1f}" r="{canopy_r:.1f}" '
        f'fill="{leaf}" opacity="0.55"/>',
        # canopy outline (sketchy)
        f'<circle cx="{x:.1f}" cy="{trunk_top - canopy_r * 0.6:.1f}" r="{canopy_r:.1f}" '
        f'fill="none" stroke="{pal["ink"]}" stroke-width="0.5" opacity="{op * 0.7:.2f}"/>',
    ]
    # a few apple-rose dots if blooming
    if plant["stage"] in ("budding", "evergreen"):
        for _ in range(2 + int(vigour * 2)):
            ax = x + rng.uniform(-canopy_r * 0.7, canopy_r * 0.7)
            ay = trunk_top - canopy_r * 0.6 + rng.uniform(-canopy_r * 0.5, canopy_r * 0.5)
            out.append(
                f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="1.2" '
                f'fill="{pal["bloom"]}" opacity="{op:.2f}"/>'
            )
    return out


def _cot_grass(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    leaf = _stage_color(plant["stage"], pal["leaf_alive"], pal["leaf_faded"], pal["leaf_dead"])
    n = 3 + int(vigour * 3)
    out = []
    for _ in range(n):
        sx = x + rng.uniform(-10, 10)
        h = 10 + 13 * vigour
        bend = rng.uniform(-2, 2)
        tip_x = sx + bend
        tip_y = base_y - h
        out.append(
            f'<path stroke="{leaf}" stroke-width="0.9" fill="none" '
            f'opacity="{op:.2f}" stroke-linecap="round" '
            f'd="M{sx:.1f},{base_y:.1f} Q{sx + bend * 0.4:.1f},'
            f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>'
        )
        # tiny seed-head on top of each blade if vigorous
        if vigour > 0.6 and rng.random() < 0.6:
            out.append(
                f'<circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="0.9" '
                f'fill="{pal["bloom_alt"]}" opacity="{op * 0.8:.2f}"/>'
            )
    return out


# ─────────────────────────────────────────────────────────────────────
# EDO · 江户木版画 — Hokusai / Hiroshige / 琳派
# ─────────────────────────────────────────────────────────────────────

_EDO_PAL = {
    "bg": "#f0e6d2",          # 米黄
    "ink": "#1a1a1a",         # 墨黑
    "blue": "#1e3a5c",        # 普鲁士蓝
    "red": "#c0383a",         # 朱
    "ochre": "#c89a4f",       # 赭
    "muted": "#8b7c5c",
    "leaf_alive": "#1e3a5c",  # leaves go blue
    "leaf_faded": "#5b6a7a",
    "leaf_dead": "#8b7c5c",
}

_EDO_DEFS = """
<filter id="edo-paper" x="0" y="0" width="100%" height="100%">
  <feTurbulence baseFrequency="0.7" numOctaves="2" seed="5"/>
  <feColorMatrix values="0 0 0 0 0.55  0 0 0 0 0.45  0 0 0 0 0.3  0 0 0 0.04 0"/>
</filter>
""".strip()


def _edo_bg(w, h, slug):
    # Bottom-corner "wave" tucked low (Hokusai nod)
    return (
        f'<rect width="{w}" height="{h}" fill="{_EDO_PAL["bg"]}"/>'
        f'<rect width="{w}" height="{h}" filter="url(#edo-paper)"/>'
        f'<path d="M 0 {h - 4} Q {w * 0.2:.0f} {h - 14} {w * 0.4:.0f} {h - 6} '
        f'Q {w * 0.65:.0f} {h - 16} {w * 0.85:.0f} {h - 6} L {w} {h} L 0 {h} Z" '
        f'fill="{_EDO_PAL["blue"]}" opacity="0.13"/>'
    )


def _edo_fg(w, h, slug):
    glyph = {"physical": "歩", "social": "縁", "intellectual": "考", "occupational": "業"}.get(slug, "空")
    return (
        f'<g transform="translate({w - 32},10)">'
        f'<rect width="22" height="22" fill="{_EDO_PAL["red"]}" stroke="{_EDO_PAL["ink"]}" stroke-width="0.8"/>'
        f'<text x="11" y="16" text-anchor="middle" '
        f'font-family="\'Yu Mincho\', \'Hiragino Mincho\', serif" '
        f'font-size="14" fill="{_EDO_PAL["bg"]}" font-weight="bold">{glyph}</text>'
        f'</g>'
    )


def _edo_bamboo(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    h = 60 + 40 * vigour
    top_x = x + math.sin(droop) * h
    top_y = base_y - h * math.cos(droop)
    leaf_fill = _stage_color(plant["stage"], pal["leaf_alive"], pal["leaf_faded"], pal["leaf_dead"])
    out = []
    # Stem: thick black outline + ochre fill (no shading, flat-color)
    # build a polygon stem
    side_w = 3.5
    sx_l = x - side_w / 2
    sx_r = x + side_w / 2
    tx_l = top_x - side_w / 2
    tx_r = top_x + side_w / 2
    out.append(
        f'<path fill="{pal["ochre"]}" stroke="{pal["ink"]}" stroke-width="1.6" '
        f'stroke-linejoin="round" opacity="{op:.2f}" '
        f'd="M{sx_l:.1f},{base_y:.1f} L{tx_l:.1f},{top_y:.1f} '
        f'L{tx_r:.1f},{top_y:.1f} L{sx_r:.1f},{base_y:.1f} Z"/>'
    )
    # Bold joint cross-bars
    segs = 4
    for k in range(1, segs):
        t = k / segs
        nx = x + (top_x - x) * t
        ny = base_y + (top_y - base_y) * t
        out.append(
            f'<line stroke="{pal["ink"]}" stroke-width="2" stroke-linecap="square" '
            f'opacity="{op:.2f}" '
            f'x1="{nx - side_w / 2 - 1:.1f}" y1="{ny:.1f}" '
            f'x2="{nx + side_w / 2 + 1:.1f}" y2="{ny:.1f}"/>'
        )
        if rng.random() < 0.65 and plant["stage"] != "dormant":
            side = 1 if rng.random() > 0.5 else -1
            ln = 18 + vigour * 8
            tip_x = nx + side * ln
            tip_y = ny - 4
            # Flat blue leaf with heavy black outline
            out.append(
                f'<path fill="{leaf_fill}" stroke="{pal["ink"]}" stroke-width="1.2" '
                f'stroke-linejoin="round" opacity="{op:.2f}" '
                f'd="M{nx:.1f},{ny:.1f} Q{nx + side * 12:.1f},{ny - 7:.1f} '
                f'{tip_x:.1f},{tip_y:.1f} Q{nx + side * 10:.1f},{ny + 2:.1f} '
                f'{nx:.1f},{ny + 1:.1f} Z"/>'
            )
    return out


def _edo_wildflower(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    droop = L.stage_droop(plant["stage"])
    op = _stage_opacity(plant["stage"])
    h = 22 + 22 * vigour
    bend = rng.uniform(-4, 4) + math.sin(droop) * 6
    tip_x = x + bend
    tip_y = base_y - h
    out = [
        # Bold stem with outline
        f'<path stroke="{pal["ink"]}" stroke-width="1.8" fill="none" '
        f'opacity="{op:.2f}" stroke-linecap="round" '
        f'd="M{x:.1f},{base_y:.1f} Q{x + bend * 0.4:.1f},'
        f'{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f}"/>'
    ]
    if plant["stage"] not in ("dormant",):
        # 5-petal bold outlined flower, alternating blue/red
        bloom = pal["red"] if plant.get("_seed_idx", 0) % 2 == 0 else pal["blue"]
        if plant["stage"] == "wilting":
            bloom = pal["muted"]
        for p in range(5):
            angle = p * 72
            out.append(
                f'<ellipse cx="0" cy="-3.5" rx="2.4" ry="3.4" '
                f'fill="{bloom}" stroke="{pal["ink"]}" stroke-width="1.0" '
                f'opacity="{op:.2f}" '
                f'transform="translate({tip_x:.1f},{tip_y:.1f}) rotate({angle})"/>'
            )
        out.append(
            f'<circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="1.5" '
            f'fill="{pal["ochre"]}" stroke="{pal["ink"]}" stroke-width="0.6" '
            f'opacity="{op:.2f}"/>'
        )
    else:
        out.append(
            f'<circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="2" '
            f'fill="{pal["muted"]}" stroke="{pal["ink"]}" stroke-width="0.8" '
            f'opacity="{op:.2f}"/>'
        )
    return out


def _edo_sapling(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    leaf_fill = _stage_color(plant["stage"], pal["blue"], pal["muted"], pal["leaf_dead"])
    h = 45 + 30 * vigour
    trunk_top = base_y - h * 0.45
    # Cloud-shaped foliage masses (3 puffs)
    out = [
        # trunk: thick black outline + ochre fill
        f'<path fill="{pal["ochre"]}" stroke="{pal["ink"]}" stroke-width="1.4" '
        f'opacity="{op:.2f}" '
        f'd="M{x - 2:.1f},{base_y:.1f} L{x - 1.5:.1f},{trunk_top:.1f} '
        f'L{x + 1.5:.1f},{trunk_top:.1f} L{x + 2:.1f},{base_y:.1f} Z"/>',
    ]
    if plant["stage"] != "dormant":
        puff_r = 11 + vigour * 4
        for (dx, dy) in ((-6, 0), (6, -4), (0, -puff_r * 0.7)):
            cx = x + dx
            cy = trunk_top + dy
            out.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{puff_r:.1f}" '
                f'fill="{leaf_fill}" stroke="{pal["ink"]}" stroke-width="1.2" '
                f'opacity="{op:.2f}"/>'
            )
    return out


def _edo_grass(plant, x, base_y, pal, rng):
    vigour = L.stage_vigour(plant["stage"])
    op = _stage_opacity(plant["stage"])
    fill = _stage_color(plant["stage"], pal["blue"], pal["muted"], pal["leaf_dead"])
    n = 3 + int(vigour * 3)
    out = []
    for _ in range(n):
        sx = x + rng.uniform(-9, 9)
        h = 9 + 12 * vigour
        bend = rng.uniform(-3, 3)
        tip_x = sx + bend
        tip_y = base_y - h
        # Bold filled blade with black outline
        out.append(
            f'<path fill="{fill}" stroke="{pal["ink"]}" stroke-width="0.7" '
            f'stroke-linejoin="round" opacity="{op:.2f}" '
            f'd="M{sx - 1.4:.1f},{base_y:.1f} '
            f'Q{sx + bend * 0.4:.1f},{base_y - h * 0.55:.1f} {tip_x:.1f},{tip_y:.1f} '
            f'Q{sx + bend * 0.4 + 1.4:.1f},{base_y - h * 0.55:.1f} '
            f'{sx + 1.4:.1f},{base_y:.1f} Z"/>'
        )
    return out


# ─────────────────────────────────────────────────────────────────────
# THEMES registry — the public surface of this module
# ─────────────────────────────────────────────────────────────────────

THEMES: dict[str, dict[str, Any]] = {
    "sumi-e": {
        "name": "Ink Wash",
        "lineage": "Hiroshi Senju · Ma Yuan",
        "palette": _SUMI_PAL,
        "defs": _SUMI_DEFS,
        "bg": _sumi_bg,
        "fg": _sumi_fg,
        "wrap_attrs": 'filter="url(#sumi-brush)"',
        "composition": "asymmetric_corner",
        "species": {
            "bamboo": _sumi_bamboo,
            "wildflower": _sumi_wildflower,
            "sapling": _sumi_sapling,
            "grass": _sumi_grass,
        },
    },
    "appleton": {
        "name": "Digital Garden",
        "lineage": "Maggie Appleton · digital garden",
        "palette": _APP_PAL,
        "defs": _APP_DEFS,
        "bg": _app_bg,
        "fg": _app_fg,
        "wrap_attrs": 'filter="url(#app-rough)"',
        "composition": "cluster",
        "species": {
            "bamboo": _app_bamboo,
            "wildflower": _app_wildflower,
            "sapling": _app_sapling,
            "grass": _app_grass,
        },
    },
    "scroll": {
        "name": "Botanical Scroll",
        "lineage": "Bencao Gangmu · Song bird-and-flower albums",
        "palette": _SCR_PAL,
        "defs": _SCR_DEFS,
        "bg": _scr_bg,
        "fg": _scr_fg,
        "wrap_attrs": 'filter="url(#scr-fine)"',
        "composition": "cluster",
        "species": {
            "bamboo": _scr_bamboo,
            "wildflower": _scr_wildflower,
            "sapling": _scr_sapling,
            "grass": _scr_grass,
        },
    },
    "cottage": {
        "name": "Victorian Herbarium",
        "lineage": "Beatrix Potter · William Morris",
        "palette": _COT_PAL,
        "defs": _COT_DEFS,
        "bg": _cot_bg,
        "fg": _cot_fg,
        "wrap_attrs": 'filter="url(#cot-soft)"',
        "composition": "centered",
        "species": {
            "bamboo": _cot_bamboo,
            "wildflower": _cot_wildflower,
            "sapling": _cot_sapling,
            "grass": _cot_grass,
        },
    },
    "edo": {
        "name": "Edo Woodblock",
        "lineage": "Hokusai · Hiroshige · Rinpa",
        "palette": _EDO_PAL,
        "defs": _EDO_DEFS,
        "bg": _edo_bg,
        "fg": _edo_fg,
        "wrap_attrs": "",  # no displacement — woodblock is sharp
        "composition": "cluster",
        "species": {
            "bamboo": _edo_bamboo,
            "wildflower": _edo_wildflower,
            "sapling": _edo_sapling,
            "grass": _edo_grass,
        },
    },
}


DEFAULT_THEME = "appleton"


def get_theme(name: str) -> dict[str, Any]:
    """Resolve a theme name to its spec dict; falls back to DEFAULT_THEME."""
    return THEMES.get(name) or THEMES[DEFAULT_THEME]
