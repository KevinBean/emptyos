# EmptyOS Desktop (Windows)

A double-clickable EmptyOS: the whole daemon — apps, vault, AI — in one folder,
with no Python to install, no terminal, and no admin rights.

This is the **product** view. If you want to run EmptyOS from source, see
`docs/GETTING-STARTED.md` instead; if you want to know how the packaging works,
see `.claude/rules/product-packaging.md`.

---

## Install

1. Download `EmptyOS-Desktop-windows-x64-<version>.zip` from the
   [releases page](https://github.com/KevinBean/emptyos/releases).
2. Unzip it anywhere you like — `C:\EmptyOS`, your Desktop, a USB stick.
   **Prefer somewhere you can write to**, e.g. `%LOCALAPPDATA%\EmptyOS`. Program
   Files works, but Windows makes it read-only, so updates cannot install
   themselves there.
3. Run **`EmptyOS.exe`**.

That's it. On first run it creates:

| What | Where |
|---|---|
| Your notes (the vault) | `Documents\EmptyOSVault\` — plain markdown, yours, readable without EmptyOS |
| Config, data, logs | `%APPDATA%\EmptyOS\` |

A wizard asks for your name, where you want your notes, and (optionally) an AI
key. You can change all three later in **Settings → System**, and you can point
the vault at a markdown folder you already have — EmptyOS will adopt it.

### "Windows protected your PC"

The release is **not code-signed**, so SmartScreen warns about it. Click
**More info → Run anyway**.

That warning is honest, and you should not simply trust it away: verify the
download instead. Every release ships a `.sha256` file; compare it against what
you downloaded:

```powershell
Get-FileHash .\EmptyOS-Desktop-windows-x64-0.5.6.zip -Algorithm SHA256
```

If the hash matches the one in the `.sha256` file, the zip is byte-for-byte the
one that was built. (Signing costs a few hundred dollars a year and still shows
the same warning until the certificate builds reputation, so for now the checksum
is the stronger guarantee, not the weaker one.)

---

## What you get

The full `standard` tier — 65 apps, the vault, the event bus, the plugins that
make sense on a laptop. Not a demo or a slice.

**What is deliberately off** in a packaged build:

- **Local-GPU AI** (image generation, local transcription, embeddings). Those
  need multi-gigabyte model runtimes; the product uses a cloud key if you give it
  one, and works without AI if you don't.
- **The dev sidecar** (`dogfood-demo`) and the **global hotkey** plugin. The first
  would boot a second copy of EmptyOS on your machine; the second needs a package
  that isn't bundled.

Everything degrades rather than breaks: with no AI key at all, EmptyOS is still a
vault, a task system, a journal, a calendar, and 60-odd other apps.

---

## Updates

EmptyOS checks for a new version in the background, downloads it, verifies its
checksum, and then **waits**. It never restarts you or replaces anything behind
your back.

When an update is ready, **Settings → System** shows *"Update ready: 0.5.7 —
Restart & update"*. Click it when you're ready. The tray icon has a
**Check for updates** item if you don't want to wait for the background check.

Under the hood each version lives in its own folder:

```
EmptyOS.exe        the launcher — always starts the newest complete version
app-0.5.6/         the version you're running
app-0.5.7/         the update, waiting
```

Nothing is ever overwritten, so an interrupted download cannot damage a working
install. If an update ever misbehaves, the previous version is still on disk:

```powershell
.\EmptyOS.exe --list                # what's installed
.\EmptyOS.exe --version 0.5.6       # start the old one
```

---

## Uninstall

Delete the folder. Then, if you also want your data gone:

- `%APPDATA%\EmptyOS\` — config, logs, and machine state
- `Documents\EmptyOSVault\` — **your notes**. This is the one worth keeping;
  it's plain markdown and every other tool can read it.

There is no registry key, no service, and nothing installed outside those paths.

---

## When something goes wrong

Two log files, both in `%APPDATA%\EmptyOS\`:

- `launcher.log` — did it start at all, on which port, from which config
- `daemon.log` — everything EmptyOS itself printed

They are written unconditionally, precisely so that a build with no console still
has something to send us.

Common things:

| Symptom | Cause |
|---|---|
| Window opens on a port other than 9000 | Something already has 9000 (often EmptyOS from source). Harmless — it scans upward. |
| "Update ready" never appears | You're on the newest version, or the machine is offline. Both are no-ops by design. |
| Update downloads but never applies | The install folder is read-only (Program Files). Move it to `%LOCALAPPDATA%\EmptyOS`. |
| Nothing happens on double-click | `launcher.log` will say why. If it's empty, the folder is incomplete — re-unzip it. |

---

## Building it yourself

```bash
python scripts/package-release.py standard --platform=laptop
EOS_PRODUCT=desktop-windows pyinstaller products/_shared/product.spec --noconfirm --distpath dist/product
EOS_PRODUCT=desktop-windows pyinstaller products/_shared/stub.spec    --noconfirm --distpath dist/stub
python products/_shared/smoke.py            # boots it and asserts it serves
python products/_shared/build_release.py    # -> dist/release/*.zip + latest.json
```

`products/_shared/smoke_update.py` drives the whole update path end to end — it
lays out a real install, stages a newer version, applies it, and asserts the new
one comes up. Run it if you touch anything in `products/_shared/`.

Releases are cut by tagging: `git tag v0.5.6-desktop && git push origin
v0.5.6-desktop`. The tag must match `release.toml` — CI refuses otherwise.
