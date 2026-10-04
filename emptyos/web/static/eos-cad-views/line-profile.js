// eos-cad-views/line-profile.js — the overhead-line sag long-section (side
// elevation): ground line, structures, and each span's catenary with its sag label,
// over a vertical-exaggeration axis. Reads the COMPUTED scene from the selected
// overhead-line object's report (stashed by regenerate from the sag-tension engine)
// — it doesn't re-derive sag client-side. Ported from overhead-line/pages/
// line-profile.html. See .claude/rules/cad-layouts.md.

import { defineView } from '/static/eos-cad-view.js';
import { svg } from '/static/eos-cad-corridor.js';

const STYLES = `
  .cadv-lp { position: absolute; inset: 0; display: flex; flex-direction: column; background: var(--bg); }
  .cadv-lp .hd { flex: none; display: flex; align-items: center; gap: 12px; padding: 6px 12px;
    border-bottom: 1px solid var(--border); font-size: 11px; }
  .cadv-lp .hd .ttl { text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
  .cadv-lp .hd .meta { font-family: var(--mono, monospace); color: var(--text); }
  .cadv-lp .hd .vex { margin-left: auto; color: var(--muted); }
  .cadv-lp .hd .vex input { width: 48px; background: var(--panel); color: var(--text);
    border: 1px solid var(--border); border-radius: 5px; padding: 2px 5px; }
  .cadv-lp .body { flex: 1; min-height: 0; position: relative; }
  .cadv-lp svg { width: 100%; height: 100%; display: block; }
  .cadv-lp .lp-empty { position: absolute; inset: 0; display: flex; align-items: center;
    justify-content: center; color: var(--muted); font-size: 13px; }
`;

function tnode(s) { return document.createTextNode(s); }

function selectedLine(store) {
  const sel = store.objectByOid(store.selection);
  if (sel && sel.kind === 'overhead-line') return sel;
  const lines = store.objectsOfKind('overhead-line');
  return lines.length ? lines[0] : null;
}

function drawProfile(s, vex, W, H) {
  const padL = 44, padR = 16, padT = 14, padB = 24;
  const L = s.route_length_m || 1, maxY = s.attachment_height_m || 20;
  const sx = (W - padL - padR) / L, sy = (H - padT - padB) / (maxY * vex || 1);
  const X = (x) => padL + x * sx;
  const Y = (y) => H - padB - y * vex * sy;        // ground (y=0) at the bottom
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet' });
  root.appendChild(svg('line', { x1: X(0), y1: Y(0), x2: X(L), y2: Y(0), stroke: '#3a4654', 'stroke-width': 1.5 }));
  for (let h = 0; h <= maxY; h += 5) {
    const t = svg('text', { x: 6, y: Y(h) + 3, fill: 'var(--muted)', 'font-size': 9 }, [tnode(h + 'm')]);
    root.appendChild(t);
    root.appendChild(svg('line', { x1: padL - 4, y1: Y(h), x2: padL, y2: Y(h), stroke: '#3a4654' }));
  }
  (s.structures || []).forEach((st) => {
    root.appendChild(svg('line', { x1: X(st.x), y1: Y(st.base_y), x2: X(st.x), y2: Y(st.attach_y),
      stroke: '#9aa7b4', 'stroke-width': 3 }));
    root.appendChild(svg('text', { x: X(st.x), y: H - 6, 'text-anchor': 'middle', fill: 'var(--muted)', 'font-size': 9 }, [tnode(st.x + ' m')]));
  });
  (s.spans || []).forEach((sp) => {
    const pts = (sp.points || []).map((p) => X(p[0]) + ',' + Y(p[1])).join(' ');
    root.appendChild(svg('polyline', { points: pts, fill: 'none', stroke: '#5fd0ff', 'stroke-width': 1.6 }));
    const mid = (sp.points || [])[Math.floor((sp.points || []).length / 2)];
    if (mid) root.appendChild(svg('text', { x: X(mid[0]), y: Y(mid[1]) + 13, 'text-anchor': 'middle',
      fill: '#5fd0ff', 'font-size': 10 }, [tnode((sp.sag_m || 0).toFixed(2) + ' m')]));
  });
  return root;
}

function render(vctx) {
  const host = vctx.pane.querySelector('[data-lp]');
  if (!host) return;
  const meta = vctx.pane.querySelector('[data-lp-meta]');
  const line = selectedLine(vctx.store);
  if (!line) { host.innerHTML = '<div class="lp-empty">No overhead line.</div>'; if (meta) meta.textContent = ''; return; }
  const rep = line._raw && line._raw._report;
  const s = rep && rep.line3d;
  if (!s || s.error) { host.innerHTML = '<div class="lp-empty">computing sag…</div>'; if (meta) meta.textContent = ''; return; }
  const W = host.clientWidth || 1000, H = host.clientHeight || 300;
  const vexEl = vctx.pane.querySelector('[data-lp-vex]');
  const vex = vexEl ? Math.max(1, Math.min(20, parseInt(vexEl.value, 10) || 4)) : 4;
  host.innerHTML = '';
  host.appendChild(drawProfile(s, vex, W, H));
  if (meta) meta.textContent =
    rep.conductor + ' · route ' + rep.route_length_m + ' m · ruling span ' + rep.ruling_span_m +
    ' m · max sag ' + rep.max_sag_m + ' m · hot tension ' + rep.hot_tension_kN + ' kN';
}

const _v = defineView({
  id: 'line-profile', styles: STYLES,
  markup: `<div class="cadv-lp"><div class="hd"><span class="ttl">Profile (side elevation)</span>
    <span class="meta" data-lp-meta></span>
    <label class="vex">vex <input type="number" min="1" max="20" value="4" data-lp-vex></label></div>
    <div class="body"><div data-lp style="position:absolute;inset:0;"></div></div></div>`,
  events: ['doc', 'object', 'select'],
  mount(vctx) {
    const vexEl = vctx.pane.querySelector('[data-lp-vex]');
    if (vexEl) vexEl.addEventListener('change', () => render(vctx));
    render(vctx);
    vctx._onResize = () => render(vctx);
    window.addEventListener('resize', vctx._onResize);
  },
  update(vctx) { render(vctx); },
  teardown(vctx) { if (vctx._onResize) window.removeEventListener('resize', vctx._onResize); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
