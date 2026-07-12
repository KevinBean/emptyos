# Audits — running and graduating one-off heuristics

A "system audit" in EmptyOS is anything that walks N apps/files/notes looking
for a class of issue: the UI walk in `scripts/ui_walk_audit.py`, the model-bench
scenario coverage check, `check-personal.py`, `check-branding.py`,
`check-clickable.py`, `check-absolute.py`, and future siblings. They all share
the same two failure modes and the same graduation path. This file documents
both.

## Failure mode 1 — heuristics fire on healthy apps too

Every audit ships with heuristics that catch *some* real signal AND a long
tail of false positives. Round 1 of the 2026-05-16 UI walk surfaced 136
findings; only 4 were real bugs and 14 were genuinely actionable patterns.
The rest were the FAB-stack heuristic firing on every app (every app has
~10 fixed-position elements — that's the architecture, not a per-app bug)
and `button:has-text('Add')` matching hidden Add buttons inside closed
modals (Playwright then timed out waiting for them to be "stable").

**Rule:** Before reporting findings to the user, run the heuristic against
3 apps you know are healthy (e.g. `hub`, `task`, `journal`). Anything that
fires on a healthy app is heuristic noise. Either:

- Tune the threshold (`fab_count > 10` instead of `> 6`)
- Filter at report time (group by message; drop messages that hit ≥30% of apps)
- Categorize as "expected" upfront (e.g. apps wrapping external standalones,
  apps with intentionally desktop-only surfaces — the `DESKTOP_ONLY` set in
  `tests/test_sys_mobile.py` is the reference)

**Don't ship a "12 apps are broken" report when 11 of them are walker noise.**
The cost of explaining a false positive once is small; the cost of pointing
the user at a bug that isn't there shows up every time they re-read the report.

## Failure mode 2 — the audit lives in `scripts/` and never runs again

A one-off script that found real bugs but doesn't get rerun decays to dead
code. The audit's value compounds only when it's running on every push, or
at minimum every release. Graduate ad-hoc audits to one of three permanent
homes the same day you write them:

| Audit shape | Home | Example |
|---|---|---|
| Per-app browser check that should fail CI when a new app regresses | `tests/test_sys_*.py` (parametrized over the app list) | `tests/test_sys_mobile.py` |
| Local-only check requiring the live daemon — used at release time only | `scripts/check-*.py`, wired into `scripts/release-public.py` | `scripts/check-clickable.py` |
| Static-file scan that doesn't need the daemon | `scripts/check-*.py`, optionally wired into a pre-commit hook | `scripts/check-personal.py`, `scripts/check-absolute.py` |

The graduation step is the load-bearing part. Without it the next person who
needs the same insight rewrites the audit from scratch.

## What graduation looks like

For the UI walk audit (round 1 + round 2, 2026-05-16):

- **Mobile-overflow heuristic** → `tests/test_sys_mobile.py` (pytest, parametrized
  over `/api/apps`). `DESKTOP_ONLY` allowlist for the few apps that can't
  collapse at narrow viewports.
- **Click-intercept via `elementFromPoint(center)`** → `scripts/check-clickable.py`
  (needs running daemon; release-only). `ALLOWLIST` for apps where the
  collision is content-vs-FAB at scroll position (legitimately expected).
- **Click-intercept dev overlay** → `emptyos/web/static/eos-debug-clickable.js`
  loaded by `?debug=clickable` query param. Visual feedback at design time.
- **Inline `position:absolute` without ancestor `position:relative`** →
  `scripts/check-absolute.py` (static scan, no daemon needed).
- **Theme-bootstrap injection** → `emptyos/web/server.py:_inject_theme_bootstrap`
  (kernel auto-injects on every `pages/index.html` response — graduates from
  per-app reminder to platform guarantee).
- **Perceptual readability (2026-07-11)** — rendered-page WCAG contrast +
  tiny-font + opacity-faded text, all apps × all 6 themes →
  `scripts/check_readability.py` (needs running daemon; manual/skill/release —
  wire into `release-public.py` once the tree stays FP-clean) +
  `emptyos/web/static/eos-readability.js` (`?debug=readability` overlay; shared
  walk core, fixture-tested in `tests/test_sys_readability.py`) +
  the extended `scripts/check-contrast.py` (rgba surface tokens composited over
  `--bg`, plus the four semantic status colors per theme; static,
  preflight-gated). Opt-out: `data-readability-ignore` on the element.

  Unusually for an audit, the **fail band was mostly real** — 317 fail groups
  across 59 apps, and the opt-out marker was needed **zero** times. That's a
  consequence of threshold design, not luck: `fail` is <2.5:1, which is below
  *every* theme-token floor, so nothing legitimately muted lives there. The
  dominant defects were **theme-invariant palettes** — three apps (`cable.css`
  shared by 7 engineering apps, `finance.css`, grid-analytics) defined `:root`
  hue tokens as dark-theme brights used as text, so every readout sat at
  1.3–1.5:1 on the light themes; aliasing those to the semantic tokens fixed
  ~90 groups in three one-line edits. Second was `color:#fff` on
  `var(--accent)` (invisible bug on purple themes, **1.68:1 in warm-dark's
  amber**) across 25 files. Full triage: `docs/READABILITY-AUDIT-2026-07-11.md`.

  Three noise classes were fixed **in the checker, not the tree** (the
  failure-mode-1 discipline above), and the middle one is the transferable
  lesson for any future rendered-DOM audit:
  1. *Emoji + symbol-only glyphs* (😊 ✎) — `color` doesn't govern a color
     bitmap; require a letter/digit before measuring.
  2. **Mid-animation frames** — entry animations (`cardIn`, `fadeDown`) leave
     elements at partial opacity for ~350ms, so the walk measured transient
     composites. Timeline's apparent 7-shade "color ramp" was **one color at
     seven animation opacities** = 44 phantom fail groups from one element.
     Fix: `document.getAnimations().forEach(a => a.finish())` before walking.
     **Any audit that reads computed style _or geometry_ must settle animations
     first.** It is now `settleAnimations()` in the shared `eos-audit-walk.js`
     harness, so a new audit inherits it by construction. The geometry half is
     not hypothetical: the affordance walk below reads `scrollHeight`/`offsetTop`,
     shipped without settling, and only the harness extraction caught it.
  3. *Warn band 3.0–4.5* — exactly where muted-by-design text legitimately
     lives; warn floor lowered to 3.0.

  **The last two defects were in the platform, not in any app** — and are the
  reason a per-app audit would never have been enough: `theme.css` itself
  defined `--blue`/`--purple` as *fixed literals* while their siblings
  `--red/--amber/--green` alias the theme-tuned tokens (so the design system
  shipped two theme-invariant colours inside the token set whose whole job is to
  be theme-aware), and the shared toast-bell painted a hardcoded dark scrim under
  `var(--text)` (dark-on-dark at 2.1:1 on the light themes). Both only surface
  when you render **the platform's own components** across **every theme**.

  The rendered audit needs a daemon and ~1h, so it can't gate. The **cheap
  static half was graduated out of it**: `scripts/check-text-tokens.py`
  (preflight `ui`, **gates**, pinned by `tests/test_unit_check_text_tokens.py`)
  pins the two highest-volume shapes — `#fff` on `var(--accent)`, and hardcoded
  status hex as a text colour — neither of which has a legitimate use. It caught
  6 violations the regex sweeps had missed on its first run. Prevention rules:
  `docs/FRONTEND-DESIGN-LANGUAGE.md` §4.1.

Each one started life as a print-statement loop inside `scripts/ui_walk_audit.py`.
The audit was the seed; the test/script/platform-fix is the keep.

- **Interaction affordance (2026-07-11)** — the perceptual gap that let a batch of
  CAD-workspace UX bugs ship behind a green `node --check` + `pytest` + `curl`:
  content clipped with no way to scroll to it, and a row of buttons acting as a
  one-of-N switcher with no tab/radio roles → `emptyos/web/static/eos-ui-affordance.js`
  (shared walk + `?debug=affordance` overlay; `data-affordance-ignore` opt-out) +
  `scripts/check_ui_affordance.py` (live daemon; design-system-audit Phase 0d) +
  `tests/test_sys_ui_affordance.py` (both-direction fixture pins, CI). Calibration
  on 21 healthy apps: **0 fails** → the broken-height-chain signal gates; the
  `buttons-as-tabs` semantic signal hit 3/21 (~14%, all genuine) → ships
  **advisory**, never gates. The third candidate detector (adjacent-panel
  non-separation) was the weakest/highest-FP and was **deliberately not built** —
  deferred with a trigger in `docs/DEFERRED-WORK.md`.

  Being the *second* rendered audit, it triggered the rule-9 extraction:
  `emptyos/web/static/eos-audit-walk.js` (`window.__eosAudit` — settle, walk
  helpers, `groupFindings` dedupe/sort/cap/mark, `?debug=` overlay) +
  `scripts/audit_driver.py` (fetch apps, authed context, goto+inject+evaluate,
  report, exit code). **A third rendered audit writes a detector, not a
  harness** — don't copy a third skeleton. The judgment half of this
  gap (visual hierarchy, "does this surface have the affordances it needs") is
  **not automatable** and stays with the `eos-ui-walk` hand-walk — whose skip on
  "the Chrome extension isn't connected" was the actual root cause; that skill now
  documents the Playwright-MCP fallback and that the skip is never valid.

The procedure is packaged as the **`eos-graduate-audit` skill** — name the defect
class, measure the false-positive rate *before* writing the checker, narrow until
the signal separates, triage every survivor, gate only the confident half, ship
with tests pinning both directions, register in `scripts/preflight.py`. Two rules
it adds to this file: an **inline opt-out marker at the call site beats a central
allowlist** (which turns every new legitimate case into a build break), and an
**ambiguous signal must never gate**. References:
`scripts/check-test-app-paths.py` (confidence split),
`scripts/check-settings-panel-drift.py` (opt-out marker).

## When NOT to graduate

- The heuristic is too app-specific to parametrize cleanly (e.g. "the
  cer-hosting calculator must match CDEGS within 0.04%" — that's a unit
  test inside one app, not a cross-app audit).
- The heuristic has a >30% false-positive rate even after tuning. A test
  that fails for unrelated reasons gets disabled within a month.
- The audit's value is exploratory — one-time mapping the codebase to
  understand its shape. Then it's research, not infrastructure.

## When NOT to add the audit at all

- The class of bug it would catch is already covered by an existing test
  layer (`test_sys_*.py`, `test_user_stories.py`, `test_journeys.py`).
- The bug surfaced once and is unlikely to recur (one-off mistake in a
  refactor — a regression test on the specific file is enough).
- The audit would slow CI by >30 seconds and the bug class is minor.
