# App Development Guide

How to build an EmptyOS app from scratch.

## What Is an App

An app is a directory under `apps/` (community, shipped) or `apps/personal/` (yours, gitignored) containing:

```
apps/myapp/
├── manifest.toml    # declaration: id, dependencies, routes, events
├── app.py           # implementation: one class extending BaseApp
├── __init__.py      # empty (required for Python imports)
└── pages/           # optional: custom HTML UI
    └── index.html
```

Apps declare what they need (capabilities, other apps, services). The platform provides it. Apps never import each other directly — they communicate through capabilities, `call_app()`, and events.

## Scaffold

Quick start:

```bash
eos app-gen myapp "Description of what it does"
```

Or create the files manually.

## Manifest

`manifest.toml` is the app's contract with the platform:

```toml
[app]
id = "myapp"
name = "My App"
version = "1.0.0"
description = "What this app does in one sentence"

[app.entry]
module = "app"          # Python file name (without .py)
class = "MyApp"         # Class name in that file

[requires]
capabilities = ["read", "write", "think"]   # which OS verbs you need
apps = ["task"]                              # other apps you call
services = []                                # plugin services you need
connectors = []

[provides.cli]
commands = ["myapp"]    # registers: eos myapp <action>

[provides.web]
prefix = "/myapp"       # all routes mount under this prefix

[provides.events]
emits = ["myapp:done", "myapp:error"]       # events this app produces
listens = ["task:completed"]                 # events this app reacts to
```

### Settings

To expose user-configurable settings:

```toml
[provides.settings]
schema = [
    {key = "myapp.theme", label = "Theme", type = "select", default = "dark", options = ["dark", "light"]},
    {key = "myapp.limit", label = "Max Items", type = "number", default = "50"},
    {key = "myapp.api_key", label = "API Key", type = "text", default = ""},
]
```

Read settings in code: `self._setting("myapp.theme", "dark")`

### Assistant Integration

To register slash commands that the assistant app discovers:

```toml
[provides.assistant]
commands = [
    {slash = "/myapp", method = "api_list", description = "List items"},
    {slash = "/myapp-add", method = "api_add", description = "Add an item"},
]
```

## App Class

`app.py` — your app is a single class:

```python
from emptyos.sdk import BaseApp, cli_command, web_route, on_event


class MyApp(BaseApp):

    # ── Capabilities (the 7 verbs) ───────────────────────

    async def example(self):
        # Read a file from vault
        content = await self.read("notes/today.md")

        # Think (routes to best LLM provider)
        summary = await self.think(
            f"Summarize this note: {content}",
            domain="text",      # routing hint: text, code, reason
        )

        # Write result back to vault
        await self.write("summaries/today.md", summary)

        # Search vault
        results = await self.search("project deadlines")

        # Stream LLM output
        async for chunk in self.think_stream("Tell me a story"):
            print(chunk.get("text", ""), end="")

    # ── Web Routes (JSON API) ────────────────────────────

    @web_route("GET", "/api/items")
    async def api_list(self, request):
        """List all items. Returns JSON."""
        return {"items": ["a", "b", "c"]}

    @web_route("POST", "/api/items")
    async def api_add(self, request):
        """Add an item. Body: {"text": "..."}"""
        data = await request.json()
        text = data.get("text", "").strip()
        if not text:
            return {"error": "text is required"}
        # ... save it
        await self.emit("myapp:added", {"text": text})
        return {"ok": True}

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        """Return app config for the UI."""
        return {"limit": int(self._setting("myapp.limit", "50"))}

    # ── CLI Commands ─────────────────────────────────────

    @cli_command("myapp", help="Manage my items")
    async def cmd_myapp(self, action: str = "list", **kwargs):
        if action == "list":
            self.print_rich("[bold]Items:[/bold]")
            self.print_rich("  - item 1")
        elif action == "add":
            text = kwargs.get("text", "")
            self.print_rich(f"[green]Added: {text}[/green]")

    # ── Event Handlers ───────────────────────────────────

    @on_event("task:completed")
    async def on_task_done(self, data: dict):
        """React when a task is completed."""
        task_text = data.get("text", "")
        await self.emit("myapp:reacted", {"trigger": task_text})

    # ── App-to-App Calls ─────────────────────────────────

    async def get_tasks(self):
        """Call another app's method directly."""
        tasks = await self.call_app("task", "list_tasks")
        return tasks
```

## Vault Operations

Apps read/write vault data through the BaseApp vault API:

