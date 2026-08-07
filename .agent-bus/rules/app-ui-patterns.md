# App UI Patterns — Mandatory Shared Helpers

Three patterns every app with the relevant surface MUST use. All live in `emptyos/web/static/eos-components.{css,js}`. Reference implementation: `apps/projects/`.

## In-App Settings Panel (when app has `[provides.settings]`)

Apps with `[provides.settings]` **must** include a ⚙ button in their toolbar that opens a slide-out panel — users should not need `/settings` to configure an app.

`EOS_UI.settingsPanel({id, title, app})` renders a compliant `.eos-settings-panel`, loads from `/settings/api/config`, saves via `/settings/api/set-bulk`. Field types: `text`, `number`, `boolean`, `select`, `textarea`, `password`.

**Prefer `app: '<app-id>'`** — the panel derives its fields from that app's manifest `[provides.settings] schema` (the same source `/settings` renders from), fetched lazily on first open. One declaration, so a new setting can never reach `/settings` and miss the app's own panel.

### Read it back with `BaseApp.setting_or_config` — not `app_config`

Declaring a schema is only half the contract. The panel (and `/settings`) writes
to the **settings service** — restart-free, keyed by the schema key **verbatim**,
since the page calls `saveSetting(s.key, …)` with no app namespacing.
`app_config()` reads a *different* store (`emptyos.toml`, loaded at boot). An app
that declares a key and reads it with `app_config` alone therefore ships a
**toggle that silently does nothing** — it writes a value the app never looks at.
This shipped in 5 of the 96 apps declaring a schema (2026-07-17) and is invisible
in review: the manifest looks right, the panel renders, the click succeeds.

```python
# Reads the live toggle, falls back to emptyos.toml [apps.<id>], then default.
theme = self.setting_or_config("garden.theme", "moss")

# config_key= when the two stores use different conventions — e.g. a dark flag
# whose TOML key must stay `feature.x.enabled` for scripts/check_dark_flags.py
# while its schema key is namespaced to avoid colliding in the global store.
on = self.setting_or_config("myapp.feature.x.enabled", False,
                            config_key="feature.x.enabled")
```

Two rules the helper encodes, both learned the hard way:

- **Namespace the schema key** (`myapp.*`). The page writes it verbatim into a
  *global* store, so a bare `ttl_minutes` collides across apps.
- **A stored `null` or `""` means unset** and falls through to TOML — a reset
  writes an explicit null, and clearing a text input writes `""`; neither should
  permanently shadow the machine's config. `False`/`0` are real values and win,
  which is what lets a toggle turn something *off*.

Use plain `app_config()` when the value is **config-only** (no schema entry) —
addon URL templates, a dark flag with no toggle, per-machine paths. That's still
correct and is what most rules here show.

```html
<button class="btn-settings" onclick="openAppSettings()">&#9881; Settings</button>
```
```js
var _appSettings = EOS_UI.settingsPanel({
    id: 'app-settings-panel',
    title: 'App Settings',
    app: 'myapp',        // fields ← manifest [provides.settings] schema
});
function openAppSettings() { _appSettings.open(); }
```

Pass `fields: [...]` instead **only when the manifest can't express them** — options built from runtime JS constants, or labels carrying guidance a schema has no room for (`ppt` derives its visual-style options from `VISUAL_STYLE_ORDER`; `reader`'s TTS labels flag local-vs-cloud). A hand-written list is a second declaration and it drifts: six apps were hiding eleven of their own settings before `scripts/check-settings-panel-drift.py` (preflight, `ui` scope) started watching. Keep it in sync, or mark a deliberate omission — a dark feature flag, a secret — at the call site:

```js
// settings-panel-drift: ignore myapp.feature.thing.enabled
```

