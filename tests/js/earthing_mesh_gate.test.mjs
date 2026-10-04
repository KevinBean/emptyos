/* The §16 mesh inputs travel only when the user has them open — and that
 * decision must not depend on page layout.
 *
 * The page moved its cards onto tabs (2026-10-03). The fault-case matrix used
 * to decide "are the mesh inputs shown?" from layout (offsetParent), and from
 * the Faults tab the Grid tab's inputs have no layout, so every matrix run
 * silently dropped to the GPR screen. The gate now reads the Schwarz extras'
 * own display flag (rgMeshInputsShown). These tests call the real payload
 * builders against the harness, where nothing has layout at all — exactly the
 * hidden-tab condition.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { makeHarness, present } from "./earthing_harness.mjs";

const GRID = {
  "rg-rho": 100, "rg-L": 500, "rg-A": 2500, "rg-h": 0.5, "rg-spacing": 7,
  "rg-len": 50, "rg-wid": 50, "rg-d-cond": 10, "rg-n-rods": 20, "rg-rod-L": 3, "rg-rod-d": 16,
  "rg-method": "schwarz", "sv-s-f": 1, "sv-d-f": 1, "sv-c-p": 1,
};

function harness(extrasShown) {
  const h = makeHarness(GRID);
  h.el("rg-schwarz-extras").style.display = extrasShown ? "" : "none";
  return h;
}

test("matrix grid spec: mesh geometry travels when the inputs are open, on any tab", { skip: !present }, () => {
  const spec = harness(true).ctx.faultMatrixGridSpec();
  assert.equal(spec.grid_length_m, 50);
  assert.equal(spec.grid_width_m, 50);
  assert.equal(spec.spacing_m, 7);
  assert.equal(spec.conductor_diameter_m, 0.01);
});

test("matrix grid spec: no mesh geometry while the inputs are closed (Sverak defaults)", { skip: !present }, () => {
  const spec = harness(false).ctx.faultMatrixGridSpec();
  assert.equal(spec.grid_length_m, undefined);
  assert.equal(spec.spacing_m, undefined);
});

test("verdict payload: the same gate — no mesh on geometry nobody entered", { skip: !present }, () => {
  const open = harness(true).ctx.verdictPayload(5000);
  assert.equal(open.grid_length, 50);
  assert.equal(open.rho_a, 100);
  const closed = harness(false).ctx.verdictPayload(5000);
  assert.equal(closed.grid_length, undefined);
  assert.equal(closed.rho_a, undefined);
});
