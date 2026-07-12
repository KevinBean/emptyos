// eos-cad-views/earthing-plan.js — the buried earthing-grid plan (top-down):
// conductor mesh + earth rods, with a grid/soil readout. Reads the selected
// earth-grid object's props (segments + rods) + its report (lengths, rod count).
// The earthing exemplar's headline view. See .claude/rules/cad-layouts.md.

import { defineView } from '/static/eos-cad-view.js';
import { svg, makeProjector } from '/static/eos-cad-corridor.js';

const STYLES = `
  .cadv-eg { position: absolute; inset: 0; display: flex; flex-direction: column; background: var(--bg); }
  .cadv-eg .hd { flex: none; display: flex; align-items: center; gap: 10px; padding: 6px 12px;
    border-bottom: 1px solid var(--border); font-size: 11px; }
  .cadv-eg .hd .ttl { text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
  .cadv-eg .hd .meta { font-family: var(--mono, monospace); color: var(--text); }
  .cadv-eg .body { flex: 1; min-height: 0; position: relative; }
  .cadv-eg svg { width: 100%; height: 100%; display: block; }
  .cadv-eg .eg-empty { position: absolute; inset: 0; display: flex; align-items: center;
    justify-content: center; color: var(--muted); font-size: 13px; }
`;

function selectedGrid(store) {
  const sel = store.objectByOid(store.selection);
  if (sel && sel.kind === 'earth-grid') return sel;
  const g = store.objectsOfKind('earth-grid');
  return g.length ? g[0] : null;
}

function bounds(segs, rods) {
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  const acc = (x, y) => { minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y); };
  segs.forEach((s) => { acc(s.x1, s.y1); acc(s.x2, s.y2); });
  rods.forEach((r) => acc(r.x, r.y));
  if (!isFinite(minx)) { minx = -1; maxx = 1; miny = -1; maxy = 1; }
  const m = Math.max(maxx - minx, maxy - miny) * 0.06 + 1;
  return { minx: minx - m, maxx: maxx + m, miny: miny - m, maxy: maxy + m };
}

function render(vctx) {
  const host = vctx.pane.querySelector('[data-eg]');
  if (!host) return;
  const meta = vctx.pane.querySelector('[data-eg-meta]');
  const grid = selectedGrid(vctx.store);
  if (!grid) { host.innerHTML = '<div class="eg-empty">No earthing grid.</div>'; if (meta) meta.textContent = ''; return; }
  const segs = grid.props.segments || [], rods = grid.props.rods || [];
  const W = host.clientWidth || 500, H = host.clientHeight || 400;
  const proj = makeProjector(bounds(segs, rods), W, H, 18, { flipY: true });
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet' });
  segs.forEach((s) => {
    const a = proj(s.x1, s.y1), b = proj(s.x2, s.y2);
    root.appendChild(svg('line', { x1: a[0], y1: a[1], x2: b[0], y2: b[1],
      stroke: grid.props.color || '#c9a227', 'stroke-width': 1.6 }));
  });
  rods.forEach((r) => {
    const c = proj(r.x, r.y);
    root.appendChild(svg('circle', { cx: c[0], cy: c[1], r: 4, fill: '#9aa7b4', stroke: '#0d1117' }));
  });
  host.innerHTML = '';
  host.appendChild(root);
  if (meta) {
    const rep = grid._raw && grid._raw._report;
    const soil = grid.props.soil || {};
    let txt = rep
      ? rep.n_segments + ' conductors · ' + rep.total_length_m + ' m · ' + rep.n_rods + ' rods · depth ' + rep.burial_depth_m + ' m'
      : segs.length + ' conductors · ' + rods.length + ' rods';
    if (soil.layer1_rho_ohm_m) txt += ' · soil ' + soil.layer1_rho_ohm_m + '/' + soil.layer2_rho_ohm_m + ' Ω·m';
    meta.textContent = txt;
  }
}

const _v = defineView({
  id: 'earthing-plan', styles: STYLES,
  markup: `<div class="cadv-eg"><div class="hd"><span class="ttl">Earthing grid (plan)</span>
    <span class="meta" data-eg-meta></span></div>
    <div class="body"><div data-eg style="position:absolute;inset:0;"></div></div></div>`,
  events: ['doc', 'object', 'select'],
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
