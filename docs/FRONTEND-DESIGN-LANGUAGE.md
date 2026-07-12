# Frontend Design Language

> The visual and interaction DNA of an EmptyOS page. If two apps built by two different contributors don't feel like the same product, this doc is out of date or not being followed.

This is **not** a component contract (that's `emptyos/web/static/eos-components.{css,js}` + its helpers). This is the language that tells you *how a page should look and behave* before you reach for a component.

## 0. System layers

EmptyOS UI has five layers. Keep changes in the narrowest layer that owns the problem:

- **Tokens**: `theme.css` and generated `DESIGN.md` own color, type, spacing, radius, focus, motion, and theme behavior.
- **Design language**: this document owns product taste, state treatment, AI surfaces, responsive behavior, and brand-island boundaries.
- **Component contract**: `emptyos/web/static/eos-components.css` and `.js` own reusable primitives and `EOS_UI.*` helpers.
- **Surface patterns**: repeated workbench/editor surfaces may add shared families inside `eos-components.css`; the first such family is `.eos-tool-*`.
- **App-local CSS**: app pages own only app-specific layout, data visualization, canvas styling, and domain drawings.

Do not create category-specific visual forks such as `eos-engineering-ui.css`. Engineering, writing, learning, and ops apps remain siblings unless they are an explicit brand island. Use surface patterns, not app categories, to decide when the shared library needs a new primitive.

## 1. Visual identity

EmptyOS looks like a well-designed reading app, not like Material, iOS, or Linear.

- **Ground:** warm off-white `#f5f2ed` (or the three theme alternates — dark, amber, nord). Never pure white.
- **Accent:** single purple `#6c5ce7`. Used sparingly — links, current-state, primary buttons, focus rings.
- **Type:** DM Sans for prose and headings; JetBrains Mono for data, timestamps, paths, FAB labels, keyboard hints.
- **Shape:** rounded everything — 8px for inputs/buttons, 14px for cards and panels, 999px for pills/avatars. 4px and 6px are acceptable for small chips, badges, and tight inline tags; 10px and 12px for elements that sit visually between input and card (e.g. button-shaped tags, embedded chips). No sharp corners. No 3/5/7/9/16+ — those are drift.
- **Depth:** soft shadows (`0 4px 14px var(--shadow)`) over hard borders. Never drop-shadow-everything — only floating layers.
- **Density:** generous whitespace. A page that feels slightly empty is correct; a page that feels full is wrong.

If you find yourself reaching for a second accent color, a gradient background, or a border-radius outside `{8, 10, 12, 14, 999}`, the language is being broken.

## 2. Layout rhythm

- **Max content width:** 720px for reading surfaces (journal, notes, articles); 1100px for dashboards and lists. Never edge-to-edge on desktop.
- **Spacing scale:** `2 / 4 / 6 / 8 / 10 / 12 / 14 / 16 / 18 / 20 / 24 / 32 / 48`. Primary rhythm (`4 / 8 / 12 / 16 / 24 / 32 / 48`) carries layout — section gaps, card padding, page edges. Fine-grain values (`6 / 10 / 14 / 18 / 20`) are for component-internal spacing where the primary scale jumps too far (icon-to-text gaps, button padding sweet spots, chip insets). `2` is for ultra-tight insets only — hairline gaps, icon nudges, badge inner padding; never for layout rhythm. Off-scale (`1 / 3 / 5 / 7 / 9 / 11 / 13 / 15 / 17 / ...`) is drift — round to the nearest scale value (round `1` up to `2`, never down to `0`). Above 48, page-level structure (hero paddings, section offsets, fixed-element insets) may use multiples of 8 (`56 / 64 / 80`); anything larger is a layout dimension, not spacing rhythm.
- **Vertical rhythm:**
  - Sections within a page: 24–32px apart
  - Cards within a section: 12px apart
  - Items within a card: 8px apart
  - Label → value within an item: 4px apart
- **Grid:** 1-col mobile / 2-col tablet / up to 4-col desktop. Defined breakpoints:
  - `500px` — content grids collapse (2-col → 1-col)
  - `640px` — nav chrome collapses (Home + current + ⋯ drawer)
  - `900px` — sidebar layouts collapse
- **Edges:** 16px side padding on mobile, 24px on desktop. Never less on mobile.

## 3. Typography

One type scale, no exceptions:

| Size | Use |
|---|---|
| 10–12px | Meta labels (all-caps, positive letter-spacing ≈0.5–1.5px) — "GOOD AFTERNOON", "TODAY'S SCORE" |
| 12px | Secondary labels, mono data, keyboard hints |
| 13px | Body small — captions, hints, secondary prose |
| 14px | Body default — most text on most pages |
| 15px | Body large — prose readers, note body |
| 16–18px | Sub-headings, emphasized labels, card titles in dense/data-heavy UIs where 22px is too loud |
| 22px | h2 — card titles, section headers |
| 28px | h1 — page titles, hero date |
| 32–48px | Hero numbers — clock, big stats |

Rules:
- **All-caps only for ≤12px meta labels** with positive letter-spacing (≈0.5–1.5px). Never at ≥13px — uppercase at body/heading sizes reads as shouting; drop the `text-transform` or shrink to ≤12px.
- **Mono is semantic** — it signals "this is data, not prose." Use it for timestamps, file paths, numeric IDs, FAB labels, keyboard shortcuts. Never for headings, never for body prose.
- **Font weight:** 400 body / 500 emphasis / 600 labels+buttons / 700–900 display only.
- **Line height:** 1.5 for prose, 1.3 for UI copy, 1.2 for headings, 1.0 for single-line numbers.

## 4. Color usage

Tokens exist in `theme.css` (`--accent`, `--bg-card`, `--border`, `--red/amber/green/blue/purple`, etc.). Rules for *how* they're used:

- **Accent is rare.** A page with more than ~5% accent pixels is wrong. Accent is for links, current state, primary buttons, focus rings — nothing decorative.
- **Never two accents in one view.** The theme picks one; pages don't introduce a second.
- **Data colors are semantic, not decorative.** `--red` = overdue/error, `--amber` = warning/stale, `--green` = done/success, `--blue` = info/pending, `--purple` = rare/special. Using `--red` for visual pop without a semantic reason is a bug.
- **Never color a whole card background** except for alerts (`.r-hero-alert` pattern). Tint — don't flood.
- **Borders carry meaning.** Default = neutral. `--border-strong` = hover/focus. Accent border = current/selected. Red border = invalid. No decorative colored borders.
- **Dark themes are not just inverted light themes.** Backgrounds get dimmer, but accent + data colors often need to desaturate 10–15% to avoid vibrating. The four themes in `theme.css` already handle this; respect them.
- **Background tokens are not interchangeable — pick by *role*, not by *theme appearance*.** In dark/grim/nord themes, `--bg-card` is intentionally a low-alpha rgba over `--bg` so inline cards (stat tiles, hub panels, hero cards) feel like subtle elevation. That same token is wrong for **floating panels that must occlude content beneath them** — palettes, modals, dropdowns, popovers, history lists. Use `--bg-surface` (solid hex in every theme) for those. Rule: anything with `position:absolute|fixed` and `z-index ≥ 50` uses `--bg-surface`; inline page content uses `--bg-card`.

### 4.1 Text colour must survive all six themes

The 2026-07-11 readability audit found ~60 apps whose text was unreadable in at
least one theme (`docs/READABILITY-AUDIT-2026-07-11.md`). Every case was the same
mistake: **a colour chosen against one background, then rendered against six.**
The four rules that prevent it:

- **Never hardcode a hex as a text colour.** `color:#34d399` is a dark-theme mint
  that reads at **1.9:1 on white**. Use the semantic token — `var(--success)` /
  `var(--warning)` / `var(--danger)` / `var(--info)` — which is tuned per theme.
  This applies doubly to a `:root` palette of your own (`--h-cyan: #22d3ee`): that
  is a hardcoded hex wearing a token's clothes, and it defeats theming for every
  page that imports it. Alias such tokens to the semantic ones.
- **Text on ANY vivid fill is `var(--ink-on-vivid)`, never `#fff`.** This covers
  `background: var(--accent)` *and* `background: var(--danger|--success|--warning|--info)`.
  White looks fine on the purple/blue accents (invisible-as-a-bug) and is **1.68:1**
  on warm-dark's amber accent; on the dark themes' *status* colours — which are
  bright by design (mint `#34d399`, rose `#f87171`) — white reads **1.6–2.3:1**.
  Within a theme, the accent and the status colours share a lightness polarity, so
  one ink serves both: `--ink-on-vivid` measures 4.0–11.9:1 on every status fill in
  every theme. (`--accent-ink` remains its per-theme source; use `--ink-on-vivid`
  at call sites so the intent reads correctly on a `--danger` button.)
  Watch for the *fallback* form too — `var(--accent-text, white)` names a token that
  doesn't exist, so `white` silently wins.
- **Dim with a colour, not with `opacity`.** `opacity: 0.4` on text that is
  *already* `--text-muted` composites below the legibility floor. `--text-muted`
  IS the token for "de-emphasised", and it is floor-checked (≥3.0:1) in every
  theme by `scripts/check-contrast.py`. Opacity is for whole decorative elements
  (icons, dots), not for text.
- **A brand colour that must stay itself → mix it toward `--text`.**
  `color-mix(in srgb, #f59e0b 72%, var(--text))` darkens on light themes and
  lightens on dark ones, keeping the hue while clearing the floor. And a page that
  is a deliberate **dark-only island** must pin its own text colour explicitly —
  an explicit `theme.css` rule (e.g. on `h1`) beats your `body`'s inherited colour.

- **Never redeclare a global theme token at `:root`.** Only `theme.css` may define
  `--accent`, `--bg*`, `--text*`, `--border*`, the status colours, or the
  `--red/--amber/--green/--blue/--purple` data set. A component stylesheet that
  declares one at `:root` has the *same specificity* as `.theme-*` but loads
  **later**, so it wins — silently replacing the active theme's token across the
  entire page, the global nav included. `eos-flipbook.css` did exactly this with
  `--accent: #6f5d3f`, hijacking the accent on all three surfaces that imported it.
  Namespace component tokens (`--ex-accent`, `--aura-amber`, `--qr-accent`) or scope
  the block to the component root. A genuinely standalone page that never loads
  `theme.css` is the one exception — mark it `/* text-tokens: ignore — … */`.

Measure, don't eyeball: `?debug=readability` on any page paints the offenders live
with their contrast ratios; `python scripts/check_readability.py --app <id>` checks
it across all six themes. `scripts/check-text-tokens.py` gates all four rules
statically (T1 white-on-vivid, T2 hardcoded status hex, T3 token hijack).

## 5. Motion

Motion is a whisper, not a performance.

- **Duration:** 120–200ms for hover/focus. 250ms for slide panels. 300ms max for anything visible to the user, except the hands-free pulse which is intentionally slow (0.8–1.4s).
- **Easing:** `cubic-bezier(0.175, 0.885, 0.32, 1.275)` for FAB/drawer spring; `ease` or `ease-out` for everything else.
- **What to animate:** `transform` and `opacity` only. Never animate `width`, `height`, `top`, `left` — they force layout.
- **What not to animate:** page transitions, loading states between views, text appearing. Content should arrive instantly; only chrome should move.
- **Reduce-motion honored.** When `prefers-reduced-motion: reduce`, replace slides and springs with fades under 120ms.
- **No parallax, no bouncy scroll, no animated backgrounds.** Not because they're bad — because they're loud, and this product is quiet.

## 6. AI-surface visual treatment

The single most important section. In a product that thinks alongside the user, the user must be able to tell at a glance what's theirs and what's the machine's.

### Four states of content

| State | Visual treatment |
|---|---|
| **User content** | Default — `--text` on `--bg`, no marker. This is the baseline everything else differs from. |
| **AI draft / suggestion** | Accent-tinted background `color-mix(in srgb, var(--accent) 8%, var(--bg-card))` + small chip top-right: "✨ draft" or "✨ suggested". Dismissible. When accepted, loses the marker and becomes user content. |
| **AI streaming** | Soft pulse on the container border (`box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 40%, transparent)` at 50% of a 1s cycle). Stop pulse on `done`. |
| **AI historical** | An AI-authored artifact the user has accepted but wants to trace — shows a small provenance chip at top-right, no background tint. |

### Provenance chips

Every card or block that is or was AI-authored shows a provenance chip, top-right:

- `🔒 local · ollama · qwen3:8b`
- `☁ cloud · gpt-4o-mini · ~$0.002`
- `🔒 local · edge-tts`
- `👤 you` (optional — used on pages where user and AI content coexist and the user content is the minority)

Chips use `.eos-badge` styling, muted color, mono font. Clicking opens a small popover with full model + timestamp + cost + a "regenerate" button when relevant.

For JSON endpoints that return `provenance`, pages may declare a pre-existing
`data-ai-output` sink and let the dark-flagged runtime mount the chip. Full
contract: `.claude/rules/auto-provenance.md`.

### Model pill (input-side provenance)

Mirror of provenance chips, at the *input* side. Every app whose user-initiated path calls `self.think()` mounts `EOS_UI.modelPill({app, mount})` in its toolbar. The pill shows the active provider's cost class as the load-bearing signal (🆓 free · 🔒 local · 💰 paid), with click-to-switch that persists per app via `settings[think.app.<id>]`. Amber tint on the chip when 💰 paid is active.

Pair with the existing provenance chip: provenance shows where bytes flowed *after* a call, pill shows who's about to be billed *before*. Both compose; never replace one with the other. Per-app default vs per-session tier picker (`EOS_UI.tierPicker`) coexist — pill is sticky, tier picker is one-session.

Full contract: `.claude/rules/model-pill.md`.

### Field-suggest (✨ on a creative input)

A blank creative input (premise, prompt, topic, brief, goal seed) MAY carry a ✨ "suggest from my vault" button via `EOS_UI.fieldSuggest` (auto-mounted from a `data-suggest-field` attribute or a formModal `suggest:` key). It's the gentlest AI surface: a small `eos-btn-sm` beside the input → a popup pick-list (`.eos-suggest-pop`) → click fills the field. Visual rules:

- **Sits beside the input it serves, never floats.** The button lives in the field's own group; the popup anchors below it.
- **Propose, never autofill.** The user picks one (or ignores all); nothing is auto-selected or submitted. This is the "with you, not for you" test passing at the smallest scale — the suggestion is a draft, the user's click is the act.
- **Only on open/creative fields.** Never on the user's own draft text, validated enums, dates, numbers, or pick-an-existing-entity fields (an LLM inventing a company name is wrong). When in doubt, omit it.

Full contract + the when-NOT-to table: `.claude/rules/field-suggest.md`.

### AI chrome placement

- **Docked, never modal-blocking.** Assistant drawer slides from the right. Hands-free chip and voice FAB sit bottom-right. Smart dot opens a speed dial — never a full-screen modal.
- **Dismissible with one key.** `Esc` closes any AI surface. The user must always be able to get back to their own workspace in one key.
- **Never inline in content flow as a "chatbot card".** If AI has something to say mid-page, it's a draft/suggestion (see table above), not a conversational bubble.

### Streaming text

- Render tokens as they arrive. Never buffer to show a finished message all at once.
- Cursor/caret optional, but if shown, use a blinking `▍` block — never an animated dot.
- When streaming is interrupted (user clicked elsewhere, model stopped), leave the partial text with a muted italic "(stopped)" or "(interrupted)" suffix. Never silently truncate.

### The "with you, not for you" visual test

If an AI surface could be mistaken for a finished decision rather than a suggestion the user can accept or reject, it's wrong. Specifically:

- An AI-drafted journal entry must look like a draft until accepted — not appear as if the user wrote it.
- An AI-suggested task must be visibly different from a user-entered task until accepted.
- An AI-auto-classification (tag, dimension, priority) must show its confidence and be editable inline — never silent.

## 7. State treatments

Every page must handle all five states. Missing states are bugs.

| State | Treatment |
|---|---|
| **Default** | The thing that renders when data loads normally. |
| **Loading <2s** | Inline spinner in place of the content area. No full-page blanket. |
| **Loading >2s** | Skeleton rows (shimmer using `color-mix(in srgb, var(--text) 5%, var(--bg-card))` animated at 1.2s) for lists. Progress bar with stage label ("Indexing 212/800…") for long operations. |
| **Empty** | One-line explainer + primary action button. Never a blank panel. Tone: "No tasks yet — [Add one]". Never "No data." |
| **Error** | Inline, `--red` toned, dismissible, with a retry when relevant. Never `alert()`. Never a full-page error. |
| **Offline / cloud-gated** | Dim + tooltip pattern (`[data-online-only]` from `.claude/rules/app-conventions-for-export.md`). Feature visible but muted with a "requires the daemon" / "requires cloud consent" hint. |

## 8. Density

- **Phone (≤640px):** tap targets ≥44px, one column, generous padding (16px edges, 12px within cards).
- **Desktop (≥900px):** tighter rows (32–40px), denser tables acceptable, multi-column allowed.
- **Same page, both densities.** Don't build two parallel designs; build one that gracefully densifies.
- **Never hide content on mobile.** Collapse, reorder, reflow — but don't hide. The user should be able to reach every feature on every device.

### Time-density conventions

When rendering time-shaped data, use the shared components and these canonical scales — don't invent per-app variants.

- **Heatmap intensity (5 steps):** `0 → empty (--border)`, `≤1 → 25% accent`, `≤2 → 50% accent`, `≤4 → 75% accent`, `>4 → 100% accent`. Shared CSS: `.eos-hm-{0,1,2,3,4}`. Override via `EOS_UI.yearHeatmap({intensity: fn})` only when the data's natural ceiling differs (e.g. a habit-tracker capped at 1/day should bucket differently).
- **Month-grid density dots:** up to 4 dots per cell + `+N` overflow chip. Tones: `overdue` (red), `today` (amber), `done` (muted). Shared CSS: `.eos-mg-dot.tone-*`. The dot row is the canonical "this day has stuff" signal — apps shouldn't paint cells with bespoke backgrounds.
- **Today's cell** is always accent-bordered + tinted (`.eos-mg-today`). Don't hand-roll a different "today" treatment.

## 9. Interaction patterns

### Mutations

- **Undo windows for fast ops.** Capture, check-off, dismiss — show a 4–6s toast with "Undo". After that window, the action is final.
- **Confirm cards for slow or irreversible ops.** Delete, send, publish, spend — use `EOS_UI.confirm`. Never auto-proceed.
- **Never `alert()`, `confirm()`, or `prompt()`.** They're modal-blocking and ugly. Use `EOS_UI.toast`, `EOS_UI.confirm`, `EOS_UI.formModal`.

### Keyboard

- **Every feature reachable by mouse must be reachable by keyboard.**
- `Ctrl+K` / `Cmd+K` — command palette
- `g` + letter — go-to nav (g-t Tasks, g-j Journal, g-s Search, g-a Assistant)
- `?` or `Ctrl+/` — shortcut help
- `/` — focus search
- `Esc` — close overlays, dismiss drafts, cancel AI surfaces
- `Ctrl+Enter` — submit in any text area

### Discoverability

- **Glanceable by default, detail on demand.** Hub shows one number per app; click for the story. Apps show a list; click for the item. Item shows a summary; click for the full note.
- **Hover is not required.** Everything reachable by hover must also be reachable by tap or keyboard.
- **Tooltips are optional enrichment**, not primary labels. If a button needs a tooltip to be understood, it needs a better icon or label.
- **Every button carries a native `title="..."` tip** (2026-06-11) — one short sentence saying what clicking it does ("Save this entry to today's journal note"), supplementing the label, never replacing it. Shared components (`eos-components.js`, auto-UI) already set them; app pages set their own. `EOS_UI.appHeader` actions take `title:` per action; `EOS_UI.emptyState` takes `actionTip:`. Audit: `python scripts/check-button-tips.py` (preflight `--scope ui`).

## 10. What we refuse to build

The negative space matters more than the positive. These are patterns that look harmless but erode the language.

- **No wellbeing-wheel UI.** No dimension pickers, no "which dimension does this serve?" prompts, no wheel visualizations. The wheel is a silent rubric — see CLAUDE.md rule 16.
- **No engagement-bait streaks or notifications** unless they serve a thin dimension per the wellbeing rubric.
- **No modal AI.** No "click to chat with your assistant" full-screen takeover. AI chrome docks.
- **No hidden-cost AI calls.** Cloud spend is visible *before* sending, not after.
- **No third-party branding in app UIs.** "Markdown vault", "source URL", "Open external" — never "Obsidian", "Suno", etc. (CLAUDE.md rule 14.)
- **No inline dialog calls.** `alert`, `confirm`, `prompt` are banned in `apps/**/pages/*.html`.
- **No decorative gradients or animated backgrounds.** Motion is a whisper.
- **No custom scrollbars, no fake cursors, no "loading…" animated text.** This product is not trying to entertain you.

## 11. The three decision tests

Before shipping any page or feature, answer these:

1. **"Could the user do this without AI?"** If no, the AI is a crutch, not a companion. Every AI-accelerated path must have a manual equivalent.
2. **"Does this feature make a judgment for the user?"** If yes, rework it as surface-and-suggest. Render the draft, let the user accept, edit, or reject.
3. **"What happens when the AI is wrong, offline, or slow?"** The answer must be "the feature still works, just without the accelerator." If the feature disappears when the AI does, it's built wrong.

## 12. Marketing / brand-island pages

Everything above (§1–11) is **absolute for product apps** — every page under `apps/**/pages/`, the daemon chrome, anything a logged-in user operates. A **brand island** is the narrow exception: a *published, public-facing, non-product* surface where higher design variance is legitimate because the audience is a prospect, not an operator. Today that's exactly three shapes:

- **Published portfolio / blog output** — `apps/public/standard/publish/portfolio_template.html` and per-site themes under `data/apps/publish/sites/<id>/`. The vault → static-site *output*, not the publish app's own chrome (which obeys §1–11).
- **External product landing pages** — e.g. `brand/plekto/site/` (its own palette, type, and build).
- **`promote` + `designer` landing previews** — the *generated* HTML is a brand island; the promote/designer app chrome is not.

Brand-island status is **opt-in per surface, never per-app.** If a surface isn't one of the shapes above, it's a product app and §1–11 bind with no exceptions.

### What a brand island MAY do that a product app may not

- Have a **hero** and marketing-voice copy (product apps have labels, not slogans).
- Derive its **own palette + display type** per product/site — it does *not* have to be the EmptyOS purple.
- Use slightly more **layout variance** — split-screen, asymmetric, a single tasteful entrance reveal.

### What stays locked even on a brand island

- **One accent, one corner-radius system, off-black/off-white** (never `#000`/`#fff`). The token *values* may differ from `theme.css`, but the discipline of §1 holds.
- **Motion is still a whisper** — `transform`/`opacity` only, `prefers-reduced-motion` honored. A brand island earns *a* hero reveal, not cinematic scroll-jacking.
- **Empty/loading/error states** if interactive (§7); **WCAG AA contrast** on every label, input, focus ring, and error string.
- **Leak discipline** — these pages are public, so `scripts/check-personal.py` + `scripts/check-branding.py` + the `eos-screenshot` redaction gate apply. No third-party branding (CLAUDE.md rule 14) even when naming your own product.

### Marketing anti-cliché checklist (borrowed)

Product UI has its forbidden-pattern list in §10; marketing pages have their own slop tells. Before shipping a brand island, declare a one-line **Design Read** ("Reading this as: *[page kind]* for *[audience]*, *[vibe]* language") and check for these (adapted from the `taste-skill` anti-slop set — see *See also*):

- **No AI-purple gradient hero** — the single loudest AI tell. A published EmptyOS page leaning on the product purple as a gradient mesh reads as generated; derive a real palette.
- **No three-equal-width feature cards** — the most generic AI layout. Use 2-col zig-zag or an asymmetric grid.
- **No section-number eyebrows** ("01 / INDEX", "06 · how it works") and **no decorative status dots** on every list item.
- **No fake `<div>` product screenshots** — use a real one (the `eos-screenshot` skill) or skip the preview.
- **No filler verbs** ("Elevate", "Seamless", "Unleash") and **no fake-precise numbers** ("92%", "4.1×") unless they're real or labelled illustrative.
- **No scroll cues, version labels in hero, or decorative photo credits.**
- **Copy self-audit** — reread every visible string before ship; hallucinated phrasing is the tell that survives every other check.

### Redesigning an existing brand island

**Audit first, don't rewrite.** Scan the page (framework, styling, current patterns) → diagnose against the checklist above → apply targeted fixes *in the existing stack*. A from-scratch rewrite throws away whatever taste the page already had.

### What we explicitly do NOT borrow from `taste-skill`

The source skill is React/Tailwind-shaped and aggressive about LLM tells we don't share:

- **The em-dash ban.** taste-skill's #1 rule is "zero em-dashes anywhere." EmptyOS uses em-dashes deliberately — including throughout this doc. Rejected.
- **GSAP / scroll-pinning / cinematic motion as default**, marquees, high motion dials. Brand islands stay quiet — they get *more* than a product app, not Awwwards.
- **Tailwind / React / Next / shadcn assumptions** and the design-system map (Fluent/Carbon/Polaris). EmptyOS brand islands are hand-CSS deriving from theme-token discipline or a per-site KB design-system note, not a framework install.
- **"Real images mandatory / image-gen first."** EmptyOS may stay restrained and text-forward; we don't force stock or generated imagery.

### External design-tool intake

The five design-tool leads reviewed on 2026-06-14 (`taste-skill`, `impeccable`,
`shadcn/ui`, `ui-ux-pro-max-skill`, `design-md-collection`) are **references, not
runtime dependencies**. They map to existing EmptyOS layers:

| External idea | EmptyOS absorption point | Boundary |
|---|---|---|
| Anti-slop rules and aesthetic tells | `eos-page-design-review`, this brand-island checklist | Borrow checks, not blanket taste law |
| Deterministic UI detectors | `eos-design-system-audit`, `scripts/check_ui_structure.py` | Add only after repeated EOS-visible issues |
| Copy-owned component library | `EOS_UI` in `emptyos/web/static/eos-components.{css,js}` | Do not import React/Tailwind/shadcn |
| Design-system brief generation | `frontend-design` + `apps/public/standard/designer/` | Brand islands only, not product app chrome |
| `design.md` plus provenance metadata | Future local design-system library | Record source/license before reuse |

Default rule: a product app page never changes its visual language because an
external design skill says so. It may gain a sharper audit check or a better
`EOS_UI` primitive. A brand island may use these tools for inspiration, then must
pass the locked constraints above.

### Arbitration when borrowed ideas conflict

Absorbing several external design tools raises a fair worry: they overlap and
sometimes contradict (e.g. taste-skill and impeccable are both anti-slop taste
advisors; one bans em-dashes, EmptyOS keeps them). The resolution is structural,
not per-tool — apply it in this order:

1. **Never run them as parallel runtime authorities.** Each tool's ideas are
   absorbed as *doctrine into the one owned layer it feeds* (the intake table
   above). Only one authority is ever consulted at runtime — the EmptyOS owned
   layer — so two skills can't fight. Conflict is resolved at *absorption time*
   (a docs edit), never at *runtime* (a live skill clash).

2. **Map first; most "conflicts" are just different layers.** Of the five,
   only taste-skill ↔ impeccable genuinely overlap (aesthetic lint). shadcn
   (component philosophy → `EOS_UI`), ui-ux-pro-max (brief recommender →
   `designer`), and design-md-collection (provenance schema) feed *different*
   organs and compose rather than compete. Before declaring a conflict, check
   whether the two ideas even target the same owned layer.

3. **Precedence ladder breaks real ties** (when two ideas hit the same layer):
   - **§1–11 product design language + `EOS_UI`** — absolute. No external skill
     overrides it.
   - **§12 brand-island rules** — apply only on the `frontend-design`/`designer`
     path.
   - **External tool suggestion** — advisory input only. No external tool has
     standing to override another or the owned doc; it's a candidate feeding a
     human/doc decision, not a runtime rule.

   Worked example: taste-skill said "ban em-dashes," this doc said "keep them" —
   the owned doc won, not because the two skills were scored against each other,
   but because the owned authority outranks the external one.

4. **Suppression = "not installed," not "turned off."** A rejected idea (em-dash
   ban, React/Tailwind stack, design CLIs, the deferred D3–D5 slices) lives in
   the "What we explicitly do NOT borrow" / deferred lists — it is never loaded,
   so there is no live skill to disable and nothing fighting an active one.

Stated once: *map each tool to the single owned layer it feeds; when absorbed
ideas collide, the EmptyOS owned doc/helper outranks any external suggestion, and
one external tool never outranks another — it is an input to a human call, not a
runtime rule.*

### Graduation

There is **no `eos-frontend-taste` skill, and shouldn't be one yet** — it would duplicate the generic `frontend-design` skill's territory with zero proven consumers (CLAUDE.md rule 9 / Forge anti-abstraction). The brand-island generator path is the existing `frontend-design` skill + the `designer` app; this section is the EmptyOS-specific guardrail they inherit. Promote it to a dedicated skill only when 2+ real marketing/publish pages are in active iteration and the friction is real.

## 13. Structured vs expressive surfaces — the two-tier doctrine

Settled 2026-07-10 (frontend design audit + adversarial steelman;
`30_Resources/EmptyOS/insights/outputs/2026-07-10-frontend-design-audit.md`):
**EmptyOS does not adopt a SwiftUI-style view-tree DSL.** LLMs are trained on
HTML/CSS/JS, not on any invented syntax — a custom view grammar would degrade
one-shot generation quality (viz/designer work *because* their medium is plain
HTML), and SwiftUI's payoffs (compile-time types, reactive diffing for large
human teams) don't transfer to a no-build-step, hot-reloaded, AI-regenerated
codebase. Don't re-litigate this without new evidence.

