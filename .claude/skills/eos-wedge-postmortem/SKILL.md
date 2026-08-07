---
name: eos-wedge-postmortem
description: Diagnose why the EmptyOS daemon went down — classify the failure shape (wedge vs death vs restart storm vs stacked boot), read the evidence bundle, and eliminate external causes before blaming code. Probe-only; never restarts :9000. Use when the user says "why did emptyos die", "the daemon is stuck", "emptyos keeps restarting", ":9000 is down", "check the wedge evidence", or after a wedge-alert.flag / Telegram wedge alert. NOT for reproducing a known bug (use eos-bug-audit), fixing async-loop bug classes you have already identified (the catalog is .claude/rules/debugging.md), or environment/dep problems (use env-check).
---

# EmptyOS Wedge Post-Mortem

The daemon is down, or keeps going down, and nobody knows why. This is the
read-only investigation that turns that into either a named cause or an honest
elimination list. It never restarts anything — `:9000` and `:9001` are the
user's (`.claude/rules/daemon-handling.md`). You diagnose; they act.

The bug *classes* live in `.claude/rules/debugging.md` (the four async wedges).
This skill is the *procedure* for finding out which one — or whether it is a
wedge at all.

## Prerequisites

- **`pip install py-spy`** — without it Step 6 is impossible and the evidence
  bundles contain no stacks. This is the one dependency that decides whether a
  live wedge is diagnosable at all.
- Run from the repo root (`D:/emptyos`); every path below is relative to it.
- Private mode needs the bearer token from `emptyos.toml [network] auth_token`
  for anything past `/api/health`.
- Nothing here needs the daemon to be *up* — the whole point is that it isn't.
  Every command is read-only; none of them restarts or kills anything.

## Step 0 — Classify the shape FIRST

Four failure shapes look identical from a browser, and they have opposite fixes.
Name the shape before reading a single stack trace.

| Shape | Tell | Means |
|---|---|---|
| **Wedge** | listener alive, `/api/health` times out | event loop blocked → debugging.md catalog |
| **Death** | no listener, no daemon process | crashed or was killed → Steps 3-5 |
| **Restart storm** | `respawned detached pid=` lines outpacing `RECOVERED` in `data/daemon-restart.log` | supervisor fighting a slow boot |
| **Stacked boot** | several `python -m emptyos start` alive, none listening | storm's aftermath — boots starving each other |

```bash
curl -s -m 5 -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:9000/api/health
netstat -ano | grep -E "LISTENING" | grep ":9000"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*emptyos start*' } | Select-Object ProcessId,CreationDate"
tail -20 data/daemon-restart.log
```

**The trap.** A storm makes every boot slow, so the syslog fills with
`slow load 'x': import=NNNNNms` warnings. Those are the storm's **symptom**, not
its cause — chasing the slow app is the wrong hunt. Confirm the shape before
believing the import times. (2026-07-30: a steady 28s boot read as 1006s purely
because four daemons were booting at once.)

## Step 1 — Check the instrument before trusting its silence

Evidence bundles can be empty, and an empty bundle is not evidence of nothing.
Open the newest `data/wedge-evidence/<ts>/` and confirm it actually captured:

- `tasklist.txt` — must have process rows, not `TimeoutExpired`
- `pyspy_dump_<pid>.txt` — must exist (needs `pip install py-spy`)
- `daemon_pidfile.txt`, `summary.json` with `daemon_pids` / `alive_but_not_listening`

If those fields are missing, the machine is on a watchdog older than
`c5cbb171` and the bundle cannot answer the question — **say so** instead of
concluding from absence. (218 bundles from 2026-07-30 carry no process list and
no stacks, because `tasklist /V` timed out and py-spy was keyed off the
listening PID, which was `None` in exactly the case being investigated.)

## Step 2 — Boot-duration table (turns "slow" into a number)

```python
import sqlite3, datetime
c = sqlite3.connect("file:D:/emptyos/data/syslog.db?mode=ro", uri=True, timeout=5)
cur = c.cursor(); f = lambda t: datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")
cur.execute("SELECT ts FROM syslog WHERE message LIKE \"%Loaded plugin 'agent-runtime'%\" ORDER BY id DESC LIMIT 12")
starts = [r[0] for r in cur.fetchall()][::-1]
cur.execute("SELECT ts FROM syslog WHERE message LIKE \"%Loaded engine 'finance'%\" ORDER BY id DESC LIMIT 12")
ends = sorted(r[0] for r in cur.fetchall())
for s in starts:
    e = next((x for x in ends if x > s), None)
    print(f"  start {f(s)} -> {f(e)} = {e-s:7.1f}s" if e else f"  start {f(s)} -> NEVER COMPLETED")
```

`agent-runtime` is the first plugin loaded and `finance` the last engine, so the
pair brackets a boot. A healthy boot on this machine is **26-31s**. Anything
past ~90s means contention, not a slow app.

## Step 3 — Read the daemon's OWN stdout (the file people miss)

Where a daemon's output goes depends on who launched it:

| Launcher | Output |
|---|---|
| `restart.bat` | `data/eos-stdout.log`, `data/eos-stderr.log` |
| watchdog respawn | **`data/watchdog-respawn.log`** |
| tray Restart | `data/daemon-restart.log` (interleaved with `[watchdog]` lines) |

