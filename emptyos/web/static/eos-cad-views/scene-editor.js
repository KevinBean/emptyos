// eos-cad-views/scene-editor.js — the engineering-scene editor as a layout view
// (stage 3 of legacy-workspace retirement; .claude/rules/cad-layouts.md). Unlike the
// part editor (which mutates the store doc directly), a scene is API-driven: the
// editable source is the eos-scene/1 SCENE (equipment instances), and the store doc
// is its COMPILED eos-cad/1 caddoc (read-only render). Each add/delete/edit hits the
// engineering-scene API, then re-fetches the caddoc into the store so viewport-3d
// re-renders. Used by the `site-edit` layout. Scene id comes from the URL ?id=.
//
// PORTED (full parity): equipment palette, instance roster (list/select/delete),
// inspector (label/position/electrical-node link), cables (add/delete), voltage
// overlay, geo-anchor, new-scene + import-network. The editable counterpart that
// lets site-layout-workspace be retired.

import { defineView, esc } from '/static/eos-cad-view.js';
import { createAiPanel } from '/static/eos-cad-ai-panel.js';

const PREFIX = '/engineering-scene/api';

function api(method, url, body) {
  return fetch(url, body === undefined ? { method }
    : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then((r) => r.json());
}
function sceneId() { return new URLSearchParams(location.search).get('id') || ''; }

const STYLES = `
  .cadv-se { height: 100%; overflow-y: auto; padding: 10px 12px; box-sizing: border-box;
    border-left: 1px solid var(--border); background: var(--bg); font-size: 12.5px; }
  .cadv-se .sec { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 10px 0 6px; }
  .cadv-se .pal-row, .cadv-se .eq-row { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 6px; cursor: pointer; }
  .cadv-se .pal-row:hover, .cadv-se .eq-row:hover { background: var(--panel); }
  .cadv-se .eq-row.sel { background: var(--panel); outline: 1px solid var(--accent); }
  .cadv-se .sw { width: 11px; height: 11px; border-radius: 3px; border: 1px solid var(--border); flex: none; }
  .cadv-se .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cadv-se .muted { color: var(--muted); font-size: 11px; }
  .cadv-se .se-del.eos-tool-icon-btn { margin-left: auto; width: 28px; min-width: 28px; min-height: 28px; padding: 0; }
  .cadv-se label { display: block; font-size: 11px; color: var(--muted); margin: 6px 0 2px; }
  .cadv-se .eos-tool-field { width: 100%; }
  .cadv-se .xyz { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 5px; }
  .cadv-se .row { display: flex; gap: 6px; margin-top: 10px; }
  .cadv-se .row .eos-tool-btn { flex: 1; }
  .cadv-se .check-row { padding: 4px 6px; margin: 3px 0; border-left: 3px solid var(--border); background: var(--panel); }
  .cadv-se .check-row.pass { border-left-color: var(--success, #3fb950); }
  .cadv-se .check-row.fail { border-left-color: var(--danger, #f85149); }
  .cadv-se .check-row.review { border-left-color: var(--warning, #d29922); }
`;

const FORMATIONS = ['auto', 'trefoil', 'flat', 'flat_spaced', 'bundle', 'triplex'];

const MARKUP = `<div class="cadv-se">
  <div class="row" style="margin-top:0">
    <button class="eos-tool-btn" data-se-newscene title="Create a new scene">+ Scene</button>
    <button class="eos-tool-btn" data-se-import title="Import a cable-network project">Import network</button>
  </div>
  <div class="row" style="margin-top:6px">
    <button class="eos-tool-btn" data-se-anchor title="Set the yard origin">📍 Anchor</button>
    <button class="eos-tool-btn" data-se-overlay title="Colour equipment by linked bus voltage" aria-pressed="false">⚡ Voltage</button>
  </div>
  <div class="sec">Add equipment</div><div data-se-palette></div>
  <div class="sec">Placed <span data-se-count class="muted"></span></div><div data-se-roster></div>
  <div class="sec">Selected</div><div data-se-insp></div>
  <div data-se-ai></div>
  <div class="sec">Engineering inputs</div><div data-se-enginputs></div>
  <div class="sec">Connections</div><div data-se-cableform></div><div data-se-cablelist></div>
  <div class="sec">Checks & deliverables</div><div data-se-checks></div>
</div>`;

function vColor(kv) {
  if (kv == null) return null;
  if (kv < 1) return '#8a8f98';
  if (kv <= 11) return '#3fb950';
  if (kv <= 33) return '#388bfd';
  return '#d29922';
}

// Voltage overlay: re-colour caddoc features by their instance's linked bus kV
// (grey = unlinked) before the store re-renders. Mutates doc in place.
function applyOverlay(doc, st) {
  const vmap = {};
  (st.nodes || []).forEach((study) => (study.buses || []).forEach((bb) => { vmap[study.study_id + '#' + bb.id] = bb.voltage_kv; }));
  const insts = (st.scene && st.scene.instances) || [];
  (doc.features || []).forEach((f) => {
    const inst = insts.find((i) => i.id === f.id);
    const ref = inst && inst.domain_ref;
    f.color = (ref && vColor(vmap[ref])) || '#3a3f46';
  });
}

async function reloadCaddoc(vctx, st) {
  const id = sceneId();
  if (!id) return;
  try {
    const r = await api('GET', PREFIX + '/scenes/' + encodeURIComponent(id) + '/caddoc');
    if (r && r.ok && r.doc) {
      r.doc.params = r.doc.params || {}; r.doc.features = r.doc.features || [];
      if (st && st.overlayOn) applyOverlay(r.doc, st);
      vctx.store.setDoc(r.doc);
    }
  } catch (e) { /* viewport keeps last render */ }
}

function anchorScene(vctx, st) {
  const id = sceneId(); if (!id) { if (vctx.setStatus) vctx.setStatus('Open a scene first', true); return; }
  if (!(window.EOS_UI && window.EOS_UI.formModal)) return;
  const o = (st.scene && st.scene.geo && st.scene.geo.origin) || [151.2093, -33.8688];
  window.EOS_UI.formModal('Anchor yard origin', [
    { key: 'lat', label: 'Latitude (°)', value: String(o[1]) },
    { key: 'lon', label: 'Longitude (°)', value: String(o[0]) }], async (v) => {
    const lat = parseFloat(v.lat), lon = parseFloat(v.lon);
    if (isNaN(lat) || isNaN(lon)) { if (vctx.setStatus) vctx.setStatus('Enter numeric lat/lon', true); return; }
    const r = await api('POST', PREFIX + '/scenes/' + encodeURIComponent(id) + '/geo-anchor', { lat, lon });
    if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus(r && r.error || 'Anchor failed', true); return; }
    if (st.scene) st.scene.geo = { origin: r.origin, crs: 'local-m' };
    if (vctx.setStatus) vctx.setStatus('Anchored at ' + lat + ', ' + lon);
  });
}

