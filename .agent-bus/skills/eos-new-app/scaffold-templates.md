# eos-new-app — scaffold file templates

**Read this file before Step 3.** The spine (`SKILL.md`) owns the step sequence,
the rules each artifact must satisfy, and the safety gates; this file owns the
literal file templates you write to disk. The autonomous `app-builder` loop reads
this file too — it is part of the scaffold contract, not an appendix.

---

## Step 3 — `apps/<id>/manifest.toml`

Fill from the Step 1 spec.

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

## Step 3.5 — `apps/<id>/INTENT.md`

Every new app gets a **living design doc** alongside the manifest. The grill spec in `30_Resources/EmptyOS/grill/` is the **birth certificate** — frozen at scaffold time. `INTENT.md` is the **living doc** — moves with the code, edited as the app evolves. `{vault}/10_Projects/emptyos/log/app-development.md` is the **auto-written changelog** (Stop hook, no manual upkeep).

Three docs, three lifetimes, three jobs. Don't conflate them.

```markdown
# Intent — <Display Name>

> Living design doc for `apps/<id>/`. Edit as the app evolves.
> Birth certificate: <spec-path>. Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why
<copy the spec's ## Why paragraph here — frozen at birth; only edit if the
app's direction genuinely changes>

## Relationships
**Calls into** (`self.call_app(...)`):
- `<app-id>` — <why we depend on it>

**Emits:**
- `<id>:<event>` — <when this fires, what listeners do with it>

**Listens for** (`@on_event`):
- `<other:event>` — <what we do when it arrives>

(If a section is empty, write `- (none)` rather than dropping the heading —
keeps the doc skimmable.)

## Open questions
<copy the spec's ## Open questions verbatim; add new ones as they surface>

## Future
- (post-v1 ideas, pruned as they ship or get rejected)
```

Fill `## Relationships` from the manifest you just wrote — `[requires] apps`, `[provides.events] emits`, and any `@on_event` decorators in `app.py`. Each bullet ends with `— <one-line why>`. The *why* is the part the manifest doesn't capture; that's what makes this doc worth keeping.

---

## Step 4 — `apps/<id>/app.py`

The skeleton must satisfy the rules listed in the spine's Step 4 — verify against
them before writing.

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

---

## Step 5b — `apps/<id>/pages/index.html` (only if Step 5 said "yes")

Use the shared helpers; do not reinvent modals, cards, or detail routing.

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

## Step 6 — `tests/test_sys_<id>.py`

Model on an existing test file (e.g. `tests/test_sys_task.py`). Aim for **10+ cases** across API + UI per `.claude/rules/testing.md`.

**Derive the test list from the spec, don't invent it.** The module docstring carries a traceability block mapping every `## Acceptance criteria` bullet from the spec note to the test(s) that prove it. Every criterion maps to ≥1 named test; tests that don't trace to a criterion are labeled edge/regression. This is the test *plan* — written against the spec before the handlers are implemented — so a reader can tell at a glance which behaviour is contractual and which is defensive.

```python
"""System app tests: <Display Name> — N use cases.

Acceptance criteria → tests (from 30_Resources/EmptyOS/grill/new-app-<id>-<ts>.md):
  AC1 GET /<id>/api/items returns list with id  → test_list_structure
  AC2 POST /<id>/api/items creates an item      → test_add_emits_event, test_add_flow
  AC3 GET /<id>/ renders the page               → test_page_loads
Edge/regression (no AC): test_unicode_text, test_empty_list_stats
"""

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

## Step 9 — Birth baseline block (written onto the spec note)

Set-semantics — replace the section if re-running, don't accumulate:

```markdown
## Test baseline (birth)
- date: <YYYY-MM-DD>
- pytest: <N> passed, <M> skipped (tests/test_sys_<id>.py)
- acceptance criteria mapped: <N criteria> → <M tests> (see test file docstring)
```

This is the same section `app-builder`'s verify endpoint stamps in the automated path, so both paths leave the same durable record. Touch only this section — the rest of the spec stays frozen.

---

## Step 10 — Report

```
New App Scaffolded: <id>

Files created:
  apps/<id>/manifest.toml
  apps/<id>/app.py
  apps/<id>/INTENT.md
  apps/<id>/pages/index.html
  tests/test_sys_<id>.py

Wired:
  release.toml     → <tier> tier
  _vault-map.toml  → <id> path registered (or "N/A — no vault data")

Verified:
  eos app info <id>         OK
  pytest test_sys_<id>.py   <N> passed, <M> skipped
  Test plan: <N> criteria → <M> tests (all mapped; baseline recorded on spec note)

Next:
  1. Implement the TODO methods in apps/<id>/app.py
  2. Flesh out the UI — list render + add form
  3. When a second app needs a pattern you just wrote, extract to sdk/
  4. Once the UI has real content, run /eos-page-design-review apps/<id>/pages/index.html
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
