# Paper Dashboard — the e-ink display system design

The system behind `display`-category devices (first board: M5Stack M5Paper Color,
400×600 Spectra-6 color e-ink). The daemon composes + renders the whole panel;
the board fetches a finished PNG, blits it, and deep-sleeps. This doc is the
design contract for turning the current hardcoded proof (`2ea42564`) into a
configurable system.

Companion docs: `docs/VOICE-SATELLITE.md` (the audio sibling),
`firmware/paper_dashboard/README.md` (flashing + provisioning),
`apps/extension/engineering/devices/` (the owning app).

## Principles

1. **Pull-only, server-renders-everything.** The board is a dumb frame: no
   on-device layout, no fonts, no SD card, no push channel. Everything below is
   a consequence of "the panel is asleep 99% of the time."
2. **Hub panels are the content source — no new contribution type.** Apps
   already declare glanceable signals via `[[contributes.hub.panel]]` and
   `panel_*` methods. The paper dashboard consumes the *same* contributions
   through a renderer→text-lines adapter. An app that has a hub panel is
   paper-capable for free; zero apps change.
3. **Config is per-device, in the registry.** Each display device carries a
   `panel` object in `data/apps/devices/registry.json` — sections, size,
   rotation, cadence. No config → today's built-in default payload (backward
   compatible with the flashed skeleton).
4. **Fail-soft everywhere.** A missing app, a broken panel method, an unknown
   renderer → the section is dropped; the clock always renders. Same discipline
   as the hub.
5. **Spectra-6 stays the palette floor.** Section accents map onto
   black/white/red/yellow/blue/green only (`dashboard.SPECTRA6`), so color
   survives the device's quantization.

## Architecture

```
apps with [[contributes.hub.panel]]          devices app
  task.todays-tasks ─┐                  ┌──────────────────────┐
  projects.deadlines ─┤  panel_* data   │ resolve device.panel │
  reminders.upcoming ─┼────────────────►│ → call each source   │      PNG
  weather.hero ───────┤                 │ → adapt to sections  ├────────────► board
  …                  ─┘                 │ → render_dashboard_png│  (poll =
                                        └──────────┬───────────┘   heartbeat)
        built-in sources (clock/date,              │
        devices-online, footer)                    └── X-EOS-Refresh-Min header
                                                       (adaptive sleep hint)
```

## Content system

### Per-device panel config (registry.json, per display device)

```jsonc
"panel": {
  "views": [                              // named layouts; v1 renders views[0]
    { "id": "home",
      "sections": [
        {"source": "hub:task.todays-tasks",          "accent": "blue",  "max_lines": 6},
        {"source": "hub:projects.upcoming-deadlines","accent": "red",   "max_lines": 3},
        {"source": "builtin:devices-online",         "accent": "green"}
      ]}
  ],
  "w": 400, "h": 600, "rotation": 0,
  "refresh_min": 15,                      // base cadence hint
  "quiet_hours": {"from": "23:00", "to": "06:30", "refresh_min": 90},
  "powered": false                        // true = dock mode (fast poll, no deep-sleep)
}
```

### Source resolution

- `hub:<app>.<panel-id>` — looked up in `kernel.apps.get_contributions("hub",
  "panel")` (the same registry the hub reads; the hub app itself is not a
  dependency). The devices app calls the contributor's `panel_*` method
  directly via `call_app`, then adapts the result.
- `builtin:<name>` — sections the devices app composes itself
  (`BUILTIN_SOURCES`): `companion`, `nudges`, `reminders`, `devices-online`
  (+ `clock`/`date`/`footer` implicit, always render). See § Companion presence.

### Renderer → e-ink lines adapter (the load-bearing table)

One pure function in `dashboard.py`: `adapt_panel(renderer, data, limit) ->
list[str] | None`. Canonical hub row keys per `.claude/rules/hub-panels.md`.

