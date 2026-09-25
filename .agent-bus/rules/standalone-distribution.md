---
paths:
  - "emptyos/sdk/exporter.py"
  - "scripts/build_standalone_release.py"
  - "scripts/verify_extension.py"
  - "scripts/check-csp-inline.py"
  - "emptyos/web/static/eos-csp-bridge.js"
---
# Standalone Distribution — single-html + Chrome-extension targets

The two standard **browser-delivered distribution formats** for an
export-enabled app: pure HTML+JS, no Python, no exe, no daemon. They are
*distribution channels*, not deployment lanes (`docs/DEPLOYMENT.md` keeps
them out of the lane table by design) — the audience is a user inside a
locked-down IT environment who can at most open a file or load an unpacked
extension. Both ride the existing export pipeline (`[provides.export]` +
`AppExporter`, `.claude/rules/app-conventions-for-export.md`); this rule is
the packaging contract on top.

**Build command (both targets):**

```bash
python scripts/build_standalone_release.py <app_id>   # → dist/<id>-standalone-<ver>/
```

**Reference consumer:** `boards` (first app shipping both targets — the
Planner-round-trip PM board).

## The two targets

| Target | Artifact | Persistence | Requires |
|---|---|---|---|
| `single-html` | one self-contained `.html` (works from `file://`, shares, email) | Chrome blocks IndexedDB on `file://` → shim memory store + amber pill + explicit **Save/Load data to file** (FS Access API, Ctrl+S) | `[provides.export].enabled` only |
| `extension` | MV3 zip: generated shell (`manifest.json` + 6-line `background.js`) + csp-safe export as `app/` | real IndexedDB on `chrome-extension://` — persists automatically | export surface passes `scripts/check-csp-inline.py --app <id>` |

Ship both when you can: single-html is the zero-permission fallback;
the extension is the better daily driver (persistent storage, toolbar
launch). The shim's Save/Load buttons work in both — they double as the
transfer path between the two artifacts.

## Declaring targets

```toml
[provides.export]
enabled = true
targets = ["single-html", "extension"]   # default: ["single-html"]
```

`build_standalone_release.py` reads `targets` (overridable via
`--targets`); a per-app `EXPORT-README.md` next to the manifest is appended
to the generated release README (boards uses it for the Planner how-to).

## The extension's one real constraint — the CSP bridge grammar

MV3 extension pages enforce `script-src 'self'`: no inline scripts, no
inline `on*=""` handlers, no eval, no hashes. EmptyOS pages keep their
inline-handler idiom anyway because the export ships
`emptyos/web/static/eos-csp-bridge.js` — a no-eval interpreter that
re-dispatches neutered `on*` attributes through a deliberately small
grammar (calls with literal/`this`/`event` args, `return false`,
`if`/ternary — braced or single-statement — sequences, one-path
assignment). It activates only when CSP actually blocks inline handlers,
so live daemon pages are untouched.

Consequences:

- **`scripts/check-csp-inline.py --app <id>` is the gate** — the builder
  refuses the `extension` target until the app's surface (pages/ minus
  `vendor/`, `export.py` client_overrides, shared static) parses clean.
  Registered in preflight (`ui`/`export` scopes).
- **Keep handlers inside the grammar.** No IIFEs, `var`/`function`
  keywords, loops, arrows, or template literals in `on*=""` attributes —
  move that logic into a named function in the page script and call it.
  The grammar deliberately refuses reserved keywords so the scanner fails
  loudly instead of the extension failing silently.
- Chrome logs CSP-violation *reports* for the neutered attributes before
  the bridge dispatches — expected noise in extension mode, filtered by
  the verification harness.

- The exporter's `csp_safe=True` (`eos app export <id> --csp-safe`) is what
  makes the bundle CSP-legal: bootstrap goes to `_data/bootstrap.js`
  instead of an inline `<script>` and the bridge tag is injected.

## Trust story (the extension's pitch to corporate IT)

The generated shell requests `unlimitedStorage` ONLY — zero host
permissions, no content scripts, no network calls (BYOK AI is opt-in via a
key the user pastes). This is deliberately the opposite of
`tools/chrome-extension/` (the daemon *bridge*, which reaches
`localhost:9000`). Don't blur them: a standalone-target app must never gain
host permissions; if it needs a daemon, it's not this distribution format.

## When NOT to use these targets

- **The user can run the daemon** — give them the daemon; exports lose the
  event bus, reactor ripples, and cross-app calls (single-app bundles get
  `call_app:stub`).
- **The app's value is its connections** (reactor chains, multi-app flows)
  — a slice would be hollow. Same bar as `product-packaging.md`.
- **Two-way sync expectations** — export is a one-way snapshot
  (`app-conventions-for-export.md`'s hard principle). The Save/Load file and
  an importer (like boards' Planner .xlsx) are the honest sync media.
- **Team-shared live state** — these are per-browser stores. The team's
  system of record stays wherever IT already blessed (boards: Planner).

## Verification (automatic per release)

`build_standalone_release.py` verifies both targets itself (skip with
`--skip-verify`):

1. single-html — Playwright over `file://`, zero console errors
   (`eos app export --verify`).
2. extension — `scripts/verify_extension.py <shell_dir> [--member <id>]`:
   loads the extension in Chromium (`--load-extension`), asserts the page
   renders (chooser + member for groups), the bridge is active, an
   IndexedDB write survives reload, and there are zero non-CSP-report
   console errors / failed requests. Also runnable standalone against any
   built shell dir.
3. `check-csp-inline.py --app <id>` — the builder's hard gate before an
   extension target.

## Extras (all shipped)

- **Icons** — generated per app (dark tile + initial, 16/48/128) by
  `build_extension_shell` in `emptyos/sdk/exporter.py`; fail-soft when
  Pillow is absent (load-unpacked works without them).
- **CLI form** — `eos app export <id> --format extension` builds a
  load-unpacked-able shell directly (csp-safe export + generated
  manifest/background/icons); `--verify` does static shell checks. The
  release script remains the full path (zip + README + CSP gate).
- **Group bundles** — `python scripts/build_standalone_release.py --group
  <id>` packages an `export-groups.toml` group as one extension: the
  chooser shell is the entry page, every member must pass the CSP scan,
  and `GroupExporter(csp_safe=True)` (also `eos export-group build
  --csp-safe`) externalizes the per-app bootstrap
  (`_data/bootstrap.<app>.js`), the shared auto-RPC wiring
  (`_data/auto-rpc.js`), and the chooser redirect. Groups have no
  single-html mode. Verified on `work-os` (5 apps, cross-app RPC + IDB
  persistence under MV3 CSP).

## Cross-references

- `.claude/rules/app-conventions-for-export.md` — app-side conventions the
  bundle relies on (state-render split, `@web_route` RPC surface).
- `.claude/rules/product-packaging.md` — the exe/product sibling; use that
  for offline tools needing real filesystem/GPU, this for browser-only.
- `docs/DEPLOYMENT.md` § Out of scope — why these aren't lanes.
- `emptyos/sdk/exporter.py` (`csp_safe`), `emptyos/web/static/eos-csp-bridge.js`,
  `scripts/check-csp-inline.py`, `scripts/build_standalone_release.py`.
