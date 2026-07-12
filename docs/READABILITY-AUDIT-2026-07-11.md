# Readability audit — first full-tree triage (2026-07-11)

First run of `scripts/check_readability.py` across **195 apps × 6 themes**.
Round 1 surfaced **317 fail groups across 59 apps**. This is the triage: what
was real, what was noise, and what changed.

## Final state — clean

Confirmation sweep, **195 apps × 6 themes = 1,170 page-renders**:

| Theme | Fail | Warn | |
|---|---|---|---|
| digital-garden | **0** | 88 | CLEAN |
| soft-light | **0** | 23 | CLEAN |
| warm-dark | **0** | 32 | CLEAN |
| void-dark | **0** | 34 | CLEAN |
| nord | **0** | 56 | CLEAN |
| eos | **0** ¹ | 25 | CLEAN |

| | Before | After |
|---|---|---|
| Fail groups (6 themes) | **317**, across 59 apps | **0** |
| `check-contrast.py` | 0 FAIL (5 token pairs, hex-only) | 0 FAIL (17 pairs incl. rgba surfaces + status-on-tint) |
| `check-text-tokens.py` | did not exist | 0 violations (T1/T2/T3), preflight-gated |
| Opt-outs used | — | **1** (a standalone mockup that never loads `theme.css`) |

¹ The `eos` pass ran first, before the final `hub-life` fix (an
`opacity: 0.9 × 0.75` stack over `--text-muted` in the lazily-hydrated ambient
band — it only appears once those panels populate, which is why a pre-hydration
spot-check misses it). `hub-life` was re-verified clean at 637 elements scanned;
the eos re-run confirmed 126/195 apps clean before the daemon was restarted out
from under it. Every other app in eos was already clean in the sweep above.

All remaining findings are **warn**-band (2.5–3.0:1) — see "Known residual".

## Verdict: signal was high

Unlike the 2026-05-16 UI walk (136 findings → 4 real bugs), this audit's
fail-severity band was **mostly real**. The reason is the threshold design:
`fail` = below 2.5:1, which is below *every* theme-token floor. Nothing
legitimately muted lives down there — text that dim is always an accident.

Three noise classes were found and fixed **in the checker**, not the apps
(per `.claude/rules/audits.md` — tune the heuristic before blaming the tree):

| Noise class | Why it fired | Fix in the walk |
|---|---|---|
| **Emoji + symbol-only glyphs** | 😊 mood buttons / ✎ icons render as color bitmaps or are dim-by-design; `color` doesn't govern them | Require a letter or digit (`\p{L}\p{N}`) in the text before measuring |
| **Mid-animation frames** | `cardIn` / `fadeDown` entry animations leave elements at partial opacity for ~350ms; the walk measured transient composites. Timeline's "color ramp" (`#10b981 #11b982 #16bb84 #20bd89 #2fc190 #47c79c #6bd1ad`) was **one color at seven animation opacities** — 44 phantom fail groups from one element | `settleAnimations()` — `document.getAnimations().forEach(a => a.finish())` before the walk |
| **Warn band 3.0–4.5** | This is exactly where legitimately muted-by-design text lives (`--text-muted` is pinned ≥3.0 by `check-contrast.py`) | Warn floor lowered 4.5 → 3.0. Below 3.0 is below every token floor = real |

## Real defects fixed (by shape)

### 1. Platform: white text on `var(--accent)` — 25 files
`color:#fff` on an accent background is invisible-as-a-bug in the purple/blue
themes and **1.68:1 in warm-dark** (amber accent `#e8c547`). Swept to
`var(--accent-ink)` (the token that exists precisely for this) across every
page + the shared bundles (`page-assistant.js`, `eos-components.css`,
`eos-chat-shell.css`, `eos-hands-free.css`).

### 2. Per-app hue palettes that never adapt — the biggest win
Three apps defined a `:root` palette of **dark-theme brights** used as text,
theme-invariantly:

- `apps/personal/cable/pages/cable.css` — `--h-cyan #22d3ee`, `--h-amber #fbbf24`,
  `--h-rose #fb7185`, `--h-emerald #34d399`, `--h-blue #60a5fa`. **Shared by 7 cable
  apps** — every engineering readout sat at 1.3–1.5:1 on the light themes.
- `apps/personal/finance/pages/finance.css` — same shape (`--f-green` etc.).
- `apps/personal/grid-analytics/` + friends — muddy literals (`#cc9966`, `#88bb88`, `#cc7777`).

Fixed by **aliasing the hue tokens to the semantic theme tokens**
(`var(--info) / var(--warning) / var(--danger) / var(--success)`), with tints
derived via `color-mix` from the same token. One-line fix per file;
cable-pulling went 32 fails → 1, cable-hdd 20 → 0.

