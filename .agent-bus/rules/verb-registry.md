# Verb Registry — one declaration per invokable app verb

`[[provides.verbs]]` is the single source of truth for "which app methods are
invokable, by which surface, and whether they're autopilot-eligible." It
replaces six fragmented declaration sites that each named overlapping subsets
of the same verbs: voice intents, voice narration, assistant slash commands,
rooms per-agent allowlists, the MCP foundry verb list, and the hand-maintained
`DEFAULT_ELIGIBLE_VERBS` autopilot floor.

**Core:** `emptyos/sdk/verb_registry.py` (`VerbEntry`, `VerbRegistry`,
`parse_verb_entry`) — pure, unit-tested. **Aggregator:**
`AppLoader.get_verbs()` (`emptyos/kernel/app_loader.py`). **Drift sweep:**
`GET /agent/api/verbs/sweep` (`?load=1` to force-load all verb-declaring apps).
**Reference impl:** `apps/public/core/task/manifest.toml`.

## Schema

```toml
[[provides.verbs]]
verb = "task.add"                 # <app>.<name>; app-half == app id OR a manifest alias
method = "add"                    # canonical call_app handler (agent [DO:] / MCP dispatch to this)
summary = "Capture a quick task"
args = { text = "string", due = "string?" }
eligibility = "stable"            # stable | gated | never  -> autopilot floor
surfaces = ["voice", "assistant", "mcp", "agent"]
voice = { method = "voice_add_task", example = "...", description = "...", always = true, card = "task-list", narrate = "narrate_after_add" }
assistant = { slash = "/add", method = "add", arg = "text", inverse = "reopen" }
```

- **Per-surface method.** A verb's dispatch method is surface-specific: `task.add`
  runs `add` for agent/MCP but `voice_add_task` (a wrapper returning `{say, card}`)
  for voice. The top-level `method` is canonical; a surface sub-table may override
  it with `<surface>.method`. `VerbEntry.method_for(surface)` resolves it.
  A **voice-only** verb (no canonical call_app method, e.g. `rooms.list` →
  `voice_list_rooms`) sets `method` to the voice handler and `surfaces = ["voice"]`.
- **Eligibility is the autopilot floor classifier.** `stable` = payload predictable
  enough to be autopilot-grant-eligible; `gated` = always reviewed; `never` =
  free-form / irreversible / outbound, never grantable. The floor reads only
  `verb` + `eligibility` — surface-agnostic.
- **Aliases.** The verb app-half must equal the app id or a `[app] aliases` entry
  (`aura.*` on `voice-assistant`, `capture.add` on `quick-action`). This is the
  cross-app guard (`evil.method` declared by `task` is rejected).
- TOML inline sub-tables (`voice = { ... }`) **cannot span lines** — keep them on
  one line. `[[provides.verbs]]` then `[provides.verbs.voice]` mis-nests; use inline.

## Two load-bearing rules

1. **Migration is all-or-nothing per (app, surface).** When a surface's consumer
   reads the registry (Phases 2-4), it skips an app's legacy declaration for
   *that surface* iff the app has any registry entry declaring that surface. So
   migrating a surface for an app means including **every** one of that app's
   verbs for that surface (e.g. all the app's voice intents together) — a partial
   migration drops the omitted ones. The floor (eligibility) is surface-agnostic
   and is exempt: an app can carry only its eligible verbs while a surface stays
   legacy.

2. **The autopilot floor uses an injected, recomputed set — never persisted.**
   `kernel.autopilot_eligible_set()` returns `effective_eligible = (derived ∪
   operator_eligible) − operator_removed`, recomputed fresh each call, passed as
   `eligible=` to `autopilot.match` / `is_eligible` / `save_grant`. Because it is
   never cached, a `stable→gated` manifest flip revokes immediately. Gated by the
   dark-default flag `[autopilot] derive_floor_from_registry`; when off, the
   legacy `policy.json` floor applies (zero behaviour change).

## Drift is loud, not silent

A renamed/typo'd method surfaces two ways: a WARNING at app instance-creation
(`AppLoader._validate_app_verbs`) and a finding in the sweep (`/agent/api/verbs/sweep`,
the release / `/preflight` gate). This is the whole point — the prior
hand-maintained floor drifted silently (it even contained a phantom, `kb.tag`,
implemented nowhere; the registry exposed it and it was retired 2026-06-07).

## Migration status (2026-06-07)

- **Phase 0 (done):** registry primitive + `get_verbs()` + boot validator + sweep.
- **Phase 1 (done):** autopilot floor derives from the registry; flag flipped on.
  20 eligible verbs migrated across 9 apps; sandbox-verified (20/20 resolve, zero
  drift, eligible set == prior DEFAULT minus the `kb.tag` phantom).
- **Phase 2 (done):** MCP foundry verb list derives from `for_surface("mcp")`;
  legacy `[provides.mcp_foundry]` removed from task + quick-action. Output identical.
- **Phase 3 (done):** voice-assistant builds `_intents` + narrators from
  `for_surface("voice")` (`method_for("voice")` + `voice.narrate`); all 9 apps'
  legacy `[[contributes.voice-assistant.intent/narration]]` blocks removed.
  Two non-eligible voice verbs added for completeness (`reader.read_aloud`,
  `voice.research`); `voice` added as a voice-assistant alias. Sandbox-verified
  (28 intents unchanged, sourced purely from the registry).
- **Phase 4 (done):** `assistant/slash.py` builds the slash table from
  `for_surface("assistant")`; task's 5 commands (`/add /tasks /done /reopen
  /snooze`) + quick-action's `/inbox` migrated (the 4 non-`add` task commands
  added as `eligibility="gated"` reads), legacy `[provides.assistant]` removed
  from both. Non-migrated apps (journal's `/journal-*`, etc.) still read legacy.
- **Phase 5 (done):** `GET /rooms/api/verb-menu` returns `for_surface("agent")`
  grouped by app (5 verbs: task.add, capture.add, rooms.team_*) for the
  agent-config picklist. Read-only; `surfaces ∋ "agent"` = *offered*, the
  per-agent `server_actions` JSON stays the execution allowlist.

**All 6 phases complete (2026-06-07).** Voice + assistant + MCP surfaces are
single-source (legacy declarations deleted); the agent picklist + autopilot floor
read the registry. Remaining legacy `[[contributes.voice-assistant.intent]]` /
`[provides.assistant]` blocks belong only to **non-migrated** apps; migrate them
the same way (registry entry → consumer already reads it → delete legacy) when
touched. The MCP foundry ships dark; the rooms agent-menu UI picklist that
consumes `/rooms/api/verb-menu` is not built yet (endpoint is the enabler).

## When NOT to add a verb entry

- A method that's internal plumbing, not an invokable user verb.
- A surface that can't dispatch it (no method exists for that surface) — omit the
  surface rather than declaring a method that will fail the drift check.
- Free-form / irreversible / outbound verbs are still declarable, but with
  `eligibility = "never"` — they can be invoked (gated) but never autopilot-granted.
