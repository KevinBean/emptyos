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

    # Collab sidecars — comments in full, activity tail-capped, attachments as
    # name lists only (bytes are never bundled; the offline UI shows names
    # with a "not bundled" note).
    comments: dict[str, dict] = {}
    activity: dict[str, dict] = {}
    attachments: dict[str, dict] = {}
    for b in boards:
        bid = b["id"]
        for it in items.get(bid, []):
            key = it.get("file") or it.get("id")
            if not key:
                continue
            try:
                thread = app._load_comments(bid, key)["comments"]
                if thread:
                    comments.setdefault(bid, {})[key] = thread
            except Exception:
                pass
            try:
                acts = app._read_activity(bid, key, limit=30)
                if acts:
                    activity.setdefault(bid, {})[key] = acts
            except Exception:
                pass
            try:
                d = app._attachments_dir(bid, key)
                if d is not None and d.exists():
                    names = sorted(p.name for p in d.iterdir() if p.is_file())
                    if names:
                        attachments.setdefault(bid, {})[key] = names
            except Exception:
                pass

    return {
        "boards": boards,
        "items": items,
        "presets": list_presets(),
        "presets_full": presets_full,
        "comments": comments,
        "activity": activity,
        "attachments": attachments,
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
    if (found) {
      window.EOS_EXPORT.emit('board:item_updated', { board: params.id, file: params.file });
      _logAct(params.id, params.file, 'board:item_updated', { updates: updates });
    }
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

  // ── Collab sidecars (comments / activity / attachments) ────
  function _sidecar(kind, boardId, key) {
    var state = window.EOS_EXPORT_DATA || {};
    state[kind] = state[kind] || {};
    state[kind][boardId] = state[kind][boardId] || {};
    return state[kind][boardId][key] || [];
  }
  async function _setSidecar(kind, boardId, key, list) {
    var state = window.EOS_EXPORT_DATA || {};
    state[kind] = state[kind] || {};
    state[kind][boardId] = state[kind][boardId] || {};
    state[kind][boardId][key] = list;
    await window.EOS_EXPORT.set('/boards/_sidecar/' + kind + '/' + boardId + '/' + key, list);
    window.EOS_EXPORT_DATA = state;
  }
  function _logAct(boardId, key, type, extra) {
    var list = _sidecar('activity', boardId, key).slice();
    var entry = Object.assign({ts: new Date().toISOString().slice(0,19), file: key, type: type, actor: 'me'}, extra || {});
    list.unshift(entry);
    _setSidecar('activity', boardId, key, list.slice(0, 100));
  }

  window.EOS_EXPORT.registerRoute('GET', '/boards/api/boards/:id/items/:file/comments', async function(req, params) {
    return { comments: _sidecar('comments', params.id, params.file) };
  });
  window.EOS_EXPORT.registerRoute('POST', '/boards/api/boards/:id/items/:file/comments', async function(req, params) {
    var text = String((req.body || {}).text || '').trim();
    if (!text) return { error: 'empty comment' };
    var list = _sidecar('comments', params.id, params.file).slice();
    var c = {
      id: 'c-' + Math.random().toString(16).slice(2, 12),
      author: (req.body || {}).author || 'me',
      text: text,
      created: new Date().toISOString().slice(0, 19),
      edited: null,
    };
    list.push(c);
    await _setSidecar('comments', params.id, params.file, list);
    _logAct(params.id, params.file, 'board:comment_added', {comment_id: c.id});
    return { ok: true, comment: c };
  });
  window.EOS_EXPORT.registerRoute('PATCH', '/boards/api/boards/:id/items/:file/comments/:cid', async function(req, params) {
    var text = String((req.body || {}).text || '').trim();
    if (!text) return { error: 'empty comment' };
    var list = _sidecar('comments', params.id, params.file).slice();
    var c = null;
    for (var i = 0; i < list.length; i++) if (list[i].id === params.cid) { c = list[i]; break; }
    if (!c) return { error: 'comment not found' };
    c.text = text;
    c.edited = new Date().toISOString().slice(0, 19);
    await _setSidecar('comments', params.id, params.file, list);
    return { ok: true, comment: c };
  });
  window.EOS_EXPORT.registerRoute('DELETE', '/boards/api/boards/:id/items/:file/comments/:cid', async function(req, params) {
    var list = _sidecar('comments', params.id, params.file).filter(function(c){ return c.id !== params.cid; });
    await _setSidecar('comments', params.id, params.file, list);
    _logAct(params.id, params.file, 'board:comment_deleted', {comment_id: params.cid});
    return { ok: true };
  });

  window.EOS_EXPORT.registerRoute('GET', '/boards/api/boards/:id/items/:file/activity', async function(req, params) {
    var events = _sidecar('activity', params.id, params.file).map(function(e) {
      return { type: e.type, timestamp: e.ts || e.timestamp, actor: e.actor || '', updates: e.updates || {}, data: e };
    });
    return { events: events };
  });

  window.EOS_EXPORT.registerRoute('GET', '/boards/api/boards/:id/items/:file/attachments', async function(req, params) {
    // Name list only — bytes are never bundled into an export.
    var names = _sidecar('attachments', params.id, params.file);
    return { attachments: names.map(function(n){ return {name: n, size: 0, url: ''}; }) };
  });
  window.EOS_EXPORT.registerRoute('GET', '/boards/api/boards/:id/attachments-index', async function(req, params) {
    var state = window.EOS_EXPORT_DATA || {};
    var byItem = (state.attachments || {})[params.id] || {};
    var counts = {};
    Object.keys(byItem).forEach(function(k){ counts[k] = (byItem[k] || []).length; });
    return { counts: counts };
  });

  window.EOS_EXPORT.registerRoute('GET', '/boards/api/me', async function() {
    return { me: 'me', me_person: '' };
  });

  // ── Planner import — same canonical apply route as the live daemon ──
  // (people degrade to raw name strings in a single-app offline bundle).
  window.EOS_EXPORT.registerRoute('POST', '/boards/api/boards/:id/planner/apply', async function(req, params) {
    var state = window.EOS_EXPORT_DATA || {};
    state.items = state.items || {};
    state.boards = state.boards || [];
    state.presets_full = state.presets_full || {};
    var body = req.body || {};
    var records = body.records || [];
    if (!records.length) return { error: 'no records' };
    var options = body.options || {};

    if (!state.boards.some(function(b){ return b.id === params.id; })) {
      if (!options.create_board) return { error: 'Board not found (pass options.create_board to create it)' };
      // Columns from EOS_PLANNER_MAP — same single source as the server.
      var map = window.EOS_PLANNER_MAP || { fields: {}, export_order: [] };
      var cols = [];
      (map.export_order || []).forEach(function(f) {
        var spec = map.fields[f] || {};
        if (spec.meta || spec.body || !spec.col) return;
        cols.push(JSON.parse(JSON.stringify(spec.col)));
      });
      var config = {
        id: params.id,
        name: options.board_name || options.plan_name || params.id,
        description: 'Imported from Planner',
        source_tag: params.id,
        columns: cols,
        views: [{type:'table', default:true}, {type:'kanban', group_by:'bucket'}, {type:'calendar', date_field:'due_date'}],
        kanban_group_by: 'bucket',
      };
      state.boards.push({ id: config.id, name: config.name, description: config.description });
      state.presets_full[config.id] = config;
      state.items[config.id] = [];
      await window.EOS_EXPORT.set('/boards/api/boards', state.boards);
    }

    var list = state.items[params.id] || [];
    var byPid = {}, byName = {};
    list.forEach(function(it) {
      if (it.planner_id) byPid[String(it.planner_id)] = it;
      var n = String(it.name || '').trim().toLowerCase();
      if (n && !byName[n]) byName[n] = it;
    });
    var created = 0, updated = 0, unchanged = 0;
    records.forEach(function(rec) {
      if (!rec || (!rec.name && !rec.planner_id)) return;
      var existing = (rec.planner_id && byPid[String(rec.planner_id)]) ||
                     byName[String(rec.name || '').trim().toLowerCase()] || null;
      if (!existing) {
        var slug = slugify(rec.name || rec.planner_id || 'item');
        var item = Object.assign({}, rec, {
          file: slug + '.md',
          path: '(offline)/' + slug + '.md',
          tags: [params.id],
        });
        delete item.body;
        list.push(item);
        if (item.planner_id) byPid[String(item.planner_id)] = item;
        created++;
      } else {
        var changed = false;
        Object.keys(rec).forEach(function(k) {
          if (k === 'body') return;
          var a = JSON.stringify(existing[k] === undefined || existing[k] === null ? '' : existing[k]);
          var b = JSON.stringify(rec[k] === undefined || rec[k] === null ? '' : rec[k]);
          if (a !== b) { existing[k] = rec[k]; changed = true; }
        });
        if (changed) updated++; else unchanged++;
      }
    });
    state.items[params.id] = list;
    await window.EOS_EXPORT.set('/boards/api/boards/' + params.id + '/items', list);
    window.EOS_EXPORT_DATA = state;
    window.EOS_EXPORT.emit('board:planner_imported', { board: params.id, created: created, updated: updated });
    return { ok: true, board: params.id, created: created, updated: updated, unchanged: unchanged, people_created: [] };
  });

  // ── Live export button is a no-op in export mode ───────────
  window.exportBoard = function() {
    if (window.EOS_UI && window.EOS_UI.toast) {
      window.EOS_UI.toast('Already in offline mode — open the 🔒 pill for settings', true);
    }
  };
})();
"""
