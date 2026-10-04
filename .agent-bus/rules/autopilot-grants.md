---
paths:
  - "emptyos/sdk/autopilot*.py"
  - "emptyos/cli/commands/autopilot.py"
  - "emptyos/mcp_outbound_server.py"
  - "apps/public/standard/rooms/**"
  - "apps/public/standard/voice-assistant/**"
  - "apps/public/standard/agent/**"
  - "apps/public/core/settings/**"
  - "apps/public/standard/billing/**"
  - "tests/*autopilot*"
  - "tests/*mcp_foundry*"
---

# Autopilot Grants Rule — explicit trust, not implicit mode

> **PIVOT 2026-06-07 — default inverted. Read this first.**
> The default is no longer "review every state change." The default now splits
> by **reversibility** (CLAUDE.md north star):
> - **`stable` (reversible/internal) verbs auto-run by default** — no grant, no
>   per-instance Apply. Safety net is **audit + one-click undo after**, not
>   approval before.
> - **`gated` + `never` (irreversible/external/billing) verbs stay human-gated**
>   by default — `publish.deploy`, outbound third-party messages, money-spending
>   cloud calls, bulk-destructive ops without a verified undo.
>
> The eligibility class (`stable`/`gated`/`never`, from
> `.claude/rules/verb-registry.md`) IS the auto/gate line. Consequently the two
> control primitives flip meaning:
> - A **grant** = "let this *gated* verb auto-run in this scope" (raises a gated
>   verb to auto). `never` verbs can never be granted.
> - A **hold** = the opposite: "pause auto for this *stable* verb right now"
>   (drops an auto verb back to gated, per-actor/scope). The escape hatch for
>   when the user wants to watch a normally-automatic verb.
>
> Everything below about grant *shape*, *scope*, *audit*, and the *eligibility
> floor* still holds. What changed is only the **default side of the line**:
> `stable` verbs no longer need a grant to run. The sections that say "the
> default is human review" describe the pre-pivot world — read them as applying
> to `gated`/`never` verbs only.

The default for a `gated`/`never` action surfaced to the user is **human
review**. Autopilot on a *gated* verb — running it without a per-instance Apply
click — is opt-in, scoped, revocable, and audited. There is no global "autopilot
ON" toggle. The user delegates specific gated verbs to specific actors via
**grants**; the system never decides on its own to skip the gate *for a gated
verb*. (Stable verbs auto-run by default per the pivot banner above — no grant
needed; a **hold** is how the user re-gates one.)

This is the symmetric move to **cloud consent**: cloud capability calls are
gated by default and the user grants `cloud_consent="always"` per provider.
Autopilot is gated by default and the user grants `autopilot` per
`(actor, verb_pattern, scope)`. Both mechanisms encode the same principle:
trust is something the user issues explicitly, not a mode the system enters.

**Why this exists.** Today's EmptyOS already auto-runs four things — reactor
handlers, scheduler jobs, agent `[DO:]` calls inside the assistant allowlist,
and post-consent cloud calls — under four different mechanisms that all mean
"the user has authorised this verb-shape ahead of time." This rule unifies
the conceptual model: each of those is a grant, expressed at a different
layer. The room review gate (CLI `[DO:]`) and proposed-action paradigm are
the surfaces that today lack a grant path. This rule adds it without
touching the default.

**The motto, re-scoped (2026-06-07).** "With you, not for you" is now a promise
about *judgment*, not *execution*. The human owns the direction and every
irreversible/external action; the system executes reversible/internal actions
on its own with audit + undo. "For you" on a *stable* verb is the new default,
not a delegation the user has to issue — the user only intervenes to *hold*
(re-gate) one. "For you" on a *gated* verb is still an explicit grant. Grants
and holds are how the user tunes the line; the eligibility floor is where the
line is anchored.

## Grant shape

A grant is a JSON record under `data/autopilot/grants.json`. One entry per
`(actor, verb_pattern, scope)` triple:

```jsonc
{
  "id": "grant-<10hex>",
  "actor": {"type": "cli" | "agent", "id": "claude-cli"},
  "verb_pattern": "task.add",            // exact, "<app>.*", or "<app>.<verb_glob>" (e.g. "email.send_*")
  "scope": "session:<id>" | "room:<id>" | "global",
  "created_at": "ISO timestamp",
  "expires_at": "ISO timestamp" | null,  // null = until revoked / scope-end
  "granted_by": "user",
  "rationale": "optional free text"      // why the user trusts this
}
```

