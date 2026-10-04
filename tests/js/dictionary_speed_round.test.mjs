/* dictionary — speed round pure helpers (pages/speed-round.js).
 *
 * The recorder and rendering are exercised by a browser walk; what is
 * testable here is the arithmetic a learner reads in the results header and
 * the card pick.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const W = loadStatic("apps/public/englishos/dictionary/pages/speed-round.js");

test("the surface under test actually loaded", () => {
  assert.equal(typeof W.speedRoundSummary, "function");
  assert.equal(typeof W.speedRoundPick, "function");
  assert.equal(typeof W.speedRoundLabel, "function");
});

test("the average covers only right answers that were timed", () => {
  const sum = { ...W.speedRoundSummary([
    { verdict: "fast", onset_s: 1.0 },
    { verdict: "slow", onset_s: 3.0 },
    { verdict: "missed", onset_s: 0.2 },   // a quick wrong word is not recall
    { verdict: "right", onset_s: null },   // correct, time not measured
    { verdict: "pending" },
  ]) };
  assert.deepEqual(sum, { total: 5, remembered: 2, slow: 1, missed: 1, pending: 1, avg_s: 2 });
});

test("with nothing timed the average is unknown, not zero", () => {
  const sum = W.speedRoundSummary([{ verdict: "right" }, { verdict: "missed", onset_s: 1 }]);
  assert.equal(sum.avg_s, null);
});

test("a scoring failure counts against the round, never as pending", () => {
  const sum = W.speedRoundSummary([{ verdict: "error" }]);
  assert.equal(sum.missed, 1);
  assert.equal(sum.pending, 0);
});

test("every verdict has a word, and an unknown one still does", () => {
  for (const v of ["fast", "right", "slow", "missed", "pending", "error", "bogus"]) {
    assert.ok(W.speedRoundLabel(v).length > 0, v);
  }
});

test("the pick is a sample of the list, capped at the round size", () => {
  const items = Array.from({ length: 30 }, (_, i) => ({ slug: "s" + i }));
  const picked = Array.from(W.speedRoundPick(items, 10, () => 0.5));
  assert.equal(picked.length, 10);
  assert.equal(new Set(picked.map((p) => p.slug)).size, 10);
  assert.ok(picked.every((p) => p.slug.startsWith("s")));
  assert.deepEqual(items.map((i) => i.slug), Array.from({ length: 30 }, (_, i) => "s" + i),
    "the caller's list is not shuffled in place");
  assert.equal(Array.from(W.speedRoundPick([{ slug: "a" }, {}, null], 10)).length, 1);
});
