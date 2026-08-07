# Voice Satellite — AtomS3R home voice assistant on EmptyOS

A thin hardware **audio I/O satellite** (M5Stack AtomS3R + Atomic Echo Base) that talks to
the EmptyOS daemon: the device captures mic audio, POSTs it, and plays the spoken reply. The
daemon owns STT, the LLM, intent dispatch, the action gate, and all vault writes. The device
holds no vault access and no reasoning — it is a microphone, a speaker, and a button.

The XiaoZhi firmware/cloud is deliberately **not** used. The on-device trigger (VAD or wake
word) is a swappable edge component; the EmptyOS contract below is identical regardless.

## Status (v1 shipped; fast mode + display sibling since)

The EmptyOS side is built and sandbox-verified end-to-end:
- `POST /voice-assistant/api/device_turn` — the device-shaped endpoint (collapses the browser
  stream into one JSON reply). `apps/public/standard/voice-assistant/device.py`.
- `POST /voice-assistant/api/device_turn_stream` — **fast mode** (the default the shipped
  firmware uses): an NDJSON stream so sentence 1 speaks while later sentences still generate.
  See § device_turn_stream below.
- Reuses the existing `listen → think → intent → speak` pipeline via `turn_events`
  (`apps/public/standard/voice-assistant/chat_pipeline.py`) — the browser `/api/chat_stream`
  path is unchanged.
- Verified on a leased sandbox: full audio round-trip (audio → transcript → reply_text →
  audio_urls), multi-turn state threading, audio fetch, and the `chat_stream` regression.
- Tests: `tests/test_sys_voice-assistant.py` (`device_turn` shape, JSON-not-NDJSON).
- **Display sibling:** the 2nd board (M5Paper Color) is a pull-mode e-ink dashboard under the
  `devices` app — server-rendered PNG, composed hub-panel sections, companion presence,
  daemon-paced deep sleep. Full design: `docs/PAPER-DASHBOARD.md`;
  firmware: `firmware/paper_dashboard/`.

Remaining hardware checks: reflash the puck with fast mode + read the latency lines;
first flash of the paper board (two `VERIFY[board]` notes).

---

## The device → daemon contract

### `POST /voice-assistant/api/device_turn`

**Request** — `multipart/form-data`:

| field | type | notes |
|---|---|---|
| `audio` | file | WAV bytes from the mic. 16 kHz mono 16-bit PCM + 44-byte header is ideal for whisper. |
| `messages` | string (optional) | JSON of prior turns `[{role,content},…]` — echo from the previous reply's `session.messages`. |
| `companion` | string (optional) | Companion id — echo from the previous reply's `session.companion`. |

**Response** — one JSON object (never an NDJSON stream):

```json
{
  "transcript": "what are my tasks",
  "reply_text": "You have three tasks due today.",
  "audio_urls": ["/voice-assistant/audio/reply_a1b2.wav", "/voice-assistant/audio/reply_c3d4.wav"],
  "actions": [{"verb": "task.list_today", "status": "applied", "description": "Today's Tasks"}],
  "session": {"messages": [{"role":"user","content":"..."},{"role":"assistant","content":"..."}], "companion": null}
}
```

- **`audio_urls`** — fetch each via `GET /voice-assistant/audio/{filename}` and play **in order**
  (there may be 1–N: a fast preamble, the reply, and any intent confirmations). Files purge after 24h.
