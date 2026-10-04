// Node behavior tests for the pure helpers in eos-cad-ai-panel.js.

import {
  resolveAiMode,
  buildProposePayload,
} from '../../emptyos/web/static/eos-cad-ai-panel.js';

let ok = 0, fail = 0;
const eq = (actual, expected, message) => {
  if (JSON.stringify(actual) === JSON.stringify(expected)) ok++;
  else {
    fail++;
    console.log('FAIL', message, '\n  got ', JSON.stringify(actual), '\n  want', JSON.stringify(expected));
  }
};

eq(resolveAiMode({ hasContent: false }), 'new', 'empty document defaults to new');
eq(resolveAiMode({ hasContent: true }), 'edit', 'populated document defaults to edit');
eq(resolveAiMode({ hasContent: true, lockedMode: 'new' }), 'new', 'explicit new lock wins');
eq(resolveAiMode({ hasContent: false, lockedMode: 'edit' }), 'edit', 'explicit edit lock wins');

const doc = { schema: 'eos-cad/1', features: [{ id: 'box1' }] };
eq(buildProposePayload({ prompt: '  make a plate  ', mode: 'new', doc }),
  { prompt: 'make a plate' }, 'new proposal omits base');
eq(buildProposePayload({ prompt: 'add a hole', mode: 'edit', doc,
  selection: { id: 'box1', label: 'Base box' } }),
  { prompt: 'add a hole', base: doc, selection: 'box1', selection_label: 'Base box' },
  'single selection is preserved');
eq(buildProposePayload({ prompt: 'align these', mode: 'edit', doc,
  selection: { ids: ['l1', 'l2'], label: '2 entities' } }),
  { prompt: 'align these', base: doc, selection: ['l1', 'l2'], selection_label: '2 entities' },
  'multi-selection is preserved');

if (fail) { console.log('FAILED', fail, 'of', ok + fail); process.exit(1); }
console.log('ALL', ok, 'PASS');
