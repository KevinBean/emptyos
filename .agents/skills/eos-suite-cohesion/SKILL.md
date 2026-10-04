---
name: eos-suite-cohesion
description: Run one suite's cohesion pass — read-verify the member apps, write the overlaps/bonds/merge-candidates report, design the surface read contract, and decide the surface (promote-before-create). Use when the user says "cohesion pass on <suite>", "suite report for X", "bond the <suite> apps", or a suite's turn comes up after the Life pilot. NOT for building the surface app itself (that's a follow-up build session) or system-wide restructuring (use eos-restructure).
---

# EOS Suite Cohesion Pass

One suite per session. The Life pilot (2026-07-18) is the worked example:
report `docs/suites/life-cohesion.md`, contract `timeline_items` +
`[[contributes.life.timeline]]`, surface `apps/public/standard/life/`.
Catalog: `suites.toml` (repo root); validator: `python scripts/check_suites.py`.


## Prerequisites

None that block the pass. Membership comes from the suite's `[[suite]]` entry in
`suites.toml`, and steps 2-4 are a filesystem sweep — all of it works with the daemon
down.

Step 3 alone touches `/api/topology`, for connectivity **evidence** only. That route is
not auth-exempt, so in `network.mode = "private"` it needs the bearer token from
`emptyos.toml`; without it the call returns `{"error":"unauthorized"}`, which reads
exactly like a daemon that is down (`.claude/rules/environment.md`). If you cannot
reach it, record the connectivity column as unknown and finish the pass — do not
start or restart the daemon (`.claude/rules/daemon-handling.md`).

## Steps

### 1. Scope from the catalog
Read the suite's `[[suite]]` entry in `suites.toml` (members, surface, line).
Grep `docs/DEFERRED-WORK.md` for prior flags on these apps.

### 2. Facts sweep (Explore agent, read-verified)
Launch one Explore agent over the member apps. Require file:line anchors for
every claim. Per app: (a) data model + date-scoped/"recent" read surfaces with
one item's key shape; (b) events emitted/subscribed ([provides.events] +
@on_event); (c) cross-app awareness (call_app targets, optional_apps, hub
contributions); (d) any existing aggregation method (list_all / panel_* /
timeline / recent). Plus: does the reactor ripple any of their events into
journal? End with a who-knows-whom matrix.

### 3. Write the report — docs/suites/<suite>-cohesion.md
Mirror the Life report's sections:
- **The overlap** — same-job-unaware surfaces, quoted with file:line.
- **Missing bonds** — events/call_app/shared-shape gaps that keep members
  from feeling like one product. Topology (`/api/topology`) is connectivity
  EVIDENCE only — product jobs decide membership, never the graph.
- **Merge candidates** — FLAG ONLY (wire first, merge last).
- **Membership corrections** — if read evidence shows an app belongs in a
  different suite, move it in `suites.toml` and re-run `check_suites.py`.
- **The read contract** — the narrow shape the surface needs (see step 4).
- **Go/no-go section** — empty checkboxes for the dogfood verdict.

### 4. Design the surface contract (only if this suite gets a surface)
- **Promote-before-create**: can an existing member host the integrated view
  (Companion → portal, Tasks & Projects → projects)? A NEW app only when no
  member can. Record the decision + rationale in the report.
- Contract = a purpose-built plain async method on each contributing member
  (NOT boards `list_all` unless it genuinely fits), declared
  `[[contributes.<suite>.<slot>]]` in the MEMBER manifests, collected by the
  surface via `call_contributions("<suite>", "<slot>", **kwargs)` — fail-soft,
  zero hard member deps, surface owns no data.
- Clamp loose numeric args with `emptyos.sdk.utils.clamp_days` (or a sibling).
- Each member also gets a thin `GET /api/<slot>-items` route so the contract
  is HTTP-testable per member; add a contract test case in each member's
  `tests/test_sys_<app>.py`.

### 5. Verify like the pilot
Sandbox lease (never :9000): dark contract with flag off, merged output with
flag on, member suites stay green, UI walk screenshot if a surface page
exists. Update `docs/DEFERRED-WORK.md` rows (close this suite's row; add
follow-ups). Commit scoped files only (parallel-session staging rules).

## Boundaries
- Analysis + contract + (optionally) a dark surface — NOT wiring every found
  bond and NOT executing merges in the same session.
- Never let cluster/topology output overrule product-job membership.
- Surface apps stay dark (`[apps.<id>] feature.enabled`, unquoted nested key)
  until their own go/no-go.
