/* Earthing neutral + transferred-potential payloads — EXECUTED, not grepped.
 *
 * WHY THIS REPLACED A SOURCE SCANNER
 * ----------------------------------
 * The first version of this contract lived in Python and regex-extracted the
 * payload keys out of earthing.js. A hostile review broke it eight ways, all
 * of which passed 13/13 green while the page sent the wrong thing:
 *
 *   - a `{` inside a comment or string shrank the brace-counted key set, so
 *     the "every key is read by the route" assertion passed vacuously;
 *   - `own_earth_ohm === '' ? 0 : (…|| null)` — the exact inversion the test
 *     named — satisfied a substring check for "null" and "===";
 *   - conditional keys assigned after the literal (`body.fault_duration_s`)
 *     were invisible, so renaming one silently disabled the thermal check;
 *   - the nested `arrangement` object was never compared to anything, so
 *     `spec.r_n_ohm` -> `spec.rn_ohm` turned an NER into a solid earth.
 *
 * A regex cannot see a payload. This file builds the payload by CALLING the
 * function against stubs and asserts the object itself, so every one of those
 * mutations changes an observed value.
 *
 * This is the same discipline as tests/test_unit_worklog_offline_js.py:
 * "these tests EXECUTE the overrides … instead of grepping the source".
 */

import test from "node:test";
import assert from "node:assert/strict";
import { makeHarness, present } from "./earthing_harness.mjs";

const NEUTRAL_OK = {
  "ne-vll": 33, "ne-z1r": 0.5, "ne-z1x": 5.0,
  "ne-z0r": 0.5, "ne-z0x": 2.0, "ne-freq": 50,
  "ne-kind": "resistance", "ne-rn": 19.05,
};

test("neutral payload: the object the page actually builds", { skip: !present }, async () => {
  const h = makeHarness(NEUTRAL_OK);
  await h.ctx.computeNeutralEarthing();
  assert.equal(h.calls.length, 1, "exactly one request");
  const b = h.calls[0].body;

  // Exact top-level key set. A renamed, added or dropped key fails here —
  // including a conditional one, because this is the real object.
  assert.deepEqual(
    Object.keys(b).sort(),
    ["arrangement", "freq_hz", "v_ll_kv", "z0_r", "z0_x", "z1_r", "z1_x"],
  );
  assert.equal(b.v_ll_kv, 33);
  assert.equal(b.z1_r, 0.5);
  assert.equal(b.z1_x, 5.0);
  assert.equal(b.z0_r, 0.5);
  assert.equal(b.z0_x, 2.0);
  assert.equal(b.freq_hz, 50);
  // Flat, not nested — the mistake made by hand three times.
  assert.equal(typeof b.z1_r, "number");
  assert.equal(b.z1_ohm, undefined);
});

test("neutral payload: the nested arrangement carries the engineering", { skip: !present }, async () => {
  const h = makeHarness(NEUTRAL_OK);
  await h.ctx.computeNeutralEarthing();
  const a = h.calls[0].body.arrangement;
  // The scanner never compared this object to anything, so spec.r_n_ohm ->
  // spec.rn_ohm turned an NER into a solid earth and stayed green.
  assert.equal(a.kind, "resistance");
  assert.equal(a.r_n_ohm, 19.05);
  assert.equal(a.x_n_ohm, undefined, "x_n_ohm belongs to the reactance kind only");
});

test("resonant sends its own fields and zeroes the series Z0", { skip: !present }, async () => {
  const h = makeHarness({ ...NEUTRAL_OK, "ne-kind": "resonant",
                          "ne-c0": 3.0, "ne-detune": 0.05, "ne-q": 40 });
  await h.ctx.computeNeutralEarthing();
  const b = h.calls[0].body;
  // The route REFUSES a series Z0 on this kind. The inputs are hidden, so a
  // stale 0.5/2.0 must not be read out of boxes the user cannot see.
  assert.equal(b.z0_r, 0, "series Z0 must be zeroed for resonant");
  assert.equal(b.z0_x, 0);
  assert.equal(b.arrangement.c0_uf_per_phase, 3.0);
  assert.equal(b.arrangement.detuning, 0.05);
  assert.equal(b.arrangement.coil_q, 40);
});

test("i_target_a is sent only for a resistance arrangement", { skip: !present }, async () => {
  const yes = makeHarness({ ...NEUTRAL_OK, "ne-itarget": 1000 });
  await yes.ctx.computeNeutralEarthing();
  assert.equal(yes.calls[0].body.i_target_a, 1000);

  const no = makeHarness({ ...NEUTRAL_OK, "ne-kind": "resonant",
                           "ne-c0": 3.0, "ne-itarget": 1000 });
  await no.ctx.computeNeutralEarthing();
  assert.equal(no.calls[0].body.i_target_a, undefined,
    "the route refuses i_target on a non-resistance kind; do not reach that refusal");
});