async function toggleOverlay(vctx, st) {
  st.overlayOn = !st.overlayOn;
  const btn = vctx.pane.querySelector('[data-se-overlay]');
  if (btn) {
    btn.classList.toggle('active', st.overlayOn);   // '.on' matched no CSS — '.active' fills accent
    btn.setAttribute('aria-pressed', st.overlayOn ? 'true' : 'false');
  }
  await reloadCaddoc(vctx, st);
  if (vctx.setStatus) vctx.setStatus(st.overlayOn ? 'Voltage overlay on (grey = unlinked)' : 'Overlay off');
}

function renderPalette(vctx, st) {
  const el = vctx.pane.querySelector('[data-se-palette]');
  if (!el) return;
  el.innerHTML = '';
  Object.keys(st.templates).forEach((type) => {
    const t = st.templates[type];
    const row = document.createElement('div'); row.className = 'pal-row';
    row.innerHTML = '<span class="sw" style="background:' + escAttr(t.color || '#9aa7b4') + '"></span>'
      + '<span class="nm">' + esc(t.label || type) + '</span>'
      + '<span class="muted" style="margin-left:auto">' + esc((t.size || []).map((n) => n + 'm').join('×')) + '</span>';
    row.addEventListener('click', () => addEquipment(vctx, st, type));
    el.appendChild(row);
  });
}

