# Boards as a View Layer — App Integration Contract

The `boards` app is a generic view+edit layer over data declared by other apps. Apps don't *belong to* boards; boards reads from them. To make an app's data renderable as a board (kanban / table / gallery / calendar / timeline / chart, with filter / sort / bulk / saved views for free), the app exposes three things.

**Reference implementations:** `apps/task/app.py`, `apps/projects/app.py`, `apps/personal/{jobs,reminders,media,expense}/app.py`, `apps/people/app.py`.

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

A preset in `apps/boards/presets.py` with `source.type = "app"`:

```python
PRESETS["job-applications"] = {
    "id": "job-applications",
    "source": {"type": "app", "app": "jobs", "method": "list_all"},
    "columns": [...],   # subset of fields list_all returns
    "views": [{"type": "kanban", "group_by": "status", "default": True}, ...],
}
```

App-sourced presets are auto-instantiated as saved boards on boards-app boot (see `BoardsApp.setup`) and default to `readonly: True` — system database views, not editable inline. The user can flip the toggle button to enable editing per-board; the choice persists.

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
