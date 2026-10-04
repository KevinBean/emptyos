// eos-cad-views/part-outliner.js — the EDITABLE feature tree for the layout-model
// part editor (stage 5 of legacy-workspace retirement). A superset of the shared
// read-only `outliner` view: select + reorder (▲/▼) + enable-toggle (👁) + delete (✕)
// + boolean pick-stack (∪ ∩ −). Used ONLY by the `part-edit` layout — the shared
// `outliner` (used by the working `part`/`part-wide` layouts) is deliberately left
// untouched until full edit parity is verified.
//
// All mutations go through the node-tested pure ops in eos-cad-part-ops.js, which
// edit store.doc + notifyDoc() — firing the 'doc' event viewport-3d re-renders from.
// The DOM wiring here is verified offline by node --check only; a live click-through
// smoke-test is owed when the daemon is up.

import { defineView } from '/static/eos-cad-view.js';
import { byId, consumedIds, enabledFeat, DEFAULT_COLOR } from '/static/eos-cad-viewport.js';
import {
  deleteFeature, toggleFeatureEnabled, moveFeature, applyBoolean,
} from '/static/eos-cad-part-ops.js';

function safeColor(c) { return (typeof c === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(c)) ? c : DEFAULT_COLOR; }

const _picked = new Set();   // feature ids picked for the next boolean (view-local)

const STYLES = `
  .cadv-pol { height: 100%; overflow-y: auto; padding: 0 12px 12px; box-sizing: border-box;
    border-right: 1px solid var(--border); background: var(--bg); scrollbar-gutter: stable; }
  .cadv-pol::-webkit-scrollbar { width: 10px; }
  .cadv-pol::-webkit-scrollbar-thumb { background: var(--border); border-radius: 5px; }
  .cadv-pol::-webkit-scrollbar-thumb:hover { background: var(--muted); }
  .cadv-pol .pol-head { position: sticky; top: 0; z-index: 2; background: var(--bg);
    padding-top: 12px; margin-bottom: 4px; border-bottom: 1px solid var(--border); }
  .cadv-pol .sec-title { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 0 0 8px; }
  .cadv-pol .pol-bool { margin-bottom: 8px; gap: 4px; }
  .cadv-pol .pol-bool-hint { font-size: 11px; color: var(--muted); line-height: 1.4; margin: 0 0 8px; }
  /* The rail is 300px: at the base 13px/10px-padding the three labels ellipsize
     ("Subtra…", "Inters…"). Tighten just these three so they read in full. */
  .cadv-pol .pol-bool .eos-tool-btn { flex: 1; min-width: 0; font-size: 12px; padding: 8px 4px; }
  .pol-row .pk:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .pol-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 8px; font-size: 13px; min-width: 0; }
  .pol-row:hover { background: var(--panel); }
  .pol-row.sel { background: var(--panel); outline: 1px solid var(--accent); }
  .pol-row.consumed { opacity: .55; }
  .pol-row.disabled .nm { opacity: .4; text-decoration: line-through; }
  .pol-row .sw { width: 12px; height: 12px; border-radius: 4px; border: 1px solid var(--border); flex: none; }
  .pol-row .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; flex: 1; }
  .pol-row .op { color: var(--muted); font-size: 10px; }
  .pol-row .eos-tool-icon-btn { flex: none; width: 28px; min-width: 28px; min-height: 28px; padding: 0; font-size: 12px; }
  .pol-row .pk { cursor: pointer; }
  .cadv-pol .empty { color: var(--muted); font-size: 12px; padding: 8px; line-height: 1.4; }
  @media (max-width: 640px), (pointer: coarse) {
    .cadv-pol .pol-bool { flex-direction: column; }
    .cadv-pol .pol-bool .eos-tool-btn { width: 100%; }
    .pol-row { padding: 8px; }
    .pol-row .eos-tool-icon-btn { min-width: 44px; min-height: 44px; }
    .pol-row .pk { width: 24px; height: 24px; }
  }
`;