function renderRoster(vctx, st) {
  const el = vctx.pane.querySelector('[data-se-roster]');
  const cnt = vctx.pane.querySelector('[data-se-count]');
  if (!el) return;
  const insts = (st.scene && st.scene.instances) || [];
  if (cnt) cnt.textContent = insts.length ? '(' + insts.length + ')' : '';
  if (!insts.length) { el.innerHTML = '<div class="muted">No equipment yet — add from the palette.</div>'; return; }
  el.innerHTML = '';
  insts.forEach((i) => {
    const t = st.templates[i.type] || {};
    const row = document.createElement('div');
    row.className = 'eq-row' + (i.id === st.selected ? ' sel' : '');
    const sw = document.createElement('span'); sw.className = 'sw'; sw.style.background = t.color || '#9aa7b4';
    const nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = i.label || i.id;
    nm.addEventListener('click', () => { st.selected = i.id; vctx.store.select(i.id); renderRoster(vctx, st); renderInspector(vctx, st); });
    const tp = document.createElement('span'); tp.className = 'muted'; tp.textContent = t.label || i.type;
    const del = document.createElement('button'); del.className = 'se-del eos-tool-icon-btn'; del.textContent = '✕'; del.title = 'Delete';
    del.addEventListener('click', (e) => { e.stopPropagation(); deleteEquip(vctx, st, i.id); });
    row.append(sw, nm, tp, del);
    el.appendChild(row);
  });
}

function nodeOptions(st, sel) {
  let opts = '<option value="">— not linked —</option>';
  (st.nodes || []).forEach((study) => {
    opts += '<optgroup label="' + escAttr(study.study_title) + '">';
    (study.buses || []).forEach((b) => {
      const ref = study.study_id + '#' + b.id;
      const kv = b.voltage_kv ? ' (' + b.voltage_kv + 'kV)' : '';
      opts += '<option value="' + escAttr(ref) + '"' + (sel === ref ? ' selected' : '') + '>' + esc(b.label) + esc(kv) + '</option>';
    });
    opts += '</optgroup>';
  });
  return opts;
}

function renderInspector(vctx, st) {
  const el = vctx.pane.querySelector('[data-se-insp]');
  if (!el) return;
  const inst = ((st.scene && st.scene.instances) || []).find((x) => x.id === st.selected);
  if (!inst) { el.innerHTML = '<div class="muted">Nothing selected.</div>'; return; }
  const tpl = st.templates[inst.type] || {};
  const xyz = inst.xyz || [0, 0, 0];
  el.innerHTML =
    '<label>Label</label><input class="eos-tool-field" data-se-label value="' + escAttr(inst.label || '') + '">'
    + '<label>Type</label><input class="eos-tool-field" value="' + escAttr(tpl.label || inst.type) + '" disabled>'
    + '<label>Position [x, y, z] m</label><div class="xyz">'
    + '<input class="eos-tool-field" data-se-x value="' + escAttr(xyz[0]) + '"><input class="eos-tool-field" data-se-y value="' + escAttr(xyz[1]) + '"><input class="eos-tool-field" data-se-z value="' + escAttr(xyz[2]) + '"></div>'
    + '<label>Linked electrical node</label><select class="eos-tool-field" data-se-ref>' + nodeOptions(st, inst.domain_ref) + '</select>'
    + '<div class="row"><button class="eos-tool-btn" data-se-apply title="Apply selected equipment changes">Apply</button><button class="eos-tool-btn eos-tool-btn-danger" data-se-del title="Delete selected equipment">Delete</button></div>';
  el.querySelector('[data-se-apply]').addEventListener('click', () => applyInstance(vctx, st));
  el.querySelector('[data-se-del]').addEventListener('click', () => deleteEquip(vctx, st, inst.id));
}

function cableLen(st, c) {
  const insts = (st.scene && st.scene.instances) || [];
  const a = insts.find((i) => i.id === c.from_instance), b = insts.find((i) => i.id === c.to_instance);
  if (!a || !b) return 0;
  const pa = a.xyz || [0, 0, 0], pb = b.xyz || [0, 0, 0];
  return Math.round(Math.hypot(pa[0] - pb[0], pa[1] - pb[1], pa[2] - pb[2]) * 1000) / 1000;
}

