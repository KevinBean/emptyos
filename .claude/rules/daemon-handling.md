# Daemon Handling — Don't Touch It From Inside Claude

The EmptyOS daemon (`python -m emptyos start`, port 9000) is owned by the user, not by Claude. From a Claude Code session, the daemon is **read-only context** — probe it, don't manage it.

## Hard rules

1. **Never run `restart.bat`, `stop.bat`, or `python -m emptyos start` from Bash/PowerShell.** These spawn the daemon under Claude's process group; when the session ends or the tool times out, the daemon dies with it. Always ask the user to run it from their own terminal.

2. **Never `taskkill` / `Stop-Process` python.exe processes**, even when something looks stuck. Stale-process diagnosis is fine (showing PIDs + start times). The user decides whether to kill anything.

3. **Never delete `data/*.db`, `data/*.db-wal`, or `data/*.db-shm`.** Those are live SQLite handles. `restart.bat` cleans them up safely after killing all writers; nothing else should touch them.

4. **Don't run one-shot Python that imports `emptyos.kernel.*`.** Importing the kernel opens a syslog SQLite connection. Even a quick `python -c` opens a WAL handle, and a kill mid-import can leave a lock. Safe alternatives:
   - `python -m pytest tests/...` (pytest fixtures don't boot the kernel)
   - `python -c "from emptyos.sdk.foo import ..."` (SDK helpers — pure modules, no kernel boot)
   - `python -c "from engines... import ..."` (engines never touch the kernel)
   - HTTP probes against the running daemon (`curl http://localhost:9000/...`)

   When in doubt, ask "does this import boot the kernel?" If yes, run it through the daemon's API instead.

5. **If the daemon is unreachable, surface the diagnosis and stop.** Don't spawn a new one, don't kill stragglers, don't poll in tight loops. Tell the user the symptom (no listener on :9000, stale PIDs, syslog lock error from `data/daemon.err.log`) and let them act.

## Why these rules exist

- Daemons launched from a Claude tool inherit Claude's process tree. When the tool returns, Windows reaps the children — the daemon vanishes silently.
- SQLite WAL is OS-handle-based, not file-based. Killing a writer leaves a dangling handle until the OS releases it (often seconds later). Deleting WAL/SHM during that window corrupts the next write.
- Multiple Claude sessions sometimes run in parallel; if each one tries to "fix" the daemon, they fight.
- The user has a working `restart.bat` + tray icon + `/eos-session-resume` flow. Claude's job is to evolve EmptyOS, not to babysit its runtime.

## What's allowed

- `curl http://localhost:9000/...` — probe live state.
- Reading `data/daemon.log`, `data/daemon.err.log`, `data/eos-stdout.log`, `data/eos-stderr.log` — surface failures.
- Inspecting processes (`Get-NetTCPConnection`, `Get-Process`) — diagnose, don't act.
- `python -m pytest tests/...` — runs against the daemon over HTTP, doesn't compete with it.
- Editing source files — the daemon hot-reloads HTML; Python changes need the user to restart.

## When the daemon is needed for verification

Most code changes can be verified offline:
- Engine logic → `python -m pytest engines/...`
- SDK helpers → `python -m pytest tests/test_sdk_*.py`
- App logic exercised through HTTP → ask the user to restart, then run `python -m pytest tests/test_sys_<app>.py`

Only ask for a daemon restart when there's no offline path. Bundle multiple changes per restart cycle.
