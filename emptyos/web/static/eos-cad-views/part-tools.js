// eos-cad-views/part-tools.js — the part-editor TOOLBAR as a layout view for
// the `part-edit` layout (the legacy-workspace retirement's stage-5 result,
// .claude/rules/cad-layouts.md — now complete; part-workspace.js is deleted).
// Mutates store.doc + calls store.notifyDoc(), which fires the 'doc' event
// the shared viewport-3d view re-renders from.
//
// Owns: primitives, params, AI draft/edit, boolean-via-pick, gizmo modes,
// snap/ortho, STL/glTF/STEP export, the 2D-drawing trigger, visual check,
// hand tracking, live collab, and the extension dock.
//
// Does NOT own: Save, Undo, Redo. Those are the layout-host shell's job for
// this layout (part-edit is one of the layouts where the shell's generic
// Save/Undo/Redo toolbar is shown — see layout-host.html's `persistent`
// condition). A panel-local copy of all three used to live here too; removed
// 2026-08-22 after a UI walk found real drift between the two Save paths
// (the panel one never cleared the store's dirty flag or updated the URL's
// `?id=` on a first save) and a worse bug in Undo/Redo — the panel's own
// document-level Ctrl+Z handler and the shell's window-level one both called
// store.undo() on the same store for one keypress, so Ctrl+Z silently undid
// two steps. Don't reintroduce a panel-local Save/Undo/Redo here.

import { defineView } from '/static/eos-cad-view.js';
import { addPrimitive, addParam } from '/static/eos-cad-part-ops.js';
import { createAiPanel } from '/static/eos-cad-ai-panel.js';
import { createHandInput } from '/static/eos-cad-hand-input.js';
import { createCollabClient } from '/static/eos-cad-collab.js';
import { isSameOriginWsUrl } from '/static/eos-cad-collab-guard.js';
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
let handInput = null;  // Phase F — webcam hand-gesture input, lazy (no camera/model load until toggled on)
let collabClient = null;  // Phase C — real-time multiplayer, lazy (no Yjs/WS load until toggled on)

