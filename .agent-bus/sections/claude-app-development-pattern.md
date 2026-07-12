

An app = backend (`app.py`) + API (`@web_route`) + UI (`pages/`). **UI is not optional** — every POST implies a form, every GET list implies a table. See `docs/DESIGN.md` "UI Philosophy" and `docs/APP-DEVELOPMENT.md`.

```python
class MyApp(BaseApp):
    data = await self.read("file.md")
    result = await self.think("Analyze this", domain="code")
    await self.write("output.md", result)

    tasks = await self.call_app("task", "list_tasks")
    await self.emit("myapp:done", {"key": "value"})

    async for chunk in self.think_stream("Summarize"):
        yield chunk
```

### Manifest

```toml
[app]
id = "myapp"
name = "My App"
version = "1.0.0"
description = "What it does"

[app.entry]
module = "app"
class = "MyAppClass"

[requires]
capabilities = ["read", "write", "think"]
apps = ["task", "journal"]
optional_apps = ["weather"]  # soft integrations; absence is tolerated

[provides.cli]
commands = ["myapp"]

[provides.web]
prefix = "/myapp"

[provides.events]
emits = ["myapp:done"]
```

Declare every literal `self.call_app("id", ...)` target. Put load-time
dependencies in `apps`; put integrations with graceful fallback in
`optional_apps`.

### App Sub-Patterns

Each pattern below has a dedicated rule file with full examples + manifest snippets. Read the rule file before building.

| Pattern | When to use | Reference |
|---|---|---|
| **Settings panel** | App has `[provides.settings]` — mandatory ⚙ button + `EOS_UI.settingsPanel()` | `.claude/rules/app-ui-patterns.md` |
| **Hash-route detail view** | App has `showDetail(id)` — bookmarkable URLs + back button via `EOS_UI.hashRoute()` | `.claude/rules/app-ui-patterns.md` |
| **4D timeline panel** | App has entity detail views with a history + future → `[provides.timeline]` `entity_source = "<method>"`; auto-injected 📅 button → drawer with past/future/now via `BaseApp.timeline()` | `.claude/rules/app-ui-patterns.md` |
| **Addons** | User-extensible URL slots → `[[apps.<id>.<slot>_addons]]` config + `GET /<app>/api/<slot>/{ctx}` | `.claude/rules/addons.md` |
| **Hub panels** | Glanceable signal on `/hub/` → `[[contributes.hub.panel]]` + `panel_*` method (20 renderers, priority bands, lazy hydration) | `.claude/rules/hub-panels.md` |
| **Voice intents** | App verb callable from Aura → `[[contributes.voice-assistant.intent]]` + `voice_*` method (scope-narrowing, card renderers) | `.claude/rules/voice-intents.md` |
| **Field-suggest** | ✨ vault-grounded AI suggestions on a creative form input → `[[provides.field_suggest]]` + `data-suggest-field` attr (or formModal `suggest:` key) auto-mounts `EOS_UI.fieldSuggest`; `BaseApp.suggest_field()` grounds in `vault_query` (Rule-19 safe). Propose-not-autofill | `.claude/rules/field-suggest.md` |
| **Boards as view layer** | Render data another app owns → `source.type = "app"` + expose `list_all`/`set_field` + `SETTABLE_FIELDS` whitelist; auto-instantiated as readonly system-database boards | `.claude/rules/boards-as-view-layer.md` |
| **Standalone export** | App should run without the daemon → `[provides.export]` + optional `export.py` hook (`export_state` / `stub_routes` / `client_overrides`); one-way snapshot, no sync | `.claude/rules/app-conventions-for-export.md` |
| **Tour steps** | App should appear in the product tour → `[[contributes.tour.step]]` with `route` + `spotlight` selector + optional `requires`; capability-missing steps auto-rewrite to `/system` | `.claude/rules/tour-steps.md` |
| **Private to repo, hidden from demo** | App must never appear in a public/demo deployment → `[app] private = true`; `app_loader` skips it when `demo.enabled`, `release-public.py` aborts if it shows up in a public snapshot | `.claude/rules/demo-mode.md` |
| **Multi-CLI participants** | App spawns external coding-agent CLIs (claude-cli, codex, gemini) per @-mention → `agent-runtime` plugin's `text_cli_run` for buffered text, `claude_cli_run` for stream-json + tool events; per-CLI config in `[plugins.agent-runtime.clis.<id>]` | `.claude/rules/multi-cli-participants.md` |
| **Slash command palette** | App's text input has 4+ verbs that need keyboard-first firing → single `SLASH_COMMANDS` array, mode-aware popup state, command-owned arg parsing | `.claude/rules/slash-command-palette.md` |
| **Room review gate** | CLI / agent emits `[DO:app.method({...})]` tokens that should require Apply/Reject before executing → `_gate_server_actions` parses + persists pending; per-action card renders in chat + activity drawer + global dashboard | `.claude/rules/room-review-gate.md` |
| **Multi-module app decomposition** | `app.py` crossed ~1200L (P4 Atomic threshold) → split into helper modules (module-level fns taking `self`, decorators preserved, re-bind in class body); 5 mandatory conventions (docstring template, binding banner, `TYPE_CHECKING` guard, no helper-to-helper imports, constants travel with consumer). Tooling: `scripts/decompose_app.py` (backend), `scripts/split_page_js.py` (frontend pages). References: `apps/dogfood-agent/`, `apps/rooms/`, `apps/projects/` | `.claude/rules/multi-module-apps.md` |

Triggers: `eos app export <id>` for export bundles. Debug surfaces: `/hub/debug/panels`, `/voice-assistant/debug/intents`.