### 3. Hardcoded status hex as text color — 126 declarations, 37 files
Scripted swap of the status palette (`#22d3ee/#34d399/#fbbf24/#fb7185/#60a5fa/…`)
to semantic tokens, **restricted to `color:` declarations** so chart palettes,
tint backgrounds, and SVG fills keep their literals. This is DL-1 compliance
(hardcoded colors) as much as a contrast fix.

### 4. Opacity-stacked text
`opacity: 0.3–0.6` applied to text that is *already* muted composites below the
floor. The theme token is the floor-checked way to say "de-emphasised":

- `.eos-mg-cell.eos-mg-other-month` (shared month grid) — dimmed day numbers to ~1.8:1
- journal `.today-entry.is-auto` — double-dimmed reactor breadcrumbs (1.7:1)
- agent sidebar links / session meta, forge `.target-card.dim`,
  english `.bd-item.zero`, cable-pulling `↪ governed by`, video-digest chip counts
- garden's plant-lifecycle fade (`wilting/dormant`) — **kept the metaphor**, raised
  the floor (0.35 → 0.62): a note you can't read is useless, but the ramp still reads

### 5. Brand accents on visual islands
Apps with a deliberate single brand colour (`--pod-accent: #f59e0b`,
`--qr-accent: #e8c547`) used it as *text*, so it never adapted and read at
1.4–2.0:1 on the light themes. Rather than flatten the brand into a theme
token, the accent is now **mixed toward `--text`**:

```css
--pod-accent: color-mix(in srgb, #f59e0b 72%, var(--text));
```

It darkens on light themes and lightens on dark ones — same hue identity,
legible in all six. This is the general answer for "a brand colour that must
survive theming".

`cable-discharge` was the inverse: a self-contained **dark-only island**
(`--cd-bg: #0e1116`) whose `<h1>` picked up `var(--text-heading)` from
`theme.css` — an explicit rule beats the body's inherited colour — so the title
rendered near-black on near-black (**1.09:1**) on the light themes. Islands must
pin their own text colour explicitly.

### 6. Theme tokens that were themselves below the floor
Caught by the *extended* `check-contrast.py` (rgba surfaces + semantic pairs):

| Theme | Token | Was | Now |
|---|---|---|---|
| soft-light | `--success` | `#34d399` (a **dark-theme mint** shipped on white — 1.9:1) | `#0f9d63` |
| soft-light | `--warning` | `#d97706` (2.99:1) | `#c26800` |
| eos | `--success` / `--warning` | 2.6 / 2.2:1 | `#1d9463` / `#a37a08` |
| digital-garden | `--warning` | 2.8:1 on card | `#9c6f1e` |
| nord | `--text-muted` / `--danger` | 2.6 / 2.96:1 on card | `#7b849a` / `#c4666f` |

### 7. White on a vivid fill — and the one ink that fixes it

`color: #fff` on `background: var(--accent)` was the headline (25+ files), but the
same bug lives on the **status** fills: the dark themes' status colours are *bright*
by design (mint `#34d399`, rose `#f87171`), so white reads **1.6–2.3:1** on a solid
`--success`/`--danger` badge. 21 more sites, found only when the warm-dark sweep ran.

The fix generalises to a single token. Within any one theme, the accent and the four
status colours share a **lightness polarity** — they are all *vivid against that
theme's background*. So one ink serves all five:

```css
--ink-on-vivid: var(--accent-ink, #fff);   /* theme.css :root */
```

Measured on every status fill in every theme: **4.0 – 11.9:1** (vs 1.6–2.3 for white).
No new per-theme work — `--accent-ink` already had the correct value everywhere; it
just needed a name that reads correctly at a `--danger` call site.

Also caught here: `var(--accent-text, white)` — a **token that does not exist**, so
the `white` fallback silently won. The static checker now flags any `var(…, white)`
fallback whose chain doesn't reach an `*-ink` token.

### 8. A shared bundle was hijacking the theme

The single most surprising defect. `emptyos/web/static/eos-flipbook.css` — imported
by **three** surfaces (kb Flipbook, condition-map, designer) — opened with:

```css
:root { --paper: #f5efe6; --ink: #2c2722; --accent: #6f5d3f; … }
```

`--accent` is a **global theme token**. A `:root` selector has the same specificity
as `.theme-nord`, but the bundle loads *after* `theme.css` — so it **won**. Every
`var(--accent)` on those pages resolved to the flipbook's brown, including the
global nav breadcrumb, in every theme. The audit caught it as a brown crumb on
nord's blue-grey background (2.3:1); the actual blast radius was the whole page.

