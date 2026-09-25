# Debugging Rule — root cause before fix, with the EmptyOS wedge catalog

The default failure mode under time pressure is **patching the symptom before
understanding the cause** — proposing a fix, watching it not work, proposing
another, and accreting changes that each make the next bug harder to see. This
rule is the discipline against that. Generic 4-phase spine (adapted from the
Superpowers `systematic-debugging` skill) on top, EmptyOS-specific failure
catalog underneath — because most EmptyOS "the daemon is stuck for no reason"
incidents are the *same four async bugs*, and knowing the catalog collapses
Phase 1 from an hour to a minute.

## The one hard rule

**No fix without a root cause first.** A change that you can't explain *why*
it fixes the bug is a guess. Guesses that happen to work hide the real cause
and resurface later. This is the action-layer version of
`feedback_fix_logic_not_processes` (memory): on "why does this keep happening",
find the code path that produces the behaviour and change it at the source —
never kill a process, delete a schedule, or restart a daemon to paper over it.

## Four phases

**1 — Investigate.** Read the actual error + stack trace. Reproduce it
consistently (a bug you can't reproduce, you can't verify fixed). Read the
*past* before touching anything: `git log -n 5 -- <path>` + grep MEMORY for
the file's slug (`.claude/rules/time-dimension.md`). For a multi-stage flow
(event chain, capability middleware, pipeline), add instrumentation at each
**boundary** to find *where* it fails before asking *why*.

**2 — Pattern-match.** Find working code that does the same shape. List every
difference between the working and broken paths. The bug is almost always in
that diff. (For EmptyOS async wedges, skip straight to the catalog below —
it *is* the pattern library.)

**3 — Single hypothesis.** Form one specific, falsifiable hypothesis about the
root cause. Make the **smallest possible change** to test it. If it's wrong,
form a *new* hypothesis — do not add a second fix on top of the first.

**4 — Implement.** Write the failing test first (it pins the bug + proves the
fix; `.claude/rules/testing.md`). Apply one fix at the root cause. Verify the
test goes green and the repro is gone.

## The 3-attempt stop rule

