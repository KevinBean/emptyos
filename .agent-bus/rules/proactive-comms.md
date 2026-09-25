---
paths:
  - "emptyos/sdk/proactive.py"
  - "apps/public/standard/proactive/**"
  - "plugins/notifications/**"
  - "plugins/telegram/**"
  - "plugins/health/**"
  - "apps/**/reminders/**"
  - "apps/**/bookme/**"
  - "apps/**/promote/**"
  - "apps/**/reactor/**"
  - "tests/*proactive*"
---

# Proactive Comms — every nudge to the user goes through the gate

When EmptyOS speaks first — a reminder, a deadline, a stuck agent session, a
budget overrun — it goes through one gate that decides whether to interrupt
the user **now**, and on which channel. The gate exists for restraint: a
system that over-talks trains the user to ignore it, which is the same failure
as a noisy audit.

**Gate:** `emptyos/sdk/proactive.py` (pure: policy, state, `decide`,
`record_sent`, audit log). **Sender API:** `BaseApp.proactive_notify` and
`BaseApp.proactive_notify_or_raw` (`emptyos/sdk/base_app.py`). **Owner app:**
`apps/public/standard/proactive/` (policy page, the `KINDS` catalog, the
15-minute scanner). **Store:** `data/proactive/{policy.json,state.json,log.jsonl}`.

## Scope

A proactive nudge goes **to the user** — reversible and internal, so it
auto-runs once the user enables the gate. Anything **outbound to a third
party** is a different verb (`send` capability, permanently human-gated) and
never routes through here. In-page feedback for something the user just did
(a toast after a click) is not a nudge either.

## The gate, in the order it decides

`decide()` returns the first reason that applies, so the audit log names
exactly which check held a nudge.

| # | Reason | Holds when | Bypassed by `urgency="critical"`? |
|---|---|---|---|
| 1 | `disabled` | policy `enabled` is false — the shipped default | no |
| 2 | `muted:<kind>` | the kind is muted | no |
| 3 | `dup:<key>` | the same `dedup_key` delivered in the last 24 h | no |
| 4 | `quiet-hours` | inside `quiet_start`–`quiet_end` (22:00–07:00 default) | yes |
| 5 | `daily-cap:<kind>` / `daily-cap` | per-kind cap, then the global `daily_cap` (8) | yes |

**`own_budget` — a separate allowance, not an exemption.** A kind configured
`{"own_budget": true}` is metered on its own counter: rows 5 and 6 above stop
consulting the **global** `daily_cap` / `min_gap_sec`, and its deliveries stop
spending them, so a file the user asked for is not refused because eight
reminders already used the day. Everything else still bites — `disabled`, mute,
dedup and quiet hours — and it is still capped: by its own `daily_cap` /
`min_gap_sec` when it declares them, otherwise **by the global numbers used as
its own allowance**, so `own_budget` alone can never mean unlimited. The flag is
read once per send (`has_own_budget`) and passed to `record_sent`; `decide` and
`record_sent` disagreeing is what would let a kind skip the global cap while
still spending it. Set it through `POST /proactive/api/policy` with a `kinds`
map (merged per kind, so it does not clear existing mutes). Today: `file`.
| 6 | `gap:<kind>` / `gap` | per-kind gap, then the global `min_gap_sec` (1800) | yes |

Only `urgency="critical"` bypasses 4–6, and only while the policy keeps
`critical_bypasses_quiet` true. **`"high"` is treated exactly like
`"normal"`** — a caller passing it gets no priority over the cap or the gap.
Dedup and mute are never bypassed.

A delivered nudge updates the shared state (last-sent, day counters, dedup
key) — except a critical nudge while `critical_bypasses_quiet` is on, which
records only its dedup key (`bypasses_budget` in `emptyos/sdk/proactive.py`).
It never ages the gap or counts toward the cap, even when neither would have
held it, so the minimum gap separates ordinary nudges only. The check-and-record in `proactive_notify` holds
`write_lock("proactive-dispatch")`, but that lock is **per app instance**, so
it does not stop two apps. What makes the section safe today is that nothing
inside it awaits: load, `decide`, `record_sent` and save are all synchronous,
so on the one event loop they run without interleaving. **Keep it that way** —
an `await` added inside that block lets two apps pass the gap together (the
async-atomicity trap in `.claude/rules/dev-gotchas.md`).

