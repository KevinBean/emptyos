# The Auto-Agent Framework — config, not code

> **The claim:** EmptyOS already has a complete framework for building autonomous
> agents. It is **distributed across existing primitives, not missing** — there is
> no agent-runner engine to build. Making a new auto-agent is a matter of picking
> one primitive per row of the recipe table below and wiring them together. This
> is the same doctrine as `docs/ENGINEERING-WORK-LOOP.md` ("no missing
> orchestration layer to build") applied to autonomy.

An "auto-agent" here is anything that does useful work **without a human driving
each step** — a scheduled shift, an event-triggered reaction, a cron sweep, a
chat persona that can take actions. EmptyOS runs several today (the staff cron
agents, the reactor, the fix-drain, the brand distribution engine). They share
no base class and need none; they share a **kit of primitives**.

## The recipe — pick one per row

To build an auto-agent, choose one mechanism from each row. Most agents leave
several rows empty (a read-only status agent has no *act* and no *gate*).

| Concern | Primitives (pick one) | Where |
|---|---|---|
| **Schedule** — when does it run? | `@scheduled(cron)` decorator · `BaseApp.add_cron_job(id, fn, cron=/interval=)` (dynamic) · a staff agent `cron` record · **an event trigger** `@on_event("some:event")` | `emptyos/sdk/decorators.py`, `emptyos/runtime/scheduler.py`, `apps/personal/staff/`, `emptyos/sdk/decorators.py` |
| **Persona / prompt** — how does it think? | `declare_prompts()` constants (rule 12) · `rooms.register_persona()` for a chat face · a staff agent `system_prompt` for a cron recipe | `.claude/rules/prompt-management.md`, `apps/public/standard/rooms/participants.py`, `apps/personal/staff/agents.py` |
| **Verbs / allow-list** — what can it do? | `[[provides.verbs]]` + an `eligibility` class · rooms `server_actions` per persona · `ALWAYS_GATE_VERBS` to force-gate an outbound verb | `.claude/rules/verb-registry.md`, `apps/public/standard/rooms/pending.py` |
| **Gates** — what stops it acting unsupervised? | proposed-action pending stores · the rooms `[DO:]` review gate · staff HITL approvals (auto-decline risky-on-cron) · autopilot grants/holds · the `.eos-personal` leak-scan · the cloud consent gate | `.claude/rules/proposed-action.md`, `.claude/rules/room-review-gate.md`, `.claude/rules/autopilot-grants.md` |
| **Budgets / bounds** — how does it stay cheap + terminate? | `emptyos/sdk/autopilot.py` monthly per-actor caps · `emptyos/sdk/run_budget.py` per-run ceiling · count/round caps · a convergence stop-streak | `.claude/rules/autopilot-grants.md`, `.claude/rules/staged-pipeline.md` |
| **Memory** — does it compound? | `episodic.py` (`remember_episode` / `recall_episodes`) · an app ledger (JSON) · `miner_state.py` cross-run dedupe | `emptyos/sdk/episodic.py`, `.claude/rules/self-audit-loops.md` |
| **Comms** — how does it reach the human / other agents? | the `notifications` service (Telegram) · `send_to_agent` / `agent_inbox` mailbox · `proactive_notify` (master-dark gate; `cockpit-attention-push` is the reference consumer — an idle agent session nudges the human) | `plugins/telegram/`, `emptyos/sdk/agent_mailbox.py`, `emptyos/sdk/proactive.py`, `apps/extension/dev/cockpit/tailer.py` |
| **UI** — where does the human see + steer it? | a hub panel (`[[contributes.hub.panel]]`) · a console page · the page-sidebar companion (`gpt:` override) · the Telegram bridge · **cockpit** (`apps/extension/dev/cockpit/`) — the read-only observer that watches every agent session's live state/artifacts/browser, no steering (a session owns its own stdin) | `.claude/rules/hub-panels.md`, `.claude/rules/app-ui-patterns.md`, `.claude/rules/browser-extension-bridge.md`, `docs/CONVERSATION-STACK.md` § observer |

Register every new loop in `emptyos/sdk/loops.py` (mandatory — a dark loop must
name its flag) so `eos loops` can find it and the self-audit surfaces can score
its stage coverage. `scripts/check_loops.py` (preflight `--scope loops`) then
reconciles the registry against reality — component paths must resolve, dark
flags must appear in code — and reports which registered loops are actually
live on this machine.

## Worked example — the brand distribution engine

The brand distribution engine (built 2026-07-15) is the reference consumer. It
is **entirely composed from the kit** — no new engine:

| Concern | What it uses |
|---|---|
| Schedule | **Event trigger** — `@on_event("publish:deployed")` in `apps/extension/dev/promote/distribution.py`. It runs when Kevin deploys a site, not on a clock. |
| Persona | `BRAND_EDITOR_PERSONA` (a seed constant) → `rooms.register_persona(id="brand-editor", …)` on `kernel:started`. |
| Verbs | `[[provides.verbs]]` in promote's manifest — read verbs `stable`, `promote.apply` is `never` (it can fire an outbound webhook). |
| Gates | The promote proposal store (Apply/Reject cards) + `("promote","apply_proposal")` in rooms `ALWAYS_GATE_VERBS` + the `.eos-personal` leak-scan before any webhook POST. Outbound is **never** automated. |
| Bounds | Scoped to `new_posts` per deploy; a first-deploy snapshot seed means enabling it can't draft the whole back catalogue; idempotent per essay. |
| Memory | The per-essay distribution tracker (`data/apps/promote/distribution/<site>--<slug>.json`) + the promote ledger. |
| Comms | The `notifications` service pings Telegram when drafts are staged; `promote.status` (a voice verb) surfaces counts on the phone. |
| UI | The brand console at `/promote/`, the `promote.queue` hub panel, and the brand-editor chat persona reachable from the console sidebar + `/rooms/`. |

The result: a hand-written essay deploys, three platform adaptations appear as
review cards with a Telegram nudge, and the human applies each — with zero new
orchestration code. That is the framework working as designed.

## Conventions

- **Every new behavior ships dark.** A new auto-agent (or a new autonomous
  behavior on an existing one) is gated behind a `feature.<slug>.enabled` flag,
  default false, so a fresh clone is byte-identical until the operator flips it
  (`project_feature_pipeline_flag_default_dark`). Activation = a settings/toml
  flip + one `restart.bat` for Python changes.
- **Register the loop.** Add a `Loop(...)` to `emptyos/sdk/loops.py`. This is how
  a loop becomes discoverable (`eos loops`) and how the self-audit surfaces know
  it exists.
- **Outbound stays human-gated.** See the standing decisions below.

## Standing decisions (do not re-litigate)

- **Outbound to third parties is never automated.** Posting to LinkedIn/X/Reddit,
  outbound email/message, and site deploy are always human-gated — there is
  nothing to undo (`.claude/rules/autopilot-grants.md` eligibility floor). An
  agent may *stage* an outbound action; a human *applies* it.
- **No new agent-runner engine.** The framework is the distributed kit above.
  Building a dedicated "auto-agent app" that re-declares (persona, schedule,
  tools, budget, policy) would duplicate the staff app and violate the
  no-missing-orchestration-layer doctrine.
- **No staff brand-editor agent** (decided 2026-06-27, reaffirmed 2026-07-15).
  The event-driven distribution engine + the weekly devlog drafter cover the
  brand cadence; a scheduled staff agent that proposes an outbound action would
  auto-decline on cron anyway (no human in the loop).

## Cross-references

- `emptyos/sdk/loops.py` — the loop registry (`eos loops list|show|stages`).
- `.claude/rules/verb-registry.md` — the verb/eligibility layer.
- `.claude/rules/autopilot-grants.md` — the reversibility-gated auto/gate line.
- `.claude/rules/proposed-action.md` + `.claude/rules/room-review-gate.md` — the
  propose→preview→confirm gate an agent's actions ride.
- `docs/CONVERSATION-STACK.md` — the conversational *surfaces* map (this doc is
  the *assembly manual*; that one is the surface catalog).
- `apps/personal/staff/` — the cron-agent reference; `apps/extension/dev/promote/`
  — the event-agent reference.