function renderCables(vctx, st) {
  const form = vctx.pane.querySelector('[data-se-cableform]');
  const list = vctx.pane.querySelector('[data-se-cablelist]');
  if (!form || !list) return;
  const insts = ((st.scene && st.scene.instances) || []).filter((i) => i.type !== 'fence' && i.type !== 'poc_marker');
  const optI = insts.map((i) => '<option value="' + escAttr(i.id) + '">' + esc(i.label || i.id) + '</option>').join('');
  const optC = Object.keys(st.cableTypes).map((k) => '<option value="' + escAttr(k) + '">' + esc(st.cableTypes[k].label || k) + '</option>').join('');
  const optF = FORMATIONS.map((f) => '<option value="' + f + '">' + f + '</option>').join('');
  form.innerHTML = insts.length < 2
    ? '<div class="muted">Add at least two equipment items to make a connection.</div>'
    : '<label>From</label><select class="eos-tool-field" data-se-cfrom>' + optI + '</select>'
      + '<label>To</label><select class="eos-tool-field" data-se-cto>' + optI + '</select>'
      + '<label>Kind</label><select class="eos-tool-field" data-se-ckind><option value="cable">Cable</option><option value="rigid_busbar">Rigid busbar</option><option value="flexible_conductor">Flexible conductor</option></select>'
      + '<label>Cable type</label><select class="eos-tool-field" data-se-ctype>' + optC + '</select>'
      + '<label>Formation</label><select class="eos-tool-field" data-se-cfmt>' + optF + '</select>'
      + '<div class="xyz"><div><label>Voltage kV</label><input class="eos-tool-field" data-se-ckv value="132"></div><div><label>Spacing m</label><input class="eos-tool-field" data-se-cspacing value="2.5"></div><div><label>Elevation m</label><input class="eos-tool-field" data-se-celev value="4"></div></div>'
      + '<label>Manual Isc kA (blank = linked Power Study)</label><input class="eos-tool-field" data-se-cisc value="">'
      + '<div class="row"><button class="eos-tool-btn" data-se-caddbtn title="Add connection">+ Connection</button></div>';
  const addBtn = form.querySelector('[data-se-caddbtn]');
  if (addBtn) addBtn.addEventListener('click', () => addCable(vctx, st));
  const cables = (st.scene && st.scene.connections) || ((st.scene && st.scene.cables) || []).map((c) => ({ ...c, kind:'cable' }));
  list.innerHTML = '';
  cables.forEach((c) => {
    const row = document.createElement('div'); row.className = 'eq-row';
    const lbl = document.createElement('span'); lbl.className = 'nm';
    lbl.textContent = (c.kind || 'cable').replaceAll('_', ' ') + ': ' + c.from_instance + ' to ' + c.to_instance + ' / ' + cableLen(st, c) + ' m';
    const del = document.createElement('button'); del.className = 'se-del eos-tool-icon-btn'; del.textContent = 'x'; del.title = 'Delete connection';
    del.addEventListener('click', () => deleteCable(vctx, st, c.id));
    row.append(lbl, del);
    list.appendChild(row);
  });
}

function renderEngineeringInputs(vctx, st) {
  const el = vctx.pane.querySelector('[data-se-enginputs]'); if (!el) return;
  const scene = st.scene || {}, settings = scene.settings || {};
  const scenario = (settings.fire_scenarios || [])[0] || {};
  const sources = (scene.instances || []).filter((i) => i.type === 'transformer' || i.type === 'station_service_transformer');
  const options = sources.map((i) => '<option value="' + escAttr(i.id) + '"' + (i.id === scenario.source_instance ? ' selected' : '') + '>' + esc(i.label || i.id) + '</option>').join('');
  el.innerHTML = sources.length ? '<label>Oil-fire source</label><select class="eos-tool-field" data-se-fsource>' + options + '</select>'
    + '<div class="xyz"><div><label>HRR MW</label><input class="eos-tool-field" data-se-fhrr value="' + escAttr((scenario.hrr_kw || 10000) / 1000) + '"></div><div><label>Fire area m2</label><input class="eos-tool-field" data-se-farea value="' + escAttr(scenario.fire_area_m2 || 4) + '"></div><div><label>Altitude m</label><input class="eos-tool-field" data-se-alt value="' + escAttr(settings.altitude_m || 0) + '"></div></div>'
    + '<div class="row"><button class="eos-tool-btn" data-se-engapply>Apply inputs</button></div>'
    : '<div class="muted">Add a transformer to configure a heat-flux scenario.</div>';
  const apply = el.querySelector('[data-se-engapply]'); if (apply) apply.addEventListener('click', () => applyEngineeringInputs(vctx, st));
}