// Persist the shared doc through the CAD save API (PUT when it has an id, else POST
// to create + adopt the new id). vctx.api is bound to /cad/api.
function downloadText(text, name, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

function fileBaseName(doc) {
  return String((doc && doc.name) || 'model').replace(/[^a-z0-9_-]+/gi, '_') || 'model';
}

// Export the live viewport meshes (the same vp.group the legacy editor exported).
// Approximate mesh (CSG preview), not exact B-rep — see exportStep for the real solid.
function exportModel(vctx, fmt) {
  const vp = vctx.store.viewport;
  if (!vp || !vp.group || !vp.group.children.length) {
    if (vctx.setStatus) vctx.setStatus('Nothing to export.', true);
    return;
  }
  const fname = fileBaseName(vctx.store.doc);
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

// Render the Tier-1 shape-validation verdict from a /compile response as a
// small status chip — this is the "✗ weird shape" feedback that was lost when
// the legacy part-workspace was retired (see .claude/rules/model-ability.md
// § "Validity gate"). `r` is the raw /compile JSON: {ok, validation, warnings,
// error}. Shows nothing (clears) when there's no validation data to show.
function renderShapeStatus(vctx, r) {
  const el = vctx.pane.querySelector('[data-pt-shape-status]');
  if (!el) return;
  el.className = 'pt-shape-status';
  const validation = r && r.validation;
  const hard = (validation && validation.violations || []).filter((v) => v.severity === 'hard');
  const soft = (validation && validation.violations || []).filter((v) => v.severity === 'soft');
  if (hard.length) {
    el.classList.add('pt-shape-err');
    el.textContent = '✗ weird shape — ' + hard.map((v) => v.code + ': ' + v.message).join('; ');
  } else if (soft.length) {
    el.classList.add('pt-shape-warn');
    el.textContent = '⚠ ' + soft.length + ' shape warning' + (soft.length > 1 ? 's' : '') + ' — ' + soft.map((v) => v.code).join(', ');
  } else if (validation) {
    el.classList.add('pt-shape-ok');
    el.textContent = '✓ shape OK';
  } else {
    el.textContent = '';
  }
}

// Compile the tree to an exact B-rep solid (CadQuery) and download the STEP
// file. Unlike STL/glTF (approximate mesh from the live viewport), this is a
// real server-side compile round-trip and needs a saved document id.
async function exportStep(vctx) {
  const doc = vctx.store.doc;
  if (!doc.id) {
    if (vctx.setStatus) vctx.setStatus('Save the document first, then export STEP.', true);
    return;
  }
  if (vctx.setStatus) vctx.setStatus('Compiling exact geometry…');
  try {
    const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/compile', { method: 'POST' });
    renderShapeStatus(vctx, r);
    if (r && r.ok) {
      downloadText(r.step_text || '', fileBaseName(doc) + '.step', 'model/step');
      const warn = r.warnings && r.warnings.length;
      if (vctx.setStatus) vctx.setStatus('Exported STEP' + (warn ? ' (see shape check below)' : ''));
    } else if (vctx.setStatus) {
      vctx.setStatus('STEP export failed: ' + ((r && r.error) || '?'), true);
    }
  } catch (e) {
    if (vctx.setStatus) vctx.setStatus('STEP export failed: ' + e, true);
  }
}

// Generate (or regenerate) the linked 2D orthographic drawing for this part
// and open it in the 2D drawing editor. Needs a saved document id.
async function generateDrawing(vctx) {
  const doc = vctx.store.doc;
  if (!doc.id) {
    if (vctx.setStatus) vctx.setStatus('Save the document first, then generate a drawing.', true);
    return;
  }
  if (vctx.setStatus) vctx.setStatus('Generating 2D drawing…');
  try {
    const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/draft', { method: 'POST' });
    if (r && r.ok) {
      if (vctx.setStatus) vctx.setStatus(r.regenerated ? 'Drawing updated' : 'Drawing generated');
      window.location.href = r.url;
    } else if (vctx.setStatus) {
      vctx.setStatus('Drawing generation failed: ' + ((r && r.error) || '?'), true);
    }
  } catch (e) {
    if (vctx.setStatus) vctx.setStatus('Drawing generation failed: ' + e, true);
  }
}

// Toggle webcam hand-gesture input (Phase F — a differentiator beyond
// CadXStudio, which has no hand/XR input at all). 100% client-side — no
// webcam frame is ever sent anywhere, see eos-cad-hand-input.js's own
// docstring. Purely additive: mouse/keyboard/gizmo input is untouched
// whether this is on or off.
async function toggleHandInput(vctx) {
  const btn = vctx.pane.querySelector('[data-pt-hand]');
  const statusEl = vctx.pane.querySelector('[data-pt-hand-status]');
  const setHandStatus = (msg, isErr) => {
    if (statusEl) { statusEl.textContent = msg || ''; statusEl.className = 'pt-shape-status' + (isErr ? ' pt-shape-err' : msg ? ' pt-shape-ok' : ''); }
  };
  if (!handInput) {
    handInput = createHandInput({ store: vctx.store, onStatus: setHandStatus });
  }
  if (handInput.isActive()) {
    handInput.stop();
    if (btn) { btn.classList.remove('active'); btn.setAttribute('aria-pressed', 'false'); }
    return;
  }
  if (btn) btn.disabled = true;
  const r = await handInput.start();
  if (btn) btn.disabled = false;
  if (r.ok) {
    if (btn) { btn.classList.add('active'); btn.setAttribute('aria-pressed', 'true'); }
  }
}

// Phase C — real-time multiplayer. Toggle: start a live session (as the
// document owner unless a shared invite link supplied a different principal
// + ws url) or stop one. Needs a saved document id (the room is 1:1 with
// the CAD document).
function setCollabStatus(vctx, msg, isErr) {
  const el = vctx.pane.querySelector('[data-pt-collab-status]');
  if (el) { el.textContent = msg || ''; el.className = 'pt-shape-status' + (isErr ? ' pt-shape-err' : msg ? ' pt-shape-ok' : ''); }
}

function setCollabPeers(vctx, names) {
  const el = vctx.pane.querySelector('[data-pt-collab-peers]');
  if (!el) return;
  const others = (names || []).filter((n) => n && n !== _collabPrincipal);
  el.textContent = others.length ? others.length + ' also here: ' + others.join(', ') : '';
}

let _collabPrincipal = null;   // set once a session starts, read by setCollabPeers to exclude self

async function toggleCollab(vctx) {
  const btn = vctx.pane.querySelector('[data-pt-collab]');
  const doc = vctx.store.doc;
  if (collabClient && collabClient.isActive()) {
    collabClient.stop();
    if (btn) { btn.classList.remove('active'); btn.setAttribute('aria-pressed', 'false'); }
    setCollabStatus(vctx, '');
    setCollabPeers(vctx, []);
    return;
  }
  if (!doc.id) {
    setCollabStatus(vctx, 'Save the document first, then start a live session.', true);
    return;
  }
  if (btn) btn.disabled = true;

  // A shared invite link carries live_ws + live_principal — join AS that
  // principal without calling /live/start (which would try to act as the
  // document owner, the wrong identity for an invited collaborator).
  const params = new URLSearchParams(window.location.search);
  const sharedWs = params.get('live_ws');
  const sharedPrincipal = params.get('live_principal');

  let wsUrl, principal, seed;
  if (sharedWs && sharedPrincipal) {
    // A crafted invite link could set live_ws to an attacker-controlled
    // WebSocket — never connect to a target the query string alone names.
    // The server only ever mints same-origin URLs, so anything else is forged.
    if (!isSameOriginWsUrl(sharedWs, window.location.href)) {
      if (btn) btn.disabled = false;
      setCollabStatus(vctx, 'Invite link is invalid or points off-site — refusing to join.', true);
      return;
    }
    wsUrl = sharedWs; principal = sharedPrincipal; seed = false;
  } else {
    const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/live/start', { method: 'POST' });
    if (!r || !r.ok) {
      if (btn) btn.disabled = false;
      setCollabStatus(vctx, 'Could not start a live session: ' + ((r && r.error) || '?'), true);
      return;
    }
    wsUrl = r.ws_url; principal = r.principal; seed = !!r.created;
  }

  _collabPrincipal = principal;
  collabClient = createCollabClient({
    store: vctx.store, wsUrl, principal, seed,
    onStatus: (msg, isErr) => setCollabStatus(vctx, msg, isErr),
    onPeers: (names) => setCollabPeers(vctx, names),
  });
  const res = await collabClient.start();
  if (btn) btn.disabled = false;
  if (res.ok) {
    if (btn) { btn.classList.add('active'); btn.setAttribute('aria-pressed', 'true'); }
  }
}

async function inviteCollaborator(vctx) {
  const doc = vctx.store.doc;
  if (!doc.id) { setCollabStatus(vctx, 'Save the document first.', true); return; }
  const nameEl = vctx.pane.querySelector('[data-pt-collab-name]');
  const name = (nameEl && nameEl.value || '').trim();
  if (!name) { setCollabStatus(vctx, 'Name the collaborator first.', true); return; }
  const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/live/invite', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ principal: name, level: 'write' }),
  });
  const linkEl = vctx.pane.querySelector('[data-pt-collab-link]');
  if (r && r.ok) {
    const url = new URL(window.location.href);
    url.searchParams.set('live_ws', r.ws_url);
    url.searchParams.set('live_principal', name);
    if (linkEl) { linkEl.value = url.toString(); linkEl.hidden = false; linkEl.select(); }
    setCollabStatus(vctx, "Invited '" + name + "' — copy the link below and send it to them.");
  } else {
    if (linkEl) linkEl.hidden = true;
    setCollabStatus(vctx, 'Invite failed: ' + ((r && r.error) || '?'), true);
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

// Grade the current viewport render against a stated intent via the
// (dark-flagged) vision-critic endpoint. Manual trigger, not automatic — see
// generate.py::_visual_critic_enabled's docstring for why this checks the
// APPLIED viewport, not a not-yet-applied proposal. Off by default: the
// endpoint itself refuses cleanly and this renders that refusal as the
// status message, same degrade-and-explain pattern as the CadQuery-missing
// message on STEP export.
function renderCritiqueResult(vctx, r) {
  const el = vctx.pane.querySelector('[data-pt-critique-result]');
  if (!el) return;
  if (!r) { el.textContent = ''; return; }
  if (!r.ok) { el.textContent = r.error || 'Visual check failed.'; el.className = 'pt-shape-status pt-shape-err'; return; }
  const c = r.critique || {};
  const bits = ['Score ' + c.score + '/10'];
  (c.mismatches || []).forEach((m) => bits.push('✗ ' + m));
  (c.matches || []).forEach((m) => bits.push('✓ ' + m));
  el.textContent = bits.join(' · ');
  el.className = 'pt-shape-status ' + (c.score >= 7 ? 'pt-shape-ok' : c.score >= 5 ? 'pt-shape-warn' : 'pt-shape-err');
}

async function runVisualCheck(vctx) {
  const doc = vctx.store.doc;
  const vp = vctx.store.viewport;
  const intentEl = vctx.pane.querySelector('[data-pt-critique-intent]');
  const intent = (intentEl && intentEl.value || '').trim();
  if (!doc.id) {
    if (vctx.setStatus) vctx.setStatus('Save the document first, then run a visual check.', true);
    return;
  }
  if (!intent) {
    if (vctx.setStatus) vctx.setStatus('Describe what this part is supposed to be first.', true);
    return;
  }
  if (!vp || !vp.renderer || !vp.domElement) {
    if (vctx.setStatus) vctx.setStatus('Nothing rendered to check.', true);
    return;
  }
  if (vctx.setStatus) vctx.setStatus('Checking against intent…');
  renderCritiqueResult(vctx, null);
  try {
    vp.renderer.render(vp.scene, vp.camera);  // force a fresh frame before capture
    const dataUrl = vp.domElement.toDataURL('image/png');
    const image_b64 = dataUrl.split(',')[1] || '';
    const r = await vctx.api('/documents/' + encodeURIComponent(doc.id) + '/visual-check', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64, intent }),
    });
    renderCritiqueResult(vctx, r);
    if (vctx.setStatus) vctx.setStatus(r && r.ok ? 'Visual check done' : ('Visual check failed: ' + ((r && r.error) || '?')), !(r && r.ok));
  } catch (e) {
    if (vctx.setStatus) vctx.setStatus('Visual check failed: ' + e, true);
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
  .cadv-pt .pt-shape-status { font-size: 11px; line-height: 1.5; margin-top: 6px; min-height: 1.5em; }
  .cadv-pt .pt-shape-status.pt-shape-ok { color: var(--green); }
  .cadv-pt .pt-shape-status.pt-shape-warn { color: var(--amber); }
  .cadv-pt .pt-shape-status.pt-shape-err { color: var(--red); }
  @media (max-width: 640px), (pointer: coarse) {
    .cadv-pt [data-pt-pval], .cadv-pt [data-pt-snapstep] { max-width: none !important; }
  }
`;

const MARKUP = `
  <div class="cadv-pt">
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
    <div class="pt-row eos-tool-row">
      <button class="eos-tool-btn pt-fill" data-pt-hand aria-pressed="false" title="Webcam hand tracking: select a feature, then pinch to grab and drag it. Video never leaves your browser.">&#128400; Hand tracking</button>
    </div>
    <div class="pt-shape-status" data-pt-hand-status></div>
    <div class="pt-row eos-tool-row">
      <button class="eos-tool-btn pt-fill" data-pt-collab aria-pressed="false" title="Real-time multiplayer editing — reuses the same live-collaboration relay other EmptyOS live docs use.">&#127760; Live collab</button>
    </div>
    <div class="pt-shape-status" data-pt-collab-status></div>
    <div class="pt-shape-status" data-pt-collab-peers></div>
    <div class="pt-row eos-tool-row">
      <input class="eos-tool-field pt-fill" data-pt-collab-name placeholder="collaborator name" aria-label="collaborator name">
      <button class="eos-tool-btn" data-pt-collab-invite title="Grant this name write access and get a shareable join link">Invite</button>
    </div>
    <input class="eos-tool-field" data-pt-collab-link readonly hidden aria-label="shareable invite link" onclick="this.select()">
    <div class="pt-sec">Export</div>
    <div class="pt-row eos-tool-row">
      <button class="eos-tool-btn pt-fill" data-pt-stl title="Export the visible model as STL (approximate mesh)">STL</button>
      <button class="eos-tool-btn pt-fill" data-pt-gltf title="Export the visible model as glTF (approximate mesh)">glTF</button>
      <button class="eos-tool-btn pt-fill" data-pt-step title="Compile to exact B-rep and export STEP (requires a saved document)">STEP</button>
    </div>
    <div class="pt-shape-status" data-pt-shape-status></div>
    <div class="pt-row eos-tool-row">
      <button class="eos-tool-btn pt-fill" data-pt-drawing title="Generate a dimensioned 2D drawing (front/top/right + isometric) from this part">Generate drawing</button>
    </div>
    <div class="pt-row eos-tool-row">
      <input class="eos-tool-field pt-fill" data-pt-critique-intent placeholder="what was this part meant to be?" aria-label="intended design intent" title="Needs apps.cad.feature.visual-critic.enabled — off by default">
    </div>
    <div class="pt-row eos-tool-row"><button class="eos-tool-btn pt-fill" data-pt-critique title="Grade the current render against the intent above via a vision-LLM (dark-flagged, off by default)">Visual check</button></div>
    <div class="pt-shape-status" data-pt-critique-result></div>
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
  wire(vctx.pane, '[data-pt-stl]', () => exportModel(vctx, 'stl'));
  wire(vctx.pane, '[data-pt-gltf]', () => exportModel(vctx, 'gltf'));
  wire(vctx.pane, '[data-pt-step]', () => exportStep(vctx));
  wire(vctx.pane, '[data-pt-drawing]', () => generateDrawing(vctx));
  wire(vctx.pane, '[data-pt-critique]', () => runVisualCheck(vctx));
  wire(vctx.pane, '[data-pt-collab]', () => toggleCollab(vctx));
  wire(vctx.pane, '[data-pt-collab-invite]', () => inviteCollaborator(vctx));
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
  wire(vctx.pane, '[data-pt-hand]', () => toggleHandInput(vctx));
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

  // Undo/Redo (buttons + Ctrl+Z/Ctrl+Shift+Z) are the layout-host shell's job
  // for this layout (layout-host.html's UNDO/REDO toolbar buttons + its own
  // window keydown handler) — part-edit is one of the 7 "persistent" layouts
  // that already gets them for free. A second, panel-local Undo/Redo used to
  // live here too; removed 2026-08-22 (UI-walk finding: on part-edit the
  // shell's window-level Ctrl+Z handler and this panel's document-level one
  // both called store.undo() on the same store for a single keypress — one
  // Ctrl+Z silently undid TWO steps, not one). Don't re-add a panel-local
  // copy of either — see .claude/rules/cad-layouts.md on the shell's
  // `persistent` doc-controls condition.
}

const _v = defineView({
  id: 'part-tools', styles: STYLES, markup: MARKUP,
  // 'select' keeps the "Editing: X" chip + placeholder live.
  events: ['doc', 'select'],
  mount(vctx) { render(vctx); },
  update(vctx) { render(vctx); },
  teardown() {
    if (aiPanel) aiPanel.teardown();
    aiPanel = null;
    if (handInput) handInput.stop();   // release the webcam — never leak it across a layout switch
    handInput = null;
    if (collabClient) collabClient.stop();   // close the live-collab WebSocket — never leak it across a layout switch
    collabClient = null;
  },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
