# EmptyOS New App

Scaffold a new core app end-to-end with every EmptyOS convention wired in from the start — manifest, capabilities, UI helpers, settings panel, deep-link routing, test file, vault-map entry, and release-tier registration. The goal is that the app is **runnable, testable, and release-ready** the moment this skill finishes.

## When to Use

- User says "new app", "create app", "scaffold app `<id>`", "add an app for `<purpose>`"
- Before starting any app that doesn't exist under `apps/` or `apps/personal/`
- **Not** for apps whose logic is personal → those go under `apps/personal/` and skip release-tier registration

## Process

Run steps in order. Confirm the spec with the user at Step 1 before generating files — naming and dependencies are hard to change later.

---

### Step 1: Gather the Spec

Ask the user for these up front (one message, not one at a time):

```
Name/id       : e.g. "reading-log"  (kebab-case, matches directory name)
Display name  : e.g. "Reading Log"
Description   : 1 line — what the app does
Tier          : core | standard | personal   (default: standard)
Capabilities  : which of read, write, think, search, speak, listen, draw
App deps      : other apps this calls via self.call_app(...)
Events emits  : fire-and-forget events — e.g. ["reading:logged"]
Dimension     : one of physical, social, intellectual, emotional, spiritual,
                environmental, financial, occupational (manifest-level only —
                NEVER user-facing per CLAUDE.md §Development Rules 16)
Vault data?   : yes/no — does it write user-authored notes to the vault?
Detail view?  : yes/no — does the UI have a showDetail(id) pattern?
Settings?     : list of (key, label, type, default) tuples, or "none"
```

If the user picks `personal`, the app goes under `apps/personal/` and Steps 6 + 7 are skipped.

---

### Step 2: Pre-flight

```bash
# Check id is free
ls apps/<id> apps/personal/<id> 2>/dev/null && echo "CLASH" || echo "OK"

# Check id doesn't shadow a plugin
ls plugins/<id> 2>/dev/null && echo "CLASH" || echo "OK"
```

Abort and ask for a new id if either clashes.

---

### Step 3: Create `apps/<id>/manifest.toml`

Template (fill from Step 1 spec):

```toml
[app]
id = "<id>"
name = "<Display Name>"
version = "1.0.0"
description = "<1-line description>"
dimensions = ["<dimension>"]

[app.entry]
module = "app"
class = "<PascalCase>App"

[provides.cli]
commands = ["<id>"]

[provides.web]
prefix = "/<id>"

[provides.events]
emits = ["<id>:added", "<id>:updated"]

[requires]
capabilities = [<from spec>]
apps = [<from spec>]
services = []
events = []

[provides.settings]
schema = [
    {key = "<id>.<setting>", label = "<Label>", type = "number|text|boolean|select", default = <default>},
]
```

Omit `[provides.settings]` if the spec said "none". Omit `[provides.events]` if emits is empty.

---

### Step 4: Create `apps/<id>/app.py`

Skeleton must use capabilities (not raw tools), declare prompts as UPPERCASE constants, and wire the declared events.

```python
"""<Display Name> — <1-line description>."""

from __future__ import annotations

import logging
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

log = logging.getLogger("emptyos.<id>")

# ── Prompts (see CLAUDE.md §Development Rules 12) ──────────────
# Keep UPPERCASE, include negative examples, use system= kwarg in calls.
<ID>_SYSTEM = """You are a <role>.
Rules:
- <rule 1>
- <rule 2>

Do NOT:
- <what must not happen>
- <another failure mode>
"""


class <PascalCase>App(BaseApp):
    async def on_start(self):
        log.info("<id> started")

    # ── CLI ────────────────────────────────────────────────────
    @cli_command("list")
    async def cli_list(self):
        items = await self.list_items()
        for it in items:
            print(it)

    # ── Web API ────────────────────────────────────────────────
    @web_route("GET", "/api/items")
    async def api_list(self, request):
        return await self.list_items()

    @web_route("POST", "/api/items")
    async def api_add(self, request):
        body = await request.json()
        item = await self.add(body.get("text", ""))
        await self.emit("<id>:added", {"id": item["id"]})
        return item

    # ── Methods (callable via self.call_app) ───────────────────
    async def list_items(self) -> list[dict]:
        # TODO: vault_query or data/ read
        return []

    async def add(self, text: str) -> dict:
        # TODO: vault_create_note or data/ write
        return {"id": "", "text": text}
```