function renderChecks(vctx, st) {
  const el = vctx.pane.querySelector('[data-se-checks]'); if (!el) return;
  const checks = (st.scene && st.scene.checks) || { status:'not-run', results:[] };
  const drawingOn = !!(st.features && st.features.drawing_generation);
  el.innerHTML = '<div class="muted">Status: ' + esc(checks.status || 'not-run') + (checks.input_hash ? ' / ' + esc(checks.input_hash) : '') + '</div>'
    + '<div class="row"><button class="eos-tool-btn" data-se-runchecks>Run all checks</button><button class="eos-tool-btn" data-se-drawing' + (drawingOn ? '' : ' disabled') + ' title="' + (drawingOn ? 'Generate coordinated plan/elevation' : 'Dark feature: enable drawing generation in app config') + '">Drawing</button></div>'
    + '<div class="row"><button class="eos-tool-btn" data-se-deliverables' + (drawingOn ? '' : ' disabled') + '>PDF + DXF report</button></div><div data-se-checkrows></div>';
  el.querySelector('[data-se-runchecks]').addEventListener('click', () => runChecks(vctx, st));
  const drawing = el.querySelector('[data-se-drawing]'); if (drawing && drawingOn) drawing.addEventListener('click', () => generateDrawing(vctx, st, false));
  const deliverables = el.querySelector('[data-se-deliverables]'); if (deliverables && drawingOn) deliverables.addEventListener('click', () => generateDeliverables(vctx, st, false));
  const rows = el.querySelector('[data-se-checkrows]');
  (checks.results || []).slice(0, 20).forEach((r) => {
    const div = document.createElement('div');
    const verdict = r.passes === true ? 'pass' : (r.passes === false ? 'fail' : 'review');
    div.className = 'check-row ' + verdict;
    div.textContent = verdict.toUpperCase() + ' ' + (r.family || 'check') + ': ' + (r.connection || r.target || r.a || r.error || '');
    rows.appendChild(div);
  });
}

function renderAll(vctx, st) {
  renderPalette(vctx, st); renderRoster(vctx, st); renderInspector(vctx, st); renderEngineeringInputs(vctx, st); renderCables(vctx, st); renderChecks(vctx, st);
  if (st.aiPanel) st.aiPanel.refresh();
}

function mountAiPanel(vctx, st) {
  st.aiPanel = createAiPanel({
    propose: (payload) => {
      if (!sceneId() || !st.scene) return Promise.resolve({ ok: false, error: 'Open a scene first' });
      return api('POST', PREFIX + '/ai/propose', payload);
    },
    getDoc: () => st.scene,
    hasContent: () => !!(st.scene && (st.scene.instances || []).length),
    getSelection: () => {
      const inst = ((st.scene && st.scene.instances) || []).find((item) => item.id === st.selected);
      return inst ? { id: inst.id, label: inst.label || inst.id } : null;
    },
    clearSelection: () => { st.selected = null; vctx.store.select(null); renderAll(vctx, st); },
    applyDoc: async (scene) => {
      const id = sceneId();
      if (!id || !st.scene) throw new Error('open a scene first');
      const result = await api('PUT', PREFIX + '/scenes/' + encodeURIComponent(id), { scene });
      if (!result || !result.ok) throw new Error((result && result.error) || 'scene save failed');
      st.scene = result.scene;
      if (st.selected && !(st.scene.instances || []).some((item) => item.id === st.selected)) {
        st.selected = null; vctx.store.select(null);
      }
      await reloadCaddoc(vctx, st);
      renderAll(vctx, st);
    },
    confirmApply: async (scene) => {
      const before = (st.scene && st.scene.instances || []).length;
      const after = (scene.instances || []).length;
      if (after >= before) return true;
      if (!(window.EOS_UI && EOS_UI.confirm)) return false;
      return EOS_UI.confirm({
        message: 'This proposal removes ' + (before - after) + ' equipment item(s). The scene has no undo history.',
        action: 'Apply proposal', danger: true,
      });
    },
    describeProposal: (scene, proposal) => {
      const count = (scene.instances || []).length;
      const warnings = ((proposal.response && proposal.response.clearances) || []).length;
      return 'Proposed site layout with ' + count + ' equipment item(s)'
        + (warnings ? ' and ' + warnings + ' clearance warning(s)' : '') + ' — review then apply.';
    },
    placeholders: {
      new: 'Describe a site layout — e.g. transformer, switchgear, and two battery containers',
      edit: 'Describe a site change — e.g. move the selected transformer 5 m east',
    },
    pill: { app: 'engineering-scene', domain: 'code', minAbility: 'standard' },
    setStatus: vctx.setStatus,
  });
  st.aiPanel.mountInto(vctx.pane.querySelector('[data-se-ai]'));
}

