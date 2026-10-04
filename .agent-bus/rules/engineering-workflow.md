---
paths:
  - "engines/**"
  - "apps/extension/engineering/**"
  - "apps/public/standard/trust-loop/**"
  - "apps/**/feature-pipeline/**"
  - "docs/ENGINEERING-APP-WORKFLOW.md"
  - "docs/TRUST-LOOP.md"
  - "docs/PRODUCT-LOOP.md"
  - "scripts/check_engineering_assurance.py"
---

# Engineering Calculator Workflow + Trust Loop

Moved out of CLAUDE.md § Apps (2026-09-25). CLAUDE.md keeps a one-line pointer.

To build an **engineering calculator** (algorithm doc → requirements → KB notes → pure engine → method-registry app → conformance → `basic-engineer` release bundle), follow `docs/ENGINEERING-APP-WORKFLOW.md` — the in-app pipeline, whose authoring apps are dev-tier tooling rather than part of the bundle (`/grill/` engineering-calculator recipe → `/app-builder/` → `/kb/` Digest doc → `engines/` → `[[provides.methods]]` → `[[provides.conformance]]` → `/release/`), optionally run autonomously via `feature-pipeline`'s conformance-gated loop.

For engineering calculators, `docs/TRUST-LOOP.md` is the assurance contract
nested inside that delivery wrapper. Its governing rule for any new obligation
is **every obligation carries a receipt** — name the stage that owns it, the
generated or executed artefact that proves it, and *what runs that artefact*.
An obligation answering the first two and not the third is a wish, and will
drift invisibly because the artefact exists and looks maintained.

Participating apps opt in through manifest `[assurance]`;
`scripts/check_engineering_assurance.py` checks the document contract,
requirement traceability, and the `## Data flow` resolve join, but never
substitutes for generated gate receipts. Three of its rules exist only to close
a vacuous pass, where a set difference over an empty set reports success for
work nobody did.

**Beside the Trust Loop runs the Product Loop** (`docs/PRODUCT-LOOP.md`, stages
P1-P5, written 2026-10-03), branching off once Validation is green. Two names, never mixed: the **test bench** is the
Stage 5 interface, for developing and checking; the **product face** is the
interface for a user who did not write the tool. The product face shows only
numbers the test bench can reproduce, computes nothing, keeps no copy of a
case, and links every result back to the same case in the test bench. Its last
two stages are a usability review and a user test; it ends at a usable
interface. How that interface ships (page, app, executable) is a separate,
later decision, and a delivery build re-runs Stage 7 on itself. No checker or
shared kit exists yet; both wait for a second product face.

Related: `.claude/rules/engineering-tier-membership.md` (what joins
`basic-engineer`), `.claude/rules/conformance-anchors.md` (when a green anchor
proves nothing).
