# Analysis: Standalone HTML+JS Export for Apps / App Groups

## Context

User asks: is it possible *today* to export an EmptyOS app (or group of apps) as a standalone HTML+JS bundle that runs without the daemon? And what should we improve?

Short answer: **Yes — the mechanism exists and is wired end-to-end. Bundles ship, run from `file://`, and persist writes to IndexedDB. Phase 3 (group export with cross-app RPC) is implemented but only shallowly adopted by apps.** The remaining work is mostly **per-app conformance** (state seeding, online-only gating, `export.py` hooks for write semantics) and a few platform sharp edges (capability BYOK wiring, asset weight, no re-import path).

## Why export exists (and why it does not weaken EmptyOS)

Export is a **one-way read-mostly postcard** of a single app or small group — for portfolios, archives, "try before install" onboarding, static hosting, air-gapped single-purpose use. It is **not a substitute** for the daemon. Mental model: like exporting a Photoshop file to PNG. The PNG is real and useful; it doesn't make Photoshop pointless.

What survives the snapshot:
- Page UI + per-app data snapshot
- IndexedDB writes (no sync back)
- In-page event bus (one tab only)
- Cross-app calls between bundled members (Tier 1 fix needed)

What does NOT survive — i.e. what makes EmptyOS the daemon-first system it claims to be:
- The 9 capabilities. Only `think` has a (half-wired) BYOK fallback.
- All 13 plugins (ollama, comfyui, voice-api, blender, telegram, webcam, …).
- Reactor cross-app ripples (`git:saved` → journal → related notes → search).
- Vault-as-external-hard-drive. Replaced with per-app IndexedDB; no `_vault-map.toml`, no PARA folders, no cross-app vault queries.
- Scheduler, vault watcher, WebSocket, cloud-consent gate, conversation mode.

The conventions (`render-from-STATE`, `[data-online-only]`, `[provides.export].fallbacks`) are designed so apps build *daemon-first* and export degrades. Export expands reach without dictating UI shape; the improvements below make the postcard honest, not promote HTML+JS as sufficient.

## What exists today (verified, with file paths)

### Build pipeline

- `emptyos/sdk/exporter.py:65` — `AppExporter` (single-app, ~430 lines). Phases 1–2 complete: copy `pages/`, inject shim + bootstrap, rewrite `/static/...` URLs, snapshot state, write `_data/state.json` + `_data/routes.json` + `_data/overrides.js`, format `dir | zip | single-html`.
- `emptyos/sdk/exporter.py:432` — `GroupExporter` (multi-app). Builds `out/<app>/index.html` per member, top-level `index.html` chooser shell, merged `_data/state.json` keyed by app id, shared `_assets/`, build warnings for disabled members + unmet `requires.apps` deps.
- `emptyos/cli/main.py:636` — `eos app export <id>` (`--out`, `--format`, `--verify`).
- `emptyos/cli/main.py:759` — `eos export-group build <id>` (uses `export-groups.toml`).
- `export-groups.toml` — three groups defined (`work-os`, `personal-core`, `assistive`), each with `entry` app + `fallbacks` list.

### Runtime polyfill (load-bearing)

- `emptyos/web/static/eos-export-shim.js` (~515 lines). Fires only when `window.EOS_IS_EXPORT === true`. Responsibilities:
  - **Fetch interceptor** (`eos-export-shim.js:215`): routes `/api/*` to (a) registered handlers, (b) snapshot routes (`window.EOS_EXPORT_ROUTES`), (c) generic IndexedDB. Non-`/api/*` calls + absolute URLs pass through.
  - **IndexedDB** (`eos-export-shim.js:51`): one DB per app (`eos_export_<app_id>`), single `kv` store keyed by URL path. POST/PATCH/PUT writes append/replace; DELETE nulls.
  - **`EOS.*` overrides** (`eos-export-shim.js:299`): `EOS.on/emit` → in-page `EventTarget` (no WebSocket), `EOS.openInViewer`/`noteActions` → "copy path" toast, `EOS.callApp` → in-bundle RPC registry, `EOS.api('/api/health')` → shaped offline response, geocode/routing disabled unless `geo` fallback declared.
  - **UI**: floating "🔒 Offline export" pill + panel listing active fallbacks, BYOK OpenAI input that writes to `localStorage.eos_export_openai_key`.
  - **Cross-app RPC** (`eos-export-shim.js:441`): `registerAppMethod(appId, method, handler)` + `EOS.callApp(appId, method, kwargs)`. Returns `{offline: true, unavailable: true}` for unbundled targets.

