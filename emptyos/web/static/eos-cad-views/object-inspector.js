// Schema-driven inspector for typed CAD objects. It deliberately edits scalar
// prop paths only; collection editors remain with the domain app.
import { defineView, esc } from '/static/eos-cad-view.js';
import { regenerateObject } from '/static/eos-cad-corridor.js';
import { getPath, setPath, fieldsMarkup } from '/static/eos-cad-schema-form.js';

const STYLES = `
  .cadv-oi { height:100%; overflow:auto; padding:12px; box-sizing:border-box; background:var(--bg); }
  .cadv-oi h3 { margin:0; font-size:13px; } .cadv-oi .sub { color:var(--muted); font:10px var(--mono, monospace); margin:3px 0 12px; }
  .cadv-oi fieldset { border:0; border-top:1px solid var(--border); padding:10px 0 2px; margin:0; }
  .cadv-oi legend { padding:0 6px 0 0; color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.08em; }
  .cadv-oi label { display:grid; grid-template-columns:minmax(95px, 1fr) minmax(90px, 1fr) auto; gap:6px; align-items:center; margin:7px 0; font-size:11px; }
  .cadv-oi input,.cadv-oi select { min-width:0; width:100%; box-sizing:border-box; border:1px solid var(--border); border-radius:var(--radius-sm, 6px); background:var(--bg-input,var(--bg-card)); color:var(--text); padding:6px 7px; }
  .cadv-oi input[type=checkbox] { width:auto; justify-self:start; } .cadv-oi .unit { color:var(--muted); font:10px var(--mono, monospace); }
  .cadv-oi pre { white-space:pre-wrap; word-break:break-word; color:var(--muted); font:10px/1.5 var(--mono, monospace); }
  .cadv-oi .notice { color:var(--muted); font-size:11px; line-height:1.5; }
`;

let registryPromise = null;
const schemas = new Map();
let sequence = 0;

function registry() {
  if (!registryPromise) registryPromise = fetch('/cad/api/object-types').then((r) => r.json()).then((r) => r.object_types || []).catch(() => []);
  return registryPromise;
}
async function render(vctx) {
  const host = vctx.pane.querySelector('[data-inspector]'); if (!host) return;
  const obj = vctx.store.objectByOid(vctx.store.selection);
  if (!obj) { host.innerHTML = '<div class="notice">Select a typed object to inspect it.</div>'; return; }
  const my = ++sequence;
  host.innerHTML = '<h3>' + esc(obj.props.label || obj.props.name || obj.oid) + '</h3><div class="sub">' + esc(obj.kind) + ' · loading schema…</div>';
  const types = await registry(); if (my !== sequence) return;
  const type = types.find((entry) => entry.kind === obj.kind);
  let fields = null;
  if (type && type.schema_method) {
    const key = type.api_prefix + '|' + obj.kind;
    if (!schemas.has(key)) schemas.set(key, fetch(type.api_prefix + '/cad/schema/' + encodeURIComponent(obj.kind)).then((r) => r.json()).catch(() => null));
    const schema = await schemas.get(key); if (my !== sequence) return;
    fields = schema && schema.ok ? schema.fields : null;
  }
  host.innerHTML = '<h3>' + esc(obj.props.label || obj.props.name || obj.oid) + '</h3><div class="sub">' + esc(obj.kind + ' · ' + obj.oid) + '</div>' +
    (fields ? fieldsMarkup(obj.props, fields) : '<div class="notice">No editable scalar schema is declared. The structured source remains read-only here.</div><pre>' + esc(JSON.stringify(obj.props, null, 2)) + '</pre>');
  host.querySelectorAll('[data-key]').forEach((input) => {
    const commit = async () => {
    const current = vctx.store.objectByOid(obj.oid); if (!current) return;
    const field = fields.find((item) => item.key === input.dataset.key);
    let value = field.type === 'boolean' ? input.checked : (field.type === 'number' ? Number(input.value) : input.value);
    if (field.type === 'number' && !Number.isFinite(value)) { render(vctx); return; }
    if (getPath(current.props, field.key) === value) return;
    const props = JSON.parse(JSON.stringify(current.props || {})); setPath(props, field.key, value);
    vctx.store.pushUndo(); vctx.store.patchObject(obj.oid, { props });
    if (vctx.setStatus) vctx.setStatus('Regenerating ' + obj.kind + '…');
    await regenerateObject(vctx.store, obj.oid);
    if (vctx.setStatus) vctx.setStatus('Updated ' + obj.oid);
    };
    input.addEventListener('change', commit);
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); commit(); } });
  });
}

const _v = defineView({
  id: 'object-inspector', styles: STYLES,
  markup: '<div class="cadv-oi"><div data-inspector></div></div>',
  events: ['doc', 'object', 'select'], mount: render, update: render,
});
export const mount = _v.mount;
export const teardown = _v.teardown;
