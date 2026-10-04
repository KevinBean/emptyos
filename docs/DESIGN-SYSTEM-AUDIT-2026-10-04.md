# Design-System Audit — 2026-10-04

Pass of `/eos-design-system-audit`, default scope (all app pages; design language + dialogs + badges + entity cards + structure; no new helpers). Phases 0–2 report the tree as found; Phase 3 migrated scope A + B + C (§ What was migrated). Baseline: `docs/DESIGN-SYSTEM-AUDIT-2026-09-12.md` (22 days, 113 page files touched since).

Glob for every hand count: `apps/**/pages/**/*.{html,css,js}` excluding `_retired/`, `/legacy/`, `*.legacy.*`, `/dist/`, `/vendor/`, `_example/`, `*.min.*` — **448 files, 234 app dirs**. Each count is split *tracked* (git-tracked, non-personal, non-island) / *island* (`scanner_lib.is_brand_island` or `design-hex: island`) / *personal* (`apps/personal/`, out of scope by default). Same glob as 09-12, so totals are comparable.

## Phase 0 — mechanical backbone

`python scripts/preflight.py --scope ui` → **21 checks · 0 FAIL · 0 warn** (09-12: 1 warn — the `welcome` escaper site is now fixed).

| Check | Result |
|---|---|
| check_hardcoded_hex | clean — 327 files, 14 islands auto-exempt |
| contrast / text-tokens / phantom-tokens / design-md / gen-design-md | clean, DESIGN.md in sync |
| check-app-nav | clean — 191 pages |
| check_ui_structure | clean — 191 pages, 15 islands (S-3 composite quiet) |
| check_ui_consistency | 260 green / 12 allowlisted / 0 need work |
| check-settings-panel-drift | note: `chatbot-studio` — **not a gap**: the page carries a `settings-panel-drift: deliberate` marker; its ⚙ Service connection covers all three keys plus token generation and a live connection test |
| check_error_state (advisory) | 54 sites; 3 in `promote/pages/index.legacy.html` (dead) → **51 live**: 27 tracked, 24 personal |

Phases 0c (rendered readability, ~20 min) and 0d (rendered affordance) were **not run** this pass.

## Phase 1a — design-language matrix

| Check | Tracked | Island | Personal | vs 09-12 | Reading |
|---|---|---|---|---|---|
| DL-1 hex | 0 (scanner) | — | — | = | Platform bundle: one `#fff` added to `eos-components.*` since 09-12 (inverse text, allowed) |
| DL-2 spacing | 498 | 183 | 430 | 1111 → 1111 | tracked top: 3px×114, 5×66, 7×62, 9×57, 1×45, 22×29, 11×25. Still the open **scale decision** (widen §2 vs migrate) |
| DL-3 radius | **7** | 34 | 45 | 122 → 86 | residue after the 09-12 codemod: 2px×3, 1, 3, 5, 20 |
| DL-4 type | 182 | 39 | 90 | 310 → 311 | tracked half-pixel 129 (11.5×46, 12.5×42, 10.5×29, 13.5×8, 14.5×4); 24px×11, 9px×11, 19px×8 |
| DL-5 all-caps | **0** | 0 | 4 | = | |
| DL-6 forbidden | **0** | — | 0 | = | 4 raw hits: 2 UI label strings, 1 comment, 1 error-message string |
| DL-7 AI markers | 39 candidates | — | — | 37 → 39 | 59 non-personal apps call `think()` and have pages; 18 carry `data-ai-output`/provenance. Unverified per app — not every one renders AI text on a page (e.g. billing, git, system-log) |
| DL-8 error states | 27 | — | 24 | 52 → 51 | `EOS_UI.errorState` exists; sites listed by `check_error_state.py` |
| DL-9 motion | 18 >300ms; **16 `transition: width` rules in 14 files** | 1 | 22 | 42 → 41 | the `transition: width` progress bar the 09-12 run fixed in the platform is still hand-rolled across 13 apps (grep, CSS only); only 3 apps use `.eos-bar-fill`. The grep missed a 17th, set from JS (`soundcheck.js`); the hostile review found it |
| DL-10 keyboard | 0 | | | = | platform `enhance()` |
| DL-11 density | not walked | | | | needs `eos-ui-walk` |

