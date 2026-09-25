

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
emits = ["myapp:done", "myapp:progress"]
internal = ["myapp:progress"]   # subset of emits — no cross-app listener by design
```

Declare every literal `self.call_app("id", ...)` target: load-time dependencies in `apps`, graceful-fallback integrations in `optional_apps`. Declare every event the code emits — the topology graph builds edges from manifests, so an undeclared emit is invisible to `/api/topology`. **`internal` modifies `emits`, it is not an alternative**: a name listed only under `internal` produces no node and no edge. `scripts/check_event_wiring.py` reports drift.

### App Sub-Patterns

Each has a rule file with full examples; read it before building. Most load automatically when you touch `apps/**`.

| Pattern | When | Reference |
|---|---|---|
| Settings panel | `[provides.settings]` → ⚙ button + `EOS_UI.settingsPanel()`; read back with `setting_or_config` | `app-ui-patterns.md` |
| Hash-route detail view | `showDetail(id)` → `EOS_UI.hashRoute()` | `app-ui-patterns.md` |
| 4D timeline panel | entity detail with past + future → `[provides.timeline]` | `app-ui-patterns.md` |
| Addons | user-extensible URL slots | `addons.md` |
| Hub panels | glanceable signal on `/hub/` | `hub-panels.md` |
| Voice intents | verb callable from Aura | `voice-intents.md`, `verb-registry.md` |
| Field-suggest | ✨ vault-grounded suggestions on a form input; propose-not-autofill | `field-suggest.md` |
| Boards as view layer | render data another app owns | `boards-as-view-layer.md` |
| App report | calculation sheet / study record as markdown → `[provides.report]` (not data exports) | `app-reports.md` |
| Standalone export | run without the daemon → `[provides.export]` | `app-conventions-for-export.md` |
| Tour steps | appear in the product tour | `tour-steps.md` |
| Private to repo | never in a public/demo deployment → `[app] private = true` | `demo-mode.md` |
| Multi-CLI participants | spawn claude-cli/codex/gemini per @-mention | `multi-cli-participants.md` |
| Slash command palette | 4+ keyboard-first verbs in one input | `slash-command-palette.md` |
| Room review gate | `[DO:app.method(...)]` tokens needing Apply/Reject | `room-review-gate.md`, `proposed-action.md` |
| Multi-module decomposition | `app.py` past ~1200L → helper modules + bindings | `multi-module-apps.md` |

(All paths under `.claude/rules/`.) Debug surfaces: `/hub/debug/panels`, `/voice-assistant/debug/intents`.
