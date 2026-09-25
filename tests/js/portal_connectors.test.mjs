/* portal-connectors.js — MCP connectors in the chat home (B5).
 *
 * Pinned: the per-chat set round-trips through the session's comma-separated
 * column without inventing an empty entry, the chip counts rather than lists,
 * and the status line keeps "connected" (a machine fact) apart from "on in
 * this chat" (a per-conversation one).
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { loadStatic } from "./shim.mjs";

const load = (extra = {}) =>
  loadStatic("apps/public/standard/portal/pages/portal-connectors.js", extra).PortalConnectors;
const PC = load();

test("the file loads and exposes its surface", () => {
  for (const n of ["init", "decorateChip", "openPanel", "parseEnabled", "joinEnabled"]) {
    assert.equal(typeof PC[n], "function", `${n} missing`);
  }
});

test("an empty column is no connectors, never one blank one", () => {
  // "".split(",") is [""], which would read as a connector with no name and
  // send the model a tool set for a server called "".
  assert.deepEqual([...PC.parseEnabled("")], []);
  assert.deepEqual([...PC.parseEnabled(null)], []);
  assert.deepEqual([...PC.parseEnabled(" , ,")], []);
});

test("the enabled set round-trips through the stored column", () => {
  assert.deepEqual([...PC.parseEnabled("files,velorn")], ["files", "velorn"]);
  assert.equal(PC.joinEnabled(["files", "velorn"]), "files,velorn");
  assert.equal(PC.joinEnabled([" files ", "velorn"]), "files,velorn");
});

test("joining drops duplicates and keeps the order the user picked", () => {
  assert.equal(PC.joinEnabled(["b", "a", "b", ""]), "b,a");
  assert.equal(PC.joinEnabled([]), "");
});

test("the chip counts, because a chat with four connectors would not fit", () => {
  assert.equal(PC.chipLabel(["a", "b"], 3), "⚭ MCP 2");
  assert.equal(PC.chipLabel([], 3), "⚭ MCP");
  // Nothing declared on this machine: no chip at all.
  assert.equal(PC.chipLabel([], 0), "");
});

test("the status line keeps the machine fact apart from the chat fact", () => {
  const row = { transport: "stdio", connected: true, tools: ["a", "b"], source: "store" };
  assert.equal(PC.rowStatus(row, false), "stdio · 2 tools");
  assert.equal(PC.rowStatus(row, true), "stdio · 2 tools · on in this chat");
  assert.equal(
    PC.rowStatus({ transport: "http", connected: false, source: "config" }, false),
    "http · not connected · from emptyos.toml",
  );
});

test("a config-declared server is labelled, because the UI cannot remove it", () => {
  const s = PC.rowStatus({ transport: "stdio", connected: false, source: "config" }, false);
  assert.match(s, /emptyos\.toml/);
});

test("with the feature off nothing is loaded", () => {
  const pc = load({ PortalChat: { isOn: () => true }, EOS: { apiSafe: () => new Promise(() => {}) } });
  pc.init({ connectors: false });
  assert.equal(pc.isOn(), false);
  assert.equal(pc._state().loaded, false, "a disabled panel must not call the server");
});

test("chat-first off holds it back even with the agent flag on", () => {
  const pc = load({ PortalChat: { isOn: () => false }, EOS: { apiSafe: () => new Promise(() => {}) } });
  pc.init({ connectors: true });
  assert.equal(pc.isOn(), false);
});

test("the page confirms by token and never asserts its own approval", () => {
  // The page must not be able to say "the user approved this" — it sends back
  // the server's one-shot token, and the server writes the row IT held. A
  // `confirm: true` flag anywhere here would be the bypass again, from the
  // one caller best placed to skip the card.
  const src = readFileSync(
    new URL("../../apps/public/standard/portal/pages/portal-connectors.js", import.meta.url),
    "utf8",
  );
  assert.equal(src.includes("confirm: true"), false, "the page must not self-assert approval");
  assert.match(src, /_post\(\{ token: d\.token \}\)/, "confirming sends only the token");
  // And every add goes through the one proposal path — no direct write.
  assert.equal((src.match(/_propose\(/g) || []).length >= 3, true);
});