```python
class MyApp(BaseApp):

    async def vault_example(self):
        # Query notes by tag
        notes = self.vault_query(tags=["project"])

        # Read frontmatter properties
        props = self.vault_get_properties("10_Projects/myproject/myproject.md")
        status = props.get("status", "unknown")

        # Update frontmatter
        self.vault_update("10_Projects/myproject/myproject.md", {
            "status": "active",
            "updated": "2026-04-12",
        })

        # Read a specific section
        tasks = self.vault_read_section("10_Projects/myproject/myproject.md", "Tasks")

        # Append to a section
        self.vault_append_section(
            "10_Projects/myproject/myproject.md",
            "Timeline",
            "- 2026-04-12 — started development",
        )

        # Create a new note
        self.vault_create_note(
            "30_Resources/EmptyOS/myapp/item.md",
            {"title": "New Item", "tags": ["myapp", "item"], "type": "item"},
            "## Description\n\nThis is a new item.",
        )
```

### Vault Path Discovery

Don't hardcode vault paths. Use the vault map:

```python
# In manifest or vault-map.toml: [myapp] source = "30_Resources/MyData"
folder = self.vault_config("source", "30_Resources/MyData")  # key, default
```

### VaultLibrary (Collection Pattern)

For apps managing a collection of vault notes (items, contacts, songs):

```python
from emptyos.sdk.vault_library import VaultLibrary

class MyApp(BaseApp):
    def __init__(self, kernel):
        super().__init__(kernel)
        self.library = VaultLibrary(
            app=self,
            tag="myitem",
            default_folder="30_Resources/MyItems",
            fields=["title", "status", "priority"],
        )

    @web_route("GET", "/api/items")
    async def api_items(self, request):
        return {"items": self.library.list()}
```

## Custom UI

Create `pages/index.html` for a custom web interface. The page is served at `/{app-id}/`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>My App - EmptyOS</title>
<link rel="stylesheet" href="/static/theme.css">
<link rel="stylesheet" href="/static/eos-components.css">
<style>
/* App-specific styles */
</style>
</head>
<body>
<div id="eos-page">
  <h1>My App</h1>
  <div id="items"></div>
</div>

<script src="/static/eos.js"></script>
<script>EOS.nav('myapp');</script>
<script>
const API = '/myapp/api';

async function init() {
  const r = await fetch(API + '/items');
  const d = await r.json();
  document.getElementById('items').innerHTML =
    d.items.map(i => `<div>${i}</div>`).join('');
}

init();
</script>
</body>
</html>
```

**Required:** every app page must call `EOS.nav('<appid>')` immediately after loading `eos.js`. This injects the top navigation bar, configurable app shortcuts, and the "All Apps" drawer. Without it, users land on an island with no way back. Pass your app id (matches `manifest.toml` `[app] id`) so the nav highlights the current app.

### Shared Components

Use the platform's shared UI library instead of building from scratch:

- **Toast notifications**: `EOS_UI.toast("Saved!", "success")`
- **Modals**: `EOS_UI.modal("Title", "<p>Content</p>")`
- **Form modals**: `EOS_UI.formModal("Add Item", fields, onSubmit)`
- **Stat cards**: `EOS_UI.statCards(container, [{label, value}])`
- **Confirm dialogs**: `EOS_UI.confirm("Delete this?", onYes)`
- **Loading states**: `EOS_UI.loading(container, true)`
- **Vault paths**: `EOS.noteActions(path)` — renders clickable view/edit links

CSS classes from `eos-components.css`: `.toast`, `.hero`, `.donut`, `.tabs`, `.modal`, `.entry-list`, `.ring`, `.heatmap`

### Hot Reload

HTML pages are read from disk on every request — edit and refresh, no restart needed. Python changes require a server restart.

### Always-On-Top Widget (Document Picture-in-Picture)

For apps where the user wants a *persistent surface* (timers, transcripts, now-playing), use the **Document Picture-in-Picture API** to pop a tiny always-on-top OS-level window out of the main UI. Chromium-only (Chrome/Edge 116+) — feature-detect and hide the trigger on Safari/Firefox.

The PiP window's JS still runs in the *parent page's scope*, so existing globals, functions, and intervals work without refactor.

**Recommended pattern:** build a fresh minimal widget inside PiP and drive it from the parent's state on a 250ms render loop — zero risk of breaking the main UI.

```js
async function popOutWidget() {
    if (!('documentPictureInPicture' in window)) {
        EOS_UI.toast('Pop-out needs Chrome or Edge', false); return;
    }
    if (window._pip) { window._pip.close(); return; }
    window._pip = await documentPictureInPicture.requestWindow({width: 260, height: 280});
    var doc = window._pip.document;
    var style = doc.createElement('style');
    style.textContent = 'body{margin:0;background:#0f1115;color:#fff;font-family:system-ui}';
    doc.head.appendChild(style);
    doc.body.innerHTML = '<div id="d">--:--</div><button id="b">▶</button>';
    doc.getElementById('b').onclick = function() { toggleTimer(); };  // calls parent fn
    var iv = setInterval(function() {
        doc.getElementById('d').textContent = formatTime(remaining);  // reads parent state
    }, 250);
    window._pip.addEventListener('pagehide', function() { clearInterval(iv); window._pip = null; });
}
```

**Gotchas:**
- Stylesheets don't transfer — inject `<style>` or copy `<link>` tags into the PiP `head`. CSS variables on `:root` don't cascade; redefine or use literal values.
- Only one PiP window per page; re-opening fails if you don't clear the reference in `pagehide`.
- Must be called from a user-gesture handler (click/keypress).
- Inline `onclick="…"` re-binds to PiP scope when nodes are *moved* into PiP — prefer `addEventListener` for any element that might be relocated.

**Live reference:** `apps/focus/pages/index.html` `popOutTimer()` — the ⤢ button next to the noise toggle pops the focus timer out as a 260×280 always-on-top widget. Full background notes: `{vault}/30_Resources/Technology/Document-Picture-in-Picture.md`.

### Keep Screen Awake on Mobile (Wake Lock API)

Mobile equivalent of PiP — same job ("keep my attention on this"), different mechanism. Holds the screen on while a time-sensitive activity runs. Works in Chrome / Edge / Safari 16.4+ / Firefox 126+. **iOS requires installed PWA for reliability.**

```js
let wakeLock = null;

