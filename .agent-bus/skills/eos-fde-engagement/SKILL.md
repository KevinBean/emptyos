---
name: eos-fde-engagement
description: Run a Forward-Deployed-Engineer (FDE) engagement loop end-to-end using EmptyOS as the rapid-prototyping substrate — discovery → engagement brief → thin-slice prototype → touchable demo → measured value-proof → handoff. Use when the user says "FDE engagement", "run an FDE loop", "land an AI solution for <customer>", "prototype a demo for <customer/team>", "prove value for <use case>", or is preparing an FDE-style customer/portfolio piece. The value-proof phase (measured manual-vs-tool before/after) is the differentiator — never invent the numbers. NOT for building a generic EmptyOS app for yourself (use eos-new-app) or for job-posting evaluation (use life-job-evaluator).
---

# FDE Engagement Kit

A **Forward Deployed Engineer** doesn't build the model or the product — they make an AI capability *land* at one customer's messy reality, fast, and prove it moved a number. This skill runs that loop with EmptyOS as the substrate: the 16 capabilities, apps-as-atoms, auto-UI, viz, and deployment lanes are exactly the FDE's rapid-prototyping kit. The skill adds the **engagement-shaped wrapper** EmptyOS lacks — and the **value-proof** step, which is what separates an FDE from a demo-builder.

The whole game is the *speed of the loop*: vague pain → working thing they can touch → a measured before/after → deployed in their environment. Optimize for that, not for code elegance.

## When to use

- A real or prospective FDE engagement: a customer/team with a workflow pain that an AI intervention could land.
- A **portfolio piece** proving "I can FDE" (energy-domain especially — ties to the FDE-transition track).
- Any "prototype + prove value for X" ask where the deliverable is a touchable artifact plus evidence it works.

## When NOT to use

- You want a durable EmptyOS app for your own use → `eos-new-app`.
- You're scoring a job posting → `life-job-evaluator`.
- Pure research with no build/prove intent → `deep-research`.
- The customer pain is genuinely out of EmptyOS's capability reach (heavy bespoke ML training, real-time control loops) — say so honestly rather than forcing a thin demo.

## The loop (the spine)

```
1 Discovery  → 2 Brief  → 3 Prototype  → 4 Demo  → 5 Value-proof  → 6 Handoff
   (listen)    (scope)    (thin slice)   (touch)    (measure)        (transfer)
```

Run in order. The discipline at each gate: **don't advance until the prior gate's artifact exists.** The classic FDE failure is skipping discovery to start building, then building the wrong thing fast.

All engagement artifacts live in one project dir (project standard, CLAUDE.md § Project standard):

```
{vault}/10_Projects/fde-<slug>/
├── fde-<slug>.md      # main note = the engagement brief (Phase 2)
├── docs/              # demo script, capability-match decision, handoff one-pager
├── assets/            # demo artifacts (viz HTML, screenshots, exported bundle)
└── log/              # value-proof measurements (Phase 5), session notes
```

`<slug>` is kebab-case of the customer/use-case. Brief frontmatter carries `tags: [fde-engagement]` so every engagement is queryable.

---

## Phase 1 — Discovery (listen)

The FDE's first job is to understand the customer's reality, not to pitch. Extract these — interview the user, or read a pasted call transcript / notes. Use `capture` for raw notes as you go.

**The seven that matter:**

1. **Customer + who feels the pain** — the actual person whose day this changes, not the org chart.
2. **The workflow today** — the literal steps they do now, in order. Watch for the manual, repetitive, judgment-light stretch — that's the wedge.
3. **Data sources** — what exists, in what shape (files, spreadsheets, a DB, an API, emails, PDFs), and *can you actually get it* (or a representative sample / mock).
4. **Pain points** — where it's slow, error-prone, inconsistent, or hated.
5. **The ONE success metric** — the single number that, if it moved, the customer would call this a win (time per task, throughput, error rate, decision latency). **If you can't name it, you're not ready to build.**
6. **Constraints** — data residency / on-prem, security, cloud-consent posture, who can see what. (Maps to EmptyOS `network.mode`, cloud-consent gate, vault-to-cloud rules.)
7. **The "magic moment"** — what would make them lean in during a demo.

Output of this phase is just understanding. Don't write the brief until you can state the **smallest intervention hypothesis**: *"If we automate [narrow slice], the [success metric] moves from [baseline] to [target]."*

