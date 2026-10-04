/* A caveat under a table of evaluated rows names the rows it applies to.
 *
 * The fault matrix, scenario compare and sweep each list their rows' caveats
 * (LIM-DEPTH) under the table with one helper, earthRowCaveats. A row-level
 * caveat that loses its row reads as true of the whole table; one repeated per
 * row buries the table under identical notes.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { makeHarness, present } from "./earthing_harness.mjs";

const W = "Burial depth h = 3 m is outside 0.25 m < h < 2.5 m";

test("each distinct caveat once, naming every row it applies to", { skip: !present }, () => {
  const { ctx } = makeHarness();
  assert.equal(typeof ctx.earthRowCaveats, "function");
  const rows = [
    { label: "HV busbar", warnings: [W] },
    { label: "MV feeder", warnings: [] },
    { label: "LV board", warnings: [W, "other"] },
  ];
  const out = Array.from(ctx.earthRowCaveats(rows, (r) => r.label));
  assert.deepEqual(out, [`HV busbar, LV board: ${W}`, "LV board: other"]);
  assert.deepEqual(Array.from(ctx.earthRowCaveats([{ label: "a" }], (r) => r.label)), []);
});

test("a row with a caveat carries the word, not only a colour", { skip: !present }, () => {
  const { ctx } = makeHarness();
  assert.match(ctx.earthCaveatMark({ warnings: [W] }), /\(caveat\)/);
  assert.equal(ctx.earthCaveatMark({ warnings: [] }), "");
  assert.equal(ctx.earthCaveatMark({}), "");
});