async function acquireWakeLock() {
    if (!('wakeLock' in navigator)) return;
    try {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => { wakeLock = null; });
    } catch (e) { /* denied / unsupported / battery saver */ }
}

function releaseWakeLock() {
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
}

// Browsers ALWAYS release on visibility hide — re-acquire on return
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && activityIsRunning) acquireWakeLock();
});
```

**Lifecycle rules:**
- Acquire on activity *start* — release on pause / reset / completion. Battery matters.
- **Re-acquire on `visibilitychange`** when returning to a still-active session — non-negotiable, browsers always drop the lock when hidden.
- First acquire must come from a user-gesture context (click / tap). Subsequent re-acquires after visibility-resume are exempt.
- Listen for the `release` event so you reset your reference when the OS yanks the lock under battery saver.

**Live reference:** `apps/focus/pages/index.html` — `acquireWakeLock()` / `releaseWakeLock()` wired into `startTimer` / `pauseTimer` / `resetTimer` / completion path / `restoreTimer` / `visibilitychange`. Full background notes: `{vault}/30_Resources/Technology/Screen-Wake-Lock.md`.

**Pairs with PiP:** PiP keeps a desktop window visible *across other apps*; Wake Lock keeps a mobile screen *visible at all*. Both can ship in the same app; feature-detection picks the right one per platform.

## Events

Apps communicate through the event bus. This is the primary coupling mechanism — apps react to each other's events without importing each other.

```python
# Emit
await self.emit("myapp:item_added", {"id": "123", "title": "New"})

# Listen (decorator)
@on_event("capture:saved")
async def on_capture(self, data: dict):
    text = data.get("text", "")
    # React to a capture event
```

### Event Naming Convention

`{app-id}:{action}` — examples:
- `task:completed`, `task:created`
- `journal:entry_added`
- `capture:saved`
- `publish:built`, `publish:deployed`

## WebSocket Endpoints

For real-time features (chat, live updates):

```python
from emptyos.sdk import ws_route

class MyApp(BaseApp):

    @ws_route("/ws/myapp")
    async def ws_handler(self, websocket):
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_json()
                response = await self.think(data.get("message", ""))
                await websocket.send_json({"reply": response})
        except Exception:
            pass
```

## LLM Prompts

Prompts are first-class artifacts in EmptyOS. Rules:

1. **Named constants** — prompts live in `UPPERCASE` constants at module top, never inline
2. **System/user separation** — use `system=` for persona/rules, user message for the request
3. **Anti-patterns** — every prompt should say what NOT to do
4. **Temperature** — parsing 0.1-0.3, analysis 0.3-0.5, creative 0.6-0.8

```python
SUMMARIZE_PROMPT = (
    "Summarize the following text in 2-3 sentences. "
    "Focus on actionable information. "
    "Do NOT add opinions, commentary, or filler phrases like 'In summary'. "
    "Return only the summary text."
)

class MyApp(BaseApp):
    async def summarize(self, text: str) -> str:
        return await self.think(
            f"{SUMMARIZE_PROMPT}\n\n{text}",
            domain="text",
        )
```

## Testing

EmptyOS uses a 4-tier test suite. Your app gets automatic coverage in Tier 1 (page loads) and Tier 2 (API 200s). For interactive features, add Tier 3 tests:

```python
# tests/test_tier3_myapp.py
import pytest