---

## Phase 2 — Brief (scope)

Write the engagement brief as the project main note `10_Projects/fde-<slug>/fde-<slug>.md`. This is the contract — what you're proving, for whom, measured how. Keep it to one screen.

```markdown
---
tags: [fde-engagement]
customer: <name>
domain: <e.g. BESS operations, grid connection, document triage>
status: discovery | prototyping | demo | proven | handed-off
success_metric: <the ONE number>
baseline: <current value of that number, or "TBD — measure in Phase 5">
target: <what we aim to hit>
created: <YYYY-MM-DD>
---

# FDE: <customer> — <use case>

## The pain
<2-3 sentences. The person, the workflow, the slow/error-prone stretch.>

## Hypothesis (smallest intervention)
If we [narrow AI slice], then [success_metric] moves from [baseline] → [target].

## Data
- Source: <what / shape / how obtained>
- Sample: <real / mock / representative subset>
- Sensitivity: <constraints that gate cloud use>

## Scope — in
- <the ONE thin slice we build this engagement>

## Scope — out (explicitly)
- <everything we are NOT building — guards against scope creep>

## Capability match
<which EmptyOS capability/app — see decision table in the skill>
```

The **Scope — out** section is load-bearing. FDEs lose engagements by building too much; naming what you won't build is how you ship in days.

---

## Phase 3 — Prototype (thin slice)

Match the pain shape to an EmptyOS capability, then build the **thinnest slice that demonstrates the hypothesis** — pointed at real customer data if you have it, a representative mock if you don't (label mocks honestly; never invent customer numbers — see portfolio honesty rules in Phase 5).

**Capability-match decision table** — write the chosen row into `docs/capability-match.md` with a one-line rationale:

| Customer pain shape | EmptyOS capability / app | Thin-slice prototype move |
|---|---|---|
| "We read/triage piles of documents" | `read` (markitdown) + `think` + `search` | ingest → summarize → route/tag |
| "We answer the same questions over and over" | `think` + vault-aware Q&A (`assistant`) | assistant grounded on their corpus |
| "We decide from scattered data" | `think` + `artifact` (viz) | dashboard / explainer that aggregates the inputs |
| "We hand-produce a report/doc" | `think` + `write` + `render_pdf` (SDK) | one-shot drafted artifact to their template |
| "We need to *see*/explain something complex" | `artifact` (viz: 3d-scene/diagram/chart) | interactive explainer |
| "We extract structure from messy input" | `think` (domain=code, parse) | extraction harness → structured rows |
| "We monitor a stream / want alerts" | events + `scheduler` + `notifications` | reactor handler + scheduled check |
| "We model a physical/engineering thing" | `model` (CadQuery) / `engines/` | parametric model from spec |

Build path:
- New surface → invoke `eos-new-app` (it wires every convention; pass it the brief as `spec=` notes).
- Reuse → wire an existing app / contribute a hub-panel or voice-intent.
- One-shot visual → `viz` / `designer` directly, no app needed.

