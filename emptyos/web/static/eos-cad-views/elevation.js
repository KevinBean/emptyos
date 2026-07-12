// eos-cad-views/elevation.js — long-section (chainage × depth) of the selected
// cable run. Depth is auto-fit to the pane height (so the shallow burial depths are
// visibly exaggerated against the long chainage axis — the line-profile.html idiom).
// Draws the ground line, the depth profile (the HDD dip), and the shared chainage
// cursor; clicking sets the cursor. Pure draw-from-store.

import { defineView } from '/static/eos-cad-view.js';
import { svg, runSpans, sectionAtChainage, interpDepth, selectedRun } from '/static/eos-cad-corridor.js';

const STYLES = `
  .cadv-elev { position: absolute; inset: 0; overflow: hidden; background: var(--bg); }
  .cadv-elev .hd { position: absolute; top: 6px; left: 10px; font-size: 11px; text-transform: uppercase;
    letter-spacing: .08em; color: var(--muted); pointer-events: none; }
  .cadv-elev .rd { position: absolute; top: 6px; right: 10px; font-size: 11px; color: var(--muted);
    font-family: var(--mono, monospace); pointer-events: none; }
  .cadv-elev svg { width: 100%; height: 100%; display: block; cursor: crosshair; }
  .cadv-elev .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    color: var(--muted); font-size: 13px; }
`;

function depthAt(run, ch) {
  const at = sectionAtChainage(run, ch);
  return at ? interpDepth(at.local, at.sec.profile != null ? at.sec.profile : 0) : 0;
}

function render(vctx) {
  const host = vctx.pane.querySelector('[data-elev]');
  if (!host) return;
  const store = vctx.store;
  const run = selectedRun(store);
  const W = host.clientWidth || 600, H = host.clientHeight || 300, pad = 26;
  if (!run) { host.innerHTML = '<div class="empty">No cable run.</div>'; return; }
  const { total } = runSpans(run);
  const N = 160;
  const depths = [];
  let maxD = 0.1;
  for (let i = 0; i <= N; i++) { const ch = total * i / N; const d = depthAt(run, ch); depths.push([ch, d]); maxD = Math.max(maxD, d); }
  maxD *= 1.15;
  const x = ch => pad + (total > 0 ? (ch / total) : 0) * (W - 2 * pad);
  const y = d => pad + (d / maxD) * (H - 2 * pad);    // depth increases downward

  const root = svg('svg', { viewBox: `0 0 ${W} ${H}` });
  // soil fill between ground (y(0)) and the profile
  const top = depths.map(([ch]) => `${x(ch)},${y(0)}`);
  const prof = depths.map(([ch, d]) => `${x(ch)},${y(d)}`);
  root.appendChild(svg('polygon', { points: top.concat(prof.slice().reverse()).join(' '),
    fill: '#6b5b3a', 'fill-opacity': 0.18 }));
  // ground line
  root.appendChild(svg('line', { x1: x(0), y1: y(0), x2: x(total), y2: y(0), stroke: 'var(--border)' }));
  root.appendChild(svg('text', { x: pad, y: y(0) - 4, fill: 'var(--muted)', 'font-size': 10 }, [textNode('ground')]));
  // depth grid (every ~1 m up to maxD)
  for (let d = 1; d < maxD; d++) {
    root.appendChild(svg('line', { x1: pad, y1: y(d), x2: W - pad, y2: y(d), stroke: 'var(--border)', 'stroke-opacity': 0.3 }));
    root.appendChild(svg('text', { x: 2, y: y(d) + 3, fill: 'var(--muted)', 'font-size': 9 }, [textNode('-' + d + 'm')]));
  }
  // the cable depth profile
  root.appendChild(svg('polyline', { points: prof.join(' '), fill: 'none',
    stroke: run.props.color || '#d62728', 'stroke-width': 2.5 }));
  // chainage cursor
  const cch = Math.min(total, Math.max(0, store.chainage));
  root.appendChild(svg('line', { x1: x(cch), y1: pad - 6, x2: x(cch), y2: H - pad + 6, stroke: 'var(--accent)', 'stroke-width': 1.5 }));
  const cd = depthAt(run, cch);
  root.appendChild(svg('circle', { cx: x(cch), cy: y(cd), r: 4, fill: 'var(--accent)' }));

  host.innerHTML = '';
  host.appendChild(root);
  const rd = vctx.pane.querySelector('[data-elev-rd]');
  if (rd) rd.textContent = `${run.oid} · ch ${cch.toFixed(1)} m · depth ${cd.toFixed(2)} m`;
  // click → set chainage
  root.onclick = (ev) => {
    const r = root.getBoundingClientRect();
    const px = (ev.clientX - r.left) * (W / r.width);
    const ch = Math.min(total, Math.max(0, (px - pad) / (W - 2 * pad) * total));
    store.setChainage(ch);
  };
}

function textNode(s) { return document.createTextNode(s); }

const _v = defineView({
  id: 'elevation', styles: STYLES,
  markup: `<div class="cadv-elev"><span class="hd">Long-section</span><span class="rd" data-elev-rd></span><div data-elev style="position:absolute;inset:0;"></div></div>`,
  events: ['doc', 'object', 'select', 'chainage'],
  mount(vctx) { render(vctx); vctx._onResize = () => render(vctx); window.addEventListener('resize', vctx._onResize); },
  update(vctx) { render(vctx); },
  teardown(vctx) { if (vctx._onResize) window.removeEventListener('resize', vctx._onResize); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
