# App UI Patterns — Mandatory Shared Helpers

Two patterns every app with the relevant surface MUST use. Both live in `emptyos/web/static/eos-components.{css,js}`. Reference implementation: `apps/projects/`.

## In-App Settings Panel (when app has `[provides.settings]`)

Apps with `[provides.settings]` **must** include a ⚙ button in their toolbar that opens a slide-out panel — users should not need `/settings` to configure an app.

Use `EOS_UI.settingsPanel({id, title, fields})` — renders a compliant `.eos-settings-panel`, loads from `/settings/api/config`, saves via `/settings/api/set-bulk`. Field types: `text`, `number`, `boolean`, `select`, `textarea`, `password`.

```html
<button class="btn-settings" onclick="openAppSettings()">&#9881; Settings</button>
```
```js
var _appSettings = EOS_UI.settingsPanel({
    id: 'app-settings-panel',
    title: 'App Settings',
    fields: [
        {key: 'myapp.foo', label: 'Foo', type: 'number', default: 10, hint: '...'},
    ],
});
function openAppSettings() { _appSettings.open(); }
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
// In showDetail(id):   _route.set(id);
// In hideDetail():     _route.clear();
// After initial load:  _route.init();   // reads current hash, opens detail if present
```

Handles URL encoding (paths/spaces/special chars) and `popstate` back/forward.
