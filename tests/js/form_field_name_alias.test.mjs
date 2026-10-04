/* A formModal field declared `name:` instead of `key:` must still carry the
 * typed value to the submit handler.
 *
 * Six pages made that mistake independently (soil "+ New", publish "Edit
 * reply", feature-pipeline "Block brief", sandbox lease, music-studio upload,
 * robot-modeller edit). Each rendered fine, accepted typing, and handed its
 * handler an object with no such field, so it silently fell back — an edit
 * that never saved, a reason replaced by a default. EOS_UI now reads `name`
 * as `key`; these tests drive the real formModal -> _submitForm path.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

function load() {
  const win = loadStatic("emptyos/web/static/eos-components.js");
  const inputs = {};
  win.document.getElementById = (id) => inputs[id] || null;
  win.EOS_UI.modal = () => {};       // no DOM to open into; the form path is what's under test
  win.EOS_UI.closeModal = () => {};
  win.EOS_UI.toast = () => {};
  return { win, EOS_UI: win.EOS_UI, inputs };
}

test("a `name:` field reaches the submit handler with the typed value", () => {
  const { EOS_UI, inputs } = load();
  let got = null;
  EOS_UI.formModal("Edit reply",
    [{ name: "reply", label: "Reply", type: "textarea", required: true }],
    (vals) => { got = { ...vals }; });
  inputs["eos-form-reply"] = { value: "a better answer" };
  EOS_UI._submitForm();
  assert.deepEqual(got, { reply: "a better answer" });
});

test("object-form formModal takes the same path", () => {
  const { EOS_UI, inputs } = load();
  let got = null;
  EOS_UI.formModal({ title: "Edit", fields: [{ name: "edit_prompt", label: "Edit" }],
                     onSubmit: (vals) => { got = { ...vals }; } });
  inputs["eos-form-edit_prompt"] = { value: "longer arm" };
  EOS_UI._submitForm();
  assert.deepEqual(got, { edit_prompt: "longer arm" });
});

test("rendered input ids come from `name` when `key` is absent", () => {
  const { EOS_UI } = load();
  const html = EOS_UI.formHtml([{ name: "title", label: "Title" }]);
  assert.ok(html.includes('id="eos-form-title"'), html);
  assert.ok(!html.includes("eos-form-undefined"), html);
});

test("`key` wins when both are given, and caller fields are not mutated", () => {
  const { EOS_UI } = load();
  const fields = [{ key: "k", name: "n", label: "L" }, { name: "only", label: "M" }];
  const norm = EOS_UI._normFields(fields);
  assert.equal(norm[0].key, "k");
  assert.equal(norm[1].key, "only");
  assert.equal("key" in fields[1], false, "caller's field object was mutated");
});

test("formValues reads a `name:` field when called directly with formHtml", () => {
  // boards' public form pairs formHtml + formValues without formModal, so
  // formValues must normalise on its own rather than rely on formModal.
  const { EOS_UI, inputs } = load();
  inputs["eos-form-x"] = { value: "typed" };
  assert.deepEqual({ ...EOS_UI.formValues([{ name: "x", label: "X" }]) }, { x: "typed" });
});

test("aiFormFill reads its schema the same way", () => {
  const { EOS_UI } = load();
  let schemaSeen = null;
  const real = EOS_UI._normFields;
  EOS_UI._normFields = (f) => { const out = real(f); schemaSeen = out; return out; };
  try { EOS_UI.aiFormFill({ schema: [{ name: "title", label: "Title" }], onSubmit: () => {} }); }
  catch (e) { /* no DOM to open into; the schema intake is what's under test */ }
  assert.ok(schemaSeen && schemaSeen[0].key === "title", "aiFormFill did not normalise its schema");
});