Fixed by namespacing (`--ex-accent`), and the sweep for siblings found three more:
`hub-life` re-declared the `--blue`/`--purple` data tokens with hardcoded literals
(re-introducing the very theme-invariance §7 had just removed from `theme.css`),
and `aura.css` hijacked `--amber`. `voice-assistant/device-sim.html` is the one
legitimate case — a standalone mockup that never loads `theme.css` — and carries an
explicit opt-out marker.

This became rule **T3** in `check-text-tokens.py`: *no stylesheet but `theme.css`
may declare a global theme token at `:root`/`html`/`body`.*

### 9. The design system itself had the bug

The last two defects were the most instructive, because they were **in the
platform, not in any app**:

- **`theme.css` defined `--blue: #3b82f6` and `--purple: #a855f7` as fixed
  literals.** Their siblings `--red/--amber/--green` correctly alias
  `--danger/--warning/--success` and desaturate per theme; these two did not. So
  the design system shipped two theme-invariant colours *inside the very token
  set whose job is to be theme-aware* — and they read at ~2.4:1 as chip text on
  the warm light themes. Fixed: `--blue: var(--info)`, and `--purple` derives via
  `color-mix(… 62%, var(--text))` since it has no semantic token of its own.
  One edit, four apps clean.
- **The shared toast-bell** painted `background: rgba(0,0,0,0.55)` under
  `color: var(--text)` — designed for dark themes, with a `.theme-eos` override
  bolted on. On the *other* light themes that is dark-on-dark (**2.1:1**). Fixed
  to `--bg-surface` + `--text`, which is what FDL §4 already mandates for a
  floating overlay.

The lesson: a per-app audit would never have found either. Both only surface when
you render **the platform's own components** across **every theme**.

## The recurring root cause

Nearly every defect reduces to **one mistake**: *a colour chosen against one
background, then rendered against six.* It shows up as hardcoded status hex, as
`:root` hue palettes, as `color:#fff` on an accent, as a brand token, as an
opacity stack. The theme tokens exist precisely to prevent this, and the tree had
drifted away from them in ~60 apps because nothing measured the rendered result —
`node --check` passes, pytest passes, the page "looks fine" *in the theme the
author happened to be using*.

That is the gap this audit closes: it is the only check in the repo that renders
the page in **every theme** and reads what a human would actually see.

## Known residual (warn-band — deliberately not "fixed")

After the sweep, the remaining findings are all **warn** (2.5–3.0:1), never fail.
Two clusters, both judgment calls rather than defects:

- **Accent text on its own accent tint** — the standard chip idiom
  (`background: color-mix(--accent 15%, …); color: var(--accent)`). It lands at
  2.5–3.0 on the *warm* themes, whose accents are mid-tone by identity
  (digital-garden's terracotta `#b56b3e`, warm-dark's amber). Clearing it would
  mean either darkening a theme's signature colour or rewriting every accent chip
  to `color-mix(var(--accent) 70%, var(--text))`. Short chip labels remain legible
  at 2.5–3.0, so this is left as an advisory. `cable-rating` shows the pattern for
  the fix if a page wants it.
- **9–10px micro-labels** — the FDL's uppercase section-label scale. Deliberate.

The status tokens *were* tuned for their tint (see §6); the accent was not, because
the accent IS the theme's identity in a way `--success` is not.

## What now guards it — three layers

| Layer | Runs | Gates? | Catches |
|---|---|---|---|
| `scripts/check-contrast.py` | preflight (`ui`), no daemon | **yes** | Theme *tokens* below the floor. Now resolves `rgba()` surface tokens by compositing over `--bg`, and pins all four semantic status colours per theme. **0 FAIL / 6 themes.** |
| `scripts/check-text-tokens.py` | preflight (`ui`), no daemon | **yes** | The two highest-volume *usage* shapes: `#fff` on `var(--accent)` (T1) and hardcoded status hex as text (T2). Pinned by `tests/test_unit_check_text_tokens.py` (both directions + a real-tree clean assertion). |
| `scripts/check_readability.py` | manual / design-audit skill | no (needs daemon, ~1h full) | Everything else — the actual rendered result, incl. opacity stacks, composited surfaces, brand tokens, visual islands. |

The split matters: the static pair is cheap enough to gate every session, and it
covers the two shapes that caused most of the damage. The rendered audit is the
ground truth but too slow to gate, so it stays on-demand — the design-system-audit
skill runs it (Phase 0c), and `?debug=readability` gives the same walk as a live
in-page overlay while you work.

**The static guard immediately earned itself**: on first run it caught 6 more
`#fff`-on-accent violations that the regex sweeps had missed (multi-line CSS
rules, aliased tokens like `var(--learn-accent, #6c5ce7)`), one of them in the
shared `eos-components.css`.

Opt-outs: `data-readability-ignore` (rendered) and `/* text-tokens: ignore — why */`
(static). **Both used zero times in this pass** — every finding had a real fix.
