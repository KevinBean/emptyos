# Voice Satellite firmware — AtomS3R (Arduino)

Push-to-talk firmware for the M5Stack **AtomS3R + Atomic Echo Base** that talks to the
EmptyOS daemon via `POST /voice-assistant/api/device_turn`. See `docs/VOICE-SATELLITE.md`
for the full architecture + the daemon-side config.

This folder **is** the Arduino sketch (folder name matches `voice_satellite.ino`). Open the
`.ino` directly in the Arduino IDE.

```
voice_satellite/
├── voice_satellite.ino   — the sketch (tracked; holds NO credentials)
├── secrets.h.example     — credential template (tracked)
├── secrets.h             — your real Wi-Fi / token (GITIGNORED — never committed)
└── README.md
```

## 1. One-time toolchain setup (Arduino IDE)

1. Install the **Arduino IDE** (2.x).
2. **File → Preferences → Additional boards manager URLs**, add:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
3. **Tools → Board → Boards Manager** → install **esp32 by Espressif**.
4. **Tools → Manage Libraries** → install **M5Unified**, **M5EchoBase**, **ArduinoJson** (v7), and **WiFiManager** (by tzapu — the on-device setup portal).
   - **M5EchoBase** drives the Atomic Echo Base's ES8311 codec — without it the mic captures
     silence and the speaker is dead. All audio goes through M5EchoBase; the sketch does NOT
     use `M5.Mic`/`M5.Speaker` (they fight it for the I2S bus).

## 2. Configure — on-device, no editing files

Provisioning is **on the device**, not in a file. On first boot (or whenever it can't
reach saved Wi-Fi) the puck opens a captive-portal Wi-Fi AP named **`EOS-Satellite`**:

1. On your phone, join the **`EOS-Satellite`** Wi-Fi network.
2. The setup page opens automatically (or visit `http://192.168.4.1`).
3. Pick your home Wi-Fi (2.4 GHz), and fill **Daemon URL** (`http://<daemon-LAN-IP>:9000`),
   **Auth token** (`emptyos.toml [network] auth_token`), and **Device id**.
4. Save — it persists everything in NVS (flash) and connects. **No reflashing** to change
   Wi-Fi or the daemon later.

**To reconfigure**, hold the front button while powering on — the portal reopens.

`secrets.h` is now **optional** — if present it only pre-fills the portal defaults. The
`.ino` holds no credentials, so it's always safe to commit.

## 3. Flash

1. Connect the AtomS3R by **USB-C** (native USB — no driver; shows up as a COM port).
2. **Tools → Board → esp32 → "M5AtomS3"**.
3. **Tools → PSRAM → enable** (OPI PSRAM — the audio buffers live in PSRAM).
4. **Tools → Port →** select the AtomS3R's port.
5. **Upload** (→). If it can't connect, **hold the reset button ~2 s while it connects**.
6. **Tools → Serial Monitor @ 115200** to watch Wi-Fi + audio + HTTP logs.

## 4. Use it

The whole front **screen** is the button (the tiny side button is RESET). Two modes:

- **Tap → single turn.** Tap, speak; it auto-stops on silence (VAD), replies, returns to idle.
  No need to hold.
- **Long-press → freehand conversation.** Talk ↔ reply ↔ talk with **no taps between turns** —
  each utterance is detected automatically. Closes after **~3 min of silence**, or tap to exit.
- **Double-tap → new conversation.** Clears the threaded context to start fresh.
- **Shake → hands-free turn.** A quick shake starts a single turn without finding the button
  (boards with an IMU, e.g. AtomS3R).

A live **orb** (ported from the browser simulator) shows the state at a glance: it **breathes**
when idle, **grows with your voice** while listening (with ripple rings), **spins** while thinking,
and **pulses blue** while speaking — the transcript (blue) and reply (white) wrap below it. Capture
uses a **two-threshold hysteresis VAD** (like the sim): speech is confirmed above an *onset* level,
the end-of-turn pause only counts below a lower *silence* level, and the band between keeps soft
speech alive so you're not cut off mid-sentence. Tap feedback is instant and calibration is fast
(~160 ms), so there's no dead wait before you speak. The screen **sleeps** (backlight off) after
~30 s idle and wakes on any press. The puck **registers itself** in the daemon's **`/devices`**
registry on boot and stays "online".

> **Activate the audio fix:** v2 pairs with a daemon-side audio-normalization step that boosts
> quiet mic audio into whisper's range. It's live after a `:9000` **restart**. If a turn still
> fails, append `?debug=1` is handled server-side — the `device_turn` reply then carries
> `rms_db`/`peak_db` so the level is diagnosable.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `WiFiManager.h: No such file` at compile | Install **WiFiManager** (by tzapu) in Manage Libraries (step 1.4). |
| Can't find the `EOS-Satellite` Wi-Fi | First boot opens it for 3 min; if missed, power-cycle, or hold the front button at boot to force it. |
| Want to change Wi-Fi / daemon | Hold the front button at boot → the setup portal reopens. No reflash. |
| **Mic silent / `[mic] captured 0 bytes`** | **M5EchoBase** not installed, or wrong base. Install it; Echo Base pins for AtomS3R are `SDA38 SCL39 DIN7 WS6 DOUT5 BCK8` (already set). |
| **`reply (no audio)` on screen** | Daemon returned non-WAV audio (edge-tts MP3). Pin `speak` to **kokoro** (WAV) in `emptyos.toml`. Transcript + reply text still appear — network path is fine. |
| `HTTP 401` | Wrong/missing `AUTH_TOKEN`, or daemon not in `private` mode. |
| `HTTP 404` on `device_turn` | Daemon hasn't loaded the endpoint — restart it (`restart.bat`). |
| `HTTP -1` / hangs on "Thinking" | Can't reach `:9000` — usually **Windows Firewall**. Test from your phone: open `http://<daemon-LAN-IP>:9000/` on the same Wi-Fi. |
| Pressing the button reboots | You're pressing the side **RESET** button — the input is the **screen face**. |
| `PSRAM FAIL` | Enable OPI PSRAM (step 3.3) and/or lower `MAX_SEC` in the sketch. |
| Upload can't connect | Hold reset ~2 s while connecting; check the COM port; try another USB-C cable. |

## Notes

- **Trigger:** v1 is push-to-talk (hold the screen). VAD auto-record is the documented
  hands-free alternative (triggers on any nearby speech).
- **Privacy:** with the daemon pinned to local STT/TTS (`docs/VOICE-SATELLITE.md`), audio
  never leaves your machine/tailnet.
- **Board:** the sketch picks a board profile at the top (`BOARD`). **AtomS3R + Echo Base** is
  implemented; CoreS3 / Cardputer have built-in codecs (a different audio driver) and are a
  follow-up — the profile struct + IMU/shake gate are already in place for them.
- **Devices framework:** the puck is the first device in the EmptyOS **`/devices`** registry —
  categories, a per-category simulator, and event-bus **connections** (e.g. a sensor's motion
  can wake the puck). Physical Approve/Reject for gated `actions[]` is the next layer.