### App-side hook

- `[provides.export]` manifest section: `enabled`, `mode = "standalone"`, `fallbacks = [...]`, `hook = "export"`.
- Optional `apps/<id>/export.py` with up to three callables: `export_state(app)`, `stub_routes()`, `client_overrides()`.
- **Reference implementation**: `apps/boards/export.py` (191 lines) — full hook. `export_state` snapshots boards/items/presets, `stub_routes` declares GET path → snapshot key mappings, `client_overrides` returns ~120 lines of inline JS that registers POST/PATCH/DELETE handlers and emits in-page events.

### Documentation + tests

- `.claude/rules/app-conventions-for-export.md` — three conventions: render-from-STATE, `[data-online-only]` gating, `@web_route` as the RPC surface.
- `tests/test_sys_export.py` + `tests/test_sys_export_groups.py` — bundle structure + headless Playwright smoke tests. Parametrize over export-enabled apps.

## What's missing / broken / shallow

### Severity-ordered

1. **App conformance is the bottleneck, not the platform.** Of the apps marked `[provides.export].enabled = true`, only **`boards` has a real `export.py`**. Projects, task, people, quick-action, journal, search, assistant rely entirely on the generic IndexedDB fallback. This means writes lose domain semantics — e.g. a task created in an exported `task` app gets stored under URL key `/task/api/tasks` but doesn't go through `task.add()`'s frontmatter-write path, doesn't ripple `task:created` to the bundled `journal`, and won't render in any app-specific cross-view that expected the canonical shape.

2. **Cross-app RPC registry has a dead end in `GroupExporter`.** `exporter.py:553-558` *collects* each app's `@web_route` method paths into `rpc_methods`, but **nothing emits JS that actually calls `EOS_EXPORT.registerAppMethod(...)` for them**. Without per-app `client_overrides()` doing the registration manually (only boards does this today), `EOS.callApp("task", "add_task", {...})` from inside a bundled `projects` app returns `{unavailable: true, reason: "no_handler"}` even though task is in the bundle. This is the single load-bearing fix to make group exports actually deliver on the cross-app promise.

3. **State convention is recommended but unenforced.** Pages call `EOS.api(...)` on `DOMContentLoaded` instead of seeding from `window.EOS_EXPORT_DATA`. The shim catches it, so it works — but first paint is delayed by interceptor latency, the page can't be tested without the shim, and there's no clean "render from STATE" function to reuse for portfolio demos. Rule exists in `.claude/rules/app-conventions-for-export.md`; adoption is near zero.

4. **Capability fallbacks are advertised but not wired.** The pill UI collects a BYOK OpenAI key into `localStorage`, but `EOS.think()` / `self.think()` paths in pages are not actually rerouted to call OpenAI directly from the browser. So `think:byok-openai` is currently a label, not a working capability. Same for `speak` (no Web Speech API fallback), `listen` (no `MediaRecorder` fallback), `draw` (no fallback at all).

5. **No `[data-online-only]` gating in practice.** AI buttons, voice triggers, "open in viewer" links all render in export bundles and silently no-op or toast on click. The convention exists; nobody has used it.

6. **Asset weight.** Every bundle ships `eos-hands-free.js` (1.8K lines), `eos-components.js` (2.7K lines), `eos-map.js`, `realtime.js` regardless of whether the app uses voice/maps/realtime. The shared-asset list at `exporter.py:44-58` is unconditional. A `[provides.export].assets = ["maps", "voice"]` opt-in would shrink bundles by ~60%.

