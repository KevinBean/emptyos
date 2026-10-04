/* provenanceLine — the guard both link and quotes rely on.
 *
 * Wired 2026-09-06 alongside the two provenance-drop fixes (link's orphan
 * insights, quotes' AI generate). Both call sites render the chip
 * unconditionally into a template string, so the '' return for an absent
 * provenance is what keeps a pre-generation surface clean rather than
 * printing a bare wrapper.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const { EOS_UI } = loadStatic("emptyos/web/static/eos-components.js");

test("EOS_UI and provenanceLine actually loaded", () => {
  assert.ok(EOS_UI, "eos-components.js did not expose EOS_UI");
  assert.equal(typeof EOS_UI.provenanceLine, "function");
});

test("absent provenance renders nothing at all", () => {
  // Not merely falsy — both call sites concatenate this into a bigger string,
  // so 'undefined' or an empty <div> would both be visible defects.
  for (const v of [null, undefined, {}, { provider: "x" }, { mode: "" }]) {
    assert.equal(EOS_UI.provenanceLine(v), "", `expected '' for ${JSON.stringify(v)}`);
  }
});

test("a real provenance renders a chip naming mode and provider", () => {
  const out = EOS_UI.provenanceLine({ mode: "cloud", provider: "openai-mini", model: "gpt-5.4-mini" });
  assert.ok(out.includes("eos-badge-provenance"), "no provenance chip class");
  assert.ok(out.includes("cloud"), "mode not rendered");
  assert.ok(out.includes("openai-mini"), "provider not rendered");
  assert.ok(out.includes("gpt-5.4-mini"), "model not rendered");
});

test("local mode is distinguishable from cloud, not just styled", () => {
  // The whole point of the chip is telling a reader whether their data left
  // the machine; if both modes rendered the same text this would be decoration.
  const cloud = EOS_UI.provenanceLine({ mode: "cloud", provider: "p" });
  const local = EOS_UI.provenanceLine({ mode: "local", provider: "p" });
  assert.notEqual(cloud, local);
  assert.ok(local.includes("local"));
});
