# StickS3 voice satellite firmware

Push-to-talk firmware for the M5Stack **StickS3** that talks to EmptyOS via the
existing voice-assistant device endpoint.

The StickS3 is a better all-in-one target than the AtomS3R puck: it has an
ESP32-S3, 8 MB flash, 8 MB PSRAM, 1.14" LCD, BMI270 IMU, ES8311 audio codec,
MEMS mic, speaker, and battery in one unit. The daemon still owns STT, TTS,
thinking, actions, and vault writes; the StickS3 is just mic, speaker, display,
and button.

## Current connection state

This Mac currently sees the StickS3 as:

```text
/dev/cu.usbmodem1101
```

Before first flash USB reported it as `M5Stack UiFlow 2.0` from
`M5Stack Technology Co., Ltd`. After flashing, it remains available on the same
serial device. If the port disappears later:

1. Use a known data-capable USB-C cable.
2. Connect the StickS3 directly to the Mac.
3. If it still does not appear, enter download mode: connect USB, then press and
   hold the side reset/power button until the internal green LED flashes.
4. Re-check the port list; it should appear as a new `/dev/cu.*` device.

## Toolchain

This MacBook now has a local Python 3.11 PlatformIO environment in
`firmware/stick_s3_voice/.venv-pio/`, with PlatformIO's package cache in
`.pio-core/`. Those generated directories are ignored by git.

Build:

```bash
cd firmware/stick_s3_voice
PLATFORMIO_CORE_DIR="$PWD/.pio-core" .venv-pio/bin/pio run
```

Upload:

```bash
cd firmware/stick_s3_voice
PLATFORMIO_CORE_DIR="$PWD/.pio-core" .venv-pio/bin/pio run -t upload
```

Arduino IDE:

1. Install Arduino IDE 2.x.
2. Add the M5Stack board package or the Espressif ESP32 board package.
3. Install libraries: **M5Unified**, **M5PM1**, **ArduinoJson v7**, and
   **WiFiManager** by tzapu.
4. Select the StickS3 board if present. Otherwise select
   `esp32-s3-devkitc-1` with OPI/QIO PSRAM enabled.
5. Enable USB CDC on boot.
6. Select the StickS3 serial port and upload.

PlatformIO settings from M5Stack's StickS3 docs:

```ini
[env:m5stack-sticks3]
platform = espressif32@6.12.0
board = esp32-s3-devkitc-1
framework = arduino
board_build.arduino.partitions = default_8MB.csv
board_build.arduino.memory_type = qio_opi
build_flags =
    -DESP32S3
    -DBOARD_HAS_PSRAM
    -mfix-esp32-psram-cache-issue
    -DARDUINO_USB_CDC_ON_BOOT=1
    -DARDUINO_USB_MODE=1
lib_deps =
    M5Unified=https://github.com/m5stack/M5Unified
    M5PM1=https://github.com/m5stack/M5PM1
    bblanchon/ArduinoJson
    tzapu/WiFiManager
```

## Configure on-device

On first boot, or when it cannot reach saved Wi-Fi, the firmware opens a setup
AP named **EOS-Satellite**:

1. Join `EOS-Satellite` from a phone or laptop.
2. Open `http://192.168.4.1` if the captive portal does not appear.
3. Select a 2.4 GHz Wi-Fi network.
4. Set **Daemon URL** to `http://<this-machine-LAN-IP>:9000` or a tailnet URL.
5. Set **Auth token** from `emptyos.toml` `[network] auth_token`.
6. Set **Device id**, usually `sticks3-01`.

To reopen setup later, hold the primary button during boot.

## Use

- Tap: record one utterance, send it to EmptyOS, play the reply.
- Double tap: clear conversation context.
- Long press: freehand conversation mode.
- Shake: start a single turn using the IMU.

Set `LOOPBACK_TEST` to `1` in `stick_s3_voice.ino` before flashing if you want
to verify local mic-to-speaker audio without Wi-Fi or EmptyOS.

## EmptyOS side

The firmware uses:

- `POST /voice-assistant/api/device_turn_stream`
- `GET /voice-assistant/audio/<file>.wav`
- `POST /devices/api/register`
- `POST /devices/api/devices/<id>/heartbeat`

The main daemon must be reachable from the StickS3 over Wi-Fi, and the response
audio should be WAV. If the device logs `reply not WAV`, pin the device speech
provider to a WAV-producing provider such as kokoro.
