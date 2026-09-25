---
name: eos-wedge-postmortem
description: Diagnose why the EmptyOS daemon went down — classify the failure shape (wedge vs death vs restart storm vs stacked boot vs machine exhaustion), read the evidence bundle, and eliminate external causes before blaming code. Covers whole-machine failures where the daemon is the victim, not the cause — console-host storms and runaway memory leaks that hard-froze this box three times. Probe-only; never restarts :9000. Use when the user says "why did emptyos die", "the daemon is stuck", "emptyos keeps restarting", ":9000 is down", "check the wedge evidence", "the PC froze", "terminal windows keep popping up", or after a wedge-alert.flag / Telegram wedge alert / a CONSOLE STORM line in the watchdog log. NOT for reproducing a known bug (use eos-bug-audit), fixing async-loop bug classes you have already identified (the catalog is .claude/rules/debugging.md), or environment/dep problems (use env-check).
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

Five failure shapes look identical from a browser, and they have opposite fixes.
Name the shape before reading a single stack trace.

| Shape | Tell | Means |
|---|---|---|
| **Wedge** | listener alive, `/api/health` times out | event loop blocked → debugging.md catalog |
| **Death** | no listener, no daemon process | crashed or was killed → Steps 3-5 |
| **Restart storm** | `respawned detached pid=` lines outpacing `RECOVERED` in `data/daemon-restart.log` | supervisor fighting a slow boot |
| **Stacked boot** | several `python -m emptyos start` alive, none listening | storm's aftermath — boots starving each other |
| **Machine exhaustion** | system commit near its limit; often thousands of `conhost`/`OpenConsole`; the whole desktop is slow, not just EmptyOS | **the daemon is a victim, not the cause** → Step 5b |

```bash
curl -s -m 5 -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:9000/api/health
netstat -ano | grep -E "LISTENING" | grep ":9000"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*emptyos start*' } | Select-Object ProcessId,CreationDate"
tail -20 data/daemon-restart.log
# machine-wide, always worth 2 seconds before blaming the daemon:
powershell -NoProfile -Command "\$m=Get-CimInstance Win32_OperatingSystem; '{0:N0} MB committed of {1:N0} MB limit' -f ((\$m.TotalVirtualMemorySize-\$m.FreeVirtualMemory)/1KB),(\$m.TotalVirtualMemorySize/1KB); 'console hosts: ' + (Get-Process conhost,OpenConsole -EA SilentlyContinue).Count"
```

**Check the machine before the daemon.** Three of this box's incidents were the
machine running out of commit, and in all three the daemon's own stack looked
guilty. 2026-08-15 is the cautionary one: py-spy named `list_pending` doing sync
`read_text` on the loop — a *genuine* member of the debugging.md catalog, and
entirely beside the point. Everything was slow because 1818 console hosts held
21 GB. Fixing that frame would have been a real fix to the wrong problem.

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
- `summary.json` `console` / `console_storm` / `memory` — **read these first**, they
  answer Step 0's fifth shape in one line. Bundles before `e40ffa8fb` (2026-08-16)
  lack them, which is why both console storms had to be reconstructed by counting
  `conhost` rows in `tasklist.txt` by hand. A sibling `<ts>-console-storm/`
  directory means the storm watch tripped and captured parentage.

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
# 3. commit-limit kill — PRINT THE MESSAGE, it names the culprit with PIDs + bytes
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Resource-Exhaustion-Detector'; StartTime=(Get-Date).AddDays(-30)} |
  Sort-Object TimeCreated | ForEach-Object { "$($_.TimeCreated)  $($_.Message)" }
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

## Step 5b — Machine exhaustion: attribute it before reading any daemon stack

Reached when Step 0 showed commit near the limit, or thousands of console hosts,
or the Step 5 detector named a consumer. **Do not proceed to Step 6 first** — a
stack taken while the box is thrashing names whichever frame happened to be
doing I/O, and that will be a plausible, real, irrelevant bug.

Three incidents on this box, two distinct mechanisms:

| Date | Mechanism | Peak | Outcome |
|---|---|---|---|
| 2026-08-01 | console-host storm | 4307 hosts, 90 GB commit | daemon wedged ×4, survived |
| 2026-08-13 | one `powershell.exe` leaking ~1 GB/min | 47 → **82 GB** in 37 min | daemon wedged ×4, crash dumps |
| 2026-08-15 | console-host storm | 1818 hosts, ~22 GB in hosts alone | **machine hard-froze** |

**Console storm — attribute it while the children are alive:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\find_console_spawner.ps1 -Minutes 60
```

LIVE half needs no elevation and prints its own verdict; HISTORY half reads
Event 4688 and needs an elevated shell (`scripts\enable_process_auditing.ps1`
turns 4688 on — **not retroactive**). The watchdog also auto-captures the live
parent breakdown to `data/wedge-evidence/<ts>-console-storm/` the moment its
storm watch trips.

Measured baselines, so the numbers mean something:

- **Healthy: 2055 console-host creations/hour (0.57/sec), only ~36 alive** —
  they exit in milliseconds. Parentage is spread across ~26 parents.
- **Storm: 6.6/sec (12×) *and they stop exiting*** — 1818 alive at once.
- A storm needs **both halves**. A rate rise alone does not pile up, so
  "what spawns them" and "what stops them draining" are two questions.
- Healthy shape is *many parents, few children each*; a storm is one parent
  owning nearly all of them. That contrast is the whole diagnosis.

**Leak — the detector names it for you.** Step 5's Resource-Exhaustion-Detector
message carries the top three consumers with PIDs and byte counts, and repeats
every ~5 min, so consecutive events give a growth *rate*: that is how 2026-08-13
resolved to a single PowerShell going 47.4 → 81.9 GB. Print the Message; do not
just test whether the event exists.

**Absence proves nothing here either.** The detector logged nothing for 08-01 or
08-15 — a hard freeze denies Windows the chance to diagnose. Consistent with
Step 1: an empty instrument is not a negative result.

Verdict (2026-09-06, from Event 4688 on the third storm): the spawner was the
**daemon itself, whenever it runs with no console at all** — the watchdog's
`DETACHED_PROCESS` respawn. Such a process gives every console child a fresh
*visible* console; Windows Terminal renders each as a tab. (A `CREATE_NO_WINDOW`
process is different: it has a hidden console its children inherit — measured,
zero new hosts.) 4688 traced 2,960 of the storm's resolved clients to the
respawned daemon's pid; the most frequent client command was `git rev-parse
--abbrev-ref HEAD` ×2,909, the polled `api_status`. Every storm on this box
(three freezes + one caught live) followed a detached respawn by 17, 4, 75 and
3 minutes. Why tabs accumulate rather than drain is inferred (non-zero exits
keep a tab; WT falls behind under a burst). Fixed at the process level by
`emptyos/headless.py` (first line of `eos start`);
`project_console_storm_machine_freeze` in memory has the numbers. If a storm
recurs with the guard installed, the decisive 4688 column is the *grandparent*
(the parent of the git/cmd client), not the client — resolve it, don't stop at
"git.exe".

## Step 6 — If a process is still alive, take the stack

```bash
py-spy dump --pid <pid>          # never needs a restart
```

Then match against the four shapes in `.claude/rules/debugging.md` (serial
fan-out on dead peers, lock-held emit deadlock, sync call in async context, long
handler in an HTTP request). A shutdown that hangs in `_cancel_all_tasks` with a
worker thread still doing filesystem work is a *teardown* hang, not a wedge — the
port is already gone, so the supervisor reports "dead" while the process lives.

### Step 6b — A stack names a frame, not a cause. Price the frame.

**One sample is one instant.** It tells you what was running, never what took
the time — and the odds of landing on any given frame scale with its duration,
so the sample is *evidence about* the answer, not the answer. Before blaming
whatever it named, measure that thing's actual cost two ways:

**First rule out the machine (Step 5b).** When the box is out of commit, *every*
frame that touches disk is slow, so the sample lands on I/O and indicts it. The
frame will look like a textbook catalog hit and the fix will be real — just not
the cause. On 2026-08-15 it named a sync `read_text` over 357 files while 1818
console hosts held 21 GB. Pricing the frame offline is what separates the two,
and here it settled the question outright: **that scan is ~19 ms warm, and the
endpoint answers in 24 ms** — nowhere near the 49-80s observed. Two commits
shipped naming it the cause before anyone measured it (`760f61adb`, corrected in
`c667d0824`). If the frame costs milliseconds on an idle box, the box was the
problem.

Beware the *third* candidate, too: a genuine unrelated defect can sit in the
same window and absorb the blame. The hub really was resolving all 114 panels
per single-panel request — a flat 4.1s on an idle box, fixed in `b511e583c` —
which exhaustion then inflated into the observed number. Two real bugs plus one
machine failure, and only the machine failure explains the magnitude.

```bash
# (a) offline, no daemon — does the suspect work cost what you are attributing?
python -c "import time,pathlib; t=time.perf_counter(); \
  [p.read_text(encoding='utf-8') for p in pathlib.Path('<dir>').glob('*.json')]; \
  print(f'{time.perf_counter()-t:.3f}s')"