Rules the skeleton must satisfy — verify before writing:
- `BaseApp` subclass, not a plain class
- All I/O via `self.read/write/search/think/vault_*` — no `open()`, no `requests`, no raw `subprocess`
- Hardcoded vault paths forbidden → use `self.vault_config("key")`
- If the app has `[provides.settings]`, read values with `self.app_config("<key>", <default>)`
- If the app calls an LLM, always pass `system=<ID>_SYSTEM` and set `temperature=` explicitly (0.1–0.3 parsing, 0.3–0.5 analysis, 0.6–0.8 creative)
- **DO NOT write a `@web_route("GET", "/")` handler to serve `pages/index.html`.** The platform auto-mounts `pages/index.html` at `{prefix}/` whenever the `pages/` directory exists (see `emptyos/web/server.py` `_mount_loaded_app_routes`). A custom `/` handler shadows the auto-mount and breaks the UI.
- **EmptyOS is FastAPI, not aiohttp.** Never import from `aiohttp.web`. Route handlers return plain `dict` (auto-serialized to JSON) or `fastapi.responses.HTMLResponse` / `FileResponse` for non-JSON content.

---

### Step 5: Decide whether you need a custom `pages/index.html`

**Default: skip this step.** New CRUD apps render via the auto-UI (`emptyos/web/auto_ui.py`) using the shared component bundle — list with hover-revealed trash buttons, "+ Add" modal with inferred fields, stats tiles where applicable. No HTML to write, full CRUD on first boot.

**Write a custom `pages/index.html` only when**:
- The app's primary surface isn't a list (deep-canvas like `canvas`/`improv`/`board`, single-shot generators like `voice-assistant`, dashboards with bespoke layouts).
- The app needs interactions auto-UI doesn't synthesize (drag-to-reorder, inline cell edit, custom widgets like cover pickers / audio recorders / map views).
- The app is a "reading surface" (journal, blog post viewer) where the layout *is* the product.

If none of those apply, ship without `pages/`. **Migration path** when an author wants to upgrade: open `http://localhost:9000/<id>/`, view source, save as `apps/<id>/pages/index.html`, edit. The platform serves `pages/index.html` in preference to auto-UI when both exist.

---

### Step 5b (only if Step 5 said "yes"): Create `apps/<id>/pages/index.html`

Use the shared helpers; do not reinvent modals, cards, or detail routing.

Template outline:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title><Display Name></title>
<link rel="stylesheet" href="/static/theme.css">
<link rel="stylesheet" href="/static/eos-components.css">
<link rel="stylesheet" href="/static/eos-keys.css">
</head>
<body>
<div class="app-shell">
  <header class="app-header">
    <h1><Display Name></h1>
    <!-- Mandatory when [provides.settings] exists -->
    <button class="btn-settings" onclick="openAppSettings()">&#9881; Settings</button>
  </header>
  <main id="main"><!-- list / detail views --></main>
</div>

<script src="/static/eos.js"></script>
<script src="/static/eos-components.js"></script>
<script src="/static/eos-keys.js"></script>
<script>
// ── Settings panel (mandatory if [provides.settings] exists) ──
var _appSettings = EOS_UI.settingsPanel({
    id: '<id>-settings-panel',
    title: '<Display Name> Settings',
    fields: [
        // one entry per schema row in manifest.toml
    ],
});
function openAppSettings() { _appSettings.open(); }

// ── Hash routing (mandatory if detail view exists) ────────────
var _route = EOS_UI.hashRoute({
    onShow: function(id) { showDetail(id); },
    onHide: function() { hideDetail(); },
});
function showDetail(id) { /* render detail */ _route.set(id); }
function hideDetail() { /* close detail */ _route.clear(); }

// ── Initial load ──────────────────────────────────────────────
async function load() {
    var items = await fetch('/<id>/api/items').then(r => r.json());
    // render list — use EOS_UI.statCards / EOS_UI.modal as needed
    // vault paths? use EOS.noteActions(path), NEVER plain esc(path)
}
load().then(function(){ _route && _route.init && _route.init(); });
</script>
</body>
</html>
```

Omit the settings block if no `[provides.settings]`. Omit the `hashRoute` block if no detail view. Never omit the stylesheet + `eos-components.js` imports.

---

### Step 6: Create `tests/test_sys_<id>.py`

Model on an existing test file (e.g. `tests/test_sys_task.py`). Aim for **10+ cases** across API + UI per `.claude/rules/testing.md`.

```python
"""System app tests: <Display Name> — N use cases."""

import pytest
from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, click_first, switch_tab, wait_for_toast


@pytest.mark.api
class Test<Pascal>API:
    def test_list_structure(self, http_client):
        data = assert_list_response(http_client.get("/<id>/api/items"))
        if data:
            assert "id" in data[0]

    def test_add_emits_event(self, http_client):
        r = http_client.post("/<id>/api/items", json={"text": TEST_PREFIX + "item"})
        assert_dict_response(r)

    # … add 4–5 more API cases: stats, filters, edge cases, persistence