## Channels

| Channel | Where it lands | Default? |
|---|---|---|
| `voice` | `proactive:announce` → the hands-free overlay's voice queue; heard only while the overlay is open | yes |
| `notify` | the `notifications` service — the vault inbox, plus Telegram when a bot token and chat id are set (its own `[plugins.notifications]`, else the `telegram` plugin's — the fallback is skipped on a sandbox, dogfood, lab-host or demo daemon, so one texts only if it has its own `[plugins.notifications]` chat id). The Telegram send is not reported back: a rejected message is only logged, and the nudge still counts as delivered | yes |
| `companion` | `proactive:companion` → the page-assistant rail (`page-assistant.js`), which subscribes only when `feature.companion-proactive.enabled` is on; while the rail is closed the nudge waits behind a badge | opt-in |

Pass `channels=[...]` to override; unknown names are dropped.

## Sending one

```python
result = await self.proactive_notify(
    "deadline",                                  # a kind listed in KINDS
    "Due tomorrow: submit the report",
    dedup_key=f"deadline:{task_id}:{due}",      # names this one fire
    urgency="normal",
    channels=None,                               # None → policy defaults
    link={"text": "Open", "href": "/task/"},
)
# → {"delivered": bool, "reason": "ok" | "<gate reason>", "channels": [...]}
```

Three things every sender owes:

1. **A kind in `KINDS`** (`apps/public/standard/proactive/app.py`). The
   policy page renders that catalog and `POST /proactive/api/mute` refuses a
   kind not in it, so an invented kind never appears where the user mutes
   things — only a hand-written `POST /proactive/api/policy` could reach it.
2. **A `dedup_key` naming the fire**, stable across retries of the same fire
   and different for the next one (`kind:YYYY-MM-DD` for once a day,
   `kind:<entity>:<moment>` for an entity). Without one the gate cannot tell a
   retry from a repeat.
3. **A decision about the verdict.** Read `delivered` / `reason`; see below.

### `proactive_notify` or `proactive_notify_or_raw`?

- **`proactive_notify`** — the default for anything new. A `disabled` verdict
  is a legitimate no-op: nothing delivered before the gate existed either.
- **`proactive_notify_or_raw`** — only for a pusher that already delivered
  **before** the gate existed. On `disabled` it sends through the raw
  `notifications` path so users who relied on it aren't silently cut off. A
  real hold (quiet hours, cap, gap, mute, dup) is still honoured. Current
  users: task, billing, people, publish, reactor, reminders, bookme, promote,
  pattern-harvester, dogfood-agent.

## If you record "sent", record it after delivery

A sender that marks its own record (`fired_at`, `notified_at`,
`status="sent"`) must do it **after** the gate delivers, never before. Held is
a normal verdict here, not an error, so marking first records a send that did
not happen. This is the general rule in `.claude/rules/proposed-action.md`
(render-shaped). It cost 10 reminders on 2026-09-14, Sydney time: the gate
held 9 for quiet hours and 1 for the gap, after each had already been stamped
fired.

Decide with `counts_as_sent(result)` (`emptyos/sdk/proactive.py`) rather than
reading the fields yourself. It is true for `delivered`, for `dup:` (an
earlier attempt with this key was delivered), and for `disabled` only when
`_or_raw` reports `raw_sent: True` — the raw fallback sends only if the
`notifications` service exists, and the verdict is `disabled` either way.
Anything else — a hold, or the call raising — leaves the record unsent.
Consumers: reminders (`fired_at`, which drives its retry) and promote
(`tracker["pinged"]`, a record only — nothing re-sends a notice left unpinged).

**A held sender that retries must back off.** Every held attempt appends a
line to `log.jsonl`, and that log is the surface the user reads — a
per-minute retry buries it. The reference is the reminders app's
`check_and_fire`: read candidates, send, then reload and stamp only what was
delivered; on a hold stop the tick (quiet hours, cap and gap hold everything
alike) and wait 5 min, doubling to 30. A reminder that never retries doesn't
need this.

## Push or pull

- **Push** — call `proactive_notify` from your own handler when the moment
  arrives (a reminder falls due, an agent goes idle).
- **Pull** — declare a source, and the proactive app polls it every 15 minutes
  while the gate is enabled:

  ```toml
  [[contributes.proactive.source]]
  id = "library-srs-review"
  method = "proactive_source_reviews"   # async, returns list[dict]
  ```

  Each candidate is `{kind, text, urgency?, dedup_key?, channels?, link?}`,
  and **`kind` and `text` are required**. A source that raises is skipped on
  its own (`call_contributions` catches per contributor), but a returned
  candidate missing either key raises `KeyError` in the unguarded `_deliver`
  and stops delivery of every candidate after it in that scan. Built-in
  sources run first in wellbeing-lens order, so emotional and financial
  nudges get the daily cap before occupational ones. Consumers:
  `library` (spaced-repetition reviews), `devboard` (fix-queue backlog).

Use pull when the condition is a state you can check ("reviews are due"); use
push when it is an event you observe.

## Known raw senders (bypass the gate)

Only these still send through the `notifications` service directly, so mute,
quiet hours and the cap don't apply:

| Sender | Why |
|---|---|
| `runbook` interactive runs | **deliberate** — the user clicked Apply seconds ago; scheduled runs use the gate |
| `plugins/health` unfixed-problem warning | a plugin has no `BaseApp` helper; moving the gate into the kernel is a separate change. `connector_down` alerts pass `telegram=False` and stay in the vault inbox |

**A plugin gates by calling the SDK directly.** `plugins/telegram`'s file sends
(`send_photo`/`send_video`/`send_document`, kind `file`) are *not* on the list
above: a plugin has no `BaseApp`, so it loads the policy and calls
`proactive.decide` / `record_sent` itself (`_proactive_hold` / `_proactive_record`).
Two properties that keep that honest — delivery is recorded only **after**
Telegram accepts (§ "If you record sent, record it after delivery"), and the
dedup key is the file's path plus its mtime, so re-sending an unchanged clip is
a duplicate while a re-rendered one is not. The reason a file needs the gate at
all is volume: one app looping over 40 generated clips would otherwise push 40
files at 03:00. `plugins/health` remains raw because its warning path has no
such loop and no gate call yet.

Everything else was migrated on 2026-09-15. bookme, promote (weekly and
distribution), pattern-harvester and dogfood-agent call `_or_raw`. Reactor's
direct Telegram sends are gone: a delivered nudge reaches the phone through the
notifications service, and a held one no longer does. Its `reminders:fired`
handler no longer adds a second nudge.

**Nothing retries a held one-off notice** — only a sender with its own retry
loop (reminders) gets a second chance. So the notices that must not wait send
with `urgency="critical"`: a new booking (bookme) and an agent modifying itself
(reactor, under its own `self-modification` kind so muting `system` never
silences it). A critical nudge records only its dedup key, so it cannot hold the
reminders behind it. Nothing caps critical nudges
themselves: bookings arrive through a public route, so the phone gets one per
booking, bounded only by the free slots. Failed test runs share one dedup key
per day, so a bad morning cannot spend the daily cap either.

`tests/test_unit_proactive_raw_senders.py` fails when a direct send appears
outside this table, or when the number of sends in an exempt file changes. The
gate itself (`emptyos/sdk/base_app.py`) and the notifications service are
exempt too. To look by hand:

```bash
grep -rnE "(service|get_optional|require|services\.get)\(\s*[\"'](notifications|telegram)[\"']\s*[,)]|\b_telegram\(|\b(notif|notifier|tg)\.send\(" --include=*.py apps plugins emptyos
```

Ignore the matches in `emptyos/sdk/base_app.py` (the gate's own delivery) and
in `plugins/notifications/` (the service itself).

## Tests

`tests/test_unit_proactive.py` (the gate), `tests/test_sys_proactive.py` (the
app), `tests/test_unit_devboard_proactive_source.py` (a pull source),
`tests/personal/test_unit_reminders_firing.py` (a retrying push sender),
`tests/test_unit_proactive_raw_senders.py` (`counts_as_sent`, no direct sends
outside the table, migrated kinds, no duplicate reminder nudge).

## Cross-references

- `.claude/rules/proposed-action.md` — render-shaped actions: mark sent after
  delivery when a gate can hold the send.
- `.claude/rules/autopilot-grants.md` — why a nudge to the user auto-runs but
  outbound sends stay human-gated.
- `.claude/rules/hub-panels.md` — the other ambient surface; a hub tile waits
  to be looked at, a nudge interrupts.
