/* PROACTIVE_GROUPS.group — the partition that decides whether a mute toggle
 * renders at all.
 *
 * The proactive catalog IS the mute UI (POST /api/mute refuses a kind it does
 * not list), so a kind that fails to come back from this function is a
 * notification the user receives and cannot silence. Everything below asserts
 * the same invariant from a different angle: every catalog key comes back
 * exactly once.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadStatic } from './shim.mjs';

const g = loadStatic('apps/public/standard/proactive/pages/proactive-groups.js');
const group = (cat, groups) => g.PROACTIVE_GROUPS.group(cat, groups);

// The surface loaded — without this every assertion below runs against undefined.
test('the module exposes group()', () => {
  assert.equal(typeof g.PROACTIVE_GROUPS?.group, 'function');
});

// Array.from at the boundary: values built INSIDE the vm carry the sandbox's
// Array.prototype, and assert/strict's deepEqual compares prototypes — an
// element-for-element identical list fails while the diff prints two that
// look the same (.claude/rules/testing.md).
const flat = (out) => Array.from(out).flatMap((s) => Array.from(s.members));
const CAT = { a: 'A', b: 'B', c: 'C' };

test('members land in their declared group, in order', () => {
  const out = group(CAT, [['First', ['a', 'b']], ['Second', ['c']]]);
  assert.deepEqual(Array.from(out.map((s) => s.name)), ['First', 'Second']);
  assert.deepEqual(Array.from(out[0].members), ['a', 'b']);
});

test('an unplaced kind falls into Other rather than vanishing', () => {
  const out = group(CAT, [['First', ['a']]]);
  assert.deepEqual(Array.from(out.map((s) => s.name)), ['First', 'Other']);
  assert.deepEqual(Array.from(out[1].members), ['b', 'c']);
});

test('no groups at all still renders every kind', () => {
  // The LIVE old-daemon case: index.html hot-reloads while app.py needs a
  // restart, so the page runs against a server that sends no _kinds_groups.
  for (const g2 of [undefined, null, []]) {
    const out = group(CAT, g2);
    assert.deepEqual(Array.from(out.map((s) => s.name)), ['Other']);
    assert.deepEqual(flat(out).sort(), ['a', 'b', 'c']);
  }
});

test('a ghost member is dropped and cannot suppress a real kind', () => {
  const out = group(CAT, [['First', ['a', 'no-such-kind']]]);
  assert.deepEqual(Array.from(out[0].members), ['a']);
  assert.deepEqual(flat(out).sort(), ['a', 'b', 'c']);
});

test('a kind named in two groups renders once, in the first', () => {
  // Two checkboxes for one kind desync after a click: one POSTs the mute, the
  // other keeps the stale tick, which reads as the mute having failed.
  const out = group(CAT, [['First', ['a']], ['Second', ['a', 'b']]]);
  assert.deepEqual(Array.from(out[0].members), ['a']);
  assert.deepEqual(Array.from(out[1].members), ['b']);
  assert.equal(flat(out).filter((k) => k === 'a').length, 1);
});

test('an empty group produces no section', () => {
  const out = group(CAT, [['Empty', []], ['First', ['a', 'b', 'c']]]);
  assert.deepEqual(Array.from(out.map((s) => s.name)), ['First']);
});

test('an empty catalog produces no sections', () => {
  assert.equal(group({}, [['First', ['a']]]).length, 0);
});

test('a kind inheriting a truthy name from Object.prototype still renders', () => {
  // `constructor` is returned by Object.keys but `!placed[k]` reads a truthy
  // inherited value, so a bare object would drop the toggle entirely — a real
  // kind with no off switch. Guarded by Object.create(null) + hasOwnProperty.
  const cat = { constructor: 'C', toString: 'T', valueOf: 'V', normal: 'N' };
  const ungrouped = group(cat, []);
  assert.deepEqual(flat(ungrouped).sort(), ['constructor', 'normal', 'toString', 'valueOf']);
  const grouped = group(cat, [['First', ['constructor']]]);
  assert.deepEqual(Array.from(grouped[0].members), ['constructor']);
  assert.deepEqual(flat(grouped).sort(), ['constructor', 'normal', 'toString', 'valueOf']);
});

test('a group naming ONLY prototype keys absent from the catalog is dropped', () => {
  const out = group({ a: 'A' }, [['Ghosts', ['toString', 'hasOwnProperty']], ['Real', ['a']]]);
  assert.deepEqual(Array.from(out.map((s) => s.name)), ['Real']);
});

test('the invariant, over every shape above: each key exactly once', () => {
  const cases = [
    [CAT, [['F', ['a']]]], [CAT, []], [CAT, null],
    [CAT, [['F', ['a', 'a']], ['S', ['a', 'b', 'c']]]],
    [{ constructor: 'C', a: 'A' }, [['F', ['a']]]],
  ];
  for (const [cat, groups] of cases) {
    const got = flat(group(cat, groups)).sort();
    assert.deepEqual(got, Object.keys(cat).sort(), `shape: ${JSON.stringify(groups)}`);
  }
});
