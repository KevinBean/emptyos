// eos-cad-views/cross-section.js — the YZ cross-section of the selected cable run at
// the shared chainage cursor (the headline corridor view). Equal-aspect so conduit
// rings stay round. Draws the trench envelope, each cable in its cross-section
// formation, and its conduit (OD/ID ring), re-slicing instantly as the cursor
// scrubs (pure client-side, from object props). A range slider in the header drives
// the shared chainage. The "drawing the trench cross-section" the tool couldn't do.

import { defineView } from '/static/eos-cad-view.js';
import { createSvgViewport } from '/static/eos-cad-svg-view.js';
import {
  svg, runSpans, sectionAtChainage, interpDepth, arrangementOffsets, selectedRun,
  bFieldUt, fieldCablesForSection, superposedRiseC, thermalCablesForSection,
} from '/static/eos-cad-corridor.js';

// Per-run rated-load thermal inputs, fetched once per oid (the run's compute_<kind>
// hook returns `thermal` only when the run carries a spec + the dark thermal-field
// flag is on). `undefined` = not fetched, `null` = fetched but no thermal data.
const _thermalCache = {};
async function loadThermal(run, onReady) {
  if (run.oid in _thermalCache) return;
  _thermalCache[run.oid] = null;                         // mark in-flight (no re-fetch)
  try {
    const r = await fetch('/cable-network/api/cad/compute/cable-run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ oid: run.oid, props: run.props }),
    }).then((r) => r.json());
    _thermalCache[run.oid] = (r && r.ok && r.compute && r.compute.thermal) || null;
  } catch (e) { _thermalCache[run.oid] = null; }
  if (onReady) onReady();
}