What we DO hold: every UI surface belongs to one of two tiers, and the tier
decides its medium.

| Tier | Surfaces | Medium |
|---|---|---|
| **Structured** | CRUD lists, dashboards, forms, panels, settings — anything auto-UI-shaped | Declarative descriptors (data + renderer name) interpreted at runtime by owned renderers. No hand-written HTML. |
| **Expressive** | viz artifacts, designer pages, brand islands, games/3D, bespoke workbenches | Freeform HTML + element-edit anchors + KB `kind:pattern` few-shot priors, guarded by deterministic validity gates. |

The structured tier is already real — six descriptor dialects exist (hub panels,
boards column/view schema, formModal fields, settings schemas, voice cards, tour
steps, plus auto_ui's inferred shape). The standing direction is **converge, not
multiply**: a new structured surface reuses an existing descriptor shape (boards'
column schema is the richest model) rather than inventing dialect seven. The full
unification (shared surface-schema module + `EOS_UI.renderSurface()`) is deferred
with a trigger in `docs/DEFERRED-WORK.md` — build it when a seventh dialect is
genuinely wanted, not speculatively.

Why descriptors for structured surfaces: an LLM emitting a small, exampled JSON
schema is schema-validatable and retryable-on-mismatch, and the owned renderer
guarantees token usage, escaping, and a11y by construction — every benefit a DSL
promises, without the training-distribution penalty.

## See also

- `emptyos/web/static/theme.css` — the tokens (colors, radii, fonts, themes). Never hardcode a hex; always use a token.
- `emptyos/web/static/eos-components.{css,js}` — the primitives. Use these before hand-rolling.
- `CLAUDE.md §Shared Frontend` — the canonical list of `EOS_UI.*` helpers.
- `CLAUDE.md §Development Rules 12` — prompts are first-class artifacts (the language for AI inputs; this doc is the language for AI outputs).
- `.claude/skills/eos-design-system-audit/SKILL.md` — audits app pages against this doc and migrates drift.
- `.claude/rules/app-conventions-for-export.md` — `[data-online-only]`, render-from-state patterns.
- `frontend-design` skill + `apps/public/standard/designer/` — the brand-island generator path that §12 guards. Use these for marketing/landing/portfolio output, not for product apps.
- `taste-skill` (github.com/Leonxlnx/taste-skill) — the external anti-slop marketing-page skill §12's checklist borrows from. Treated as an aesthetic lint for brand islands only; NOT adopted as a default skill (see §12 "What we explicitly do NOT borrow").
- `docs/OPEN-SOURCE-BORROWING-PLAN.md` — external design-tool intake record and deferred slices.
