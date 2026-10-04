---
paths:
  - "apps/**/boards/**"
  - "emptyos/sdk/collection_app.py"
  - "emptyos/sdk/column_types.py"
---
# Boards as a View Layer — App Integration Contract

The `boards` app is a generic view+edit layer over data declared by other apps. Apps don't *belong to* boards; boards reads from them. To make an app's data renderable as a board (kanban / table / gallery / calendar / timeline / chart, with filter / sort / bulk / saved views for free), the app exposes three things.

**Reference implementations:** `apps/public/core/task/app.py`, `apps/public/standard/projects/app.py`, `apps/personal/{jobs,reminders,media,expense}/app.py`, `apps/public/standard/people/app.py`.

## What an app declares

### 1. `SETTABLE_FIELDS: set[str]`

Class-level whitelist of frontmatter / record fields the app promises can be flipped from outside without going through domain orchestration. Anything that needs validation, multi-step workflow, or downstream coupling stays *off* the list and remains the app page's responsibility.

```python
class JobsApp(BaseApp):
    SETTABLE_FIELDS = {"status", "salary", "match_score", "priority",
                       "recruiter", "source", "location"}
```

### 2. `async def list_all(self) -> list[dict]`

Flat list of records with stable `id` (or `file`) keys. Each row should carry only display-relevant fields — drop heavy `_vault_path`, raw HTML, body content, etc. Boards calls this via `self.call_app(target, "list_all")` when the preset has `source.type = "app"`.

```python
async def list_all(self) -> list[dict]:
    rows = []
    for a in self._read_apps():
        rows.append({
            "id": a["id"], "company": a["company"], "role": a["role"],
            "status": a["status"], "match_score": a["match_score"],
            "created": a["created"], "updated": a["updated"],
        })
    return rows
```

### 3. `async def set_field(self, id: str, field: str, value) -> dict`

Cross-app setter. Boards calls this on inline cell edits, kanban drag-drops, bulk-edit. The standard shape:

1. Reject if `field not in SETTABLE_FIELDS` → `{"error": "field 'X' not settable"}`
2. Resolve the record (return `{"error": "<App> not found"}` if missing)
3. Write through the app's normal storage path (vault_update / JSON / delete+add)
4. Emit a domain event so reactor / other apps see the change
5. Return `{"ok": True}`

The write path is intentionally *not* abstracted into `BaseApp` — each app has different storage (vault frontmatter, JSON, markdown table, VaultLibrary). Copy the shape from a similar app. Don't introduce a helper unless five+ apps share an identical write path verbatim.

```python
async def set_field(self, id: str, field: str, value) -> dict:
    if field not in self.SETTABLE_FIELDS:
        return {"error": f"field '{field}' not settable"}
    n = self._find_note(id)
    if not n:
        return {"error": "Person not found"}
    self.vault_update(n["path"], {field: value})
    await self.emit("people:updated", {"id": id, "field": field, "value": value})
    return {"ok": True}
```

## What boards declares

A preset in `apps/public/standard/boards/presets.py` with `source.type = "app"`:

```python
PRESETS["job-applications"] = {
    "id": "job-applications",
    "source": {"type": "app", "app": "jobs", "method": "list_all"},
    "columns": [...],   # subset of fields list_all returns
    "views": [{"type": "kanban", "group_by": "status", "default": True}, ...],
}
```

App-sourced presets are auto-instantiated as saved boards on boards-app boot (see `BoardsApp.setup`) and default to `readonly: True` — system database views, not editable inline. The user can flip the toggle button to enable editing per-board; the choice persists.

**A `vault_tag`-sourced preset auto-materializes too, but only via manifest contribution — never the built-in `PRESETS` dict.** `_sync_presets` (2026-08-22) tracks which preset ids came from `call_contributions("boards", "preset")` and auto-saves those regardless of `source.type`; the static `PRESETS` templates (`crm-pipeline`, `bug-tracker`, `personal-habits`, ...) stay template-gallery-only so a fresh user doesn't get a sidebar full of boards for domains they don't use. A contributed `vault_tag` board is **not** forced `readonly` — there's no source app to route writes through, so it's editable like any user-created `vault_tag` board. First consumer: `emptyos/sdk/collection_app.py`'s `CollectionApp.board_presets()` — a schema-driven app that owns its own vault-tagged notes gets a live, editable board for free. Test: `tests/test_unit_boards_preset_sync.py`.

