// eos-draft-store.js - shared-state spine for the 2D Draft layout.
//
// Draft uses eos-draft/1, not eos-cad/1. This store mirrors the CAD layout
// store contract closely enough for layout-host views to share one source of
// truth without smuggling state through pane globals.

import { createHistory } from './eos-history.js';

export function emptyDraftDoc() {
  return {
    schema: 'eos-draft/1',
    id: 'draft-proposal',
    name: 'Untitled drawing',
    units: 'mm',
    current_layer: '0',
    dim_style: { text_height: 3.5, arrow_size: 2.5, precision: 1, ext_gap: 1.5, ext_overshoot: 1.5 },
    sheet: {
      paper: 'A3',
      orientation: 'landscape',
      scale: 'NTS',
      fields: { project: '', title: '', drawing_no: '', revision: 'A', date: '', author: '', client: '' },
    },
    layers: [{ name: '0', color: '#e8e8e8', linetype: 'continuous', lineweight: 0.25, visible: true, locked: false, frozen: false }],
    entities: [],
    blocks: {},
    ext: {},
  };
}

function clone(x) {
  return typeof structuredClone === 'function'
    ? structuredClone(x)
    : JSON.parse(JSON.stringify(x));
}

function normalizeDraftDoc(doc) {
  const d = (doc && typeof doc === 'object') ? doc : emptyDraftDoc();
  d.schema = d.schema || 'eos-draft/1';
  d.id = d.id || 'draft-proposal';
  d.name = d.name || 'Untitled drawing';
  d.units = d.units || 'mm';
  d.current_layer = d.current_layer || '0';
  d.dim_style = d.dim_style || emptyDraftDoc().dim_style;
  d.sheet = d.sheet || emptyDraftDoc().sheet;
  d.layers = Array.isArray(d.layers) && d.layers.length ? d.layers : emptyDraftDoc().layers;
  d.entities = Array.isArray(d.entities) ? d.entities : [];
  d.blocks = (d.blocks && typeof d.blocks === 'object') ? d.blocks : {};
  d.ext = (d.ext && typeof d.ext === 'object') ? d.ext : {};
  return d;
}

function normalizeSelection(ids) {
  if (!ids) return [];
  if (ids instanceof Set) return Array.from(ids).filter(Boolean);
  if (Array.isArray(ids)) return ids.filter(Boolean);
  if (typeof ids === 'string') return ids ? [ids] : [];
  return [];
}

