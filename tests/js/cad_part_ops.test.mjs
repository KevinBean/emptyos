// Node behavior tests for emptyos/web/static/eos-cad-part-ops.js — the pure
// store-mutating core of the layout-model part-editor (stage 5 of legacy-workspace
// retirement). Run directly: `node tests/js/cad_part_ops.test.mjs`, or via the
// pytest wrapper tests/test_unit_cad_part_ops.py (skips when node is absent).
//
// These pin the offline-verifiable subset (add-primitive / add-param). The visual
// render reuses the proven viewport-3d view; daemon-dependent features (AI draft,
// export, save, booleans, gizmo, extensions) are not yet ported.

import {
  nextFeatureId, primitiveFeature, addPrimitive, addParam,
  deleteFeature, toggleFeatureEnabled, moveFeature, booleanFeature, applyBoolean,
} from '../../emptyos/web/static/eos-cad-part-ops.js';

let ok = 0, fail = 0;
const eq = (a, b, m) => {
  if (JSON.stringify(a) === JSON.stringify(b)) ok++;
  else { fail++; console.log('FAIL', m, '\n  got ', JSON.stringify(a), '\n  want', JSON.stringify(b)); }
};
function fakeStore(doc) {
  return {
    doc, events: [], sel: null, cursor: { x: 3, y: 0, z: 5 },
    notifyDoc() { this.events.push('doc'); },
    select(id) { this.sel = id; },
  };
}

// nextFeatureId increments past existing ids
eq(nextFeatureId({ features: [{ id: 'box1' }, { id: 'box2' }] }, 'box'), 'box3', 'nextFeatureId past existing');
eq(nextFeatureId({ features: [] }, 'sphere'), 'sphere1', 'nextFeatureId empty');

// primitive defaults per op
const cyl = primitiveFeature({ features: [] }, 'cylinder', [0, 0, 0]);
eq([cyl.op, cyl.r, cyl.h, cyl.axis], ['cylinder', 8, 20, 'z'], 'cylinder defaults');
const box = primitiveFeature({ features: [] }, 'box', [1, 2, 3]);
eq([box.size, box.at], [[20, 20, 20], [1, 2, 3]], 'box size + at');
const cone = primitiveFeature({ features: [] }, 'cone', [0, 0, 0]);
eq([cone.r, cone.r2, cone.h], [10, 0, 20], 'cone defaults');

// addPrimitive mutates store + fires 'doc' + selects, spawns at rounded cursor
const s = fakeStore({ features: [] });
eq(addPrimitive(s, 'box'), 'box1', 'addPrimitive returns id');
eq(s.doc.features.length, 1, 'feature pushed');
eq(s.doc.features[0].at, [3, 0, 5], 'spawn at cursor');
eq(s.events, ['doc'], "doc event fired (→ viewport rebuild)");
eq(s.sel, 'box1', 'selected new feature');
addPrimitive(s, 'box');
eq(s.doc.features[1].id, 'box2', 'second box id increments');

// addParam
const s2 = fakeStore({ features: [], params: {} });
eq(addParam(s2, ' r ', '8.5'), true, 'addParam trims + accepts');
eq(s2.doc.params.r, 8.5, 'param value parsed');
eq(addParam(s2, '', '5'), false, 'addParam rejects empty name');
eq(addParam(s2, 'n', 'notnum'), true, 'addParam non-numeric accepted');
eq(s2.doc.params.n, 0, 'non-numeric param → 0');

// deleteFeature removes + clears selection if it was selected + fires 'doc'
const s3 = fakeStore({ features: [{ id: 'box1' }, { id: 'box2' }] });
s3.sel = 'box1';
s3.selection = 'box1';
eq(deleteFeature(s3, 'box1'), true, 'deleteFeature returns true');
eq(s3.doc.features.map((f) => f.id), ['box2'], 'feature removed');
eq(s3.sel, null, 'selection cleared on delete of selected');
eq(deleteFeature(s3, 'nope'), false, 'deleteFeature missing id → false');

// toggleFeatureEnabled flips around the enabledFeat (default-true) convention
const s4 = fakeStore({ features: [{ id: 'box1' }] });
eq(toggleFeatureEnabled(s4, 'box1'), false, 'first toggle disables');
eq(s4.doc.features[0].enabled, false, 'enabled=false set');
eq(toggleFeatureEnabled(s4, 'box1'), true, 'second toggle re-enables');
eq(toggleFeatureEnabled(s4, 'nope'), null, 'toggle missing → null');

// moveFeature reorders within bounds
const s5 = fakeStore({ features: [{ id: 'a' }, { id: 'b' }, { id: 'c' }] });
eq(moveFeature(s5, 'c', -1), true, 'move c up');
eq(s5.doc.features.map((f) => f.id), ['a', 'c', 'b'], 'reordered a,c,b');
eq(moveFeature(s5, 'a', -1), false, 'move first up → false (clamped)');
eq(moveFeature(s5, 'b', 1), false, 'move last down → false (clamped)');

// booleanFeature shape: subtract = target+tools, union/intersect = inputs
const bsub = booleanFeature({ features: [] }, 'subtract', ['a', 'b', 'c']);
eq([bsub.target, bsub.tools], ['a', ['b', 'c']], 'subtract target+tools');
const bun = booleanFeature({ features: [] }, 'union', ['a', 'b']);
eq(bun.inputs, ['a', 'b'], 'union inputs');
eq(booleanFeature({ features: [] }, 'union', ['a']), null, 'boolean needs >=2 picks');

// applyBoolean pushes + selects + fires 'doc'
const s6 = fakeStore({ features: [{ id: 'box1' }, { id: 'box2' }] });
eq(applyBoolean(s6, 'subtract', ['box1', 'box2']), 'subtract1', 'applyBoolean id');
eq(s6.doc.features.length, 3, 'boolean feature pushed');
eq(s6.sel, 'subtract1', 'boolean selected');

if (fail) { console.log('FAILED', fail, 'of', ok + fail); process.exit(1); }
console.log('ALL', ok, 'PASS');
