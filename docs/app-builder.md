# app-builder — operational contract

The architectural shape of the autonomous app-creation loop lives in a local Claude Code plan note and in `.claude/rules/test-fix-verify-loop.md` (the friction→fix→verify pattern this loop adapts). This doc is the **operational contract**: what `app-builder` guarantees, what it refuses, and how its gates compose with daemon-handling and autopilot-grants rules.

Read this before driving a scaffold run, before touching `apps/extension/dev/app-builder/`, or before extending the autonomous wiring.

## What app-builder is for

`app-builder` is `fix-agent`'s sibling for **new-app creation**. Given a spec note in the vault, it spawns claude-cli inside a git worktree, has it scaffold one new EmptyOS app per the spec, and exposes a 4-gate review path (merge / verify / revert / discard) so a human reviews each state transition.

What it is **not** for:

- **Modifying an existing app.** Use `fix-agent` for that — `app-builder` refuses if `apps/<app_id>/` already exists.
- **Cross-app refactors.** The scope guard in `api_run_merge` refuses any diff touching files outside `apps/<app_id>/ + tests/test_sys_<app_id>.py + release.toml`.
- **Spec generation.** Spec notes are written by the human (interactive `eos-new-app` skill) or by a persona drafter (loop-internal, Phase 2 in `apps/personal/staff/`). `app-builder` consumes specs; it does not invent them.

## The 4 gates

```
spec note exists in vault                          ← human writes (or persona drafts)
        │
        ▼ POST /app-builder/api/run {spec_path}
spawn claude-cli in .claude/worktrees/app-builder/
        │
        ▼ status: queued → running → ready
        │
   [GATE 1: Merge]                                 ← human reviews diff, clicks Merge
        │ py_compile gate + scope guard
        ▼ git merge --ff-only → status: merged
        │
   [GATE 2: Restart daemon]                        ← human runs restart.bat
        │ (per daemon-handling.md — never automated)
        │
   [GATE 3: Install via Store]                     ← human flips toggle at /store
        │
        ▼ POST /app-builder/api/runs/{rid}/verify
endpoint smoke against the loopback daemon
        │
        ▼ status: verified | verify-failed | verify-app-not-loaded
        │
   [GATE 4: Revert (if verify-failed)]             ← human decides revert or hand-fix
```

The cron-driven spec drafter (a persona that fires on a project entering `status: spec-ready` and emits a spec note for review) is the *only* automation between gates and lives in `apps/personal/staff/` — Kevin-specific, not in core. The four gates above are mandatory regardless of how the spec got written.

## What worktree-per-scaffold guarantees

