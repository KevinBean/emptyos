/* worklog — CPEng competency chip rendering.
 *
 * `renderCompetencyTags` is the last piece of competency logic left in the
 * browser. An earlier cut also carried a JS copy of the server's
 * COMPETENCY_TAG_RE and a hardcoded FOCUS_ELEMENTS = [11, 13]; both were
 * deleted after a hostile review measured them disagreeing with the server on
 * out-of-range tags and on ordering against attachment stripping — an item
 * could be COUNTED as evidence by the roll-up while rendering with no chip.
 * The page now renders whatever `/api/day` says. What remains testable is that
 * the chip carries a WORD and not just a colour, and that it refuses junk.
 *
 * First app-page consumer of the shim: loadStatic takes any repo path, and the
 * helper lives in its own sibling file precisely so it can be loaded without
 * worklog.js's boot IIFE (.claude/rules/testing.md).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const W = loadStatic("apps/public/standard/worklog/pages/competency-chips.js");
const render = W.renderCompetencyTags;

test("the surface under test actually loaded", () => {
  assert.equal(typeof render, "function");
});

test("a focus element carries the word, not only the red", () => {
  // .claude/rules/list-card-density.md — status is never colour alone. The
  // page's own calendar comment says the same thing three lines away.
  const html = render([11], { 11: "gap" });
  assert.match(html, /#c11/);
  assert.match(html, />#c11 gap</, "the word 'gap' must be inside the chip text");
  assert.match(html, /class="cp-tag focus"/);
});

test("a non-focus element gets no focus class and no word", () => {
  const html = render([6], {});
  assert.match(html, /class="cp-tag"/);
  assert.doesNotMatch(html, /focus/);
  assert.match(html, />#c6</);
});

test("focus map is read by string key, the shape the server sends", () => {
  assert.match(render([13], { "13": "gap" }), />#c13 gap</);
});

test("out-of-range and junk element numbers render nothing", () => {
  for (const bad of [[0], [17], [99], ["nope"], [null], [undefined]]) {
    assert.equal(render(bad, {}), "", `expected '' for ${JSON.stringify(bad)}`);
  }
});

test("empty and missing input are safe", () => {
  assert.equal(render([], {}), "");
  assert.equal(render(undefined, undefined), "");
});

test("a focus label reaching the chip is escaped, not injected", () => {
  const html = render([11], { 11: '<img src=x onerror=alert(1)>' });
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});

test("several elements render in the order given", () => {
  const html = render([2, 11], { 11: "gap" });
  assert.ok(html.indexOf("#c2") < html.indexOf("#c11"));
});