async function addCable(vctx, st) {
  const id = sceneId();
  const fr = vctx.pane.querySelector('[data-se-cfrom]').value, to = vctx.pane.querySelector('[data-se-cto]').value;
  if (fr === to) { if (vctx.setStatus) vctx.setStatus('Pick two different items', true); return; }
  const kind = vctx.pane.querySelector('[data-se-ckind]').value;
  const isc = parseFloat(vctx.pane.querySelector('[data-se-cisc]').value);
  const payload = { kind, from_instance: fr, to_instance: to,
    cable_type: vctx.pane.querySelector('[data-se-ctype]').value,
    formation: vctx.pane.querySelector('[data-se-cfmt]').value,
    voltage_kv: parseFloat(vctx.pane.querySelector('[data-se-ckv]').value),
    phase_spacing_m: parseFloat(vctx.pane.querySelector('[data-se-cspacing]').value),
    elevation_m: parseFloat(vctx.pane.querySelector('[data-se-celev]').value) };
  if (!isNaN(isc) && isc > 0) payload.fault_override = { isc_3ph_ka: isc };
  const r = await api('POST', PREFIX + '/scenes/' + encodeURIComponent(id) + '/connections', payload);
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Add connection failed: ' + ((r && r.error) || '?'), true); return; }
  st.scene = r.scene; await reloadCaddoc(vctx, st); renderAll(vctx, st);
  if (vctx.setStatus) vctx.setStatus('Connection added');
}

async function deleteCable(vctx, st, cid) {
  const id = sceneId();
  const r = await api('DELETE', PREFIX + '/scenes/' + encodeURIComponent(id) + '/connections/' + encodeURIComponent(cid));
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Delete connection failed', true); return; }
  st.scene = r.scene; await reloadCaddoc(vctx, st); renderAll(vctx, st);
}

async function applyEngineeringInputs(vctx, st) {
  if (!st.scene) return;
  const source = vctx.pane.querySelector('[data-se-fsource]').value;
  const hrrMw = parseFloat(vctx.pane.querySelector('[data-se-fhrr]').value);
  const area = parseFloat(vctx.pane.querySelector('[data-se-farea]').value);
  const altitude = parseFloat(vctx.pane.querySelector('[data-se-alt]').value);
  if (!(hrrMw > 0) || !(area > 0) || isNaN(altitude)) { if (vctx.setStatus) vctx.setStatus('Engineering inputs must be numeric and positive', true); return; }
  st.scene.settings = { ...(st.scene.settings || {}), altitude_m: altitude,
    fire_scenarios: [{ source_instance: source, hrr_kw: hrrMw * 1000, fire_area_m2: area }] };
  const r = await api('PUT', PREFIX + '/scenes/' + encodeURIComponent(sceneId()), { scene: st.scene });
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Save inputs failed', true); return; }
  st.scene = r.scene; await reloadCaddoc(vctx, st); renderAll(vctx, st);
  if (vctx.setStatus) vctx.setStatus('Engineering inputs saved; checks are stale');
}

async function runChecks(vctx, st) {
  const r = await api('POST', PREFIX + '/scenes/' + encodeURIComponent(sceneId()) + '/checks/run', {});
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Checks failed: ' + ((r && r.error) || '?'), true); return; }
  st.scene.checks = r.checks; await reloadCaddoc(vctx, st); renderAll(vctx, st);
  if (vctx.setStatus) vctx.setStatus('Checks complete: ' + r.checks.status);
}

