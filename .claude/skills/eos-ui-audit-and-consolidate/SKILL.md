---
name: eos-ui-audit-and-consolidate
description: Audit EmptyOS app UIs against the frontend design language (docs/FRONTEND-DESIGN-LANGUAGE.md) and migrate drift toward shared EOS_UI helpers and design tokens. Covers design-language violations (hardcoded hex colors, off-scale spacing, forbidden patterns, AI-surface markers), component drift (hand-rolled dialogs/badges/entity-cards), structural sibling-ness (per-app reinvention of header/modal/toast/stats vocabulary even when shared helpers exist), shared-library adoption ratio, inline-CSS budget, and mandatory-rule gaps (settings panel, hash-route). Use when user says "audit UI", "consolidate UI", "check design language", "clean up shared components", "do these apps feel like siblings", or after adding a new EOS_UI helper or design-language rule.
---

# EmptyOS UI Audit & Consolidate

Two-pass skill:

1. **Audit** — every page against the frontend design language (`docs/FRONTEND-DESIGN-LANGUAGE.md`) and the component library (`emptyos/web/static/eos-components.{js,css}`).
2. **Consolidate** — migrate the high-value drift in place. Pragmatic, not a framework rewrite — migrate only patterns that appear in ≥5 apps, block a mandatory rule, or violate the design language.

Run this **when**:
- A new `EOS_UI` helper or design-language rule was added and existing apps haven't adopted it
- Apps have accumulated and visual inconsistency is showing up
- User asks to "audit UI", "consolidate UI", "check design language", "migrate to shared helpers"
- Before a release, as part of the pre-release checklist

## Do NOT run this for:
- A single-app cleanup — use `/eos-simplify` on the changed files
- Framework rewrites — this skill is migration, not redesign
- Adding new `EOS_UI` helpers speculatively — only extract when 5+ apps need it

## Inputs you need from the user

Ask before starting:
1. **Scope** — full pass across `apps/**/pages/*.html`, or a specific set of apps?
2. **Targets** — design-language violations / dialogs / badges / entity-cards / buttons / AI-surface markers / all?
3. **Do they want new `EOS_UI` helpers extracted?** — or only migrations to existing ones?

If scope is unclear, default to: all apps + design-language + dialogs + badges + entity-cards, no new helpers.

## The Pass

### Phase 0 — Mechanical pre-flight (always run first)

Two fail-fast checks that catch the highest-noise drift before any subjective audit.

**0a. DESIGN.md ↔ theme.css sync**

`DESIGN.md` at the repo root is the machine-readable token contract for non-Claude AI tools and exported bundles. Its frontmatter mirrors `theme.css`. Drift between them silently mis-trains every external agent that reads the repo.

```bash
python scripts/gen-design-md.py --check
```

- If it exits 0 → in sync, continue.
- If it exits 1 → run `python scripts/gen-design-md.py` to regenerate, stage the change, then continue. Mention the regen in the final report.

This is purely mechanical — never hand-edit the frontmatter of `DESIGN.md`. The body (after the closing `---`) is hand-maintained and the generator preserves it untouched.

**0b. App-page nav include**

Every `apps/<id>/pages/*.html` (and `apps/personal/<id>/pages/*.html`) must load `/static/eos.js` so the global nav bar + speed-dial dock render. Pages without it ship a broken back-to-Home / app-switcher / keyboard-shortcut experience — and the bug is invisible until someone opens the page and notices the chrome missing (cf. `apps/store` + `apps/fix-agent`, 2026-05-14).

```bash
python scripts/check-app-nav.py             # report violations
python scripts/check-app-nav.py --fix       # auto-insert the canonical 3-script block
```

The canonical block inserted after `<body>` is:

```html
<script src="/static/eos.js"></script>
<script src="/static/eos-components.js"></script>
<script>EOS.nav('<app-id>');</script>
```

Opt-out is allowed but must be explicit. Some pages legitimately ship without global chrome (fullscreen presenters, immersive demos, first-run onboarding). Mark those with an HTML comment in the `<head>`:

```html
<!-- eos-nav: skip — fullscreen presenter view, deck only -->
<!-- eos-nav: skip — first-run onboarding, deliberately minimal chrome -->
<!-- eos-nav: skip — full-bleed immersive canvas, no global chrome -->
```