@pytest.mark.tier3
async def test_myapp_crud(page):
    """Test basic CRUD flow."""
    await page.goto("http://localhost:9000/myapp/")
    # ... Playwright assertions
```

Run: `pytest tests/ -v -k tier1` (EmptyOS must be running on localhost:9000).

## Personal vs Community Apps

| Location | Shipped | Tracked | Purpose |
|---|---|---|---|
| `apps/myapp/` | Yes | Git | Community app — generic, useful to others |
| `apps/personal/myapp/` | No | Gitignored | Your app — personal logic, local only |

Both are equal at runtime — same manifest, same capabilities, same lifecycle. The only difference is distribution.

**Rule**: If the app's **logic** is personal (tracks your specific workflow, uses your specific data schema), it goes in `apps/personal/`. If it's generic (anyone could use it), it goes in `apps/`. Machine-specific **config** (API keys, paths, preferences) always goes in `emptyos.toml` under `[apps.myapp]`, not in app code.

## Addons

An **addon slot** is a place where your app renders user-supplied entries (typically external-site buttons or URL templates) without hardcoding them. Addons keep apps shippable as generic while users extend them with personal integrations.

**Rules:**

1. Addons are **data in `emptyos.toml`**, never code in `apps/`.
2. Your app **owns the slot** (dictionary renders `word_addons` on the lookup card); it **knows nothing about the entries** (YouGlish, Forvo, etc.).
3. **No built-in defaults** — if the user hasn't configured addons, nothing appears.
4. The entry's `label` is shown verbatim, so community apps must not ship with brand-named labels (Rule 14). Users can write whatever they want in *their* config.

**Config shape:**

```toml
[[apps.<app_id>.<slot>_addons]]
id = "unique-id"
label = "Verb shown on button"
icon = "🎧"                  # optional
url_template = "https://site.com/...{ctx_var}..."
```

**Backend route** (until SDK extraction — see `.claude/rules/addons.md`):

```python
@web_route("GET", "/api/word-addons/{word}")
async def api_word_addons(self, request):
    word = (request.path_params.get("word") or "").strip()
    if not word:
        return {"addons": []}
    raw = self.app_config("word_addons", []) or []
    from urllib.parse import quote
    addons = []
    for item in raw:
        if not isinstance(item, dict): continue
        tmpl = item.get("url_template") or ""
        if not tmpl: continue
        addons.append({
            "id": item.get("id") or item.get("label") or "addon",
            "label": item.get("label") or "Open",
            "icon": item.get("icon") or "",
            "url": tmpl.replace("{word}", quote(word, safe="")),
        })
    return {"addons": addons}
```

**UI render** — fetch after the main content, append to an action row, never block primary content:

```js
EOS.api('/myapp/api/word-addons/' + encodeURIComponent(word)).then(function(r) {
    if (!r || !r.addons || !r.addons.length) return;
    var row = document.getElementById('result-actions');
    r.addons.forEach(function(a) {
        var btn = document.createElement('button');
        btn.className = 'examples-btn';
        btn.textContent = (a.icon ? a.icon + ' ' : '') + a.label;
        btn.addEventListener('click', function() { window.open(a.url, '_blank', 'noopener'); });
        row.appendChild(btn);
    });
});
```

**Reference implementation:** `apps/dictionary/` `word_addons` slot.

**When NOT to use addons:**

- Core lifecycle features (e.g. "Save to Vault") → just a normal button, not an addon.
- App-to-app wiring with logic → use the event bus, not addons.
- Single-purpose integration you'll never duplicate → just hardcode it.

**Graduation paths** (not yet built — see `.claude/rules/addons.md`):

- `BaseApp.resolve_addons(slot, **ctx)` SDK helper — extract when a 2nd app adds a slot.
- Manifest `[contributes.<app>.<slot>]` — when an addon needs logic (parsing, events), it becomes its own app that registers via manifest. Platform merges with config-based addons.

## Hub Panel Contributions

The hub (`/hub/`) is a **panel aggregator**, not a hardcoded dashboard. Any app can render on the hub by declaring `[[contributes.hub.panel]]` in its manifest and implementing a matching `panel_*` method. Panels fail-soft — your app can't break the hub.

**When to contribute:**
- Your app has state that deserves a glance from the home screen (a count, a recent item, a ritual reminder, a progress bar, a nudge).
- A user currently has to open your app to see it — but they would act on it from anywhere.

**When NOT to contribute:**
- Detail views, admin surfaces, search interfaces — those live in your app's own `pages/index.html`.
- Data that updates so frequently it'd dominate the hub (put it on your own page with a "View →" link from a minimal stat tile).
- Everything you produce — overwhelming the hub is worse than absence. One or two panels per app is typical.

### Manifest shape

```toml
[[contributes.hub.panel]]
id = "my-panel"              # unique across all apps
method = "panel_my_thing"    # instance method returning data for the renderer
renderer = "stat-tile"       # see renderer registry below
title = "My Section"         # optional header (renderers that show titles)
priority = 150               # lower = higher on page; default 100
group = "dashboard"          # optional; panels sharing a group render together
limit = 5                    # optional cap when method returns a list
lazy = true                  # optional; placeholder until scrolled into view
```

### Priority bands

| Band | Meaning | Examples |
|---|---|---|
| 10–49 | Hero chrome — sits alongside greeting/clock | weather, pinned chips |
| 50–149 | Cognitive layer — acted on right now | priority alert, what-now, 4 slots, today's tasks, AI insights |
| 150+ | Ambient layer — background rhythms, widgets | dashboard tiles, goals, countdowns, month compare, yesterday, quote |

The hub inserts a visual divider between cognitive (<150) and ambient (≥150).

### Method contract

Your method returns data shaped for the declared renderer (see registry below). Return `None` when there's nothing to show and the panel drops silently. Return a list when the renderer expects one; the hub applies `limit` automatically.

```python
async def panel_my_thing(self) -> dict | list[dict] | None:
    # Query your own state, vault, or another app.
    data = await self._compute()
    if not data:
        return None           # panel drops silently
    return self.stat_tile(    # BaseApp helper for stat-tile shape
        icon="📋",
        value=data["count"],
        label="pending",
        href="/myapp/",
    )
