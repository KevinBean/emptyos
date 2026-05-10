"""Boards — export hook.

Invoked by ``emptyos.sdk.exporter.AppExporter`` when building a standalone
bundle. Provides three exports:

- ``export_state(app)`` — snapshot current boards + items + presets into
  ``_data/state.json``. Loaded at runtime as ``window.EOS_EXPORT_DATA``.

- ``stub_routes()`` — tell the export shim which GET endpoints are backed by
  the snapshot. The shim's fetch interceptor serves these directly from
  ``EOS_EXPORT_DATA`` without the app needing custom JS.

- ``client_overrides()`` — JavaScript registered into the export shim that
  handles writes (POST/PATCH/DELETE) against IndexedDB. The boards UI keeps
  working with the same ``fetch('/boards/api/...')`` calls it uses online.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import BoardsApp


async def export_state(app: BoardsApp) -> dict:
    from .board_engine import DynamicBoardLibrary
    from .presets import get_preset, list_presets

    boards = app._store.list_boards()
    items: dict[str, list] = {}
    for b in boards:
        config = app._store.get_board(b["id"]) or {}
        if config:
            try:
                lib = DynamicBoardLibrary(app, config)
                items[b["id"]] = lib.list_filtered()
            except Exception:
                items[b["id"]] = []

    # Surface each preset by id too, so exported bundles can render the
    # "Create from Preset" grid (the live site shows these alongside real boards).
    presets_full = {p["id"]: get_preset(p["id"]) for p in list_presets()}

    return {
        "boards": boards,
        "items": items,
        "presets": list_presets(),
        "presets_full": presets_full,
    }


def stub_routes() -> dict:
    """GET endpoints whose response is derivable from the snapshot.

    String values resolve to a single path into ``state``.
    Dict values assemble a response from multiple paths (mirror the live API).
    The shim replaces ``$id``-style placeholders with URL-captured params.
    """
    return {
        # Home: live API returns {boards: [...], presets: [...]} — mirror it.
        "GET /boards/api/boards": {
            "boards": "state.boards",
            "presets": "state.presets",
        },
        "GET /boards/api/boards/:id": "state.presets_full[$id]",
        "GET /boards/api/boards/:id/items": "state.items[$id]",
        "GET /boards/api/presets": {"presets": "state.presets"},
    }


def client_overrides() -> str:
    """Write-path handlers + UX degradations for the exported boards UI."""
    return r"""
// Boards export — write handlers against IndexedDB.
(function(){
  if (!window.EOS_EXPORT) return;

  function slugify(s) {
    return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'item';
  }

  // ── Create item ─────────────────────────────────────────────
  window.EOS_EXPORT.registerRoute('POST', '/boards/api/boards/:id/items', async function(req, params) {
    var state = window.EOS_EXPORT_DATA || {};
    state.items = state.items || {};
    var list = state.items[params.id] || [];
    var body = req.body || {};
    var nameField = body.name || body.title || ('item-' + Date.now());
    var slug = slugify(nameField);
    var now = new Date().toISOString().slice(0, 10);
    var item = Object.assign({}, body, {
      file: slug + '.md',
      path: '(offline)/' + slug + '.md',
      created: now,
      tags: [params.id],
    });
    list.push(item);
    state.items[params.id] = list;
    await window.EOS_EXPORT.set('/boards/api/boards/' + params.id + '/items', list);
    window.EOS_EXPORT_DATA = state;
    window.EOS_EXPORT.emit('board:item_created', { board: params.id, file: item.file });
    return { ok: true, file: item.file, path: item.path };
  });

  // ── Update item (inline edit) ──────────────────────────────
  window.EOS_EXPORT.registerRoute('PATCH', '/boards/api/boards/:id/items/:file', async function(req, params) {
    var state = window.EOS_EXPORT_DATA || {};
    state.items = state.items || {};
    var list = state.items[params.id] || [];
    var updates = (req.body && req.body.updates) || req.body || {};
    var found = null;
    for (var i = 0; i < list.length; i++) {
      if (list[i].file === params.file) {
        list[i] = Object.assign({}, list[i], updates);
        found = list[i]; break;
      }
    }
    state.items[params.id] = list;
    await window.EOS_EXPORT.set('/boards/api/boards/' + params.id + '/items', list);
    window.EOS_EXPORT_DATA = state;
    if (found) window.EOS_EXPORT.emit('board:item_updated', { board: params.id, file: params.file });
    return found ? { ok: true } : { error: 'Item not found' };
  });

  // ── Archive item ───────────────────────────────────────────
  window.EOS_EXPORT.registerRoute('DELETE', '/boards/api/boards/:id/items/:file', async function(req, params) {
    var state = window.EOS_EXPORT_DATA || {};
    state.items = state.items || {};
    var list = state.items[params.id] || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].file === params.file) {
        list[i].status = 'Archived'; break;
      }
    }
    state.items[params.id] = list;
    await window.EOS_EXPORT.set('/boards/api/boards/' + params.id + '/items', list);
    window.EOS_EXPORT_DATA = state;
    window.EOS_EXPORT.emit('board:item_archived', { board: params.id, file: params.file });
    return { ok: true };
  });

  // ── Create board from preset ───────────────────────────────
  window.EOS_EXPORT.registerRoute('POST', '/boards/api/boards', async function(req) {
    var state = window.EOS_EXPORT_DATA || {};
    state.boards = state.boards || [];
    state.items = state.items || {};
    var body = req.body || {};
    var preset_id = body.preset || '';
    var config;
    if (preset_id) {
      var full = (state.presets_full || {})[preset_id];
      if (!full) return { error: 'Unknown preset: ' + preset_id };
      config = Object.assign({}, full);
      if (body.id) config.id = body.id;
      if (body.name) config.name = body.name;
    } else {
      if (!body.id) return { error: 'Board ID is required' };
      config = {
        id: body.id,
        name: body.name || body.id,
        description: body.description || '',
        source_tag: body.source_tag || body.id,
        tags: ['board-config'],
        columns: body.columns || [
          { id: 'name', label: 'Name', type: 'text' },
          { id: 'status', label: 'Status', type: 'select', options: ['To Do','In Progress','Done'] },
        ],
        views: body.views || [{ type: 'table', default: true }, { type: 'kanban', group_by: 'status' }],
        kanban_group_by: 'status',
      };
    }
    state.boards.push({ id: config.id, name: config.name, description: config.description || '' });
    state.items[config.id] = [];
    state.presets_full = state.presets_full || {};
    state.presets_full[config.id] = config;
    await window.EOS_EXPORT.set('/boards/api/boards', state.boards);
    await window.EOS_EXPORT.set('/boards/api/boards/' + config.id + '/items', []);
    window.EOS_EXPORT_DATA = state;
    window.EOS_EXPORT.emit('board:created', { id: config.id, name: config.name });
    return { ok: true, id: config.id, name: config.name };
  });

  // ── Live export button is a no-op in export mode ───────────
  window.exportBoard = function() {
    if (window.EOS_UI && window.EOS_UI.toast) {
      window.EOS_UI.toast('Already in offline mode — open the 🔒 pill for settings', true);
    }
  };
})();
"""
