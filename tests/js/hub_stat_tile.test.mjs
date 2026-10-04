/* Pins the core hub's `stat-tile` renderer to the documented data contract.
 *
 * `BaseApp.stat_tile(icon, value, label, href)` (docs/APP-DEVELOPMENT.md,
 * renderer table) promises `{icon, value, label, href}`, and the personal hub
 * renders the icon — but the core hub never had, from its first commit, while
 * 14 of the 40 live dashboard tiles were passing one (2026-09-03). The
 * renderer is a pure function of its items, so it runs under the shim.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { loadStatic, makeWindow, REPO_ROOT } from "./shim.mjs";

// The page and the shared renderer bundle are classic scripts sharing ONE
// global scope in the browser — `hrefAttr` (bundle) resolves `escAttr` (a
// window global from eos.js) and hub.js reads `EOS_HUB_RENDERERS` (bundle).
// So both files run into a single context here, in load order. hub.js also
// builds page chrome at load (`EOS_UI.pageHeader`, `EOS.*`), which needs a
// real DOM the shim does not have; the renderer under test touches none of
// it, so those two globals are inert stubs. `escAttr` is the shipped
// `EOS_UI.escAttr` — the same four replacements eos.js installs on window.
// Only `URLSearchParams` is added: timers must stay the shim's unref'd
// wrappers, or a load-time interval in hub.js would hang `node --test`.
const inert = () => new Proxy({}, { get: () => () => ({}) });
const comps = loadStatic("emptyos/web/static/eos-components.js");
const hub = makeWindow({
  escAttr: comps.EOS_UI.escAttr,
  EOS_UI: inert(),
  EOS: inert(),
  URLSearchParams,
});
vm.createContext(hub);
for (const rel of ["emptyos/web/static/eos-hub-renderers.js", "apps/public/core/hub/pages/hub.js"]) {
  vm.runInContext(fs.readFileSync(path.join(REPO_ROOT, rel), "utf8"), hub, { filename: rel });
}

test("hub.js loaded with its renderer map", () => {
  assert.equal(typeof hub.RENDERERS, "object");
  assert.equal(typeof hub.RENDERERS["stat-tile"], "function");
});

const tile = (data) => hub.RENDERERS["stat-tile"]([{ data }]);

test("renders the contract's icon inside the label element, once", () => {
  const html = tile({ icon: "📥", value: 3, label: "Inbox to triage", href: "/quick-action/#triage" });
  // Anchored to the label div: an icon rendered in `.r-stat-sub`, as bare
  // text, or twice must all fail here (audits.md § Failure mode 3 — an
  // assertion scoped wider than the thing it names).
  assert.match(html, /r-stat-label">📥 Inbox to triage<\//);
  assert.equal((html.match(/📥/g) || []).length, 1);
  assert.match(html, /r-stat-val">3</);
  assert.match(html, /href="\/quick-action\/#triage"/);
});

test("a tile without an icon renders its label unchanged", () => {
  const html = tile({ value: 7, label: "Pending", href: "/x/" });
  assert.match(html, /r-stat-label">Pending</);
  assert.doesNotMatch(html, /undefined|null/);
});

test("icon and label are escaped, not injected", () => {
  const html = tile({ icon: "<b>", value: 1, label: "<i>", href: "/x/" });
  assert.doesNotMatch(html, /<b>|<i>/);
  assert.match(html, /&lt;b&gt; &lt;i&gt;/);
});

test("a null contributor is skipped, not rendered as a blank tile", () => {
  const html = hub.RENDERERS["stat-tile"]([{ data: null }, { data: { value: 1, label: "a", href: "/a/" } }]);
  assert.equal((html.match(/r-stat-tile/g) || []).length, 1);
});