- **`actions`** — what the assistant did. `status: "applied"` = done; `status: "pending"` /
  `"confirm_required"` = gated action awaiting approval (the device should *say* it's queued — v1
  has no on-device approve button; that's Phase 2 / `m5-bridge`).
- **`session`** — persist verbatim and resend `messages` + `companion` on the next turn. This is
  the entire multi-turn state; the device is otherwise stateless.
- **Errors** — `{"error": "no audio" | "Could not understand audio" | "..."}` (HTTP 200, JSON).

### `POST /voice-assistant/api/device_turn_stream` (fast mode — firmware default)

Same multipart request as `device_turn`, plus two optional fields the shipped firmware sends:
`device_id` / `device_board` (drives the `device:seen` registry announce) and `fast` (0/1).
The response is an **NDJSON stream** — one JSON object per line:

| event | shape | device action |
|---|---|---|
| `transcript` | `{"type":"transcript","text":…}` | optional display |
| `text` | `{"type":"text","delta":…}` | optional display |
| `audio` | `{"type":"audio","url":…}` | fetch + play immediately (one per sentence) |
| `action` | `{"type":"action","verb":…,"description":…}` | optional display |
| `done` | `{"type":"done","reply_text":…,"session":…,"timing":…}` | persist `session`; `timing` carries `stt_ms`/`gen_ms` |
| `error` | `{"type":"error","error":…}` | speak/show the failure |

Fast mode is why the puck feels responsive: sentence-level TTS URLs stream as they're ready
instead of waiting for the whole reply. Config knobs (via `[apps.voice-assistant]`):
`device_fast_mode` (default true), `device_speak_providers` (default `["kokoro"]`),
`device_transcode_wav`, `device_audio_normalize`, `device_audio_debug_save`.

### Auth
In `network.mode = "private"`, every request needs `Authorization: Bearer <auth_token>`.
Reach the daemon over the tailnet at `https://<magic-dns>/voice-assistant/api/device_turn`.

---

## Daemon config (`emptyos.toml`) — local-first + tailnet

Pin audio to local providers so an always-on device never streams the room to a cloud STT
(also avoids the consent gate hanging on a headless device). Local-only is achieved by
*omitting* the cloud provider sections + setting consent — there is no provider-reorder key in
v1; order is plugin-registration order (edge-tts/kokoro/whisper register local).

```toml
[network]
mode = "private"
auth_token = "<openssl rand -hex 32>"      # device sends: Authorization: Bearer <token>

[cloud]
consent = "never"                          # block cloud audio; fall through to local

[capabilities.think]
providers = ["ollama"]                     # local reasoning

[capabilities.think.ollama]
host = "http://localhost:11434"
model = "llama3.1"

[plugins.edge-tts]
enabled = true                             # in-process free TTS (registers priority 0)

[plugins.voice-api]
host = "http://127.0.0.1:8602"             # local whisper STT + kokoro TTS

[plugins.tailscale-serve]
enabled = true
auto_serve = true
https_port = 443
# Do NOT add [capabilities.speak.openai-tts] or [capabilities.listen.openai-whisper]
```

**Audio format:** pin `speak` to **kokoro** (returns WAV) rather than edge-tts (MP3) so the
ESP32 plays WAV without an MP3 decoder. The reply WAV header carries its own sample rate —
parse it and configure the I²S DAC to match (kokoro is fixed-rate, so you can hardcode once
you confirm it).

---

## Firmware

A complete, flashable **Arduino + M5Unified** sketch ships at
`firmware/voice_satellite/` (`voice_satellite.ino` + `README.md` with the exact flash steps).
It implements the contract above: Wi-Fi → button push-to-talk capture → WAV → POST
`device_turn` (Bearer auth) → play `audio_urls` → persist `session`. The board-specific
caveat is the AtomS3R + Echo Base **ES8311 codec** init — flagged in both files.

The StickS3 variant lives at `firmware/stick_s3_voice/`. It uses the same daemon contract,
but relies on M5Unified's native `M5.Mic` / `M5.Speaker` path for the StickS3's built-in
ES8311 codec, MEMS mic, speaker, LCD, IMU, and battery instead of the Atomic Echo Base.

Pseudocode of the same flow (for an ESP-IDF or alternate port):

```c
// 1. Wi-Fi connect (store SSID/pass + daemon URL + auth_token in NVS).

// 2. Trigger — default to push-to-talk (deterministic, no false triggers):
//      while (button_held()) capture_i2s_pcm(&buf);     // 16 kHz mono 16-bit
//    VAD variant (chosen as fastest): start on speech-onset energy, stop after
//    ~700 ms silence; gate on min-duration + max-clip to suppress false fires.

// 3. Wrap the raw PCM as a WAV (prepend a 44-byte WAV header: 16000 Hz, mono, 16-bit).

// 4. POST multipart to the daemon, carrying the saved session:
//      POST https://<magic-dns>/voice-assistant/api/device_turn
//      Header: Authorization: Bearer <auth_token>
//      Parts:  audio=<wav bytes>;  messages=<saved session.messages JSON>;
//              companion=<saved session.companion>

// 5. Parse the JSON reply (cJSON). For each audio_urls[i]:
//      GET https://<magic-dns><audio_urls[i]>  -> WAV bytes
//      parse WAV header for sample rate -> configure I²S -> stream to speaker.
//    Play them in array order.

// 6. Persist reply.session (messages + companion) to RAM/NVS; send it on the next turn.
//    Optionally show reply_text / actions[].status on the AtomS3R LCD.
```

Half-duplex is fine for v1 (AtomS3R AEC is limited — no barge-in). LCD/LED can reflect the
`actions[]` status ("queued for approval" when an action is `pending`).

---

## Phase 2 — partially superseded by the `devices` app

The old `m5-bridge` sketch is mostly covered now:
- ~~Device status screen feed~~ → **shipped** as the `devices` app's paper dashboard
  (server-rendered PNG composed from hub-panel sections; `docs/PAPER-DASHBOARD.md`).
- ~~Hub tile~~ → **shipped**: `devices` contributes `panel_device_status()`.
- ~~physical **Approve/Reject** for gated actions~~ → **shipped**, on the
  Cardputer Adv, not this puck: `firmware/cardputer_adv_chat/` — Tab shows the
  oldest `GET /rooms/api/pending?status=open` entry, Y/N applies/rejects via
  `POST /rooms/api/pending/{id}/apply|reject`. No board before it had a keyboard
  to build this on. Still open: the same review surface on a *voice* device
  (this puck has no way to say "yes"/"no" without a button), and long-press →
  session grant (`emptyos/sdk/autopilot.py::save_grant(actor_type="device", …)`;
  eligibility floor applies).
- The M5Paper Color carries the same ES8311 codec family as the Echo Base, so a later phase
  can fold this voice path into the paper board (one device = dashboard + puck) — verify the
  ~2× ADC/DAC clock quirk first (`voice_satellite.ino` `audioInit`).

Wake word (ESP-SR / Porcupine / microWakeWord) is the Phase-2 trigger upgrade if VAD proves
too noisy. See `.claude/rules/autopilot-grants.md`, `.claude/rules/room-review-gate.md`,
`.claude/rules/hub-panels.md`.