| Hub renderer | Line shape | Notes |
|---|---|---|
| `task-list` | `□ text` / `■ text` (+ ` · tag`) | □/■ not ☐/☑ — CJK faces (YaHei) lack U+2610/2611 |
| `stat-tile` / `tiles-row` | `label · value` | single dict or list |
| `plain-list` / `chips` / `deadline-row` / `entity-card` | `title — subtitle` | canonical + legacy alias keys; href dropped (paper) |
| `bar` | `label ▮▮▮▯▯ 63%` | 5-cell glyph bar |
| `countdown-tile` | `title · 12d` | |
| `hero-weather` | `description · 22°C` | empty temp → description only |
| `quote` / `text-card` / `accent-card` | quoted/plain text lines | |
| anything else | `None` → section skipped | fail-soft; one syslog warn |

The adapter is pure (no `self`) so it unit-tests headless next to
`render_dashboard_png` in `tests/test_unit_devices_dashboard.py`. Emoji are
stripped at the render boundary (`_clean_line`) — e-ink faces have no colour
emoji glyphs — and `_font()` probes per-OS CJK-capable faces (YaHei / Noto CJK /
PingFang) before the Latin-only fallbacks, so bilingual task text renders.

## Companion presence

The AI companion is on the panel three ways — all shipped, none needing a dark
flag (activation = putting the section in the panel config):

- **`builtin:companion`** — the hub's one-breath message: heading = the digest
  greeting ("Good evening"), lines = the Next-move `title` + wrapped `why`.
  Backed by `hub.companion_line()` (`apps/public/core/hub/app.py`) — the same
  brain as the home screen: `_build_digest()` + the cached Aura
  `_aura_next_move()` with the deterministic template ladder as fallback when
  the companion is disabled or the model is weak. The hub's signal-signature
  cache paces LLM spend, so a 15-min poll usually hits cache. Section accent
  follows the move's tone (overdue→red, today→blue, calm→green) unless pinned.
- **`builtin:nudges`** — recent *delivered* proactive messages via
  `proactive.recent_delivered(limit)` (wraps the audit log,
  `emptyos/sdk/proactive.py::read_log`). What the companion chose to say,
  as a panel section.
- **`builtin:reminders`** — `reminders.upcoming(days)` mapped to
  `"{due} {text}"` lines (reminders has no hub panel; this is the manual map).

**The proactive→paper wiring** is one connection record (config, not code):

```json
{"source_event": "proactive:delivered", "target_type": "app_verb",
 "target_app": "devices", "target_method": "mark_display_dirty",
 "target_args": {"id": "paper-01"}}
```

After a nudge fires, the display's next poll returns `X-EOS-Refresh-Min: 2`
(flag consumed on that poll), so the panel comes back quickly and shows it,
then decays to base cadence. The same mechanism later wires puck-turn → paper.

**Voice on the board is Phase 2, unchanged** — the mic/speaker path reuses
`/voice-assistant/api/device_turn_stream` exactly as the puck does; gated on
hardware verification of the ES8311 clock quirk (firmware README "Phase 2").

## Reaction loop — see → reach → act → reflect (shipped 2026-07-03)

The panel is the **see** half; the reaction half lives on the phone:

```
SEE      the paper renders composed sections (above)
REACH    a QR bottom-right on the panel → opens the interactive twin on a phone
ACT      the twin (pages/panel.html?id=…) renders the SAME sections structured +
         actionable: tap-complete/snooze tasks and reminders, open the companion
         move's action link; free-form talk/voice via the page-assistant rail
         (auto-mounts; with feature.companion-frame.enabled it IS the companion)
REFLECT  every successful act POSTs /api/displays/{id}/dirty → the paper's next
         poll is fast (~2 min) → the user sees their reaction land on e-ink
TALK v2  BUILT (2026-07-03): HOLD the wake button → record while held → the
         companion answers through the board's speaker (device_turn_stream,
         M5Unified Mic/Speaker) → panel redraws with what the turn changed.
         Hardware-verify pending (possible ES8311 rate quirk).
```

