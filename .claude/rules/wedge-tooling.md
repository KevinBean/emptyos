---
paths:
  - "scripts/daemon_watchdog.py"
  - "tests/test_unit_daemon_watchdog.py"
  - "emptyos/proactor_guard.py"
  - "emptyos/headless.py"
  - ".claude/skills/eos-wedge-postmortem/**"
  - "restart.bat"
---

# Wedge Tooling — watchdog, evidence snapshots, restart storms

Split out of `debugging.md` (2026-09-25). Read before diagnosing a daemon
that stopped answering.


- **`scripts/daemon_watchdog.py`** — run in a side terminal (esp. after a
  prior hang). Polls `/api/health`; on 2 misses snapshots
  `data/wedge-evidence/<ts>/` with netstat, tasklist, py-spy per-thread stacks,
  log + syslog tails. **Evidence-only by default** (manual diagnostic runs never
  touch the daemon); pass `--restart` to make it a crash/wedge **supervisor** —
  after capturing evidence it targeted-kills the wedged PID's tree (`taskkill /F
  /T /PID`, never blanket `python.exe`) + respawns the daemon detached, with a
  storm guard (`--max-restarts` / `--restart-window`, default 3 / 30 min, then it
  gives up + alerts). `restart.bat` launches it with `--restart`, so :9000
  self-heals in normal operation. This stays within `daemon-handling.md` because
  the watchdog is a **user-owned** process (launched by restart.bat) — Claude
  itself still never restarts/kills :9000. `pip install py-spy` unlocks full
  stacks. `--port 9001/9002+` for sidecar/sandbox. (`reference_daemon_watchdog`)
- **Recovery can decline, and two cases make it.** A respawn is skipped when
  `:PORT` is still held (the old daemon is alive; a second one stacks rather than
  helps) and when system commit is at `--max-commit-pct` (95). The removed
  "respawning anyway" branch was the last line written to disk before the box
  died on 2026-08-15, adding a 1.1 GB daemon while commit was already 11 GB past
  physical RAM.
- **The machine can be the wedge.** A per-cycle console-host census
  (`--no-storm-watch` to disable) alarms above 300 against a ~36 baseline, and
  captures the live parent breakdown to `data/wedge-evidence/<ts>-console-storm/`;
  `summary.json` now carries `console` + `memory`. Twice a console storm — not a
  daemon bug — hard-froze the box, and both times the daemon's own stack named a
  real, irrelevant frame. When commit is exhausted every disk-touching frame
  looks guilty; the walk for that shape is the `eos-wedge-postmortem` skill
  (Step 0's fifth shape → Step 5b), not this catalog.
- **A restart storm is not a wedge — read the boot times first.** A dead port has
  two causes (crashed vs still booting) and the supervisor distinguishes them by
  the PID it spawned, waiting while that process is alive (`--restart-grace` 240s,
  extended to `--boot-timeout` 900s). Before blaming a slow app, check whether
  several daemons were booting at once: `slow load` warnings in syslog and
  `respawned detached pid=` lines in `data/daemon-restart.log` that outpace
  `RECOVERED`. Stacked boots starve each other, so the import times they report
  are a *symptom* of the storm, not its cause — on 2026-07-30 a steady 28s boot
  read as 1006s across four daemons. Fixed 2026-07-30; the A/B repro is
  `boot_still_running` / `pids_to_clear` in `tests/test_unit_daemon_watchdog.py`.
- **A watchdog "recovery" is not evidence of a wedge — read `seconds_wedged` first.**
  `summary.json` in each `data/wedge-evidence/<ts>/` carries `seconds_wedged`,
  `alive_but_not_listening`, and a netstat + py-spy dump. Measured 2026-09-09
  over **244** snapshots on homepc: median `seconds_wedged` **35.0s**, **235
  (96%) at or under the two-poll detection floor**, `alive_but_not_listening`
  false in 237, netstat showing `:9000` **LISTENING on the very PID that was
  then killed**, and py-spy showing the main thread **idle in `select`**. A
  duration distribution pinned at the detection floor means the *threshold* set
  the number, not the fault — a real wedge does not heal, so it would grow and
  vary. The genuine shape is the 7 `alive_but_not_listening` cases (process up,
  port unheld). Fixed by raising `--probe-timeout` 5s → 15s and gating the KILL
  (not the evidence capture) behind `--recover-after` 120s of *sustained*
  failure — which deliberately does **not** apply to a dead or
  alive-but-not-listening daemon, since delaying those would break the case the
  watchdog exists for. `--recover-after 0` restores the old behaviour.
  Same family as the idle-loop rule above: **an idle loop means the hang is in
  the client**, and here the client was the watchdog's own 5s probe against a
  box sitting at 75–94% commit.
- **Reproduce on a sandbox member, never on `:9000`.** Lease a pool member
  (`.claude/rules/sandbox-driven-testing.md`), reproduce + fix there. Never
  `taskkill` / `restart.bat` the user's daemon (`.claude/rules/daemon-handling.md`).

