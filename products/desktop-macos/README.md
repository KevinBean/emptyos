# EmptyOS Desktop (macOS)

A native macOS app that wraps the **whole EmptyOS daemon** — every app, plugin,
the vault, conversation-mode growth — in one double-clickable window. This is
*not* a WriteDesk-style single-app slice; it boots the real daemon from this
repo and opens it in a native [WKWebView](https://pywebview.flowrl.com/) window.

> **Status:** dev-grade (Tier *open*, see `.claude/rules/product-packaging.md`).
> Runs on your own Mac, references the working clone, unsigned. Signing /
> notarization / `.dmg` distribution is the documented next step (below), not
> built yet.

> **Platform split (intentional).** On Windows the daily driver is
> `scripts/eos_desktop.py` — a browser `--app` window that *attaches* to an
> already-running daemon. This Mac app instead *boots* its own daemon and uses
> a native pywebview (WKWebView) window. Same daemon + vault model, two
> platform-appropriate shells — siblings, not duplicates. The shared
> `free_port` / health-poll plumbing is small and currently lives in each
> launcher; a `emptyos/sdk/daemon_launcher.py` extraction is the clean
> follow-up once both shells are on one branch.

## How it works

```
EmptyOS.app                         built by build_app.py
└─ Contents/MacOS/EmptyOS           shell stub: bakes EOS_REPO + venv python
      │                             → execs launcher.py
      ├─ python -m emptyos start    the REAL daemon, as a subprocess
      │     cwd = repo root         → apps/ plugins/ engines/ discovered live
      │     EOS_CONFIG = app-support→ config + data + vault OUTSIDE the repo
      ├─ wait /api/health           on 127.0.0.1:<port>
      └─ webview window             native, Dock icon; close = daemon torn down
```

Because the app references the working clone, `git pull` or any edit under
`apps/` is live on the next launch — **no rebuild**. The bundle is a thin shell;
the daemon is the codebase.

### Where your data lives

| Path | What |
|---|---|
| `~/Library/Application Support/EmptyOS/emptyos.toml` | machine config (created on first run) |
| `~/Library/Application Support/EmptyOS/data/` | kernel telemetry (syslog, billing, sessions) |
| `~/Library/Application Support/EmptyOS/venv/` | the runtime Python environment |
| `~/EmptyOS/Vault/` | default markdown vault — change `notes.path` in the config or Settings |

Config + data + vault are kept out of the repo (absolute paths in the toml), so
`git` operations and a future read-only bundle never touch user data, while
`cwd` stays the repo root for app discovery. `network.mode = "local"` binds
`127.0.0.1` with no auth — single user, single machine.

## Build (on a Mac)

No PyInstaller, no Xcode needed — just stdlib + a venv:

```bash
python3 products/desktop-macos/build_app.py        # creates the venv + EmptyOS.app
open products/desktop-macos/dist/EmptyOS.app       # first time: right-click → Open
```

`build_app.py` creates `~/Library/Application Support/EmptyOS/venv`, runs
`pip install -e <repo>` + `pip install -r requirements.txt`, then writes the
`.app` bundle whose stub points at that venv and this repo.

- `--no-venv` rebuilds only the bundle (skip the slow pip step).
- Drop an `AppIcon.icns` next to `build_app.py` to brand the Dock icon.

### First launch & Gatekeeper

The app is **unsigned** in this tier. macOS will block a double-click the first
time. **Right-click the app → Open → Open** once; afterwards it launches
normally. (Or `xattr -dr com.apple.quarantine dist/EmptyOS.app`.)

## Run without building (dev loop)

```bash
# one-time: make a venv with the repo + pywebview
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../.. && pip install -r requirements.txt

python launcher.py                 # boots daemon + opens the window
python launcher.py --no-window     # daemon only (smoke test)
python launcher.py --vault ~/Notes # first-run: use an existing vault
python launcher.py --port 9000     # override the port
```

## External services (optional)

The daemon runs fully without them; capabilities fall back to `human`. To
automate more on macOS:

- **Local LLM** — `brew install ollama && ollama pull llama3.1` (matches the
  default `[capabilities.think.ollama]`).
- **Cloud LLM** — uncomment `[capabilities.think.openai]` in the config and set
  `OPENAI_API_KEY`.
- ComfyUI / voice / etc. plug in the same way as on any EmptyOS install.

## Shipping to other people (future — Tier *secret* / signed)

Not built yet; the path per `.claude/rules/product-packaging.md`:

1. **Bundle the runtime** — copy a release-filtered tree + a relocatable Python
   into `Contents/Resources` instead of referencing the working clone, so the
   `.app` is self-contained.
2. **Code-sign + notarize** — needs an Apple Developer account ($99/yr):
   `codesign --deep --options runtime --sign "Developer ID Application: …"`,
   then `xcrun notarytool submit` + `xcrun stapler staple`.
3. **Package** — wrap in a `.dmg`.
4. **Cross-OS** — a Windows/Linux sibling follows the same launcher shape;
   `launcher.py` is already platform-clean except the bundle build.

Keep secrets server-side (Lane 1 service) rather than baked into the binary —
client code is never cryptographically secure.

## Files

| File | Role |
|---|---|
| `launcher.py` | boots the daemon subprocess, waits for health, opens the window, tears down on close |
| `build_app.py` | creates the venv + `EmptyOS.app` bundle (Info.plist + shell stub) |
| `requirements.txt` | shell-only deps (pywebview); daemon deps come from `pip install -e .` |