Worst tracked DL-2/3/4: cable_network (74), kb (65), portal (62), rooms (28), worklog (21), operate (21), model-bench (21), agent (23).

`transition: width` (tracked): dogfood-agent, memory-fidelity, progress, dictionary, gesture (index + demo — demo declares `eos-ui: exempt`, a bundle opt-out, but is not a brand island), model-serving, operate (`macro.css`), app-analytics, billing, boards (`style.css`), expense, learn, projects (`workspace.css`).

## Phase 1b — component drift

| Pattern | Finding |
|---|---|
| Native dialogs | 0 actionable |
| Hand-rolled status/priority/age badges | 0 tracked (personal: career 2, media 1) |
| Hand-rolled entity cards | task `.task-item` ×4, projects ×2, grill ×2, replay/forge/agent ×1 — nothing at the ≥5-app bar |
| `.btn-*` variants | 17 tracked apps, max 3 rules each — thin |
| Settings panel | 0 gaps (chatbot-studio deliberate, above) |
| Hash route | 0 gaps. `store`'s `showDetails()` is a read-only info modal over the in-memory catalog, not a detail view — advisory deep-link candidate only |

## Phase 1c — structure

`check_ui_structure.py` clean — 0 S-3 outliers. Note `chatbot-studio` hand-rolls `.toast`/`toast()` and `.btn` beside a loaded `eos-components.js`; below the S-3 composite bar, an on-touch item.

## Recommended scope

