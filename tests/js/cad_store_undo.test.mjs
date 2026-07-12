// Node behavior tests for the undo/redo history added to createCadStore
// (emptyos/web/static/eos-cad-store.js) + its integration with the pure part ops.
// Run: `node tests/js/cad_store_undo.test.mjs`, or via tests/test_unit_cad_store_undo.py.
//
// Pins the reversibility contract the part surface relies on: snapshot-before-mutate,
// deep (unaliased) snapshots, redo cleared on a new edit, the bounded history, and
// that an AI-apply-style setDoc is reversible.

import { createCadStore } from '../../emptyos/web/static/eos-cad-store.js';
import { addPrimitive, deleteFeature } from '../../emptyos/web/static/eos-cad-part-ops.js';

let ok = 0, fail = 0;
const eq = (a, b, m) => {
  if (JSON.stringify(a) === JSON.stringify(b)) ok++;
  else { fail++; console.log('FAIL', m, '\n  got ', JSON.stringify(a), '\n  want', JSON.stringify(b)); }
};

// ── empty history ──
const s = createCadStore({ schema: 'eos-cad/1', units: 'mm', params: {}, features: [], ext: {} });
eq([s.canUndo, s.canRedo], [false, false], 'starts with empty history');
eq(s.undo(), false, 'undo on empty returns false');
eq(s.redo(), false, 'redo on empty returns false');

// ── snapshot-before-mutate (the manual contract) ──
s.pushUndo();
s.doc.features.push({ id: 'box1', op: 'box' });
s.notifyDoc();
eq(s.doc.features.length, 1, 'one feature after add');
eq([s.canUndo, s.canRedo], [true, false], 'canUndo after a pushUndo');
eq(s.undo(), true, 'undo returns true');
eq(s.doc.features.length, 0, 'undo removed the feature');
eq([s.canUndo, s.canRedo], [false, true], 'redo available after undo');
eq(s.redo(), true, 'redo returns true');
eq(s.doc.features.length, 1, 'redo re-added the feature');

// ── snapshots are deep / unaliased: mutating the live doc must not corrupt history ──
s.pushUndo();
s.doc.features[0].op = 'sphere';         // in-place edit after snapshot
s.notifyDoc();
s.undo();
eq(s.doc.features[0].op, 'box', 'undo restored the pre-edit value (deep snapshot)');

// ── a new edit clears the redo stack ──
s.pushUndo(); s.doc.features.push({ id: 'box2', op: 'box' }); s.notifyDoc();
s.undo();                                 // redo now has the 2-feature doc
eq(s.canRedo, true, 'redo present before a fresh edit');
s.pushUndo(); s.doc.params.r = 5; s.notifyDoc();
eq(s.canRedo, false, 'a fresh pushUndo clears redo');

// ── AI-apply shape (setDoc) is reversible ──
const before = JSON.parse(JSON.stringify(s.doc));
s.pushUndo();
s.setDoc({ schema: 'eos-cad/1', units: 'mm', params: {}, features: [{ id: 'ai1', op: 'cone' }], ext: {} });
eq(s.doc.features[0].id, 'ai1', 'AI apply replaced the doc');
s.undo();
eq(s.doc, before, 'undo restored the pre-AI-apply doc exactly');

// ── history events fire ──
const evs = [];
s.subscribe((e) => evs.push(e.type), ['history']);
s.pushUndo(); s.undo();
eq(evs.filter((t) => t === 'history').length >= 2, true, 'history events emitted on push + undo');

// ── bounded history (HISTORY_LIMIT = 60): older snapshots drop off ──
const b = createCadStore({ schema: 'eos-cad/1', units: 'mm', params: {}, features: [], ext: {} });
for (let i = 0; i < 65; i++) { b.pushUndo(); b.doc.params['p' + i] = i; b.notifyDoc(); }
let undos = 0;
while (b.undo()) undos++;
eq(undos, 60, 'history capped at 60 undo steps');

// ── part-ops integration: addPrimitive snapshots, undo removes it ──
const p = createCadStore({ schema: 'eos-cad/1', units: 'mm', params: {}, features: [], ext: {} });
addPrimitive(p, 'box');
eq(p.doc.features.length, 1, 'addPrimitive added a feature');
eq(p.canUndo, true, 'addPrimitive pushed an undo snapshot');
p.undo();
eq(p.doc.features.length, 0, 'undo removed the primitive');

// deleteFeature is likewise reversible
addPrimitive(p, 'box'); addPrimitive(p, 'sphere');
const n = p.doc.features.length;
deleteFeature(p, p.doc.features[0].id);
eq(p.doc.features.length, n - 1, 'deleteFeature removed one');
p.undo();
eq(p.doc.features.length, n, 'undo restored the deleted feature');

if (fail) { console.log(`\n${fail} FAILED, ${ok} passed`); process.exit(1); }
console.log(`ALL ${ok} PASS`);
