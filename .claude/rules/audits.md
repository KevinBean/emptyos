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

## Failure mode 3 — the check is green because it checks nothing

The first two failure modes are about a check that fires too often or stops
running. This one is worse because it looks like success: **a check that passes
on a healthy tree has proved nothing at all until you have watched it fail.**
Green is the expected state, so it is indistinguishable from green-for-the-
wrong-reason, and the check then sits in CI for months certifying a defect
class it never actually inspects.

**The rule: break the thing deliberately and watch the check go red — once per
failure shape it claims to cover, not once overall.** The procedure is
`.claude/skills/eos-mutation-verify`; don't restate it, invoke it.

What this section adds is that the rule is **not scanner-specific**. It binds
equally to test assertions and to conformance anchors:

- **Scanners** — `check_helper_bindings.py`'s first "0 findings" version stayed
  green when a `@web_route` binding was deleted, precisely the case where an
  unbound helper is worst (the endpoint silently stops existing). Catalog entry
  below.
- **Test assertions** — a chart test asserting "the SVG has numeric tick text"
  passed against a build whose y axis was completely blank, because the *x* axis
  still carried a label. Measured 2026-08-28, on a test written for a bug that
  had already shipped.
- **Conformance anchors** — a number back-filled from the implementation rather
  than transcribed from the published source proves self-consistency, not
  correctness. It cannot go red for the shape that matters.

**"There is nothing to anchor" is usually a claim about the wrong quantity.** A
composite result can be unbenchmarkable while a *factor* inside it is published,
and anchoring the factor is worth far more than anchoring nothing. The earthing
app's manifest asserted for months that its EG-0 method had "no fixed scalar to
anchor" — true of the **risk**, which depends on an exposure model nobody
publishes, and false of the **physiology** under it: AS 2067 App. A publishes
P_fibrillation = 0.652 at 430 V / 500 ms, and the engine returned 0.6546
unmodified. The whole probabilistic path shipped unverified behind a sentence
that was accurate about one thing and read as a verdict on everything.

The test for a factor worth anchoring is **independence**: P_fibrillation is a
function of touch voltage and duration only, so no exposure input can
contaminate it — which is exactly what licenses pinning one field of a case
whose other half has no published value. Assert that independence too
(`test_exposure_inputs_cannot_move_the_anchored_field`), or the anchor quietly
stops meaning what the manifest says it does. And anchor only the published
field: adding `Z_B` or `I_B` there, because the worked example implies them,
back-fills from the implementation and lands straight back in the row above.

The generalisable trap, and the reason the chart case is worth recording: **an
assertion scoped wider than the thing it names is satisfied by a healthy
neighbour of the broken part.** "Somewhere in this page / file / response there
is an X" passes while the specific X you meant is missing. The fix is usually to
narrow the assertion to the named unit — which may require making the markup
addressable first (that test needed `.eos-tick-x/-y` classes before it could
assert per-axis at all). Budget for that: a check can be un-writable until the
thing it checks is made observable.

It wears three disguises, and only the third is really surprising. A value
printed on **two rows** (or a word on two lines) where deleting one occurrence
still satisfies the whole-document assertion. A section that renders **only on
failure**, which a reader cannot tell apart from one never implemented. And a
test that **greps the source** for a marker string — which the explanatory
*comment above the code* satisfies on its own, so the guard can be disabled and
the grep stays green. All three appeared in one session (2026-09-01), none was
caught by the suite going green, and only mutation found them. For the grep
shape the fix is not a narrower grep: stop reading the file and **execute the
handler**.

**Six more shapes of the same failure** — each has a measured case in
`.claude/rules/audits-casebook.md` (scanners, tests) or
`.claude/rules/conformance-anchors.md` (engineering anchors). Read the relevant
file before writing that kind of check.

- **An identity field copied from the request is a label, not evidence** — trace
  a provenance field to the response side before trusting it.
- **A defensive `except` inside a scanner reports "no findings" whenever it
  cannot parse its own input** — assert the input shape up front and fail loud.
- **A test of a pure helper never proves anything calls it** — assert the call
  site; when mutation-verifying, delete the wiring, not the helper.
- **An anchor on the first equation of a chain proves nothing about the chain**
  — anchor the deepest published value, one case per branch.
- **An anchor's inputs must come from the source, and its tolerance must exclude
  the wrong answer** — grep the source for every literal; published value ± a
  tight absolute, never a range.
- **An uncertainty flag keyed on the winner cannot see a loser that would
  overtake it** — re-run the selection under each documented alternative reading.

## Graduation

The graduation catalog (mobile overflow, click-intercept, readability,
affordance, helper bindings, spoken register, skill refs) lives in
`.claude/rules/audits-casebook.md`.

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