test("the thermal trio is all-or-nothing", { skip: !present }, async () => {
  // One value alone: the route's check needs all three, so sending it alone
  // produced no block, no error and no message.
  const partial = makeHarness({ ...NEUTRAL_OK, "ne-tf": 0.5 });
  await partial.ctx.computeNeutralEarthing();
  assert.equal(partial.calls.length, 0, "must refuse before the request");

  const all = makeHarness({ ...NEUTRAL_OK, "ne-tf": 0.5,
                            "ne-rated-i": 1000, "ne-rated-t": 10 });
  await all.ctx.computeNeutralEarthing();
  assert.equal(all.calls[0].body.fault_duration_s, 0.5);
  assert.equal(all.calls[0].body.arrangement.rated_current_a, 1000);
  assert.equal(all.calls[0].body.arrangement.rated_time_s, 10);
});

test("an empty form is refused by name, not by the engine's phrasing", { skip: !present }, async () => {
  const h = makeHarness({});
  await h.ctx.computeNeutralEarthing();
  assert.equal(h.calls.length, 0, "nothing should be sent");
  const msg = h.el("ne-result").innerHTML;
  assert.match(msg, /nominal voltage/);
  assert.match(msg, /source Z/);
});

test("the derived current reaches the fault case rounded, with its notes", { skip: !present }, async () => {
  const h = makeHarness(NEUTRAL_OK);
  await h.ctx.computeNeutralEarthing();
  h.ctx.sendNeutralToFaultCases();
  const c = h.ctx.STATE.faultCases[0];
  // Rounded: the raw solve is unusable in an editable field.
  assert.equal(c.fault_current_a, 954.8);
  // Blank on purpose — clearing time is a protection input, not derived.
  assert.equal(c.fault_duration_s, "");
  // Provenance carried, not dropped.
  assert.match(c.notes, /derived/);
});

const TRANSFER_OK = {
  "tp-gpr": 10852, "tp-rho": 299.2, "tp-rg": 1.036, "tp-tol": 1489,
};

test("transfer payload uses the ROUTE's names, not the engine's", { skip: !present }, async () => {
  const h = makeHarness(TRANSFER_OK);
  h.ctx.STATE.transferObjects = [
    { name: "fence", distance_m: "300", bonded: true, own_earth_ohm: "" },
  ];
  await h.ctx.evaluateTransfer();
  assert.equal(h.calls.length, 1);
  const b = h.calls[0].body;
  assert.deepEqual(Object.keys(b).sort(),
    ["gpr_v", "objects", "rg", "rho_soil", "tolerable_touch_v"]);
  // The engine takes rho_ohm_m / r_g_ohm; the ROUTE takes rho_soil / rg.
  assert.equal(b.rho_soil, 299.2);
  assert.equal(b.rg, 1.036);
  assert.equal(b.rho_ohm_m, undefined);
  assert.equal(b.r_g_ohm, undefined);
});

test("a blank own_earth_ohm is null, never 0", { skip: !present }, async () => {
  const h = makeHarness(TRANSFER_OK);
  h.ctx.STATE.transferObjects = [
    { name: "fence", distance_m: "300", bonded: true, own_earth_ohm: "" },
    { name: "pipe", distance_m: "20", bonded: false, own_earth_ohm: "5" },
  ];
  await h.ctx.evaluateTransfer();
  const objs = h.calls[0].body.objects;
  // TransferObject.__post_init__ REFUSES own_earth_ohm <= 0, so a 0 here turns
  // an untouched optional box into an error on every row.
  assert.equal(objs[0].own_earth_ohm, null);
  assert.equal(objs[1].own_earth_ohm, 5);
  assert.equal(objs[0].distance_m, 300);
  assert.equal(objs[0].bonded, true);
  assert.equal(objs[1].bonded, false);
});

test("a blank DISTANCE is refused, never screened as 0 m", { skip: !present }, async () => {
  const h = makeHarness(TRANSFER_OK);
  // This is exactly what `seedTypicalCrossings` produces.
  h.ctx.STATE.transferObjects = [
    { name: "Perimeter fence", distance_m: "", bonded: true, own_earth_ohm: "" },
  ];
  await h.ctx.evaluateTransfer();
  assert.equal(h.calls.length, 0, "must not POST a fabricated 0 m");
  // 0 m is the maximally FAVOURABLE answer: V_feet == GPR, so a bonded object
  // has touch_v of exactly 0 and is reported "pass" on a distance nobody gave.
  assert.match(h.el("tp-result").innerHTML, /no distance/i);
});

test("the governing row is keyed on position, so duplicate names do not both mark",
     { skip: !present }, async () => {
  const h = makeHarness(TRANSFER_OK);
  h.ctx.STATE.transferObjects = [
    { name: "a", distance_m: "20", bonded: false, own_earth_ohm: "" },
    { name: "a", distance_m: "300", bonded: true, own_earth_ohm: "" },
  ];
  await h.ctx.evaluateTransfer();
  // The canned response deliberately has TWO objects named "a" and governing.name = "a".
  const html = h.el("tp-result").innerHTML;
  const marks = (html.match(/&rarr;/g) || []).length;
  assert.equal(marks, 1, "exactly one governing marker, not one per matching name");
});

test("seeded crossings carry no fabricated distance", { skip: !present }, async () => {
  const h = makeHarness(TRANSFER_OK);
  h.ctx.STATE.transferObjects = [];
  h.ctx.seedTransferObjects();
  assert.equal(h.ctx.STATE.transferObjects.length, 5);
  for (const o of h.ctx.STATE.transferObjects) {
    assert.equal(o.distance_m, "", `${o.name} must ship blank — a seeded distance is a site fact`);
  }
});