**`board_presets()` now reaches relational + computed columns, not just scalars (2026-08-22).** Its field-projection whitelist used to drop `link-record`'s `target_board`/`multi`/`inverse` and `rollup`'s `source_link`/`target_field`/`agg` — the column still rendered as the right `type`, but the underlying boards machinery (`links.py`'s inverse maintenance, `board_engine.py`'s `evaluate_formulas`) silently never activated, because it never saw those keys. Fixed at the single point every collection-app schema passes through, so any app already declaring these fields benefits without a code change. The grill `collection-app` recipe now extracts `link-record`/`rollup` fields too (one-directional links only — no reciprocal-field auto-authoring; see `apps/extension/dev/grill/app.py`'s `_collection_handoff_frontmatter`), and `CollectionLibrary` write/read paths call `boards`' `set_link_field`/`evaluate_collection_items` (both in `links.py`) so a schema-driven app's own native CRUD gets the same reciprocal-inverse write-through and live rollup evaluation a boards-native write already had — `boards` is a soft (`optional_apps`) dependency, degrading gracefully when absent. `eos-components.js`'s `formHtml`/`formValues` gained a `link-record` picker widget and a read-only `rollup`/`formula` display to match. Tests: `tests/test_sdk_column_types.py`, `tests/test_sdk_collection_app.py`.

**A public Form view exists now (2026-08-22).** `apps/public/standard/boards/public_form.py` + `pages/form.html` — an anonymous, no-login submission form generic over any board's fillable columns (`link-record`/`rollup`/`formula` excluded, matching Airtable's own Form-view restriction). `GET /boards/f/<id>` serves the page, `GET/POST /boards/api/public/{schema,submit}/<id>` are the `public_routes`-exempt endpoints. Reuses `_create_item_from_fields` (no parallel writer) and the shared `columns_to_form_fields` mapper (`emptyos/sdk/column_types.py` — also used by `emptyos/web/auto_ui.py`'s authenticated add-form, so the two never drift on field shape).

### Two ways to register a preset

| Path | Where the preset lives | Use when |
|---|---|---|
| **Built-in** | a `PRESETS[...]` entry in `apps/public/standard/boards/presets.py` | the board ships with the public boards app (generic, community presets) |
| **Manifest contribution** | the contributing app's own manifest + a method returning the preset dict(s) | a board belongs to one app — especially a **personal/gitignored** app that must not put a reference into the public `boards` code |

The contribution path mirrors hub panels: declare `[[contributes.boards.preset]]` with a `method`, and the app's method returns a preset dict (or a list of them). `BoardsApp.setup` merges these via `call_contributions("boards", "preset")` into the same auto-instantiation loop as the built-in `PRESETS`, fail-soft. boards holds zero per-app knowledge.

```toml
# in the contributing app's manifest.toml
[[contributes.boards.preset]]
id = "myapp-boards"
method = "board_presets"   # returns a preset dict or list of dicts
```

Reference: `apps/personal/nest/` (residences + rooms boards via one `board_presets` method) — a personal app contributing boards without touching public code.

## Contract clarifications

### `id` resolution

Boards uses `item.get("id") or item.get("file")` as the row key. New apps should put a stable `id` on every row in `list_all`. Vault-backed apps may use the filename as `id` for symmetry with the `vault_tag` source type — both work.

### Delete from a board

Today there is no UI for deleting items from an app-sourced board. The boards `DELETE /api/boards/{id}/items/{file}` endpoint only sets `status = "Archived"` on vault notes — it doesn't reach app `set_field`. If you want delete-from-board on an app-sourced board, add `"Archived"` to the column's `options`, treat that value as a delete in your `set_field`, and document the convention next to your `SETTABLE_FIELDS`.

### Create from a board

App-sourced boards disable "+ Add Item" (the `.board-edit-only` button is gated by source type). Items are created in the source app's own UI/CLI and surface in the board on next reload. An `add_item(payload)` contract for app-sourced create-from-board is **not yet defined** — defer adding it until two apps need it.

### Embed mode

Other apps can iframe a single board view inline via `/boards/?id=<board>&embed=1`, which hides the boards-app chrome (sidebar nav, topbar, detail pane, global EOS nav) and renders only the view. Add `&chrome=0` to also hide the view-tabs row, `&readonly=1` to force read-only at boot regardless of board config. Use this for "advanced view" panels inside an app's own page; for full-rich-view navigation, keep using a plain link to `/boards/?id=<board>` (see the `⊞ Board view` link pattern in `apps/personal/reminders/pages/index.html`). Both forms feature-detect via `GET /boards/api/boards/<id>` so an uninstalled boards app leaves no dead UI.

### Failure modes

If the source app is uninstalled or fails to load, `DynamicBoardLibrary.get_items()` returns `[]` and sets `_source_error`. The frontend reads `GET /boards/api/boards/<id>/source-status` and renders a red banner explaining which app is missing. Apps wiring up `set_field` don't need to defend against missing-app scenarios — that's handled in the engine layer.

## Division of labor

| App page does | Boards does |
|---|---|
| Vertical: one record, deep — domain workflows, AI features, capability-bound surfaces (`speak`, `listen`, `think`) | Horizontal: N records — filter, sort, group, bulk, saved views |
| Single-record CRUD with custom widgets (cover picker, audio recorder, SRS rating) | Generic CRUD across many records |
| Things tied to capabilities or events | Pure data presentation + edit |

The `set_field` whitelist is the explicit boundary: fields *on* the list are safe to flip from anywhere; fields *off* it require the app's own form/workflow.

## Adding a new view type

If you add a new view type to boards (e.g. the `gallery` cover-grid was added 2026-04), every integrated app gets it free — no app code changes. The render function reads view config + columns; the data already flows from `list_all`.

## When NOT to integrate

Skip the contract if:
- The app is stream-shaped (journal entries, syslog, billing events) — boards doesn't help.
- The app is single-shot (search, voice-assistant, focus timer) — no collection to render.
- The data is too unstructured (raw markdown notes without frontmatter conventions).
- The app is a generator/output (publish, podcast, music-studio) — its work isn't a queryable list.

These are the bulk of EmptyOS apps. Integration is opt-in based on whether the data is naturally a list-of-records.