- **`GET /devices/api/panel/{id}/interactive`** — the twin's feed: same section
  specs as the PNG, but rows keep ids and carry `acts` (`{label, method, url,
  body}` — the task read-feed act contract). Per-source resolvers: today's
  tasks via `task.list_all` (+ toggle/snooze acts), reminders (+ complete/
  snooze by id), companion (+ `action_href` button — `hub.companion_line` now
  returns it), nudges (links), everything else read-only lines linking into
  the owning app.
- **QR posture: URL only, never the token.** The phone authenticates once at
  `/login` (30-day `eos_session` cookie). QR target is built from the request
  base URL, so it's LAN/tailnet-correct automatically; per-device panel config
  `"qr": "url" | "off"` (default on). Rendered server-side (`qrcode` dep,
  `[project.optional-dependencies] devices`; fail-soft — no lib, no QR, same
  layout).
- **No firmware change** — the QR is part of the same PNG the board already
  fetches.

## Views + the button

The board's wake button (already a `TODO[board]` GPIO wake) becomes the view
cycler: a button wake fetches `GET /api/dashboard/{id}?view=next`; the daemon
tracks the device's current view index in the registry and rotates through
`panel.views`. A timer wake fetches without `?view=` and gets the current view.
v1 ships with a single view; the `views[]` shape exists from day one so adding
"agenda" / "week" views is config, not code.

## Adaptive cadence

> **INERT on `paper-01` as of 2026-07-12.** The firmware now ships
> `BUTTON_ONLY = true` (`firmware/paper_dashboard/paper_dashboard.ino`): there is
> **no timer wake at all** — the panel sleeps until the top key is pressed. It
> therefore ignores `X-EOS-Refresh-Min`, and everything in this section plus the
> dirty flag below has no effect on that board. The daemon still computes and
> sends the header (harmless). Set `BUTTON_ONLY = false` to re-activate all of it.

The daemon owns pacing; the firmware obeys a hint:

- Every dashboard response carries `X-EOS-Refresh-Min: <int>` computed from the
  device's `refresh_min`, `quiet_hours`, and the dirty flag (below).
- Firmware: read the header, clamp to `[1, 240]`, deep-sleep that long instead of
  the compiled `REFRESH_MIN` (which becomes the fallback when the header is
  absent). **Gated on `BUTTON_ONLY = false`.**
- `powered: true` (dock mode — board on USB) → firmware skips deep-sleep and
  polls every `refresh_min` minutes with Wi-Fi held; the daemon may return
  hints as low as 1.

## Connections bridge — the dirty flag

A sleeping e-ink cannot be pushed to; the honest latency floor is the sleep
period. The bridge is a **dirty flag**, not a push (also inert under
`BUTTON_ONLY` — a button press is the only thing that fetches):

- New verb `devices.mark_display_dirty(id=…)` — registered as a connection
  target (`target_type: app_verb`). Example wiring: puck turn event →
  `mark_display_dirty(paper-01)` ("the paper shows what the puck heard" thread).
- A dirty device's next poll gets a *short* `X-EOS-Refresh-Min` (e.g. 2) until
  the flag clears on the following poll — so after activity, the panel
  temporarily refreshes fast, then decays back to base cadence.
- No WoL, no persistent socket, no on-device queue. If sub-minute latency ever
  matters, that's dock mode.

## Endpoints (delta over what's built)

All shipped (2026-07-02):

| Route | Behaviour |
|---|---|
| `GET /devices/api/dashboard/{id}` | reads `panel` config; `?view=<id\|next>` selects/cycles views; every response carries `X-EOS-Refresh-Min`; poll = heartbeat + consumes the dirty flag; no config → the legacy built-in payload |
| `GET /devices/api/dashboard/{id}/data` | same composition, JSON out + `refresh_min` + `view` — read-only (no heartbeat / rotation / dirty consumption) |
| `PUT /devices/api/devices/{id}/panel` | replace the `panel` object (validated: known sources, Spectra-6 accents, bounds, HH:MM quiet hours) |
| `GET /devices/api/panel-sources` | every referenceable source — hub contributions (title, renderer, adaptable flag) + builtins — feeds the config UI picker |
| `POST /devices/api/displays/{id}/dirty` | HTTP face of the `mark_display_dirty` verb (the connection target) |