async function generateDrawing(vctx, st, confirm) {
  const r = await api('POST', PREFIX + '/scenes/' + encodeURIComponent(sceneId()) + '/drawing/generate', { confirm_regenerate: !!confirm });
  if (r && r.regeneration_required && !confirm && window.EOS_UI && EOS_UI.confirm) {
    const yes = await EOS_UI.confirm({ message:'Regenerate and replace the existing coordinated drawing?', action:'Regenerate', danger:true });
    if (yes) return generateDrawing(vctx, st, true);
  }
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Drawing failed: ' + ((r && r.error) || '?'), true); return; }
  if (vctx.setStatus) vctx.setStatus('Drawing generated: ' + r.draft_id);
}

async function generateDeliverables(vctx, st, confirm) {
  const r = await api('POST', PREFIX + '/scenes/' + encodeURIComponent(sceneId()) + '/deliverables', { confirm_regenerate: !!confirm });
  if (r && r.regeneration_required && !confirm && window.EOS_UI && EOS_UI.confirm) {
    const yes = await EOS_UI.confirm({ message:'Regenerate the drawing before creating PDF and DXF?', action:'Generate', danger:true });
    if (yes) return generateDeliverables(vctx, st, true);
  }
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Report failed: ' + ((r && r.error) || '?'), true); return; }
  if (vctx.setStatus) vctx.setStatus('Deliverables created: PDF + DXF');
}

async function addEquipment(vctx, st, type) {
  const id = sceneId(); if (!id) { if (vctx.setStatus) vctx.setStatus('Open a scene first', true); return; }
  const vp = vctx.store.viewport; const c = (vp && vp.cursor) || { x: 0, y: 0, z: 0 };
  const r = await api('POST', PREFIX + '/scenes/' + encodeURIComponent(id) + '/instances',
    { type, xyz: [Math.round(c.x), Math.round(c.y), 0] });
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Add failed: ' + ((r && r.error) || '?'), true); return; }
  st.scene = r.scene; st.selected = r.instance && r.instance.id;
  await reloadCaddoc(vctx, st); if (st.selected) vctx.store.select(st.selected);
  renderAll(vctx, st);
  if (vctx.setStatus) vctx.setStatus('Added ' + ((r.instance && (r.instance.label || r.instance.id)) || type));
}

async function deleteEquip(vctx, st, instId) {
  const id = sceneId();
  const r = await api('DELETE', PREFIX + '/scenes/' + encodeURIComponent(id) + '/instances/' + encodeURIComponent(instId));
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Delete failed', true); return; }
  st.scene = r.scene; if (st.selected === instId) st.selected = null;
  await reloadCaddoc(vctx, st); renderAll(vctx, st);
}

async function applyInstance(vctx, st) {
  const id = sceneId();
  const num = (sel) => { const v = parseFloat(vctx.pane.querySelector(sel).value); return isNaN(v) ? 0 : v; };
  const refEl = vctx.pane.querySelector('[data-se-ref]');
  const payload = {
    label: vctx.pane.querySelector('[data-se-label]').value.trim() || undefined,
    xyz: [num('[data-se-x]'), num('[data-se-y]'), num('[data-se-z]')],
    domain_ref: refEl ? (refEl.value || null) : undefined,
  };
  const r = await api('PUT', PREFIX + '/scenes/' + encodeURIComponent(id) + '/instances/' + encodeURIComponent(st.selected), payload);
  if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Apply failed', true); return; }
  st.scene = r.scene;
  await reloadCaddoc(vctx, st); renderAll(vctx, st);
  if (vctx.setStatus) vctx.setStatus('Updated');
}

function newScene(vctx) {
  if (!(window.EOS_UI && window.EOS_UI.formModal)) return;
  window.EOS_UI.formModal('New scene', [{ key: 'title', label: 'Scene name', value: 'New yard' }], async (v) => {
    const title = (v.title || '').trim(); if (!title) return;
    const r = await api('POST', PREFIX + '/scenes', { title });
    if (r && r.ok && r.id) {
      const u = new URL(location.href); u.searchParams.set('id', r.id); location.href = u.toString();   // reload on the new scene
    } else if (vctx.setStatus) { vctx.setStatus('Create failed: ' + ((r && r.error) || '?'), true); }
  });
}