| # | Migration | Size | Why |
|---|---|---|---|
| A | DL-9: hand-rolled `transition: width` bars → `.eos-bar`/`.eos-bar-fill` where the shape matches, else drop the width transition (the platform's 09-12 decision) | 13 apps, 14 files, 16 rules | §5 rule, ≥5 apps, existing helper |
| B | DL-8: `catch → empty state` → `EOS_UI.errorState({message, onRetry})` in tracked apps | 27 sites | ≥5 apps, existing helper, the most common bug class per §7 |
| C | DL-3 radius residue | 7 declarations | mechanical |

Needs a decision first: **D** DL-2 scale (3/5/7/9px — widen §2 or migrate ~500 tracked sites); **E** DL-4 half-pixel type (129 tracked — round to integer, flag-don't-auto-fix by default).

Deferred / on touch: DL-7 provenance (39 candidates), `.btn-*` variants, `rgba()` tints, DL-11 density, buttons-as-tabs.

## Scope chosen

A + B + C. D (DL-2 scale) left open by decision; E (half-pixel type) not taken.

## What was migrated

| # | Change | Files |
|---|---|---|
| A | No tracked app page animates `width` any more — 17 rules in 15 files. **Dropped** (13 files, 14 rules): bars rebuilt through `innerHTML` with their width inline, where the transition never fired because no transition runs on first paint (progress, dictionary, app-analytics ×2, billing, boards, expense, learn, projects, dogfood-agent's run bar, operate's macro-run bar); and two bars updated in place that now snap, the trade the platform made for `.eos-bar-fill` on 09-12 — memory-fidelity's dial loses its reveal after the fetch, model-serving's download bar loses its interpolation between polls. **Converted to `transform: scaleX`** (3 files), where the motion carried meaning: gesture's confidence fill (index + demo — set every animation frame, and the short transition is what damps per-frame noise in the score), the demo's PiP collapse (now `scaleX(0)` + opacity from its right edge), and soundcheck's answer countdown (set from JS, `transform <ms> linear`; it needed an explicit style flush, or the browser coalesced the start and end values and the bar never moved — measured, see Verification). Moving the bars onto `.eos-bar-fill` would have *added* a reveal that does not exist today. operate's reduced-motion rule lost the selector it no longer needs | dropped: dogfood-agent, memory-fidelity, progress, dictionary, model-serving, operate (`macro.css`), app-analytics ×2, billing, boards (`style.css`), expense, learn, projects (`workspace.css`) · converted: gesture (`index.html`, `demo.html` ×2), soundcheck (`soundcheck.js` + `.css`) |
| B | 24 of the 27 flagged `catch → empty-state` sites → `EOS_UI.errorState({message, onRetry})`, with a retry wherever the loader is a page global and a retry can recover (none for: cockpit's `preview` and radio's transcript, both module-local; portal search; reader scene generation, whose Generate button is the retry; kb fulltext's chapter, private to its module — the message names the contents jump that reloads it; improv's session detail, which fails only on a render exception a re-fetch would repeat). Two in-band failure branches painted the same grey beside their `catch` were migrated too: reader's `res.error`, radio's `d.error`. cockpit loads only `eos.js`, so its call is guarded with the old markup as fallback. video-digest's empty `catch` was a false-success bug, not an error-state: a failed vocabulary save — and every 4xx/5xx, which never reached the `catch` — still toasted "Saved"; it now checks `res.ok` and says when the word was kept for the session but not saved. 2 sites marked `error-state: intentional` with a reason: chatbot-studio (the offline panel *is* the setup call to action; the status bar carries the error), Aura (declared visual island, no shared bundle) | chatbot-studio, cockpit, memory-fidelity, model-bench ×2, progress, promote ×4, cable-bonding, cable_network, improv ×2, dictionary, radio ×3, kb (`flipbook.js` ×2, `fulltext.js`), music-library, portal (`portal-verbs.js`), reader ×2, routing, video-digest, vlog, voice-assistant |
| C | 2 of 7 radius sites: `earthing/site.html` legend bar 3 → 4px, `cable-pulling` add-type select 5 → 6px (matches the app's own `.field select` in `cable-base.css`). The other five are deliberate: soundcheck confetti (documented exemption), cad-network-ext and cable-pulling route-legend swatches (categorical paint samples), the cable-pulling `pit` glyph (square on purpose, so it reads apart from the round dots), journal 20px (flagged 09-12, not changed) | 2 files |

`check_error_state.py`: 54 → 27, all of them `apps/personal/` (24) or the dead `promote/pages/index.legacy.html` (3).

New pin: `tests/test_unit_app_page_width_transition.py` — no tracked app page transitions `width`: CSS first-in-list, mid-list, `transition-property`, vendor-prefixed, upper case, split across lines, inline `style=`, and JS `style.transition` / `transitionProperty` assignments; comments, `max-/min-width`, `--custom-transition` names and a later `width:` declaration are spared. **No brand-island or `eos-ui: exempt` exemption** — §12 keeps "Motion is still a whisper — transform/opacity only" locked on islands, and `eos-ui: exempt` opts a page out of the shared bundle, not out of the design language. Vacuity guard on the walk. Mutation-verified with `eos-mutation-verify`: 9 red rows caught (boards — a brand island — re-gains its rule; soundcheck back to a JS width transition; gesture index and the demo's PiP back to width; the JS scan dropped; case-insensitivity dropped; the custom-property anchor dropped; the value allowed past `;`) and 1 green probe tolerated (the rule inside a CSS comment). Not covered: a width hidden behind a custom property (`transition: var(--t)`). Width only on purpose: `transition: all` (65 hover rules) is the same §5 class and a separate pass.

### Verification

- `preflight --scope ui`: 21 checks, 0 FAIL, 0 warn.
- `node --check` on every edited `.js` file and every inline script of the edited HTML pages (module scripts as `.mjs`): clean.
- Browser walk on sandbox `:9002` with the API route forced to fail: routing trips, music-library, promote campaigns and improv library each render a visible `.eos-error-state` with `role="alert"`, Retry clears it once the route answers, no page errors.
- Motion on `:9002`: soundcheck's countdown reads `scaleX(0.533)` at 700 ms of 1 500 ms (linear: 0.533); HEAD's width version, replayed in the same page, reads 459 of 860 px. Before the style flush the transform version sat at `scaleX(0)` — the regression the measurement caught. Gesture confidence fills (index and demo) read `scaleX(0.20)`/`(0.41)` 40 ms after a set to 0.6 and `0.6` once settled; the PiP transitions `transform, opacity`.
- System suites on sandbox `:9002` (one process per file). The first run was cut at improv when `:9000` — and with it the pool — restarted at 18:43 outside this session; every suite after that point skipped, so the run was repeated on a fresh lease with a health check before each file. Passing: app_analytics 20, billing 10, boards 34, cable_bonding 31, cockpit 16, dictionary 16, earthing 104, expense 22, gesture 10 (re-run after the transform change), improv 31, kb 139, memory_fidelity 6, model_bench 20, model_serving 13, music_library 11, operate 22, promote 26, radio 24, reader 30, routing 19, soundcheck 32, vlog 12. Failures, none reachable from this diff: learn 1 (`course not found` from an API whose page changed by one CSS line — the sandbox vault has no course); video_digest 7 (the listen tests write fixture digests into the vault `emptyos.toml` names, the sandbox reads its own — `digest not found`); progress 1 (`test_page_loads` passes the `Page` object to `assert_no_js_errors`, a test bug — `TypeError: 'Page' object is not iterable`); projects 1 (sort `recent` returns duplicated rows, 72 vs 78 — data, beside a CSS-only edit); portal 3 (backend picker hidden, agent pane 575 px short — layout, beside an edit inside the search-failure `catch`).

## Review disposition

Two hostile reviewers (`eos-adversarial-review`), one per chunk.

**errorState chunk — 7 findings.** Fixed: kb fulltext told the user to "reopen" a chapter with no reopen control (message now names the contents jump); the video-digest `intentional` marker hid a false "Saved" toast (fixed in code, marker removed); radio's in-band `d.error` stayed grey (migrated); improv's detail Retry could not recover the only failure that reaches it (removed); model-bench missed the `(e.message || e)` fallback. Waived: `errorState` escapes only `"` in `onRetry`, not `&` — not reachable today (campaign ids are `camp-<hex>`, session ids server-generated), and the fix belongs in the shared helper, where 59 call sites would need checking for pre-escaped input; `test_sys_kb::test_ui_symbol_library_loads` passes on the failure path because the `<h3>` renders either way — predates this change.

**transitions/test/doc chunk — 10 findings.** Fixed: the pin exempted brand islands and `eos-ui: exempt` pages, contradicting §12, so the boards rule could return unseen (exemptions removed; the demo's two rules converted); soundcheck's JS-assigned width transition was missed by grep and test (converted, and the JS shape is now scanned); the `eos-ui: exempt` substring in a comment exempted a whole file (moot with exemptions gone); upper-case, multi-line, `--custom` and vendor shapes; this doc called dogfood's and operate's bars in-place (both are rebuilt by `innerHTML`); the gesture confidence bar would jitter at frame rate after a snap (now `scaleX` with its 0.15 s smoothing); memory-fidelity and model-serving did lose a real reveal/interpolation (stated above as the trade); operate's dead reduced-motion selector; count and wording errors here. Waived: `boards.js:2418,2479` re-assigns `style.width` to itself in a `setTimeout` to "trigger" an animation that never ran — a no-op in a file this change does not touch; the cable-pulling select at 6 px rather than §1's 8 px for inputs — 6 is on the scale and matches the app's own selects.

## Scanner notes

- `check_error_state.py` flagged `video-digest/pages/listen.js:492`, an empty `catch` holding only a comment, because the apostrophe in the comment ("shouldn't") opened a quote in `_block_at`'s scan and the block ran on into later code. Quote tracking does not skip comments. The site is fixed in code and no longer flagged (its new comment has no apostrophe); the scanner bug is unfixed.
- On `music-library` the error card sits in one cell of the `auto-fill` grid, as `.ml-empty` did before it. Not a regression; `.eos-error-state` has no `grid-column: 1 / -1`, so any grid container squeezes it.
