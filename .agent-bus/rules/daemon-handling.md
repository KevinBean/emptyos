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

> **The user-owned watchdog is the exception that proves rule 5 — and it is NOT a license for Claude.** `scripts/daemon_watchdog.py --restart`, launched by `restart.bat` in its own window, DOES restart/kill :9000 on a crash or wedge (targeted-kill the daemon PID tree + detached respawn, with a storm guard). That's legitimate because it's a **user-owned** process the user opted into via restart.bat — the restart is the user's standing instruction, not Claude's live action. From a Claude session you still **never** run it with `--restart`, never invoke `kill_pid_tree`, and never respawn :9000 yourself; rule 5 (surface + stop) is unchanged for you. See `.claude/rules/debugging.md` § Tooling for a wedge.

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

## The release boot smoke is not a daemon you own

`scripts/check_snapshot_boot.py` spawns `python -m emptyos start` as a child it
owns, on an **ephemeral port** (never 9000-9009), with its `data_dir` and vault
in a scratch temp dir, and terminates it in a `finally` via
`emptyos.sdk.daemon_supervisor.terminate_daemon` (the same helper sandbox-pool
uses). It never touches `:9000` / `:9001`, never reads the user's vault, and
never `taskkill`s a PID it didn't spawn.

So the "never run `python -m emptyos start`" rule above does **not** apply to
it. Running `python scripts/check_snapshot_boot.py` (or `python
scripts/preflight.py --scope release`, which invokes it) from a Claude session
is safe and expected — it's how a release proves the snapshot boots before it
ships. Don't "fix" it into an HTTP probe against `:9000`: probing the running
daemon tests the *working tree with cached modules*, which is precisely the
blind spot that cost releases v0.2.7-v0.2.10.

## Claude-owned sandbox daemons (built — `plugins/sandbox-pool/`)

Sandbox daemons on `:9002+` ARE the carve-out from this rule. They're spawned + supervised by the `sandbox-pool` plugin, which writes each PID to `data/sandbox/pool.json` and only ever terminates handles it owns. Claude never `taskkill`s a member directly — every kill goes through `POST /sandbox/api/lease/{id}/restart`, which the plugin executes with full knowledge of which PID it owns.

The contract for Claude:

1. **Lease** a member before any sandbox work: `POST /sandbox/api/lease` body `{"purpose": "<short tag>"}`.
2. **Restart** the member after a code edit: `POST /sandbox/api/lease/{lease_id}/restart`. Returns when the new daemon answers `/api/health`.
3. **Release** when done: `DELETE /sandbox/api/lease/{lease_id}`.
4. **Never** Bash `taskkill` or `python -m emptyos start` against `:9002+` — even though the rule's daemon-handling exception applies to those ports, the supervised path is cleaner (no orphaned subprocesses, no PID guessing, no state corruption) and the auto-classifier will refuse raw kills anyway. Use the API.

Full contract: `.claude/rules/sandbox-usage.md`. The user's `restart.bat` still kills all `python.exe`, so pool members die on every user restart — acceptable, the plugin re-attaches on the next main-daemon boot via `data/sandbox/pool.json`.

## Detached subprocess survival (agent-runtime — landed 2026-05-27)

`agent-runtime` exposes a `spawn_detached(cmd, ..., supervision_key=...)` path that spawns children with Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` (POSIX `start_new_session`). The child outlives the spawning daemon, so `restart.bat` killing every `python.exe` no longer kills these subprocesses — only Python processes die; bare claude-cli grandchildren keep writing to their stdout files.

On boot, the plugin's supervision record at `data/agent-runtime/supervised/<key>.json` lets consumers re-attach to a live PID (via `runtime.reattach(key, ...)`) or reconstruct outcome from the on-disk stream when the PID is gone (`exited_before_attach: true`). First consumer: `apps/dogfood-agent/`, where `_resume_supervised_runs` (called from `setup()`) re-attaches every supervised run before the legacy reaper runs. The "interrupted (daemon restart)" outcome that dominated 91% of runs prior to 2026-05-27 should now be rare.

This doesn't change anything about Claude's daemon-handling rules — the user still owns `:9000`/`:9001`; Claude still doesn't `taskkill`, restart, or touch SQLite. The new path just means a restart during a dogfood run is no longer fatal to the run.
