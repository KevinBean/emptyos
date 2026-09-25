/* Pins which build the Cable Pulling+ page believes it is showing.
 *
 * The page must stay HOSTED (nothing unpublished shown) unless the status
 * route says plainly that this is your own copy. A failed call, an error body,
 * or a daemon that predates the field all leave it hosted. Held app, so every
 * case skips where apps/extension/ is absent (tests/test_unit_js_suite.py).
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { loadStatic, REPO_ROOT } from "./shim.mjs";

const REL = "apps/extension/engineering/cable-pulling/pages/pulling-host.js";
const PRESENT = fs.existsSync(path.join(REPO_ROOT, REL));
const H = PRESENT ? loadStatic(REL).PP_HOST : null;
const skip = { skip: !PRESENT && "held app absent (public snapshot)" };

const OWN = { hosted: false, examples: { split: [{ type: "straight" }], noBend: [] } };
const HOSTED = { hosted: true, kb_slugs: ["pulling-pit-placement"], citations: { "ieee-1185-2019-4-10": "IEEE 1185-2019 §4.10" } };

test("the module under test is actually loaded", skip, () => {
  assert.equal(typeof H.fromStatus, "function");
});

test("before any reply the page is hosted and shows no unpublished note", skip, () => {
  const s = H.initial();
  assert.equal(s.on, true);
  assert.equal(H.kbShown(s, "pulling-pit-placement"), false);
  assert.equal(s.examples, null);
});

for (const [label, reply] of [
  ["a failed call (null)", null],
  ["an error body", { error: "boom" }],
  ["a daemon without the field", { enabled: true, remedies: false }],
  ["hosted as a truthy string", { hosted: "false" }],
]) {
  test(`${label} leaves the page hosted`, skip, () => {
    const s = H.fromStatus(reply, H.initial());
    assert.equal(s.on, true);
    assert.equal(s.examples, null);
    assert.equal(H.kbShown(s, "cable-caterpillar-installation-au"), false);
  });
}

test("your own copy gets the worked examples and every ⓘ", skip, () => {
  const s = H.fromStatus(OWN, H.initial());
  assert.equal(s.on, false);
  assert.ok(Array.isArray(s.examples.split));
  assert.equal(H.kbShown(s, "cable-caterpillar-installation-au"), true);
});

test("a hosted build shows only the published notes, and never examples", skip, () => {
  const s = H.fromStatus(HOSTED, H.initial());
  assert.equal(s.on, true);
  assert.equal(H.kbShown(s, "pulling-pit-placement"), true);
  assert.equal(H.kbShown(s, "cable-caterpillar-installation-au"), false);
  assert.equal(s.examples, null);
  assert.equal(s.citations["ieee-1185-2019-4-10"], "IEEE 1185-2019 §4.10");
});

test("a failed later call does not undo a known own copy", skip, () => {
  const own = H.fromStatus(OWN, H.initial());
  assert.equal(H.fromStatus(null, own).on, false);
});

test("own copy fetches a renamed note by its vault slug; hosted by its public name", skip, () => {
  const own = H.fromStatus({ ...OWN, kb_alias: { "pull-section-modelling": "pull-planner-section-modelling" } }, H.initial());
  assert.equal(H.vaultSlug(own, "pull-section-modelling"), "pull-planner-section-modelling");
  assert.equal(H.vaultSlug(own, "pulling-pit-placement"), "pulling-pit-placement");
  const hosted = H.fromStatus(HOSTED, H.initial());
  assert.equal(H.vaultSlug(hosted, "pull-section-modelling"), "pull-section-modelling");
});

test("the TB 889 estimator shows only in a confirmed own copy", skip, () => {
  assert.equal(H.estimatorShown(H.initial()), false);
  assert.equal(H.estimatorShown(H.fromStatus(null, H.initial())), false);
  assert.equal(H.estimatorShown(H.fromStatus(HOSTED, H.initial())), false);
  assert.equal(H.estimatorShown(H.fromStatus(OWN, H.initial())), true);
});

test("saved projects show only in a confirmed own copy", skip, () => {
  assert.equal(H.projectsShown(H.initial()), false);
  assert.equal(H.projectsShown(H.fromStatus(null, H.initial())), false);
  assert.equal(H.projectsShown(H.fromStatus(HOSTED, H.initial())), false);
  assert.equal(H.projectsShown(H.fromStatus(OWN, H.initial())), true);
});

// A stand-in page: the buttons apply() touches, each with a `hidden` flag.
function fakePage() {
  const btn = (onclick) => ({ hidden: true, getAttribute: () => onclick });
  const byId = { "pp-estimator-btn": btn(""), "pp-ex-split": btn(""), "pp-ex-nobend": btn(""),
                 "pp-projects-btn": btn(""), "pp-save-project-btn": btn("") };
  const infos = [btn("ppKbPopover('pulling-pit-placement',null,this)"),
                 btn("ppKbPopover('cable-caterpillar-installation-au','x',this)")];
  return {
    byId, infos,
    getElementById: (id) => byId[id] || null,
    querySelectorAll: () => infos,
  };
}

test("apply() on your own copy shows the estimator, projects, the examples and every info button", skip, () => {
  const doc = fakePage();
  H.apply(H.fromStatus(OWN, H.initial()), doc);
  assert.equal(doc.byId["pp-estimator-btn"].hidden, false);
  assert.equal(doc.byId["pp-projects-btn"].hidden, false);
  assert.equal(doc.byId["pp-save-project-btn"].hidden, false);
  assert.equal(doc.byId["pp-ex-split"].hidden, false);
  assert.deepEqual(doc.infos.map((b) => b.hidden), [false, false]);
});

test("apply() on a hosted build hides the estimator, projects, the examples and the unpublished note", skip, () => {
  const doc = fakePage();
  for (const b of [...Object.values(doc.byId), ...doc.infos]) b.hidden = false;
  H.apply(H.fromStatus(HOSTED, H.initial()), doc);
  assert.equal(doc.byId["pp-estimator-btn"].hidden, true);
  assert.equal(doc.byId["pp-projects-btn"].hidden, true);
  assert.equal(doc.byId["pp-save-project-btn"].hidden, true);
  assert.equal(doc.byId["pp-ex-split"].hidden, true);
  assert.equal(doc.byId["pp-ex-nobend"].hidden, true);
  assert.deepEqual(doc.infos.map((b) => b.hidden), [false, true]);
});

test("popover() fetches the vault slug in your own copy", skip, () => {
  const calls = [];
  const ui = { kbPopover: (slug, section) => calls.push([slug, section]) };
  const own = H.fromStatus({ ...OWN, kb_alias: { "pull-section-modelling": "pull-planner-section-modelling" } }, H.initial());
  H.popover(own, ui, "pull-section-modelling", "Decision table", null);
  H.popover(H.fromStatus(HOSTED, H.initial()), ui, "pull-section-modelling", "Decision table", null);
  assert.deepEqual(calls.map((c) => Array.from(c)), [
    ["pull-planner-section-modelling", "Decision table"],
    ["pull-section-modelling", "Decision table"],
  ]);
});