`watchdog-respawn.log` is append-only across every respawn ever. Split it on the
`===== respawn <iso-ts>` markers and read the section that *ends* at the death:

```python
import re; from pathlib import Path
txt = Path("data/watchdog-respawn.log").read_text(encoding="utf-8", errors="replace")
marks = [(m.start(), m.group(1)) for m in re.finditer(r"===== respawn ([0-9T:.\-]+)", txt)] + [(len(txt), "EOF")]
for i in range(len(marks)-1):
    body = txt[marks[i][0]:marks[i+1][0]]
    print(f"{marks[i][1]:28} lines={len(body.splitlines()):6} "
          f"stopped={body.count('EmptyOS stopped.')} bind10048={body.count('10048')}")
```

- `EmptyOS stopped.` present → the CLI's normal exit path ran (graceful)
- `10048` → it lost a bind race; it was a *surplus* daemon, not the original
- neither → the process ended abruptly; go to Step 4

## Step 4 — Graceful or abrupt?

Two independent records answer this. `kernel.stop()` emits `kernel:stopping`
**before** any teardown and clears `daemon.pid.json` as its first act, so a stop
that was *attempted* leaves a trace even if it never finished.

```python
import sqlite3
c = sqlite3.connect("file:D:/emptyos/data/events.db?mode=ro", uri=True, timeout=8)
cur = c.cursor()   # NOTE: column is `timestamp` (UTC ISO string), type is `type`
cur.execute("SELECT timestamp,type FROM events WHERE type IN ('kernel:stopping','kernel:stopped') ORDER BY id DESC LIMIT 10")
print(cur.fetchall())
```

| `kernel:stopping` | pidfile | Verdict |
|---|---|---|
| present | cleared | graceful stop path ran (tray Quit/Restart, Settings restart) |
| present | still names a dead PID | stop started, then something killed it mid-teardown |
| absent | cleared | stop began before the marker existed — old build; inconclusive |
| absent | still names a dead PID | **terminated from outside, no cleanup** |

## Step 5 — Eliminate external causes (all four, then stop guessing)

```powershell
# 1. native crash — LocalDumps must be PROVEN ACTIVE before absence means anything
Get-ChildItem "$env:LOCALAPPDATA\CrashDumps" | Select-Object Name,LastWriteTime
# 2. WER / Application Error for python.exe
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddDays(-7)} |
  Where-Object { $_.Message -like '*python*' } | Select-Object -First 10 TimeCreated,Id,ProviderName
# 3. commit-limit kill
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Resource-Exhaustion-Detector'; StartTime=(Get-Date).AddDays(-14)}
# 4. GPU TDR (matters when ComfyUI/MV work is running)
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-7)} | Where-Object { $_.Id -in 4101,4098 }
```

**Only dumps from other programs make an absent python dump meaningful.** If the
CrashDumps folder is empty for everything, LocalDumps is not capturing and you
have learned nothing.

Also rule out the deliberate restart paths — each kills *every* `python.exe`, so
**the watchdog surviving the death rules all three out at once**: `restart.bat`,
the Ctrl+K "Restart Daemon" palette action, and `POST /settings/api/restart-daemon`.
`settings/product.py`'s `os._exit` paths are gated by `_product_enabled()` and are
dead when running from source.

## Step 6 — If a process is still alive, take the stack

```bash
py-spy dump --pid <pid>          # never needs a restart
```

Then match against the four shapes in `.claude/rules/debugging.md` (serial
fan-out on dead peers, lock-held emit deadlock, sync call in async context, long
handler in an HTTP request). A shutdown that hangs in `_cancel_all_tasks` with a
worker thread still doing filesystem work is a *teardown* hang, not a wedge — the
port is already gone, so the supervisor reports "dead" while the process lives.

## Step 7 — Conclude honestly

Write the verdict as either a named cause **with the evidence that proves it**, or
an elimination list plus the one artifact that would decide it next time. Do not
promote the leading hypothesis to a conclusion — this failure has more than one
candidate that leaves identical traces.

Record eliminations to memory (`reference_daemon_watchdog`) so the next run does
not re-derive them; each one costs several minutes of Windows event-log queries.

## Gotchas that cost real time

- **Python cannot reach `localhost`.** `curl http://localhost:9000` works, but
  `urllib`/`http.client` resolve `::1` first while the daemon binds IPv4 — always
  `http://127.0.0.1:9000` from Python.
- **Private mode needs auth.** `Authorization: Bearer <emptyos.toml [network] auth_token>`.
- **Timestamp formats differ per store.** `syslog.db.ts` is a float epoch in local
  time; `events.db.timestamp` is a UTC ISO string with `+00:00`. Filtering
  `events.db` with a local-time string silently returns nothing.
- **`events.db` ids are not time-ordered** when several daemons wrote concurrently.
  Sort by `timestamp`, not `id`.
- **Don't `grep -rn` the repo** — the tree is huge; use the Grep tool (ripgrep).

## Never

Restart, kill, or `taskkill` `:9000` / `:9001`; run `restart.bat` / `stop.bat`;
delete `data/*.db*`. Surface the diagnosis and let the user act. To *test* a fix,
lease a sandbox member (`.claude/rules/sandbox-driven-testing.md`).
