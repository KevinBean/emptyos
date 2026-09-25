---
paths:
  - "scripts/check*.py"
  - "scripts/*audit*.py"
  - "scripts/preflight.py"
  - "scripts/scanner_lib.py"
  - "scripts/audit_driver.py"
  - "tests/test_unit_check_*.py"
  - "tests/test_sys_readability.py"
  - "tests/test_sys_ui_affordance.py"
  - "tests/test_sys_mobile.py"
  - "emptyos/web/static/eos-audit-walk.js"
  - "emptyos/web/static/eos-readability.js"
  - "emptyos/web/static/eos-ui-affordance.js"
  - ".claude/skills/eos-graduate-audit/**"
  - ".claude/skills/eos-mutation-verify/**"
---

# Audits Casebook — worked cases behind `audits.md`

Split out of `audits.md` (2026-09-25). `audits.md` keeps the failure modes and
the rules; this file keeps the measured cases that justify them. Read it before
writing or tightening a scanner, a checker test, or a rendered audit.

## More failure-mode-3 shapes

**An identity field copied from the request proves nothing about what
answered.** A check that compares "what ran" against "what was asked for" is
only as good as where "what ran" comes from — and if it is the request echoed
back, the comparison is equality by construction and can never go red. Found
2026-09-21 in `scripts/run_model_matrix.py`: its SUBSTITUTED flag compared each
run's recorded `subject_model` to the pinned model, but `openai_compat` stamps
its own configured `self.model` onto usage and never reads the model the server
returned, so a wrong-host fallback still recorded the requested name. The flag
was removed rather than kept as decoration. Before trusting an identity or
provenance field, trace it to the **response** side; if it originates on the
request side, it is a label, not evidence.

**A defensive `except` inside a scanner is the same failure wearing a helpful
face.** `try: rx.search(line) / except AttributeError: continue` reads as
robustness and means *"whenever I do not understand my own input, report no
findings"* — the scanner then passes on every file, forever, in the reassuring
voice of a clean run. Found 2026-08-30 in a brand-new credential scan whose
pattern list turned out to be `(name, regex)` tuples rather than bare regexes:
every line raised, every raise was swallowed, and a planted `sk-ant-…` key was
reported clean. The fix is not a better `except` — it is **asserting the shape
of the input up front and letting an unexpected one fail loudly**, because a
scanner that cannot parse its own patterns has nothing to say about the tree.
Applies equally to `except Exception: pass` around a parse, a fetch, or a
subprocess inside any gate.

**A test of a pure helper proves the helper works, never that anything calls
it.** This is the same failure one layer out, and it is the easiest of all to
ship, because the helper's own coverage genuinely looks exhaustive. Measured
four times in one session (2026-08-31, markitup): `parse_viewport` had 25
passing tests against a `navigate()` that never invoked it — replacing the two
wiring lines with `vp = None` left all 25 green; a test fed `EDITABLE_TAGS`
into the script builder and asserted they came back out, so pointing the real
call site at a hand-written `["p", "h1"]` passed **and** silently dropped the
anchor cap from 300 to 5; a badge-variant guard compared the app's tag table
against a hand-copy of the vocabulary *in the same module*, so adding an
invented variant to **both** passed while the badge, the pin and the PDF swatch
all lost their colour on screen; and complete coverage of two
credential-handling functions stayed green when **both** of their call sites
were deleted.

Two tells, either of which is enough: the assertion's expected value is defined
in the file under test (circular), or nothing in the suite names the
**consumer**. The fix is to reach for the call site rather than the function —
`ast`-read the real invocation and assert the argument it actually passes, or
drive the entry point and observe the effect. `tests/test_unit_markitup_wiring.py`
is the worked example, and its module docstring states the shape so the next
reader does not have to rediscover it.

