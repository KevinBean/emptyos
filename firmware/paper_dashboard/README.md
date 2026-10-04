# Paper dashboard firmware — M5Paper Color (Arduino) · SKELETON

A pull-mode **e-ink dashboard** satellite for EmptyOS. The daemon renders the whole
panel as a PNG; the board fetches it, blits it, and deep-sleeps until the next
refresh. It registers as a `display` device in the EmptyOS **`/devices`** registry.

**No SD card required.** The microSD slot is optional — the dashboard content lives
in the daemon, fonts/layout are server-side, and the on-board RX8130CE RTC + flash +
8 MB PSRAM cover everything the board needs. A card only helps if you later want an
offline cache of the last image or large local CJK fonts; this path uses neither.

**Hardware-verified**: provisioning, `/devices` registration (+ battery/RSSI
telemetry), PNG fetch, the M5GFX `drawPng` call, button-only deep sleep, and
top-key wake → redraw + view cycling all run on a real M5PaperColor (rotation 0
and top-key ext1 press-to-wake both confirmed on hardware).

```
paper_dashboard/
├── paper_dashboard.ino   — the sketch (tracked; holds NO credentials)
├── secrets.h.example     — optional portal-default template (tracked)
├── secrets.h             — your real daemon URL / token (GITIGNORED)
└── README.md
```

## How it works

```
wake (top key — BUTTON_ONLY; or timer when BUTTON_ONLY = false)
  → connect Wi-Fi (captive portal "EOS-Paper" on first boot / button-hold)
  → POST /devices/api/register   (category "display" + battery % + Wi-Fi RSSI)
  → GET  /devices/api/dashboard/<device-id>?w=400&h=600   → PNG
        (button wake appends &view=next → cycles the configured views)
  → M5.Display.drawPng(...)       (e-ink holds the image at zero power)
  → esp_deep_sleep with NO timer wake — sleeps until the next button press
```

**Refresh is button-only** (`BUTTON_ONLY = true` at the top of the `.ino`): the
panel never wakes on a timer, so it only redraws when you press the top key, and
the daemon's `X-EOS-Refresh-Min` pacing header is ignored. Set `BUTTON_ONLY =
false` to restore the timed cadence (fallback `REFRESH_MIN`, daemon-overridden
per poll for quiet hours / dirty-flag fast follow-up).

The **daemon side** (already built) is `apps/extension/engineering/devices/`:
`GET /devices/api/dashboard/<id>` renders a Spectra-6-palette PNG (clock + today's
tasks + devices-online), and a poll counts as a heartbeat. Preview it in a browser:
`http://<daemon>:9000/devices/api/dashboard/paper-01?preview=1`.

## Toolchain (Arduino IDE 2.x)

1. **File → Preferences → Additional boards manager URLs**, add the M5Stack package:
   `https://static-cdn.m5stack.com/resource/arduino/package_m5stack_index.json`
   then **Tools → Board → Boards Manager** → install **M5Stack**.
2. **Manage Libraries** → install **M5Unified** and **WiFiManager** (by tzapu).
   M5GFX (bundled with M5Unified) decodes PNG natively — no separate PNG library.
3. **Tools → Board → M5Stack → "M5PaperColor"**. **PSRAM: enabled.**
   **Partition Scheme: "Huge APP (3MB No OTA/1MB SPIFFS)"** (or any ≥2MB-app
   scheme) — the default 1.25MB app slot is too small for M5Unified + M5GFX +
   WiFiManager + TLS ("text section exceeds available space"). Flash Size: 16MB.
   (Buttons hardware-probed: **top=GPIO1** — the wake/next-view button —
   middle=GPIO10, bottom=GPIO9, all active-low. Panel is native portrait
   400×600, `setRotation(0)`. Both confirmed on real hardware.)

## Configure (on-device, no file editing)

First boot opens a captive-portal Wi-Fi AP **`EOS-Paper`**:

1. Join `EOS-Paper`, the setup page opens (or visit `http://192.168.4.1`).
2. Pick your 2.4 GHz Wi-Fi; set **Daemon URL** (`http://<daemon-LAN-IP>:9000`),
   **Auth token** (`emptyos.toml [network] auth_token`), **Device id** (e.g.
   `paper-01` — must match the URL you preview).
3. Save — persisted to NVS. **Hold a button at boot** to reopen the portal later.

## Tuning

| Constant (top of the `.ino`) | Meaning |
|---|---|
| `BUTTON_ONLY` | `true` (default): no timer wake — refresh only on a top-key press |
| `REFRESH_MIN` | Deep-sleep minutes when `BUTTON_ONLY = false` — the daemon's `X-EOS-Refresh-Min` header then overrides per poll |
| `PANEL_W` / `PANEL_H` | Requested image size; swap for landscape |
| `DEVICE_ID` default | NVS fallback id |

