// eos-cad-views/corridor-inspector.js — the corridor edit + readout pane. On
// selection of a cable-run (or the first run by default) it renders an editable
// form for the run's IEC 60287 ampacity `spec` (schema fetched from the owning
// app, GET <api_prefix>/cad/schema/cable-run, rendered via the shared
// eos-cad-schema-form.js — the same mechanism object-inspector.js uses), then
// calls compute_<kind> (POST <api_prefix>/cad/compute/cable-run) and shows the
// domain readout: MBR verdict, pulling tension + sidewall pressure, clearance
// vs structures, cable count + ampacity (or why ampacity failed once a spec is
// attached). Before this, the readout had no way to attach a spec at all — the
// ampacity row just said "— (set cable spec)" with no "somewhere" to do that.

import { defineView, esc } from '/static/eos-cad-view.js';
import { selectedRun, regenerateObject } from '/static/eos-cad-corridor.js';
import { getPath, setPath, fieldsMarkup } from '/static/eos-cad-schema-form.js';

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
  .cadv-ci details.ci-spec { margin-bottom: 10px; border: 1px solid var(--border); border-radius: var(--radius-sm, 6px); }
  .cadv-ci details.ci-spec summary { cursor: pointer; padding: 6px 8px; font-size: 11.5px; font-weight: 600;
    color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .cadv-ci .ci-spec fieldset { border: 0; border-top: 1px solid var(--border); padding: 8px; margin: 0; }
  .cadv-ci .ci-spec legend { padding: 0 6px 0 0; color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
  .cadv-ci .ci-spec label { display: grid; grid-template-columns: minmax(90px, 1fr) minmax(80px, 1fr) auto;
    gap: 6px; align-items: center; margin: 6px 0; font-size: 11px; }
  .cadv-ci .ci-spec input, .cadv-ci .ci-spec select { min-width: 0; width: 100%; box-sizing: border-box;
    border: 1px solid var(--border); border-radius: var(--radius-sm, 6px);
    background: var(--bg-input, var(--bg-card)); color: var(--text); padding: 5px 6px; }
  .cadv-ci .ci-spec .unit { color: var(--muted); font: 10px var(--mono, monospace); }
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
  if (c.ampacity != null) h += row('Ampacity (IEC 60287)', c.ampacity + ' A');
  else if (c.ampacity_error) h += row('Ampacity', c.ampacity_error, false);
  else h += row('Ampacity', '— (set cable spec below)');
  const warns = (c.pulling && c.pulling.warnings) || [];
  if (warns.length) h += '<div class="ci-warn">' + warns.map(esc).join('<br>') + '</div>';
  return h;
}

// ── Cable spec editor — the "somewhere" the ampacity readout above used to be
// missing. Rendering (getPath/setPath/fieldsMarkup) comes from the shared
// eos-cad-schema-form.js — object-inspector.js's identical pattern was the
// first consumer; this is the second, so it's shared rather than re-copied
// (CLAUDE.md rule 9). Only the schema fetch + commit wiring stay here, since
// they're cable-run-specific (fixed endpoint, own status messaging). ──
let _schemaPromise = null;
function schemaFields() {
  if (!_schemaPromise) {
    _schemaPromise = fetch('/cable-network/api/cad/schema/cable-run')
      .then((r) => r.json()).then((r) => (r && r.ok && r.fields) || []).catch(() => []);
  }
  return _schemaPromise;
}
async function renderSpecEditor(vctx, run) {
  const host = vctx.pane.querySelector('[data-ci-spec]');
  if (!host) return;
  const fields = await schemaFields();
  const cur = vctx.store.objectByOid(run.oid);
  if (!cur) return;
  host.innerHTML = fields.length ? fieldsMarkup(cur.props, fields)
    : '<div class="ci-empty">No editable schema declared.</div>';
  host.querySelectorAll('[data-key]').forEach((input) => {
    const commit = async () => {
      const current = vctx.store.objectByOid(run.oid);
      const field = fields.find((f) => f.key === input.dataset.key);
      if (!current || !field) return;
      let value;
      if (field.type === 'number') {
        value = input.value === '' ? null : Number(input.value);
        if (value !== null && !Number.isFinite(value)) { renderSpecEditor(vctx, run); return; }
      } else {
        value = input.value === '' ? null : input.value;
      }
      const props = JSON.parse(JSON.stringify(current.props || {}));
      setPath(props, field.key, value);
      vctx.store.pushUndo();
      vctx.store.patchObject(run.oid, { props });
      if (vctx.setStatus) vctx.setStatus('Regenerating ' + run.oid + '…');
      // regenerateObject's store.notifyDoc() + patchObject's own 'object' event
      // both fall inside this view's event mask below, so the outer render()
      // (readout + this form) repaints itself automatically — no manual re-render
      // call here, same convention as object-inspector.js's commit handler.
      await regenerateObject(vctx.store, run.oid);
      if (vctx.setStatus) vctx.setStatus('Updated ' + run.oid);
    };
    input.addEventListener('change', commit);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } });
  });
}

// The pane's whole innerHTML is rebuilt on every 'object'/'doc' store event —
// which fires after EVERY single field commit (see commit() above), so a
// <details open> attribute baked into a template string would close itself
// after each edit. A user filling in several spec fields in a row would have
// to re-open "Cable spec" after typing each one. Track the open state here
// instead so it survives the rebuild.
let _specOpen = false;

let _seq = 0;
function render(vctx) {
  const host = vctx.pane.querySelector('[data-ci]');
  if (!host) return;
  const run = selectedRun(vctx.store);
  if (!run) { host.innerHTML = '<div class="ci-empty">No cable run in this document.</div>'; return; }
  host.innerHTML = '<div class="ci-head">' + esc(run.oid) + '</div>' +
                   '<details class="ci-spec"' + (_specOpen ? ' open' : '') + '><summary>Cable spec</summary><div data-ci-spec></div></details>' +
                   '<div class="ci-sub">cable run · computing…</div><div data-ci-body></div>';
  const details = host.querySelector('details.ci-spec');
  if (details) details.addEventListener('toggle', () => { _specOpen = details.open; });
  renderSpecEditor(vctx, run);
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