One discipline when mutation-verifying this class: **delete the wiring, not the
helper.** A broken helper fails the helper's own tests, which proves only that
those tests exist. The mutation has to be the one a careless refactor would
actually make — a call site removed, an argument replaced by a literal, a
binding dropped — because that is the change no amount of pure-function
coverage can see.

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
  pins the two highest-volume shapes — `#fff` on `var(--accent)` (T1), and
  hardcoded status hex as a text colour (T2) — neither of which has a legitimate
  use. It caught 6 violations the regex sweeps had missed on its first run.
  Prevention rules: `docs/FRONTEND-DESIGN-LANGUAGE.md` §4.1.

  Two rules were added later. **T3** catches a non-theme.css stylesheet
  redefining a global token at `:root` — same specificity as `.theme-*` but
  loaded after it, so it silently replaced the active theme page-wide.
  **T4** (2026-08-17) extends T2's literal one property over, to a status hex
  painting a *surface* (background / border / outline): `--red/--amber/--green`
  alias the per-theme status tokens, so the literal is frozen where the token
  adapts. Unlike T1-T3 the signal does not separate on its own — it needs four
  measured exclusions (gradients, 3+-hex palette lines, quoted JS values,
  `var(--token, #hex)` fallbacks) plus a brand-island skip, and its literal set
  is **empirical**: a hex earns membership from being used as *state* in this
  tree, never from its position in a colour ramp. Purple and `#38bdf8` were
  measured and excluded on exactly that test — every occurrence was identity
  (a named pill, a chart series, a speaker, a deck accent), so adding them would
  only manufacture false positives. T4 found six defects in the **shared
  bundle** (`.eos-bar-*`, `.obs-callout-*`), which no per-app pass can surface.

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

- **Bound-helper bindings (2026-08-15)** — a multi-module app's spine forgetting
  a `<name> = _mod.<name>` line. Graduated straight to
  `scripts/check_helper_bindings.py` (preflight `apps`/`release`, **gates**) +
  `tests/test_unit_check_helper_bindings.py`, generalising the per-app pin
  `tests/test_unit_publish_media_bindings.py`.

  Two things here are worth copying. First, the **narrowing** is the whole
  audit: "every module-level def must be bound" flags 36% of modules and "every
  def taking `self`" flags 9%, and in both bands nearly every hit is correct
  behaviour — a module-private `_helper(self, …)` invoked inside its own module
  needs no binding. Only requiring a *reason* to be bound (self-called, OR
  decorated, OR a `call_app` verb) separates the signal, at 0 findings across
  235 modules.

  Second, and more transferable: the first "0 findings" version was a **vacuous
  pass**. It stayed green when a `@web_route` binding was deleted, because a
  route handler is never called as `self.api_x(` — precisely the case where an
  unbound helper is worst, since the endpoint silently stops existing. This is
  the worked example behind § Failure mode 3 — the check was green because it
  checked nothing.

  **Extended 2026-08-30 to the inverse direction, after the same gate was green
  through a live outage.** The original catches a helper defined but never
  bound; it could not see a binding naming something the helper *no longer
  defines*. That is the worse half — an unbound helper disables one endpoint,
  while a dangling binding raises `AttributeError` as the class body executes,
  so the app never loads and everything declaring it in `[requires].apps` goes
  down too. A rename that reached `english/events.py` and not its spine took out
  four apps for two days, surfacing only in syslog. The generalisable point:
  **a checker that models a relationship should be asked which direction it
  actually walks** — "every A has a B" and "every B has an A" are different
  checks, and here the un-walked one was the destructive one. Four
  false-positive guards were needed to make it gate (re-exported imports,
  conditional defs, `__getattr__`/star-import skips, non-local aliases), each
  with its own mutation test, since a broken guard is what gets a gate disabled.

  **Extended again 2026-08-31 to the convention's own drift-prevention device.**
  Each helper carries a banner comment listing every name it exports, so that a
  reader adding a method sees a binding is required. Nothing validated the
  banner, so it rotted into advertising the opposite: after the rename above,
  english's banner still named `on_reader`, and the one repair it invited —
  restore the binding — re-creates the outage. A row that is neither bound nor
  defined now gates (0 on a healthy tree); the two shapes that cannot stop an
  app loading stay advisory at 5 and 62.

  Three lessons transfer past this checker. **A doc that exists to prevent a
  defect is itself a defect surface** — if it is load-bearing, something has to
  check it. **The advisory band is not automatically a noise band**: the first
  draft justified not-gating by citing this file's >30% false-positive rule,
  which was simply the wrong argument — those findings are *true*, they just
  cannot break anything, and conflating "low severity" with "unreliable signal"
  is what the 30% rule exists to keep apart. And **a false-positive guard must
  suppress by the property it names**: keying the mixin skip on "the qualifier
  matches the spine's import name" instead of "the qualifier is a class"
  silently blinded the gate on 84 rows across 12 modules — including the module
  following the alias-collision convention `multi-module-apps.md` § 7 mandates.

  It is also the sharpest case yet of § Failure mode 3, and the hostile-review
  pass is what caught it. Six tests, all green, all mutation-checked by the
  author — and the whole direction could still be switched off (`started = True`,
  no banner header required) with every test passing and **zero** findings
  repo-wide, because every fixture put the banner on line 1 while a real helper
  opens with a docstring. **A fixture that is not shaped like the real input
  tests a parser you do not ship.** Five of eight mutations survived the
  author's own battery; the gap was that `main()` — which decides what actually
  gates — had no test at all, only `scan()` did.

  **Two more fixture shapes that pass for the wrong reason** (measured
  2026-09-03 on `check_hardcoded_hex.py`, three times in one session, every one
  found by mutation and none by review):

  1. **A fixture covered by two exclusions pins neither.** The case asserting
     `/_retired/` is skipped used the path `apps/personal/_retired/…`, which the
     *personal* rule also skips — so deleting `/_retired/` outright left the
     suite green, on the exclusion the scanner's own docstring calls its
     headline. Same shape sank a black/white test whose three hexes sat in one
     declaration, where the unrelated *palette* rule skipped them first. **A
     fixture must isolate the rule under test from every other rule that could
     also exempt it** — which means listing the other exclusions and checking
     none of them fires, not just checking the assertion is true.
  2. **The expected value has to differ between the correct and the broken
     implementation.** An exit-code test asserting `main() == 1` on a
     *one-finding* fixture cannot tell `return 1` from `return len(findings)`;
     both give 1, and the second exits 0 on exactly 256 findings — success on
     the check's worst-ever run. Use a fixture whose count is *not* the expected
     return.

  The generalisable habit: when a mutation **survives**, do not assume the
  mutation was too narrow. Ask first what *else* in the pipeline could be
  satisfying the assertion — that is nearly always the real answer, and it is a
  defect in the test, not in the mutation.

