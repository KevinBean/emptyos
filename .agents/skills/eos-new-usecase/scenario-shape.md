# Scenario house shape — the authoring contract

The single source of truth for what a dogfood use-case scenario file must look
like. Consumers: `/eos-new-usecase` (authors to it), `eos-usecase-audit`
Phase 1 (delegates here), the automated dogfood-agent persona loop (runs the
files), `eos-ui-walk` (hand-walks them).

Scenario files live at `apps/extension/dev/dogfood-agent/scenarios/<slug>.md`
and **ARE committed** — they're product test assets. Everything under `data/`
is not.

## Frontmatter schema

```yaml
---
tier: dogfood           # dogfood (routine flows) | feature (verifies a shipped feature)
mode: discovery         # coverage (verify shipped behavior) | discovery (express a real
                        # goal without checking support first — gap-hunting). Absent =
                        # coverage. Selection rules read this: an audit run must include
                        # at least one discovery scenario.
persona: kevin-weekday  # who is walking; power-user, telegram-user, … also exist
surface: cli            # web (default when absent) | cli | bridge
expected_apps:          # apps the flow touches — drives coverage credit + rotation deficit
  - task
  - capture
budget_turns: 15        # hard turn cap the body must restate
runtime: persona        # persona = the automated loop CAN run it;
                        # manual ONLY for human-hands steps (phone touch, extension popup)
checks:                 # optional: deterministic acceptance probes
  - "eos task list --json exits 0 and data is a list"
goals:                  # abandonment detection reads these; one line per goal
  - add a task from the CLI and see it on /hub/
---
```

**Goals are user-intent statements, not feature descriptions.** A goal the
system cannot complete yet is valid — walking it produces a `#missing`
finding, which is how feature gaps get discovered (the discovery mode in
`/eos-new-usecase`). Never rewrite a goal to fit what the system currently
does.

### Manual-walk scenario frontmatter (optional — a different file, same spirit)

A **manual, need-first UI walk** (`eos-ui-walk`) writes its own `scenario.md`
under `data/ui-walk/usecases/<walk>/` — NOT in the committed scenarios dir —
describing a months-long user outcome. That file may carry a trace identity
so findings keep their origin through promotion → fix → receipt
(`.claude/rules/loop-traceability.md`):

```yaml
---
usecase_id: riverside-bess-132kv   # slug for the real user need
need: One line stating the outcome the user must accomplish
milestones:
  - id: month-0-design-basis      # slug per lifecycle checkpoint
    title: "Month 0: establish the project design basis"
---
```

Don't confuse the two: dogfood scenario frontmatter (above) drives the
automated persona loop; manual-walk frontmatter is identity for the
evidence/triage chain. Legacy manual walks without it still work — identities
derive deterministically.

Parser notes (`_scenario_meta` in `apps/extension/dev/dogfood-agent/app.py`):

- **Unknown keys are ignored** — extensions are backward-compatible; legacy
  scenarios without newer keys stay loadable via fallbacks.
- `emptyos.sdk.utils.strip_frontmatter` removes the block before a persona
  sees the body — frontmatter is metadata for the machinery, the body is the
  brief for the walker.
- `_scenarios()` re-globs the directory per call, so
  `GET /dogfood-agent/api/scenarios` reflects a new file immediately — no
  daemon restart.

## Body requirements

Every scenario body must carry:

1. **`{{DAEMON_URL}}` placeholder** — the runner substitutes the sandbox URL.
   Plus the guardrails: never access any other host; never read source under
   `D:/emptyos/` ("you are a user, not a developer"); files only inside the
   working directory (the throwaway vault).
2. **Hard turn cap** — restate `budget_turns` in prose ("15 turns maximum")
   and define what counts as a turn.
3. **3-turn fumble cap** — any single goal taking more than 3 turns of
   fumbling is abandoned and logged `#missing` / `#confusing`. Real users
   give up; pushing through hides the friction.
4. **Tag vocabulary** — `#bug` / `#missing` / `#confusing` (scenarios may add
   narrower tags like `#inconsistent`, but these three are the contract the
   friction pipeline parses).
5. **Mandatory `## Wrap`** — the final log entry: 2-4 sentences, did you
   finish, what hurt, what worked.
6. **No-source-dive rule** — wanting to read EmptyOS source to figure out how
   something works IS the bug: log `#confusing` and stop.
7. **Log file** — append to `./dogfood-log.md` after every tagged turn,
   typically `- TURN N — <action> — #<tag> — <one specific sentence>`.

## Templates (copy the nearest)

| Surface | Template | Shape |
|---|---|---|
| `web` (persona routine) | `scenarios/tuesday-evening.md` | narrative evening routine, numbered goals, browser/WebFetch interaction |
| `cli` | `scenarios/cli-daily-driver.md` | terminal-only routine, `eos` commands, `--json` envelope probes in `checks` |
| `bridge` (HTTP-checkable) | `scenarios/telegram-inbound.md` | acceptance-check list probing what's verifiable from local HTTP; explicitly names what needs manual hands |

## Rotation guard

**`runtime: manual` scenarios must never be added to the scheduled rotation
config** (`[apps.dogfood-agent]` scenario rotation) — the automated loop
can't perform human-hands steps. Keep manual steps out of `persona`
scenarios; if a flow is mostly automatable with a couple of human steps,
prefer a `bridge` scenario that probes the HTTP-checkable part and names the
manual remainder (the `telegram-inbound.md` pattern).
