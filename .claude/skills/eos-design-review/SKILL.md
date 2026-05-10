---
name: eos-design-review
description: Review an EmptyOS app page from a frontend designer's perspective and propose fixes — bounded by the existing theme tokens, eos-components helpers, type/spacing/radius scales, and forbidden-pattern rules. Use when a page is technically compliant with the design language but feels flat / unbalanced / lifeless / hard to scan, or when the user says "review the design", "check the design", "make it look better", "this is too plain", "this feels off". DO NOT use this for: brand-island work (use frontend-design instead), full-system design-language compliance sweeps (use eos-ui-audit-and-consolidate), per-file code review (use eos-simplify), or generating new designs from scratch.
---

# EmptyOS Design Review

A designer's read on a page that's *already* in compliance — does it have hierarchy, focal point, rhythm, restraint vs density, where does the eye land, what feels lifeless — bounded by **only the existing tokens, components, type scale, spacing scale**.

This is the missing skill between:
- `eos-ui-audit-and-consolidate` (mechanical compliance — hex colors, native dialogs, mandatory rules)
- `frontend-design` (free creativity, distinctive aesthetics, brand islands)

It does not introduce new fonts, new palettes, brand islands, novel components. It just makes pages *better* within the system.

## When to use

- User says: "review the design", "check the design", "this is too plain", "this feels flat", "make it look better", "design feedback"
- A page just passed `/eos-simplify` or `/eos-ui-audit` (compliance is fine) but visually weak
- A new page was scaffolded from auto-UI patterns and needs a design pass before shipping
- After heavy structural changes (added/removed sections, rearranged controls)

## Inputs

Ask the user for:

