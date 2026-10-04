"""garden — single rendering surface.

`render_plot(plot_data, theme_name) -> str` returns a complete SVG document
body for one plot. `_app.py` wraps it in HTTP. The renderer is the only
file that knows how to compose (defs + bg + plant grammar + chrome) into
a single SVG.

Theme grammars live in `themes.py`; geometry primitives in `_lsystem.py`.
This file owns composition: anchor placement, plant iteration, layer
stacking. Pure functions.
"""

from __future__ import annotations

from typing import Any

from . import _lsystem as L
from . import themes as _themes


PLOT_W = 260
PLOT_H = 180


def render_plot(plot_data: dict[str, Any], theme_name: str) -> str:
    """Compose one plot SVG from `plot_data` and the named theme.

    `plot_data` shape (from `_plot_sources.read_<slug>`):
        {
            "slug": "social",
            "species": "wildflower",
            "plants": [Plant dict, ...],
        }
    """
    theme = _themes.get_theme(theme_name)
    palette = theme["palette"]
    species = plot_data.get("species") or "grass"
    slug = plot_data.get("slug") or ""
    plants = list(plot_data.get("plants") or [])

    rng = L.rng_for(f"{slug}:{theme_name}")

    # Anchor placement is composition-driven.
    composition = theme.get("composition", "cluster")
    anchors = L.map_anchors(PLOT_W, plants, rng, composition=composition)
    base_y = PLOT_H - 14

    # Mark per-plant context that grammars may inspect (sumi-e accent,
    # appleton annotation gating, edo seed-index for alternating colors).
    if plants and composition == "asymmetric_corner":
        # one accent plant — pick a live one
        for p in plants:
            if p.get("stage") in ("budding", "evergreen"):
                p["_accent"] = True
                break
    for idx, p in enumerate(plants[:len(anchors)]):
        p["_seed_idx"] = idx
        if idx == 0 and p.get("stage") == "budding":
            p["_show_annot"] = True

    # Pull the species grammar from the theme. If missing, empty plot.
    grammar = (theme.get("species") or {}).get(species)
    plant_svg_parts: list[str] = []
    if grammar and anchors:
        for plant, ax in zip(plants, anchors):
            try:
                parts = grammar(plant, ax, base_y, palette, rng)
                if parts:
                    plant_svg_parts.extend(parts)
            except Exception:
                # Single plant failure mustn't crash the plot — render
                # leaves out, plot stays valid.
                continue
    if not plant_svg_parts:
        # Empty plot — soil tuft signals "nothing here yet" without alarm.
        plant_svg_parts.append(L.soil_tuft(PLOT_W * 0.5, base_y, kind="arc"))

    defs = theme.get("defs") or ""
    bg = theme["bg"](PLOT_W, PLOT_H, slug)
    fg = theme["fg"](PLOT_W, PLOT_H, slug)
    wrap = theme.get("wrap_attrs") or ""
    body = "".join(plant_svg_parts)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {PLOT_W} {PLOT_H}" '
        f'class="eos-g-plot" data-plot="{slug}" data-theme="{theme_name}">'
        f'<defs>{defs}</defs>'
        f'{bg}'
        f'<g class="eos-g-plants" {wrap}>{body}</g>'
        f'{fg}'
        f'</svg>'
    )


def render_mini(state: dict[str, Any], theme_name: str,
                width: int = 200, height: int = 80) -> str:
    """Compact 200×80 SVG strip — hub mini panel.

    One small column per plot, height encodes plant count, color from
    theme palette. Stays glanceable. No grammars, no chrome.
    """
    theme = _themes.get_theme(theme_name)
    pal = theme["palette"]
    plots = (state or {}).get("plots") or {}
    if not plots:
        return ""
    slugs = list(plots.keys())
    n = len(slugs)
    if n == 0:
        return ""
    max_plants = max(
        (len((plots[s] or {}).get("plants") or []) for s in slugs),
        default=1,
    )
    max_plants = max(1, max_plants)
    col_w = (width - 12) / n
    bars = []
    for i, s in enumerate(slugs):
        plants_n = len((plots[s] or {}).get("plants") or [])
        h = max(4, int(8 + (plants_n / max_plants) * (height - 20)))
        x = 6 + i * col_w + col_w * 0.2
        y = height - 8 - h
        w = col_w * 0.6
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" '
            f'rx="1" fill="{pal.get("leaf_alive", pal.get("ink", "#666"))}" '
            f'opacity="{0.45 + (plants_n / max_plants) * 0.45:.2f}"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'style="width:100%;height:{height}px;display:block;">'
        f'<rect width="{width}" height="{height}" fill="{pal["bg"]}"/>'
        f'<line x1="0" y1="{height - 6}" x2="{width}" y2="{height - 6}" '
        f'stroke="{pal.get("muted", pal.get("ink"))}" stroke-width="0.4" opacity="0.4"/>'
        f'{"".join(bars)}'
        f'</svg>'
    )