7. **One-way snapshot is by design, but there's no "import back" path.** A user who edits 30 days of journal entries in an exported bundle has no command to merge them back into the daemon's vault. The IndexedDB content is reachable (DevTools → Application), but no `eos export import <bundle>` reverse command exists. CLAUDE.md's `app-conventions-for-export.md` explicitly disclaims sync, but a one-shot drain-to-markdown for the journal/task case would be cheap and valuable.

8. **Cloud-consent gate doesn't exist in export.** Live mode routes every cloud provider call through `Capability.execute()`'s consent prompt. Export's BYOK skips this — first call to OpenAI fires without a per-call confirmation. Low-stakes for personal use, but worth a per-domain `localStorage` consent token to mirror the rule-18 invariant.

9. **No `docs/EXPORT.md`.** The story is split across CLAUDE.md, `.claude/rules/app-conventions-for-export.md`, the docstring at the top of `exporter.py`, and `eos-export-shim.js`. A single user-facing doc with "what is, isn't, and won't be supported" would prevent the next round of "but I assumed sync would work" surprises.

10. **No bundle verifier beyond Playwright smoke.** Bundles produce no manifest of "what works offline vs. silently degrades" for the user. A `_meta/capabilities.json` listing concrete per-feature status (`vault.write: indexeddb`, `think: byok-openai-required`, `cross_app.task: rpc-bundled`, `voice: disabled`) would let the chooser shell render an honest matrix.

## Recommended improvement plan (prioritised)

### Tier 1 — Make group export actually work

- **Auto-generate cross-app RPC registration in `GroupExporter._rewrite_sub_app_html`**: emit a `<script>` that walks every bundled app's `@web_route` method and registers a handler that does an in-page `fetch('/<other-app>/api/<path>')` (which the shim already routes correctly via the merged routes table + handlers). This is ~30 lines in `exporter.py` and unblocks every group bundle. Critical file: `emptyos/sdk/exporter.py:553`.
- **Add `client_overrides` for the four group members that don't have one** (task, projects, people, quick-action) — at minimum the POST/PATCH/DELETE handlers that mirror their `set_field` / `add` write paths so writes don't silently lose semantics. Use `apps/boards/export.py` as the template. Each is ~60-100 lines.

### Tier 2 — Make capabilities real

- **Wire `think:byok-openai`** in the shim: override `EOS.think()` (and the underlying call path used by `self.think()`-driven UI) to POST directly to `https://api.openai.com/v1/chat/completions` with the localStorage key. Add a per-domain consent token (`localStorage.eos_export_consent_openai_v1 = true`) that prompts on first call. Critical file: `emptyos/web/static/eos-export-shim.js:299` (within `_overrideEOS`).
- **Add Web Speech API fallback** for `speak` (`speechSynthesis.speak`) — three lines, big perceived completeness win.
- **Stub `draw` / `animate` / `listen`** explicitly with toast messaging when no fallback declared (currently just silent).

### Tier 3 — Per-app conformance

- **Migrate three high-value pages to render-from-STATE**: journal, task, boards. Pattern: read from `window.EOS_EXPORT_DATA` if set, else fetch. Pure `render(STATE)` function. Critical files: `apps/journal/pages/index.html`, `apps/task/pages/index.html`, `apps/boards/pages/index.html`.
- **Apply `[data-online-only]`** to AI buttons + voice mic + "open in viewer" affordances across journal, task, capture, boards. The shim already dims + tooltips them; the work is just adding the attribute.
- **Add `[provides.export].assets`** opt-in (default = "minimal" bundle of `theme.css + eos.js + eos-components.{js,css} + eos-export-shim.js`). Critical file: `emptyos/sdk/exporter.py:44`.

### Tier 4 — Polish

- **`_meta/capabilities.json`** emitted by the exporter, rendered in the chooser pill panel as an honest "what works here" matrix.
- **`docs/EXPORT.md`** — single canonical user-facing doc.
- **`eos export reimport <bundle>`** — drain IndexedDB writes back to vault for journal + task collections (one-shot, prints diff before writing).
- **Bundle-size budget test** in `tests/test_sys_export.py` — fail CI if a bundle exceeds e.g. 1.5 MB minified.

## Verification

End-to-end smoke for any of the above:

1. `eos export-group build work-os --format dir --out /tmp/work-os` (today: succeeds; warnings list shallow members).
2. Open `/tmp/work-os/index.html` from `file://`; load `task/index.html`; create a task; reload — task persists (IndexedDB).
3. From `task`, drag a task onto a board — currently fails silently (Tier 1 fix target). After Tier 1: task moves, both apps re-render via in-page event bus.
4. Click "AI suggest" — currently no-ops. After Tier 2: prompts for BYOK key, calls OpenAI, returns suggestion.
5. `python -m pytest tests/test_sys_export_groups.py -v` should still pass.
6. `python -m pytest tests/test_sys_export.py -v -k boards` already passes — keep that as the regression baseline.

## Critical files to touch (when implementation begins)

| File | Why |
|---|---|
| `emptyos/sdk/exporter.py` | Auto-RPC registration (Tier 1), `[provides.export].assets` opt-in (Tier 3), `_meta/capabilities.json` (Tier 4) |
| `emptyos/web/static/eos-export-shim.js` | BYOK think/speak wiring + consent gate (Tier 2), explicit stubs for unfallback'd capabilities |
| `apps/task/export.py` *(new)* | `client_overrides` for task POST/PATCH/DELETE (Tier 1) |
| `apps/projects/export.py` *(new)* | Same, for projects |
| `apps/people/export.py` *(new)* | Same, for people |
| `apps/quick-action/export.py` *(new)* | Same, for capture/quick-action |
| `apps/journal/pages/index.html`, `apps/task/pages/index.html`, `apps/boards/pages/index.html` | Render-from-STATE migration + `[data-online-only]` gating (Tier 3) |
| `tests/test_sys_export_groups.py` | Add cross-app RPC assertion + bundle-size budget |
| `docs/EXPORT.md` *(new)* | Single canonical user-facing doc (Tier 4) |

## Limits — protection and Python dependencies

### Protection (a bundle is portable, by definition)

Anything that runs in a browser can be copied. Protection is about *what you put in the bundle*, not the bundle format.

- **`[provides.export].enabled = false` is the load-bearing knob.** Default off. Export only apps you're OK shipping (calculator, public viewer, demo). Proprietary logic stays daemon-only. Today's posture (opt-in) is correct.
- **Server-side compute = nothing to export.** Kernel, reactor, conversation mode, cross-app ripples, capability mesh, plugin integrations cannot be exported because they require a running Python process. The most valuable parts of EmptyOS stay home by construction.
- **BYOK shifts cost.** Re-hosters of a cloud-`think` bundle still need their own API key.
- **Don't bake other people's data in.** Today the snapshot is scoped to the exporting app's own data. Worth adding a build-time warning if cross-user data is detected.
- **Watermark `_meta/export.json`** with bundle id + recipient + timestamp for traceability. Cheap; doesn't prevent copy.

What this does *not* protect: a flat artifact, once shipped, is a flat artifact. Treat it like a PNG. Anything needing DRM-grade protection must not be exported — keep it in the daemon or behind a Lane 1 service with auth.

### Python packages — build-time vs runtime