# (b) live — the narrowest endpoint that does the same work, nothing else
curl -s -o /dev/null -w "%{time_total}s\n" -H "$AUTH" "http://127.0.0.1:9000/<narrow-route>"
```

If (a) and (b) disagree with the wedge duration by an order of magnitude, the
frame is a passenger, not the driver. Measured 2026-08-16: py-spy caught
`rooms.list_pending` inside `open()` 50s into a wedge, and the scan it was
blamed for costs **19 ms** offline and **24 ms** through its own route.

**Pricing the frame finds real defects that are still not the cause — say which
is which.** That same pass found a genuine one nearby: the hub's single-panel
endpoint resolved *all* contributors and discarded all but one, a flat **4.1s**
on an idle box (fixed in b511e583c, now 0.003s). It is a true bug, it was worth
fixing, and it did **not** wedge the daemon — Step 0's console-host storm did.
Exhaustion inflated that 4.1s to the 49-80s actually observed, which is exactly
why the two got conflated. Two commits shipped naming the wrong cause before the
machine was checked. The order in this skill is the lesson: **machine (Step 0/5b)
→ price the frame (here) → only then name a cause.**

**Bisect a slow aggregate endpoint by timing its parts.** Anything that fans out
(`/hub/api/panels`, digests, dashboards) hides which member is slow:

```bash
for id in $(curl -s -H "$AUTH" "$H/hub/api/panels" | python -c \
    "import json,sys;print('\n'.join(i['id'] for b in json.load(sys.stdin)['blocks'] for i in b['items']))"); do
  printf "%s " "$(curl -s -m 90 -o /dev/null -w '%{time_total}' -H "$AUTH" "$H/hub/api/panel/$id")"; echo "$id"
done | sort -rn | head
```

Two tells worth knowing: **near-zero variance across runs** means a fixed cost
(a timeout, a constant fan-out), not accumulated work; and **two unrelated
members timing identically** means neither figure is about the member you asked
for — the cost is in the wrapper. Note that `python -c` prints CRLF on Windows,
so `tr -d '\r'` an id list before building URLs from it or every request 404s at
0.000s and reads as "everything is instant".

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
- **Summed `tasklist` memory is not commit charge.** The Mem Usage column is
  working set; adding it up gives a number that looks like commit and is not.
  Read commit from `Win32_OperatingSystem` (Step 0) or `summary.json.memory`.
- **A `conhost` count is not a spawn rate.** Console hosts normally exit in
  milliseconds, so ~36 alive is consistent with 2000 created per hour. Alive-count
  answers "are they draining", 4688 answers "how fast are they being made" — the
  storm needs both to be wrong, so never infer one from the other.
- **Windows Terminal is this box's default console host**, so every console
  allocation is a *visible window*. "Terminal windows keep popping up" is a
  user-visible symptom of the Step 0 fifth shape, not a cosmetic complaint.

## Never

Restart, kill, or `taskkill` `:9000` / `:9001`; run `restart.bat` / `stop.bat`;
delete `data/*.db*`. Surface the diagnosis and let the user act. To *test* a fix,
lease a sandbox member (`.claude/rules/sandbox-driven-testing.md`).
