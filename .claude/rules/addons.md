# Addons Rule — Config-Driven Extension Points in Apps

An **addon slot** is a place where an app renders user-provided entries — typically external-site buttons (`YouGlish`, `Forvo`) or URL templates — without hardcoding them. Addons are the "add features without touching app code" mechanism.

**First reference implementation:** `apps/dictionary/` `word_addons` slot (`apps/dictionary/app.py` `api_word_addons` + `apps/dictionary/pages/index.html` fetch-and-render).

## Principles

1. **Addons are data, not code** — a row in `emptyos.toml`, not a file in `apps/`. No import, no class, no registry.
2. **The app owns the slot, not the addons** — dictionary defines "we render word_addons on the lookup card"; it knows nothing about YouGlish/Forvo/etc.
3. **No built-in defaults in the app** — if the user hasn't configured addons, nothing appears. Keeps `apps/` shippable as generic + user config as personal.
4. **Rule 14 — label = verb, never brand** — the config's `label` field is shown verbatim; the user is free to write brand names in *their* config, but the codebase must not.
5. **Graduate when an addon needs logic** — if an addon must parse results, emit events, or log to another app, it becomes its own app (may still contribute a URL via the same config).

## Convention (until we have 2+ slots and extract to SDK)

### Config shape

```toml
[[apps.<app_id>.<slot>_addons]]
id = "unique-id"
label = "Verb shown on button"
icon = "🎧"          # optional
url_template = "https://site.com/...{word}..."
```

### Backend route

Every app with an addon slot exposes:

```python
@web_route("GET", "/api/<slot>/{ctx}")
async def api_slot(self, request):
    ctx = (request.path_params.get("ctx") or "").strip()
    if not ctx:
        return {"addons": []}
    raw = self.app_config(f"{slot}_addons", []) or []
    from urllib.parse import quote
    addons = []
    for item in raw:
        if not isinstance(item, dict): continue
        tmpl = item.get("url_template") or ""
        if not tmpl: continue
        addons.append({
            "id": item.get("id") or item.get("label") or "addon",
            "label": item.get("label") or item.get("id") or "Open",
            "icon": item.get("icon") or "",
            "url": tmpl.replace("{" + "ctx_var" + "}", quote(ctx, safe="")),
        })
    return {"addons": addons}
```

Substitute `ctx_var` with the actual template variable name (e.g. `word`, `place`, `query`).

### UI render

Fetch after the main content renders; append to an action row; never block primary content on it. Button uses `window.open(url, '_blank', 'noopener')`.

## Graduation: SDK helper (when 2nd app adds a slot)

Extract to `emptyos/sdk/base_app.py`:

```python
def resolve_addons(self, slot: str, **ctx) -> list[dict]:
    """Read [[apps.<id>.<slot>_addons]], substitute ctx into url_template, return normalized list."""
    from urllib.parse import quote
    raw = self.app_config(f"{slot}_addons", []) or []
    out = []
    for item in raw:
        if not isinstance(item, dict): continue
        tmpl = item.get("url_template") or ""
        if not tmpl: continue
        url = tmpl
        for k, v in ctx.items():
            url = url.replace("{" + k + "}", quote(str(v), safe=""))
        out.append({
            "id": item.get("id") or item.get("label") or "addon",
            "label": item.get("label") or "Open",
            "icon": item.get("icon") or "",
            "url": url,
        })
    return out
```

App routes collapse to one line: `return {"addons": self.resolve_addons("word", word=word)}`.

## Graduation: Manifest `[contributes]` (when addons need to ship as code)

If an addon is complex enough to be a real app — custom parsing, event emission, cross-app wiring — it becomes its own app and registers via manifest:

```toml
[contributes.dictionary.word_addons]
id = "forvo"
label = "Native speaker audio"
icon = "🔊"
url_template = "https://forvo.com/word/{word}/"
```

Platform at boot reads every app's manifest, aggregates `[contributes.<target_app>.<slot>]` entries, merges with config-based ones. Not built yet — build when the first code-based addon lands.

## When NOT to use an addon slot

- If the feature is part of the app's core lifecycle (e.g. dictionary's "Save to Vault" button), it's not an addon, it's a feature.
- If the feature is app-to-app wiring via events (e.g. dictionary → shadowing), use the event bus, not addons.
- If the user will never add a second instance (single-purpose integration), just hardcode it — the abstraction costs more than it saves.
