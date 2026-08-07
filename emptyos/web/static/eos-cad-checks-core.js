// eos-cad-checks-core.js — pure interpretation of an engineering-scene check result.
//
// Split out of eos-cad-views/checks.js for the same reason eos-cad-part-ops.js was
// split out of the part editor: a view imports '/static/…' by absolute URL, which
// node cannot resolve, so the logic most likely to be WRONG (field names, unit
// formatting, fallbacks for a result family we don't recognise) would be untestable
// while it sat inside the view. Nothing here touches the DOM, the store, or fetch.
//
// The vocabulary is engineering-scene's check contract, produced by
// apps/extension/engineering/engineering-scene/checks.py::run_scene_checks:
//   {family, passes: true|false|null, ...family-specific fields}
// Unknown families degrade to their raw check/status name rather than vanishing.

export const FAMILY_LABEL = {
  heat_flux: 'heat',
  equipment_clearance: 'separation',
  electrical_clearance: 'clearance',
  short_circuit_force: 'sc force',
};

export function familyLabel(family) {
  return FAMILY_LABEL[family] || family || '';
}

// `passes` is deliberately tri-state: false = violated, true = satisfied,
// null = could not be computed (a missing fault level, an engine error). The third
// state is not a failure and must never be rendered as one.
export function verdictClass(passes) {
  if (passes === false) return 'bad';
  if (passes === true) return 'good';
  return 'unknown';
}

// Candidate instance ids for a result, best first. The caller keeps only ids that
// resolve to a real feature in the compiled caddoc — scene_to_caddoc uses the
// instance id AS the feature id, so a hit means selection lands on real geometry.
export function candidateIds(r) {
  if (!r || typeof r !== 'object') return [];
  const out = [];
  if (r.target) out.push(r.target);        // heat_flux: the exposed item
  if (r.a) out.push(r.a);                  // equipment_clearance: the offending pair
  if (r.b) out.push(r.b);
  if (r.source) out.push(r.source);        // heat_flux: the fire source
  if (r.connection) out.push(r.connection);
  return out.filter((v) => typeof v === 'string' && v);
}

export function pickId(doc, r) {
  const feats = (doc && doc.features) || [];
  for (const id of candidateIds(r)) {
    if (feats.some((f) => f && f.id === id)) return id;
  }
  return null;
}

const num = (v) => (v === undefined || v === null ? null : +v);

// One line of plain language per result. An error wins over everything — a result
// that failed to compute should say why, not render a confident-looking zero.
export function describeResult(r) {
  if (!r || typeof r !== 'object') return 'check';
  if (r.error) return String(r.error);
  const fam = r.family;

  if (fam === 'heat_flux') {
    const q = num(r.heat_flux_kw_m2);
    if (q === null) return 'heat flux not computed';
    return `${r.target || '?'} receives ${q.toFixed(1)} kW/m² (limit ${num(r.limit_kw_m2) || 0})`;
  }
  if (fam === 'equipment_clearance') {
    return `${r.a || '?'} ↔ ${r.b || '?'} gap ${(num(r.gap_m) || 0).toFixed(2)} m `
      + `(needs ${(num(r.min_m) || 0).toFixed(2)} m)`;
  }
  if (fam === 'electrical_clearance') {
    const a = num(r.actual_mm);
    if (a === null) return `${r.check || 'clearance'} not computed`;
    return `${r.check || 'clearance'} ${Math.round(a)} mm (needs ${Math.round(num(r.required_mm) || 0)} mm)`;
  }
  if (fam === 'short_circuit_force') {
    const f = num(r.f_support_n) !== null ? num(r.f_support_n) : num(r.f_tensile_n);
    if (f === null) return 'short-circuit force not computed';
    return `support force ${Math.round(f)} N`;
  }
  return r.check || r.status || fam || 'check';
}

// What a result cites as its basis, for the "why is this a rule?" line.
//
//   {kind: 'standard', text}  — a published document (stamped by checks.py from the
//                               engine that computed it)
//   {kind: 'kb', slug, text}  — a project rule whose justification lives in a KB
//                               note; the note may or may not be authored yet
//   null                      — nothing cited
//
// The two are deliberately distinguishable: "AS 2067:2016" and "our own default"
// carry very different weight in a design review, and a readout that blurred them
// would overstate the authority of a project default.
export function citation(r) {
  if (!r || typeof r !== 'object') return null;
  if (r.reference) return { kind: 'standard', text: String(r.reference) };
  if (r.kb) return { kind: 'kb', slug: String(r.kb), text: String(r.rule || r.kb) };
  return null;
}

// Failures first, then could-not-compute, then passes — a readout nobody scrolls is
// a readout nobody reads. Stable within each band (the engine's own order).
export function sortResults(results) {
  const rank = (p) => (p === false ? 0 : (p === true ? 2 : 1));
  return (Array.isArray(results) ? results.slice() : [])
    .map((r, i) => [r, i])
    .sort((x, y) => (rank(x[0] && x[0].passes) - rank(y[0] && y[0].passes)) || (x[1] - y[1]))
    .map((pair) => pair[0]);
}
