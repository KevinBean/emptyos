# Marketplace examples

Two minimal, installable templates for the Store **Marketplace** (`/store` →
Marketplace tab). Copy either folder as the starting point for your own app or
plugin/connection.

| Folder | Kind | id | What it shows |
|---|---|---|---|
| `hello-eos/` | app | `hello-eos` | smallest app — one `@web_route`, one event, a tiny page |
| `hello-eos-connector/` | plugin (connection) | `hello-eos-connector` | smallest plugin — a no-op service + the `connector` tag |

These live under `examples/` (not `apps/` or `plugins/`), so the daemon does
**not** load them automatically — they're templates, installed on demand.

## Install one right now (no GitHub needed)

Open `/store` → **Marketplace** tab:

1. **Apps | Plugins** toggle — pick *Apps* for `hello-eos`, *Plugins* for `hello-eos-connector`.
2. **📁 Local folder** → paste the absolute path, e.g.
   `…/emptyos/examples/marketplace/hello-eos` (or `…/hello-eos-connector`).
3. Review the card (capabilities/routes for the app; services + the
   "runs code at boot" warning + a static scan for the plugin) → **Install**.
4. Run `restart.bat` so the daemon loads it. The app appears at `/hello-eos/`;
   the plugin's service registers as `hello-eos-connector` and shows under the
   **Connections** tab.

To remove it: Store → Marketplace → *Installed from elsewhere* → **Uninstall**
(deletes the folder + drops its `data/store/sources.json` record).

The **Zip** path is the same — zip the folder (so the zip contains one
`hello-eos/` with the manifest inside) and upload it.

## Publish to GitHub (to test the URL path + curate it)

The Marketplace's **GitHub repo** field installs any public repo that contains
a `manifest.toml` (it's fetched as a tarball — no `git clone`, no hooks run).
To make one of these examples installable that way:

```bash
# from the example folder you want to publish, e.g. examples/marketplace/hello-eos
gh repo create eos-hello-app --public --source . --push
```

Then in the Store, paste `github.com/<you>/eos-hello-app` (Apps kind). For a
repo where the app/plugin isn't at the root, use
`github.com/<you>/<repo>/tree/<branch>/<subdir>`.

## Add it to the official registry (optional)

To surface it on the Marketplace's **Official registry** list, add an entry to
[`registry/index.json`](../../registry/index.json) — `apps[]` for an app,
`plugins[]` for a plugin. See [`registry/README.md`](../../registry/README.md)
for the exact entry shape and rules.

## Conventions these templates follow

- **Apps use capabilities, never raw tools** — `self.read()/think()/emit()`.
- The web **prefix == app id == folder name** (`/hello-eos`).
- Block-style frontmatter, unique lowercase `^[a-z][a-z0-9-]*$` id.
- **Plugins are higher trust** — `connect()` runs at daemon boot, so the Store
  always static-scans them and warns. A *connection* is just a plugin with the
  `connector` tag. See `.claude/rules/store.md`.
