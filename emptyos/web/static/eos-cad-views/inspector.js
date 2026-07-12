// eos-cad-views/inspector.js — the property editor for the selected feature
// (the Blender Properties analogue). Reads the selection off the shared store,
// renders editable fields for the feature, and writes edits back into
// doc.features + store.notifyDoc() so the 3D viewport + outliner re-render. Ported
// from part-workspace.js renderInspector/applyField. Re-renders on 'doc' + 'select'.

import { defineView, esc } from '/static/eos-cad-view.js';
import { byId, DEFAULT_COLOR } from '/static/eos-cad-viewport.js';

function val(arr, i, def) {
  return Array.isArray(arr) && arr[i] !== undefined ? arr[i] : (def === undefined ? '' : def);
}
function parseFieldVal(s) {
  const n = parseFloat(s);
  return (s !== '' && !isNaN(n) && String(n) === s.trim()) ? n : (s.trim() === '' ? 0 : s.trim());
}
function field(label, key, v) {
  return `<label>${esc(label)}</label><input class="eos-tool-field" data-f="${esc(key)}" value="${v === undefined ? '' : esc(String(v))}">`;
}
function axisField(f) {
  const a = f.axis || 'z';
  return `<label>Axis</label><select class="eos-tool-field" data-f="axis">
    <option ${a === 'x' ? 'selected' : ''}>x</option>
    <option ${a === 'y' ? 'selected' : ''}>y</option>
    <option ${a === 'z' ? 'selected' : ''}>z</option></select>`;
}

const STYLES = `
  .cadv-insp { height: 100%; overflow-y: auto; padding: 12px; box-sizing: border-box;
    border-left: 1px solid var(--border); background: var(--bg); }
  .cadv-insp .sec-title { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 0 0 8px; }
  .cadv-insp label { display: block; font-size: 11px; color: var(--muted); margin: 8px 0 4px; }
  .cadv-insp .triple { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr); gap: 8px; }
  @media (max-width: 640px), (pointer: coarse) {
    .cadv-insp .triple { grid-template-columns: minmax(0, 1fr); }
  }
`;

function applyField(vctx, f, key, raw) {
  if (vctx.store.pushUndo) vctx.store.pushUndo();
  if (key === 'name' || key === 'color' || key === 'axis') { f[key] = raw; }
  else if (key.includes('.')) {
    const [k, idx] = key.split('.');
    const i = parseInt(idx, 10);
    if (!Array.isArray(f[k])) f[k] = [0, 0, 0];
    f[k][i] = parseFieldVal(raw);
  } else { f[key] = parseFieldVal(raw); }
  vctx.store.notifyDoc();
}

function render(vctx) {
  const el = vctx.pane.querySelector('[data-cad-insp]');
  if (!el) return;
  const doc = vctx.store.doc;
  const f = byId(doc)[vctx.store.selection];
  if (!f) { el.innerHTML = '<p class="eos-tool-empty">Select a feature to edit its dimensions and transform.</p>'; return; }
  let h = field('Name', 'name', f.name || '') + field('Color (#hex)', 'color', f.color || DEFAULT_COLOR);
  if (f.op === 'box') h += field('Size X', 'size.0', val(f.size, 0)) + field('Size Y', 'size.1', val(f.size, 1)) + field('Size Z', 'size.2', val(f.size, 2));
  else if (f.op === 'sphere') h += field('Radius', 'r', f.r);
  else if (f.op === 'cylinder') h += field('Radius', 'r', f.r) + field('Height', 'h', f.h) + axisField(f);
  else if (f.op === 'cone') h += field('Base radius', 'r', f.r) + field('Top radius', 'r2', f.r2) + field('Height', 'h', f.h) + axisField(f);
  h += `<label>Position [x,y,z]</label><div class="triple">
    <input class="eos-tool-field" data-f="at.0" value="${esc(String(val(f.at, 0, 0)))}">
    <input class="eos-tool-field" data-f="at.1" value="${esc(String(val(f.at, 1, 0)))}">
    <input class="eos-tool-field" data-f="at.2" value="${esc(String(val(f.at, 2, 0)))}"></div>`;
  h += `<label>Rotation° [x,y,z]</label><div class="triple">
    <input class="eos-tool-field" data-f="rotate.0" value="${esc(String(val(f.rotate, 0, 0)))}">
    <input class="eos-tool-field" data-f="rotate.1" value="${esc(String(val(f.rotate, 1, 0)))}">
    <input class="eos-tool-field" data-f="rotate.2" value="${esc(String(val(f.rotate, 2, 0)))}"></div>`;
  el.innerHTML = h;
  el.querySelectorAll('[data-f]').forEach((inp) =>
    inp.addEventListener('change', () => applyField(vctx, f, inp.dataset.f, inp.value)));
}

const _v = defineView({
  id: 'inspector',
  styles: STYLES,
  markup: `<div class="cadv-insp"><div class="sec-title">Inspector</div><div data-cad-insp></div></div>`,
  events: ['doc', 'object', 'select'],
  mount(vctx) { render(vctx); },
  update(vctx) { render(vctx); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
