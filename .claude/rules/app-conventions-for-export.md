# App conventions that make export painless

These are **defaults**, not mandates. Apps written today work in exported bundles with zero changes thanks to the fetch interceptor in `emptyos/web/static/eos-export-shim.js`. But three small patterns — adopted from the start — make every app cheaper to export, cheaper to test, and honest about what it can and can't do offline.

If you're writing a new app, do these. If you're touching an existing app and it's easy, do these. Don't rewrite working code to adopt them.

## 1. Render from state, not from response

Bad (common today):
```js
async function loadItems() {
    var res = await fetch('/myapp/api/items');
    var items = await res.json();
    document.getElementById('list').innerHTML = items.map(renderItem).join('');
}
```

Good:
```js
var STATE = { items: [] };
async function loadItems() {
    STATE.items = await fetch('/myapp/api/items').then(r => r.json());
    render();
}
function render() {
    document.getElementById('list').innerHTML = STATE.items.map(renderItem).join('');
}
```

Why: `render()` is a pure function of `STATE`. Export mode pre-populates `STATE` from `window.EOS_EXPORT_DATA` and calls `render()` directly — no round-trip through the fake-fetch interceptor. Portfolio demos seed `STATE` from a JSON fixture. Tests inject `STATE` and assert DOM. One pattern, three wins.

## 2. Gate daemon-only features with `data-online-only`

Some features have no meaningful offline fallback: pushing to an external webhook, triggering a capability that needs the kernel (e.g. `self.speak()` via a local TTS service), talking to another app that isn't bundled. Mark them:

```html
<button data-online-only onclick="pushToExternalService()">Send to webhook</button>
<section data-online-only>
    <h3>Live system log</h3>
    <div id="live-log"></div>
</section>
```

The shim dims every `[data-online-only]` in export mode and adds a tooltip ("requires the EmptyOS daemon"). The app doesn't need export-mode branches — it just needs to be honest about which parts are live-only.

## 3. `@web_route` is the RPC surface — no parallel internal methods

When app A calls app B, use `self.call_app("B", "method_name")` where `method_name` is an `@web_route`-decorated method on B. Don't create internal-only helper methods that other apps reach into.

Why: the Phase-3 export-groups builder walks every `@web_route` on bundled apps and auto-generates in-browser RPC handlers. Methods decorated with `@web_route` become callable across bundled apps in an exported bundle — internal methods do not. One decorator = live HTTP + in-browser RPC = one thing to keep consistent.

If a method is intentionally private (an implementation detail), keep it private (no decorator, leading underscore). Cross-app callers must go through the public `@web_route` API.

## The one hard principle

**Export is a one-way snapshot. There is no sync.** Writes in an exported bundle persist in that bundle's IndexedDB and never replay back to the daemon. If a feature needs true two-way sync, it's a different mode (`mode = "synced-pwa"`) and is not built today. Don't design UIs around "I'll edit in export and it'll merge later" — it won't.