```

**Helper:** `BaseApp.stat_tile(icon, value, label, href)` returns the canonical `{icon, value, label, href}` dict for the `stat-tile` renderer. Use it instead of hand-building the dict — if the shape ever changes, every caller gets the update.

### Renderer registry

Platform ships 20 renderers. Pick the one whose data shape matches your intent.

| Renderer | Intent | Data shape |
|---|---|---|
| `hero-weather` | Weather in the hero greeting | `{emoji, temperature, unit, description}` |
| `hero-alert` | Big attention-grabbing card (one) | `{label, text, sub, url, severity}` |
| `accent-card` | Purple gradient action card | `{label, text, url, button_label?}` |
| `next-up` | Time + event + tag row | `{time, event, tag, schedule?}` |
| `expandable-text` | Collapsible text block | `{title, body_html, actions[]}` |
| `chips` | Compact horizontal shortcuts | `[{title, href, ...}]` |
| `plain-list` | Vertical list of links | `[{title, subtitle?, href}]` |
| `slot-list` | Cognitive slot with badges | `[{title, subtitle, href, badge, source, priority}]` |
| `task-list` | Checkable task rows | `[{text, done, tag, tag_tone, href}]` |
| `tiles-row` | 4 big number tiles (hero-adjacent) | `[{value, label, tone, href}]` |
| `stat-tile` | Single stat tile (use with `group="dashboard"`) | `{icon, value, label, href}` |
| `ring` | Score number + collapsible dimensions | `{score, max, label, dimensions[]}` |
| `bar` | Progress bar with label + detail | `{name, pct, detail}` |
| `countdown-tile` | Small countdown card (use with `group="countdowns"`) | `{name, days, direction}` |
| `deadline-row` | Days-until + name + date row | `[{days, name, date, href}]` |
| `compare-tile` | Curr vs prev + delta (use with `group="month-compare"`) | `{name, curr, prev, delta, unit, inverse?}` |
| `media-card` | Emoji + title + subtitle card | `{emoji, title, subtitle, href}` |
| `checklist` | Toggleable checklist rows | `[{text, done, toggle_index?, toggle_endpoint?}]` |
| `text-card` | Small summary text block | `{title, body, href}` |
| `quote` | Footer quote (consumed by chrome) | `{text, author}` |
| `activity-list` | Icon + title + when log | `[{icon, title, sub, when}]` |

**Need a new shape?** Add a renderer to `apps/personal/hub/pages/index.html` with a documented data contract, then use it. New visual = platform change. New instance of existing visual = app manifest change.

### Group rendering

Panels sharing a `group` value render as one visual section, merging their lists. Declare contributions for `dashboard`, `goals`, `countdowns`, or `month-compare` — the hub groups them by name and applies the first panel's renderer.

```toml
# In two different apps, same group:
# apps/expense/manifest.toml
[[contributes.hub.panel]]
id = "expense-month"
method = "panel_month_spend"
renderer = "stat-tile"
group = "dashboard"           # joins dashboard grid

