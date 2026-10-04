---
paths:
  - "emptyos/sdk/exporter.py"
  - "scripts/build_standalone_release.py"
  - "scripts/verify_extension.py"
  - "scripts/check-csp-inline.py"
  - "emptyos/web/static/eos-csp-bridge.js"
  - "export-groups.toml"
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

## Who may receive an export

An export hands the recipient the code inside it, so who may have it depends on
**everything the artifact carries**, not just the app it is named after. This is
a *distribution* verdict, **public** (anyone, a public release) vs **held**
(named recipients only). It is separate from `product-packaging.md`'s Open/Secret
*protection* tiers: every export here is Open-tier (the code is readable),
whether it is public or held.

Three sizes of exported program:

| Size | Declared in | Forms |
|---|---|---|
| One app | the app's `[provides.export]` (`enabled`; `targets` is what the release script below builds) | single-html, extension (this script); dir / zip / single-html / extension (`eos app export <id> --format …`) |
| Several apps | a `[[group]]` in `export-groups.toml` | dir / zip (`eos export-group build <id>`), extension (`--group <id>`, see Extras below). No single-html |
| Daemon slice | a `release.toml` tier + `products/<id>/product.toml` | exe (`product-packaging.md`) |

- **An artifact is held if anything in it is held:**
  - any `apps/extension/` or `apps/personal/` app;
  - a tier marked `private = true`;
  - a held engine or held service directory.

  Count apps pulled in through `extends` and the plugins too. Only an artifact
  with none of these may go on a public release. `apps/public/labs` apps count
  as public for this purpose, but labs has not graduated, so a labs export is a
  preview, not a release.
- **Every exe is held today.** `package-release.py` copies each `[include]` path
  wholesale, including all of `engines/` (only `engines/personal/` is excluded)
  and `englishos-cloud/`. So a product exe carries the held engineering engines
  whatever its tier says. The measured case: `dist/emptyos-macro-studio-0.6.4/engines/`
  holds 28 engines, including `cables`, `earthing` and `thermal`. Never attach
  an exe to a public release until the packager filters engines and include
  paths per tier the way `release-public.py` does.
- **A client-side form cannot hide a method.** If the method must stay private,
  ship a hosted service (a Lane 1 service), not an export.
- **To export part of an app, split that part into its own app first**, so the
  verdict stays per app.
- **Log every build and every delivery** in the vault note
  `30_Resources/EmptyOS/exported-programs.md`: what was built, which version, who
  got it and how. Recipients' names go there, never into the repo (CLAUDE.md
  rule 13).
- **Known gaps:**
  - `export-groups.toml` has no `private` flag and ships in the public snapshot
    unfiltered. Never add a held app to a group until that guard exists.
  - The file's header comment is stale. It says `eos export-group <id>` (the
    command is `eos export-group build <id>`) and promises a single-html bundle
    that the CLI refuses.

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
