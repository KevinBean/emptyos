/* Print the two real request payloads as JSON, for the Python contract test.
 *
 * The Python side compares these against the keys safety.py actually reads.
 * Nothing here asserts — a failure to BUILD a payload must surface as a crash
 * with node's own message, not as an empty object the comparison would pass
 * vacuously against.
 */

import { makeHarness } from "./earthing_harness.mjs";

const NEUTRAL = { "ne-vll": 33, "ne-z1r": 0.5, "ne-z1x": 5.0, "ne-z0r": 0.5,
                  "ne-z0x": 2.0, "ne-freq": 50, "ne-kind": "resistance",
                  "ne-rn": 19.05, "ne-itarget": 1000,
                  "ne-tf": 0.5, "ne-rated-i": 1000, "ne-rated-t": 10 };
const TRANSFER = { "tp-gpr": 10852, "tp-rho": 299.2, "tp-rg": 1.036, "tp-tol": 1489 };

const n = makeHarness(NEUTRAL);
await n.ctx.computeNeutralEarthing();

const t = makeHarness(TRANSFER);
t.ctx.STATE.transferObjects = [
  { name: "fence", distance_m: "300", bonded: true, own_earth_ohm: "" },
  { name: "pipe", distance_m: "20", bonded: false, own_earth_ohm: "5" },
];
await t.ctx.evaluateTransfer();

// A THIRD run whose only difference is a blank distance. Without this the
// Python side could not observe the refusal at all: every dumped object
// carried a real distance, so `assert distance_m > 0` held no matter what the
// page did, and deleting the guard left that test green. The batch runner
// caught it as a survivor; the manual loop had reported it caught, because the
// JS test — a different file — was the one going red.
const blank = makeHarness(TRANSFER);
blank.ctx.STATE.transferObjects = [
  { name: "seeded crossing", distance_m: "", bonded: true, own_earth_ohm: "" },
];
await blank.ctx.evaluateTransfer();

console.log(JSON.stringify({
  neutral: n.calls.length ? n.calls[0].body : null,
  transfer: t.calls.length ? t.calls[0].body : null,
  // null means the page refused to POST — which is the correct outcome here.
  transfer_blank_distance: blank.calls.length ? blank.calls[0].body : null,
}));
