---
version: alpha
name: EmptyOS
description: |
  Visual identity tokens for EmptyOS — a mind companion that thinks and creates
  with the user, not for them. The product looks like a well-designed reading
  app, not like Material/iOS/Linear. This file is the machine-readable token
  contract; docs/FRONTEND-DESIGN-LANGUAGE.md is the human-readable language
  (states, AI-surface treatment, motion philosophy, refused patterns).
  Source of truth: emptyos/web/static/theme.css.
  Regenerate with: python scripts/gen-design-md.py

colors:
  # Base — eos signature theme (warm stone paper, quiet ink, violet thread)
  bg:             "#ede9e3"
  bg-surface:     "#e4e0d8"
  bg-card:        "#f8f6f2"
  bg-card-hover:  "#faf9f6"
  bg-input:       "#f8f6f2"

  text:            "#2e2e36"
  text-heading:    "#1a1a20"
  text-secondary:  "#5c5c68"
  text-muted:      "#8e8e9a"

  border:         "#d6d2ca"
  border-strong:  "#c4c0b6"

  accent:         "#6c5ce7"
  accent-dim:     "#8b80f0"
  accent-ink:     "#ffffff"

  # Semantic data colors — used by intent, never decoratively.
  red:     "#d44040"    # overdue / error
  amber:   "#d4a017"   # warning / stale
  green:   "#27ae76"   # done / success
  blue:    "#3b82f6"     # info / pending
  purple:  "#a855f7"     # rare / special

typography:
  display:
    fontFamily: "DM Sans"
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  h1:
    fontFamily: "DM Sans"
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  h2:
    fontFamily: "DM Sans"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  prose:
    fontFamily: "DM Sans"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  body:
    fontFamily: "DM Sans"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  small:
    fontFamily: "DM Sans"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  meta:
    fontFamily: "DM Sans"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "0.14em"   # all-caps labels only
  mono:
    fontFamily: "IBM Plex Mono"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.3

rounded:
  sm:   "6px"
  base: "8px"   # inputs, buttons
  lg:   "14px"  # cards, panels, modals
  pill: "999px"

spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "24px"
  "6": "32px"
  "7": "48px"

motion:
  duration:
    fast:  "120ms"   # hover, focus
    base:  "200ms"   # default
    slide: "250ms"   # drawers, slide panels
    max:   "300ms"   # upper bound for visible motion
  easing:
    base:   "ease"
    out:    "ease-out"
    spring: "cubic-bezier(0.175, 0.885, 0.32, 1.275)"

layout:
  maxProse: "720px"   # journal, notes, articles
  maxApp:   "1100px"  # dashboards, lists
  edgePadMobile:  "16px"
  edgePadDesktop: "24px"
  breakpoints:
    grid:    "500px"   # content grids collapse
    nav:     "640px"   # nav chrome collapses
    sidebar: "900px"   # sidebar layouts collapse

components:
  button:
    backgroundColor: "{colors.accent}"
    textColor:       "{colors.accent-ink}"
    rounded:         "{rounded.base}"
    padding:         "8px 20px"
    typography:      "{typography.body}"
  buttonGhost:
    backgroundColor: "transparent"
    textColor:       "{colors.text-secondary}"
    rounded:         "{rounded.base}"
    padding:         "8px 20px"
  card:
    backgroundColor: "{colors.bg-card}"
    rounded:         "{rounded.lg}"
    padding:         "{spacing.4}"
  input:
    backgroundColor: "{colors.bg-input}"
    textColor:       "{colors.text}"
    rounded:         "{rounded.base}"
    padding:         "8px 12px"
    typography:      "{typography.body}"
  badge:
    rounded:    "{rounded.sm}"
    padding:    "2px 10px"
    typography: "{typography.meta}"
  navBar:
    backgroundColor: "{colors.bg}"   # tinted with 92% + backdrop blur at runtime
    height:          "46px"

themes:
  - eos          # default — warm stone paper, violet thread (tokens above)
  - void-dark    # pure black, minimal
  - warm-dark    # amber lantern
  - nord         # arctic twilight
  - soft-light   # paper daylight
---

# EmptyOS — DESIGN.md

> The visual and interaction DNA of EmptyOS. If two apps built by two contributors don't feel like the same product, this file (or its companion `docs/FRONTEND-DESIGN-LANGUAGE.md`) is out of date or not being followed.

This file is the **machine-readable token contract**. The frontmatter above is the source of truth for AI coding agents. The runtime source of truth is `emptyos/web/static/theme.css`; this file mirrors it. For everything tokens cannot encode — state treatments, AI-surface visual rules, motion philosophy, refused patterns — see [`docs/FRONTEND-DESIGN-LANGUAGE.md`](docs/FRONTEND-DESIGN-LANGUAGE.md).

## Overview

EmptyOS looks like a **well-designed reading app**, not like Material, iOS, or Linear.

- **Ground:** warm off-white. Never pure white.
- **Accent:** a single color (violet in the signature `eos` theme), used sparingly — links, current-state, primary buttons, focus rings.
- **Type:** DM Sans for prose and headings; IBM Plex Mono for data, timestamps, paths, FAB labels, keyboard hints.
- **Shape:** rounded everything — `rounded.base` for inputs/buttons, `rounded.lg` for cards and panels. No sharp corners.
- **Depth:** soft shadows over hard borders.
- **Density:** generous whitespace. A page that feels slightly empty is correct; a page that feels full is wrong.

