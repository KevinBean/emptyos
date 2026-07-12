# Self-Audit Loops — turn EmptyOS's own tools back on EmptyOS

EmptyOS is "alive" (CLAUDE.md Principle 10) in a specific, concrete way: **every
registry or generator it builds as a feature is also a yardstick it can hold up
to itself.** A tool built *with* the user becomes a tool that improves the system
that built it. This file names that meta-move and catalogs the loops so they're
discoverable and not reinvented.

This is the umbrella over two existing rules — don't duplicate them:
`.claude/rules/test-fix-verify-loop.md` (the behavior/bug loop) and
`.claude/rules/audits.md` (how to run a one-off heuristic and **graduate** it).
This rule adds the *catalog* + the *"capture the host as a peer"* shape that
those two are instances of.

## The shape

1. **Capture the host as a peer.** Put EmptyOS's own state into the same registry
   the feature already uses for external things — so the system is *comparable*
   to a benchmark, not special-cased. (e.g. `design-system-emptyos` puts the
   system's theme in the same `design-system-*` registry as Linear/Vercel.)
2. **Benchmark against the registry.** Ask "where is our own worse than the
   well-made examples we already imported?" Objective metric, not vibes.
3. **Measure → minimal fix.** Compute the number; propose the smallest on-palette
   / on-contract change; apply.
4. **Graduate the one-off** (the load-bearing step, per `audits.md`). A loop only
   compounds if it reruns. Turn the ad-hoc check into a `scripts/check-*.py`
   (or `tests/test_*`). An audit that found a real issue once but never reruns
   decays to dead code.

   The static `check-*.py` / `*_audit.py` family runs through **one scope-gated
   runner — `scripts/preflight.py`** (a registry of `check → scope → gate`;
   each check is a black-box subprocess, so adding one is a single registry row,
   no per-script convention and no skill edit). Its two runtime homes:
   - **`/preflight`** — session-start, judgment-curated (run only the scopes the
     session touches: `--scope ui,kb,…`). Live/judgment checks (git, daemon, bus,
     env) stay in the skill, not the runner.
   - **`scripts/release-public.py`** — the hard gate before a public release.

   This runner is itself an instance of the pattern: the registry of self-audits
   grew big enough (~13+ scanners) to deserve its own aggregator.

## Catalog (the loops that exist)

> **The machine-readable catalog is `emptyos/sdk/loops.py`** — the registry that names
> every EmptyOS feedback loop (not just the self-audit ones) as a single concept: each
> scored on the six stages (`friction`, `act`, `gate`, `revert`, `memory`, `bound`), with
> its live/dark status and exact dark-flag key. Query it with `eos loops list` / `show <id>`
> / `stages`. **When you add a loop, add it there** — this table is the *narrative* of the
> self-audit axis; the registry is the inventory of record. It also carries `LoopReceipt` /
> `receipt_from_drain_state`, which builds a captured-run receipt from a real fix-agent
> `run.json` (friction → attempts → files → gates → verify → revert → learned).
>
> Grounding note (2026-07-11): `bound` (termination/budget) exists because every serious
> external framing names "how does the loop stop" as first-class — a loop with no stopping
> condition is a failure mode, not a loop. See `project_loop_engineering_concept_grounding`.

| Axis | Generator / registry (the feature) | Self-audit use | Where it reruns |
|---|---|---|---|
| Behavior / bugs | dogfood-agent + fix-agent | find friction → fix → verify | `test-fix-verify-loop.md`; CI dogfood |
| Model choice | model-bench | benchmark our own `think` provider chain | `eos-model-bench-scenario-audit` skill |
| KB coverage vs real questions | assistant + rooms Q&A history vs the KB corpus | kb-gap-miner — repeatedly-asked questions the KB can't answer → `propose_kb_extractions` into the review gate | daily cron (`kb-gap-miner-sweep`, dark until `[apps.kb-gap-miner] enabled`) |
| Engineering correctness | KB (`formula`/`case`) + conformance gates | engine output vs KB ground truth | `scripts/kb_claim_audit.py` (/preflight) |
| Code health | code_archaeology, check-personal/branding/clickable/absolute | dead code, leaks, a11y, drift | `scripts/check-*.py` (release/preflight) |
| **UI / design** | **designer + `design-system-*` registry** | theme token/contrast vs imported pros | `scripts/check-contrast.py` (/preflight) |
| **UI / interaction affordance** | the rendered DOM itself (no registry — geometry + ARIA are the ground truth) | can the user *reach* and *understand* the surface: content clipped with no scroll (broken height chain); a one-of-N button row with no tab/radio roles | `scripts/check_ui_affordance.py` + `?debug=affordance` (live daemon; design-system-audit Phase 0d / release) + `tests/test_sys_ui_affordance.py` (CI) |
| **AI-native** | the platform's own AI mechanisms (`think` chain, `EOS_UI` AI chrome, verb registry) | per-app backend-AI vs assistant reach vs visible AI UI; "dark AI" = an app that thinks but shows no chip and exposes no verb | `scripts/check_ai_native.py` (/preflight `--scope apps`) + `eos-ai-native-audit` skill |
| Flag hygiene | the dark-flag convention (`feature.<slug>.enabled` + `[autopilot]` keys) | inventory every dark flag with per-machine state + age; STALE = dark >90d with no `WANTED_DARK` trigger registered | `scripts/check_dark_flags.py` (/preflight, always scope) |
| App gap vs market | `eos-app-gap-analysis` skill + vault registry (`30_Resources/EmptyOS/gap-analysis/`, one note per app) | benchmark each app vs 3-5 front-tier market alternatives; gap lifecycle (`open→shipped/deferred/declined`) tracked across re-runs | `scripts/check_gap_freshness.py` (/preflight, apps scope) + insights-ledger lens `gap` |
| Docs | `eos app info`, `generate_emptyos_site.py` | self-documenting apps + live site | session-wrapup |

First UI-axis instance (2026-06-07): `design-system-emptyos` made the system's
themes comparable to Linear/Stripe/Vercel; the benchmark surfaced `--text-muted`
failing WCAG AA in 4/6 themes (the pros all passed). Fix = 5 same-hue token
nudges; graduated to `scripts/check-contrast.py`. Lesson note:
`30_Resources/EmptyOS/kb/notes/lesson-designer-self-improvement.md`.

## When to add a loop (and when not)

**Add** when a feature you built produces a registry/benchmark/generator that
could measure the host on an axis nothing else covers, AND you can name an
objective metric. Then follow the shape above — *including step 4*.

**Don't:**
- Codify a loop with only one instance — that's premature (CLAUDE.md rule 9 /
  Forge anti-abstraction). This rule earned its place at the *second* instance.
- Skip graduation. A one-off print-loop that found a bug but doesn't rerun is
  research, not a loop. Either graduate it or delete it.
- Over-apply a benchmark. A host artifact is usually a **superset** of the
  external reference (e.g. an app theme carries dark-mode / states / motion that
  a marketing design-system doesn't). Borrowed examples are *benchmarks and
  starting points, not drop-in replacements*. Measure the shared axis; don't
  wholesale-swap.
- Let a benchmark fire on healthy targets (the `audits.md` false-positive trap) —
  tune the threshold against 3 known-good cases before trusting the report.

## Cross-references
- CLAUDE.md Principle 10 — "the system is alive … self-audit loop" (the principle)
- `.claude/rules/audits.md` — running + **graduating** one-off heuristics
- `.claude/rules/deep-research.md` — the read/triangulate/refute/grade method these loops apply when analysing a corpus
- `.claude/rules/test-fix-verify-loop.md` — the behavior/bug instance
- `30_Resources/EmptyOS/kb/notes/lesson-designer-self-improvement.md` — the UI instance, narrated
