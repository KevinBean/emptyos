---
paths:
  - "engines/**"
  - "apps/extension/engineering/**"
  - "apps/public/standard/trust-loop/**"
  - "apps/**/feature-pipeline/**"
  - "docs/ENGINEERING-APP-WORKFLOW.md"
  - "docs/TRUST-LOOP.md"
  - "scripts/check_engineering_assurance.py"
---

# Engineering Calculator Workflow + Trust Loop

Moved out of CLAUDE.md § Apps (2026-09-25). CLAUDE.md keeps a one-line pointer.

To build an **engineering calculator** (algorithm doc → requirements → KB notes → pure engine → method-registry app → conformance → `basic-engineer` release bundle), follow `docs/ENGINEERING-APP-WORKFLOW.md` — the in-app pipeline (`/grill/` engineering-calculator recipe → `/app-builder/` → `/kb/` Digest doc → `engines/` → `[[provides.methods]]` → `[[provides.conformance]]` → `/release/`), optionally run autonomously via `feature-pipeline`'s conformance-gated loop.

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

Related: `.claude/rules/engineering-tier-membership.md` (what joins
`basic-engineer`), `.claude/rules/conformance-anchors.md` (when a green anchor
proves nothing).
