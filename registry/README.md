# EmptyOS community app registry

`index.json` is the catalog the Store's **Marketplace** tab browses. It holds two
flat lists — `apps` and `plugins` — of installable community items (the "official
marketplace we provide"). Each entry points at a public source (today: a GitHub
repo); the Store fetches it and, behind a review-gate, installs an app into
`apps/<category>/<id>/` or a plugin flat into `plugins/<id>/`.

The Marketplace tab reads the array matching the active kind (Apps vs
Plugins/Connections). A **connection** is just a plugin carrying the `connector`
tag — it lives in the `plugins` array, not a separate one.

This is **not** the only way to install. The Marketplace tab also takes an
arbitrary GitHub URL, a zip upload, or a local folder path — the registry is
just the curated front door. A working example you can install via the folder
path today (and push to GitHub to test the URL path) lives in
`examples/marketplace/`.

## Where the Store reads this from

- Default: this in-repo `registry/index.json` (ships with every clone).
- Override: set `[apps.store] registry_url = "<raw-json-url>"` in `emptyos.toml`
  to track a remote registry (e.g. a forked/community-maintained one).

## Entry shape

```jsonc
{
  "schema": 1,
  "name": "EmptyOS community app registry",
  "apps": [
    {
      "id": "my-app",                       // ^[a-z][a-z0-9-]*$ — matches the manifest [app] id
      "name": "My App",
      "description": "One line on what it does.",
      "category": "extension/others",       // INSTALL TARGET folder (track/group): extension/<group>,
                                            // public/labs, or personal[/labs]. NOT public/core|standard.
                                            // Determines where the app lands: apps/<category>/<id>/.
      "version": "1.0.0",
      "author": "handle or name",
      "capabilities": ["read", "write"],    // shown in the review-gate; informational
      "source": {
        "type": "github",
        "repo": "owner/repo",               // or "owner" + "repo" split
        "ref": "main",                       // branch / tag / sha (optional, defaults main→master)
        "subdir": ""                         // path within the repo, if the app isn't at root
      }
    }
  ]
}
```

## Rules

- `id` must match the installed app's manifest `[app] id` and the regex
  `^[a-z][a-z0-9-]*$`.
- `source.type` is `github` today. (Zip/folder installs are user-initiated and
  don't go through the registry.)
- Keep entries generic — no personal data, no third-party brand names in
  user-facing strings (CLAUDE.md rules 13 + 14).
- The app at the source must be a normal EmptyOS app folder: `manifest.toml`
  + `app.py` (+ `pages/`), self-contained, no hardcoded absolute paths.

## Plugin entries

Plugins go in the `plugins` array with the same shape, minus `category`
(plugins always land flat at `plugins/<id>/`):

```jsonc
"plugins": [
  {
    "id": "hello-eos-connector",          // ^[a-z][a-z0-9-]*$ — matches manifest [plugin] id
    "name": "Hello Connector",
    "description": "One line on what it does.",
    "version": "1.0.0",
    "author": "handle or name",
    "source": { "type": "github", "repo": "owner/repo", "ref": "main", "subdir": "" }
  }
]
```

Extra rules for plugins (they run code at daemon boot, so the bar is higher):

- `id` must not collide with a built-in plugin or `ESSENTIAL_PLUGINS` (`health`),
  and must not be blacklisted (`plugins/BLACKLIST.toml`).
- The source must be a normal plugin folder: `manifest.toml` + `plugin.py`.
- A "connection" carries `"connector"` in `[provides] tags` — that's the only
  marker; it still lives in the `plugins` array.
- The Store runs a **mandatory static scan** on every plugin install regardless
  of config (see `.claude/rules/store.md`).
