# Agent Team Patterns — structural shapes, mapped to live surfaces

> Borrowed *idea* from `revfactory/harness` (six agent-team patterns). EmptyOS already
> realizes all six across `rooms`, `company`, `staff`, and `emptyos/sdk/pipeline.py`.
> This doc names each pattern and points at the surface that implements it, plus
> "which shape fits which task." **No new agent files, no SDK team-helper** — per
> CLAUDE.md rule 9, helpers wait for a second concrete consumer.

The point of a "team pattern" is to pick the right *structure* for a multi-step or
multi-perspective task. You don't build a framework; you choose a surface that already
encodes the shape, or compose `call_app` + the event bus.

## The six patterns

| Pattern | Shape | Live surface | When to reach for it |
|---|---|---|---|
| **pipeline** | ordered stages, each artifact feeds the next; resumable | `emptyos/sdk/pipeline.py` (`Stage` / `Pipeline.start/resume`) | 3+ ordered stages with ≥1 expensive step + a preview seam (podcast, MV) |
| **fan-out / fan-in** | one prompt → N independent agents → synthesize | `company` scenario dispatch + `emptyos/sdk/multi_lens.py` (`multi_lens_analyze`) | N independent perspectives needed at once (critique panel, judge votes) |
| **producer-reviewer** | one agent produces, another (or a gate) reviews | `dogfood-agent` (produce friction) → `fix-agent` (review + merge gate) | Generate-then-verify where the verifier must be independent of the producer |
| **expert-pool** | a dispatcher routes each item to the best-fit specialist | `company` members (role-typed) + `rooms` participant routing | Heterogeneous items each needing a different specialist lens |
| **supervisor** | one overseer schedules, collects, re-routes | `staff` (scheduled agents + HITL approvals) | Recurring/scheduled work with a human approval gate |
| **hierarchical** | supervisor delegates to sub-teams, aggregates upward | `staff` + `rooms` team verbs (`team_add_task` / `team_assign` / `team_set_status`) | Multi-level delegation; a lead breaking work down across a team |

## Choosing a structure (decision guide)

- **Is the work a fixed sequence of expensive steps?** → **pipeline** (`emptyos/sdk/pipeline.py`).
  Get resume-from-stage + `stop_after` preview for free. See `.claude/rules/staged-pipeline.md`.
- **Do you need several independent opinions on one thing?** → **fan-out/fan-in**.
  Use `company`'s scenario run (`critique` / `workshop` / `interview`) or `multi_lens_analyze`
  for a one-call synthesized digest. See `eos-orgs-run-scenario`.
- **Must the checker be independent of the maker?** → **producer-reviewer**.
  The test-fix-verify loop is the canonical instance (`.claude/rules/test-fix-verify-loop.md`).
- **Different items need different specialists?** → **expert-pool** via `company` role-typed members.
- **Scheduled + needs human sign-off?** → **supervisor** via `staff` agents + approvals.
- **A lead delegating across a standing team?** → **hierarchical** via `rooms` team verbs.

## What NOT to do

- ❌ **Don't build a generic team-orchestration SDK** (`fan_out_to_actors`,
  `synthesize_responses`, `actor_context_for`) until a second app genuinely needs the
  same plumbing. The shapes above are realized per-surface today; that's correct under
  rule 9. The candidate extraction is noted in the exploration but deliberately deferred.
- ❌ **Don't spawn persistent agent files** to "be" a team. EmptyOS teams are
  data + events (`rooms` participants, `company` members), not on-disk agent definitions.
- ❌ **Don't merge the conversation families.** TOOL-LOOP (agent), CONVERSATION (rooms +
  assistant), VOICE (voice-assistant), CRON (staff) were audited as distinct
  (`docs/CONVERSATION-STACK.md`). A team pattern composes them; it doesn't fuse them.
- ❌ **Don't reach for a barrier (fan-in) when a pipeline fits.** A barrier wastes
  wall-clock; only synchronize when stage N genuinely needs all of stage N-1.

## Cross-references

- `emptyos/sdk/pipeline.py` + `.claude/rules/staged-pipeline.md` — the pipeline pattern.
- `apps/public/standard/company/` + `emptyos/sdk/multi_lens.py` — fan-out/fan-in + expert-pool.
- `apps/public/standard/rooms/` (`team.py`, `participants.py`) — hierarchical team verbs.
- `apps/personal/staff/` — supervisor (scheduled + approvals).
- `.claude/rules/test-fix-verify-loop.md` — producer-reviewer (the four-role loop).
- `docs/CONVERSATION-STACK.md` — the four conversation families teams compose over.
- `docs/ENGINEERING-WORK-LOOP.md` — where these patterns slot into a phase.
- `docs/OPEN-SOURCE-BORROWING-PLAN.md` — why this doc exists (harness borrow).