Panel **content** is configured daemon-side, not here: `/devices/` → the
display card's **panel** link (sections, accents, cadence, quiet hours, live
preview). Full system design: `docs/PAPER-DASHBOARD.md`.

## Phase 2 — voice (BUILT 2026-07-03, hardware-verify pending)

The board's **ES8311 codec + MEMS mic + speaker** are now wired in `voice.ino`
via M5Unified's native `M5.Mic`/`M5.Speaker` (no hand-rolled codec driver):

- **HOLD BtnA while the board sleeps** → wake into voice mode (beep) → it
  records while you keep holding (release = done, second beep) → the utterance
  goes to `/voice-assistant/api/device_turn_stream` (the puck's fast path) →
  **the companion speaks the reply through the board's speaker**, sentence by
  sentence → the normal dashboard cycle runs, so the panel redraws with
  whatever your turn changed. A quick **TAP** still just cycles views.
- Wi-Fi joins non-blocking while you're still speaking (static-IP path), so
  the turn starts almost immediately after release.
- Single-turn per wake (deep sleep drops the session); low buzz = error.
- `VERIFY[board]`: whether this ES8311 shows the puck's ~2× clock quirk — if
  replies play at chipmunk/half speed, adjust the rate in `paperPlayWav`.

## Hardware reference — M5Stack M5Paper Color (verified + doc-sourced)

Collected from docs.m5stack.com/en/core/PaperColor + hardware probing 2026-07-03,
stored here so no session re-fetches it. Board id in M5Stack pkg: **M5PaperColor**
(M5Unified detects it as `board=28`).

| Subsystem | Detail |
|---|---|
| SoC | ESP32-S3R8 (QFN56) — 16 MB flash, 8 MB OPI PSRAM |
| Panel | 4" E Ink Spectra 6, **400×600 native portrait**, controller ED2208-DOA (EL040EF1); full refresh 10–20 s |
| Side keys (probed) | **top=GPIO1, middle=GPIO10, bottom=GPIO9** — all active-low, idle-high with pullup. All RTC-capable (ext1 wake OK). Power key = reset/power (resets the chip). |
| Audio codec (speaker) | **ES8311** @ I2C `0x18` (internal bus SDA=G2 SCL=G3); I2S: MCLK=G42 BCLK=G40 LRCK=G41 **DSDIN=G39** (per docs; M5Unified maps spk data_out=G38 — one source has 38/39 swapped) |
| Mic ADC | **ES7210** (AEC) @ I2C `0x40`, same I2S clocks, **SDOUT=G38** (docs; M5Unified maps mic data_in=G39) |
| Speaker amp | AW8737A, enable **SPK_EN=G46** |
| Audio power | **AUDIO_PWR_EN=G45** feeds codecs + mic (must be high for any audio) |
| RTC | RX8130CE |
| Battery | 1250 mAh; `M5.Power.getBatteryLevel()` works (no charge control) |
| M5Unified audio | 0.2.17+ has full PaperColor support: `M5.Speaker` (ES8311 init cb, I2S_NUM_0) + `M5.Mic` (ES7210 init cb, I2S_NUM_1), enable callbacks drive G45/G46 |

Flash quirks (hard-won, also in the sketch header): download-mode LATCHES until a
real reset (`esptool --after watchdog-reset` kicks the app); never assert DTR/RTS
when monitoring serial (holds the chip in reset); Huge APP partition required.
Under `BUTTON_ONLY` the board no longer wakes on a timer, so it will **not**
re-enumerate USB by itself — press the top key (or the power/reset key) to wake
it into a flashable window.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Won't compile | Wrong board selected — pick **M5PaperColor** from the M5Stack package (not a generic esp32 entry). |
| "Sketch too big" / "text section exceeds available space" | Default partition scheme — set Tools → Partition Scheme → **Huge APP (3MB No OTA)**. |
| `HTTP 401` | Wrong/missing auth token, or daemon not in `private` mode. |
| `HTTP 404` on dashboard | `devices` app not installed/loaded — install it in `/store`, restart `:9000`. |
| Blank/empty image | Daemon reachable but render failed — open the `?preview=1` URL in a browser. |
| Image upside-down / cropped | Adjust `M5.Display.setRotation(...)` in `setup()` (`VERIFY[board]` — native portrait assumed). |
| Can't find `EOS-Paper` Wi-Fi | Power-cycle; hold a button at boot to force the portal. |
