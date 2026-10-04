---
paths:
  - "scripts/check*.py"
  - "scripts/*audit*.py"
  - "scripts/preflight.py"
  - "scripts/mine_corrections.py"
  - "scripts/gen_*.py"
  - "emptyos/sdk/loops.py"
  - "emptyos/cli/commands/loops.py"
  - ".claude/skills/preflight/**"
---

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
   - **CI** — `.github/workflows/tests.yml` runs `--scope docs` on every push.
   - **`--scope release --gate-only`, run by hand before `/eos-release-public`.**
     `scripts/release-public.py` does **not** invoke the runner — it hand-calls
     nine scripts of its own. This file claimed it as the runner's second home
     until 2026-09-05, when the first `--all` run found zero references to
     preflight in release-public: the release scope (32 rows, 25 gated) was
     never what a release actually ran. Whether to wire it in is an open
     decision (`docs/SYSTEM-AUDIT-2026-09-05.md` §6).

   This runner is itself an instance of the pattern: the registry of self-audits
   grew big enough (~35 check-*/audit scanners) to deserve its own aggregator.

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
| **The agent's own behaviour** | the auto-memory / rules / hooks layer itself — the place a correction is *supposed* to land | **the correction miner**: repeated user corrections mined out of `~/.claude/projects/**/*.jsonl` and clustered, closing the one gap in that layer. Storing a correction was solved; *noticing a repeat* was model judgment made mid-conversation, so the feedback memories on disk were the ones that happened to get noticed and the denominator was unknown. Two filters do the work, both measured before either threshold was fixed: an **agent-reference** gate (72 marker hits split 34 about-the-agent / 38 domain choices — "no, let us go with X" must never become a standing rule) and **term lift**, `p(term \| correction) / p(term \| any turn)`, which is what separates a theme (`browser`, 12.5) from a verb every request contains (`change`, 3.7). Advisory forever, and it **proposes only** — accept/dismiss is durable so a theme is judged once, but the memory or rule stays hand-written | `scripts/mine_corrections.py` (/preflight `--scope memory`) + `tests/test_unit_mine_corrections.py` |
| Model choice | model-bench | benchmark our own `think` provider chain | `eos-model-bench-scenario-audit` skill |
| KB coverage vs real questions | assistant + rooms Q&A history vs the KB corpus | kb-gap-miner — repeatedly-asked questions the KB can't answer → `propose_kb_extractions` into the review gate | daily cron (`kb-gap-miner-sweep`, dark until `[apps.kb-gap-miner] enabled`) |
| Engineering correctness | KB (`formula`/`case`) + conformance gates | engine output vs KB ground truth | `scripts/kb_claim_audit.py` (/preflight) |
| Engineering assurance | participating app manifest `[assurance]` + eight controlled documents | required documents, requirement-to-code/test traceability, the `## Data flow` resolve join, calculator surfaces, test presence, and receipt-backed promotion — plus three guards against a vacuous pass (a package must state requirement ids, a web-facing one must state `UX-*` ids, a data flow must qualify one symbol) | `scripts/check_engineering_assurance.py` (preflight `apps` / `docs` / `release`, gates structural drift) |
| Engineering assurance — the rows themselves | each traceability row's cited test NAME, cited test FILE, and page symbol | the checker above enforces that a stated requirement *has* a row and never **opens** it, so a rewrite can delete a requirement's test and still report `0 error(s)` — measured in `D:/prelim-sizing`, where 17 of 20 UX rows cited tests deleted in the same rewrite, and the requirement most visibly lost regressed in the commit that removed its guard. Advisory: a row may legitimately cite a fixture or a runtime-built page, and per `audits.md` an ambiguous signal must never gate. Opt out at the row with `<!-- traceability: external -->`. **The file half was added 2026-08-28, after the same checker passed a target it could not see for the second time**: four Stage-5 rows in `cable-bonding` cited `test_sys_cable-bonding.py` — a hyphen, so not an importable module — and every test FUNCTION they named existed under the real underscore file, so a resolver keyed on the function name alone reported OK on a path that cannot be run. The lesson generalises past this checker: **a two-part reference needs both parts resolved**, because the half that resolves is exactly what makes the broken half invisible | `scripts/check_traceability_targets.py` (preflight `apps` / `docs` / `release`, advisory) |
| Engineering data dictionary | the calculator's own I/O declaration (`spec.py`) | `ALGORITHM.md` §§ 2/3 are rendered from it, so a document disagreeing with the code is drift rather than an editing oversight | `scripts/gen_trust_loop_tables.py --check` (preflight `apps` / `docs` / `release`, gates) |
| Published-artifact data | `dictionary/crosswalk.py` (151 vowel↔spelling correspondences) | a standalone artifact cannot fetch its data — an artifact's CSP blocks every external host — so it inlines a copy. Generated from the module, and the **prose counts around it pinned separately**, because the generator only guarantees the block it writes; the masthead and footnotes stated their own numbers by hand and would have gone quietly wrong on the page whose argument is that its counts are measured | `scripts/gen_vowel_crosswalk_artifact.py --check` (preflight `apps`, gates) + `tests/test_unit_dictionary_crosswalk.py` (data + prose, both directions) |
| Published-artifact data — **the upstream that moves** | the vault note `10_Projects/cpeng/docs/competency-evidence-inventory.md` (16 EA Stage 2 elements + a per-element Strength) | worklog's CPEng roll-up inlines the element names so its **standalone offline bundle** can name them (an exported HTML has no server and no vault). Names are stable; **Strength is a live assessment** that moves the moment an element gets evidenced, so the copy drifts toward advertising a gap that has closed. Its first `--check` found exactly that — 5 and 7 were `Adequate` in the note and absent from the code's focus map, so two elements rendered as Strong. Two design rules fell out: **do not generate what the note states in prose** (its unit split lives in a sentence, so parsing table headings yields 3/4/9/0), and **no vault ⇒ skip with exit 0**, because a check that breaks every fresh clone gets disabled | `scripts/gen_competency_focus.py --check` (preflight `apps` + `vault`, gates, 0.1s) + `tests/test_unit_gen_competency_focus.py` (15 cases; every red one synthetic because the real note converged first pass) |
| Code health | code_archaeology, check-personal/branding/clickable/absolute | dead code, leaks, a11y, drift | `scripts/check-*.py` (release/preflight) |
| **A skill's claims about the repo** | the skill corpus itself — `.claude/skills/` + `skills/`, the instructions an agent follows | a repo path named in a skill BODY that no longer resolves. The three sibling checkers structurally cannot see this: `check_skills.py` reads frontmatter, `check_skill_script_sync` and `check_skill_vault_sync` compare copies against each other, and none reads the body for claims about the tree. Measured 2026-09-12: **16 files, 45 dead references**, most of them flat `apps/<id>/app.py` paths the track-tree move left behind — including two living-memory sidecars whose "read it first" instruction pointed at a path that does not exist, so the skill ran without its memory. Advisory, because whether a `scripts/x.py` is repo-relative, vault-relative, or a file the skill CREATES is a judgment no regex settles; the three legitimate shapes opt out with an inline marker at the file | `scripts/check_skill_refs.py` (/preflight `docs` + `skills`, the latter with `--user`) + `tests/test_unit_check_skill_refs.py` |
| **The bundled↔user skill mirror** | `scripts/sync_user_skills.py` — the scrub-then-verify sync that decides what a fresh clone ships | drift between the git-tracked `skills/` tree and the per-machine `~/.claude/skills` store. It was the only skill mirror nothing watched, and the checker already existed — `--check` mode — so the 2026-09-12 fix was a **registration**, not a new scanner. Writing a second one would have had to duplicate the personal-path scrub and then drift from it | `sync_user_skills.py --check` (/preflight `skills`, advisory — the store is machine-specific and absent on CI) |
| **Work that never left the machine** | git itself — each owned repo's `@{u}..HEAD` | commits stranded past a session boundary. The nested `apps/personal` repo is **gitignored by the parent**, so it is invisible to every other check here; four of its commits — including the one adding the whole System Icon Library — sat unpushed overnight (2026-09-01) and surfaced only because a wrapup happened to look. Two narrowings do the work, and the first run found the counter-example for each: **age, not count** (unpushed *is* the normal state of an active session, so a count-based signal fires every run and gets ignored — threshold 12h, i.e. survived a session boundary), and **owner-matched discovery** (a plain `find .git` swept in 5 clones of *other people's* repos under `.tmp/repo-review/`; the owner is derived from the parent's own remote at runtime, so no account name lands in a tracked file — CLAUDE.md rule 13). Pruning `data/` was both a 95s → 0.3s fix and the only false positive: the publish app's generated `gh-pages` checkout holds 22 deliberately-local commits, 143 days old. Offline degrades to the last-known ref, which **over-reports and never under-reports**, so it still catches a strand | `scripts/check_unpushed.py` (preflight `always`/`git`, 0.3s, advisory — holding a commit back is sometimes deliberate) + `tests/test_unit_check_unpushed.py`, 11 cases over real repos against a local bare remote, 8 mutations red-proven. One of those pins exists because the offline-degradation fix was **silently reverted mid-session** by restoring a mutation backup taken before it, and the suite stayed green — a fix with no test is one careless `cp` from gone |
| **Runtime-undefined names** | the multi-module split itself — a helper's `__globals__` is its own module, so an import left in the spine is absent at runtime | missing imports, **and** a `TYPE_CHECKING`-only name used at runtime (which pyflakes calls *bound* and never reports) | `scripts/check_undefined_names.py` (preflight `always`/`apps`/`release`, **gates**) + `tests/test_unit_check_undefined_names.py` pinning both directions |
| **Route 500 on a refused id** | the SDK pair itself — `require_path_segment` raises (correct for a *path builder*), `path_segment_error` returns (correct at an HTTP boundary) | a `@web_route` calling the raising form with no `try` and no guard, so `nul`/`foo..` 500 while a merely-unknown id answers `{"error": ...}` — one mistake, two shapes. Found writing a smoke test; was live in 2 apps, 4 reachable routes | `scripts/check_route_500.py` (preflight `always`/`apps`/`release`, **gates**) + `tests/test_unit_check_route_500.py` pinning both directions. The narrowing that mattered was *dropping* a filter: requiring the arg to mention `request.` found the same 4 but was blind to a route stashing input in a local first — the shape one of the two fixes uses |
| **UI / design** | **designer + `design-system-*` registry** | theme token/contrast vs imported pros | `scripts/check-contrast.py` (/preflight) |
| **UI / interaction affordance** | the rendered DOM itself (no registry — geometry + ARIA are the ground truth) | can the user *reach* and *understand* the surface: content clipped with no scroll (broken height chain); a one-of-N button row with no tab/radio roles | `scripts/check_ui_affordance.py` + `?debug=affordance` (live daemon; design-system-audit Phase 0d / release) + `tests/test_sys_ui_affordance.py` (CI) |
| **UI / failure honesty** | `EOS_UI.errorState` (the platform already ships the distinct treatment) | a `catch` painting a failure in the muted vocabulary reserved for "there is nothing here" — 24 pages used the helper and 128 blocks routed around it (2026-08-13); 59 and 98 on 2026-09-03 with the personal track included (46 and 52 on a fresh clone), when the scan stopped exempting a whole file on one helper call and started judging each `catch` block (that skip had hidden 23 findings in 8 partly-converted pages). Worst measured case rendered **"No tasks found"** from a `catch`: not merely unstyled, but false | `scripts/check_error_state.py` (/preflight `--scope ui`, advisory — "was the silent degrade deliberate?" is not a scanner's judgment; call sites opt out with `// error-state: intentional — <why>`) + `tests/test_unit_check_error_state.py` |
| **AI-native** | the platform's own AI mechanisms (`think` chain, `EOS_UI` AI chrome, verb registry) | per-app backend-AI vs assistant reach vs visible AI UI; "dark AI" = an app that thinks but shows no chip and exposes no verb | `scripts/check_ai_native.py` (/preflight `--scope apps`) + `eos-ai-native-audit` skill |
| **Spoken register** | the user's own spoken material — the Phrase Bank interview frames + four other `spoken: true` notes | drafted-for-him prose against written-by-him prose: contractions spelled out, semicolons (no spoken form), sentences past one breath. The measured gap is the whole signal — his own notes run 69-100% contractions, a brief drafted for him ran **0 of 31**, and the existing linter scored a contraction-free recruiter email *"clean, 0 findings"* because register is invisible to a banned-word scan | `emptyos/sdk/prose_lint.py` (`spoken=True` family) + `scripts/check_prose_tone.py --spoken` / `--vault` (/preflight `--scope vault`, advisory) + `tests/test_sdk_prose_lint.py` + `tests/test_unit_check_prose_tone.py` |
| Flag hygiene | the dark-flag convention (`feature.<slug>.enabled` + `[autopilot]` keys) | inventory every dark flag with per-machine state + age; STALE = dark >90d with no `WANTED_DARK` trigger registered | `scripts/check_dark_flags.py` (/preflight, always scope) |
| **Loop registry** | `emptyos/sdk/loops.py` (the loop catalog itself) | every registered loop's components resolve + dark flags appear in code; reconcile dark-vs-live per machine (dark-but-live-here). Unregistered-loop detection is deliberately NOT automated (ambiguous) — the register mandate stays doctrine | `scripts/check_loops.py` (/preflight `--scope loops`, advisory) + `tests/test_unit_check_loops.py` |
| **KB maintenance** | kb-butler (`kb.health()` sweep) | autonomous backlink repair + convergence reflection → fix-prompts into the review gate | scheduled cron (dark until `[apps.kb-butler] enabled`) |
| **Agent attention** | cockpit (session-state observer) | idle→waiting/stuck detection across parallel agent sessions → `proactive_notify` nudge | `cockpit-attention-push` loop (dark until `feature.attention-push.enabled`) |
| Content quality | publish framework evaluator | multi-lens LLM scorecard grading a draft vs its framework, deterministic-validated | `apps/public/standard/publish/framework.py` (dark until `feature.framework-eval.enabled`) |
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
