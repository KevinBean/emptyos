---
name: eos-design-system-audit
description: Audit EmptyOS app UIs against the frontend design language and migrate drift toward shared EOS_UI helpers and design tokens. Covers design-language violations (hardcoded hex colours, off-scale spacing, forbidden patterns, AI-surface markers), component drift (hand-rolled dialogs, badges, entity cards), structural sibling-ness (per-app reinvention of header, modal, toast and stats vocabulary even where shared helpers exist), shared-library adoption ratio, inline-CSS budget, and mandatory-rule gaps. Use when the user says "audit UI", "consolidate UI", "check design language", "clean up shared components", "do these apps feel like siblings", or after adding a new EOS_UI helper or design-language rule. NOT single-page aesthetic judgment (use eos-page-design-review — this is the system-wide compliance sweep), NOT per-file code quality (use eos-simplify), NOT walking real user flows in a browser (use eos-ui-walk).
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

## Reference files (read on demand)

| File | Read when |
|---|---|
| `<SKILL_DIR>/checks.md` | You reach Phase 1 — every DL-N design-language check, the brand-island table, the component-drift signatures, and the structural/adoption matrix |

## The Pass

### Phase 0 — Mechanical pre-flight (always run first)

Fail-fast checks that catch the highest-noise drift before any subjective audit.

**0. Run the whole UI scope first — one command, 15 checks.**

```bash
python scripts/preflight.py --scope ui        # ~15s, static, no daemon needed
```

This is the mechanical backbone and it **supersedes running 0a/0b individually** —
they are two of the 15 rows. It also covers ground this skill's later phases only
approximate: `check-contrast.py`, `check-text-tokens.py` (the DL-1 shapes that
actually gate), `check_ui_structure.py` (the graduated Phase 1c), `check_ui_consistency.py`,
`check-settings-panel-drift.py` (Phase 1b/E), `check-esc-phantom.py`, `check-csp-inline.py`,
`check-app-nav.py`, `check-ios-safe-area.py`, `check_hub_panels.py`.

**Read the result as the prior for everything that follows.** If it comes back
`0 FAIL`, the mechanized layer is clean and any large count your own greps produce
is far more likely to be a scanner artifact than real drift — go looking for the
filter you got wrong before you go looking for apps to migrate. (2026-07-31: preflight
said 0 FAIL; the hand-rolled greps then claimed 2,800+ violations, of which ~2 were real.)

The individual checks below are kept for the case where you need one in isolation,
or preflight is unavailable.

**0a. DESIGN.md ↔ theme.css sync**

`DESIGN.md` at the repo root is the machine-readable token contract for non-Claude AI tools and exported bundles. Its frontmatter mirrors `theme.css`. Drift between them silently mis-trains every external agent that reads the repo.

```bash
python scripts/gen-design-md.py --check
```

- If it exits 0 → in sync, continue.
- If it exits 1 → run `python scripts/gen-design-md.py` to regenerate, stage the change, then continue. Mention the regen in the final report.

This is purely mechanical — never hand-edit the frontmatter of `DESIGN.md`. The body (after the closing `---`) is hand-maintained and the generator preserves it untouched.

**0b. App-page nav include**

Every `apps/<id>/pages/*.html` (and `apps/personal/<id>/pages/*.html`) must load `/static/eos.js` so the global nav bar + speed-dial dock render. Pages without it ship a broken back-to-Home / app-switcher / keyboard-shortcut experience — and the bug is invisible until someone opens the page and notices the chrome missing (cf. `apps/public/core/store` + `apps/extension/dev/fix-agent`, 2026-05-14).

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

**0c. Rendered readability audit (needs the live daemon — skip if `:9000` is down)**

Phases 1a+ *grep* for color drift; this step *measures* it. The perceptual
readability audit loads every in-scope app in every theme and computes real
WCAG contrast on the rendered DOM (ancestor rgba layers composited, hardcoded
hex that ignores the theme, opacity-stacked text, tiny fonts) — the class of
"technically fine, unreadable to humans" issues no grep can see:

```bash
python scripts/check_readability.py                    # all apps × all 6 themes (~20 min)
python scripts/check_readability.py --app <id>         # scoped to the audit's app set
```

- Exit 0 + no fails → rendered contrast is clean, continue.
- Fail findings carry `data-eos-src` file:line — fix the color at the source
  (use theme tokens, not hex) or mark a deliberate case with
  `data-readability-ignore`. Full report at `data/readability/report.json`.
- Companion surfaces: `?debug=readability` on any page paints the offenders
  live; `scripts/check-contrast.py` (static, preflight-gated) pins the
  token-level floors the rendered audit assumes.

### Phase 0d — Rendered affordance audit (needs the live daemon)

