# Daemon Handling — Don't Touch It From Inside Claude

EmptyOS runs **two user-owned daemons** on this machine, both bound by the same hands-off rule:

| Port | Role | Spawned by |
|---|---|---|
| `:9000` | Main daemon (`python -m emptyos start`) — real vault, real providers | User via `restart.bat` |
| `:9001` | Dogfood sidecar — throwaway vault under `dogfood/`, human-only `think` | Auto-spawned by `:9000` via `plugins/dogfood-demo/plugin.py:auto_start()` |

Because the dogfood daemon is plugin-spawned by `:9000`, **one `restart.bat` covers both** — there is no separate restart command for `:9001`. From a Claude Code session, every daemon on the machine is **read-only context** — probe them, don't manage them.

## Hard rules

These apply to **every user-owned daemon** (`:9000` and `:9001`), not just the main one:

1. **Never run `restart.bat`, `stop.bat`, or `python -m emptyos start` from Bash/PowerShell.** These spawn the daemon under Claude's process group; when the session ends or the tool times out, the daemon dies with it. Always ask the user to run it from their own terminal.

2. **Never `taskkill` / `Stop-Process` python.exe processes**, even when something looks stuck. Stale-process diagnosis is fine (showing PIDs + start times). The user decides whether to kill anything. This applies to *all* python.exe — you can't reliably tell from a PID list which one is `:9000` vs `:9001` vs an unrelated script, and killing the wrong one corrupts state.

3. **Never delete `data/*.db`, `data/*.db-wal`, or `data/*.db-shm`** — neither under the main daemon's `data/` nor under `dogfood/data/`. Those are live SQLite handles. `restart.bat` cleans them up safely after killing all writers; nothing else should touch them.

4. **Don't run one-shot Python that imports `emptyos.kernel.*`.** Importing the kernel opens a syslog SQLite connection. Even a quick `python -c` opens a WAL handle, and a kill mid-import can leave a lock. Safe alternatives:
   - `python -m pytest tests/...` (pytest fixtures don't boot the kernel)
   - `python -c "from emptyos.sdk.foo import ..."` (SDK helpers — pure modules, no kernel boot)
   - `python -c "from engines... import ..."` (engines never touch the kernel)
   - HTTP probes against the running daemon (`curl http://localhost:9000/...` or `curl http://localhost:9001/...`)

   When in doubt, ask "does this import boot the kernel?" If yes, run it through a daemon's API instead.

5. **If a daemon is unreachable, surface the diagnosis and stop.** Don't spawn a new one, don't kill stragglers, don't poll in tight loops. Tell the user the symptom (no listener on the port, stale PIDs, syslog lock error from `data/daemon.err.log` or `dogfood/data/daemon.err.log`) and let them act.

## After editing `.py` files — the reminder shape

Python changes don't take effect until the daemon respawns. After any edit to `apps/**/*.py`, `plugins/**/*.py`, `emptyos/**/*.py`, or anything else the daemon imports, tell the user something like:

> This change won't be live until you run `restart.bat` — that respawns `:9000`, which in turn respawns `:9001` via the `dogfood-demo` plugin, so both pick up the new code in one shot.

If the change only affects an app's HTML/CSS/JS under `pages/`, no restart is needed — the daemon serves those from disk per request. Don't pad reminders onto static-only edits.

If `dogfood-demo` is disabled (check `[plugins.dogfood-demo] enabled` in `emptyos.toml`, or `curl http://localhost:9001/api/health` returns connection refused), only `:9000` needs to come back — adjust the wording accordingly.

## Why these rules exist

- Daemons launched from a Claude tool inherit Claude's process tree. When the tool returns, Windows reaps the children — the daemon vanishes silently.
- SQLite WAL is OS-handle-based, not file-based. Killing a writer leaves a dangling handle until the OS releases it (often seconds later). Deleting WAL/SHM during that window corrupts the next write.
- Multiple Claude sessions sometimes run in parallel; if each one tries to "fix" the daemon, they fight.
- The user has a working `restart.bat` + tray icon + `/eos-session-resume` flow. Claude's job is to evolve EmptyOS, not to babysit its runtime.

## What's allowed

- `curl http://localhost:9000/...` and `curl http://localhost:9001/...` — probe live state on either daemon.
- Reading `data/daemon.log`, `data/daemon.err.log`, `data/eos-stdout.log`, `data/eos-stderr.log` (and their `dogfood/data/` counterparts) — surface failures.
- Inspecting processes (`Get-NetTCPConnection`, `Get-Process`) — diagnose, don't act.
- `python -m pytest tests/...` — runs against the main daemon over HTTP, doesn't compete with it.
- Editing source files — the daemons hot-reload HTML; Python changes need the user to restart.

## When a daemon is needed for verification

Most code changes can be verified offline:
- Engine logic → `python -m pytest engines/...`
- SDK helpers → `python -m pytest tests/test_sdk_*.py`
- App logic exercised through HTTP → ask the user to restart, then run `python -m pytest tests/test_sys_<app>.py` against `:9000`

Only ask for a daemon restart when there's no offline path. Bundle multiple changes per restart cycle.

## Claude-owned sandbox daemons (built — `plugins/sandbox-pool/`)

Sandbox daemons on `:9002+` ARE the carve-out from this rule. They're spawned + supervised by the `sandbox-pool` plugin, which writes each PID to `data/sandbox/pool.json` and only ever terminates handles it owns. Claude never `taskkill`s a member directly — every kill goes through `POST /sandbox/api/lease/{id}/restart`, which the plugin executes with full knowledge of which PID it owns.

The contract for Claude:

1. **Lease** a member before any sandbox work: `POST /sandbox/api/lease` body `{"purpose": "<short tag>"}`.
2. **Restart** the member after a code edit: `POST /sandbox/api/lease/{lease_id}/restart`. Returns when the new daemon answers `/api/health`.
3. **Release** when done: `DELETE /sandbox/api/lease/{lease_id}`.
4. **Never** Bash `taskkill` or `python -m emptyos start` against `:9002+` — even though the rule's daemon-handling exception applies to those ports, the supervised path is cleaner (no orphaned subprocesses, no PID guessing, no state corruption) and the auto-classifier will refuse raw kills anyway. Use the API.

Full contract: `.claude/rules/sandbox-usage.md`. The user's `restart.bat` still kills all `python.exe`, so pool members die on every user restart — acceptable, the plugin re-attaches on the next main-daemon boot via `data/sandbox/pool.json`.
