

| What | Where | Purpose |
|---|---|---|
| **System prompt** | `CLAUDE.md` | Full architecture, capabilities, conventions, development rules, gotchas |
| **Architecture doc** | `docs/DESIGN.md` | Deep design: runtime modes, capability system, consciousness model, UI philosophy |
| **Behavioral rules** | `.claude/rules/*.md` | Rules (docs-sync, vault-operator) |
| **Skills / procedures** | `.claude/skills/*/SKILL.md` | Reusable procedures |
| **Work loop** | `docs/ENGINEERING-WORK-LOOP.md` | How EmptyOS builds any feature: strategy → brainstorm → plan → work → review → compound-learning, each phase mapped to a live surface |
| **Public docs** | `README.md`, `docs/GETTING-STARTED.md`, `docs/APP-DEVELOPMENT.md` | External-facing |

Read `CLAUDE.md` first — it is the boot prompt. It loads the architecture, philosophy, and dev rules needed for coherent contributions.

> **Don't import an external "compound engineering" / agent-fleet framework.** EmptyOS already runs the full strategy→…→compound-learning loop, distributed across its own apps (`grill`, `app-builder`, `feature-pipeline`, `dogfood-agent`, `fix-agent`, KB lessons, `agent-bus`, session-wrapup). See `docs/ENGINEERING-WORK-LOOP.md` for the map and `docs/OPEN-SOURCE-BORROWING-PLAN.md` for why borrowed frameworks were taken as *ideas → docs*, not installed.
