# EmptyOS Documentation

The map of the docs. Find the doc for what you're trying to do. Each entry is tagged
`[authored]` / `[generated]` and `[shipped]` (in a public clone) / `[internal]`.

> How the docs themselves are structured — surfaces, audiences, generators — is in
> **[DOC-SYSTEM.md](DOC-SYSTEM.md)**. Read that before changing how docs work.

---

## I want to use EmptyOS

| Doc | |
|---|---|
| [../README.md](../README.md) | What EmptyOS is, why, quick start, architecture · `[authored] [shipped]` |
| [GETTING-STARTED.md](GETTING-STARTED.md) | Install, configure providers, first boot · `[authored] [shipped]` |
| [DESKTOP.md](DESKTOP.md) | EmptyOS Desktop — the double-clickable Windows build: install, updates, uninstall · `[authored] [shipped]` |
| [APPS.md](APPS.md) | The app catalog — every app, what it does, its capabilities · `[generated] [shipped]` |
| [PRIVACY.md](PRIVACY.md) | What stays local, what cloud consent means · `[authored] [internal]` |

## I want to build on EmptyOS

| Doc | |
|---|---|
| [APP-DEVELOPMENT.md](APP-DEVELOPMENT.md) | Build an app — pattern, manifest, UI · `[authored] [shipped]` |
| [APP-SPECS.md](APP-SPECS.md) | Per-app spec sheets · `[authored] [internal]` |
| [FRONTEND-DESIGN-LANGUAGE.md](FRONTEND-DESIGN-LANGUAGE.md) | Visual + interaction DNA — read before touching a page · `[authored] [internal]` |
| [CONVERSATION-STACK.md](CONVERSATION-STACK.md) | The 5 chat backends × 8 frontends, and when to use which · `[authored] [internal]` |
| [CONTEXT-PACKING.md](CONTEXT-PACKING.md) | Token-compression domain · `[authored] [internal]` |
| [EOS-MARKDOWN-PROFILE.md](EOS-MARKDOWN-PROFILE.md) | Vault Markdown storage + rendering contract · `[authored] [internal]` |
| [SOFT-SCHEMA.md](SOFT-SCHEMA.md) | VaultModel soft-typed frontmatter · `[authored] [internal]` |
| [ENGINEERING-APP-WORKFLOW.md](ENGINEERING-APP-WORKFLOW.md) | Build an engineering calculator end-to-end · `[authored] [internal]` |
| [TRUST-LOOP.md](TRUST-LOOP.md) | Seven-stage engineering-assurance package contract - `[authored] [internal]` |
| [ROOMS-V3.md](ROOMS-V3.md) · [app-builder.md](app-builder.md) | Rooms review gate · in-app app generation · `[authored] [internal]` |
| [AGENT-RUNNER-MIGRATION.md](AGENT-RUNNER-MIGRATION.md) | AgentRunner contract — depend on a runner shape, not Claude Code · `[authored] [internal]` |
| [VOICE-SATELLITE.md](VOICE-SATELLITE.md) · [PAPER-DASHBOARD.md](PAPER-DASHBOARD.md) | Hardware satellites — voice puck · e-ink display system design · `[authored] [internal]` |
| [SKILLS.md](SKILLS.md) | The skill matrix — every repo skill, theme, triggers, routing · `[generated] [internal]` |
| `.claude/rules/*` | ~45 pattern rules (addons, hub-panels, voice-intents, multi-module-apps, …) · `[authored] [internal]` |

## I want to deploy / package EmptyOS

| Doc | |
|---|---|
| [DEPLOYMENT.md](DEPLOYMENT.md) | The 5 deployment lanes; Hetzner + Caddy walkthrough · `[authored] [internal]` |
| [PACKAGING.md](PACKAGING.md) | Choose the packaging/runtime layer; author a distribution from a Space · `[authored] [internal]` |
| [TIERS.md](TIERS.md) | Tiers & packages — what each bundle contains, platforms, release targets · `[generated] [shipped]` |
| [RELEASING.md](RELEASING.md) | Release flow + tier packaging · `[authored] [internal]` |
| [AUTH.md](AUTH.md) | Single-user auth pin + rationale · `[authored] [internal]` |
| [CLOUD-ARCHITECTURE.md](CLOUD-ARCHITECTURE.md) | Storage domains + cloud-migration posture — vault stays markdown, services migrate around it · `[authored] [internal]` |
| [PUBLISHING.md](PUBLISHING.md) | The publish app / static-site pipeline · `[authored] [internal]` |

## I want to understand or evolve the system

| Doc | |
|---|---|
| [DESIGN.md](DESIGN.md) | Architecture, philosophy, consciousness model · `[authored] [shipped]` |
| [WORK-SURFACE-PIVOT.md](WORK-SURFACE-PIVOT.md) | Outcome-first pivot — the `work` app, deliverable registry, build order · `[authored] [internal]` |
| [../CLAUDE.md](../CLAUDE.md) | Conversation-mode boot prompt — full architecture for an AI tool · `[authored] [shipped]` |
| [SYSTEM-INTERNALS.md](SYSTEM-INTERNALS.md) | Kernel boot, loaders, event bus internals · `[authored] [internal]` |
| [DOGFOOD-AGENT.md](DOGFOOD-AGENT.md) · [fix-agent.md](fix-agent.md) | The self-test / self-fix loop · `[authored] [internal]` |
| [DOC-SYSTEM.md](DOC-SYSTEM.md) | How the docs are structured + the generators · `[authored] [internal]` |

---

## Generated docs

These are produced from code — **do not hand-edit**; run the script to refresh
(see [DOC-SYSTEM.md](DOC-SYSTEM.md)):

- [APPS.md](APPS.md) ← `scripts/generate_apps_doc.py`
- [SKILLS.md](SKILLS.md) ← `scripts/generate_skills_doc.py`
- [TIERS.md](TIERS.md) ← `scripts/generate_tiers_doc.py`
- eos.binbian.net site pages ← `scripts/generate_emptyos_site.py`

## Point-in-time docs (history, not current reference)

`BACKLOG.md`, `MIGRATION.md`, `HOME-COMPANION-REDESIGN.md`, `EMPTYOS-EM-ROADMAP.md`,
`OPEN-SOURCE-BORROWING-PLAN.md`, `KB-APP-ALIGNMENT-AUDIT.md`, `EXPORT-ANALYSIS.md`,
`AGENT-BENCH.md`, `AGENT-TEAM-PATTERNS.md`, `CABLE-REPORT-LIMITS.md`,
`cable-rating-em-engine-integration.md`, `ENGINEERING-WORK-LOOP.md`, `legacy/` — kept for
history. They captured a moment; verify against the code before relying on them.