function heatColor(t) {
  t = Math.max(0, Math.min(1, t));
  const stops = [[37, 99, 235], [34, 211, 238], [74, 222, 128], [250, 204, 21], [232, 99, 95]];
  const x = t * (stops.length - 1), i = Math.floor(x), f = x - i;
  const a = stops[i], b = stops[Math.min(stops.length - 1, i + 1)];
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`;
}

const STYLES = `
  .cadv-xs { position: absolute; inset: 0; display: flex; flex-direction: column; background: var(--bg); }
  .cadv-xs .hd { flex: none; display: flex; align-items: center; gap: 10px; padding: 6px 10px;
    border-bottom: 1px solid var(--border); font-size: 11px; }
  .cadv-xs .hd .ttl { text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
  .cadv-xs .hd .mbr { font-family: var(--mono, monospace); }
  .cadv-xs .hd .mbr.ok { color: #4caf72; } .cadv-xs .hd .mbr.bad { color: var(--danger, #e8635f); }
  .cadv-xs .hd .fld { font-family: var(--mono, monospace); }
  .cadv-xs .hd .fld.ok { color: #4caf72; } .cadv-xs .hd .fld.bad { color: var(--danger, #e8635f); }
  .cadv-xs .hd input[type=range] { flex: 1; min-width: 60px; accent-color: var(--accent); }
  .cadv-xs .hd .ch { font-family: var(--mono, monospace); color: var(--muted); min-width: 96px; text-align: right; }
  .cadv-xs .body { flex: 1; min-height: 0; position: relative; }
  .cadv-xs svg { width: 100%; height: 100%; display: block; }
  .cadv-xs .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    color: var(--muted); font-size: 13px; }
`;

function carryingTrench(store, runOid) {
  return store.objectsOfKind('trench').find(t => (t.props.carries || []).includes(runOid)) || null;
}

function renderSvg(vctx) {
  const host = vctx.pane.querySelector('[data-xs]');
  if (!host) return;
  const store = vctx.store;
  const run = selectedRun(store);
  if (!run) { host.innerHTML = '<div class="empty">No cable run.</div>'; return; }
  const W = host.clientWidth || 400, H = host.clientHeight || 300, pad = 24;
  const at = sectionAtChainage(run, Math.min(runSpans(run).total, Math.max(0, store.chainage)));
  const sec = at ? at.sec : (run.props.sections || [])[0];
  if (!sec) { host.innerHTML = '<div class="empty">No section here.</div>'; return; }
  const depth = interpDepth(at ? at.local : 0, sec.profile != null ? sec.profile : 0);
  const offs = arrangementOffsets(sec.arrangement, sec);
  const coreR = +(run.props.core_r || 0.06);
  const cond = sec.conduit && +sec.conduit.od_m > 0 ? sec.conduit : null;
  const trench = carryingTrench(store, run.oid);

  // Equal-aspect fit: lateral span (cables + conduit/trench) × depth span.
  let maxU = 0.1, maxR = coreR;
  offs.forEach(([, u]) => { maxU = Math.max(maxU, Math.abs(u)); });
  if (cond) maxR = Math.max(maxR, cond.od_m / 2);
  let latHalf = maxU + maxR + 0.3;
  if (trench) latHalf = Math.max(latHalf, (trench.props.width_m || 1.2) / 2 + 0.2);
  const depthSpan = Math.max(depth + maxR + 0.3, trench ? (trench.props.depth_m || 0.9) + 0.2 : 0);
  const scale = Math.min((W - 2 * pad) / (2 * latHalf), (H - 2 * pad) / depthSpan);
  const cx0 = W / 2, top = pad;
  const X = u => cx0 + u * scale;
  const Y = d => top + d * scale;

  const root = svg('svg', { viewBox: `0 0 ${W} ${H}` });
  // ground + depth grid
  root.appendChild(svg('line', { x1: pad, y1: Y(0), x2: W - pad, y2: Y(0), stroke: 'var(--border)' }));
  root.appendChild(svg('text', { x: pad, y: Y(0) - 4, fill: 'var(--muted)', 'font-size': 10 }, [t('ground')]));
  for (let d = 1; d <= Math.floor(depthSpan); d++)
    root.appendChild(svg('line', { x1: pad, y1: Y(d), x2: W - pad, y2: Y(d), stroke: 'var(--border)', 'stroke-opacity': 0.3 }));

  // Thermal temperature field (precedence) — when the run carries a cable spec and
  // the dark thermal-field flag is on, the compute hook returns rated-load losses;
  // we render the soil ΔT field by Kennelly image-method superposition (ambient →
  // conductor temp). Otherwise fall back to the EMF magnetic-field overlay.
  const th = run.props && run.props.spec ? _thermalCache[run.oid] : null;
  if (run.props && run.props.spec && !(run.oid in _thermalCache)) {
    loadThermal(run, () => renderSvg(vctx));            // kick off; re-render on arrival
  }
  const currentA = +(sec.current_a || run.props.current_a || 0);
  if (th && th.losses_w_per_m > 0) {
    const rho = +th.soil_rho_kmw || 1.0, amb = +th.ambient_c || 25;
    const condC = +th.conductor_temp_c || (amb + 65);
    const cables = thermalCablesForSection(offs, depth, th.losses_w_per_m);
    const NX = 30, NZ = 22, uMin = -latHalf, uMax = latHalf, zMax = depthSpan;
    const tSpan = Math.max(1, condC - amb);            // scale ambient(0) → conductor(1)
    const cw = (uMax - uMin) / NX * scale + 0.6, ch = zMax / NZ * scale + 0.6;
    for (let iz = 0; iz < NZ; iz++) for (let ix = 0; ix < NX; ix++) {
      const u = uMin + (ix + 0.5) / NX * (uMax - uMin), z = (iz + 0.5) / NZ * zMax;
      const tt = Math.min(1, superposedRiseC(cables, u, z, rho) / tSpan);
      if (tt < 0.03) continue;
      root.appendChild(svg('rect', { x: X(uMin + ix / NX * (uMax - uMin)), y: Y(iz / NZ * zMax),
        width: cw, height: ch, fill: heatColor(tt), 'fill-opacity': Math.min(0.6, 0.12 + tt * 0.5) }));
    }
    const fld = vctx.pane.querySelector('[data-xs-field]');
    if (fld) { fld.textContent = 'θ ' + Math.round(condC) + '°C core @ rated'; fld.className = 'fld ok'; }
  } else if (currentA > 0) {
    const limit = +(run.props.emf_limit_ut || 100);
    const cables = fieldCablesForSection(offs, depth, currentA);
    const NX = 30, NZ = 22, uMin = -latHalf, uMax = latHalf, zMax = depthSpan;
    const scaleMax = limit * 2;                        // fixed physical scale (red ≈ ≥ limit)
    const cw = (uMax - uMin) / NX * scale + 0.6, ch = zMax / NZ * scale + 0.6;
    for (let iz = 0; iz < NZ; iz++) for (let ix = 0; ix < NX; ix++) {
      const tt = Math.min(1, bFieldUt(cables, uMin + (ix + 0.5) / NX * (uMax - uMin), (iz + 0.5) / NZ * zMax) / scaleMax);
      if (tt < 0.03) continue;
      root.appendChild(svg('rect', { x: X(uMin + ix / NX * (uMax - uMin)), y: Y(iz / NZ * zMax),
        width: cw, height: ch, fill: heatColor(tt), 'fill-opacity': Math.min(0.6, 0.12 + tt * 0.5) }));
    }
    // The number that matters: max |B| along the ground surface (accessible area) —
    // NOT the singular field at the conductor itself.
    let groundMax = 0;
    for (let ix = 0; ix <= 80; ix++)
      groundMax = Math.max(groundMax, bFieldUt(cables, uMin + ix / 80 * (uMax - uMin), 0.0));
    const fld = vctx.pane.querySelector('[data-xs-field]');
    if (fld) { fld.textContent = 'B ' + groundMax.toFixed(1) + ' µT @ grade'; fld.className = 'fld ' + (groundMax >= limit ? 'bad' : 'ok'); }
  } else {
    const fld = vctx.pane.querySelector('[data-xs-field]'); if (fld) fld.textContent = '';
  }

  // trench envelope
  if (trench) {
    const tw = (trench.props.width_m || 1.2), td = (trench.props.depth_m || 0.9);
    root.appendChild(svg('rect', { x: X(-tw / 2), y: Y(0), width: tw * scale, height: td * scale,
      fill: '#6b5b3a', 'fill-opacity': 0.16, stroke: '#6b5b3a', 'stroke-dasharray': '4 3' }));
  }
  // cables (+ conduits)
  offs.forEach(([label, u, w]) => {
    const px = X(u), py = Y(depth - (w || 0));
    if (cond) {
      root.appendChild(svg('circle', { cx: px, cy: py, r: cond.od_m / 2 * scale, fill: '#3a3f47', stroke: '#8b98a8' }));
      root.appendChild(svg('circle', { cx: px, cy: py, r: cond.id_m / 2 * scale, fill: 'var(--bg)', stroke: '#5a5f67' }));
    }
    root.appendChild(svg('circle', { cx: px, cy: py, r: Math.max(2, coreR * scale), fill: run.props.color || '#d62728', stroke: '#0d1117' }));
    if (scale > 30) root.appendChild(svg('text', { x: px, y: py - Math.max(2, coreR * scale) - 2, fill: 'var(--muted)', 'font-size': 8, 'text-anchor': 'middle' }, [t(label)]));
  });
  if (vctx._svgViewport) vctx._svgViewport.mount(root, `0 0 ${W} ${H}`);
  else { host.innerHTML = ''; host.appendChild(root); }
}

function t(s) { return document.createTextNode(s); }

function refreshHeader(vctx) {
  const store = vctx.store, run = selectedRun(store);
  const slider = vctx.pane.querySelector('[data-xs-slider]');
  const chOut = vctx.pane.querySelector('[data-xs-ch]');
  const mbr = vctx.pane.querySelector('[data-xs-mbr]');
  if (!run) { if (mbr) mbr.textContent = ''; if (chOut) chOut.textContent = ''; return; }
  const total = runSpans(run).total;
  const cch = Math.min(total, Math.max(0, store.chainage));
  if (slider) { slider.max = total.toFixed(2); slider.step = (total / 200 || 0.1).toFixed(3); slider.value = cch; }
  if (chOut) chOut.textContent = `${run.oid} · ${cch.toFixed(1)}/${total.toFixed(0)} m`;
  if (mbr) {
    const rep = run._raw && run._raw._report;
    if (rep) { mbr.textContent = rep.feasible ? '✓ MBR ' + (run.props.mbr_m || 0) + ' m' : '✗ MBR'; mbr.className = 'mbr ' + (rep.feasible ? 'ok' : 'bad'); }
    else { mbr.textContent = 'MBR ' + (run.props.mbr_m || 0) + ' m'; mbr.className = 'mbr'; }
  }
}

function render(vctx) { refreshHeader(vctx); renderSvg(vctx); }

const _v = defineView({
  id: 'cross-section', styles: STYLES,
  markup: `<div class="cadv-xs">
    <div class="hd"><span class="ttl">Cross-section</span><span class="mbr" data-xs-mbr></span><span class="fld" data-xs-field></span>
      <input type="range" min="0" max="100" value="0" data-xs-slider aria-label="chainage">
      <span class="ch" data-xs-ch></span><button type="button" class="eos-tool-btn" data-xs-fit title="Fit drawing">Fit</button></div>
    <div class="body"><div data-xs style="position:absolute;inset:0;"></div></div></div>`,
  events: ['doc', 'object', 'select', 'chainage'],
  mount(vctx) {
    const slider = vctx.pane.querySelector('[data-xs-slider]');
    if (slider) slider.addEventListener('input', () => vctx.store.setChainage(+slider.value));
    const drawing = vctx.pane.querySelector('[data-xs]');
    if (drawing) vctx._svgViewport = createSvgViewport(drawing);
    const fit = vctx.pane.querySelector('[data-xs-fit]');
    if (fit) fit.addEventListener('click', () => vctx._svgViewport && vctx._svgViewport.fit());
    render(vctx);
    vctx._onResize = () => renderSvg(vctx);
    window.addEventListener('resize', vctx._onResize);
  },
  update(vctx, evt) {
    // Structural edits (incl. a spec change) invalidate the cached rated-load
    // thermal inputs; chainage scrubs + selection re-read the cache as-is.
    if (evt.type === 'doc' || evt.type === 'object') {
      for (const k in _thermalCache) delete _thermalCache[k];
    }
    render(vctx);
  },
  teardown(vctx) {
    if (vctx._onResize) window.removeEventListener('resize', vctx._onResize);
    if (vctx._svgViewport) vctx._svgViewport.destroy();
  },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
