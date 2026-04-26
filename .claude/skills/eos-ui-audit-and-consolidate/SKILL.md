---
name: eos-ui-audit-and-consolidate
description: Audit EmptyOS app UIs against the frontend design language (docs/FRONTEND-DESIGN-LANGUAGE.md) and migrate drift toward shared EOS_UI helpers and design tokens. Covers design-language violations (hardcoded hex colors, off-scale spacing, forbidden patterns, AI-surface markers), component drift (hand-rolled dialogs/badges/entity-cards), and mandatory-rule gaps (settings panel, hash-route). Use when user says "audit UI", "consolidate UI", "check design language", "clean up shared components", or after adding a new EOS_UI helper or design-language rule.
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

### Phase 0 — Sync DESIGN.md (mechanical, always run first)

`DESIGN.md` at the repo root is the machine-readable token contract for non-Claude AI tools and exported bundles. Its frontmatter mirrors `theme.css`. Drift between them silently mis-trains every external agent that reads the repo.

Run the generator and check for drift:

```bash
python scripts/gen-design-md.py --check
```

- If it exits 0 → in sync, continue to Phase 1a.
- If it exits 1 → run `python scripts/gen-design-md.py` to regenerate, stage the change, then continue. Mention the regen in the final report.

This is purely mechanical — never hand-edit the frontmatter of `DESIGN.md`. The body (after the closing `---`) is hand-maintained and the generator preserves it untouched.

### Phase 1a — Design-language audit (read-only)

Scan `apps/**/pages/*.html` for violations of `docs/FRONTEND-DESIGN-LANGUAGE.md`. Each check below maps to a section of that doc — read the doc first so the "why" is in context.

**DL-1. Hardcoded colors (§1, §4)**
```
#[0-9a-fA-F]{3,8}\b              — any hex color in an app page
rgba?\([^)]+\)                    — raw rgb/rgba calls
```
Every hardcoded color must be replaced with a `--token` from `theme.css`. Exception: SVG `fill="#..."` in an icon is OK if the icon must not re-theme.

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
(?<!EOS_UI\.)(?<!await )(?<!await EOS_UI\.)\b(confirm|alert|prompt)\s*\(
```

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

Count occurrences per pattern per app. Flag any pattern with ≥5 app occurrences as a high-value consolidation target.

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

Recommended scope: DL-1 hardcoded colors (mechanical) + DL-6 forbidden patterns (blocking) + dialogs (quick) + badges (high-value) + 2 reference-app entity-card migrations.
Deferred: DL-4 off-scale type (needs per-app judgment), button variants (needs its own session).
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

### Phase 4 — Verify

```bash
# Invariant: DESIGN.md still in sync with theme.css
python scripts/gen-design-md.py --check

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
