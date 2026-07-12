// eos-cad-views/part-tools.js — the part-editor TOOLBAR as a layout view (the
// store-mutating subset of the legacy part-workspace, ported onto the layout host).
// This is stage 5 of the legacy-workspace retirement (.claude/rules/cad-layouts.md):
// part-workspace.js mutates a local doc + vp.setDocument/rebuild; the layout
// equivalent is mutate store.doc + store.notifyDoc() — which fires the 'doc' event
// that the (proven) viewport-3d view re-renders from, IDENTICALLY to the legacy
// buildScene() path. Used by the additive `part-edit` layout; the working `part`
// layout + the legacy part-workspace are untouched until full parity is verified.
//
// PORTED (pure store mutations, offline node-testable):
//   • Add primitive (box / sphere / cylinder / cone)
//   • Add parameter
// DEFERRED to a daemon-up session (each needs cadApi / WebGL gizmo / Three exporters
// — untestable offline, so not ported blind): AI draft, STL/glTF export, save/persist,
// boolean-via-pick (needs the multi-select pick stack), gizmo modes, snap/ortho,
// tree drag-reorder, the extension dock. Tracked as the remainder of stage 5.

import { defineView } from '/static/eos-cad-view.js';
import { addPrimitive, addParam } from '/static/eos-cad-part-ops.js';
import { createAiPanel } from '/static/eos-cad-ai-panel.js';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

// Small inline SVG shape icons (16px, currentColor → follow theme + hover accent).
// SVG rather than unicode because unicode has no clean cylinder glyph.
const SVG = {
  box: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M12 2l8 4.5v9L12 20l-8-4.5v-9z"/><path d="M4 6.5l8 4.5 8-4.5M12 11v9"/></svg>',
  sphere: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="9" ry="3.4"/></svg>',
  cylinder: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6"><ellipse cx="12" cy="6" rx="7" ry="2.5"/><path d="M5 6v12M19 6v12"/><path d="M5 18a7 2.5 0 0 0 14 0"/></svg>',
  cone: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M12 3l7 15M12 3L5 18"/><ellipse cx="12" cy="18" rx="7" ry="2.5"/></svg>',
};
const PRIMITIVES = [
  ['box', 'Box', 'Add a box at the cursor', SVG.box],
  ['sphere', 'Sphere', 'Add a sphere at the cursor', SVG.sphere],
  ['cylinder', 'Cylinder', 'Add a cylinder at the cursor', SVG.cylinder],
  ['cone', 'Cone', 'Add a cone at the cursor', SVG.cone],
];
const GIZMOS = [
  ['translate', 'Move', 'Move the selected feature'],
  ['rotate', 'Rotate', 'Rotate the selected feature'],
  ['scale', 'Scale', 'Scale the selected feature'],
];
let currentGizmo = 'translate';   // three.js TransformControls default

// The feature the shared `selection` currently points at (or null). Selection is
// synced across viewport/outliner/inspector — we only READ it here.
function selFeature(vctx) {
  const sel = vctx.store.selection;
  if (!sel) return null;
  return (vctx.store.doc.features || []).find((f) => f.id === sel) || null;
}
function selLabel(f) { return f ? (f.name || f.id) : ''; }
let aiPanel = null;
let _undoKey = null;   // document keydown handler, live only while part-tools is mounted

