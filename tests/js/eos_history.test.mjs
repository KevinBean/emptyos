// Node behavior tests for the extracted createHistory factory (eos-history.js) —
// the shared undo/redo stack that eos-cad-store.js + eos-draft-store.js compose.
// Run: `node tests/js/eos_history.test.mjs`, or via tests/test_unit_eos_history.py.

import { createHistory } from '../../emptyos/web/static/eos-history.js';

let ok = 0, fail = 0;
const eq = (a, b, m) => {
  if (JSON.stringify(a) === JSON.stringify(b)) ok++;
  else { fail++; console.log('FAIL', m, '\n  got ', JSON.stringify(a), '\n  want', JSON.stringify(b)); }
};

// Model: a single external value `v`; snapshot captures it, restore sets it back.
let v = 0;
const events = [];
const h = createHistory({
  snapshot: () => v,
  restore: (s) => { v = s; },
  limit: 3,
  onChange: (st) => events.push(st),
});

// empty history
eq([h.canUndo, h.canRedo], [false, false], 'starts empty');
eq(h.undo(), false, 'undo on empty returns false');
eq(h.redo(), false, 'redo on empty returns false');
eq(events.length, 0, 'no onChange on empty undo/redo');

// push (snapshots v=0) then mutate, then undo restores
h.push(); v = 1;
eq([h.canUndo, h.canRedo], [true, false], 'canUndo after push, redo cleared');
eq(h.undo(), true, 'undo returns true');
eq(v, 0, 'undo restored the pre-push value');
eq([h.canUndo, h.canRedo], [false, true], 'redo available after undo');
eq(h.redo(), true, 'redo returns true');
eq(v, 1, 'redo re-applied the value');

// a fresh push clears the redo branch
h.push(); v = 2; h.undo();          // redoStack now has 1 entry (v=2)
eq(h.canRedo, true, 'redo present before a fresh push');
h.push(); v = 3;
eq(h.canRedo, false, 'a fresh push clears redo');

// onChange fires with boolean flags
const last = events[events.length - 1];
eq([typeof last.canUndo, typeof last.canRedo], ['boolean', 'boolean'], 'onChange carries {canUndo,canRedo}');

// limit caps the undo depth
let g = createHistory({ snapshot: () => 0, restore: () => {}, limit: 3 });
for (let i = 0; i < 6; i++) g.push();
let n = 0; while (g.undo()) n++;
eq(n, 3, 'history capped at limit=3');

// onChange is optional (no throw when omitted)
let q = createHistory({ snapshot: () => 0, restore: () => {} });
q.push(); eq(q.undo(), true, 'works without onChange');

// clear() drops both stacks
let c = createHistory({ snapshot: () => 0, restore: () => {} });
c.push(); c.push(); c.undo();
eq([c.canUndo, c.canRedo], [true, true], 'stacks populated before clear');
c.clear();
eq([c.canUndo, c.canRedo], [false, false], 'clear drops both stacks');

if (fail) { console.log(`\n${fail} FAILED, ${ok} passed`); process.exit(1); }
console.log(`ALL ${ok} PASS`);