`app-builder` keeps **one** worktree at `.claude/worktrees/app-builder/` (mirrors `fix-agent`'s shape). Per scaffold, `_run_one` resets the worktree:

```
git reset --hard HEAD
git clean -fd
git checkout -B app-builder/<app_id> main
```

This guarantees:

- **Isolation across runs** — every scaffold starts from `main`'s current tip on its own branch.
- **Main's checkout untouched while claude-cli edits** — the worktree is a separate checkout; the user's main editor + `:9000` daemon keep running against the primary checkout.
- **One scaffold at a time** — `asyncio.Lock` serializes runs. Queue multiple, they execute sequentially.

What it does **NOT** guarantee:

- **No orphan branches.** Discard explicitly deletes; merge leaves the branch in place; interrupted runs leave the in-flight branch behind.
- **Worktree cleanup on uninstall.** Removing `apps/extension/dev/app-builder/` doesn't `git worktree remove` the directory.

## Gate 1 — the merge guard

`api_run_merge` runs **two** static gates before any merge:

1. **py_compile**: every changed `*.py` file in the diff is compiled. A `SyntaxError` in the scaffold would make the daemon fail to load the new app on restart — the worst possible blind-merge outcome. Refuse if any file fails.
2. **Scope guard**: the diff is allowed to touch only `apps/<app_id>/`, `tests/test_sys_<app_id>.py`, or `release.toml`. Any file outside this set refuses the merge. This is the load-bearing safety property: claude-cli operating in scaffold mode is constrained to its own sandbox, never the kernel or other apps.

Both gates' results are stored on the run record (`meta["compile_check"]` and `meta["scope_check"]`) so the UI can surface the specific failure.

The merge itself is `git merge --ff-only <branch>` — no merge commits. Non-fast-forwardable means the user has to rebase the branch manually; the safest signal is "the world moved under us."

## Gate 3 — endpoint smoke verify

`api_run_verify` runs a Python-driven smoke test against the loopback daemon (`127.0.0.1:<network.port>`):

1. **Loaded check**: `GET /api/apps` — confirms `<app_id>` is in the daemon's loaded set. If absent, status flips to `verify-app-not-loaded` with a clear "restart + install via /store" reason. Re-verify is idempotent — once the app loads, re-running advances the state machine.
2. **Endpoint smoke**: parse the spec's `## Acceptance criteria` section for `(METHOD, PATH)` tuples, hit each against the loopback daemon, record status code + ok-flag per endpoint. An endpoint is "ok" if it responds with anything < 500 except 404 — the goal is "route exists and validates input," not "succeeds with no body."

If all endpoints pass: status → `verified`. Any failure: status → `verify-failed`, revert becomes available.

Acceptance-criteria bullet shapes the parser recognises:

```markdown
- GET /myapp/api/status
- `POST /myapp/api/add`
- DELETE /myapp/api/items/{id}
```

If the spec declares no HTTP endpoints in its acceptance criteria, verify passes with `reason: "no HTTP endpoints declared in acceptance criteria — passed by default"`. Specs for non-HTTP apps (CLI-only, event-only) should make the verify intent explicit in the body.

## Auth + loopback

Verify hits the loopback daemon with the `auth_token` from `[network]` config — bearer header. This works because `app-builder` runs *inside* that same daemon and can read its own config. Self-hosters with no auth_token configured get no header, which is correct.

The `[network] host` value is intentionally ignored — verify always targets `127.0.0.1:<port>`. The same code must work on a laptop (host=`127.0.0.1`), a Tailscale machine (host=`100.x.x.x`), or a VPS (host=`0.0.0.0`) without changes; loopback is the only universal address.

## What `app-builder` writes and where

| Path | What | Lifecycle |
|---|---|---|
| `.claude/worktrees/app-builder/` | Active claude-cli worktree | Reset per run; reused across runs |
| `data/apps/app-builder/runs/<run_id>/run.json` | Per-run metadata | Persistent; trimmed in UI to last 50 |
| `data/apps/app-builder/runs/<run_id>/prompt.md` | Spec body sent to claude-cli | Persistent (audit) |
| `data/apps/app-builder/runs/<run_id>/stream.jsonl` | claude-cli streaming output | Persistent (audit) |
| `data/apps/app-builder/runs/<run_id>/stderr.log` | claude-cli stderr | Persistent (debug) |

`app-builder` never writes to the vault, never writes outside its own `data/` dir. The vault read is one call (`self.read(spec_path)` to pull the spec body).

## Reactor wiring

`app-builder` emits three events:

- `app-builder:run_started` — claude-cli has started writing
- `app-builder:run_finished` — terminal status reached (ready / no-changes / error / merged / verified / verify-failed / verify-app-not-loaded)
- `app-builder:scaffold_merged` — scaffold landed on main

The reactor logs all three to the action log. `scaffold_merged` additionally ripples a journal breadcrumb so the day's daily note records the milestone alongside the rest of the day's work. The reactor stops there — no auto-restart, no auto-install, no auto-verify. Per autopilot-grants rule, free-form vault writes (which this milestone effectively is, at scale) are never autopilot-eligible. The human is the gate.

## When NOT to use app-builder

- **App is already in the codebase.** Use `fix-agent`. `app-builder` refuses on collision.
- **Spec is unstable / under negotiation.** Iterate on the spec note in the vault first; only fire app-builder when the spec is "ready to scaffold." Each scaffold burns claude-cli tokens; a spec that needs three rewrites wastes three runs.
- **Diff would necessarily touch existing code.** The scope guard refuses. Either land the prerequisite changes via `fix-agent` first, or recognise this isn't really "a new app" — it's a feature on an existing one.
- **You want a Python module, not an EmptyOS app.** Plain Python modules don't need scaffolding; create them directly.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `apps/<app_id>/ already exists` on `api_run` | App was scaffolded in a prior run, or you've got the wrong `app_id` in the spec | Update the spec, OR delete the old `apps/<app_id>/` first (rare) |
| `agent-runtime plugin not loaded` | Plugin disabled in `emptyos.toml` `[plugins.agent-runtime]` | Enable the plugin |
| `claude CLI not on PATH` | `claude` binary not installed or not in PATH | Install Claude Code |
| `git worktree add failed` | `.claude/worktrees/app-builder/` exists from a prior install but isn't a worktree | `git worktree prune` then retry |
| `ff-merge failed (branch likely needs rebase)` | Main moved while the scaffold ran | Rebase the branch manually, then retry the merge |
| `scaffold touched files outside apps/<app_id>/ + tests/...` | claude-cli wandered outside its scope | Discard the run; tighten the spec; retry |
| `py_compile failed` | SyntaxError in the scaffold | Discard the run; retry (claude-cli is usually consistent on second attempt) |
| `app '<id>' not loaded in daemon` on verify | Daemon hasn't restarted since merge, OR app isn't installed in the Store | `restart.bat`, then enable via `/store` |
| Verify says route is 404 | claude-cli scaffolded the manifest's `[provides.web].prefix` differently than the spec assumes | Inspect `apps/<app_id>/manifest.toml`; fix-agent the prefix, or update the spec's acceptance criteria |

## Phase status

| Phase | Item | Status |
|---|---|---|
| 1 | Manual trigger via UI (paste spec_path → Scaffold) | ✓ |
| 1 | Worktree + claude-cli + py_compile gate + scope guard | ✓ |
| 1 | API tests (validation + parser unit) | ✓ |
| 2 | Verify via endpoint smoke (Python-driven) | ✓ |
| 2 | Revert on verify-failed | ✓ |
| 2 | Reactor journal-ripple on scaffold_merged | ✓ |
| 2 | docs/app-builder.md + eos-new-app SKILL automated-mode section | ✓ |
| 3 | `api_draft_from_project` — LLM-drafts a spec from a project note | ✓ |
| 3 | Reactor `projects:status_changed` → fires drafter (fire-and-forget) | ✓ |
| 3 | Reactor `app-builder:draft_ready` → journal-ripples the spec path | ✓ |
| 3 | Reactor `dogfood:verify_completed` → journal-ripples scaffold-smoke verdict | ✓ |
| 3 | Dogfood `scaffold-smoke` scenario file | ✓ |
| 3 | End-to-end test (skip-marked, manual smoke only) | ✓ |

After Phase 3, the loop fires end-to-end without a human starting it: flip a project's status to `spec-ready` → reactor calls drafter → spec lands in grill dir with a 📝 journal breadcrumb → human reviews → POST /api/run → scaffold → review → merge → restart → install → verify. Four human gates remain (spec review, merge, restart/install, ship-flag) — none of them can be removed without violating the autopilot-grants rule.

## Phase 3 — autonomous drafting

The drafter lives in `app-builder` itself (not in `apps/personal/staff/`) because it's pure: project markdown + linked KB → LLM call → spec note write. It owns no state.

**Trigger.** Reactor handler `on_project_status_changed` (in `apps/public/standard/reactor/reactions_system.py`) filters `projects:status_changed` events for `new == "spec-ready"`. When matched, it fires `call_app("app-builder", "api_draft_from_project", project_id)` via `self.spawn_background(...)` so the reactor handler returns immediately (per memory `feedback_long_handler_in_http_request` — synchronous handlers blocking on LLM calls break the event chain). It was a bare `asyncio.create_task` until 2026-08-06; that detaches but keeps no strong reference, so the drafter could be garbage-collected before it ran.

**Input.** `POST /app-builder/api/draft_from_project {"project_id": "<slug>"}`. The endpoint:
1. Reads `10_Projects/<project_id>/<project_id>.md`
2. Extracts wikilinks from the body, attempts to read up to 10 linked KB notes (looks in `kb/sources/`, `kb/notes/`, `kb/docs/`)
3. Calls `self.think(user_prompt, system=_DRAFTER_SYSTEM_PROMPT, domain="text", temperature=0.3, max_tokens=2000)`
4. Validates the output starts with `---` (frontmatter) — if not, refuses and returns a preview
5. Writes to `30_Resources/EmptyOS/grill/draft-<project_id>-<ts>.md`
6. Emits `app-builder:draft_ready {project_id, spec_path}`

**Drafter system prompt** (`apps/extension/dev/app-builder/prompts.py:_DRAFTER_SYSTEM_PROMPT`) instructs the LLM to produce exactly the spec shape `app-builder` consumes — same frontmatter keys, same body sections. The prompt explicitly tells the model to land assumptions in `## Open questions` rather than hiding them in prose, so the human reviewer sees what was guessed.

**Why not autopilot the human review?** Per `.claude/rules/autopilot-grants.md`, free-form vault writes (specs, code, KB notes) are never autopilot-eligible. The diff IS the value of the gate. Auto-approving spec drafts → auto-firing app-builder → auto-merging would be a "Maximizer mode" anti-pattern — the user would only see the result, not the decisions. The draft is the input the human needs; the gate is non-negotiable.

## Phase 3 — dogfood-driven verify (alternative to Python smoke)

`apps/extension/dev/dogfood-agent/scenarios/scaffold-smoke.md` is a persona-driven scenario that walks the app's home page + smokes every endpoint listed in the spec's acceptance criteria. Use it when:

- The Python smoke (`api_run_verify`) passes but you want a "would a user be confused" check
- The app's acceptance criteria include behaviour beyond raw HTTP status codes (page renders, UI affordances visible, etc.)
- You're shipping the app to demo and want a dogfood verdict on the record

Fire it via the existing `dogfood-agent` API:
```
POST /dogfood-agent/api/run
  {"persona": "kevin-weekday", "scenario": "scaffold-smoke"}
```

When the scenario completes, dogfood-agent emits `dogfood:verify_completed {kind, app_id, ok}` — reactor logs and journal-ripples 🧪 with the verdict. The decision to mark the project shipped stays with the human (the gate isn't bypassable by the verify result alone).

There is intentionally no app-builder UI button for this — wire it into your own workflow when you want it.