async function importNetwork(vctx, st) {
  const id = sceneId(); if (!id) { if (vctx.setStatus) vctx.setStatus('Open a scene first', true); return; }
  if (!(window.EOS_UI && window.EOS_UI.formModal)) return;
  let ids = [];
  try { ids = ((await api('GET', '/cable-network/api/projects')).projects || []).map((p) => p.id); } catch (e) { /* */ }
  if (!ids.length) { if (vctx.setStatus) vctx.setStatus('No cable-network projects found', true); return; }
  window.EOS_UI.formModal('Import network — replaces this scene', [
    { key: 'project', label: 'cable-network project', type: 'select', options: ids, value: ids[0] }], async (v) => {
    if (!v.project) return;
    const r = await api('POST', PREFIX + '/scenes/' + encodeURIComponent(id) + '/import-network',
      { source: 'cable-network', project_id: v.project });
    if (!r || !r.ok) { if (vctx.setStatus) vctx.setStatus('Import failed: ' + ((r && r.error) || '?'), true); return; }
    try { const sc = await api('GET', PREFIX + '/scenes/' + encodeURIComponent(id)); st.scene = (sc && sc.scene) || null; } catch (e) { /* */ }
    await reloadCaddoc(vctx, st); renderAll(vctx, st);
    if (vctx.setStatus) vctx.setStatus('Imported ' + (r.instances || 0) + ' nodes + ' + (r.cables || 0) + ' cables');
  });
}

async function boot(vctx, st) {
  const id = sceneId();
  const [tpl, ct, nd, ft] = await Promise.all([
    api('GET', PREFIX + '/templates').catch(() => ({})),
    api('GET', PREFIX + '/cable-types').catch(() => ({})),
    api('GET', PREFIX + '/electrical-nodes').catch(() => ({})),
    api('GET', PREFIX + '/features').catch(() => ({})),
  ]);
  st.templates = (tpl && tpl.templates) || {};
  st.cableTypes = (ct && ct.cable_types) || {};
  st.nodes = (nd && nd.studies) || [];
  st.features = (ft && ft.features) || {};
  if (id) {
    try { const sc = await api('GET', PREFIX + '/scenes/' + encodeURIComponent(id)); st.scene = (sc && sc.scene) || null; } catch (e) { st.scene = null; }
  }
  const nb = vctx.pane.querySelector('[data-se-newscene]'); if (nb) nb.addEventListener('click', () => newScene(vctx));
  const ib = vctx.pane.querySelector('[data-se-import]'); if (ib) ib.addEventListener('click', () => importNetwork(vctx, st));
  const ab = vctx.pane.querySelector('[data-se-anchor]'); if (ab) ab.addEventListener('click', () => anchorScene(vctx, st));
  const ob = vctx.pane.querySelector('[data-se-overlay]'); if (ob) ob.addEventListener('click', () => toggleOverlay(vctx, st));
  renderAll(vctx, st);
  // "+ New Site Layout" from the Documents dashboard lands here with ?new=1 —
  // open the New scene modal immediately (reuses the same create flow).
  if (!id && new URLSearchParams(location.search).get('new') === '1') newScene(vctx);
}

const _v = defineView({
  id: 'scene-editor', styles: STYLES, markup: MARKUP,
  events: ['select'],
  mount(vctx) {
    vctx._seState = { templates: {}, cableTypes: {}, nodes: [], features: {}, scene: null, selected: null, aiPanel: null };
    mountAiPanel(vctx, vctx._seState);
    boot(vctx, vctx._seState);
    // mirror viewport selection into the roster
    vctx._seUnsub = vctx.store.subscribe((e) => {
      if (vctx._seState) {
        vctx._seState.selected = e.oid;
        renderRoster(vctx, vctx._seState); renderInspector(vctx, vctx._seState);
        if (vctx._seState.aiPanel) vctx._seState.aiPanel.refresh();
      }
    }, ['select']);
  },
  update() { /* edits re-render locally; nothing store-driven beyond selection */ },
  teardown(vctx) {
    if (vctx._seUnsub) { try { vctx._seUnsub(); } catch (e) { /* */ } }
    if (vctx._seState && vctx._seState.aiPanel) vctx._seState.aiPanel.teardown();
  },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
