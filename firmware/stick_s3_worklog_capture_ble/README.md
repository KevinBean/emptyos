# StickS3 Bluetooth Worklog Capture

This firmware turns the M5Stack StickS3 into a Bluetooth Low Energy button for
EmptyOS Worklog Capture. It uses no Wi-Fi and stores no EmptyOS address or auth
token. A small native macOS bridge receives the BLE button press and calls the
local Worklog Capture endpoint.

## How it works

```text
StickS3 tap -> BLE notification -> Mac bridge -> EmptyOS localhost -> review queue
                                     <- result acknowledgement <-
```

The StickS3 display reports whether the capture succeeded, was queued without a
screenshot because macOS permission is missing, or failed because EmptyOS is
offline. Nothing is applied to the worklog until it is approved in the review
queue.

## Build and flash

```bash
cd /Users/kb/EmptyOS/firmware/stick_s3_worklog_capture_ble
PLATFORMIO_CORE_DIR="../stick_s3_voice/.pio-core" ../stick_s3_voice/.venv-pio/bin/pio run
PLATFORMIO_CORE_DIR="../stick_s3_voice/.pio-core" ../stick_s3_voice/.venv-pio/bin/pio run -t upload
```

The configured USB port is `/dev/cu.usbmodem1101`.

## Run the Mac bridge

From a normal Terminal window:

```bash
cd /Users/kb/EmptyOS
firmware/stick_s3_worklog_capture_ble/run_bridge.sh
```

The bridge uses Apple's built-in CoreBluetooth framework and Objective-C compiler,
so there are no Python packages to install. On first launch, macOS asks Terminal for
Bluetooth access. Allow it under **System Settings > Privacy & Security >
Bluetooth** if needed.

The bridge reads the bearer token from `emptyos.toml` `[network] auth_token` and
only calls `http://127.0.0.1:9000`; the token never travels over Bluetooth. Start
EmptyOS from your own terminal before using the button.

For automatic reconnection after login or reboot, install the included macOS
login service once:

```bash
firmware/stick_s3_worklog_capture_ble/install_bridge.sh
```

Remove it with `firmware/stick_s3_worklog_capture_ble/uninstall_bridge.sh`.

## Use

- Start EmptyOS on the Mac.
- Keep `run_bridge.sh` running in a Terminal window.
- Wait for the StickS3 display to show **READY**.
- Tap the front button once.
- Review the queued result at `/worklog-capture/` in EmptyOS.

The ESP32-S3 supports Bluetooth Low Energy only, not Bluetooth Classic. The BLE
service UUID is intentionally app-specific; nearby devices can discover it, but
the only exposed operation is a reversible capture request that still lands in
the review queue.