## Config UI

Shipped: each `display`-category device card on `/devices/` carries a
**panel** link → `EOS_UI.modal` editor — section list (source picker fed by
`/api/panel-sources`, accent, max-lines, reorder/remove), cadence +
quiet-hours fields, and a live `?preview=1` iframe that re-renders on save.
The editor edits `views[0]`; extra button-cycled views are edited as JSON via
the PUT endpoint until a second view consumer justifies more UI.

## Firmware deltas (`firmware/paper_dashboard/paper_dashboard.ino`)

1. ✅ Read `X-EOS-Refresh-Min` response header → override sleep duration
   (built; inert while `BUTTON_ONLY = true`).
2. ✅ Button-GPIO wake → append `?view=next` to the fetch. Hardware-verified.
3. `POWERED` compile flag (or NVS field) → loop-poll instead of deep-sleep.
4. ✅ Both hardware TODOs closed: board entry **M5PaperColor**, rotation 0.

## Build order

1. ✅ **Panel config + composition** (2026-07-02) — `panel` object, source
   resolution, the adapter, `X-EOS-Refresh-Min`, `PUT …/panel`,
   `GET …/panel-sources`. Unit + system tests; sandbox-verified.
2. ✅ **Config UI** — the panel modal + live preview on `/devices/`.
3. ✅ **Dirty flag + companion builtins** — `mark_display_dirty`,
   `builtin:companion/nudges/reminders`; the `proactive:delivered` →
   dirty connection verified on a sandbox. Wire the same record on `:9000`
   after restart (it's per-deployment data, not code).
4. ✅ **Firmware — HARDWARE-VERIFIED 2026-07-03; button-only refresh 2026-07-12**
   — full cycle live on a real M5PaperColor: Wi-Fi join (raw-first with static-IP
   fallback) → register 200 (+ battery/RSSI) → dashboard 200 → e-ink draw → deep
   sleep. Refresh is now **button-only** (`BUTTON_ONLY = true`): no timer wake,
   top-key press → wake → redraw, verified on hardware. Rotation 0 confirmed.
   Board entry: **M5PaperColor** (M5Stack
   boards package, Huge APP partition). Bring-up gotchas are recorded in the
   sketch header (bootloader latch/watchdog-reset, DTR/RTS serial trap, mesh
   DHCP flakiness, NVS-vs-secrets authority, 40s fetch timeout). Remaining
   `VERIFY[board]`: BtnA press-to-wake polarity.
5. ✅ **Views/button cycling (server side)** — `?view=next` rotation shipped;
   the firmware half lands with lot 4.

Phase 2 (unchanged, deferred): fold the voice puck into this board — the
M5Paper Color carries an ES8311 codec + mic + speaker; verify the ~2× clock
quirk against `voice_satellite.ino` `audioInit` first.

## What NOT to build

- **A `[[contributes.devices.panel]]` slot** — duplicates hub panels; the
  adapter gets the same breadth for free. Revisit only if a source needs
  paper-*specific* data no hub renderer carries.
- **Push/WebSocket to the board** — physically meaningless against deep-sleep;
  the dirty flag + dock mode cover the real latency needs.
- **On-device layout / fonts / SD cache** — the whole point of strategy A is
  that the daemon owns rendering. Strategy B (JSON out) stays as a debug
  surface, not a second path to maintain.
- **Per-view scheduling ("agenda at 8am, tasks at noon")** — cron-flavored
  complexity with no consumer yet; the button cycler + adaptive cadence cover
  it. Add a row to `docs/DEFERRED-WORK.md` if the need firms up.
- **Grayscale dithering / images on the panel** — Spectra-6 text sections
  first; photos are a different rasterizer and a different battery budget.