# apps/finance/manifest.toml
[[contributes.hub.panel]]
id = "net-worth"
method = "panel_net_worth"
renderer = "stat-tile"
group = "dashboard"           # same grid, different tile
```

### Lazy panels

Mark expensive panels (LLM calls, heavy scans) with `lazy = true`. The hub renders a placeholder; the client hydrates via IntersectionObserver when the panel scrolls into view. Prevents slow methods from blocking first paint.

### Debug view

Visit `/hub/debug/panels` to see every panel's raw data next to its rendered HTML. Useful for eyeballing "why does my panel look wrong" without restarting the daemon.

## Standalone Export

Every app lives inside the EmptyOS daemon — but some apps are valuable on their own. A board of tasks, a gesture demo, a habits tracker, a portfolio case study — each is a coherent piece of UI that a user (or a friend, or a website visitor) might want to open without booting a Python runtime. `[provides.export]` is how an app opts into that second life.

Running `eos app export <id>` produces a bundle — a directory, a ZIP, or a single HTML file — that opens from `file://` or any static host. Inside the bundle, the **export shim** (`emptyos/web/static/eos-export-shim.js`) intercepts `fetch()` calls, stubs the `EOS.*` globals that would normally talk to the daemon, and degrades features that need system services. The app's own code is mostly unchanged: the shim's job is to make every call **safe** (never throws), and the app's job is to stay honest about which features still work.

### Contract — `[provides.export]`

```toml
[provides.export]
enabled   = true
mode      = "standalone"                 # future: "pwa" (service-worker installable)
hook      = "export"                     # optional app module with export_state / stub_routes / client_overrides
fallbacks = [
    "vault:indexeddb",
    "think:byok-openai",
    "events:local-bus",
    "call_app:stub",
    "search:local",
    "viewer:none",
    "speak:web-speech", "listen:web-speech",
    "draw:none",
    "geo:public-osm",
]
```

`fallbacks` is an ordered list — the shim picks the first matching entry for each capability. When a capability isn't listed, the shim defaults to a safe no-op.

### Fallback catalog

| Capability | Strategies | Behaviour |
|---|---|---|
| `vault` | `indexeddb`, `localstorage`, `fs-api`, `none` | Persist writes to the browser's IndexedDB (default), localStorage, or the File System Access API (Chrome only). `none` → writes are dropped. |
| `think` | `byok-openai`, `none` | BYOK opens a key field in the offline panel; requests go directly to `api.openai.com` from the browser. `none` → `think` calls return an empty string. |
| `events` | `local-bus`, `none` | `local-bus` = in-tab `CustomEvent` bus (no cross-tab, no WebSocket). |
| `call_app` | `stub` | All cross-app calls resolve to `{unavailable: true, offline: true}`. |
| `search` | `local`, `none` | `local` = client-side string search over the bundled snapshot. |
| `viewer` | `none` | "Open in vault viewer" affordances degrade to a "copy path" button. |
| `speak` / `listen` | `web-speech`, `none` | Browser SpeechSynthesis / SpeechRecognition when available. |
| `draw` | `none` | Draw UI hidden or dimmed (no browser fallback for diffusion models). |
| `geo` | `public-osm`, `none` | `public-osm` → directly hits Nominatim + OSRM public endpoints. |

### Hook (optional) — `apps/<id>/export.py`

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .app import MyApp

async def export_state(app: "MyApp") -> dict:
    """Return a snapshot dict. Populates window.EOS_EXPORT_DATA."""
    items = await app.call_app("myapp", "list_items")
    return {"items": items, "config": app.app_config("public_config", {})}

def stub_routes() -> dict:
    """Map GET endpoints to expressions over window.EOS_EXPORT_DATA.

    - String values resolve a single path: "state.items"
    - `$param` inside [] captures URL params: "state.items[$id]"
    - Dict values assemble composite responses:
        {"items": "state.items", "meta": "state.config"}
    """
    return {
        "GET /myapp/api/items": "state.items",
        "GET /myapp/api/items/:id": "state.items[$id]",
        "GET /myapp/api/home": {
            "items": "state.items",
            "config": "state.config",
        },
    }

def client_overrides() -> str:
    """JS injected after the shim. Register write-path handlers here.

    The shim exposes:
      window.EOS_EXPORT.registerRoute(method, path, async (req, params) => response)
      window.EOS_EXPORT.get(key)  /  set(key, value)  /  all()   — IndexedDB
      window.EOS_EXPORT.emit(eventType, detail)                   — local bus
    """
    return r'''
    window.EOS_EXPORT.registerRoute('POST', '/myapp/api/items', async function(req){
        var state = window.EOS_EXPORT_DATA || {};
        state.items = state.items || [];
        state.items.push(req.body);
        await window.EOS_EXPORT.set('/myapp/api/items', state.items);
        window.EOS_EXPORT.emit('myapp:item_added', req.body);
        return { ok: true };
    });
    '''