export function createDraftStore(initialDoc, meta) {
  const state = {
    doc: normalizeDraftDoc(initialDoc),
    selection: [],
    tool: null,
    view: { cx: 210, cy: 148, scale: 2 },
    paper: (meta && meta.paper) || null,
    titleblock: (meta && meta.titleblock) || [],
    vaultPath: (meta && meta.vaultPath) || '',
    status: { message: '', isError: false },
    dirty: false,
  };
  const subs = new Set();

  function emit(evt) {
    for (const s of Array.from(subs)) {
      if (s.mask && !s.mask.has(evt.type)) continue;
      try { s.fn(evt); }
      catch (e) {
        if (typeof console !== 'undefined' && console.warn) console.warn('[draft-store] subscriber failed', evt, e);
      }
    }
  }

  // The snapshot field set MUST match draft-workspace.js's undoSnapshot (name /
  // units / dim_style included), so the view's stack and this store's stack —
  // both fed on every edit in layout-host mode — never restore different fields.
  function snapshot() {
    return clone({
      name: state.doc.name,
      units: state.doc.units,
      current_layer: state.doc.current_layer,
      dim_style: state.doc.dim_style,
      sheet: state.doc.sheet || {},
      layers: state.doc.layers,
      entities: state.doc.entities,
      blocks: state.doc.blocks || {},
    });
  }

  function applySnapshot(snap) {
    if (!snap) return false;
    const base = emptyDraftDoc();
    state.doc.name = snap.name || base.name;
    state.doc.units = snap.units || base.units;
    state.doc.current_layer = snap.current_layer || '0';
    state.doc.dim_style = clone(snap.dim_style || base.dim_style);
    state.doc.sheet = clone(snap.sheet || base.sheet);
    state.doc.layers = clone(snap.layers || base.layers);
    state.doc.entities = clone(snap.entities || []);
    state.doc.blocks = clone(snap.blocks || {});
    state.selection = [];
    state.dirty = true;
    emit({ type: 'doc', reason: 'history' });
    emit({ type: 'select', ids: [] });
    emit({ type: 'dirty', dirty: true });
    return true;
  }

  // Undo/redo — draft snapshots a field subset (see snapshot() — kept in sync with
  // draft-workspace.js) and restores in place via applySnapshot. Stack mechanics
  // shared with the cad store via eos-history.js.
  const history = createHistory({
    snapshot,
    restore: applySnapshot,
    limit: 100,
    onChange: ({ canUndo, canRedo }) => emit({ type: 'history', canUndo, canRedo }),
  });

  return {
    get doc() { return state.doc; },
    get selection() { return state.selection.slice(); },
    get tool() { return state.tool; },
    get view() { return { ...state.view }; },
    get paper() { return state.paper ? state.paper.slice() : null; },
    get titleblock() { return state.titleblock; },
    get vaultPath() { return state.vaultPath; },
    get status() { return { ...state.status }; },
    get dirty() { return state.dirty; },
    get canUndo() { return history.canUndo; },
    get canRedo() { return history.canRedo; },

    subscribe(fn, types) {
      const s = { fn, mask: (types && types.length) ? new Set(types) : null };
      subs.add(s);
      return () => subs.delete(s);
    },

    setDoc(doc, nextMeta) {
      state.doc = normalizeDraftDoc(doc);
      state.selection = [];
      if (nextMeta && Object.prototype.hasOwnProperty.call(nextMeta, 'paper')) state.paper = nextMeta.paper || null;
      if (nextMeta && Object.prototype.hasOwnProperty.call(nextMeta, 'titleblock')) state.titleblock = nextMeta.titleblock || [];
      if (nextMeta && Object.prototype.hasOwnProperty.call(nextMeta, 'vaultPath')) state.vaultPath = nextMeta.vaultPath || '';
      emit({ type: 'doc', reason: 'setDoc' });
      emit({ type: 'select', ids: [] });
    },

    notifyDoc(reason) {
      state.dirty = true;
      emit({ type: 'doc', reason: reason || 'mutate' });
      emit({ type: 'dirty', dirty: true });
    },

    markDirty(value) {
      state.dirty = value === undefined ? true : !!value;
      emit({ type: 'dirty', dirty: state.dirty });
    },

    setSelection(ids) {
      state.selection = normalizeSelection(ids);
      emit({ type: 'select', ids: state.selection.slice() });
    },

    select(id, opts) {
      if (!id) {
        this.setSelection([]);
        return;
      }
      if (opts && opts.additive) {
        const next = new Set(state.selection);
        next.has(id) ? next.delete(id) : next.add(id);
        this.setSelection(next);
      } else {
        this.setSelection([id]);
      }
    },

    setTool(name, data) {
      state.tool = name ? { name, data: data || {} } : null;
      emit({ type: 'tool', tool: state.tool });
    },

    setView(patch) {
      state.view = { ...state.view, ...(patch || {}) };
      emit({ type: 'view', view: { ...state.view } });
    },

    setPaper(paper, titleblock) {
      state.paper = paper ? paper.slice() : null;
      state.titleblock = titleblock || [];
      emit({ type: 'sheet', paper: this.paper, titleblock: state.titleblock });
    },

    setVaultPath(path) {
      state.vaultPath = path || '';
      emit({ type: 'meta', vaultPath: state.vaultPath });
    },

    setStatus(message, isError) {
      state.status = { message: message || '', isError: !!isError };
      emit({ type: 'status', ...state.status });
    },

    pushUndo() { history.push(); },
    undo() { return history.undo(); },
    redo() { return history.redo(); },
  };
}
