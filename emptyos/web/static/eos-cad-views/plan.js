// eos-cad-views/plan.js — top-down (XY) plan of a cable corridor. Renders trench
// envelopes, cable-run centrelines, existing structures, tree-protection circles,
// and joints from the shared doc's corridor objects; draws the chainage cursor on
// the selected run; clicking a run/trench selects it. Pure draw-from-store.

import { defineView } from '/static/eos-cad-view.js';
import {
  svg, makeProjector, runFullRoute, xyAtChainage, selectedRun,
  corridorRoutes, treeEncroachment,
} from '/static/eos-cad-corridor.js';

// AS 2067:2016 keep-out radii (m) per nominal voltage, fetched once per kV from the
// owning app's engine (server-side, conformance-gated). Empty when the dark flag
// feature.cad-as2067-clearance.enabled is off (endpoint returns no data), so the
// overlay is naturally dark. `_as2067Pending` guards against re-fetching in flight.
const _as2067ByKv = {};
const _as2067Pending = new Set();
async function loadAs2067(voltages, onReady) {
  const want = voltages.filter((v) => !(v in _as2067ByKv) && !_as2067Pending.has(v));
  if (!want.length) return;
  want.forEach((v) => _as2067Pending.add(v));
  try {
    const r = await fetch('/cable-network/api/cad/as2067-clearance', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voltages_kv: want }),
    }).then((r) => r.json());
    const byKv = (r && r.ok && r.by_kv) || {};
    want.forEach((v) => { _as2067ByKv[v] = byKv[String(v)] || null; });
  } catch (e) {
    want.forEach((v) => { _as2067ByKv[v] = null; });
  } finally {
    want.forEach((v) => _as2067Pending.delete(v));
  }
  if (onReady) onReady();
}

const STYLES = `
  .cadv-plan { position: absolute; inset: 0; overflow: hidden; background: var(--bg); }
  .cadv-plan .hd { position: absolute; top: 6px; left: 10px; font-size: 11px; text-transform: uppercase;
    letter-spacing: .08em; color: var(--muted); pointer-events: none; }
  .cadv-plan svg { width: 100%; height: 100%; display: block; }
  .cadv-plan .pickable { cursor: pointer; }
`;

function bounds(objs) {
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  const acc = (x, y) => { minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y); };
  for (const o of objs) {
    const p = o.props || {};
    if (o.kind === 'cable-run') (p.sections || []).forEach(s => (s.route || []).forEach(pt => acc(pt[0], pt[1])));
    else if (o.kind === 'trench') (p.route || []).forEach(pt => acc(pt[0], pt[1]));
    else if (p.at) {
      const sz = p.size || [1, 1, 1];
      const cl = +p.voltage_kv > 0 && _as2067ByKv[+p.voltage_kv];
      const r = Math.max(+p.nrz_m || 0, (cl && cl.section_safety_m) || 0);
      acc(p.at[0] - sz[0] / 2 - r, p.at[1] - sz[1] / 2 - r);
      acc(p.at[0] + sz[0] / 2 + r, p.at[1] + sz[1] / 2 + r);
    }
  }
  if (!isFinite(minx)) { minx = -1; maxx = 1; miny = -1; maxy = 1; }
  const mx = (maxx - minx) * 0.08 + 1, my = (maxy - miny) * 0.08 + 1;
  return { minx: minx - mx, maxx: maxx + mx, miny: miny - my, maxy: maxy + my };
}