The dash + rationale is mandatory — a bare `<!-- eos-nav: skip -->` is rejected so every opt-out carries audit context.

Pair: `tests/test_sys_app_nav.py` shells the scanner in CI so any new violation trips the build.

### Phase 1a — Design-language audit (read-only)

Scan `apps/**/pages/*.html` for violations of `docs/FRONTEND-DESIGN-LANGUAGE.md`. Each check below maps to a section of that doc — read the doc first so the "why" is in context.

**DL-1. Hardcoded colors (§1, §4)**
```
[: ,(]#[0-9a-fA-F]{6}\b           — any 6-char hex color in CSS context (excludes &#NNNN; HTML entities)
rgba?\([^)]+\)                    — raw rgb/rgba calls
```
Every hardcoded color must be replaced with a `--token` from `theme.css`. Exception: SVG `fill="#..."` in an icon is OK if the icon must not re-theme.

> **Audit-tooling note.** The naive regex `#[0-9a-fA-F]{3,8}\b` over-counts by ~3× because it matches HTML entities like `&#NNNN;`. The leading `[: ,(]` anchor restricts to `#` preceded by a CSS-property/value separator, and limiting to 6 characters drops 3-char near-matches that almost never appear in EmptyOS app pages.
>
> **Brand-island exemption (mechanical detection).** A file is a *brand island* — a deliberate visual island with its own token namespace, exempt from DL-1 hex→theme-token migration — when ANY of the following hold against its `<style>`/CSS content:
>
> 1. A `:root { ... }` block declares ≥3 tokens with a shared prefix matching `--[a-z]{1,4}-` (e.g. `--p-bg`, `--p-text`, `--p-blue`).
> 2. The same file then maps those private tokens onto theme tokens (`--bg: var(--p-bg)`, `--accent: var(--p-blue)`, etc.) — proves intentional override, not drift.
> 3. The file uses a non-default font-family stack (`Times New Roman`, `Iowan Old Style`, `JetBrains Mono` as primary, etc.) AND has its own color palette.
>
> Inline hex inside an island is **part of the island's design**, not drift. The right fix for un-tokenized hex *within* an island is "extract to a local `--<prefix>-*` var at `:root`", filed under a separate track — not DL-1.
>
> **Current known brand islands** (regenerate by grepping for files matching the rule above; this list is the cache):
>
> | File | Namespace | Aesthetic |
> |---|---|---|
> | `apps/explore/pages/explore.css` | `--paper` `--ink` `--accent` `--rule` `--traffic-*` | Paper / serif / macOS chrome |
> | `apps/publish/portfolio_template.html` | `--p-*` | Recruiter portfolio dark/light |
> | `apps/reports/static/report.css` | `--doc-*` | A4 print / PDF |
> | `apps/voice-assistant/pages/aura.css` | (Aura's own dark palette) | Voice-assistant brand island |
> | `apps/reader/pages/index.html` | `--eos-*` (with `#5aa9ff`) | Reader brand blue (open question — see system-integrity track) |
> | `apps/personal/jobs/pages/jobs.css` | `--j-amber` `--j-mono` `--j-serif` `--j-cyan` `--j-red` `--j-green` `--j-blue` `--j-surface` (14 tokens) | Career Command — Bloomberg-terminal amber/mono/italic-serif |
>
> When a Phase 1a sweep flags a file with a high hex count, **first apply the island rule above**. If it qualifies, exclude it from the DL-1 count and add it to this table with a one-line aesthetic rationale. Don't re-flag files in the table on subsequent runs.
>
> **Other legitimate per-site skips that should NOT be flagged:** categorical chart palettes (`var COLORS = [...]`), Picture-in-Picture self-contained styles (separate document, no `:root` inheritance), `var(--token, #fallback)` patterns (token already wired, fallback acceptable), `#fff` for inverse text on tinted button backgrounds.

**DL-2. Off-scale spacing (§2)**
Grep for `padding:`, `margin:`, `gap:` with values not in `{0, 2, 4, 8, 12, 16, 24, 32, 48}px`. `5px`, `7px`, `10px`, `15px`, `20px` are the common offenders.

**DL-3. Off-scale radius (§1)**
`border-radius:` values not in `{0, 4, 6, 8, 14, 999}px`. Anything else (5px, 10px, 12px, 16px…) is drift.

**DL-4. Off-scale type (§3)**
`font-size:` values not in `{10, 11, 12, 13, 14, 15, 22, 28, 32, 48}px`. Flag, don't auto-fix — may be intentional in data-heavy surfaces.

**DL-5. All-caps misuse (§3)**
```
text-transform:\s*uppercase
```
Only legal on ≤11px meta labels with ≥1.5px letter-spacing. Flag any all-caps over 12px or without letter-spacing.

**DL-6. Forbidden patterns (§10)**
- `alert\(`, `confirm\(`, `prompt\(` — banned in pages, must use `EOS_UI.*`
- wellbeing-wheel UI: dimension pickers, `dimension:` select controls, wheel SVGs visible in app pages
- engagement-bait: `streak`, `🔥` + counter patterns, notification spam
- modal AI: full-screen chatbot takeovers
- third-party brand names in user-facing strings (see `.eos-branding`)

**DL-7. AI-surface marker gaps (§6)**
For any app that renders AI-generated content:
- Is there a provenance chip (🔒/☁ + model name) on AI-authored cards? Grep for `think(` usage in the paired `app.py`, cross-ref the rendering page.
- Is AI draft/suggestion content visually distinct (accent tint background + "draft" or "suggested" chip)?
- Does streaming content pulse the container border while active?
- Can `Esc` dismiss the AI surface?

**DL-8. State gaps (§7)**
For each list/detail page, verify all 5 states exist:
- Loading (<2s inline spinner, >2s skeleton)
- Empty (explainer + primary action, never blank)
- Error (inline, toned, dismissible)
- Offline / cloud-gated (`[data-online-only]` on daemon-only features)
- Default

Missing empty or error states are the most common bug.

**DL-9. Motion discipline (§5)**
- `transition:` durations > 300ms (outside hands-free pulse)
- animations on `width`, `height`, `top`, `left` (should only animate `transform`, `opacity`)
- no `@media (prefers-reduced-motion: reduce)` block in a page that has animations

**DL-10. Keyboard path coverage (§9)**
Every clickable control should be a `<button>` or have `tabindex="0"` + keyboard handler. Grep for `onclick=` on `<div>`/`<span>` without `role="button"` or `tabindex`.

Output Phase 1a as a violations matrix: row = app, column = DL-N check, cell = count. Apps with zero violations in all columns are clean.

### Phase 1b — Component-library audit (read-only)

Grep across `apps/**/pages/*.html` (include `apps/personal/` unless gitignored):

**A. Native dialogs** — candidates for `EOS_UI.confirm` / `EOS_UI.formModal` / `EOS_UI.toast`:
```
\b(alert|confirm|prompt)\s*\(            then pipe through:  grep -vE "EOS_UI\.|await EOS_UI"
```

> **Audit-tooling note.** The fixed-look-behind variant `(?<!EOS_UI\.)(?<!await )(?<!await EOS_UI\.)\b(confirm|alert|prompt)\s*\(` *undercounts* because each look-behind is independent — a line with `await EOS_UI.confirm(...)` matches the bare-word but NOT every alternative, so behavior depends on regex-engine quirks. The two-step `grep` then `grep -vE` pattern is more reliable across `ripgrep`, `grep`, and Python `re`.

**B. Hand-rolled status/priority/age badges** — candidates for `.eos-badge-*` classes:
```
\.status-(idea|active|blocked|shelved|completed|archived|draft|published)
\.priority-(high|med|low)
\.age-(fresh|aging|stale|zombie)
status-badge|\.sb-|\.pi-tag
```
Also scan per-app inline CSS for pill rules: `padding:.*border-radius.*font-size:(10|11|12)px`.

**C. Hand-rolled entity cards** — candidates for `EOS_UI.entityCard`:
```
\.(project|post|task|song|job|book|contact|place|recipe|track)-(card|item|row)
```
Pair with the render function using them to see the title + meta + badge + actions shape.

**D. Duplicate button variants** — candidates for consolidation on `.eos-btn*`:
```
\.btn-[a-z]+\s*\{
```

**E. Mandatory-rule violations** (CLAUDE.md §In-App Settings Panel, §Deep-linking Detail Views):
- Apps with `[provides.settings]` but no `EOS_UI.settingsPanel` call
- Apps with a `showDetail(` pattern but no `EOS_UI.hashRoute` call
- Missing global nav include — Phase 0b already ran the canonical scanner (`scripts/check-app-nav.py`). Re-run here only if Phase 0b was skipped or the user added pages mid-audit.

> **Audit-tooling note (settings + hashRoute checks).** Grep `apps/<id>/pages/**/*.{html,js}`, NOT just `pages/*.html`. Apps that follow `app-ui-patterns.md` and split their JS into `pages/app.js` / `pages/dialogs.js` / `pages/<app>.js` will false-flag if you only scan HTML — the `EOS_UI.settingsPanel(...)` definition lives in the JS file, not the HTML. Confirmed with the 2026-05-10 audit run, where 27/30 false MISSes were apps whose panel call was in `dialogs.js` or `<app>.js`. Always grep both extensions before reporting any mandatory-rule gap.
>
> Reference glob (ripgrep): `rg -lE 'EOS_UI\.(settingsPanel|hashRoute)\(' D:/emptyos/apps/<id>/pages/`

Count occurrences per pattern per app. Flag any pattern with ≥5 app occurrences as a high-value consolidation target.

### Phase 1c — Structural & adoption audit (read-only)

**The reason this phase exists.** Phases 1a and 1b are *signature* audits — they only see drift that someone has already named (`.status-X`, `confirm()`, `#hex`). They scan each page in isolation. A system can pass both phases entirely while every app reinvents its own header/modal/toast/card vocabulary using novel class names (`.jh`, `.cd-box`, `.modal-bg`, `.hero-card`). When the user asks "do these apps feel like siblings?", phases 1a/1b cannot answer. Phase 1c does.

Three signal-based checks. Each one would have caught the jobs/expense divergence on its own.

**S-1. Shared-library adoption ratio**

For every page that loads `eos-components.css`, compute:

```
adoption_ratio = count(class="...eos-...") / count(class="...")
```

Use a tolerant regex — count any token starting with `eos-` inside any `class="..."` attribute, not whole-class-equals-`eos-X`. Same for the denominator (any `class="..."` attribute, summed token count).

Flag pages with `adoption_ratio < 0.30`. These pages declared their intent to use the shared library (loaded the CSS) but then ignored it. That's reimplementation drift — almost guaranteed to overlap an existing helper.

Pages without `eos-components.css` link don't get a ratio (they're probably brand islands or auto-UI-only, judge separately).

