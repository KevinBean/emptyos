# Cardputer Adv Chat firmware

M5Stack **Cardputer Adv** firmware that turns the device into a pocket chat
terminal into one fixed EmptyOS rooms agent. Type a message, hit Enter, get a
reply on the 1.14" screen — no note-capture inbox, no on-device task/journal
modes. This is the typed-text sibling of `firmware/voice_satellite/` (talk)
and closer in spirit to the `plugins/telegram/` two-way bridge: any action
the agent proposes goes through the same server-side `[DO:]` review gate
(Apply/Reject reviewed on your phone/PC), never auto-executed from this
device. Full design rationale: the plan this was built from (daemon-side
setup, HTTP contract, deferred Phase 2) — see `docs/VOICE-SATELLITE.md`
"Still open" for the physical-Approve/Reject gap this device could close later.

```
cardputer_adv_chat/
├── cardputer_adv_chat.ino   — the sketch (tracked; holds NO credentials)
├── secrets.h.example        — credential template (tracked)
├── secrets.h                — your real daemon URL / token (GITIGNORED)
└── README.md
```

## 0. One-time daemon-side setup (before touching the device)

**Already done for this repo's daemon** — the `cardputer` room/agent below
was created and verified live on 2026-08-02 (see the note at the end of this
section). Re-read this if you ever recreate the room or widen its allowlist.

The firmware talks to `POST /rooms/api/chat` against **one fixed room/agent**.
Create it once, from a browser (or the API), before flashing anything:

1. Create a new agent — id **`cardputer`** (or your own choice; you'll enter
   it on the device during provisioning either way).
2. Set **`gate_mode: gate`**. On its own this only forces `gated`/`never`
   verbs through the pending Apply/Reject queue — **it does NOT stop a
   `stable`-eligibility verb from auto-running** if this daemon has
   `[autopilot] auto_stable_default` on (check with `eos autopilot review`,
   or just try it — see step 3.5). `task.add` and `capture.add` (quick-action's
   `add`) are both classified `stable` in the verb registry, so without step
   3.5 they'd auto-execute the instant the agent proposes them, silently
   defeating the whole point of putting a review gate in front of a device
   that has no on-device Apply/Reject button. Verified live: the same
   `task.add` request that showed `status: "pending"` after adding the hold
   below showed `status: "applied"` (auto_reason: `stable-default`) without it.
3. Give it a short, plain-text persona (no markdown — a 240×135 screen can't
   render it) and a `server_actions` allowlist. Start narrow — e.g.
   `task.add`, `capture.add` — and widen deliberately later rather than
   granting everything from the start.
3.5. **For every `stable`-eligibility verb in the allowlist, add a hold** so
   it actually gates for this device, regardless of `auto_stable_default`:
   ```
   eos autopilot hold task.add --actor-type agent --actor-id cardputer \
     --scope room:cardputer --rationale "Cardputer has no on-device Apply/Reject"
   eos autopilot hold quick-action.add --actor-type agent --actor-id cardputer \
     --scope room:cardputer --rationale "Cardputer has no on-device Apply/Reject"
   ```
   A hold only ever *adds* friction (per `.claude/rules/autopilot-grants.md`)
   — it can't widen access, so this is safe to over-apply. Skip this step
   only for verbs you're deliberately fine letting run unreviewed (a `gated`/
   `never` verb never needs one; it's already always gated).
4. **Test it — a few chat turns before flashing** — confirm the reply text
   reads right, and confirm a state-changing request (e.g. "add a task: test")
   comes back with `server_results[0].status == "pending"`, not `"applied"`.
   If it shows `"applied"` with `auto_reason: "stable-default"`, step 3.5 was
   skipped or the verb pattern didn't match — fix before flashing.

No app code changes are needed for any of this — `/rooms/api/chat` already
exists and already returns exactly what the firmware expects. `eos autopilot
review` / the `/rooms/` pending dashboard are how you'd notice a hold went
missing later.

## 1. One-time toolchain setup (Arduino IDE)

1. Install the **Arduino IDE** (2.x).
2. **File → Preferences → Additional boards manager URLs**, add:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
3. **Tools → Board → Boards Manager** → install **esp32 by Espressif**.
4. **Tools → Manage Libraries** → install **M5Unified**, **M5Cardputer**
   (keyboard driver — needs a June-2026-or-later release for Cardputer Adv
   support; installing it also pulls in **IRremote** as a dependency, used
   below), **ArduinoJson** (v7), and **WiFiManager** (by tzapu).

## 2. Configure — on-device, no editing files