| Timing | Python available? | Pattern |
|---|---|---|
| **Build time** (`export.py` runs in the daemon's Python) | Yes | Use any pip package; ship the *result* in `_data/state.json`. |
| **Runtime in browser** | No | JS only. Pyodide is an escape hatch (~10 MB, breaks most C-extension packages) — opt-in per app, never default. |

Practical guidance:

- **Pure data transform** the user sees as a list/chart → run in `export.py:export_state`, render in JS. Works today.
- **Interactive logic that genuinely needs Python at request time** → mark route `online_only=True` (a flag we'd add to `@web_route`); exporter skips it; shim toasts on call. Or move the feature to the daemon and accept "not in export."
- **Heavy interactive Python (Jupyter, live numpy)** → export is the wrong tool. Use Lane 1 (service container) or Lane 2 (daemon container) from `docs/DEPLOYMENT.md`.

Add to Tier 4: an `@web_route(online_only=True)` flag honored by both the exporter (skip) and the shim (graceful disable with toast).

## Worked example — a calculator app

A calculator is the canonical happy case: no capabilities, no cross-app calls, tiny state, pure-JS compute.

- **Shape:** `apps/calculator/{manifest.toml, app.py, pages/index.html}`. State `{expression, history}` rendered from `window.EOS_EXPORT_DATA` with a pure `render(STATE)` function. History list persisted via generic IndexedDB on `POST /calculator/api/history` — no `export.py` needed.
- **Export:** `eos app export calculator --format single-html --out calculator.html`. Single file, drop on GitHub Pages, email, or Telegram. Opens from `file://` or any static URL.
- **Works today:** math, button grid, history persistence per-browser, in-page event bus, offline pill.
- **Caveats today:** bundle is 2–3 MB (ships voice/map/realtime JS unused) → fixed by Tier 3 minimal-assets opt-in (~150–300 KB after). IndexedDB is per-origin → opening from a different folder gives a fresh history. No reverse import → daemon-side history and export-side history don't merge (Tier 4 closes this).
- **Treat as reference:** first use case for Tier 3's `[provides.export].assets = "minimal"` and the docs/EXPORT.md walkthrough.

### Tier 3.5 — Pyodide runtime tier (Python at runtime in the browser)

Opt-in escape hatch for apps whose logic genuinely requires Python at request time (sympy, numpy, scientific stack). Not the default — earns its 10 MB only when the user provides input that must be processed by a Python library on the spot.

**Manifest opt-in:**
```toml
[provides.export]
enabled = true
runtime = "pyodide"                  # default "js"
pyodide_packages = ["sympy", "numpy"]
```

**Exporter additions** (`emptyos/sdk/exporter.py`):
- Copy Pyodide loader + WASM core into `_assets/pyodide/`.
- Copy declared wheels into `_assets/pyodide/wheels/`.
- Embed `app.py` text into `_data/app.py.txt`.
- Bootstrap script: `loadPyodide()` → `loadPackage(...)` → `runPythonAsync(app_py_text)` → register handlers.

**Capability bridge** (new `emptyos/web/static/eos-export-pyodide-shim.js`): Python-side wrapper for `BaseApp` capabilities (`self.read/write/emit/call_app`) calling back into the existing JS shim (`idbGet/Set`, `_localBus`, `EOS.callApp`). Same `app.py` runs in daemon and browser; only the I/O layer flips.

**Bundle profile** for a Python-calculator (sympy + numpy): ~17 MB total. Cold start 2–5 s first load, <1 s cached. Cached after first visit.

**Honest tradeoffs:**
- 10–20 MB floor — surgical use only.
- Single-threaded by default; Workers possible but non-trivial.
- Native-extension packages without WASM wheels will not load.
- Capabilities other than `think` (BYOK) still don't survive — Pyodide doesn't change the capability story.
- Tracebacks land in browser console with WASM offsets; ship sourcemaps and set expectations.

**Decision rule:**
- Deterministic, not user-input-dependent → run in `export.py:export_state` at build time, ship JSON. Stay on JS runtime.
- User input → Python lib → result, on the fly → Pyodide.
- Heavy interactive Python (Jupyter-style notebooks) → not export. Use Lane 1 service or Lane 2 daemon container.

**Critical files when implementing:**
- `emptyos/sdk/exporter.py` — new branch in `AppExporter.build` for `runtime = "pyodide"`.
- `emptyos/web/static/eos-export-pyodide-shim.js` *(new)* — capability bridge.
- `tests/test_sys_export_pyodide.py` *(new)* — Playwright smoke asserting Python actually runs (e.g. `sympy.diff(x**2, x) == 2*x`).
- `docs/EXPORT.md` — document the runtime-tier choice + bundle weight.

## Bottom line

The export mechanism is **architecturally complete and correctly factored** — separation of concerns between exporter (build), shim (runtime polyfill), and per-app `export.py` (domain semantics) is exactly right. What's missing is conformance work in the apps and one platform fix (auto-RPC) to make group bundles deliver their advertised cross-app behaviour. Nothing here requires a redesign; the shape is good.
