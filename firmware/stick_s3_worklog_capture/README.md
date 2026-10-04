# StickS3 Worklog Capture firmware

Dedicated M5Stack StickS3 firmware that turns the front button into a physical
trigger for EmptyOS Worklog Capture. A tap asks the EmptyOS daemon on this Mac
to capture the Mac screen plus current app/browser context. The result goes to
the review queue; it is not written to the worklog until you approve it.

## Interaction

- Tap: trigger `POST /worklog-capture/api/capture` with `source="device"`.
- Hold the front button during boot: reopen Wi-Fi and EmptyOS setup.
- The display distinguishes a complete screenshot, a context-only capture caused
  by missing macOS Screen Recording permission, authentication failure, and an
  unreachable daemon.

## Configure

The first boot opens a Wi-Fi network named **EOS-Worklog**. Join it and set:

1. A 2.4 GHz Wi-Fi network shared with the MacBook.
2. **Daemon URL** to `http://<macbook-lan-ip>:9000`.
3. **Auth token** to `[network] auth_token` from `emptyos.toml`.
4. **Device id** to `sticks3-worklog-01` or another unique id.

The firmware reuses the same NVS keys as the voice firmware, so an already
configured StickS3 normally keeps its Wi-Fi, daemon URL, token, and device id.
Hold the button during boot if any value needs changing.

EmptyOS must be running and the `worklog-capture` app must be installed. macOS
may ask the EmptyOS/Python process for Screen Recording, Accessibility, and
browser Automation permissions. Screen Recording is the only permission needed
for the image; the other permissions enrich the queued context.

## Build and upload on this MacBook

The PlatformIO environment already created for the voice firmware can build this
project without another installation:

```bash
cd /Users/kb/EmptyOS/firmware/stick_s3_worklog_capture
PLATFORMIO_CORE_DIR="../stick_s3_voice/.pio-core" ../stick_s3_voice/.venv-pio/bin/pio run
PLATFORMIO_CORE_DIR="../stick_s3_voice/.pio-core" ../stick_s3_voice/.venv-pio/bin/pio run -t upload
```

The configured USB port is `/dev/cu.usbmodem1101`.

