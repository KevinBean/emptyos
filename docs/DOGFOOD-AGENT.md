# dogfood-agent

Spawn `claude-cli` as a persona that uses a throwaway EmptyOS instance like a real human. Surface friction as **issues** (deduped across runs), and let a coding agent fix them one by one.

## Mental model

The unit of work is the **issue** — a discrete problem the system has — not the **run**. A run is just an event that surfaces or verifies issues; an issue persists until it's fixed (verified) or dismissed (judgment).

- **Issues** (primary view): all open friction items, grouped by the scenario that last surfaced them. Each has a fix-prompt file you copy into Claude Code.
- **Activity** (secondary view): timeline of runs with full transcript + heatmap. The debugging surface, not the daily one.
- **Header bar** shows agent state at a glance: enabled? next cron fire? runs today? open issues?

## Morning workflow (the thing this is for)

1. Open `/dogfood-agent/` → Issues tab is already showing what surfaced overnight, grouped by scenario.
2. For each real bug: click **📋 Copy** → paste into Claude Code → it fixes → restart daemon (Ctrl+K → "Restart Daemon").
3. For noise / wontfix: click **Dismiss** on the issue.
4. After fixes: hit **▶ Re-run to verify these** on each affected scenario. Verify run tests the *whole* scenario at once. Cleared issues auto-move to `done/`. Issues that come back show as "still there".
5. Close laptop. Cron fires more runs while you sleep.

## One-time setup

1. **Make a throwaway PARA vault.** Click **Bootstrap vault** on the setup banner (header). The endpoint creates `dogfood/vault/` with PARA folders + a few seed notes, plus `dogfood/emptyos.toml` + `dogfood/start.bat`.
2. **Run the dogfood daemon** in a second terminal: double-click `dogfood\start.bat`. It listens on port 9001.
3. **Configure cron** via the ⚙ Settings panel (or `[apps.dogfood-agent]` in your real `emptyos.toml`):

```toml
[apps.dogfood-agent]
vault_path = "D:/emptyos/dogfood/vault"
daemon_url = "http://localhost:9001"

schedule = "0 */4 * * *"        # every 4 hours; empty = manual mode only
enabled = true                  # cron kill switch (the header pill toggles this live)
daily_budget = 6                # max scheduled runs/day; manual runs uncapped
error_backoff_after = 3         # if last N scheduled runs all errored, pause until enabled flipped
telegram_on_new_friction = true # push to telegram on NEW (not duplicate) friction

rotation = [
    { persona = "kevin-weekday", scenario = "tuesday-evening" },
    { persona = "kevin-weekday", scenario = "saturday-morning" },
    { persona = "new-user",      scenario = "tuesday-evening" },
]
```

4. Restart the real daemon (`Ctrl+K → Restart Daemon`) so the cron registers.

## Cron guards (in order, before any work runs)

1. **Kill switch** — `enabled = false` in config (or click the **⏸ Pause cron** pill in the header) → skip without touching anything.
2. **Error backoff** — last `error_backoff_after` scheduled runs all errored → skip; flip `enabled` to ack-and-resume.
3. **Daily budget** — `daily_budget` scheduled runs already today → skip.
4. **Daemon health** — dogfood daemon at `daemon_url` unreachable → skip *without* consuming budget (fixing the daemon shouldn't cost the day's runs).

Manual runs from the header bar are always uncapped. Verify-runs (per-scenario) also bypass budget — they're triggered by you, not the cron.

## Issue dedup

Friction items are keyed by `kind::first-60-chars-lowercased`. First time a key is seen → new issue, fix-prompt file written, telegram pinged, task created in `emptyos-dogfood` project. Same key in any later run → existing issue's `count` and `last_seen` get bumped; **no second task, no second telegram, no duplicate prompt file**.

When a verify-run shows an issue cleared (its key didn't appear), the fix-prompt file moves to `fix-prompts/done/` and the seen-friction entry is dropped. A future regression of the same text will create a fresh task + ping (treated as new).

## Fix-prompt queue (agent-readable)

Every NEW issue writes a markdown fix-prompt to:

```
data/apps/dogfood-agent/fix-prompts/<safe-key>.md
```

Plus an auto-rebuilt index at `fix-prompts/_queue.md` listing pending items in priority order (bug → confusing → missing, then most-recent first).

A Claude Code skill can read the queue and process items unattended:

```
1. Read data/apps/dogfood-agent/fix-prompts/_queue.md
2. Pick the top entry, read its file for full context
3. Fix the bug per the embedded instructions
4. Move the file to fix-prompts/done/   (or POST /dogfood-agent/api/queue/<file>/done)
5. Repeat
```

The fix-prompt body has YAML frontmatter (`kind`, `count`, `first_seen`, `last_seen`, `last_run_id`, `recent_runs`) so the skill can prioritize without re-parsing the body.

## Hub panel

Contributes a `dashboard`-group hub-panel: `Dogfood · N runs/24h · M to triage`. Visible on `/hub/`.

## Telegram notifications

When a NEW friction key lands (not a duplicate), the app pushes a one-line message via the `telegram` plugin if available. Duplicates do NOT notify. Set `telegram_on_new_friction = false` to disable, or remove the telegram plugin to silence everything.

## Verify runs (the single most useful action)

Per-issue "re-test" buttons aren't a thing — verify is always whole-scenario. From the Issues tab, each scenario group has **▶ Re-run to verify these** which fires a single scenario rerun anchored to its most recent run. After it finishes:

- Issues that didn't reappear → auto-marked done in queue.
- Issues that did reappear → flagged "still there" (the fix didn't hold).
- New friction → opened as new issues.

Manual single-run from the header bar is always available for ad-hoc testing.

## File layout

```
apps/dogfood-agent/
├── manifest.toml             # provides web + hub panel; emits dogfood:friction
├── app.py                    # endpoints + scheduler + finalization
├── behavior.py               # transcript → friction + heatmap + diffs
├── personas/                 # markdown — the human profile
│   ├── new-user.md           # generic, ships
│   └── kevin-weekday.md      # personal, gitignored — drop your own .md files here

├── scenarios/                # markdown — the situation + 15-turn budget
│   ├── tuesday-evening.md
│   └── saturday-morning.md
├── pages/index.html          # Issues / Activity tabs + header + side detail
└── README.md                 # this file

data/apps/dogfood-agent/      # gitignored runtime data
├── runs/<run_id>/            # per-run: run.json, behavior.json, stream.jsonl, dogfood-log.md
├── fix-prompts/              # one .md per open issue
│   ├── _queue.md             # auto-rebuilt index for skills
│   └── done/                 # processed/cleared/dismissed items
├── seen_friction.json        # cross-run dedup state
├── behavior-rollup.json      # aggregate heatmap across all runs
├── state.json                # rotation pointer + daily budget counter
└── activity log              # syslog table (via log_activity)
```

## When to declare success

**Pass:** Wednesday-night cron runs while you sleep. Thursday morning: 1-3 fresh issues in the Issues tab — at least one is a real bug or genuine UX gap. None are duplicates.

**Fail:** Telegram floods (dedup broken), tasks pile exponentially, verify-runs don't credit cleared items. Kill via `enabled = false` and report what went wrong.
