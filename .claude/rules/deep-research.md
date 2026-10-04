---
abstract: When and how to run the deep-research method — read primary sources, triangulate, baseline, adversarially refute, grade evidence — for any cross-source analysis where being confidently wrong is costly. Doctrine the agent loads, not a tool it calls.
---

# Deep-Research Method — read, triangulate, refute, grade

A reasoning discipline for any task that reaches a conclusion by reading **across many sources** — insights runs, repo/tool borrow-verdicts, architecture/security/bug/design audits, "what is the state/trajectory of X", "why does Y keep recurring", strategic reviews. It exists because the default failure mode under breadth is **tabulating the surface instead of reading the substance** — counting commits/flags/scores and shipping statistics dressed as insight.

Origin: the eos-insights v1→v2 jump, where a solo "count" pass made 3 confident errors (stale-vs-current friction, a gitignored-flags false alarm, a wrong trajectory claim) that a "read + verify" pass caught — see `self-audit-loops.md`.

## The method (6 moves)

1. **Baseline first** — read what was concluded last time (a prior report, `git log`, MEMORY, a ledger scorecard). A finding that *recurs unaddressed* is stronger than a fresh one. (`time-dimension.md` — read the past before acting.)
2. **First pass (breadth)** — the cheap inventory: counts, lists, rankings. This *locates* where to look; it is **not** the answer.
3. **Gap pick** — name the 3–5 **load-bearing or uncertain** claims, the ones a decision hangs on. Deepen those, not the easy counts.
4. **Deep-read the primary sources** — for each gap, open the actual artifact (the log body, the code, the raw rows, the source repo). Don't infer from the tally. **Triangulate**: every headline rests on ≥2 independent sources.
5. **Adversarially refute** — before publishing, try to *disprove* each top finding; test each heuristic against 3 known-healthy cases (`audits.md`). Drop or downgrade what doesn't survive.
6. **Grade the evidence** — tag each finding `counted` / `read-verified` / `inferred`. The grade is the honesty signal that separates insight from a confident guess.

## When to use

- A conclusion drawn by reading **across multiple sources** where being wrong is costly: insights, repo/tool borrow-verdicts (`eos-repo-extract`), architecture/security/bug/design audits, "state/trajectory of X", "why does Y recur", strategic reviews.
- Any answer you'd be embarrassed to be **confidently wrong** about.

## When NOT to use

- A single-file lookup, a known one-line fix, a fact verifiable in one read — the ceremony costs more than it saves.
- High-frequency or trivial calls. Reserve it for conclusions that matter.

## Mechanism vs judgment — what's coded, what never is

The method is **half mechanism, half judgment**, and only the mechanism is packaged as code:

| Coded (reusable) | Judgment (never coded) |
|---|---|
| parallel fan-out → the **Workflow** tool | which gap is load-bearing |
| cross-run dedup/score → `emptyos/sdk/miner_state.py` | which two sources actually triangulate |
| record→scorecard loop → `scripts/insights_ledger.py` | what would refute this claim |
| gap-driven deepening pattern → `emptyos/sdk/deep_loop.py` | what the evidence *means* |

**Do not package the judgment as a tool.** A black box that "does deep research" invites skipping the thinking that *is* the value — the reification trap (`three-natures-lens.md`). This is doctrine the agent loads and applies, not a verb it calls.

## Serial by default; multi-agent only when it earns it

The 6 moves run fine **serially in one pass** — that's the default and carries most of the gain. Escalate to a multi-agent **Workflow** (parallel deep-readers + adversarial verifiers) ONLY when the corpus genuinely exceeds one context window, or for a periodic deep audit. The Workflow tool needs explicit user opt-in; don't reach for it on a routine question.

## Cross-references — the method is already distributed across these

- `self-audit-loops.md` — turn the tools on the system; the umbrella this sits under.
- `audits.md` — false-positive discipline (test on 3 healthy cases) = move 5.
- `debugging.md` — root-cause before fix = read the real thing, don't patch the surface.
- `time-dimension.md` — read the past before acting = move 1.
- `three-natures-lens.md` — don't reify the surface appearance = why move 4 matters.
- `agent-bus.md` — `bus_context(rules=["deep-research"])` to load this into an internal `think()` agent.
- `eos-repo-extract` skill — ground every claim against real source = move 4 for repos.
- Consumers today: `.claude/skills/eos-insights`, `.claude/skills/eos-life-insights` (both apply this rule rather than restating it).
