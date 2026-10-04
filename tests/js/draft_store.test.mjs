// Node behavior tests for emptyos/web/static/eos-draft-store.js.

import { createDraftStore, emptyDraftDoc } from '../../emptyos/web/static/eos-draft-store.js';

let ok = 0, fail = 0;
const eq = (a, b, m) => {
  if (JSON.stringify(a) === JSON.stringify(b)) ok++;
  else { fail++; console.log('FAIL', m, '\n  got ', JSON.stringify(a), '\n  want', JSON.stringify(b)); }
};

const doc = emptyDraftDoc();
doc.entities.push({ id: 'e1', type: 'line', start: [0, 0], end: [10, 0], layer: '0' });
const events = [];
const store = createDraftStore(doc, { paper: [420, 297], titleblock: [{ id: 'tb1' }], vaultPath: 'outputs/x/record.md' });
store.subscribe((evt) => events.push(evt.type));

eq(store.doc.schema, 'eos-draft/1', 'schema normalized');
eq(store.paper, [420, 297], 'paper meta stored');
eq(store.vaultPath, 'outputs/x/record.md', 'vault path stored');

store.setSelection(new Set(['e1']));
eq(store.selection, ['e1'], 'set selection from Set');

store.setTool('line', { pts: [] });
eq(store.tool.name, 'line', 'tool set');
store.setView({ cx: 10, scale: 3 });
eq(store.view, { cx: 10, cy: 148, scale: 3 }, 'view patch merged');

store.pushUndo();
store.doc.entities.push({ id: 'e2', type: 'circle', center: [0, 0], r: 5, layer: '0' });
store.notifyDoc('test');
eq(store.doc.entities.length, 2, 'doc mutation visible');
eq(store.dirty, true, 'notify marks dirty');
eq(store.undo(), true, 'undo returns true');
eq(store.doc.entities.map((e) => e.id), ['e1'], 'undo restores snapshot');
eq(store.redo(), true, 'redo returns true');
eq(store.doc.entities.map((e) => e.id), ['e1', 'e2'], 'redo restores mutation');

store.markDirty(false);
eq(store.dirty, false, 'mark clean');
eq(events.includes('select'), true, 'select event emitted');
eq(events.includes('doc'), true, 'doc event emitted');
eq(events.includes('dirty'), true, 'dirty event emitted');

if (fail) { console.log('FAILED', fail, 'of', ok + fail); process.exit(1); }
console.log('ALL', ok, 'PASS');