// Persist the shared doc through the CAD save API (PUT when it has an id, else POST
// to create + adopt the new id). vctx.api is bound to /cad/api.
function downloadText(text, name, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

// Export the live viewport meshes (the same vp.group the legacy editor exported).
function exportModel(vctx, fmt) {
  const vp = vctx.store.viewport;
  if (!vp || !vp.group || !vp.group.children.length) {
    if (vctx.setStatus) vctx.setStatus('Nothing to export.', true);
    return;
  }
  const fname = String(vctx.store.doc.name || 'model').replace(/[^a-z0-9_-]+/gi, '_') || 'model';
  try {
    if (fmt === 'stl') {
      downloadText(new STLExporter().parse(vp.group), fname + '.stl', 'model/stl');
      if (vctx.setStatus) vctx.setStatus('Exported STL');
    } else {
      new GLTFExporter().parse(vp.group, (gltf) => {
        downloadText(JSON.stringify(gltf, null, 2), fname + '.gltf', 'model/gltf+json');
        if (vctx.setStatus) vctx.setStatus('Exported glTF');
      }, () => { if (vctx.setStatus) vctx.setStatus('glTF export failed', true); }, {});
    }
  } catch (e) {
    if (vctx.setStatus) vctx.setStatus('Export failed: ' + e, true);
  }
}

function applySnap(vctx) {
  const vp = vctx.store.viewport; if (!vp) return;
  const on = vctx.pane.querySelector('[data-pt-snap]').checked;
  const step = parseFloat(vctx.pane.querySelector('[data-pt-snapstep]').value);
  vp.setSnap(on, step);
}

function selectedToCursor(vctx) {
  const vp = vctx.store.viewport; const sel = vctx.store.selection;
  if (!vp || !sel) { if (vctx.setStatus) vctx.setStatus('Select a feature first', true); return; }
  const f = (vctx.store.doc.features || []).find((x) => x.id === sel);
  if (!f) return;
  const c = vp.cursor || { x: 0, y: 0, z: 0 };
  f.at = [Math.round(c.x), Math.round(c.y), Math.round(c.z)];
  vctx.store.notifyDoc();
}

async function saveDoc(vctx) {
  const doc = vctx.store.doc;
  try {
    let r;
    if (doc.id) {
      r = await vctx.api('/documents/' + encodeURIComponent(doc.id),
        { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document: doc }) });
    } else {
      r = await vctx.api('/documents',
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: doc.name || 'Untitled', document: doc }) });
      if (r && r.ok && r.id) doc.id = r.id;
    }
    if (vctx.setStatus) vctx.setStatus((r && r.ok) ? 'Saved' : ('Save failed: ' + ((r && r.error) || '?')), !(r && r.ok));
  } catch (e) {
    if (vctx.setStatus) vctx.setStatus('Save failed: ' + e, true);
  }
}

// ── View (DOM toolbar wired to the pure store mutations in eos-cad-part-ops) ──

const STYLES = `
  .cadv-pt { height: 100%; overflow-y: auto; padding: 12px; box-sizing: border-box;
    border-left: 1px solid var(--border); border-top: 1px solid var(--border);
    background: var(--bg); }
  .cadv-pt .pt-sec { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 4px 0 8px; }
  .cadv-pt .pt-grid { margin-bottom: 16px; }
  .cadv-pt .pt-row { margin-bottom: 8px; }
  .cadv-pt .pt-fill { flex: 1; }
  .cadv-pt .eos-tool-row > .eos-tool-field { flex: 1; }
  .cadv-pt textarea.eos-tool-field { min-height: 64px; margin-bottom: 8px; }
  .cadv-pt label { min-width: 0; }
  .cadv-pt .pt-note { font-size: 11px; color: var(--muted); line-height: 1.5; margin-top: 8px; }
  .cadv-pt .pt-prim { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
  .cadv-pt .pt-ico { display: inline-flex; flex: 0 0 auto; line-height: 0; }
  .cadv-pt .pt-ico svg { display: block; }
  .cadv-pt .pt-lbl { overflow: hidden; text-overflow: ellipsis; }
  .cadv-pt .pt-gizmos { display: flex; margin-bottom: 16px; }
  .cadv-pt .pt-gizmos .eos-tool-segment { flex: 1; }
  @media (max-width: 640px), (pointer: coarse) {
    .cadv-pt [data-pt-pval], .cadv-pt [data-pt-snapstep] { max-width: none !important; }
  }
`;