1. **Target** — file path or app id (`apps/radio/pages/index.html`)
2. **Mode** — `review` (output only, propose-don't-edit) or `apply` (edit in place)
3. **Constraint reminders** (optional) — anything to be especially careful about (e.g. "this is the public/kiosk page")

If only the app id is given, scan its `pages/*.html`.

## Phase 1 — Read the page

Load the HTML + its inline `<style>` block. Render the page mentally as a designer would: top → bottom, what's the focal point, what's the second focal point, what's secondary chrome, what's tertiary, where does the eye get lost.

Cross-reference:
- `docs/FRONTEND-DESIGN-LANGUAGE.md` — the visual + interaction DNA
- `emptyos/web/static/theme.css` — available tokens (`--bg`, `--bg-card`, `--bg-hover`, `--text`, `--text-muted`, `--text-heading`, `--accent`, `--border`, `--border-strong`, `--shadow`, `--glow`, `--success`, `--danger`, `--radius-sm/md/lg`, `--font`, `--font-display`)
- `emptyos/web/static/eos-components.css` — available components and their canonical use
- The paired `app.py` — what data drives the page; the page's *purpose*

## Phase 1.5 — Classify the archetype

The seven questions are universal. The *expected answers* differ by page archetype. Classify the page first; the rubric in Phase 2 then has different defaults for each.

Detection signals (from page markup + paired `app.py`):

| Archetype | Strongest signals |
|---|---|
| **instrument** | `<audio>` / `<video>` / one big circular or radial control; one primary verb (play/start/record); no list rendering; manifest declares `capabilities` like `speak`/`listen`/`draw` |
| **list** | Heavy use of `EOS_UI.entityCard` or hand-rolled card-row classes; `vault_query()` / `list_*()` / `list_all()` in `app.py`; pagination, filters, sort controls |
| **dashboard** | `EOS_UI.statCards`, multiple `panel_*` methods, manifest `[[contributes.hub.panel]]`, multiple metric/donut renderers |
| **detail** | URL pattern `/<app>/<id>` or `?id=`; `vault_get_properties()` / `song_detail()` / `_find_note()`; back button; sidebar metadata |
| **form** | `<form>`, `EOS_UI.formModal` heavy; many `<input>`/`<textarea>`; one submit button; `[provides.settings]` manifest |
| **generator** | One prominent input + one big primary button + a result region; `self.think()` / `self.draw()` central; output replaces or follows input |

If the page is **mixed** (e.g. a list view with a hero stat-card on top), name both: `list + dashboard-banner`. The dominant archetype defines the primary rubric; the secondary one is allowed where it lives.

If the archetype is unclear, **ask the user** before proceeding — wrong rubric is worse than no rubric.

### Per-archetype defaults

Use these as expected answers when applying Phase 2:

| Question | instrument | list | dashboard | detail | form | generator |
|---|---|---|---|---|---|---|
| **focal** | One visual hero (cover, dial, waveform). Page IS the content. | No single hero — scan rhythm. Top filter / search bar at most. | Most-changed metric or first action. | Headline metadata block (title + subtitle + meta). | The first input field. Primary submit at bottom. | The input field, then the result. |
| **hierarchy** | 2 levels max: hero, transport. Everything else recedes. | Many parallel rows of equal weight; chrome (filter/sort) ranks above rows. | Tile size by importance; activity / events below tiles. | Title → meta → body. Sidebar is supplementary. | Field-group → field → label. Submit anchored. | Input → CTA → output. Three-step progression. |
| **rhythm** | Generous gaps; 1-2 dividers max. | Tight uniform rows; consistent row height. | Card grid rhythm; clear gutters. | Long readable column; quiet section breaks. | Field groups separated; one field per row. | Phases (input / generating / output) clearly distinct. |
| **weight** | One primary CTA (play); secondary verbs in pill cluster. | All rows equal. Filter chrome equal-weight to each other. | Tiles weighted by data importance; clickable cards visible. | One primary action max (Edit / Delete in toolbar). | One submit button. Cancel is ghost. | One big primary button. Ghost variants for retry/clear. |
| **density** | Sparse. Chrome should disappear. ≤ 7 controls visible. | Dense rows OK (15+); chrome (filter, search) is help, not noise. | Medium. Tile grid + activity feed. | Sparse content body. Metadata can be dense in sidebar. | Medium. Field groups breathe. | Sparse. Don't compete with output. |
| **affordance** | Each control obvious by shape. Glyph family consistent. | Row hover state visible; click affordance clear. | Cards clickable; hover lifts. | Few interactive targets, each clearly labeled. | Inputs only; no clickable text. | One button. Loading state when active. |
| **restraint** | Maximum. Title can be removed when data is present. | Less. Filters help users, keep them. | Medium. Don't over-label tiles. | High. Strip everything not about THIS entity. | Maximum. No decoration. | Maximum. Output is the only payoff. |

### Per-archetype refusals (auto-reject these proposals)

**instrument:**
- Adding filter bars, search inputs, list views, multi-item navigation
- Adding more than one primary action
- Adding "history" / "queue" / "library" surfaces inline (those are list archetypes — separate page)

**list:**
- Adding a hero element above the rows that competes for the eye
- Replacing rows with cards-with-images when the data isn't visual
- Adding a single primary action when the page is about *N items*

**dashboard:**
- Turning it into a list (use boards for that)
- Turning it into a single-purpose page (a hero stat is fine; a player isn't)
- Inline edit of stat values (those are summaries — edit happens in the source app)

**detail:**
- Bulk action toolbars (you're on ONE thing)
- Adding a list of similar items inline (link to the list page instead)
- Multiple primary actions (pick one)

**form:**
- Adding a hero or status panel before the fields
- Multi-step wizards when one form would do
- Inline help text longer than the field labels

**generator:**
- Persisting the result inline as if it were a list
- Adding "options" panels longer than the output
- Multiple primary buttons (pick the verb)

If a fix the user is asking for falls into the refusal list, surface to the user that the right move is a **different archetype** — link to the page that should host that capability, or propose a new page if none exists.

## Phase 2 — The seven-question critique

Apply each question. For each problem found, tag with one of the seven categories. Write down the finding before moving on.

### 1. `focal` — Where does the eye land first?
- Is there ONE clear visual hero? Or are several elements competing for it?
- If the hero is data-driven (cover art, key number, current value) — does it read clearly when the data is *missing* (idle / empty / loading)?
- Common bug: hero is the page title, not the data. The title is chrome.

### 2. `hierarchy` — Top three things by weight
- After the focal point, what's #2 and #3? Can you list them in 5 seconds without thinking?
- Are related lines (eyebrow / title / subtitle / attribution) grouped as a *block* or scattered as siblings with the same gap as everything else?
- Common bug: "metadata block" pieces (kind chip, title, subtitle) floating with equal gaps from each other AND from the unrelated controls below — reads as a list of strangers.

### 3. `rhythm` — Is the vertical rhythm varied or monotonous?
- Count back-to-back visually identical row treatments (same chip style, same divider, same height). More than 2 in a row = monotony.
- Are dividers used sparingly to mark *real* boundaries, or as punctuation between every group?
- Common bug: three section labels with three identical hairline rules in a row.

### 4. `weight` — Does each control's visual weight match its importance?
- Walk through every control. Rate its visual weight (1=barely there → 5=screaming). Compare to its role-importance (1=tertiary → 5=primary verb).
- Mismatches in either direction are bugs:
  - High-importance + low-weight = user can't find the action
  - Low-importance + high-weight = user is distracted by sleep timer when they came to play music
- Common bug: persona/DJ control given the same chip-row treatment as channels — when persona is more conceptually important (it's *who's hosting*, not *what's playing*).

### 5. `density` — Restrained, balanced, or cluttered?
- How many distinct interactive control types are visible at once? More than 5 = scan fatigue.
- Is the page mostly chrome (labels, dividers, borders) or mostly content?
- For a player / dashboard / single-purpose page: the page IS the content. Chrome should be invisible.
- Common bug: every section labeled, every group divided, every chip bordered — the chrome accumulates and crowds the content.

### 6. `affordance` — Are interactive elements visually consistent and discoverable?
- Pick one buttoned interaction. Find every other button on the page. Do they share visual vocabulary (shape, weight, hover state)?
- Pick one icon. Find every other icon. Are they all in the same family (all glyphs, all SVG, all CSS-drawn) or mixed (one emoji, two unicode glyphs, three CSS shapes)?
- Are non-obvious affordances (clickable text, dotted underline, hover-reveal) signaled before hover?
- Common bug: a single emoji icon among otherwise geometric glyphs reads as inconsistent.

### 7. `restraint` — What can be removed?
- For each visible element: would the page be worse without it? If "no" or "barely" → remove it.
- Labels especially — most labels exist because the designer didn't trust the data to speak. Trust the data first.
- Common bug: labels above chip rows where the chip *content* is self-explanatory (channel names, persona names, durations).

## Phase 2.5 — Theme coverage

EmptyOS ships ~7+ themes (`default`, `void-dark`, `warm-dark`, `nord`, `soft-light`, `sepia`, etc.). Every theme redefines the same tokens with different values, often with **wide spreads** — for example `--shadow` ranges from `rgba(0,0,0,0.03)` in soft-light to `rgba(0,0,0,0.4)` in void-dark, and `--glow` is `none` in some themes but a 20px-spread accent halo in others.

A page that's beautiful in one theme can dissolve, scream, or invert in another. The seven-question critique is theme-agnostic, but the **fixes** must hold in every theme.

### Token spread audit

Open `emptyos/web/static/theme.css` and identify, for the page under review, which tokens it uses *heavily*. For each, note the inter-theme spread:

| Token | Spread risk | What can go wrong |
|---|---|---|
| `--shadow` | ~13× opacity range | Heavy `box-shadow` reads as bold drop in light themes, invisible in some dark themes |
| `--glow` | `none` ↔ `0 0 20px ...` | Glow-driven focal points vanish in `none` themes (default, soft-light, void-dark) |
| `--border` | `rgba(...,0.06)` ↔ solid `#e4e4e0` | Hairline borders may be invisible in some themes |
| `--bg-card` | semi-transparent ↔ solid | A nested card-on-card layout flattens when both resolve to the same color |
| `--accent` | hue varies dramatically | A design relying on accent against tinted bg may have low contrast in one theme |
| `--accent-ink` | varies for inverse text | Inverse-on-accent text (`color: var(--accent-ink)`) needs its own token, not `var(--bg)` |
| `--text-heading` | sometimes equals `--text`, sometimes distinct | Type hierarchy that depends on heading-vs-body contrast may collapse |

### Failure patterns to flag

- **Shadow as hierarchy**: card depth comes ONLY from `box-shadow: 0 4px 24px var(--shadow)`. In soft-light (shadow opacity 0.03), the card looks pasted on. *Fix:* combine shadow with a `1px solid var(--border)` for depth that survives across themes.
- **Glow as focal**: hero defined by `box-shadow: 0 0 80px var(--glow)`. In themes where `--glow: none`, focal point disappears. *Fix:* the focal element must read clearly even when glow is absent — use shape, size, or accent fill as the primary signal; treat glow as a bonus.
- **Inverse text on accent**: `background: var(--accent); color: var(--bg);` looks fine in dark themes but breaks in light themes where `--bg` is also light. *Fix:* use `var(--accent-ink)` (which themes specifically for this purpose) — never `var(--bg)`.
- **Hardcoded shadow opacity**: `box-shadow: 0 4px 24px rgba(0,0,0,0.5)` instead of `var(--shadow)`. Fixed value ignores theme. *Fix:* always wrap shadow color in a token.
- **Borderless dividers**: relying on background contrast (`bg-card` on `bg`) for separation. In themes where they're nearly equal, divider vanishes. *Fix:* add a 1px border or use `--border` directly.
- **Animation on theme-conditional value**: animating `box-shadow` or `opacity` from a `--glow` value that's `none` in some themes will keyframe-jump. *Fix:* only animate properties that exist in every theme.

### Mental render in 3 themes

For each finding from Phase 2, mentally render the proposed fix in three theme contexts:

1. **A light theme** (soft-light or default-light) — shadow/glow weak; borders are *the* depth cue
2. **A dark theme** (void-dark or default-dark) — shadow strong, glow may or may not exist
3. **A tinted theme** (warm-dark, sepia, nord) — accent hue changes character; borders carry tint

If a fix fails in any of the three, revise. If it cannot be revised within tokens, flag it as **theme-fragile** and surface to the user — they may need to add a theme-conditional override, but that's a system change (out of scope for this skill).

### Programmatic check (when running in `apply` mode)

After applying fixes, manually toggle `document.body.className` through the available themes via DevTools and visually confirm:
- Focal point still reads
- Card boundaries still visible
- Inverse-text legibility (accent-fill chips / buttons)
- No element disappears

If a daemon's running, this can be partly automated by hitting `/api/presentation/state` patterns or theme-switch endpoints. Document any visible regressions in the report.

## Phase 2.6 — Mobile / breakpoint coverage

EmptyOS ships as a PWA tested on iPhone Safari, Android Chrome, desktop Chrome, and desktop Edge (per CLAUDE.md § Testing). A page that's beautiful on a 1440px laptop screen can be unusable on a 390px iPhone. The seven-question critique is breakpoint-agnostic, but the *answers* must hold on mobile.

The canonical EmptyOS mobile breakpoint is **640px** (per the mobile-nav-collapses memory rule). Some pages use 520px or 540px for tighter content; both are fine.

### Touch targets (the load-bearing one)

Per WCAG / iOS HIG, interactive controls must be **≥ 44 × 44 px** on touch devices. Inspect every interactive element:

- Primary buttons (`.eos-btn`, `.eos-btn-sm` are usually 32px or 36px tall — too small for primary actions on mobile unless given extra padding)
- Icon-only buttons (toolbar ⚙, mute, play) — easy to undersize
- Chip rows (channels, tags, filters) — chip padding < 7px on either axis fails the 44px minimum
- Slider thumbs — 12px is fine because the input itself has a larger hit area, but verify
- Hover-revealed actions (`.task-actions { opacity: 0; ... }`) — DO NOT EXIST on touch. The page must either show them always on mobile (`@media(max-width:640px) { opacity: 1 }`) or replace with a long-press / "more" button affordance.

### Horizontal overflow

At 360px / 390px / 414px viewport, nothing should require horizontal scroll. Common offenders:

- Fixed-width columns (e.g. `width: 280px` on a slider) without `max-width: 90%`
- Multi-column grids (`grid-template-columns: repeat(5, 1fr)`) that don't break to fewer columns
- Pre-formatted text or long monospaced strings without `overflow-wrap: break-word`
- Stat-card rows that fit 5 cards on desktop and 2 on mobile — must `repeat(auto-fit, minmax(...))` or have an explicit `@media` override

### EmptyOS mobile-specific patterns (load-bearing)

- **Nav collapses ≤640px** (memory: `feedback_mobile_nav_collapse.md`) — global EOS nav shows *Home + current + ⋯ drawer only*, never horizontal-scrolling tabs. App-internal tabs (`.eos-tabs`) follow the same constraint when there are >3.
- **Stacked layouts** — two-column `.rd-edit-grid` on radio's persona editor, sidebar+main in detail views, etc. all need `grid-template-columns: 1fr` at the breakpoint.
- **iOS form-zoom prevention** — text inputs with `font-size < 16px` auto-zoom on focus on iOS Safari, breaking the layout. EmptyOS standard input size is 14px, which IS below the threshold; verify the input parent forces 16px on mobile or use `font-size: max(16px, 1em)` on inputs.
- **Safe-area insets** — fixed-position elements (FABs, sticky headers, kiosk banners) must respect `env(safe-area-inset-bottom)` / `-top` for notched devices. Standard pattern: `bottom: calc(env(safe-area-inset-bottom, 0px) + 20px);`.
- **Sticky elements during scroll** — sticky `.sp-head` / `.sp-foot` in slide-out panels, sticky `<th>` in tables, sticky `.rd-cfg-tabs` in config pane. All need `top` / `bottom` values that account for safe-area on mobile.

### Per-archetype mobile defaults

| Archetype | Mobile rule |
|---|---|
| **instrument** | Hero (dial / waveform / cover) shrinks to fit but stays the visual anchor. Transport controls remain thumb-reachable bottom-half of screen. |
| **list** | Rows tighten but stay scannable. Filter chrome collapses to a sheet or drawer if it has >3 controls. Hover-revealed row actions become always-visible. |
| **dashboard** | Tile grid collapses from 5–6 columns → 2 columns at ≤640px, → 1 column at ≤400px. Hero stat tile may shrink type from 24px → 18px but stays a stat. |
| **detail** | Sidebar metadata becomes a collapsible accordion above or below the body. Tab nav for related entities collapses to a `<select>` or carousel. |
| **form** | Already mostly mobile-friendly (single column). Verify field labels stay above inputs (not inline-left), and submit stays anchored. |
| **generator** | Input + CTA + output stack vertically (already typical). Output region must be scrollable independently if it can grow. |

### Failure patterns to flag

- **Hover-only affordances without touch fallback** — most common
- **Fixed pixel widths** on inputs / sliders / containers that don't shrink with viewport
- **Multi-column grids without `auto-fit` or breakpoint overrides** — break at narrow widths
- **Type scale collapsed on mobile** — type that's 14px on desktop should be 14px on mobile too (don't shrink to 11px to fit, *change layout*)
- **Tappable elements smaller than 44×44** — even when visually OK, this fails the standard
- **Fixed-position controls without safe-area-inset** — clips behind iOS home indicator
- **Sticky panels with `top: 0`** — collide with iOS Safari's URL bar transitions
- **Input `font-size: 14px` (or smaller)** without an iOS zoom-prevention override — every form on the site triggers Safari zoom on focus
- **`<div onclick>` / `<aside onclick>` / `<section onclick>` without `cursor: pointer`** — iOS Safari **silently drops** the tap, no error. The element must have `cursor: pointer` declared somewhere in its class chain, OR an inline `style="cursor: pointer"`, OR be replaced with a `<button>`. Memory: `feedback_ios_safe_area_scanner.md`. (T-bug from PR #5.)
- **`100vh` instead of `100dvh`** for layout shells — the URL bar collapse on iOS jitters the layout. Use `100dvh` (dynamic viewport height) for full-screen surfaces.

### Use the scanner — single grep for all five iOS bugs

EmptyOS ships `scripts/check-ios-safe-area.py` which catches all five bug families (N/H/P/V/T) in <1s. Run it before declaring a page mobile-clean:

```bash
python scripts/check-ios-safe-area.py apps/<id>/pages/index.html
```

Exit 0 = clean, exit 1 = findings. The scanner runs in CI on every push, so a regression that escapes the design-review pass will fail the build later — but catching it here is cheaper. The opt-out comment `/* eos-ios-ok: V — reason */` in the rule body suppresses the check when the deviation is intentional (rationale required).

### Mental render at 3 widths

For each finding from Phase 2, mentally render the proposed fix at:

1. **390px (iPhone 13/14/15 standard)** — the workhorse mobile width
2. **414px (iPhone 14 Plus / Pro Max)** — slightly more breathing room
3. **640px (the breakpoint) and 768px (small tablet)** — where the layout transitions

If a fix fails at any of these, revise. If a fix needs different markup at mobile vs desktop, that's a markup-aware fix and stays in scope — but flag it so the user knows the structural change.

### Programmatic check (in `apply` mode)

After applying fixes, open Chrome DevTools → device toolbar → cycle through:
- iPhone 14 (390 × 844)
- Pixel 7 (412 × 915)
- iPad Mini (768 × 1024)

Verify:
- No horizontal scroll
- Every primary CTA is thumb-reachable (lower 60% of screen, ≥ 44 × 44)
- Hover-revealed actions are visible
- Sticky elements don't clip with safe-area

If a daemon's running, append `?width=390` to a debug URL or use the EmptyOS PWA mobile preview tooling.

## Phase 3 — Propose fixes within tokens only

For each finding, write a fix. Each fix MUST:

1. **Use only existing tokens** — `var(--accent)` not `#4a90e2`; `var(--radius-md)` not `8px`; `var(--font)` not `'Inter'`
2. **Use only existing helpers** — `EOS_UI.statCards`, `.eos-badge`, `.eos-entity-card`, etc. — not new ones
3. **Stay on the type/spacing/radius scale** — `{10, 11, 12, 13, 14, 15, 22, 28, 32, 48}px` for type; `{0, 2, 4, 8, 12, 16, 24, 32, 48}px` for spacing; `{0, 4, 6, 8, 14, 999}px` for radius
4. **Not introduce a new font, palette, or visual language** — the moment a fix needs that, the right answer is a brand-island review (run `frontend-design` instead, with explicit user approval)

### Refusal list — auto-reject these proposals

If the design problem feels like it *needs* one of these, surface to the user that this skill can't fix it:

- **Custom typography** — Google Fonts, fancy serifs, monospace for chrome — no
- **Custom palette** — non-token colors, even "just for this one accent" — no
- **Brand-island treatments** — film grain, scanlines, custom textures — no
- **New `EOS_UI.*` helpers** — extraction is its own session, run when ≥5 apps need it
- **Wheel/dimension UI** — the wellbeing wheel is a silent rubric (DL § Forbidden Patterns)
- **Engagement-bait** — streaks, fire emojis, notification chrome (DL § Forbidden Patterns)
- **Modal AI takeovers** — full-screen chat overlays (DL § Forbidden Patterns)
- **Third-party brand names in user-facing strings** — generic terms only

## Phase 4 — Output format

For `mode = review` (no edits), produce:

```
EOS Design Review — apps/<app>/pages/<file>

Archetype: <instrument | list | dashboard | detail | form | generator>
            <one-sentence justification — what signals classify it>

## Read

<3-5 sentences describing the visual flow you see when scanning top to bottom>

## Findings

| # | Tag | Issue | Proposed fix |
|---|---|---|---|
| 1 | hierarchy | Eyebrow / title / subtitle floating as siblings | Group into .rd-meta block with gap-6, single max-width |
| 2 | rhythm | Three identical hairline dividers in 200px | Replace with single soft divider between content + control blocks |
| ...

## Refused

- <any fix that wanted custom typography / palette / brand island, with reason>

## Recommended order

<smallest-blast-radius first; structural moves last>
```

For `mode = apply`, edit in place per the proposed fixes table, run smoke checks, and produce the same output but with each finding marked `[FIXED]` / `[FLAGGED]`.

## Phase 5 — Verify

- `grep -E "[: ,(]#[0-9a-fA-F]{6}\b"` on the page → 0 hex colors (per DL-1, brand-islands excepted but this skill never produces them)
- Type sizes used → all within scale
- Spacing values used → all within scale
- No `alert()` / `confirm()` / `prompt()` introduced
- No new font families loaded
- No emoji added that weren't there before (unless replacing inconsistent emoji with CSS shapes)
- Per `eos-ui-audit-and-consolidate` Phase 1c: page's `S-1 adoption ratio` should not have decreased; inline `<style>` lines (`S-2 budget`) should not have grown beyond ~60 lines unless the fix removed inline rules elsewhere
- **Theme coverage**: every property using `--shadow`, `--glow`, `--border`, accent-vs-bg pairings has been mentally rendered in at least one light, one dark, and one tinted theme. No `rgba()` or hex shadow values left unwrapped. No `color: var(--bg)` used for inverse text on accent (use `var(--accent-ink)`).
- **Theme bootstrap present**: `grep -E "theme-' \+ |localStorage.*eos-theme|/static/eos\.js" pages/index.html` must match at least once. EmptyOS theme tokens live inside `.theme-X` selectors on `<html>`; without a bootstrap, every `var(--bg-card)` / `var(--border-strong)` / `var(--accent)` resolves to nothing and the page renders with invisible cards, borders, and slider tracks. Public-facing pages prefer the inline `<script>(function(){var t=localStorage.getItem('eos-theme')||'eos';document.documentElement.className='theme-'+t;})();</script>` in `<head>`; authed pages can also load `/static/eos.js` which applies it at lines 17–18.
- **Mobile breakpoint coverage**: `grep -E "@media.*max-width.*(640|520|540|400)px" pages/index.html` must match at least once UNLESS the page is genuinely viewport-agnostic (rare). Verify hover-only affordances (`opacity: 0; ... :hover { opacity: 1 }`) have a `@media(max-width:640px) { opacity: 1 }` companion. Verify text inputs that ship below 16px font-size aren't on the page (or are wrapped in `font-size: 16px` on mobile to suppress iOS zoom-on-focus). Verify fixed-position elements use `env(safe-area-inset-bottom)` patterns.

## Safety

- **Don't redesign — refine.** A fix that requires rewriting >40% of the page is a redesign; surface to user and stop.
- **Preserve behavior.** Every JS handler, every API call, every state machine continues to work. Only CSS + minor markup reshuffling.
- **Small commits.** If you're applying multiple fixes, group them by category (`hierarchy fixes`, `affordance fixes`) and keep each group one diff.
- **Don't touch `apps/personal/`** unless explicitly scoped.
- **Don't add new sections to the page** — this skill removes/regroups existing content, doesn't invent new content.

## See also

- `docs/FRONTEND-DESIGN-LANGUAGE.md` — visual + interaction DNA
- `emptyos/web/static/theme.css` — token list
- `emptyos/web/static/eos-components.{css,js}` — helper library
- `.claude/skills/eos-ui-audit-and-consolidate/SKILL.md` — for system-wide compliance sweeps
- `.claude/skills/eos-simplify/SKILL.md` — for per-file code review (capabilities, prompts, branding)
- `frontend-design:frontend-design` — for *creating* distinctive interfaces (the opposite mode; brand islands)