**Per-entity config** (editing one project/record's metadata) is a *different* panel. Reuse the `.eos-settings-panel` class for visual consistency, build the body inline. See `apps/projects/pages/index.html` `openProjectSettings`.

## Deep-linking Detail Views (when app has `showDetail(id)`)

Any app with a `showDetail(id)` pattern (single-entity view toggled via DOM) **must** use hash-based routing so detail URLs are bookmarkable and the browser back button works.

Use `EOS_UI.hashRoute({onShow, onHide})`:

```js
var _route = EOS_UI.hashRoute({
    onShow: function(id) { showDetail(id); },
    onHide: function() { /* close detail DOM */ },
});
// In showDetail(id):   _route.set(id, {silent: true});
// In hideDetail():     _route.clear();
// After initial load:  _route.init();   // reads current hash, opens detail if present
```

Handles URL encoding (paths/spaces/special chars) and `popstate` back/forward.

**`silent` matters.** `set(id)` re-fires `onShow` — correct for *navigation-style*
callers (`onclick="_route.set(id)"` where `onShow` does the render), wrong from
*inside* `showDetail(id)`: the default re-invokes `showDetail`, running the whole
detail render (and its fetches) **twice per open**. Calling `set` from within your
show function → pass `{silent: true}`; using `set` as the click handler itself →
plain `set(id)`. Legacy pages using the non-silent form inside `showDetail` still
work (the re-entry self-terminates after one extra pass) — migrate on touch.

## 4D Timeline Panel (when app has entity detail views with a history + a future)

Apps whose detail views represent **an entity with a story (past) and commitments (future)** — projects, people, job applications, KB notes, places, etc. — **must** opt into the 4D timeline contract. Auto-injected 📅 button → slide-out drawer with PAST / FUTURE / NOW sections aggregated by `BaseApp.timeline(entity_path)`.

This is the structural promise that turns `.claude/rules/time-dimension.md` from advice into a UI guarantee: every entity-shaped detail view becomes a 4D view, not just a snapshot.

### Two opt-in shapes

**Vault-backed (most apps)** — the entity is a vault `.md` note. Declare an `entity_source` method that returns its vault-relative path; the platform aggregator handles the rest:

```toml
[provides.timeline]
entity_source = "project_path"   # method on the app
```

```python
async def project_path(self, id: str = "") -> str:
    """Return vault-relative path for an entity id, or "" if not found."""
    f = self._find_project_file(id)
    if not f:
        return ""
    try:
        rel = f.resolve().relative_to(self.vault_root.resolve())
        return str(rel).replace("\\", "/")
    except Exception:
        return ""
```

**Custom (data/-backed apps)** — entity isn't a vault note (e.g. agent runs in JSON). Declare `custom_handler` returning the full `{past, future, now}` dict yourself:

```toml
[provides.timeline]
custom_handler = "entity_timeline"
```

### Auto-mount contract (frontend)

`eos-components.js` reads `/api/sdk/timeline-apps` on page load; if the current page's URL matches a declaring app's prefix, every element carrying `data-entity-path` gets a 📅 button injected (idempotent, MutationObserver-watched so late-rendering detail swaps work).

App-side: when entering detail view, set `data-entity-path` on any stable element:

```js
async function showDetail(id) {
    var p = await EOS.api('/myapp/api/items/' + encodeURIComponent(id));
    if (p._vault_path) {
        document.getElementById('detail-view').setAttribute('data-entity-path', p._vault_path);
    }
    // ... rest of render ...
}
```

The backend's detail endpoint must return `_vault_path` (or equivalent). Convention is to compute it once at the response boundary:

```python
try:
    vault_rel = str(target.resolve().relative_to(self.vault_root.resolve())).replace("\\", "/")
except Exception:
    vault_rel = ""
return {**p, "_vault_path": vault_rel}
```

### When NOT to opt in

Aggregator/admin apps (hub, settings, store, system, topology, search, boards, voice-assistant) — no entity to render. Generator apps (publish, music-studio, podcast) — output isn't a queryable entity. Stream apps (syslog, billing, devlog) — events ARE the timeline; the drawer would be redundant. Single-shot tools (focus timer, capture composer).

Apps opt out simply by omitting `[provides.timeline]`. The platform never auto-mounts on a page whose backend didn't declare.

### Opt-out per page

Place `data-timeline-button="manual"` anywhere on a page to suppress auto-mount; then call `EOS_UI.timeline4D(path)` yourself from a custom button.

### Manual call

The drawer is also callable directly — useful for graph node clicks, link previews, or anywhere a vault path is in hand:

```js
EOS_UI.timeline4D('10_Projects/myproject/myproject.md', { title: 'Timeline' });
```

Reference impls: `apps/projects/` (`project_path`), `apps/people/` (`person_path`), `apps/personal/jobs/` (`job_path`), `apps/vault-graph/` (node-click → drawer).