const MARKUP = `
  <div class="cadv-pt">
    <div class="pt-row eos-tool-row"><button class="eos-tool-btn eos-tool-btn-primary pt-fill" data-pt-save title="Save this CAD document">Save</button></div>
    <div class="pt-row eos-tool-row">
      <button class="eos-tool-btn pt-fill" data-pt-undo title="Undo (Ctrl+Z)" disabled>&#8630; Undo</button>
      <button class="eos-tool-btn pt-fill" data-pt-redo title="Redo (Ctrl+Shift+Z)" disabled>&#8631; Redo</button>
    </div>
    <div data-pt-ai></div>
    <div class="pt-sec">Add primitive</div>
    <div class="pt-grid eos-tool-grid" data-pt-prims></div>
    <div class="pt-sec">Transform (selected)</div>
    <div class="eos-tool-segmented pt-gizmos" role="group" aria-label="Transform mode" data-pt-gizmos></div>
    <div class="pt-sec">Snap &amp; cursor</div>
    <div class="pt-row eos-tool-row">
      <label style="display:flex;align-items:center;gap:5px;flex:1;font-size:12px;color:var(--muted)">
        <input type="checkbox" data-pt-snap> snap</label>
      <input class="eos-tool-field" type="number" step="any" data-pt-snapstep value="1" aria-label="snap step" style="max-width:70px">
    </div>
    <div class="pt-grid eos-tool-grid">
      <button class="eos-tool-btn" data-pt-curorigin title="Move the cursor to the origin">Cursor&rarr;0</button>
      <button class="eos-tool-btn" data-pt-curselected title="Move the cursor to the selected feature">Cursor&rarr;sel</button>
      <button class="eos-tool-btn" data-pt-seltocur title="Move the selected feature to the cursor">Sel&rarr;cursor</button>
    </div>
    <div class="pt-sec">Export</div>
    <div class="pt-row eos-tool-row">
      <button class="eos-tool-btn pt-fill" data-pt-stl title="Export the visible model as STL">STL</button>
      <button class="eos-tool-btn pt-fill" data-pt-gltf title="Export the visible model as glTF">glTF</button>
    </div>
    <div class="pt-sec">Add parameter</div>
    <div class="pt-row eos-tool-row">
      <input class="eos-tool-field" data-pt-pname placeholder="name (e.g. r)" aria-label="parameter name">
      <input class="eos-tool-field" data-pt-pval placeholder="value" type="number" step="any" aria-label="parameter value" style="max-width:90px">
    </div>
    <div class="pt-row eos-tool-row"><button class="eos-tool-btn pt-fill" data-pt-addparam title="Add a named numeric parameter">Add parameter</button></div>
    <div class="pt-note">Primitives spawn at the cursor. Drafted parts stay as proposals until you apply them; exports use the live viewport.</div>
  </div>
`;

function fillGrid(pane, sel, items, onClick, opts) {
  const grid = pane.querySelector(sel);
  if (!grid || grid.childElementCount) return;
  opts = opts || {};
  items.forEach(([key, label, title, icon]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'eos-tool-btn' + (opts.cls ? ' ' + opts.cls : '');
    // label + icon are trusted module constants (never user input).
    if (icon) b.innerHTML = '<span class="pt-ico">' + icon + '</span><span class="pt-lbl">' + label + '</span>';
    else b.textContent = label;
    if (title) b.title = title;
    b.addEventListener('click', () => onClick(key, label));
    grid.appendChild(b);
  });
}

