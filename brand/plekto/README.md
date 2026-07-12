# Plekto brand assets

Brand mark for the Plekto distribution. Selected 2026-05-20 after 14
hand-crafted SVG iterations + 8 AI-generated FLUX candidates.

## Final mark — `icon.svg`

3x3 twill weave with a single amber accent spine.

- **Weave** strands (8 of 9): slate `#475569`, 9px round-capped
- **Spine** (V2 centre vertical): amber `#d97706`, 9px round-capped, continuous
- **Geometry**: 128×128 viewBox; strands at x/y = 44, 64, 84

The spine is the "over" strand — it passes on top of all three horizontals,
while V1 and V3 (the slate outer verticals) are broken at each intersection,
passing under. Reads as "one thread holds the structure together" — minimal
hint of weave + structure without forming a letter or imposing a literal
shape.

## PNG renders

Pre-rendered at the canonical icon sizes for direct use:

| File | Use |
|---|---|
| `icon_16px.png`   | Tab favicon (legacy browsers, embedded contexts) |
| `icon_32px.png`   | Tab favicon (default browser size) |
| `icon_64px.png`   | Windows Start menu, small thumbnails |
| `icon_128px.png`  | Mac dock (low-DPI), Linux desktop |
| `icon_256px.png`  | Windows tile, social card avatar |
| `icon_512px.png`  | OG image / Twitter card, large thumbnail |
| `icon_1024px.png` | Mac dock (Retina), app store hero |

Re-render with `python brand/plekto/_render_preview.py icon.svg <size>`.
For arbitrary sizes the SVG renders cleanly — the PNGs are convenience.

## Favicon usage

Modern browsers (Chrome, Edge, Firefox, Safari 14+) prefer SVG favicons.
On the Plekto landing page, link directly:

```html
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/icon_32px.png">
<link rel="apple-touch-icon" sizes="180x180" href="/icon_256px.png">
```

## Wordmark (not yet designed)

The icon is half the brand. Wordmark companion — "Plekto" set in a strong
typeface — pending. Candidates: Inter, IBM Plex Sans, Geist. Keep it
lightweight; pick one, set it, done.

## Helper scripts (developer-only)

- `_render_preview.py` — SVG → PNG via headless Chromium. Reusable for
  future distributions (PowerDesk etc.).
- `_ai_generate.py` — Plekto-specific FLUX prompts via ComfyUI. None of
  the 8 generated candidates were used; kept for future exploration or
  porting to PowerDesk / other brand projects.

## Rejected variants

See `_archive/` for the 11 abandoned hand-crafted concepts:
- 3 originals (A Borromean rings, B woven strands, C P-knot monogram)
- 4 weave-with-P attempts (all disasters — embedded P never read cleanly)
- 4 B1 variations (cross / thick / teal-spine / amber-warm-grid / diagonal)

The 8 AI-generated candidates (knot/rings/weave/braid families) were
deleted; reproduce via `_ai_generate.py` if needed.
