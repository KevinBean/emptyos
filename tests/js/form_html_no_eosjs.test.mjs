/* boards/pages/form.html loads eos-components.js WITHOUT eos.js — the only
 * page in the tree that does, and it is the anonymously-reachable public Form
 * view. So every EOS_UI helper it calls must stand alone in that bundle.
 * Loading the file bare here is the point: `window.escAttr` lives in eos.js.
 *
 * WHAT THIS DOES NOT PROVE. These pass both before and after the 2026-09-06
 * fix that qualified eight bare `escAttr(` calls, and a mutation reinstating
 * the bare call in entityCard left them green — because formHtml never reaches
 * any of those eight sites. That is exactly why the escAttr break was latent
 * rather than live, and this file is the standing guard for that: it pins that
 * the public form stays independent of eos.js, NOT that the eight sites are
 * qualified. The evidence for the fix itself is provenance_line.test.mjs,
 * whose chip cases went red before it and green after.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const { EOS_UI } = loadStatic("emptyos/web/static/eos-components.js");

test("formHtml renders without eos.js present", () => {
  const html = EOS_UI.formHtml([
    { key: "name", label: "Name", type: "text" },
    { key: "notes", label: "Notes", type: "textarea" },
  ]);
  assert.ok(html.includes("name"), "field key missing from rendered form");
  assert.ok(html.includes("Notes"), "field label missing from rendered form");
});

test("select options accept {value, label} objects as well as strings", () => {
  const html = EOS_UI.formHtml([
    { key: "pick", label: "Pick", type: "select", value: "b",
      options: [{ value: "", label: "— none —" }, { value: "b", label: "Bee" }, "c"] },
  ]);
  assert.ok(!html.includes("[object Object]"), "object option stringified");
  assert.ok(html.includes('<option value="b" selected>Bee</option>'), "object option lost its value, label or selection");
  assert.ok(html.includes('<option value="">— none —</option>'), "empty-value object option lost its label");
  assert.ok(html.includes('<option value="c">c</option>'), "string option changed");
});

test("multi-select options accept objects and keep their selection", () => {
  const html = EOS_UI.formHtml([
    { key: "many", label: "Many", type: "multi-select", value: ["x"],
      options: [{ value: "x", label: "Ex" }, { value: "y", label: "Why" }] },
  ]);
  assert.ok(html.includes('<option value="x" selected>Ex</option>'), "selected object option not marked");
  assert.ok(html.includes('<option value="y">Why</option>'), "unselected object option wrong");
});

test("a field whose id would need attribute escaping still renders", () => {
  // formHtml puts the key into an id="" — the escAttr path.
  const html = EOS_UI.formHtml([{ key: 'a"b', label: "X", type: "text" }]);
  assert.ok(!html.includes('id="a"b"'), "attribute closed early — unescaped id");
});