```

When the hook is absent, the exporter snapshots nothing, returns empty arrays for every `GET`, and forwards every `POST`/`PATCH`/`PUT` to a generic IndexedDB collection keyed by URL path. That's enough for apps whose state is entirely client-side (gesture demo, timer, quotes reader).

### Formats

| Format | File layout | Good for |
|---|---|---|
| `dir` | `index.html` + `_assets/` + `_data/` + `_meta/` | Deploying to Netlify / Vercel / a static nginx host; fastest to inspect. |
| `zip` | Single `.zip` with the above layout | Sharing a downloadable offline app, or delivering a portfolio demo. |
| `single-html` | One `.html` file, all assets inlined | "Email someone a copy" — opens from `file://` with zero dependencies. |

`file://` URLs block `fetch()` against sibling files, so the snapshot (state + routes) is **inlined into the HTML** as `window.EOS_EXPORT_DATA` / `window.EOS_EXPORT_ROUTES`. Sibling `<script src>` and `<link href>` work because browsers allow them over `file://`. `single-html` goes further and inlines every JS/CSS too.

### Triggering an export

```bash
eos app export boards                                 # dir → ./boards-export/
eos app export boards --format zip --out D:/export/   # D:/export.zip
eos app export boards --format single-html            # ./boards-export.html
eos app export boards --verify                        # headless-Chromium smoke test
```

Also exposed over HTTP: `POST /api/apps/{app_id}/export?format=zip` streams a ZIP attachment; useful for a one-click "download this app" button in the UI.

### What the shim does for you

- **Fetch interceptor.** Any `/<prefix>/api/*` call tries (1) handlers registered by `client_overrides`, (2) snapshot paths from `stub_routes`, (3) a generic IndexedDB fallback. External URLs (`https://...`) pass through unchanged.
- **Asset rewriter.** `eos.js` dynamically appends `<script src="/static/eos-keys.js">` etc. The shim patches `document.head/body.appendChild` to rewrite `/static/` → `_assets/` on the fly — the exporter copies all shared static files into `_assets/` during build.
- **EOS overrides.** `EOS.on(...)` becomes an in-page EventTarget. `EOS.openInViewer / noteActions / viewerLink` copy the path to the clipboard and toast a "vault viewer unavailable" hint. `EOS.geocode / getRoute` pass through to public endpoints if `geo:public-osm` is declared, otherwise no-op.
- **Offline pill.** A fixed-position "🔒 Offline export" button top-right opens a panel listing the active fallbacks and, if `think:byok-openai` is declared, accepting an OpenAI key that gets stored only in `localStorage`.

### When NOT to declare `[provides.export]`

Apps that are fundamentally multi-app, kernel-coupled, or depend on live infrastructure should leave `enabled = false`:

- `reactor` — fans events out across every app; meaningless in a single-tab world.
- `billing` — cost tracking against real token telemetry only makes sense live.
- `agent`, `run`, `git`, `app-gen` — need permission-gated tool execution against the filesystem.
- `model-bench`, `providers` — need the live provider chain.
- `settings`, `release`, `system-log` — kernel views.

If an app is borderline — "most of the UI works offline, but a few features need the daemon" — declare `enabled = true`, list honest fallbacks, and gate the remaining features behind `if (window.EOS_IS_EXPORT) { ... }` with a dimmed UI explaining they only run live.

### Gotchas

- **Escape `</script>` in inlined JS.** The `single-html` path auto-applies `</script>` → `<\/script>` replacement; don't bypass it.
- **Composite routes are literal lookups.** `"GET /api/home": {"items": "state.items"}` builds a dict. Don't put logic there — use `client_overrides` if you need computation.
- **`params[$id]` in snapshot paths is string-typed.** URL captures always arrive as strings — if your state keys are integers, normalize in `export_state`.
- **Snapshot size.** Everything in `state` is inlined into the HTML. For tens of MB of data, move large collections into `_data/state.json` and load lazily in your app hook (the `dir`/`zip` formats support this; `single-html` inlines everything).
- **Keep fallbacks honest.** Don't declare `think:byok-openai` if the UI has no opt-in flow — users will see the pill offering it, try the feature, and get silently empty responses.

### Reference implementations

- `apps/task/app.py` — `panel_pulse_stats` (tiles-row) + `panel_todays_tasks` (task-list)
- `apps/projects/app.py` — `panel_upcoming_deadlines` (deadline-row) + `panel_projects_pipeline` (stat-tile in dashboard group)
- `apps/personal/briefing/extended.py` — `panel_morning_routine` (checklist with toggle endpoint)
- `apps/personal/hub/app.py` — hub's own panels for priority-alert, score-ring, slot panels, ai-insights (lazy)

See `.claude/rules/hub-panels.md` for the companion rule when editing apps.

## Contributing tour steps