@pytest.mark.interactive
class Test<Pascal>UI:
    def test_page_loads(self, page, base_url):
        page.goto(base_url + "/<id>/")
        assert_no_js_errors(page)

    def test_add_flow(self, page, base_url):
        page.goto(base_url + "/<id>/")
        # click add → fill form → submit → wait_for_toast
        ...

    # … add 3–4 more UI cases: settings open, detail route (if any), filter
```

If the app stores data anywhere `conftest.py` doesn't already clean up, add a cleanup block there keyed on `TEST_PREFIX`.

---

### Step 7: Register in `release.toml` (skip if personal)

Open `release.toml` and add `"<id>"` to the appropriate tier's `apps = [...]` list. Default is `standard` unless the spec said `core`.

```bash
python scripts/package-release.py --check
```

Must exit clean.

---

### Step 8: Vault-Map Entry (skip if no vault data)

If the app writes user-authored notes to the vault, declare where. Vault-map lives at `{vault}/30_Resources/EmptyOS/_vault-map.toml`. Read the current vault connection from `.claude/vault-connection.json`, open the file, and add:

```toml
[<id>]
path = "30_Resources/EmptyOS/<id>"
description = "<what the app stores>"
```

`self.vault_write(...)` will land under that path; `self.vault_config("path")` reads it. **Never hardcode a vault path in `app.py`.**

---

### Step 9: Verify

```bash
# Restart the daemon so the new app is loaded
cd D:/emptyos
# ask user to run restart.bat if daemon is running, then:

# Confirm the app registered
curl -s http://localhost:9000/api/apps | python -c "import sys,json; ids=[a['id'] for a in json.load(sys.stdin)]; print('<id>' in ids)"

# Self-documenting info
python -m emptyos app info <id>

# Run its test file (daemon must be up)
pytest tests/test_sys_<id>.py -v
```

All three must succeed. If `eos app info` fails, the manifest is malformed. If tests fail against an empty implementation, that's expected — stub the handlers to return valid shapes so the baseline tests pass.

---

### Step 10: Report

```
New App Scaffolded: <id>

Files created:
  apps/<id>/manifest.toml
  apps/<id>/app.py
  apps/<id>/pages/index.html
  tests/test_sys_<id>.py

Wired:
  release.toml     → <tier> tier
  _vault-map.toml  → <id> path registered (or "N/A — no vault data")

Verified:
  eos app info <id>         OK
  pytest test_sys_<id>.py   <N> passed, <M> skipped

Next:
  1. Implement the TODO methods in apps/<id>/app.py
  2. Flesh out the UI — list render + add form
  3. When a second app needs a pattern you just wrote, extract to sdk/
  4. Once the UI has real content, run /eos-design-review apps/<id>/pages/index.html
     — catches theme-bootstrap missing, phantom tokens, doubled signals,
     and archetype-mismatched chrome before they harden
  5. iOS check (any page with fixed/sticky elements or div-onclick handlers):
       python scripts/check-ios-safe-area.py apps/<id>/pages/index.html
     Catches the 5 iOS Safari bug families (notch overlap, home-indicator
     overlap, hardcoded notch padding, 100vh viewport collapse, div-onclick
     silent drop). The scanner runs in CI on every push, so failing
     here means the PR won't merge — cheaper to catch now.
  6. Before commit: run /eos-simplify
  7. End of session: run /eos-session-wrapup
```

## Safety

- **Never** scaffold over an existing app — if `apps/<id>` exists, abort and ask.
- **Never** commit personal config into the app's manifest or code — per-machine settings go in `emptyos.toml` `[apps.<id>]`, read via `self.app_config()`.
- **Never** add a wellbeing-wheel picker, dimension tag prompt, or wheel visual to the UI (CLAUDE.md §Development Rules 16). `dimensions = [...]` in manifest is manifest-only metadata.
- **Never** reference third-party brands in user-facing text (prompts, UI labels, error messages) — plugin integrations are the only exception.
- Don't hand-write release-tier entries for `personal` apps — they stay under `apps/personal/` and are gitignored.

## Relationship

- **Once the UI is fleshed out (not before — needs real content to evaluate)** → `/eos-design-review apps/<id>/pages/index.html`. Catches theme-bootstrap regressions, phantom tokens, doubled signals, and archetype-mismatched chrome (e.g. an instrument with too many controls, a list with a competing hero) at the cheapest possible point — when the patterns aren't yet calcified.
- Pre-commit after building out the scaffold → `/eos-simplify`
- End of session → `/eos-session-wrapup` (updates CLAUDE.md counts, safety scan, devlog)
- System health / what to build next → `/eos-system-check-and-fix`
