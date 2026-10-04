/* Pins the System-Icon backlog loop's stopping conditions.
 *
 * The loop spends one model call per iteration, so both directions matter:
 * it must not grind on a model that cannot do the job, and it must not stop
 * on the first unusable draft either — that halted a 200-app run every few
 * icons once echo detection started refusing clones.
 *
 * `studio` is a PERSONAL app (apps/personal/ is gitignored from this repo),
 * so this file skips rather than fails where that tree is absent — a public
 * clone and CI must stay green. See tests/test_unit_js_suite.py.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { loadStatic, REPO_ROOT } from "./shim.mjs";

const REL = "apps/personal/studio/pages/icon-batch-loop.js";
const PRESENT = fs.existsSync(path.join(REPO_ROOT, REL));

const B = PRESENT ? loadStatic(REL).STUDIO_ICON_BATCH : null;

const ok = { app_id: "a", status: "ready" };
const bad = { app_id: "a", status: "error", error: "unusable" };
const busy = { planned: 5, error: 0 };
const empty = { planned: 0, error: 0 };
const run = (o) => B.nextStep({ continuous: true, running: true, ...o });

test("the module under test is actually loaded", { skip: !PRESENT && "apps/personal absent" }, () => {
  // Without this, every case below would throw on a null B rather than pass —
  // but a reader seeing 6 skips should know which half of the fork they got.
  assert.equal(typeof B.nextStep, "function");
  assert.equal(B.ERROR_STREAK, 3);
});

test("carries on past an isolated unusable draft", { skip: !PRESENT && "apps/personal absent" }, () => {
  // The regression this loop was changed for: the old code stopped here.
  const step = run({ item: bad, summary: busy, streak: 0 });
  assert.equal(step.action, "continue");
  assert.equal(step.streak, 1);
});

test("a good draft clears the streak so failures must be consecutive", { skip: !PRESENT && "apps/personal absent" }, () => {
  const step = run({ item: ok, summary: busy, streak: 2 });
  assert.equal(step.action, "continue");
  assert.equal(step.streak, 0, "a success must reset, or scattered failures would add up to a stop");
});

test("stops on the third consecutive unusable draft, not the second", { skip: !PRESENT && "apps/personal absent" }, () => {
  assert.equal(run({ item: bad, summary: busy, streak: 1 }).action, "continue");
  const step = run({ item: bad, summary: busy, streak: 2 });
  assert.equal(step.action, "stop-unusable");
  assert.equal(step.streak, 3);
});

test("an empty backlog stops as idle, never as a model failure", { skip: !PRESENT && "apps/personal absent" }, () => {
  // These two must stay distinguishable: 'stop-idle' is a finished run and
  // must not surface the "your model cannot do this" error.
  assert.equal(run({ item: ok, summary: empty, streak: 0 }).action, "stop-idle");
  assert.equal(run({ item: bad, summary: empty, streak: 0 }).action, "stop-idle");
});

test("a paused or non-continuous run does not schedule more work", { skip: !PRESENT && "apps/personal absent" }, () => {
  assert.equal(run({ item: ok, summary: busy, streak: 0, running: false }).action, "stop-idle");
  assert.equal(run({ item: ok, summary: busy, streak: 0, continuous: false }).action, "stop-idle");
  // ...but a model that is plainly failing still stops loudly even when the
  // user has already paused, so the reason is not lost.
  assert.equal(run({ item: bad, summary: busy, streak: 2, running: false }).action, "stop-unusable");
});

test("a missing summary is treated as nothing left to do", { skip: !PRESENT && "apps/personal absent" }, () => {
  // A malformed response must not spin the loop forever.
  assert.equal(run({ item: ok, streak: 0 }).action, "stop-idle");
  assert.equal(B.nextStep().action, "stop-idle");
});