The product tour walks new users through real EmptyOS pages. Apps contribute steps by adding `[[contributes.tour.step]]` to their manifest — no JS, no app code change. The orchestrator (`emptyos/web/static/eos-tour.js`) reads them from `/tour/api/steps`, navigates to the route, and uses `EOS_UI.spotlight()` to highlight the named selector.

```toml
[[contributes.tour.step]]
id = "myapp.first-action"           # globally unique
priority = 250                       # lower = earlier in the tour
route = "/myapp/"                    # where the step lives
spotlight = "#main-input"            # CSS selector to highlight
title = "Your first action"
body = "Type something — that's the whole interface."
requires = ["read", "write"]         # missing capability → step rewrites to /system
```

Capability-gating is automatic: a step whose `requires` includes a capability with no available provider is auto-rewritten to `/system?capability=<missing>` with a "set this up first" body, so users always land somewhere actionable.

See `.claude/rules/tour-steps.md` for the full contract, priority bands, and selector-stability tips. Reference implementations: `apps/task/manifest.toml` (`task.capture`), `apps/journal/manifest.toml` (`journal.write`), `apps/quick-action/manifest.toml` (`capture.try`).

## Building view-rich apps (table / kanban / calendar / timeline)

Two paths, both backed by the same primitives. Pick by who owns the data.

### Path A — your app owns the data, you want richer in-page views

Drop in the shared EOS_UI helpers:

- `EOS_UI.viewSwitcher({mountId, views, active, onChange})` — `.eos-mode-bar` of view-type buttons. `views` is `['table','kanban','calendar']` or `[{type, label?, icon?}]`.
- `EOS_UI.kanbanLayout({mountId, items, groups, getGroup|inGroup, renderCard, onMove, getItemId, colorMap, wrapCards})` — kanban columns + drag-drop. `groups = [{key, label, color}]`. `onMove(item, newGroupKey)` fires on successful drop. Pass `wrapCards: false` if your `renderCard` already returns a styled card (e.g. `EOS_UI.entityCard`).
- `EOS_UI.inlineCellEdit({el, value, type, options, onSave, onCancel})` — replaces a cell with an input/select on click. Type: `text` / `select` / `date` / `number`. `onSave(newValue)` fires on blur or Enter; Esc reverts.
- `EOS_UI.pillBadge(value, colorMap)` — colored pill (`.eos-pill-blue/amber/green/emerald/red/purple/orange/gray`) for free-form category coloring. Distinct from `.eos-badge-status-*` (semantic).

Reference: `apps/projects/pages/app.js` `renderKanban` uses `kanbanLayout` with `wrapCards: false` so `EOS_UI.entityCard` is the card surface; `onMove` POSTs to `/projects/api/projects/{id}/status`.

### Path B — another app owns the data, you want a power view over it

Make your app boards-compatible — boards becomes the view layer:

1. Expose `async list_all() -> list[dict]` returning a flat list with stable per-row `id` (or `file`). See `apps/projects/app.py:591` and `apps/task/app.py` for reference shapes.
2. Expose `async set_field(id, field, value) -> dict` with a `SETTABLE_FIELDS` whitelist + an event emit on success. Reference: `apps/projects/app.py:629`, `apps/task/app.py` `set_field`.
3. Optionally expose `@web_route("POST", "/api/set-field")` that delegates to `set_field` so HTTP clients can write too.
4. Add a board preset to `apps/boards/presets.py` with `source: {type: "app", app: "<your-id>", method: "list_all"}` and the columns/views you want users to see.
5. Add an "Open as Board" button to your toolbar that POSTs `/boards/api/boards/from-preset` with `{preset_id: "<your-preset>"}` (idempotent — returns existing board if already created), then redirects to `/boards/#<id>`.

The boards engine (`emptyos/sdk/board_engine.py` `DynamicBoardLibrary`) handles source resolution, filtering, sorting, aggregation. Saved per-user view state goes through `emptyos/sdk/view_store.py` `ViewStore`. The view config schema lives in `emptyos/sdk/board_config.py` (`BoardConfig` TypedDict + `COLUMN_TYPES` + `VIEW_TYPES`).

Reference implementations: `project-tracker` and `task-tracker` presets in `apps/boards/presets.py`.

## Checklist

Before shipping a community app:

- [ ] `manifest.toml` has all fields filled
- [ ] No hardcoded paths, names, or personal data
- [ ] No third-party branding in user-facing text (see `.eos-branding`)
- [ ] `eos check-release` passes
- [ ] App has a `pages/index.html` or works with auto-generated UI
- [ ] Events are declared in manifest (`emits` + `listens`)
- [ ] Settings are declared if the app has configurable behavior
- [ ] If the app has hub-worthy state, a `[[contributes.hub.panel]]` entry is declared
- [ ] If the app has user-extensible slots (URL addons, etc.), it exposes them as an addon slot (see Addons) rather than hardcoding entries
