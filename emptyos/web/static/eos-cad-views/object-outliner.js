// Generic typed-object outliner. Unlike the feature outliner, this is driven by
// every ext.*.objects[] namespace and therefore works for any registered domain.
import { defineView, esc } from '/static/eos-cad-view.js';

const STYLES = `
  .cadv-oo { height:100%; overflow:auto; padding:12px; box-sizing:border-box; background:var(--bg); }
  .cadv-oo h3 { margin:0 0 4px; font-size:13px; }
  .cadv-oo .hint { color:var(--muted); font-size:11px; margin-bottom:10px; }
  .cadv-oo button { width:100%; display:grid; grid-template-columns:1fr auto; gap:8px; border:1px solid transparent;
    border-radius:var(--radius-sm, 6px); padding:8px; text-align:left; color:var(--text); background:transparent; cursor:pointer; }
  .cadv-oo button:hover { background:var(--bg-hover); }
  .cadv-oo button.sel { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 12%, transparent); }
  .cadv-oo .oid { overflow:hidden; text-overflow:ellipsis; font-size:12px; font-weight:600; }
  .cadv-oo .kind { color:var(--muted); font:10px var(--mono, monospace); }
  .cadv-oo .empty { color:var(--muted); font-size:12px; }
`;

function render(vctx) {
  const host = vctx.pane.querySelector('[data-objects]');
  if (!host) return;
  const objects = vctx.store.iterObjects();
  if (!objects.length) { host.innerHTML = '<div class="empty">No typed objects in this document.</div>'; return; }
  host.innerHTML = objects.map((obj) =>
    '<button type="button" data-oid="' + escAttr(obj.oid) + '" class="' + (obj.oid === vctx.store.selection ? 'sel' : '') + '">' +
      '<span><span class="oid">' + esc(obj.props.label || obj.props.name || obj.oid) + '</span><br>' +
      '<span class="kind">' + esc(obj.ext + ' / ' + obj.kind) + '</span></span>' +
      '<span class="kind">' + (obj.feature_ids || []).length + ' feat.</span></button>'
  ).join('');
  host.querySelectorAll('[data-oid]').forEach((button) => button.addEventListener('click', () => vctx.store.select(button.dataset.oid)));
}

const _v = defineView({
  id: 'object-outliner', styles: STYLES,
  markup: '<div class="cadv-oo"><h3>Object model</h3><div class="hint">Authoritative domain objects</div><div data-objects></div></div>',
  events: ['doc', 'object', 'select'],
  mount(vctx) { if (vctx.store.selection == null && vctx.store.iterObjects().length) vctx.store.select(vctx.store.iterObjects()[0].oid); render(vctx); },
  update(vctx) { render(vctx); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
