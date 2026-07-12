// eos-cad-views/corridor-inspector.js — the corridor readout pane. On selection of
// a cable-run (or the first run by default) it calls the owning app's compute_<kind>
// hook (POST <api_prefix>/cad/compute/cable-run) and shows the domain readout: MBR
// verdict, pulling tension + sidewall pressure, clearance vs structures, cable count
// + ampacity. The integration seam made visible. Pure draw-from-store + one fetch.

import { defineView, esc } from '/static/eos-cad-view.js';
import { selectedRun } from '/static/eos-cad-corridor.js';

const STYLES = `
  .cadv-ci { height: 100%; overflow-y: auto; padding: 12px; box-sizing: border-box;
    border-right: 1px solid var(--border); background: var(--bg); }
  .cadv-ci .ci-head { font-size: 14px; font-weight: 600; }
  .cadv-ci .ci-sub { font-size: 11px; color: var(--muted); margin-bottom: 10px; }
  .cadv-ci .ci-row { display: flex; justify-content: space-between; gap: 8px; padding: 5px 0;
    border-bottom: 1px solid var(--border); font-size: 12.5px; }
  .cadv-ci .ci-row .k { color: var(--muted); }
  .cadv-ci .ci-row .v { font-family: var(--mono, monospace); }
  .cadv-ci .ci-row .v.ok { color: #4caf72; } .cadv-ci .ci-row .v.bad { color: var(--danger, #e8635f); }
  .cadv-ci .ci-warn { margin-top: 8px; font-size: 11.5px; color: var(--danger, #e8635f); line-height: 1.5; }
  .cadv-ci .ci-empty { color: var(--muted); font-size: 12px; padding: 6px 0; }
`;

// Resolve kind → owning-app api_prefix from the object-type registry (cached).
let _objTypes = null;
async function apiPrefixFor(kind) {
  if (!_objTypes) {
    _objTypes = {};
    try {
      const r = await fetch('/cad/api/object-types').then((r) => r.json());
      (r.object_types || []).forEach((o) => { _objTypes[o.kind] = o.api_prefix; });
    } catch (e) { /* fall back below */ }
  }
  return _objTypes[kind] || '/cable-network/api';
}

async function computeRun(store, run) {
  const prefix = await apiPrefixFor('cable-run');
  const structures = store.objectsOfKind('structure').map((s) => ({
    oid: s.oid, at: s.props.at, size: s.props.size, clearance: s.props.clearance,
  }));
  try {
    const r = await fetch(prefix + '/cad/compute/cable-run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ oid: run.oid, props: run.props, structures }),
    }).then((r) => r.json());
    return (r && r.ok) ? r.compute : null;
  } catch (e) { return null; }
}

function row(label, value, ok) {
  const cls = ok === true ? ' ok' : ok === false ? ' bad' : '';
  return '<div class="ci-row"><span class="k">' + esc(label) + '</span>' +
         '<span class="v' + cls + '">' + esc(value) + '</span></div>';
}

function renderReadout(c) {
  let h = '';
  if (c.mbr) h += row('MBR', (c.mbr.feasible ? '✓ ' : '✗ ') + (c.mbr.mbr_m || 0) + ' m', c.mbr.feasible);
  if (c.pulling) {
    h += row('Max tension', c.pulling.max_tension_kN + ' kN', c.pulling.tension_ok);
    h += row('Sidewall pressure', c.pulling.max_swp_N_m + ' N/m', c.pulling.swp_ok);
    h += row('Pull length', c.pulling.total_length_m + ' m');
  }
  if (c.clearance) {
    const n = (c.clearance.breaches || []).length;
    h += row('Clearance', n ? (n + ' breach' + (n > 1 ? 'es' : '')) : 'OK', c.clearance.ok);
  }
  h += row('Cables', String(c.n_cables || '—'));
  h += c.ampacity != null
    ? row('Ampacity (IEC 60287)', c.ampacity + ' A')
    : row('Ampacity', '— (set cable spec)');
  const warns = (c.pulling && c.pulling.warnings) || [];
  if (warns.length) h += '<div class="ci-warn">' + warns.map(esc).join('<br>') + '</div>';
  return h;
}

let _seq = 0;
function render(vctx) {
  const host = vctx.pane.querySelector('[data-ci]');
  if (!host) return;
  const run = selectedRun(vctx.store);
  if (!run) { host.innerHTML = '<div class="ci-empty">No cable run in this document.</div>'; return; }
  host.innerHTML = '<div class="ci-head">' + esc(run.oid) + '</div>' +
                   '<div class="ci-sub">cable run · computing…</div><div data-ci-body></div>';
  const my = ++_seq;
  computeRun(vctx.store, run).then((c) => {
    if (my !== _seq) return;                          // a newer selection superseded this
    const sub = host.querySelector('.ci-sub'); if (sub) sub.textContent = 'cable run';
    const body = host.querySelector('[data-ci-body]'); if (!body) return;
    body.innerHTML = c ? renderReadout(c) : '<div class="ci-empty">compute unavailable</div>';
  });
}

const _v = defineView({
  id: 'corridor-inspector', styles: STYLES,
  markup: `<div class="cadv-ci"><div data-ci></div></div>`,
  events: ['doc', 'object', 'select'],
  mount(vctx) { render(vctx); },
  update(vctx) { render(vctx); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