Provisioning is **on the device**, not in a file. On first boot (or whenever
it can't reach saved Wi-Fi) it opens a captive-portal Wi-Fi AP named
**`EOS-Cardputer`**:

1. On your phone, join the **`EOS-Cardputer`** Wi-Fi network.
2. The setup page opens automatically (or visit `http://192.168.4.1`).
3. Pick your home Wi-Fi (2.4 GHz), and fill:
   - **Daemon URL** — `http://<daemon-LAN-IP>:9000`
   - **Auth token** — `emptyos.toml [network] auth_token`
   - **Room / agent id** — the room you created in step 0 (e.g. `cardputer`)
   - **Device id** — e.g. `cardputer-01`
4. Save — it persists everything in NVS (flash) and connects. **No
   reflashing** to change any of this later.

**To reconfigure**, hold the **backtick (`` ` ``) key** ~1.5s while powering
on — the portal reopens. secrets.h (if present) only pre-fills the portal
defaults; the `.ino` holds no credentials, so it's always safe to commit.

This firmware reuses the same NVS keys (`daemon`/`token`/`devid`) as
`voice_satellite`/`stick_s3_worklog_capture`, plus a new `room` key — so a
device already set up for one of those keeps its Wi-Fi/daemon/token/id here
too if you're re-flashing the same physical board.

## 3. Flash

1. Connect the Cardputer Adv by **USB-C**.
2. **Tools → Board → esp32 → "M5Cardputer"** (verify this recognizes the Adv
   variant — see the compile-time note in the sketch header if not).
3. **Tools → Port →** select the Cardputer's port.
4. **Upload** (→).
5. **Tools → Serial Monitor @ 115200** to watch Wi-Fi + chat HTTP logs.

## 4. Use it

- **Type, then Enter** to send. The typed message word-wraps across the
  screen as you type (not truncated to one line).
- **Reply screen**: the agent's answer wraps across the screen; press
  **`;`** to scroll up, **`.`** to scroll down through a long reply. Press
  **any other key** to dismiss the reply and start typing a new message.
- **⏳ N pending review** appears in the footer if the agent proposed an
  action.
- **Tab** — shows the oldest pending `[DO:]` action awaiting review (same
  queue as `/rooms/` and the global pending dashboard). **Y** applies it,
  **N** rejects it, **Tab** again (or any other key) dismisses without
  resolving anything. "No pending actions" if the queue is empty.
- **`/ir <name>` then Enter** — fires a saved IR code by name (see
  `IR_CODES` in the sketch — empty until you add real NEC address/command
  pairs for your own devices). Never touches the network; works even with
  no Wi-Fi/daemon.
- **Idle ~30s** → screen sleeps (backlight off). Wakes on any keypress or a
  shake (the Adv's IMU) — either gesture is consumed by waking, not also
  acted on.
- **Hold `` ` `` at boot** to reopen the Wi-Fi/daemon setup portal.
- Self-registers into `/devices/` on boot (category `input`) and
  **re-registers every 60s** (idempotent — also refreshes battery %/Wi-Fi
  RSSI, visible in the daemon's `/devices/` dashboard).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `WiFiManager.h: No such file` at compile | Install **WiFiManager** (by tzapu) in Manage Libraries. |
| `M5Cardputer.h: No such file` / keyboard doesn't compile | Install/update the **M5Cardputer** library — Adv support landed June 2026. |
| Can't find `EOS-Cardputer` Wi-Fi | First boot opens it for a few minutes; power-cycle if missed, or hold `` ` `` at boot to force it. |
| Keys type nothing | Keyboard (TCA8418) not initialized — confirm `M5Cardputer.begin(cfg)` ran, not a bare `M5.begin()`. |
| `AUTH FAILED` | Wrong/missing token, or the daemon isn't in a mode requiring/accepting one. |
| `ROOM MISSING` (HTTP 404) | The configured room/agent id doesn't exist — check it matches what you created in step 0, or that `rooms` is installed. |
| `NO WI-FI` | Not connected — hold `` ` `` at boot to reconfigure. |
| `DAEMON OFFLINE` | Can't reach the daemon URL — check it's reachable on the same Wi-Fi, and Windows Firewall isn't blocking `:9000`. |
| Screen upside-down / mirrored | Add `M5Cardputer.Display.setRotation(1)` (or another value) in `setup()` — default rotation wasn't hardware-verified. |
| Device never shows "online" in `/devices/` | Re-register stalled — check the 60s timer isn't stuck behind a hung HTTP call. |
| Won't compile: `M5Cardputer.Imu`/`M5Cardputer.Power` not found | Sub-object names were guessed by analogy with `.Keyboard`/`.Display` (both confirmed working) — check the installed M5Cardputer/M5Unified headers for the correct accessor if these don't match. |
| Shake doesn't wake the screen | `checkShake()` threshold (`|mag-1.0g| > 0.8`) may need tuning for the Adv's mount/orientation — same shape as `voice_satellite.ino`'s, tune there too if it's over/under-sensitive. |
| `IRremote.hpp: No such file` at compile | Installing the **M5Cardputer** library should have pulled it in automatically as a dependency; install **IRremote** (by Arduino-IRremote) manually if not. |
| `/ir <name>` always says "Unknown code" | `IR_CODES` in the sketch is empty by default — add real `{name, address, command}` entries for your own devices and reflash. |

## Notes

- **No audio.** The Cardputer's ES8311 codec path is a different driver than
  the AtomS3R's Atomic Echo Base (M5EchoBase) and is unverified/deferred —
  see `firmware/voice_satellite/voice_satellite.ino`'s own note on this. This
  firmware is text-only by design, not as a temporary limitation.
- **No on-device capture/task/journal modes.** Earlier design drafts had
  this device log notes for later triage; that was dropped in favor of
  direct chat, since that's how this device is actually meant to be used.
  The existing `quick-action`/`task`/`journal` endpoints are unaffected and
  can still be reached by the agent itself via `[DO:]` if the allowlist
  includes them.
- **Devices framework:** self-registers into the EmptyOS **`/devices`**
  registry (category `input`) on every boot alongside the voice puck and the
  paper dashboard.