// Gizmo mode as a segmented control that reflects the active transform mode.
function markGizmo(box) {
  box.querySelectorAll('[data-gizmo]').forEach((b) => {
    const on = b.dataset.gizmo === currentGizmo;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}
function fillGizmos(vctx) {
  const box = vctx.pane.querySelector('[data-pt-gizmos]');
  if (!box) return;
  if (!box.childElementCount) {
    GIZMOS.forEach(([mode, label, title]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'eos-tool-segment';
      b.dataset.gizmo = mode;
      b.textContent = label;
      b.title = title;
      b.addEventListener('click', () => {
        const vp = vctx.store.viewport;
        if (vp) vp.setGizmoMode(mode);
        currentGizmo = mode;
        markGizmo(box);
      });
      box.appendChild(b);
    });
  }
  markGizmo(box);   // re-apply on every render so the highlight survives re-renders
}

function wire(pane, sel, fn) {
  const b = pane.querySelector(sel);
  if (b && !b._wired) { b._wired = true; b.addEventListener('click', fn); }
}

function render(vctx) {
  const vp = () => vctx.store.viewport;
  fillGrid(vctx.pane, '[data-pt-prims]', PRIMITIVES, (op, label) => {
    addPrimitive(vctx.store, op); if (vctx.setStatus) vctx.setStatus('Added ' + label);
  }, { cls: 'pt-prim' });
  fillGizmos(vctx);
  wire(vctx.pane, '[data-pt-save]', () => saveDoc(vctx));
  wire(vctx.pane, '[data-pt-stl]', () => exportModel(vctx, 'stl'));
  wire(vctx.pane, '[data-pt-gltf]', () => exportModel(vctx, 'gltf'));
  if (!aiPanel) {
    aiPanel = createAiPanel({
      propose: (payload) => vctx.api('/ai/propose', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
      getDoc: () => vctx.store.doc,
      hasContent: () => (vctx.store.doc.features || []).length > 0,
      getSelection: () => {
        const feature = selFeature(vctx);
        return feature ? { id: feature.id, label: selLabel(feature) } : null;
      },
      clearSelection: () => vctx.store.select(null),
      applyDoc: (doc) => {
        vctx.store.pushUndo();                 // AI apply is reversible (Ctrl+Z)
        doc.id = vctx.store.doc.id || null;
        vctx.store.setDoc(doc);
      },
      describeProposal: (doc, proposal) => {
        const count = (doc.features || []).length;
        if (proposal.mode !== 'edit') return 'Proposed: ' + count + ' features — review then apply.';
        return proposal.selection
          ? 'Proposed change to ' + proposal.selection.label + ' (' + count + ' features) — review then apply.'
          : 'Proposed revision (' + count + ' features) — review then apply.';
      },
      placeholders: {
        new: 'Describe a new part — e.g. a 20mm cube with a 6mm hole',
        edit: 'Describe a change to this part — e.g. add a 6mm hole in each corner',
      },
      pill: { app: 'cad', domain: 'code', minAbility: 'standard' },
      suggest: { app: 'cad', field: 'description' },   // [[provides.field_suggest]]
      setStatus: vctx.setStatus,
    });
    aiPanel.mountInto(vctx.pane.querySelector('[data-pt-ai]'));
  } else {
    aiPanel.refresh();
  }
  wire(vctx.pane, '[data-pt-curorigin]', () => { if (vp()) vp().setCursor(0, 0, 0); });
  wire(vctx.pane, '[data-pt-curselected]', () => { if (vp()) vp().cursorToSelectedMesh(); });
  wire(vctx.pane, '[data-pt-seltocur]', () => selectedToCursor(vctx));
  const snapEl = vctx.pane.querySelector('[data-pt-snap]');
  if (snapEl && !snapEl._wired) {
    snapEl._wired = true;
    snapEl.addEventListener('change', () => applySnap(vctx));
    vctx.pane.querySelector('[data-pt-snapstep]').addEventListener('input', () => applySnap(vctx));
  }
  wire(vctx.pane, '[data-pt-addparam]', () => {
    const nameEl = vctx.pane.querySelector('[data-pt-pname]');
    const valEl = vctx.pane.querySelector('[data-pt-pval]');
    if (addParam(vctx.store, nameEl.value, valEl.value)) { nameEl.value = ''; valEl.value = ''; }
  });

  // Undo / redo — buttons + Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y). The store owns the
  // snapshot history; every part view + the viewport re-render from the restored
  // 'doc'. Re-runs on the 'history' event to keep the buttons' enabled state live.
  const undoBtn = vctx.pane.querySelector('[data-pt-undo]');
  const redoBtn = vctx.pane.querySelector('[data-pt-redo]');
  if (undoBtn && !undoBtn._wired) {
    undoBtn._wired = true;
    undoBtn.addEventListener('click', () => { if (vctx.store.undo() && vctx.setStatus) vctx.setStatus('Undo'); });
    redoBtn.addEventListener('click', () => { if (vctx.store.redo() && vctx.setStatus) vctx.setStatus('Redo'); });
  }
  if (undoBtn) undoBtn.disabled = !vctx.store.canUndo;
  if (redoBtn) redoBtn.disabled = !vctx.store.canRedo;
  if (!_undoKey) {
    _undoKey = (e) => {
      const tag = e.target && e.target.tagName;
      if (tag && /^(INPUT|TEXTAREA|SELECT)$/.test(tag)) return;   // don't hijack field editing
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = (e.key || '').toLowerCase();
      if (k === 'z' && !e.shiftKey) { if (vctx.store.undo()) e.preventDefault(); }
      else if (k === 'y' || (k === 'z' && e.shiftKey)) { if (vctx.store.redo()) e.preventDefault(); }
    };
    document.addEventListener('keydown', _undoKey);
  }
}

const _v = defineView({
  id: 'part-tools', styles: STYLES, markup: MARKUP,
  // 'select' keeps the "Editing: X" chip + placeholder live; 'history' keeps the
  // Undo/Redo buttons' enabled state live.
  events: ['doc', 'select', 'history'],
  mount(vctx) { render(vctx); },
  update(vctx) { render(vctx); },
  teardown() {
    if (aiPanel) aiPanel.teardown();
    aiPanel = null;
    if (_undoKey) { document.removeEventListener('keydown', _undoKey); _undoKey = null; }
  },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