const MARKUP = `<div class="cadv-pol">
  <div class="pol-head">
    <div class="sec-title">Features</div>
    <div class="pol-bool eos-tool-row">
      <button class="eos-tool-btn" data-bool="union" title="Union: merge the picked features into one solid">&#8746; Union</button>
      <button class="eos-tool-btn" data-bool="subtract" title="Subtract: cut the later picks out of the first picked feature">&#8726; Subtract</button>
      <button class="eos-tool-btn" data-bool="intersect" title="Intersect: keep only the overlap of the picked features">&#8745; Intersect</button>
    </div>
    <div class="pol-bool-hint" data-pol-hint></div>
  </div>
  <div data-pol-tree></div>
</div>`;

function render(vctx) {
  const tree = vctx.pane.querySelector('[data-pol-tree]');
  if (!tree) return;
  const store = vctx.store;
  const doc = store.doc;
  const sel = store.selection;
  const feats = doc.features || [];

  // Boolean workflow hint — explains the pick-then-combine flow + why the
  // buttons are disabled. Re-runs on every render (incl. each pick change).
  const hint = vctx.pane.querySelector('[data-pol-hint]');
  if (hint) {
    hint.textContent = _picked.size < 2
      ? 'Tick 2+ features below, then combine.'
      : 'Combine ' + _picked.size + ' picked — first pick is the Subtract base.';
  }

  // boolean buttons enabled only with >= 2 picks
  vctx.pane.querySelectorAll('[data-bool]').forEach((b) => {
    b.disabled = _picked.size < 2;
    if (!b._wired) {
      b._wired = true;
      b.addEventListener('click', () => {
        if (_picked.size < 2) return;
        applyBoolean(store, b.dataset.bool, [..._picked]);
        _picked.clear();
      });
    }
  });

  if (!feats.length) { tree.innerHTML = '<div class="empty">No features yet. Add a primitive from Tools.</div>'; return; }
  const consumed = consumedIds(doc);
  tree.innerHTML = '';
  feats.forEach((f, i) => {
    const owner = store.objectOwningFeature && store.objectOwningFeature(f.id);
    const row = document.createElement('div');
    row.className = 'pol-row' + (f.id === sel ? ' sel' : '')
      + (consumed.has(f.id) ? ' consumed' : '') + (enabledFeat(f) ? '' : ' disabled');
    const pk = document.createElement('input');
    pk.type = 'checkbox'; pk.className = 'pk'; pk.checked = _picked.has(f.id); pk.disabled = !!owner;
    pk.setAttribute('aria-label', 'Pick ' + (f.name || f.id) + ' for boolean');
    pk.title = 'pick for boolean';
    pk.addEventListener('change', () => { pk.checked ? _picked.add(f.id) : _picked.delete(f.id); render(vctx); });
    const sw = document.createElement('span'); sw.className = 'sw'; sw.style.background = safeColor(f.color);
    const nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = f.name || f.id;
    nm.addEventListener('click', () => store.select(f.id));
    const op = document.createElement('span'); op.className = 'op'; op.textContent = f.op;
    const generatedTip = owner ? 'Generated by ' + owner.oid + '; edit the source object' : '';
    const up = mkAct('▲', generatedTip || 'move up', () => moveFeature(store, f.id, -1), !!owner || i === 0);
    const dn = mkAct('▼', generatedTip || 'move down', () => moveFeature(store, f.id, 1), !!owner || i === feats.length - 1);
    const eye = mkAct(enabledFeat(f) ? '👁' : '🚫', generatedTip || 'toggle enabled', () => toggleFeatureEnabled(store, f.id), !!owner);
    const del = mkAct('✕', generatedTip || 'delete', () => { _picked.delete(f.id); deleteFeature(store, f.id); }, !!owner);
    row.append(pk, sw, nm, op, up, dn, eye, del);
    tree.appendChild(row);
  });
}

function mkAct(label, title, fn, disabled) {
  const b = document.createElement('button');
  b.className = 'eos-tool-icon-btn'; b.textContent = label; b.title = title; b.disabled = !!disabled;
  if (disabled) b.style.opacity = '.25';
  else b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
  return b;
}

const _v = defineView({
  id: 'part-outliner', styles: STYLES, markup: MARKUP,
  events: ['doc', 'object', 'select'],
  mount(vctx) { render(vctx); },
  update(vctx) { render(vctx); },
  teardown() { _picked.clear(); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
