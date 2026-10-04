// eos-cad-views/part-extensions.js — the CAD extension dock as a layout view (the
// final part-workspace feature ported for legacy retirement, .claude/rules/cad-layouts.md
// + .claude/rules/cad-extensions.md). It builds the `cadApi` surface backed by the
// shared store and mounts every enabled [[contributes.cad.extension]] domain panel
// (e.g. electrical/cables) into a dock — the panels touch the base CAD ONLY through
// cadApi, exactly as in the legacy editor. Used only by the `part-edit` layout.

import { defineView, esc } from '/static/eos-cad-view.js';
import { byId, resolve, round } from '/static/eos-cad-viewport.js';
import { nextFeatureId, deleteFeature as opDelete } from '/static/eos-cad-part-ops.js';

function clone(o) { return JSON.parse(JSON.stringify(o)); }

function makeApi(prefix) {
  return (path, opts) => fetch((prefix || '') + path, opts || {}).then((r) => {
    const ct = r.headers.get('content-type') || '';
    return ct.includes('application/json') ? r.json() : r.text();
  });
}

// addFeatures with id-collision suffixing (mirrors the legacy _cadAddFeatures).
function addFeatures(store, features) {
  if (!Array.isArray(features)) return [];
  const doc = store.doc;
  if (!doc.features) doc.features = [];
  const existing = new Set(doc.features.map((f) => f.id));
  const added = [];
  let snapped = false;
  features.forEach((f) => {
    if (!f || typeof f !== 'object' || !f.op) return;
    // Snapshot once, before the first real mutation, so undo removes all
    // added features in one step (and all-invalid input leaves no no-op entry).
    if (!snapped) { if (store.pushUndo) store.pushUndo(); snapped = true; }
    let id = f.id || nextFeatureId(doc, f.op);
    if (existing.has(id)) { let n = 2; while (existing.has(id + '-' + n)) n++; id = id + '-' + n; }
    existing.add(id);
    doc.features.push({ ...f, id });
    added.push(id);
  });
  if (added.length) store.notifyDoc();
  return added;
}

function makeCadApi(store, ext, vctx, subs) {
  const extApi = makeApi((ext && ext.api_prefix) || '/cad/api');
  return {
    getDocument: () => clone(store.doc),
    getFeature: (id) => { const f = byId(store.doc)[id]; return f ? clone(f) : null; },
    getSelection: () => store.selection,
    getCursor: () => { const c = store.cursor || { x: 0, y: 0, z: 0 }; return { x: c.x, y: c.y, z: c.z }; },
    addFeatures: (feats) => addFeatures(store, feats),
    updateFeature: (id, patch) => {
      const f = byId(store.doc)[id]; if (!f) return false;
      if (store.pushUndo) store.pushUndo();
      Object.assign(f, patch || {}); store.notifyDoc(); return true;
    },
    deleteFeature: (id) => opDelete(store, id),
    regenerate: (oldIds, feats) => {
      (oldIds || []).forEach((id) => { try { opDelete(store, id); } catch (e) { /* gone */ } });
      return addFeatures(store, feats || []);
    },
    getExtData: (ns) => { const e = store.doc.ext; if (!e || typeof e !== 'object') return {}; const v = e[ns]; return v ? clone(v) : {}; },
    setExtData: (ns, data) => {
      if (!store.doc.ext || typeof store.doc.ext !== 'object') store.doc.ext = {};
      store.doc.ext[ns] = data; if (store.markDirty) store.markDirty(); return true;
    },
    select: (id) => store.select(id),
    units: () => store.doc.units || 'mm',
    getViewport: () => store.viewport,
    onChange: (cb) => { if (typeof cb === 'function') subs.push(store.subscribe(() => cb(), ['doc'])); },
    onSelect: (cb) => { if (typeof cb === 'function') subs.push(store.subscribe((e) => cb(e.oid), ['select'])); },
    // Best-effort: a layout gizmo-move isn't yet relayed through the store, so this
    // registers but may not fire (the legacy editor fired it on gizmo drop). Panels
    // that only model-from-source (the common case) are unaffected.
    onTransform: (cb) => { /* TODO: relay viewport gizmo commits via the store */ },
    setStatus: (m, e) => { if (vctx.setStatus) vctx.setStatus(m, e); },
    toast: (m, k) => { if (window.EOS_UI && window.EOS_UI.toast) window.EOS_UI.toast(m, k || 'info'); },
    api: extApi,
    resolve: (v) => resolve(store.doc, v),
    round,
  };
}

const STYLES = `
  .cadv-pext { height: 100%; overflow-y: auto; padding: 12px; box-sizing: border-box;
    border-left: 1px solid var(--border); background: var(--bg); }
  .cadv-pext .sec-title { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 0 0 8px; }
  .cadv-pext .ext-card { border: 1px solid var(--border); border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
  .cadv-pext .ext-card-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
    background: var(--bg-card, var(--panel)); font-size: 13px; font-weight: 600; min-width: 0; }
  .cadv-pext .ext-card-body { padding: 10px 12px; min-width: 0; }
`;

const MARKUP = `<div class="cadv-pext"><div class="sec-title">Extensions</div><div data-ext-dock></div></div>`;

async function loadExtensions(vctx, state) {
  unmountExts(state);
  const dock = vctx.pane.querySelector('[data-ext-dock]');
  if (!dock) return;
  let exts = [];
  try { exts = ((await fetch('/cad/api/extensions').then((r) => r.json())).extensions) || []; } catch (e) { return; }
  const enabled = exts.filter((e) => e.enabled);
  if (!enabled.length) { dock.innerHTML = '<div class="eos-tool-empty">No CAD extensions are enabled.</div>'; return; }
  dock.innerHTML = '';
  for (const ext of enabled) {
    const card = document.createElement('div'); card.className = 'ext-card';
    card.innerHTML = '<div class="ext-card-head"><span>' + esc(ext.icon || '⊞') + '</span>'
      + esc(ext.label || ext.id) + '</div><div class="ext-card-body"></div>';
    const body = card.querySelector('.ext-card-body');
    dock.appendChild(card);
    const url = ext.panel_module + (ext.panel_module.includes('?') ? '&' : '?') + 'v=' + Date.now();
    let mod;
    try { mod = await import(url); } catch (e) { body.textContent = 'Failed to load this extension.'; continue; }
    if (!mod || typeof mod.mount !== 'function') { body.textContent = 'Extension has no mount().'; continue; }
    try {
      await mod.mount(body, makeCadApi(vctx.store, ext, vctx, state.subs));
      state.mounted.push({ id: ext.id, unmount: mod.unmount, el: card });
    } catch (e) { body.textContent = 'Extension failed to start.'; }
  }
}

function unmountExts(state) {
  state.mounted.forEach((m) => { try { if (m.unmount) m.unmount(); } catch (e) { /* best-effort */ } if (m.el) m.el.remove(); });
  state.mounted = [];
  state.subs.forEach((u) => { try { if (typeof u === 'function') u(); } catch (e) { /* best-effort */ } });
  state.subs = [];
}

const _v = defineView({
  id: 'part-extensions', styles: STYLES, markup: MARKUP,
  events: [],
  mount(vctx) {
    vctx._extState = { mounted: [], subs: [] };
    // Defer one tick so the viewport-3d pane has constructed store.viewport first
    // (some panels read getViewport() at mount).
    setTimeout(() => loadExtensions(vctx, vctx._extState), 0);
  },
  teardown(vctx) { if (vctx._extState) unmountExts(vctx._extState); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
