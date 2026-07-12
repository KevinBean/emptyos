// eos-cad-store.js — the shared-state spine for the unified CAD document.
//
// The CAD architecture rethink replaces "a switcher across separate document
// editors" with "one document, many synchronized views, switchable layouts".
// This store is the single source of truth every view subscribes to:
//
//   { doc, selection, cursor, chainage, viewport } + a typed-mask change bus.
//
// A view never talks to another view — it mutates the store (select / patchObject
// / setChainage) and re-draws on the store's events. Selecting a cable in the plan
// view calls store.select(oid); the `select` event fans out and the 3D view
// highlights the mesh, the elevation view thickens the run, the inspector loads
// its form, the cross-section re-slices — all from one mutation, no view knowing
// about any other. See .claude/rules/cad-layouts.md.
//
// Dependency-free, no framework. The JS twin of emptyos/sdk/cad_objects.py for the
// ext.<id>.objects[] typed-object convention (findObject / iterObjects) lives here
// so views query the doc the same way the backend does.

import { createHistory } from './eos-history.js';

// ── JS twins of cad_objects.py (read the ext.<id>.objects[] convention) ──

export function iterObjects(doc, opts) {
  // → [{oid, kind, ext, feature_ids, props}, ...]; opts = {kind?, ext?}
  const out = [];
  if (!doc || typeof doc !== 'object') return out;
  const ext = doc.ext;
  if (!ext || typeof ext !== 'object') return out;
  const wantKind = opts && opts.kind, wantExt = opts && opts.ext;
  for (const extId of Object.keys(ext)) {
    if (wantExt && extId !== wantExt) continue;
    const ns = ext[extId];
    if (!ns || typeof ns !== 'object' || !Array.isArray(ns.objects)) continue;
    for (const raw of ns.objects) {
      if (!raw || typeof raw !== 'object') continue;
      const oid = raw.oid, kind = raw.kind;
      if (typeof oid !== 'string' || !oid || typeof kind !== 'string' || !kind) continue;
      if (wantKind && kind !== wantKind) continue;
      out.push({
        oid, kind, ext: extId,
        feature_ids: Array.isArray(raw.feature_ids) ? raw.feature_ids.filter(f => typeof f === 'string') : [],
        props: (raw.props && typeof raw.props === 'object') ? raw.props : {},
        _raw: raw,                       // the live object (views may mutate via the store)
      });
    }
  }
  return out;
}

export function objectsOfKind(doc, kind) { return iterObjects(doc, { kind }); }

export function findObject(doc, oid) {
  if (typeof oid !== 'string' || !oid) return null;
  for (const o of iterObjects(doc)) if (o.oid === oid) return o;
  return null;
}

// ── The store ──

export function createCadStore(initialDoc) {
  const state = {
    doc: initialDoc || { schema: 'eos-cad/1', units: 'mm', params: {}, features: [], ext: {} },
    selection: null,            // selected OBJECT oid (or a raw feature id) — shared across views
    cursor: { x: 0, y: 0, z: 0 }, // 3D cursor (CadViewport.cursor3d mirror)
    chainage: 0,                // active long-run position (corridor cursor), metres from run start
    viewport: null,             // the ONE persistent CadViewport handle
    dirty: false,
  };
  const subs = new Set();       // { fn, mask:Set|null }

  // Undo/redo history — the part surface snapshots the whole doc before every
  // mutation (pushUndo) so an edit (incl. an AI apply) is reversible. Restore
  // replaces the doc reference; every part view + the 3D viewport re-render from
  // the 'doc' event. Scene/other surfaces that never call pushUndo pay nothing.
  // Shared stack mechanics live in eos-history.js. See docs/CAD-ROADMAP.md.
  const history = createHistory({
    snapshot: () => JSON.parse(JSON.stringify(state.doc)),
    restore: (snap) => { state.doc = snap; state.dirty = true; emit({ type: 'doc' }); },
    limit: 60,
    onChange: () => emit({ type: 'history' }),
  });

  function emit(evt) {
    for (const s of Array.from(subs)) {
      if (s.mask && !s.mask.has(evt.type)) continue;
      try { s.fn(evt); } catch (e) {
        if (typeof console !== 'undefined' && console.warn) console.warn('[cad-store] subscriber failed', evt, e);
      }
    }
  }

  return {
    // reads
    get doc() { return state.doc; },
    get selection() { return state.selection; },
    get cursor() { return state.cursor; },
    get chainage() { return state.chainage; },
    get viewport() { return state.viewport; },
    get dirty() { return state.dirty; },
    objectByOid(oid) { return findObject(state.doc, oid); },
    objectsOfKind(kind) { return objectsOfKind(state.doc, kind); },
    iterObjects(opts) { return iterObjects(state.doc, opts); },

    // subscription — `types` is an array of event types this view cares about
    // (doc | object | select | cursor | chainage | dirty | history), or omitted for all.
    subscribe(fn, types) {
      const s = { fn, mask: (types && types.length) ? new Set(types) : null };
      subs.add(s);
      return () => subs.delete(s);
    },

    // mutations
    setDoc(d) { state.doc = d || state.doc; emit({ type: 'doc' }); },

    // Broadcast that the current doc was mutated IN PLACE (a feature edited, a
    // param changed, a gizmo drop). Marks dirty + emits 'doc' so every view +
    // the 3D viewport re-render. Use this for raw-feature edits (the `part`
    // layout); patchObject covers the typed-object case.
    notifyDoc() { state.dirty = true; emit({ type: 'doc' }); },

    // Merge a patch into ONE typed object (found by oid across ext.*.objects).
    // patch.props is merged; patch.feature_ids replaces; returns true if applied.
    patchObject(oid, patch) {
      const found = findObject(state.doc, oid);
      if (!found) return false;
      const raw = found._raw;
      if (patch && typeof patch === 'object') {
        if (patch.props && typeof patch.props === 'object') {
          raw.props = Object.assign({}, raw.props || {}, patch.props);
        }
        if (Array.isArray(patch.feature_ids)) raw.feature_ids = patch.feature_ids.slice();
      }
      state.dirty = true;
      emit({ type: 'object', oid });
      return true;
    },

    select(oid) {
      if (state.selection === oid) return;
      state.selection = oid;
      emit({ type: 'select', oid });
    },
    setCursor(x, y, z) {
      state.cursor = { x: x || 0, y: y || 0, z: z || 0 };
      emit({ type: 'cursor' });
    },
    setChainage(c) {
      const v = +c || 0;
      if (v === state.chainage) return;
      state.chainage = v;
      emit({ type: 'chainage', chainage: v });
    },
    setViewport(vp) { state.viewport = vp; },
    markDirty(b) { state.dirty = (b === undefined ? true : !!b); emit({ type: 'dirty' }); },

    // ── history ── (mechanics in eos-history.js; snapshot/restore injected above)
    // Call pushUndo BEFORE mutating (in place → notifyDoc, or via setDoc).
    pushUndo() { history.push(); },
    undo() { return history.undo(); },
    redo() { return history.redo(); },
    get canUndo() { return history.canUndo; },
    get canRedo() { return history.canRedo; },
  };
}