- **`actor`** — required, specific. Never `{"type": "*"}` or `{"id": "*"}`.
  Every grant names exactly one CLI or agent identity. A grant for
  "claude-cli" does NOT cover "codex" — they have different failure modes.
- **`verb_pattern`** — three forms:
  - exact — `task.add`
  - app-wide — `task.*` (every verb on `task`)
  - verb-prefix glob — `email.send_*`, `kb.list_*`, `aura.*_memory`
    (`fnmatch` against the verb half; the app half stays exact)

  Bare `*` is forbidden, and cross-app patterns (`*.send`, `*.delete`) are
  forbidden — every grant names exactly one app. App-wide is fine when the
  user actually trusts every verb in that app's `[provides.assistant]
  server_actions` list, which is a high bar; verb-prefix glob is the
  middle ground for "trust everything starting with X without granting
  the full app." Glob patterns must match at least one verb in
  `eligible_verbs` at save time — a dead-on-arrival grant is rejected
  loudly. Per-call `match()` still re-checks `is_eligible(verb)` so a
  glob can never sneak in a non-eligible verb at fire time.
- **`scope`** — three options, narrowest to widest:
  - `session:<id>` — the **Claude Code Shift+Tab analogue**. Grant is
    alive only for the named session (a room session, a CLI session, a
    voice-assistant session). When the session ends (close room, restart
    daemon, explicit "end session" toggle), the grant is reaped. Default
    `expires_at` for session grants is `now + 1h` even if the session
    runs longer; the user re-arms by toggling again.
  - `room:<id>` — "only when this actor proposes in this room", persistent
    across daemon restarts until revoked. Default scope when issuing a
    grant from an Apply card.
  - `global` — "in any room or any agent surface." The user explicitly
    upgrades to `global` from the settings panel; the Apply-card third
    button never widens to global by default.
- **`expires_at`** — optional. Useful for "trust this for the next 2 hours
  while I'm focused elsewhere." Required default for `session:<id>` scopes
  (1h); optional for `room:<id>` and `global`. Reaper sweep on every
  daemon boot drops grants past their expiry.

## What's autopilot-eligible

A verb is autopilot-eligible only if its **payload shape is stable enough
that the user no longer benefits from seeing each instance**. The rule
isn't "reversible" — `task.add` is not really reversible (an added task
is real state) — the rule is "predictable":

- ✅ `task.add({text})` — payload shape is one string; user has reviewed
  enough instances to know what to expect
- ✅ `capture.save({text, tags?})` — same
- ✅ `kb.tag({slug, tag})` — payload is two short identifiers
- ✅ `journal.add_entry({text, mood?})` — bounded shape
- ❌ `rooms.write_note({path, content})` — `content` is free-form; the
  diff IS the value of reviewing
- ❌ `publish.deploy()` — irreversible external publish
- ❌ `notifications.send({channel, text})` — outbound message; sending
  the wrong thing is unrecoverable
- ❌ Any verb that calls a cloud capability and bills money (image-gen,
  inference) — cloud-consent gate stays in place

The eligibility list lives at `data/autopilot/policy.json`, seeded with
defaults and operator-extensible. The grant store **refuses** to create
a grant on a non-eligible verb; the only path is to mark the verb
autopilot-eligible in policy first, with explicit justification.

A verb's eligibility is **independent** of whether it's been granted —
non-eligibility is a hard floor that no grant can override.

## Where grants are checked

| Surface | Default | After grants land |
|---|---|---|
| CLI `[DO:]` in rooms | Always pending (`_gate_server_actions`) | Eligible + matching grant → auto-apply + audit; else pending |
| Agent `[DO:]` in `server_actions` allowlist | Auto-apply (the allowlist IS the grant, historically) | Unchanged — allowlist semantics preserved; documented here as the agent-side grant mechanism |
| Agent `[DO:]` with `gate_mode="gate"` | Routes to pending queue | Eligible + matching grant → auto-apply; else pending |
| Reactor handlers | Auto-runs | Unchanged — handler wiring is the operator's explicit grant |
| Scheduler jobs | Auto-runs | Unchanged — schedule entry is the operator's explicit grant |
| Cloud capability calls | `cloud_consent` gate | Unchanged — separate mechanism, different axis (privacy/billing, not autopilot) |
| `SandboxedWrite` (vault write proposals) | Always pending | **No grant path** — diff preview is the point |
| `publish.deploy` / outbound messages | Always pending (when gated) | **No grant path** — irreversible |

## How grants are created

Four paths. Each leaves a written grant record the user can find later.

1. **Room toolbar toggle — "auto-accept this session"** (the Claude Code
   Shift+Tab analogue, primary surface). Every room with at least one CLI
   participant carries a chip in the toolbar: `⚡ auto-accept · OFF` /
   `⚡ auto-accept · ON · 47m left`. Click to toggle:
   - **ON** issues a session-scoped grant covering **every eligible verb**
     from **every CLI participant** in this room, with
     `expires_at = now + 1h`. Visible countdown ticks down in the chip.
   - Clicking again before expiry **opens a duration picker** (15m, 1h,
     4h, until-session-end) rather than toggling off immediately, so a
     mis-click doesn't kill an in-flight workflow. A second click after
     the picker closes the panel without changing state; an explicit
     "Turn off" entry in the picker revokes the grant.
   - **OFF** revokes the session grant immediately. Any in-flight pending
     actions stay pending — revocation isn't retroactive but it's
     instant for future actions.
   - The chip shows a short list ("✓ task.add, capture.save, kb.tag —
     3 eligible verbs covered") on hover so the user can see what's
     actually trusted right now.
   - A keyboard shortcut (`Shift+Tab` inside a room input, mirroring
     Claude Code's gesture) toggles the chip without leaving the typing
     surface.

   This is the path most users will use most of the time. It's session-
   scoped on purpose — autopilot expires when attention shifts elsewhere.

2. **Apply card third button — "Always allow this"** (persistent grant).
   Every pending-action card has Apply / Reject today; gains a third
   button that:
   - Applies the current action.
   - Creates a grant with `verb_pattern = <exact verb>`, `scope = room:<id>`,
     `expires_at = null`.
   - Surfaces a one-line "grant created — manage in Settings → Autopilot"
     toast.

   A long-press / option-click opens a small dialog for advanced shape
   (pattern widening to `<app>.*` or a verb-prefix glob like
   `<app>.send_*`, scope upgrade to `global`, expiry, rationale text).

3. **Settings → Autopilot panel.** Lists active grants in a table:
   actor · verb pattern · scope · created · expires · audit count.
   Each row: Revoke, Edit expiry, Widen/narrow pattern. The panel also
   shows the verb-eligibility policy and lets the operator extend it
   (separate, deliberately less ergonomic affordance).

4. **CLI** — `eos autopilot grant <actor-id> <verb-pattern> [--scope=room:<id>|session:<id>|global] [--expires=<duration>] [--rationale=<text>]`.
   For operator-side scripting and one-shot grants.

## When the session ends

Session-scoped grants are reaped on:

- The room "session" boundary — defined as the user closing the room tab
  in the UI, or a server-side idle timeout (default 30 min of no chat
  activity in that room).
- Daemon restart — every session grant is dropped because the session ID
  it referenced won't exist after boot. The room toolbar chip returns
  to OFF after restart.
- Explicit toolbar toggle to OFF.
- Auto-expiry at `expires_at`.

The reaper sweep runs on daemon boot and on each room load; it removes
grants whose scope-session is no longer alive. Room-scoped and global
grants survive both restarts and tab closes — they're persistent until
the user revokes.

## Audit

Every auto-applied action logs to syslog with the `grant_id` that authorised
it. `eos autopilot log` (or `/settings → Autopilot → Activity`) shows the
audit trail — when grants fired, against what, by which actor.

Revoking a grant does **not** retroactively undo past auto-applies; it stops
future ones. Past auto-applies remain in syslog for inspection. If the user
needs to undo past actions, that's the existing per-app undo / reactor /
vault-rollback path, not an autopilot concern.

**Periodic re-review (定期复审, shipped 2026-06-11).** Auto-expiry covers
session grants, but a persistent `room:`/`global`/`mcp:` grant lives forever —
the re-review surface is what asks "is this still earning its keep?".
`review_grants()` (`emptyos/sdk/autopilot.py`, pure) aggregates per-grant fire
counts from `audit.jsonl` (grants deliberately carry no counters) and
classifies each grant `stale` (persistent, >14d old, unfired in 14d — consider
revoking) / `expiring` / `fresh` / `active`, plus a trailing-7-day roll-up and
over-budget actors. Three read-only consumers:

- `eos autopilot review [--stale-days N] [--json]` — agent-cli envelope; exit 1
  when any stale grants exist (exit-code-as-signal).
- `GET /agent/api/autopilot/review` — same report over HTTP.
- Hub panel `autopilot-review` (agent app, `plain-list`, lazy) — stale/expiring
  /over-budget rows + "fired N× this week". Returns `None` (invisible) until at
  least one grant or audit entry exists, so unused deployments see nothing.

Review never revokes — revocation stays on the existing paths (`eos autopilot
revoke`, the foundry panel). Tests: `tests/test_sdk_autopilot_review.py`.

## Why this isn't "autopilot mode" with a toggle

The room toolbar chip ("⚡ auto-accept · ON · 47m left") IS a toggle, so
this distinction matters: it's a toggle that issues a **scoped grant**,
not a global mode. The differences a reader should hold onto:

| Mode toggle (what we reject) | Scoped grant toggle (what we ship) |
|---|---|
| Binary global state | Per-room, per-session, per-actor, per-eligible-verb |
| "Autopilot ON" applies to anything that ever fires | Only matches actors + verbs visible right now |
| Off after-the-fact reveals surprises | Off doesn't apply retroactively; in-flight pending stays pending; future actions stop auto-applying instantly |
| Survives indefinitely until you remember to turn it off | Auto-expires at `expires_at`, on session-end, or on daemon restart |
| Bypasses eligibility | Eligibility floor still applies — no toggle can grant `rooms.write_note` or `publish.deploy` |
| User has to remember which rooms / verbs / agents are covered | Chip hover-list shows exactly what's covered in this room right now |

The Shift+Tab in Claude Code is the model: it looks like a mode to the
user but is actually session+verb-class-scoped behind the scenes. Same
here. Paperclip's "Maximizer mode" is the anti-pattern — a global
unscoped "let the agents run" toggle whose only retroactive evidence
is the diff of what they did. Modes leak unintended actions because
their granularity doesn't match the user's actual risk model.

Grants are coarse where the user wants coarse (`task.* in room:capture-bot`
for a session) and fine where the user wants fine (`task.add only from
agent-jobsearch, persistent`). The user expresses the **shape of trust
they actually have**, not a binary trust/distrust.

## Per-actor budget caps (2026-06-09)

Grants/holds + the eligibility floor decide *whether* a verb auto-runs. A
**budget cap** is the orthogonal ceiling on *how much* an actor may spend doing
so — the runaway-cost guardrail for autonomously running loops (`staff`,
fix-drain, `dogfood-agent`) that can otherwise burn cloud spend without bound.
Borrowed 2026-06-09 from the Paperclip mining as the one primitive EmptyOS had
no equivalent for (it gated by reversibility + cloud consent, never by spend).

Store: `data/autopilot/budgets.json` →
`{"actors": {<actor_id>: {monthly_cap_usd, spent_usd, window_start}}}`. The
window is a calendar month (UTC); spend rolls to zero lazily on the first
read/record in a new month — no cron. A `None`/absent cap means "track spend
but never gate". SDK (all in `emptyos/sdk/autopilot.py`, pure, unit-tested in
`tests/test_unit_autopilot_budget.py`):

- `set_budget(data_dir, actor_id, monthly_cap_usd)` — set/clear (`None`/`<=0`
  clears); preserves accrued spend so raising/lowering mid-month doesn't reset.
- `record_spend(data_dir, actor_id, amount_usd)` — add to current-window spend.
- `within_budget(data_dir, actor_id)` — `False` once spend ≥ cap.
- `budget_status` / `all_budgets` — panel/audit feeds.

**It is the fourth step in `decide()`**, behind the `enforce_budget` flag: any
`auto` outcome (stable-default *or* grant) is overridden to
`gate` with reason `over-budget` when the actor is over cap. The `grant_id`
that *would* have fired is preserved on the gate for the audit trail. The
budget never flips a `gate` to `auto` — it only ever adds friction, so it can't
widen access (same one-directional safety property as a hold). With
`enforce_budget=False` it is byte-for-byte the pre-budget behaviour, so it
ships dark exactly like `auto_stable`.

**Eligibility floor is unaffected** — `never` verbs (publish/outbound/billing)
are still un-grantable and un-auto regardless of remaining budget; a budget cap
is a ceiling *under* the floor, not a way around it.

**Per-run complement (`RunBudget`).** This per-actor cap is a **monthly** ceiling.
Its inner, orthogonal sibling is the **per-run** cost ceiling `RunBudget`
(`emptyos/sdk/run_budget.py`, on the staged-pipeline layer — `.claude/rules/
staged-pipeline.md`): "this single generation run may spend $Y". A finished run
forwards its spend into *this* monthly ledger via `record_spend`, so the two
compose (run ceiling inside month ceiling). `RunBudget` adds estimate→reserve→
reconcile + a per-action approval pause that the monthly cap has no notion of.

**Wiring (shipped dark 2026-06-11, AP2-borrow session):** the three operator
pieces now exist, each behind a default-off flag:

- **Metering** — `[autopilot] meter_spend`: billing's `think:executed` handler
  calls `record_spend(data_root, actor_id=<calling app id>, cost)` for every
  paid call (`apps/public/standard/billing/app.py::_meter_spend`). Attribution
  is the **app id** — for the autonomous spenders (staff, dogfood-agent,
  fix-agent) the app IS the actor. Per-room/per-job actor threading through
  `BaseApp.think()` is still deferred; claude-cli subprocess cost stays $0/
  invisible regardless.
- **Enforcement** — `[autopilot] enforce_budget_caps`: the rooms gate passes
  `enforce_budget=` into `decide()`; an over-cap actor's would-be auto gates
  with `gate_reason: "over-budget"` (+ `gate_grant_id` preserving the grant
  that would have fired) stamped on the pending record.
- **CLI** — `eos autopilot budget set <actor> <usd>` / `show [actor] [--json]`
  (`emptyos/cli/commands/autopilot.py`; `--json` uses the agent-cli envelope,
  exit 1 when any actor is over cap).

Tests: `tests/test_unit_billing_meter.py`,
`tests/test_unit_rooms_logic.py::TestGateBudgetCeiling`. Still not built: the
hub panel fed by `all_budgets` (build when budgets are in real use) and
actor-identity threading (build when a consumer needs the per-job split).

## Cross-references

- `.claude/rules/room-review-gate.md` — the CLI `[DO:]` gate that grants
  bypass on a per-verb basis; gate semantics and pending storage are
  unchanged.
- `.claude/rules/proposed-action.md` — the diff/render/impact preview
  paradigm. Grants don't change preview semantics; they change whether
  Apply fires automatically. Diff-shaped previews (`SandboxedWrite`)
  remain non-grantable.
- `emptyos/capabilities/__init__.py::Capability.execute` — the cloud
  consent gate; symmetric mechanism for a different axis. Grants for
  autopilot do NOT bypass cloud consent; the two gates compose.
- CLAUDE.md § North Star — the motto this rule specifies.

## Build order (when implementing)

This rule is the design contract; the code lands incrementally on demand.
Likely order when the first grantable verb has enough Apply-fatigue to
justify the work. Steps 1-3, 5, and 6 are done (see § Status) — step 4
(the Apply-card third button) is the one still open.

1. **Grant store + policy** (foundation) — `data/autopilot/grants.json` +
   `data/autopilot/policy.json` with eligibility list, plus a small
   `emptyos/sdk/autopilot.py` exposing `load_grants()`,
   `match(actor, verb, scope)`, `is_eligible(verb)`, `save_grant()`,
   `revoke(grant_id)`, `reap_expired()`. Reaper runs on daemon boot.
2. **Gate hook** — `_gate_server_actions` in `apps/public/standard/rooms/pending.py`
   (bound to `RoomsApp` via the multi-module decomposition pattern in
   `.claude/rules/multi-module-apps.md`) consults `match()` before
   persisting a pending entry; on match + eligible, dispatches via
   `call_app` directly and emits `rooms:action_auto_applied` with
   `grant_id` in the payload. Audit lands in syslog automatically.
   The companion path is `_execute_server_actions` in the same module
   (already supports `gate_mode == "gate"` to route agent `[DO:]` into
   the pending queue) — both gain the same `match()` consultation.
3. **Room toolbar chip + Shift+Tab** (primary surface) — the session-
   scoped toggle in `apps/public/standard/rooms/pages/index.html`. POSTs to
   `/rooms/api/autopilot/session` (toggle), `/rooms/api/autopilot/extend`
   (re-arm duration). Server-side handler issues / revokes a
   `scope: "session:<room_id>"` grant covering every eligible verb from
   every CLI participant in the room. Countdown ticks client-side from
   the grant's `expires_at`. This is what users see first; ship it with
   the gate hook or not at all.
4. **Apply-card third button** (persistent grants) — adds the
   `room:<id>`-scoped path. Same `/rooms/api/autopilot/grant` endpoint
   from a different surface.
5. **Settings → Autopilot panel** — grant list, revoke, extend, widen.
   Policy editor (verb eligibility) as a separate, deliberately
   less-ergonomic affordance under the same page.
6. **CLI** — `eos autopilot {grant,revoke,list,log}` for operator-side
   scripting.

Don't ship pieces 1-2 without piece 3 — a grant store with no toolbar
chip means users have no path to issue session grants, which is the
high-frequency case. Either it's a real feature or it stays in this doc.
Pieces 4-6 are incremental and can land in any order once 1-3 are live.

## Actor type: `mcp-client` (outbound MCP foundry)

The outbound MCP foundry (`emptyos/mcp_outbound_server.py`, see `docs/AGENT.md`)
is a grant consumer with `actor_type="mcp-client"` and the scope convention
`mcp:<actor_id>` (the external client's id, e.g. `mcp:codex`). It is issued via
`eos autopilot grant <client-id> <verb> [--scope mcp:<client-id>]` or the
`/agent/pages/foundry.html` panel — the first shipped non-voice grant-**issuing**
surface. The foundry honours the same eligibility floor; non-eligible verbs
(`rooms.write_note`, `publish.deploy`, outbound messages) can never be exposed
to an external client regardless of grant or manifest opt-in.

## Status (2026-08-22)

Re-review surface (定期复审) shipped 2026-06-11 — see § Audit. Read-only:
it flags stale persistent grants but never revokes.

Four consumers exist, plus the cross-cutting operator surfaces:

- **mcp-client (outbound foundry)** — pieces 1+2+3 live: grant store
  (`emptyos/sdk/autopilot.py`), the per-call `match()` gate inside
  `mcp_outbound_server._handle_call`, and two grant issuers
  (`eos autopilot grant`/`revoke` + the agent-app `/agent/pages/foundry.html`
  panel). Behind `[apps.agent] feature.mcp-foundry.enabled` (dark default).
  Tests: `tests/test_unit_mcp_foundry.py` + `tests/test_sys_mcp_foundry.py`.

- **voice-assistant** — pieces 1+2+3 are live as a complete vertical:
  the grant store (`emptyos/sdk/autopilot.py`), the inline `_is_grant_active`
  check in `voice-assistant/pending.py`, and the `⚡ auto-accept` session
  toggle chip in its `pages/index.html`. This path is fully usable.

- **rooms** — pieces 1+2+3 are live (landed 2026-07-04, `apps/public/standard/rooms/autopilot.py`):
  `_gate_server_actions` consults `match()`/`decide()` (`rooms/pending.py`),
  and the room toolbar carries a working `⚡ auto-accept` / `⏸ pause-auto`
  chip (`apToggle`/`apRefresh` in `pages/rooms-features.js`) that issues
  session-scoped grants via `POST /rooms/api/autopilot/session` (or holds via
  `/api/autopilot/hold` once the global `auto_stable_default` flag is on).
  **The rooms auto-apply branch is reachable and working** — the caveat
  below from the prior status entry is resolved; do not re-add it without
  re-verifying against the live code.

- **CLI** — full issuer, not read-only: `eos autopilot {grant,revoke,hold,
  unhold,list-grants,list-holds,review,policy,verify,log,budget set/show}`
  (`emptyos/cli/commands/autopilot.py`). `grant`/`revoke` predate the rooms
  chip and were built for the MCP foundry case, but work for any actor type.

- **Settings → Autopilot console** (build-order step 5, shipped 2026-08-22)
  — the operator-facing global view this doc originally speced: a table of
  every active grant (with status/fire-count from `review_grants()`) +
  every active hold + every actor's monthly budget, each row revocable, plus
  a manual "+ Issue grant" form and a "+ Set cap" form. Lives at
  `apps/public/core/settings/autopilot_panel.py` (bound onto `SettingsApp`)
  + the "Autopilot" tab in `apps/public/core/settings/pages/index.html`.
  Read/write surface is global (every room/session/actor at once), unlike
  the per-room chip. Tests: `tests/test_sys_settings.py::TestAutopilotConsoleAPI`.

**What's still not built** — the Apply-card third button ("Always allow
this", § How grants are created path 2) that would let a user convert one
reviewed `[DO:]` action directly into a standing grant from the pending card
itself, instead of going to the room chip or the Settings console first.
Low-friction addition once a real "I keep clicking Apply on the same verb"
pattern shows up — build it then, not speculatively.
