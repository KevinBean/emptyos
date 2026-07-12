// eos-cad-views/outliner.js — the feature-tree view (the Blender Outliner analogue).
// Lists doc.features over the shared store; clicking a row selects it (store.select),
// which the 3D view + inspector pick up via the 'select' event. Re-renders on 'doc'
// + 'select'. Read+select for now (drag-reorder / boolean-pick / eye-toggle stay in
// the legacy part editor until the layout host reaches edit parity).

import { defineView, esc } from '/static/eos-cad-view.js';
import { byId, consumedIds, enabledFeat, DEFAULT_COLOR } from '/static/eos-cad-viewport.js';

function safeColor(c) { return (typeof c === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(c)) ? c : DEFAULT_COLOR; }

const STYLES = `
  .cadv-outliner { height: 100%; overflow-y: auto; padding: 10px 12px; box-sizing: border-box;
    border-right: 1px solid var(--border); background: var(--bg); }
  .cadv-outliner .sec-title { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 0 0 8px; }
  .cadv-tree-row { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 6px;
    cursor: pointer; font-size: 13px; }
  .cadv-tree-row:hover { background: var(--panel); }
  .cadv-tree-row.sel { background: var(--panel); outline: 1px solid var(--accent); }
  .cadv-tree-row.consumed { opacity: .55; padding-left: 18px; font-size: 12px; }
  .cadv-tree-row.disabled { opacity: .4; text-decoration: line-through; }
  .cadv-tree-row .sw { width: 12px; height: 12px; border-radius: 4px; border: 1px solid var(--border); flex: none; }
  .cadv-tree-row .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cadv-tree-row .op { color: var(--muted); font-size: 11px; margin-left: auto; }
  .cadv-empty { color: var(--muted); font-size: 12px; padding: 6px; }
`;

function render(vctx) {
  const el = vctx.pane.querySelector('[data-cad-tree]');
  if (!el) return;
  const doc = vctx.store.doc;
  const sel = vctx.store.selection;
  const feats = doc.features || [];
  if (!feats.length) { el.innerHTML = '<div class="cadv-empty">No features yet.</div>'; return; }
  const consumed = consumedIds(doc);
  el.innerHTML = '';
  feats.forEach((f) => {
    const row = document.createElement('div');
    row.className = 'cadv-tree-row'
      + (f.id === sel ? ' sel' : '')
      + (consumed.has(f.id) ? ' consumed' : '')
      + (enabledFeat(f) ? '' : ' disabled');
    row.innerHTML =
      `<span class="sw" style="background:${safeColor(f.color)}"></span>` +
      `<span class="nm">${esc(f.name || f.id)}</span>` +
      `<span class="op">${esc(f.op)}</span>`;
    row.addEventListener('click', () => vctx.store.select(f.id));
    el.appendChild(row);
  });
}

const _v = defineView({
  id: 'outliner',
  styles: STYLES,
  markup: `<div class="cadv-outliner"><div class="sec-title">Features</div><div data-cad-tree></div></div>`,
  events: ['doc', 'object', 'select'],
  mount(vctx) { render(vctx); },
  update(vctx) { render(vctx); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
