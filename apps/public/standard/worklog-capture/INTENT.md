# worklog-capture — intent & deferred work

## What this app is

A friction-free **capture source** feeding the existing `worklog` app. Two
in-the-moment triggers (a watched screenshot folder; a hotkey "live scrape" that
grabs the screen + app/window/browser context) plus two passive evidence sources
(AI-session transcripts, browser history) are correlated into time clusters, OCR'd
and drafted **locally** into worklog entries, and surfaced for batch review. Nothing
touches the vault until the user Applies (propose→preview→confirm).

`worklog` owns writing `60_Worklogs/`; this app never writes there directly — Apply
routes through `call_app("worklog", "log_work", ...)`.

## Boundaries (why it's shaped this way)

- **Triggered, never ambient.** No interval/auto screenshotting. The user decides
  what's worth a capture; the digest only interprets what was flagged (plus a
  dimmed "unflagged activity" lane from the passive trail — never auto-applied).
- **Local-first, round 1.** On-device OCR (Apple Vision via `ocrmac`) and the local
  `think` provider (ollama). No screen pixels or vault content leave the machine.
- **Mac first.** The OS primitives live in `capture_mac.py` as the future plugin
  boundary — a Windows port extracts a `screen-capture` plugin with per-OS backends
  without touching app logic.

## Deferred (round 2+)

- **Windows backend** → extract `plugins/screen-capture/` with mac/win backends
  behind one interface; `capture_mac.py` is the seam.
- **M5Stack StickS3 physical capture button (built).** Direct-Wi-Fi firmware under
  `firmware/stick_s3_worklog_capture/` calls the device-friendly
  `POST /api/capture` contract with bearer auth. The Bluetooth Low Energy variant
  under `firmware/stick_s3_worklog_capture_ble/` keeps the daemon URL and token on
  the Mac and uses a native CoreBluetooth bridge. Both trigger the same live
  scrape without adding another capture path.
- **Cloud vision opt-in** — send the screenshot to a cloud vision model (gpt-4o /
  Claude vision) for richer interpretation than local OCR. Gated, off by default;
  the OCR seam in `digest.py::ocr_image` is where an alternate backend slots in.
- **Computer-use tool as an active evidence source.** A future agentic
  screenshot→act→observe loop could *actively navigate* an app to pull structured
  context (open the ticket system, walk the PR list) — richer than a passive
  screenshot. It is cloud-vision + actuation + gated, orthogonal to this passive
  capture core, and should **reuse `capture_mac.py`'s screen-grab primitive** rather
  than the reverse. Round 3+.
- **Full browser page content** via the existing Chrome extension bridge
  (`tools/chrome-extension/`) — beyond the URL/title osascript grab.
- **More evidence sources** — shell history, StickS3 device pings, git activity
  (reuse `apps/extension/dev/progress`). Add each as a pure reader in `sources.py`
  returning `Evidence` spans (calendar is the reference: fetch via `call_app`,
  build spans in a pure function, toggle behind a `source_*` setting).
  *Calendar shipped 2026-07-13* — timed events via `calendar.get_agenda`.
- **Safari history** (needs Full Disk Access; Chrome sqlite is round 1).
- **Selection capture default-on** — it simulates Cmd+C / clobbers the clipboard,
  so it ships implemented-but-off (`include_selection` default false).
- **Live FSEvents folder watcher** — the hourly scheduled scan + "Process now"
  button suffice; a live watcher is only worth it if latency ever matters.
- **Auto-writing `## Timesheet` hour lines** from evidence spans — the
  `footprint_worklog` skill remains the deliberate, human-authored path for hour
  attribution; this app drafts `## Work` items, not timesheet hours.