After **3 failed fix attempts on the same bug, stop patching and question the
architecture.** Three misses means the mental model is wrong, not the code —
keep going and you're debugging your own patches. Step back, re-run Phase 1
with the assumption that the bug is structural. (This is the hand-debugging
mirror of the drain loop's per-attempt budget, `project_drain_loop_v2`.)

## EmptyOS async-wedge catalog — check these FIRST

Symptom is nearly always the same: **process alive + listening, but HTTP stops
answering / a pipeline freezes mid-stage / no syslog error, no `blocked_reason`,
no crash.** `EventBus.emit()` runs handlers *serially with `await` in the same
task* (`emptyos/kernel/event_bus.py`), so any blocking handler pins the whole
bus. Shapes five and six are the mirror image — work that was detached when it
should not have been, or not detached when it should have been. The seventh is
not a bus problem at all: it is the socket layer failing to finish its own
teardown, and it is the one shape that announces itself in the console. The
seven shapes:

| Shape | Tell | Fix |
|---|---|---|
| **Serial fan-out on dead peers** | Daemon freezes after PC sleep/lock; `Get-NetTCPConnection -LocalPort 9000` shows `CLOSE_WAIT` pile-up | Schedule the broadcast with `asyncio.create_task` at the bus boundary + per-send `asyncio.wait_for(..., timeout=2.0)` + `gather`. (`feedback_realtime_broadcast_serial_blocks_bus`) |
| **Lock-held emit deadlock** | Pipeline stuck `stage: X, status: running` forever, no new subprocess, no error | Any `await self.emit(...)` inside `async with self._lock_for(...)` → `self.spawn_background(self.emit(...))`. `asyncio.Lock` isn't reentrant. (A bare `create_task` detaches but keeps no strong ref — see the detached-task row.) (`feedback_lock_held_emit_deadlock`) |
| **Sync call in async context** | Daemon goes silent N seconds after a trigger; py-spy names an ONNX/subprocess/IO frame on the main thread | Wrap the blocking call in `await asyncio.to_thread(fn, ...)`. Applies to middleware `apply()`, plugin service methods, route handlers. (`feedback_middleware_sync_blocks_loop`) |
| **Long handler in HTTP request** | HTTP 500 "No response returned" at ~30s, handler keeps running, no syslog entry | Fire-and-forget the entry-point emit: `self.spawn_background(self.emit(...))` then `return`. Don't `await` a multi-second chain in a route. (`feedback_long_handler_in_http_request`) |
| **Detached task collected mid-flight** | Work that "sometimes doesn't happen" — a warm-up that ran yesterday and not today, no error either way. Worse under memory pressure, i.e. exactly when a boot is already struggling | asyncio only *weak*-refs a running task, so a bare `asyncio.create_task(x)` whose result is discarded can be GC'd before it finishes. Use `self.spawn_background(coro, label=…)` (apps + plugins) — it keeps the ref, cancels on teardown, and logs failures. Guarded by `scripts/check_bare_create_task.py`. |
| **Awaited warm-up in `setup()`** | One app dominates boot; `slow load '<x>': setup=NNNNms` in syslog | A cache prime / index build nobody awaits does not belong inline — it blocks the whole app loader. `spawn_background` it. garden was 2.5s median / 31.7s worst this way; the *embedder* behind it was worse (see below). |
| **Peer reset skips socket teardown** (Windows) | Console prints `Exception in callback _ProactorBasePipeTransport._call_connection_lost()` + `[WinError 10054]`; later the daemon won't exit — `taskkill` times out and the port stays held | CPython calls `sock.shutdown()` as the **first statement of a bare `finally`**, so a peer reset skips `sock.close()`, `server._detach(self)` and the completion flag: the socket lingers until GC and the server's active-transport count never drops, which is what hangs `Server.wait_closed()`. `install_proactor_reset_guard()` (`emptyos/proactor_guard.py`) finishes the teardown; installed at daemon boot + in `scripts/eos-agent.py serve`. **The traceback is the visible half, not the damage** — treating it as log noise leaves the leak. |

Before writing any `await self.emit(...)`: does the current task hold a lock?
will the target handler re-acquire it? does it need to finish before return?
Any "yes" → `spawn_background` it (or it's a design smell — use a return value).

**A constructor can be the blocking call.** `Embedder.__init__` did a
`json.loads` of a 265 MB cache — 2.4s on the event loop and 0.39 GB resident,
paid *per app instance*, and `available` (the common call, and the only one
journal's `setup()` made) never reads a single cached vector. So the tell was
"every app is slow to load", not "one function is slow". When a boot is
uniformly slow, look at what every app constructs, not at what any one app does.

## Tooling for a wedge

Watchdog, evidence snapshots, restart storms, console storms and reading
`seconds_wedged` → `.claude/rules/wedge-tooling.md`. **Reproduce on a sandbox
member, never on `:9000`** (`.claude/rules/sandbox-driven-testing.md`).

## When NOT to apply the full ceremony

- Typo / obvious one-liner with a clear error message and an obvious fix —
  just fix it. The 4 phases are for bugs you *don't* immediately understand.
- A test already pins the behaviour and it's red — the failing test IS Phase 1.
- The bug is in a third-party dep you can't change — investigate enough to
  work around it, then stop; root-causing someone else's code is out of scope.

## Cross-references

- `.claude/rules/time-dimension.md` — read the past (git log + memory) before acting; Phase 1.
- `.claude/rules/testing.md` — failing-test-first; Phase 4 + the four test layers.
- `.claude/rules/daemon-handling.md` — never kill/restart `:9000`/`:9001`.
- `.claude/rules/sandbox-driven-testing.md` — reproduce wedges on a leased member.
- `.claude/rules/dev-gotchas.md` — non-architectural surprises (Windows/encoding/integration).