## Colors

Every theme defines the same token names. The frontmatter above lists the **`eos` signature theme**; the four alternates (`void-dark`, `warm-dark`, `nord`, `soft-light`) live in `theme.css` and override the same keys.

- **Accent is rare.** A page with more than ~5% accent pixels is wrong.
- **Never two accents in one view.** The theme picks one; pages don't introduce a second.
- **Data colors are semantic, not decorative.** `red` overdue/error · `amber` warning/stale · `green` done/success · `blue` info/pending · `purple` rare/special. Using `red` for visual pop without a semantic reason is a bug.
- **Never color a whole card background** except for alerts. Tint, don't flood.
- **Borders carry meaning.** Default = neutral. `border-strong` = hover/focus. Accent = current/selected. Red = invalid.

## Typography

One scale, no exceptions. See `typography.*` tokens. Rules:

- **All-caps only for `meta` (≤11px) labels** with `+0.06em` letter-spacing. Never elsewhere.
- **Mono is semantic** — it signals "this is data, not prose." Use for timestamps, file paths, numeric IDs, FAB labels, keyboard shortcuts. Never for headings or body prose.
- Weights: `400` body / `500` emphasis / `600` labels+buttons / `700+` display only.
- Line height: `1.5` prose, `1.3` UI copy, `1.2` headings, `1.0` single-line numbers.

## Layout

- **Max content width:** `layout.maxProse` (720px) for reading surfaces; `layout.maxApp` (1100px) for dashboards. Never edge-to-edge on desktop.
- **Spacing scale:** `spacing.1`–`spacing.7` (4 / 8 / 12 / 16 / 24 / 32 / 48). Nothing in between. If you need 10, round to 8 or 12.
- **Vertical rhythm:** sections 24–32px apart · cards within a section 12px · items within a card 8px · label→value 4px.
- **Grid:** 1-col mobile / 2-col tablet / up to 4-col desktop. Breakpoints are in `layout.breakpoints`.
- **Edges:** 16px side padding on mobile, 24px on desktop. Never less on mobile.

## Elevation

Soft shadows over hard borders. Floating layers (cards on hover, drawers, modals, FABs) get `0 4px 20px var(--shadow)` with the theme's `--shadow` color. Non-floating surfaces stay flat. No drop-shadow-everything.

## Shapes

- `rounded.sm` (6px) — small chips, inline tags
- `rounded.base` (8px) — inputs, buttons, small cards
- `rounded.lg` (14px) — cards, modals, panels
- `rounded.pill` (999px) — FAB pills, pill badges

If you reach for a radius that isn't in this scale, the language is being broken.

## Components

The frontmatter `components.*` block is the **token contract** for primitives. The actual implementations live in `emptyos/web/static/eos-components.{css,js}` and are exposed as `EOS_UI.*` helpers (modal, formModal, statCards, confirm, entityCard, emptyState, errorState, provenance). Use those before hand-rolling.

## Motion

Motion is a whisper, not a performance.

- Hover/focus: `motion.duration.fast` (120ms)
- Most transitions: `motion.duration.base` (200ms)
- Slide panels: `motion.duration.slide` (250ms)
- Hard upper bound: `motion.duration.max` (300ms), except the hands-free pulse which is intentionally 0.8–1.4s
- Spring easing only for FAB/drawer; everything else `ease` or `ease-out`
- Animate `transform` and `opacity` only — never `width/height/top/left`
- Honor `prefers-reduced-motion: reduce`

## Do's and Don'ts

**Do**
- Use tokens. Never hardcode a hex color. Never invent a spacing value.
- Use `EOS_UI.*` primitives before hand-rolling components.
- Show provenance on every AI-authored block (`.eos-badge-provenance` + `local`/`cloud`/`user`).
- Honor every state — default, loading, empty, error, offline.

**Don't**
- No second accent color. No gradient backgrounds. No off-scale radius (8/14/999 only).
- No `alert()` / `confirm()` / `prompt()` — use `EOS_UI.confirm` / `EOS_UI.toast` / `EOS_UI.formModal`.
- No modal AI takeovers. AI chrome docks (right drawer, bottom-right FAB), never blocks.
- No hidden-cost cloud calls — visible *before* sending.
- No third-party brand names in app UIs ("markdown vault", not "Obsidian").
- No engagement-bait streaks or notifications. No wellbeing-wheel UI (it's a silent rubric, not a feature).
- No decorative gradients or animated backgrounds. No custom scrollbars or fake cursors.

## See also

- [`docs/FRONTEND-DESIGN-LANGUAGE.md`](docs/FRONTEND-DESIGN-LANGUAGE.md) — full DNA: AI-surface treatment, state treatments, interaction patterns, refused patterns, three decision tests
- [`emptyos/web/static/theme.css`](emptyos/web/static/theme.css) — runtime token source, all five themes
- [`emptyos/web/static/eos-components.css`](emptyos/web/static/eos-components.css) + [`.js`](emptyos/web/static/eos-components.js) — component primitives
- [`CLAUDE.md` § Shared Frontend](CLAUDE.md) — canonical list of `EOS_UI.*` helpers
