# Intent — Runbook

> Living design doc for `apps/public/standard/runbook/`. Edit as the app evolves.
> Birth certificate: the approved plan `borrow-from-deepnote` (MCP connector + Runbook).
> Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why

Deepnote-in-Codex validated the "exploration → persistent workflow → scheduled
run → published app" ladder. EmptyOS had 7 of 8 rungs; the missing piece was a
re-runnable executable artifact. A Runbook is that artifact — but EmptyOS-native,
not a Jupyter clone: ONE human-editable markdown vault note whose ordered typed
`eos-block` fences form a pipeline. The note stays canonical and hand-editable;
existing capabilities + scheduler + the review gate execute it; run-state is
telemetry kept out of the vault.

## Relationships

**Calls into** (`self.call_app(...)`):
- `viz` — `chart` blocks generate a standalone artifact (optional; block errors if absent)
- `rooms` — side-effecting blocks (`write_draft`, `notify`, `call_app` w/ `mutates`) route through `save_pending_action` (the review gate); interactive runs never write without it
- `assistant` — `from-session` reads a session transcript to draft a runbook (optional; `transcript` body is the fallback)

**Uses services** (`self.service(...)`):
- `notifications` — `notify` blocks propose a gated `runbook.send_notification` action. Interactive runs deliver raw after the user's Apply click (present-tense consent — never suppressed). Scheduled (grant-fired) runs route delivery through the proactive gate (`kind="runbook"`: quiet hours / caps / per-kind mute; `priority="critical"` pierces quiet hours; a "disabled" gate falls back to raw)

**Emits:**
- `runbook:created` — a new runbook note was written (UI/from-session)
- `runbook:block_ran` — a single block (run-from-here) finished
- `runbook:run_finished` — a full `run_all` completed (ok/failed)
- `runbook:scheduled` — a cron schedule was set on a runbook

**Listens for** (`@on_event`):
- (none) — runbooks are user/scheduler-triggered, not reactive

## Open questions

- Should `run_from` recompute the full transitive-dependency subgraph rather than
  "this block + everything after in document order"? Linear pipelines make the
  simpler rule correct today; revisit if branching runbooks appear.
- Scheduled side-effects need an `actor_type="runbook"` grant issuer. v1 *reads*
  grants (no-op + `needs-grant` log without one); the issuer is deferred until a
  real scheduled-write need lands.

## Future

- `serialize`-back-to-note edit-in-place (the engine deliberately never rewrites
  user fences today; an explicit "format" action could).
- Block library / examples gallery seeded from `pattern` KB notes.
- Promote a runbook's terminal artifact via the `publish` app (today: hub panel only).
