# Frontend Design Language

> The visual and interaction DNA of an EmptyOS page. If two apps built by two different contributors don't feel like the same product, this doc is out of date or not being followed.

This is **not** a component contract (that's `emptyos/web/static/eos-components.{css,js}` + its helpers). This is the language that tells you *how a page should look and behave* before you reach for a component.

## 1. Visual identity

EmptyOS looks like a well-designed reading app, not like Material, iOS, or Linear.

- **Ground:** warm off-white `#f5f2ed` (or the three theme alternates — dark, amber, nord). Never pure white.
- **Accent:** single purple `#6c5ce7`. Used sparingly — links, current-state, primary buttons, focus rings.
- **Type:** DM Sans for prose and headings; JetBrains Mono for data, timestamps, paths, FAB labels, keyboard hints.
- **Shape:** rounded everything — 8px for inputs/buttons, 14px for cards and panels. No sharp corners.
- **Depth:** soft shadows (`0 4px 14px var(--shadow)`) over hard borders. Never drop-shadow-everything — only floating layers.
- **Density:** generous whitespace. A page that feels slightly empty is correct; a page that feels full is wrong.

If you find yourself reaching for a second accent color, a gradient background, or a border-radius that isn't 8/14/999, the language is being broken.

## 2. Layout rhythm

- **Max content width:** 720px for reading surfaces (journal, notes, articles); 1100px for dashboards and lists. Never edge-to-edge on desktop.
- **Spacing scale:** `4 / 8 / 12 / 16 / 24 / 32 / 48`. Nothing in between. No 7px, no 15px. If you need 10, round to 8 or 12.
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
| 10–11px | Meta labels (all-caps, +1.5px letter-spacing) — "GOOD AFTERNOON", "TODAY'S SCORE" |
| 12px | Secondary labels, mono data, keyboard hints |
| 13px | Body small — captions, hints, secondary prose |
| 14px | Body default — most text on most pages |
| 15px | Body large — prose readers, note body |
| 22px | h2 — card titles, section headers |
| 28px | h1 — page titles, hero date |
| 32–48px | Hero numbers — clock, big stats |

Rules:
- **All-caps only for ≤11px meta labels** with +1.5px letter-spacing. Never elsewhere.
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

## See also

- `emptyos/web/static/theme.css` — the tokens (colors, radii, fonts, themes). Never hardcode a hex; always use a token.
- `emptyos/web/static/eos-components.{css,js}` — the primitives. Use these before hand-rolling.
- `CLAUDE.md §Shared Frontend` — the canonical list of `EOS_UI.*` helpers.
- `CLAUDE.md §Development Rules 12` — prompts are first-class artifacts (the language for AI inputs; this doc is the language for AI outputs).
- `.claude/skills/eos-ui-consolidate/SKILL.md` — audits app pages against this doc and migrates drift.
- `.claude/rules/app-conventions-for-export.md` — `[data-online-only]`, render-from-state patterns.