- **Spoken register (2026-08-19)** — prose drafted to be *said* reading as written
  English: contractions spelled out, semicolons, sentences past one breath. The
  existing `prose_lint.py` was blind to all three and scored a contraction-free
  recruiter email *"clean, 0 findings"*. Graduated to a `spoken=True` rule family
  + `scripts/check_prose_tone.py --spoken` + a preflight `vault` row.

  Two things here transfer. First, the **calibration corpus was the user's own
  writing**, not a synthetic sample — his notes run 69-100% contractions and the
  drafted-for-him brief ran 0%, so the floor could sit at 50% and separate the two
  without touching the former. When the defect is "this doesn't sound like them",
  the standard already exists in their own files; find it before inventing a
  threshold.

  Second, and the reason this one nearly died at the graduation step: **a checker
  for content that does not live in the repo needs an opt-in marker before it can
  run unattended.** There is no way to tell an interview answer from a CV by path,
  and a blanket career-folder scan would have fired register rules on 42 trackers
  and strategy docs — the >30% band that audits.md says not to ship. A `spoken:
  true` frontmatter marker made the corpus explicit and the run honest. Without it
  the only options were "no runner" (the audit decays) or "gate on an ambiguous
  signal" (the audit gets disabled). The marker is what made a third option exist.

- **Dead references inside a skill body (2026-09-12)** — a skill names
  `apps/kb/shared.py`, the file moved to `apps/public/standard/kb/shared.py`, and
  the agent either wastes a search or answers confidently about a file it never
  opened. 16 files, 45 references. Graduated to `scripts/check_skill_refs.py`
  (advisory, preflight `docs` + `skills`) + `tests/test_unit_check_skill_refs.py`.

  Three things transfer, and the second is the one that nearly shipped broken.

  **A single character in a lookbehind hid every finding.** The first version
  excluded a backtick-preceded path — which is exactly how a skill normally
  writes one — so it reported a clean tree while all 13 real rows sat in front
  of it. Caught only because a both-direction test asserted the *commonest*
  form rather than a convenient one. The general shape: when a scanner's
  exclusion is written from memory of "what noise looks like", it will exclude
  the signal, and only a fixture drawn from real input notices.

  **The test suite was 90% decorative and looked thorough.** A hostile mutation
  pass found **10 of its 12 directory prefixes and 11 of its 12 extensions could
  be deleted with all 13 tests green** — coverage was concentrated on two
  filters plus an incidental live-corpus pin. Dropping `md` alone would have
  killed the rules-path and sidecar classes this very audit called its most
  important rows. The fix is to state the contract **in the test file**, not
  read it from the module: an assertion whose expected value comes out of the
  code under test proves self-consistency, and survives every narrowing of that
  code. Parametrize one row per member so a trim produces one red per loss.

  **Two repaired tests were themselves vacuous, and mutation found both.** A
  traversal fixture used a path `exists()` answers True for, so it passed with
  the exclusion deleted; a walk-depth assertion looked for one `/`, which a
  top-level file satisfies. Both had been written *specifically* to close that
  class of gap — which is the point: writing a test against a named failure
  mode does not make it immune to that failure mode.

