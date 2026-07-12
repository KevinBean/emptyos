// eos-cad-views/params.js — the named-parameter table view. Lists doc.params over
// the shared store; editing a value writes it back + store.notifyDoc() so every
// feature referencing the param re-evaluates and the 3D viewport rebuilds. Ported
// from part-workspace.js renderParams. Re-renders on 'doc'.

import { defineView, esc } from '/static/eos-cad-view.js';

const STYLES = `
  .cadv-params { height: 100%; overflow-y: auto; padding: 12px; box-sizing: border-box;
    border-right: 1px solid var(--border); background: var(--bg); }
  .cadv-params .sec-title { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 0 0 8px; }
  .cadv-param-row { display: grid; grid-template-columns: minmax(0, 1fr) 96px; gap: 8px; align-items: center; margin-bottom: 8px; }
  @media (max-width: 640px), (pointer: coarse) {
    .cadv-param-row { grid-template-columns: minmax(0, 1fr); }
  }
`;

function render(vctx) {
  const el = vctx.pane.querySelector('[data-cad-params]');
  if (!el) return;
  const params = (vctx.store.doc && vctx.store.doc.params) || {};
  const keys = Object.keys(params);
  if (!keys.length) { el.innerHTML = '<div class="eos-tool-empty">No parameters yet. Add one from Tools.</div>'; return; }
  el.innerHTML = '';
  keys.forEach((k) => {
    const row = document.createElement('div');
    row.className = 'cadv-param-row';
    row.innerHTML =
      `<input class="eos-tool-field" value="${esc(k)}" disabled>` +
      `<input class="eos-tool-field" type="number" step="any" value="${esc(String(params[k]))}" data-pk="${esc(k)}">`;
    el.appendChild(row);
  });
  el.querySelectorAll('[data-pk]').forEach((inp) =>
    inp.addEventListener('input', () => {
      const v = parseFloat(inp.value);
      vctx.store.doc.params[inp.dataset.pk] = isNaN(v) ? 0 : v;
      vctx.store.notifyDoc();
    }));
}

const _v = defineView({
  id: 'params',
  styles: STYLES,
  markup: `<div class="cadv-params"><div class="sec-title">Parameters</div><div data-cad-params></div></div>`,
  events: ['doc'],
  mount(vctx) { render(vctx); },
  update(vctx) { render(vctx); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
