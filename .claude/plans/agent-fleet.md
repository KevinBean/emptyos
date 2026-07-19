# agent_fleet — first-party fleet view over local coding-agent sessions

**Status: REVISE (plan-stage), not started** (2026-07-17). External review
verdict 2026-07-17: *"good product decision and scope, but the plan is not
build-ready yet — revise, not reject. The ConPTY/xterm.js non-goal is exactly
right."* The four blocking findings + corrections are folded in below; each is
tagged `[R1]`–`[R4]` / `[Rc]` at the point it changed the plan.

Decision context: Orca 1.4.143 trial + `project_ade_grokbuild_borrow_verdict`
memory + vault review `30_Resources/Web-Clips/2026-07-17 ADE landscape Synara
Orca + grok-build - repo review.md`. Kevin chose **build our own** over using
Orca; Orca stays installed as reference (its Claude hooks were stripped from
`~/.claude/settings.json` 2026-07-17 — backup at
`settings.json.pre-orca-strip.bak`).

**[R1] Verdict-record consistency:** the original "BUILD NOTHING / use Orca"
verdict in `docs/OPEN-SOURCE-BORROWING-PLAN.md` and the vault review now carry
a dated **superseded addendum** ("superseded after hands-on Orca trial
2026-07-17 → build `agent_fleet`, fleet-view slice only; ConPTY hosting still
BUILD NOTHING"), so `scripts/check_borrow_verdict.py` no longer surfaces two
contradictory positions. Done 2026-07-17.

## Naming (approved)

App id / dir / web prefix: **`agent_fleet`** (Kevin's pick 2026-07-17;
underscore precedent: `apps/extension/engineering/cable_network`). Per
`feedback_app_id_equals_web_prefix`, manifest `[app] id`, directory name, and
`[provides.web] prefix = "/agent_fleet"` must all match.

## The scoping cut (load-bearing)

Orca's five benefits split by build cost. We build the thin layers over
existing substrate and explicitly REFUSE the expensive moat:

| Benefit | Substrate | Verdict |
|---|---|---|
| Status hooks → "agent blocked" pings | Kevin's own `wezterm-status.ps1` hooks are the single-session version | Build (Phase 1) |
| Fleet view of all sessions | event bus, **Run Center harness contract** (`sdk/run_status.py`), realtime WS | Build (Phase 1) — via Run Center, not a new hub panel [R4] |
| Worktree-per-task management UI | `emptyos/sdk/worktree.py`, sandbox `source_root`, fix-agent, `/eos-worktree-gc` | Build (Phase 2) |
| Phone access | PWA + Tailscale + `/code/` phone remote | Already have |
| Hosting external CLI TUIs (ConPTY ↔ xterm.js) | nothing | **NON-GOAL — never build**; if truly needed, that's the trigger to just use Orca (MIT) |

Other non-goals: yolo launch defaults (against the review-gate posture), a
mobile emulator, any orchestration intelligence in the fleet app (it observes;
rooms/agent/staff act).

## Status of Phase 1 (2026-07-17)

Split two ways: **hook half = BUILT + WIRED** (this session); **backend half =
dispatched to Codex** (`.claude/plans/agent-fleet-codex-prompt.md`). The split
follows risk: the hook is machine-global and fires in every Claude session, so
it needed live execution + benchmarking, which a worktree agent cannot do.

Built: `scripts/agent_fleet_hook.py` (stdlib-only, follows the existing
`scripts/` hook convention), `tests/test_unit_agent_fleet_hook.py` (45 tests,
green), `tests/fixtures/agent_fleet/` (13 **real** captured Claude events).
Wired: `~/.claude/settings.json` (backup `settings.json.pre-agent-fleet.bak`) +
`.codex/hooks.json`. Verified against the live daemon: the hand-rolled HTTP +
bearer gets **404** (route not built yet — Codex's half) and **401 without the
bearer**, which proves auth is really enforced rather than the probe being
incapable of failing.

**Codex's backend merged 2026-07-17** (`04c84a1b`) after an
`/eos-agent-diff-review` pass. It was faithful and avoided every trap in the
prompt; the one real defect was **specification loss the prompt itself caused** —
the vocabulary block I wrote included `"unknown": "done"`, which shadowed
`normalize_run_status`'s `finished`-aware default and rendered a *live* session
as `done`, hiding it from the run-center panel the fleet exists to fill. Worst
at the moment of first evaluation: enabling the flag meets every running session
mid-stream. Fixed at the shared helper (`54c4427c`) — the fix is the *absence*
of the key — and pinned, including a test asserting it is never re-added.
Codex's 29 tests passed throughout: the bug shipped green because the one status
with subtle semantics was the one left untested.

**`SessionEnd` resolved 2026-07-17: it DOES fire** (2 observed, both
`reason: "clear"`). `/clear` retires the old `session_id` and opens a new one in
the same second, so marking the old record `ended` is correct. **The TTL sweep
is still load-bearing, not a fallback** — both observations were graceful, an
abruptly-killed terminal is not known to emit SessionEnd, and Codex has no
documented SessionEnd at all.

**End-to-end proven on real data:** 84 captured events → 0 rejected → 7 session
rows; the 2 `/clear`'d sessions terminal, all 5 live sessions visible.

## Phase 1 — hook harvest (a day; most of the value)

Mechanism reverse-engineered from Orca's `claude-hook.cmd` (see review note):
a coding-agent hook that POSTs each event to a local endpoint. First-party
version:

1. **Hook script — one combined hook, two obligations.**
   - **[Rc] WezTerm update runs on EVERY transition, unconditionally** —
     `wezterm-status.ps1` writes to the originating console, so the daemon
     POST can never substitute for it. Order: update WezTerm first (cheap,
     local), then attempt the POST. It is *not* a fallback for daemon
     downtime; the POST is the optional half.
   - **[Rc] Normalize locally to an allowlist before POSTing** — send only
     `{source, session_id, turn_id, event, cwd, agent_id, notification_type,
     ts}`. Never forward raw prompt text or tool payloads: keeps hook latency
     low and keeps prompts/tool results out of daemon logs. POST to
     `http://127.0.0.1:9000/agent_fleet/api/hook` with bearer from
     `emptyos.toml` (private mode — `reference_daemon_auth_token_probe`),
     `--connect-timeout 0.5`-style fast no-op when the daemon is down.
   - **[R2] Two sources, one envelope.** Codex hooks EXIST and are already
     active in this checkout (`D:/emptyos/.codex/hooks.json` +
     `~/.codex/hooks.json`): `SessionStart`, `UserPromptSubmit`,
     `PermissionRequest`, `Stop`, subagent hooks — see the official Codex
     hooks docs (developers.openai.com/codex/hooks). Phase 1 ingests Claude
     AND Codex through the same normalized envelope (`source: "claude" |
     "codex"`). Worktree inference is demoted to *reconciliation* of
     missing/stale sessions, not the Codex ingestion path.
   - Hook only the events the reducer needs: `SessionStart`,
     `UserPromptSubmit`, `PermissionRequest`, `Notification`, `Stop`,
     `SessionEnd` (Claude). `PreToolUse`/`PostToolUse` only if per-tool
     activity display earns it later.
   - **[Rc] ~~UNVERIFIED~~ → BENCHMARKED 2026-07-17, then RE-BENCHMARKED live.
     The result still inverts the plan's premise, but by less than first
     claimed.** The hook we are *replacing* is the expensive one:
     `powershell -NoProfile -File wezterm-status.ps1` measures **~172–210 ms
     per event** (PowerShell startup), i.e. already worse than the Orca cost
     the plan was worried about. Measured end-to-end against the live daemon —
     real script, real wired command line — this hook is **~60 ms p50 (min
     51)**: **~2.9x cheaper** than the status hook it replaces, while also
     feeding the fleet. Per turn (UserPromptSubmit + Stop): ~344 ms → ~120 ms,
     saving ~224 ms/turn. Phase 1 is still a **net latency win, not a cost**.
     Breakdown: ~35 ms python startup + imports, ~7 ms p50 daemon round-trip
     (tail ~27 ms under load).
     **An earlier revision of this line claimed ~36 ms / 4.8x. That was
     measured before `_post` waited for the reply — i.e. against a daemon that
     was never actually ingesting anything (see § the fire-and-forget bug). The
     number rose because the hook started working.** A benchmark of a broken
     path is not a benchmark.
     Four measured facts shaped the script, each pinned by a test in
     `tests/test_unit_agent_fleet_hook.py`: python can open `CONOUT$` directly
     (so ConOut.dll + the PowerShell hop both go — one spawn does both jobs);
     `urllib.request` costs ~27 ms to import (→ raw socket, ~3 ms); `tomllib`
     costs ~14 ms to read two config values (→ hand-scan); `pathlib` ~4 ms (→
     os.path); `-S` saves ~7 ms of site init, safe *because* the hook is
     stdlib-only.

   - **The fire-and-forget bug (the worst of the build, 2026-07-17).** `_post`
     originally closed the socket right after `sendall` — "we don't care about
     the reply, and waiting would put daemon latency on every turn". **uvicorn
     cancels the request handler when the client disconnects before the
     response**, so the ingest was aborted mid-flight and *nothing was ever
     persisted*. The hook still exited 0 in ~1 ms and looked perfect; the fleet
     was simply always empty, with nothing to explain why. Every offline test
     passed — a fake test server cannot reproduce uvicorn's cancellation, which
     is the whole point: **only the real hook against the real daemon could
     show it.** Proven by A/B against the live daemon (close-without-reading:
     does not land; read-then-close: lands). Fixed by reading the reply; the
     ~11 ms is not optional. Pinned by `test_post_waits_for_the_server_reply`,
     which asserts the client-side contract (still running when the reply
     arrives) since the server-side behaviour is unreproducible in a unit test.

2. **State reducer — explicit, and `Stop` ≠ session end [R3].** Claude's
   `Stop` fires once per **turn**; only `SessionEnd` marks termination.
   Reducer (per session, keyed `source:session_id`):

   | Event | New state |
   |---|---|
   | `SessionStart` | `idle` |
   | `UserPromptSubmit` | `working` |
   | `PermissionRequest` / `Notification(permission_prompt)` | `blocked` |
   | `Stop` | `idle` |
   | `SessionEnd` | `ended` |
   | stale TTL exceeded | `unknown` → `ended` |

   Codex has no documented `SessionEnd`, so TTL reconciliation is mandatory
   there (and a safety net for Claude sessions killed without the hook
   firing). Emit `agent_fleet:blocked` on entry to `blocked` and
   `agent_fleet:ended` on `ended` — NOT on every `Stop`.

3. **Persistence — decided now [Rc]:** the existing **`RunRegistry`** shape
   (`emptyos/sdk/run_registry.py`) + a per-session write lock + TTL sweep.
   Syslog is an event trail, not a live-state store; in-memory-only loses the
   fleet on restart. One run-record per session; hook POSTs update it.

4. **Surfaces [R4] — reuse Run Center, don't duplicate it.**
   `apps/extension/dev/run-center/` already aggregates harnesses through
   normalized `list_all()` rows and already owns the priority-70 hub panel.
   So: `agent_fleet.list_all()` implements the existing run-status contract
   (`emptyos/sdk/run_status.py`) and registers as an **optional Run Center
   harness** — fleet sessions appear in the existing panel + page for free.
   **No second fleet-shaped hub panel.** `/agent_fleet/` is reserved for the
   detail surface (session table with per-session state/worktree, Phase-2
   workspace management).
   - **[Rc] Blocked alerts:** `proactive_notify` ships dark by default
     (`emptyos/sdk/proactive.py`). Success criterion is "blocked>N-min alerts
     deliver **when the proactive gate is enabled**; otherwise they are
     suppressed and logged" — not "alerts always fire".

## Phase 2 — workspace manager

Page section (or `/agent_fleet/` tab) listing ALL worktrees — codex
(`~/.codex/worktrees/*/emptyos`), fix-agent, manual — via `git worktree list`
+ `sdk/worktree.py`; join against Phase-1 live sessions so each worktree card
shows branch, dirty state, live agent state. Actions:

- **Create** — `ensure_worktree` creates a **detached** worktree only [Rc];
  the create action must explicitly choose detached vs named-branch creation
  (named branch = `git worktree add -b <branch>`; decide per use at build
  time, default named-branch for agent tasks so merges have a ref).
- **GC** — reuse the `/eos-worktree-gc` discipline (clean + merged only).
  [Rc] That procedure is prose in the skill today, not reusable code; extract
  a pure classifier into `sdk/worktree.py` only when Phase 2 becomes its
  **second real consumer** (rule 9). Hard floor either way: never
  reset/clean an existing user worktree, never force-remove one.
- **Launch** — copy a launch command (`wezterm cli spawn --cwd <wt>`).

This is Orca's left rail without hosting terminals.

## Phase 3 (optional, on demand)

Embed EmptyOS-native agent sessions (eos chat brain already streams over WS —
no PTY needed) in the fleet page as a center pane. Only if the fleet page
proves sticky.

## Acceptance tests [Rc] (write with Phase 1, not after)

- Reducer unit tests over **real captured Claude + Codex hook payload
  fixtures** (incl. multi-turn: `Stop` must NOT produce `ended`).
- TTL reconciliation + duplicate-event dedup tests.
- Hook POST against private-mode auth (bearer required, 401 path).
- Daemon-down latency: hook completes fast (measure; compare against the
  30–90 ms Orca baseline — this is where the "cheaper" claim gets verified).
- `list_all()` rows validate against the run-status contract + render in Run
  Center.
- Phase 2 GC cases: dirty, unmerged, locked worktrees are all refused.
- UI walk of `/agent_fleet/` + sandbox live-verify
  (`.claude/rules/sandbox-driven-testing.md`) before any :9000 restart ask.

## Cleanup — the temporary capture rig (REMOVED 2026-07-17)

Torn down after it caught 84 events across 7 sessions, including the two
`SessionEnd`s that resolved the last open question. Both pieces were
uncommittable by construction (`scripts/_*.py` and `.claude/settings.local.json`
are both gitignored), so nothing leaked into the repo.

To re-capture (e.g. to record Codex's own hook payloads, which are still
unobserved — only Claude's were captured):

1. Re-create `scripts/_capture_hook_payloads.py` — append stdin JSON + argv to a
   JSONL file; never raise.
2. Add a `"hooks"` key to `.claude/settings.local.json` calling it per event.
   Hook-config reload timing is **not** what I assumed: I believed hooks are
   snapshotted at session start and that only a *new* session would fire them.
   Disproven by the live fleet at wrapup — it captured **8 real sessions
   including the one that wired the hooks**, still `working`, minutes after the
   edit. So an already-open session does pick up a hook-config change. Budget
   for that when wiring a capture rig: it may fire sooner and wider than you
   expect.
3. Regenerate `tests/fixtures/agent_fleet/claude-events.jsonl` from the capture,
   redacting `prompt` / `last_assistant_message` / `message` / `transcript_path`
   and anonymising session ids — the *shape* is the point, the content never is.

## Live-verified 2026-07-17 (leased sandbox, not offline)

84 real captured events → **84/84 accepted** → 7 sessions with real git
branches; the settings toggle flips restart-free; run-center aggregates
`agent_fleet` with the blocked session in `needs-review` and 5 live sessions in
`running`. `/agent_fleet/` renders (200, settings panel present). Two defects
only the live run could surface (`79864507`) — see that commit; both had shipped
green.

## ~~Known gap~~ RESOLVED 2026-07-17 — settings toggles that did nothing

Not an agent_fleet quirk; a platform pattern worth a scanner. `[provides.settings]`
renders a live toggle that writes to the **settings service** using the schema
key verbatim (the page calls `saveSetting(s.key, …)`, no app namespacing), while
`app_config()` reads **emptyos.toml**. An app that declares a schema and reads it
via `app_config` therefore ships a toggle that silently does nothing.

**5 of 96** apps declaring a schema do this, and **none of the 5 read the
settings service**: `garden` (`garden.theme`, `garden.window_days`),
`github-connector` (`github.token`, `github.default_repo`), `healing`
(`workout.weekly_goal`, `sleep.target_hours`), `commons`
(`commons.mark_published`), and `agent_fleet` (fixed, `79864507`).

Statically provable (manifest schema key × `app_config("<same key>")` × absence
of a settings read), ~5% hit rate, zero judgment needed — a good
`/eos-graduate-audit` candidate. Fix shape is `_setting()` in
`apps/extension/dev/agent_fleet/app.py`; the platform precedent is
`_auto_provenance_enabled` in `emptyos/web/server.py`. **Resolved (`b8f040b5`)**: extracted `BaseApp.setting_or_config` at the fourth
consumer and fixed agent_fleet + garden + github-connector + commons. Detector
went 5/96 → 1/96; the survivor is `healing` (`apps/personal/`, gitignored, out
of scope). `commons._cfg` had the rule right first — including that a cleared
text input writes `""` and must count as unset — so its semantics became the
SDK's rather than the reverse.

## Known gap — no retention policy on session records

`list_all()` returns every session ever recorded (`recent_states(n=None)`), and
each `/clear` retires a session id and mints a new one, so `ended` rows
accumulate without bound. Harmless today (they're terminal, and run-center's
hub panel filters to `needs-review`/`running`), but the `/agent_fleet/` page and
the run-center dashboard will grow a long tail. Build a prune/`n` cap when the
list actually gets long — not before.

## Triggers to start building

- Kevin says build, OR
- a multi-agent-heavy work period where session juggling visibly costs time
  (the fleet view's value case), OR
- a third parallel-session collision incident (the registry doubles as
  collision awareness — see `.claude/rules/environment.md` § parallel-session).

## Related deferred rows (docs/DEFERRED-WORK.md)

- "agent_fleet Phase 1-2" row (2026-07-17)
- "eos chat ACP server mode" (2026-07-17) — orthogonal; only if an external
  ADE GUI is adopted after all
- grok-build verify-loop prompt disciplines (2026-07-17) — unrelated to fleet,
  same review session

## Review claim grades (2026-07-17, recorded for the builder)

| Claim | Grade | Disposition |
|---|---|---|
| Avoid terminal hosting; use Orca if PTY embedding becomes necessary | read-verified | keep |
| Phone/PWA `/code/` access already exists | read-verified | keep |
| Hub contributions need priority <150 | read-verified | correct but moot — reuse Run Center's panel [R4] |
| `sdk/worktree.py` covers manager creation | read-verified | only partially — detached-only; Phase 2 chooses branch mode |
| Codex may lack hooks | refuted | Codex hooks exist + already configured here [R2] |
| Orca hooks stripped, WezTerm/walk hooks remain | read-verified | keep |
| Replacement hook will cost less than Orca's | **benchmarked live 2026-07-17** | true but weaker than first claimed: **~60 ms p50** vs the ~172 ms PowerShell status hook it replaces (**~2.9x**), fleet POST included and actually landing. The *existing* hook was the expensive one. The first pass claimed ~36 ms / 4.8x — measured before the POST waited for a reply, i.e. against a daemon that never ingested. A benchmark of a broken path is not a benchmark. |
