// eos-cad-part-ops.js — pure part-editor document mutations (no DOM, no imports),
// the store-mutating core ported from the legacy part-workspace for the layout-model
// `part-tools` view (stage 5 of legacy-workspace retirement, .claude/rules/cad-layouts.md).
//
// Each mutation edits store.doc in place then calls store.notifyDoc() — which fires
// the 'doc' event the (proven) viewport-3d view re-renders from, exactly as the legacy
// buildScene()'s vp.setDocument+rebuild did. Kept import-free so it unit-tests under
// node directly (the view shell lives in part-tools.js).

export const DEFAULT_COLOR = '#3a6ea5';

export function nextFeatureId(doc, op) {
  const ids = new Set(((doc && doc.features) || []).map((f) => f.id));
  let i = 1;
  while (ids.has(op + i)) i++;
  return op + i;
}

export function primitiveFeature(doc, op, at) {
  const f = { id: nextFeatureId(doc, op), op, name: op, color: DEFAULT_COLOR,
    at: at || [0, 0, 0], rotate: [0, 0, 0] };
  if (op === 'box') f.size = [20, 20, 20];
  else if (op === 'sphere') f.r = 10;
  else if (op === 'cylinder') { f.r = 8; f.h = 20; f.axis = 'z'; }
  else if (op === 'cone') { f.r = 10; f.r2 = 0; f.h = 20; f.axis = 'z'; }
  return f;
}

export function spawnAt(store) {
  const c = (store && store.cursor) || { x: 0, y: 0, z: 0 };
  return [Math.round(c.x || 0), Math.round(c.y || 0), Math.round(c.z || 0)];
}

export function addPrimitive(store, op) {
  const doc = store.doc;
  if (store.pushUndo) store.pushUndo();
  if (!doc.features) doc.features = [];
  const f = primitiveFeature(doc, op, spawnAt(store));
  doc.features.push(f);
  store.notifyDoc();             // → viewport-3d setDocument + rebuild (proven path)
  if (store.select) store.select(f.id);
  return f.id;
}

export function addParam(store, name, value) {
  const n = String(name == null ? '' : name).trim();
  if (!n) return false;
  const doc = store.doc;
  if (store.pushUndo) store.pushUndo();
  if (!doc.params) doc.params = {};
  const v = parseFloat(value);
  doc.params[n] = isNaN(v) ? 0 : v;
  store.notifyDoc();
  return true;
}

export function deleteFeature(store, id) {
  const doc = store.doc;
  const feats = doc.features || [];
  const next = feats.filter((f) => f.id !== id);
  if (next.length === feats.length) return false;
  if (store.pushUndo) store.pushUndo();
  doc.features = next;
  if (store.selection === id && store.select) store.select(null);
  store.notifyDoc();
  return true;
}

export function toggleFeatureEnabled(store, id) {
  const f = (store.doc.features || []).find((x) => x.id === id);
  if (!f) return null;
  if (store.pushUndo) store.pushUndo();
  // enabledFeat(f) is true unless f.enabled === false; flip around that.
  f.enabled = (f.enabled === false);
  store.notifyDoc();
  return f.enabled;
}

export function moveFeature(store, id, dir) {
  // dir = -1 (up / earlier) | +1 (down / later). Returns true if it moved.
  const feats = store.doc.features || [];
  const i = feats.findIndex((f) => f.id === id);
  if (i < 0) return false;
  const j = i + (dir < 0 ? -1 : 1);
  if (j < 0 || j >= feats.length) return false;
  if (store.pushUndo) store.pushUndo();
  const [f] = feats.splice(i, 1);
  feats.splice(j, 0, f);
  store.notifyDoc();
  return true;
}

export function booleanFeature(doc, op, pickedIds) {
  if (!Array.isArray(pickedIds) || pickedIds.length < 2) return null;
  const f = { id: nextFeatureId(doc, op), op, name: op, color: DEFAULT_COLOR, at: [0, 0, 0], rotate: [0, 0, 0] };
  if (op === 'subtract') { f.target = pickedIds[0]; f.tools = pickedIds.slice(1); }
  else f.inputs = pickedIds.slice();   // union / intersect
  return f;
}

export function applyBoolean(store, op, pickedIds) {
  const f = booleanFeature(store.doc, op, pickedIds);
  if (!f) return null;
  if (store.pushUndo) store.pushUndo();
  if (!store.doc.features) store.doc.features = [];
  store.doc.features.push(f);
  store.notifyDoc();
  if (store.select) store.select(f.id);
  return f.id;
}
