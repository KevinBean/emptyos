---
paths:
  - "apps/**/store/**"
  - "emptyos/kernel/app_loader.py"
  - "emptyos/kernel/plugin_loader.py"
  - "registry/**"
---
# Store — Per-User Install Gate

`/store` is the ComfyUI-Manager/Obsidian-community-plugins-style UI for browsing and toggling apps + plugins + skills. Restart-required for apps and plugins; skills hot-load (Claude Code reads `.claude/skills/` live).

- **Catalog source** — every `manifest.toml` under the app track tree (`apps/public/<group>/`, `apps/extension/<group>/`, `apps/personal/`, at any depth), plus `plugins/*/manifest.toml` and `.claude/skills/eos-*/SKILL.md`. Discovery is the shared depth-agnostic scan in `emptyos/sdk/app_layout.py` (`iter_app_dirs`). The built-in tabs (Apps/Plugins/Skills) are a local catalog; the **Marketplace** tab installs apps from *outside* the repo (see Marketplace section below).
- **Three states (Obsidian-style)** — apps + plugins have *installed / enabled* as separate toggles. Installed-and-disabled keeps the code + config + dep graph intact but skips loading at boot — quick "I don't want this running today" without losing setup. Skills only have two states (installed / uninstalled) since their toggle is a folder move (`.claude/skills/eos-<id>/` ↔ `.claude/skills/_retired/eos-<id>/` — `_retired/` is the one archive dir the agent-bus sync preserves).
- **State location** — apps + plugins in `data/store/installed-{apps,plugins}.json` (per-user, mutable, schema v2: `{installed: {...}, disabled: [...], last_change: ...}`). State stays out of manifests so `git pull` can't clobber per-user choices.
- **Loader semantics** — `loader.installed_ids()` = raw install set (for catalog UI); `loader.disabled_ids()` = subset that's installed but disabled; `loader.enabled_ids()` = `installed - disabled ∪ essentials` (what actually loads). Kernel/CLI/web-middleware all iterate `enabled_ids()`.
- **First-boot seed** — `AppLoader.installed_ids()` and `PluginLoader.installed_ids()` write the state file once with every discovered manifest marked installed. Existing daemons see no behavior change after upgrade; curation is opt-in.
- **Always-on essentials** — `ESSENTIAL_APPS = {store, settings, hub}` in `emptyos/kernel/app_loader.py` and `ESSENTIAL_PLUGINS = {health}` in `plugin_loader.py`. The store UI refuses to toggle these; a determined user can edit the JSON file directly. (State inspection lives at the built-in `/system` server route — not an app — so it isn't an essential.)
- **Demo mode bypasses the gate** — `demo.enabled = true` ⇒ every discovered manifest is treated as installed (preserves the bundled-experience semantics); `/store` UI is reachable but state changes have no effect at boot.
- **Optional manifest field** — `[app] store_category = "engineering"` (or `productivity`, `creative`, `engineering`, `personal`, `ai`, `dev`, `core`, `meta`). Drives the store's category filter **and the launcher section grouping** on the home page, the global `⋯` "All Apps" drawer, and the hub `app-grid` panel (all via `GET /api/apps/sections` → `emptyos/sdk/app_sections.py`); defaults to `"other"`.
- **Tiers vs store (sunset plan resolved 2026-07)** — tiers are *packaging* vocabulary (what a release ships); the store is *runtime* choice (what a user enables). The old plan to collapse `[tiers.standard]` / `[tiers.engineering]` / `[tiers.english-learning]` / `[tiers.dev]` into `[tiers.core]` + `[tiers.demo]` is retired as unexecutable: `standard` IS the public release scope (`[targets.public]`, `release-public.py PUBLIC_TIERS`, `check-tier-folder.py`), `engineering` anchors the `portfolio` distribution via `extends`, and `dev` supplies the `basic-engineer` authoring apps. End-state: tiers remain the release/packaging layer, per-user enablement remains the store's job, and tiers never gate runtime behavior (all loaded apps are equal). Distribution tiers with no target, profile, or deploy path get deleted outright (kairo + concord, removed 2026-07 — see git history) rather than kept "until V1 settles".

## Marketplace — installing apps from outside the repo

The Store's **Marketplace** tab brings third-party apps into the app track tree.
This is the one mechanism both we (dev) and a consumer use: a consumer tree and a
dev tree have the **same shape** (`apps/public/` + `apps/extension/` +
`apps/personal/` + `apps/_catalog/`); the only difference is which first-party
apps the release filter left in `public/`.

- **Category placement (no `apps/installed/`)** — an install declares a *category*
  (track/group, e.g. `extension/engineering`, `public/labs`, `personal`) and lands
  at `apps/<category>/<id>/`, a first-class member of the tree the loader scans.
  Curated public tracks (`public/core`, `public/standard`) are **not** install
  targets; default bucket is `extension/others`. Category source priority:
  registry-declared > request-supplied > default.
- **Kept out of git per-clone** — on install the Store appends `apps/<category>/<id>/`
  to **`.git/info/exclude`** (NOT the tracked `.gitignore`), so installed
  third-party code can sit in any category yet is never committed/shipped, and the
  tracked `.gitignore` stays free of per-machine churn. `release-public.py` /
  `release.py` also drop the whole `extension/` + `personal/` subtrees defensively.
- **Four sources, one pipeline** — *official registry* (curated `registry/index.json`,
  in-repo or a configurable `[apps.store] registry_url`; entries carry their own
  `category`), *GitHub URL* (`github.com/user/repo[/tree/ref/subdir]`), *zip upload*,
  *local folder*. All resolve to a temp dir, then share validate → review → commit.
- **Provenance** — `data/store/sources.json` records `{id: {type, url, ref, version,
  category, install_dir, exclude_line, installed_at}}` per install (audit + update +
  clean uninstall). Install also flips `store_state.mark_installed` so the Apps tab
  sees it.
- **Trust gate (propose → preview → confirm)** — `preview`/`upload` resolve +
  validate but install nothing; they return a manifest summary (capabilities, app
  deps, routes, emits, file list, target category) behind a confirm token. The UI
  shows it, the user confirms, then `install` commits by token. **No code runs at
  install** — it loads on the next `restart.bat`. Aligns with
  `.claude/rules/proposed-action.md`.
- **Safety** — GitHub fetch uses a **tarball download, not `git clone`** (no git
  hooks run); zip/tar extraction validates every member (os.sep-boundary
  path-traversal guard + symlink rejection); a **`py_compile` gate** rejects any
  app whose `*.py` won't compile. Reserved `ESSENTIAL_APPS` ids and collisions with
  built-in/personal apps are refused (a same-id app already tracked in
  `sources.json` is treated as an update).
- **Uninstall** — for a marketplace app, `api_uninstall` deletes the recorded
  `install_dir` (containment-guarded to inside `apps/`), removes the
  `.git/info/exclude` line, and drops the `sources.json` record (no parking — the
  code isn't first-party). First-party apps keep toggle-only uninstall.
- **Code** — pipeline in `apps/public/core/store/marketplace.py` (bound onto `StoreApp`
  per `.claude/rules/multi-module-apps.md`); UI is the Marketplace tab in
  `apps/public/core/store/pages/index.html`; registry entry shape in `registry/README.md`.

### Plugins + Connections (same pipeline, `kind`-threaded)

The marketplace installs **plugins** too — not via a separate code path but by
threading a `kind` ("apps" | "plugins") through the existing pipeline. A
**"connection" is just a plugin** (Gmail, Obsidian, Tailscale, ollama,
comfyui…); there is no distinct connector type. Differences from the app path:

- **Flat placement** — a plugin lands at `plugins/<id>/` (not a category folder),
  because the plugin loader globs `plugins/*/manifest.toml` flat. The per-clone
  exclude line is `plugins/<id>/`. The historical "`plugins/installed/`" guess was
  wrong — flat is what the loader sees.
- **`PluginManifest`, not `AppManifest`** — `_validate_plugin_dir` parses the
  `[plugin]` section (a missing one → "is this an app, not a plugin?"), refuses
  `ESSENTIAL_PLUGINS` (`health`) + ids in `plugins/BLACKLIST.toml`, and checks
  collisions against `kernel.plugins.manifests`.
- **Mandatory static scan** — plugins execute code at daemon boot
  (`connect()` / `auto_start()`), unlike apps (no load-time code). So `skill_scan`
  runs on **every** plugin install regardless of `feature.skill-scan.enabled`
  (the flag stays advisory for apps), and the review-gate shows a stronger "this
  plugin runs code when the daemon starts" warning. Fail-soft — the user is still
  the gate (`.claude/rules/proposed-action.md`).
- **Dependencies warn, never auto-install** — `[requires.plugins]` that aren't
  installed surface as `missing_plugin_deps` in the preview; install still proceeds.
- **Namespaced `sources.json`** — `{"schema":2,"apps":{id:…},"plugins":{id:…}}` so an
  app and a plugin can share an id. A legacy flat `{id:…}` file migrates into the
  `apps` namespace on read (persisted on next record/drop).
- **Endpoints** — `kind` rides on `preview` (body), `upload` (form field),
  `registry`/`installed` (`?kind=`); `install` reads `kind` from the stashed pending
  entry (the token is the source of truth); `uninstall/{kind}/{id}` already existed.
- **Connections lens** — a plugin that bridges to an external service/runtime/API
  carries `"connector"` in `[provides] tags`; the store's **Connections** tab is a
  filtered view over the plugins catalog (install/enable/disable reuse the plugins
  path — presentation only, no new state).
- **Restart** — same as apps: install only stages the folder + state + sources; the
  plugin doesn't `connect()` until the next `restart.bat`.

- **Out of scope (v1)** — auto-update, signing/checksums, a hosted catalog service,
  inter-plugin dependency auto-install.