function render(vctx) {
  const host = vctx.pane.querySelector('[data-plan]');
  if (!host) return;
  const store = vctx.store;
  const objs = store.iterObjects();
  const routes = corridorRoutes(store);   // for tree-zone encroachment (AS 4970)
  let encroached = 0;
  // AS 2067 keep-out overlay: fetch radii for any energized structures' voltages.
  const energized = objs.filter((o) => o.kind === 'structure' && +(o.props || {}).voltage_kv > 0);
  if (energized.length) {
    const volts = [...new Set(energized.map((o) => +o.props.voltage_kv))];
    loadAs2067(volts, () => render(vctx));
  }
  const W = host.clientWidth || 600, H = host.clientHeight || 400;
  const proj = makeProjector(bounds(objs), W, H, 16, { flipY: true });
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet' });
  const line = (route, attrs, oid) => {
    const pts = (route || []).map(p => proj(p[0], p[1]).join(',')).join(' ');
    const el = svg('polyline', { points: pts, fill: 'none', ...attrs });
    if (oid) { el.classList.add('pickable'); el.dataset.oid = oid; el.addEventListener('click', () => store.select(oid)); }
    return el;
  };
  const sel = store.selection;

  // trenches (translucent band = route stroked at trench width)
  for (const o of objs.filter(o => o.kind === 'trench')) {
    const wpx = Math.max(2, (o.props.width_m || 1.2) * proj.scale);
    root.appendChild(line(o.props.route, {
      stroke: o.props.color || '#6b5b3a', 'stroke-width': wpx, 'stroke-opacity': o.oid === sel ? 0.55 : 0.35,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, o.oid));
  }
  // structures (boxes) + tree-zones (circles) + joints
  for (const o of objs) {
    const p = o.props || {};
    if (o.kind === 'structure' && p.at) {
      const sz = p.size || [1, 1, 1];
      const [x0, y0] = proj(p.at[0] - sz[0] / 2, p.at[1] + sz[1] / 2);   // top-left after flip
      root.appendChild(svg('rect', { x: x0, y: y0, width: sz[0] * proj.scale, height: sz[1] * proj.scale,
        fill: '#777c82', 'fill-opacity': 0.5, stroke: o.oid === sel ? 'var(--accent)' : '#9aa7b4',
        class: 'pickable' })).addEventListener('click', () => store.select(o.oid));
      // AS 2067:2016 personnel keep-out (section-safety clearance) around an
      // energized structure — dashed amber ring + label, like the tree NRZ zones.
      const kv = +p.voltage_kv;
      const cl = kv > 0 ? _as2067ByKv[kv] : null;
      if (cl && cl.section_safety_m > 0) {
        const c = proj(p.at[0], p.at[1]);
        root.appendChild(svg('circle', { cx: c[0], cy: c[1], r: cl.section_safety_m * proj.scale,
          fill: '#e0a030', 'fill-opacity': 0.08, stroke: '#e0a030', 'stroke-dasharray': '5 3' }));
        root.appendChild(svg('text', { x: c[0], y: c[1] - cl.section_safety_m * proj.scale - 3,
          'font-size': 9, fill: '#c98a18', 'text-anchor': 'middle' },
          [document.createTextNode(kv + ' kV · AS 2067 ' + cl.section_safety_m.toFixed(1) + ' m')]));
      }
    } else if (o.kind === 'tree-zone' && p.at) {
      const c = proj(p.at[0], p.at[1]);
      const enc = treeEncroachment(p.at, +p.nrz_m || 0, +p.srz_m || 0, routes);
      if (enc.nrz) encroached++;
      // amber when a route breaches the NRZ, red when it breaches the worse SRZ.
      const col = enc.srz ? '#e8635f' : enc.nrz ? '#e0a030' : '#3f8f4f';
      if (+p.nrz_m) root.appendChild(svg('circle', { cx: c[0], cy: c[1], r: p.nrz_m * proj.scale,
        fill: col, 'fill-opacity': enc.nrz ? 0.16 : 0.10, stroke: col,
        'stroke-dasharray': enc.nrz ? '5 3' : '' }));
      if (+p.srz_m) root.appendChild(svg('circle', { cx: c[0], cy: c[1], r: p.srz_m * proj.scale, fill: '#2f6f3f', 'fill-opacity': 0.25 }));
    } else if (o.kind === 'joint' && p.at) {
      const c = proj(p.at[0], p.at[1]);
      root.appendChild(svg('rect', { x: c[0] - 5, y: c[1] - 5, width: 10, height: 10, fill: '#c0c4cc', stroke: '#0d1117' }));
    }
  }
  // cable-run centrelines
  for (const o of objs.filter(o => o.kind === 'cable-run')) {
    (o.props.sections || []).forEach(s => root.appendChild(line(s.route, {
      stroke: o.props.color || '#d62728', 'stroke-width': o.oid === sel ? 3 : 2,
      'stroke-dasharray': o.oid === sel ? '' : '' }, o.oid)));
  }
  // chainage cursor tick on the selected run
  const run = selectedRun(store);
  if (run) {
    const route = runFullRoute(run);
    const [cx, cy] = xyAtChainage(route, store.chainage);
    const c = proj(cx, cy);
    root.appendChild(svg('circle', { cx: c[0], cy: c[1], r: 6, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2 }));
    root.appendChild(svg('line', { x1: c[0] - 10, y1: c[1], x2: c[0] + 10, y2: c[1], stroke: 'var(--accent)' }));
    root.appendChild(svg('line', { x1: c[0], y1: c[1] - 10, x2: c[0], y2: c[1] + 10, stroke: 'var(--accent)' }));
  }
  const hd = vctx.pane.querySelector('.hd');
  if (hd) hd.textContent = encroached ? ('Plan · ⚠ ' + encroached + ' tree NRZ encroached') : 'Plan';
  host.innerHTML = '';
  host.appendChild(root);
}

const _v = defineView({
  id: 'plan', styles: STYLES,
  markup: `<div class="cadv-plan"><span class="hd">Plan</span><div data-plan style="position:absolute;inset:0;"></div></div>`,
  events: ['doc', 'object', 'select', 'chainage'],
  mount(vctx) {
    render(vctx);
    vctx._onResize = () => render(vctx);
    window.addEventListener('resize', vctx._onResize);
  },
  update(vctx) { render(vctx); },
  teardown(vctx) { if (vctx._onResize) window.removeEventListener('resize', vctx._onResize); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