Contrast answers "can I read it"; this answers "can I *reach* and *understand*
it". Same rendered-DOM posture, a different axis — content clipped with no way
to scroll to it, and a row of buttons acting as a one-of-N switcher without the
roles to say so:

```bash
python scripts/check_ui_affordance.py                  # all apps
python scripts/check_ui_affordance.py --app <id>       # scoped to the audit's app set
```

- Exit code counts **only** `fail` findings — the confident "a declared scroll
  region is clamped off by a clipping ancestor" (broken height chain). Calibrated
  FP-clean across 21 healthy apps, so a fail is real: fix the height chain.
- `buttons-as-tabs` is always advisory (`warn`) — add `role="tablist"/"tab"` (or
  `radiogroup`/`radio` for a value picker), or mark a deliberate case with
  `data-affordance-ignore`. Never gate on it (a11y-semantic advice, audits.md).
- Companion surface: `?debug=affordance` on any page paints the offenders live.
- **This is the deterministic half only.** Panel separation, visual hierarchy,
  and "does this surface have the affordances it needs" are judgment calls no
  scanner can make — those belong to the `eos-ui-walk` hand-walk, which is the
  only mechanism that catches them and must never be skipped.

### Phase 1 — the read-only check catalog (1a design-language, 1b component-library, 1c structural)

**Read `<SKILL_DIR>/checks.md` now** — it holds all three phases in full: every DL-N
check with its grep and its exemptions, the brand-island table, the component-drift
signatures, and the structural/adoption matrix.

All three are mechanical sweeps over ~60-150 files and run as ONE Explore-agent
dispatch. Do not skip 1c because 1a and 1b looked clean — that is the whole reason
1c exists, and it is the only one of the three that answers "do these apps feel
like siblings?".

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
  DL-11 card density:      M apps with under-informative cards (color-only status, no snippet)
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
     **Never round `1px` (or an off-scale `2px`) DOWN to `0`** — at that size the value is
     almost always a border or hairline, and `0` erases it. Round UP to the smallest
     non-zero scale value, and document the asymmetry in any codemod.
   - **Before editing any file, check the brand-island table in `<SKILL_DIR>/checks.md`.**
     Two entries are do-not-touch for reasons a violation count cannot show: the viz
     `#0d1117` was migrated in error once and reverted, and cymcap-modifier's `confirm()`
     calls are load-bearing for its offline build. Migrating either re-breaks a fixed bug.
   - **DL-5 all-caps misuse** → either shrink to ≤11px + letter-spacing, or remove `text-transform: uppercase`.
   - **DL-6 forbidden patterns** — `alert/confirm/prompt` migrate with step 1 below. Wheel UI / engagement-bait / modal AI / third-party brands — delete, then flag to user why.
   - **DL-7 AI-surface marker gaps** → add provenance chip (use `.eos-badge eos-badge-provenance`, add it to `eos-components.css` if missing), tint draft backgrounds, wire `Esc` handler. This is the hardest migration — batch to 1–2 reference apps, don't mass-migrate.
   - **DL-8 state gaps** → add the missing state treatment. Empty states usually need a one-liner + button; error states need an `EOS_UI.toast(msg, false)` catch.
   - **DL-9 motion discipline** → replace `width`/`height`/`top`/`left` animations with `transform`; cap durations at 300ms; add reduce-motion block.
   - **DL-10 keyboard path** → replace `<div onclick>` with `<button>` (or add `role="button" tabindex="0"` + keydown handler).
   - **DL-11 card density** → add the missing badge/meta/snippet field per `.claude/rules/list-card-density.md`; replace a color-only status signal with a labelled badge or text token. Don't force a migration to `EOS_UI.entityCard` — add the field to the existing markup.

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

# Re-run the mechanical backbone — the fastest proof you didn't regress anything
python scripts/preflight.py --scope ui

# Run system tests for every migrated app.
# EOS_SKIP_LEAK_GUARD=1: conftest's session-teardown vault leak scan walks the
# real ~12k-note vault and will blow --timeout on a multi-file run, killing the
# session in TEARDOWN with the test results already green but unreported. The
# env var is the documented escape (.claude/rules/vault-operator.md); drop it
# for a single-file run, and never set it in CI.
EOS_SKIP_LEAK_GUARD=1 python -m pytest tests/test_sys_<app>.py -q --timeout=120
python -m pytest tests/ --ignore=tests/personal -v   # before commit

# Visual check on localhost:9000 for each migrated app — trigger the paths that used to pop native dialogs and eyeball the shared-component layout.
```

Always `python -m pytest`, never bare `pytest` (CLAUDE.md § Testing).

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
- `.claude/rules/app-ui-patterns.md` — mandatory settings-panel + hash-route patterns
- `emptyos/web/static/eos-components.js` — the helper library (read it before proposing new helpers)
- `.claude/skills/eos-simplify/SKILL.md` — the per-file review pass; use that for single-file cleanups