**Speed discipline:** if it takes more than a day or two, the slice is too wide — cut it. A working narrow thing beats a broad half-thing every time. Verify it runs (CLAUDE.md Dev Rule 10: testable from `localhost:9000` or it's not done). Use a leased sandbox member to verify Python changes without touching `:9000` (`.claude/rules/sandbox-driven-testing.md`).

---

## Phase 4 — Demo (touch)

The deliverable the customer can put their hands on. Save the artifact under `assets/` and a tight demo script under `docs/demo-script.md`.

- Lead with the **magic moment** from discovery, not a feature tour.
- Use the customer's *own* data/example so they recognize it.
- Make it touchable: the app UI, a viz one-shot HTML, or an exported standalone bundle (`eos app export <id>` — runs without the daemon, easy to hand over).
- The demo script is 5-7 beats: setup → the pain restated → the magic moment → the result → "and here's what it took."

Keep it honest — show the thin slice working on a real instance, not a polished mock of a thing that doesn't run.

---

## Phase 5 — Value-proof (measure) — the differentiator

This is what nothing else in EmptyOS does, and it's where FDE value is won. A demo shows it *works*; value-proof shows it *moved the number*. Write to `log/value-proof.md`.

**Measurement protocol (measure, don't vibe):**

1. Pick ONE representative real task instance (from discovery).
2. **Baseline:** do (or have the customer do) the task the current manual way. Timestamp start→end, count discrete steps, note rework/error points. Capture ≥1 sample; more is better.
3. **Tool-assisted:** run the same task instance through the prototype. Timestamp, count steps, note where a human is still needed.
4. **Delta:** time %, step count, and the qualitative wins (consistency, fewer errors, lower decision latency).
5. **ROI line:** scale the per-task delta by frequency.

```markdown
---
tags: [fde-engagement, value-proof]
engagement: fde-<slug>
metric: <success_metric>
---

# Value-proof — <customer> / <task>

| | Manual (baseline) | Tool-assisted | Delta |
|---|---|---|---|
| Time per task | 47 min | 6 min | −87% |
| Discrete steps | 14 | 3 | −11 |
| Rework points | 2-3 | 0 | — |

**ROI:** ~<N>×/week × time saved ≈ <X> hrs/week reclaimed for <person/team>.
**What still needs a human:** <the judgment the tool deliberately leaves with them>.
**Method:** <measured live on <date> / estimated from <source> — labeled>.
```

**Honesty rules (hard — `portfolio_no_invent` + `confidential_case_studies`):**
- Never invent customer numbers. Measure them, or label them clearly as **estimates** with the basis.
- For portfolio/demo use with confidential data, use **labeled illustrative** numbers and say so on the artifact.
- Report what still needs a human — overclaiming kills FDE credibility faster than a modest, true delta.

Update the brief frontmatter: `status: proven`, fill `baseline` + `target` with the measured values.

---

## Phase 6 — Handoff (transfer)

Make it the customer's, not yours. Two artifacts under `docs/`:

1. **`eos app info <id>`** output (self-documenting — CLAUDE.md Dev Rule 6) → save as `docs/app-info.md`.
2. **`docs/handoff.md`** — a customer-facing one-pager: what was built, how to run it, what it costs (cloud/compute), what's deliberately out of scope, and the obvious next slice. Use `render_pdf` (SDK) if they want a branded PDF.

Set brief `status: handed-off`. If this was a portfolio piece, suggest `/eos-devlog-publish` to surface it (after a leak/branding check — never expose customer identity without consent).

---

## FDE anti-patterns (the loop's failure modes)

- **Building before discovery.** No named success metric → you're guessing. Go back to Phase 1.
- **Scope creep.** Every "while we're at it" widens the slice and slips the ship date. The Scope-out list is your weapon.
- **Demo that can't deploy.** A beautiful thing that only runs on your laptop isn't landed. Mind the constraints (Phase 1.6) and EmptyOS deployment lanes from the start.
- **No value-proof.** "It works" without "it moved X" is a science-fair project, not an FDE win.
- **Invented numbers.** One fabricated metric and the whole engagement's credibility is gone.
- **Over-automating judgment.** FDE = "with you, not for you" at the customer scale — leave the human the irreversible/judgment calls; automate the toil. (Mirrors EmptyOS's own north star.)

## Doctrine grounding (the canon)

The loop and anti-patterns here are grounded in the FDE canon, digested into the KB:
- `30_Resources/EmptyOS/kb/notes/fde-role-and-when-it-fits.md` — what the role is, the three prerequisites for when the motion fits at all.
- `30_Resources/EmptyOS/kb/notes/fde-traits-and-delivery-doctrine.md` — prove-value-in-production motion, the five traits, the services-trap and other failure modes.

Read these before a real engagement — especially the "when it fits" prerequisites (high-ACV, opinionated-agnostic vision, heterogeneous needs); they're the gate on whether to run the loop at all.

## Relationship to other skills/apps

- `eos-new-app` — the prototype-build step delegates here for new surfaces.
- `viz` / `designer` apps — the demo-artifact engines.
- `capture` / `projects` — discovery notes + the engagement project dir.
- `emptyos.sdk.pdf.render_markdown_pdf` — branded handoff PDFs.
- `eos-devlog-publish` — surface a portfolio engagement publicly (consent + leak check first).
- `.claude/rules/sandbox-driven-testing.md` — verify prototype code without touching `:9000`.
- FDE-transition track (career) — completed engagements are the strongest portfolio evidence for the pivot.

## Vault connection

Requires vault connection for the brief + value-proof notes. Check `.claude/vault-connection.json`. Engagement root: `{vault}/10_Projects/fde-<slug>/`.