**S-2. Inline-CSS budget**

Count lines inside the **first** `<style>...</style>` block on each page (ignore additional blocks — they're rare and usually scoped to print/media). Threshold: **60 lines**.

Pages over budget: each one is asserting "the shared library doesn't have what I need" without saying which helper is missing. Triage:
- If the inline CSS defines `.modal`, `.modal-bg`, `.toast`, `.header`, `.hero`, `.btn-X`, `.tabs`, `.entry-list` — it's reimplementing an existing helper (tag with which one).
- If it defines a layout primitive that doesn't exist in the library (e.g. a kanban board, a calendar grid) and ≥3 apps need it — it's a missing helper (candidate for extraction).
- If it's truly app-specific (sparkline, donut math, app-unique chrome) — accept and move on.

The point isn't to reach 0 lines — it's to make every line of inline CSS a deliberate choice rather than a default.

**S-3. Structural fingerprint matrix**

For each page, fill a 6-column row. Each cell is one of `EOS_UI` / `hand-rolled` / `none` / `mixed`:

| App | Page header | Modal | Toast | Stat cards | Buttons | Custom fonts |

Detection rules:
- **Page header**: search inline `<style>` for `.header`, `.app-header`, `.jh`, `.app-title` — if found and not a child of `.eos-*`, mark `hand-rolled`. If `EOS_UI.pageHeader(` (or its class equivalent `.eos-page-header` once it exists) is present, mark `EOS_UI`. If neither, mark `none`. (Until `EOS_UI.pageHeader` exists, **every page will read `hand-rolled`** — that *is* the finding for the first run; surface it as a missing-helper signal not as per-app drift.)
- **Modal**: `EOS_UI.modal(` / `EOS_UI.formModal(` / `EOS_UI.confirm(` in JS → `EOS_UI`. Inline `.modal-bg` / `.modal-handle` / hand-built backdrop CSS → `hand-rolled`. Both → `mixed`. Neither → `none`.
- **Toast**: `EOS_UI.toast(` → `EOS_UI`. Inline `.toast` / `.toast-ok` / `.toast-err` CSS → `hand-rolled`.
- **Stat cards**: `EOS_UI.statCards(` or `.eos-hero-card` → `EOS_UI`. Inline `.hero-card` / `.stat-card` / `.kpi-card` / `.hero-val` → `hand-rolled`.
- **Buttons**: ratio of `class="...eos-btn..."` vs `class="...btn-..."` (where `btn-` is not `eos-btn`). >70% eos-btn → `EOS_UI`; <30% → `hand-rolled`; in between → `mixed`.
- **Custom fonts**: `<link href="https://fonts.googleapis.com">` present → list which families. Empty → blank cell.

Compute a **sibling score** per app: count of `EOS_UI` cells out of 5 component cells (custom fonts is informational, not a deficit). Flag any app with score < 2 as a structural outlier.

**Pairwise diff:** pick the app with the highest sibling score (the de facto exemplar — usually `task` or `projects`) and any app with sibling score < 2. They are not visual siblings. Surface the gap in the report.

**Why these checks aren't expensive.** All three are mechanical greps over ~60–150 files. The whole phase runs in one Explore-agent dispatch. Don't skip it because phases 1a/1b looked clean — the whole reason this phase exists is that 1a/1b can be clean while 1c is on fire.

**Limits.** Brand islands (per the table in DL-1) are exempt from S-1 and S-3 — count them separately, never average them in. The fingerprint detection is regex-based and will misclassify creative cases (e.g. an app using `EOS_UI.modal` *and* defining one extra `.modal-something` selector for a unique sub-element). Read the page when in doubt — don't blindly trust the cell.

### Phase 2 — Report + user confirmation

Produce:
```
EOS UI Audit — <N> apps scanned

Design-language violations (docs/FRONTEND-DESIGN-LANGUAGE.md):
  DL-1 hardcoded colors:   K occurrences across M apps
  DL-2 off-scale spacing:  K occurrences
  DL-3 off-scale radius:   K occurrences
  DL-6 forbidden patterns: K occurrences (alert/confirm/wheel-UI/brands)
  DL-7 AI-surface gaps:    M apps render AI content, L missing provenance chips
  DL-8 state gaps:         M apps missing empty-state, L missing error-state
  ...
  Worst offenders: apps/foo (DL-1×12, DL-6×3), apps/bar (DL-7×2)

Component drift:
  Dialogs (native → EOS_UI): N calls across M apps
  - apps/foo/pages/index.html:42 — confirm()
  - apps/bar/pages/index.html:108,201 — alert()
  ...

Badges (hand-rolled): N apps, M inline rules
  - projects: .status-* × 6 variants
  - task: .age-* × 4 variants
  ...

Entity cards (hand-rolled): N apps using per-app card classes
  - projects: .project-card
  - publish: .post-item
  - contacts: .contact-row
  ...

Mandatory-rule gaps:
  - apps/foo: declares [provides.settings] but no EOS_UI.settingsPanel — add it
  - apps/bar: has showDetail() but no EOS_UI.hashRoute — add it

Structural & adoption (Phase 1c):
  S-1 adoption ratio < 0.30:        N pages (loaded eos-components.css but barely use it)
    - apps/foo (3% — 2 of 67 classes are eos-*)
    - apps/bar (12% — using only .eos-tab)
    ...
  S-2 inline-CSS over budget (>60 lines): N pages
    - apps/foo (181 lines — reimplements .modal/.toast/.hero — see existing helpers)
    - apps/bar (142 lines — reimplements .toast)
    ...
  S-3 structural fingerprint (sibling score = EOS_UI cells / 5):
    Exemplar (highest score): apps/task (5/5)
    Outliers (score < 2):
      - apps/foo  (0/5: header=hand, modal=hand, toast=hand, stats=hand, btns=hand) — fonts: Newsreader, Space Mono
      - apps/bar  (1/5: header=hand, modal=EOS_UI, toast=hand, stats=hand, btns=hand)
    Library gap surfaced: 12/12 pages mark header=hand-rolled — no `EOS_UI.pageHeader` helper exists. Recommend extraction before per-app migration.

Recommended scope: DL-1 hardcoded colors (mechanical) + DL-6 forbidden patterns (blocking) + dialogs (quick) + badges (high-value) + 2 reference-app entity-card migrations + extract EOS_UI.pageHeader (gap surfaced by S-3) + migrate 2 worst structural outliers as references.
Deferred: DL-4 off-scale type (needs per-app judgment), button variants (needs its own session), full-system structural migration (do opportunistically when each app is touched).
```

**Ask the user to confirm scope before editing anything.**

### Phase 3 — Migrate (edit in place)

Order of operations — each independent, easy to revert. Design-language fixes go **first** because they often overlap with component migrations (e.g. a hardcoded color inside a hand-rolled dialog gets fixed in one pass).

0. **Design-language violations** — per finding from Phase 1a:
   - **DL-1 hardcoded colors** → replace with a token. `#6c5ce7` → `var(--accent)`, `rgba(0,0,0,0.18)` → `var(--shadow)`, etc. If the hex has no matching token, pick the nearest semantic token; never add a new token without discussion.
   - **DL-2 off-scale spacing / DL-3 radius / DL-4 type** → round to the nearest scale value. Flag any case where rounding would change visible layout.
   - **DL-5 all-caps misuse** → either shrink to ≤11px + letter-spacing, or remove `text-transform: uppercase`.
   - **DL-6 forbidden patterns** — `alert/confirm/prompt` migrate with step 1 below. Wheel UI / engagement-bait / modal AI / third-party brands — delete, then flag to user why.
   - **DL-7 AI-surface marker gaps** → add provenance chip (use `.eos-badge eos-badge-provenance`, add it to `eos-components.css` if missing), tint draft backgrounds, wire `Esc` handler. This is the hardest migration — batch to 1–2 reference apps, don't mass-migrate.
   - **DL-8 state gaps** → add the missing state treatment. Empty states usually need a one-liner + button; error states need an `EOS_UI.toast(msg, false)` catch.
   - **DL-9 motion discipline** → replace `width`/`height`/`top`/`left` animations with `transform`; cap durations at 300ms; add reduce-motion block.
   - **DL-10 keyboard path** → replace `<div onclick>` with `<button>` (or add `role="button" tabindex="0"` + keydown handler).

1. **Native dialogs** — per-file find-and-replace:
   - `confirm(...)` inside an `async` function → `if (!await EOS_UI.confirm(msg)) return;`
   - `confirm(...)` inside a sync function → `EOS_UI.confirm(msg, function() { ... })`
   - `alert(msg)` → `EOS_UI.toast(msg, false)` (or `, true` on success path)
   - `prompt('X')` then `prompt('Y')` → single `EOS_UI.formModal(title, [{key,label,placeholder}], async function(values){...})`
   - If the page doesn't load `eos-components.js`, add the `<script src="/static/eos-components.js"></script>` before `/static/eos.js`.

2. **Status/priority/age badges** — in each app:
   - Replace inline CSS blocks defining `.status-*` / `.priority-*` / `.age-*` with usage of shared `.eos-badge-*` classes (defined in `eos-components.css`)
   - Update HTML template strings: `class="status-badge status-X"` → `class="eos-badge eos-badge-status-X"`
   - Available variants: status-{idea,active,blocked,shelved,completed,archived,draft,published}, priority-{high,med,low}, age-{fresh,aging,stale,zombie}, neutral
   - If a new status name is needed that isn't in the shared variants, add it to `emptyos/web/static/eos-components.css` (not the app).

3. **Entity cards** — pick 2–3 reference apps (large lists, representative shapes) and convert their render function to `EOS_UI.entityCard({title, subtitle, badges, body, meta, actions, onClick, className})`. Keep the rest to migrate opportunistically — **do not migrate all apps in one pass**. Typical conversions:
   - `.project-card` (projects) → entityCard with `{title: p.name, badges: [{label: p.status, variant: 'status-' + p.status}], meta: '<tasks/deadline>', body: progressBar(p), onClick: "showDetail('id')"}`
   - `.post-item` (publish) → entityCard with `{title: p.title, badges: [...tags], body: summary, meta: date, actions: buttonHtml}`
   - Keep per-app CSS modifiers (like `.is-draft`) by changing the selector to `.eos-entity-card.is-draft` and passing `className: 'is-draft'`.

4. **Mandatory-rule gaps** — add `EOS_UI.settingsPanel` or `EOS_UI.hashRoute` per CLAUDE.md §Development Rules 17/18. These are non-negotiable.

5. **New `EOS_UI.*` helper extraction** — only if Phase 2 found ≥5 apps with a clearly identical pattern AND the user approved it. Write the helper in `emptyos/web/static/eos-components.js`, the CSS in `eos-components.css`, and migrate at least 2 apps in the same session to prove the API.

6. **Structural-drift reference migrations (Phase 1c findings)** — only after the gap helpers from step 5 exist. Pick the **two worst structural outliers** from S-3 (sibling score 0–1) and migrate them in full to use the shared vocabulary. This produces two reference apps that future opportunistic migrations can copy from. Don't mass-migrate the rest — leave them for the next time each app is touched. The migration usually drops the page's inline `<style>` block to <60 lines (S-2 budget) and pushes adoption ratio over 0.30 (S-1 floor) automatically. Re-run Phase 1c after to confirm.

### Phase 4 — Verify

```bash
# Invariant: DESIGN.md still in sync with theme.css
python scripts/gen-design-md.py --check

# Invariant: every app page still loads /static/eos.js (or carries an explicit opt-out)
python scripts/check-app-nav.py

# Invariant: no native dialogs leaking back in
grep -rnE "\b(confirm|alert|prompt)\s*\(" apps/**/pages/*.html | grep -vE "EOS_UI\.|await "

# Run system tests for every migrated app
pytest tests/test_sys_<app>.py -v        # per migrated app
pytest tests/ --ignore=tests/personal -v # before commit

# Visual check on localhost:9000 for each migrated app — trigger the paths that used to pop native dialogs and eyeball the shared-component layout.
```

If tests fail, fix the migration — never skip or mute a test. If the failure predates this session, surface that to the user before continuing (don't let pre-existing breakage be hidden by this pass).

## Report Format

```
EOS UI Consolidation — <N> apps migrated

Shared components added/updated:
  - emptyos/web/static/eos-components.js:LINES — EOS_UI.<newHelper>  (if any)
  - emptyos/web/static/eos-components.css:LINES — .eos-badge-*, .eos-entity-card  (if any)

Dialog migrations: M calls, N apps
  - apps/<app>/pages/index.html — confirm/alert/prompt → EOS_UI.<helper>

Badge migrations: K apps moved to shared .eos-badge-*
  - <list>

Entity-card migrations: L apps
  - <list>

Deferred:
  - <pattern> (reason — e.g. "button variants, needs its own session")

Verification:
  grep invariant: PASS
  pytest test_sys_<migrated apps>: <N passed, M skipped>
  visual check: <brief notes per app>
```

## Safety

- **Do not migrate every occurrence in one pass.** Pick reference apps, prove the pattern, leave the rest for opportunistic migration when touched.
- **Do not invent new `EOS_UI` helpers** unless the user explicitly approved extraction AND ≥5 apps share the pattern.
- **Never touch `apps/personal/`** unless the user's scope explicitly included it.
- **Preserve visual behaviour for the migrated apps** — consolidation is about reducing duplication, not redesign. If a migration forces a layout change (e.g. horizontal → vertical), flag it to the user before applying.
- **One app per PR/commit** if possible — makes rollback surgical.
- When adding CSS variants, **extend `eos-components.css`**, never add app-local overrides that shadow the shared classes.
- Suggest `/eos-session-wrapup` at the end.

## See also

- `docs/FRONTEND-DESIGN-LANGUAGE.md` — the visual + interaction DNA. Load-bearing; read before running the audit.
- `DESIGN.md` (repo root) — machine-readable token contract. Generated from `theme.css` by `scripts/gen-design-md.py`; never hand-edit the frontmatter.
- `CLAUDE.md §Shared Frontend` — the canonical list of EOS_UI helpers
- `CLAUDE.md §Development Rules 17, 18` — mandatory settings-panel + hash-route patterns
- `emptyos/web/static/eos-components.js` — the helper library (read it before proposing new helpers)
- `.claude/skills/eos-simplify/SKILL.md` — the per-file review pass; use that for single-file cleanups
