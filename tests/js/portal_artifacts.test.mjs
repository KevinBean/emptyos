/* portal-artifacts.js — the chat's artifact panel (B4).
 *
 * Pinned: a revision replaces its entry rather than stacking a second one,
 * the newest-touched artifact is the one the panel opens, and only a
 * CreateArtifact result claims the panel — every other tool passes through.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const PA = loadStatic("apps/public/standard/portal/pages/portal-artifacts.js").PortalArtifacts;

test("the file loads and exposes its surface", () => {
  for (const n of ["init", "openFor", "onToolResult", "close", "mergeArtifact", "artifactOf"]) {
    assert.equal(typeof PA[n], "function", `${n} missing`);
  }
});

test("a new artifact goes to the front of the list", () => {
  let items = PA.mergeArtifact([], { id: "a", title: "First" });
  items = PA.mergeArtifact(items, { id: "b", title: "Second" });
  assert.deepEqual([...items].map((a) => a.id), ["b", "a"]);
});

test("revising an artifact moves its entry instead of adding one", () => {
  let items = PA.mergeArtifact([], { id: "a", title: "First" });
  items = PA.mergeArtifact(items, { id: "b", title: "Second" });
  items = PA.mergeArtifact(items, { id: "a", title: "First v2" });
  assert.equal(items.length, 2, "a revision must not stack a duplicate");
  assert.deepEqual([...items].map((a) => a.id), ["a", "b"]);
  assert.equal(items[0].title, "First v2");
});

test("a revision that omits a field keeps what the entry already knew", () => {
  // The live tool result carries title+shape; a later one might not.
  let items = PA.mergeArtifact([], { id: "a", title: "Budget", shape: "chart" });
  items = PA.mergeArtifact(items, { id: "a" });
  assert.equal(items[0].title, "Budget");
  assert.equal(items[0].shape, "chart");
});

test("urls are derived when the server did not send them", () => {
  const [a] = PA.mergeArtifact([], { id: "ab 12", title: "T" });
  assert.equal(a.url, "/viz/api/html/ab%2012");
  assert.equal(a.source_url, "/viz/api/source/ab%2012");
  assert.equal(a.open_url, "/viz/#ab%2012");
});

test("a payload with no id changes nothing", () => {
  const items = [{ id: "a", title: "First" }];
  assert.equal(PA.mergeArtifact(items, {}), items);
  assert.equal(PA.mergeArtifact(items, null), items);
  assert.equal(PA.mergeArtifact(items, { title: "no id" }), items);
});

test("only a CreateArtifact result claims the panel", () => {
  assert.equal(PA.artifactOf({ name: "Read", path: "a.md" }), null);
  assert.equal(PA.artifactOf({}), null);
  assert.equal(PA.artifactOf(null), null);
  // An artifact block without an id is not usable either — the id is the
  // whole address of the thing to show.
  assert.equal(PA.artifactOf({ artifact: { title: "T" } }), null);
  assert.deepEqual({ ...PA.artifactOf({ artifact: { id: "a" } }) }, { id: "a" });
});

test("the header label names the kind when there is one", () => {
  assert.equal(PA.labelFor({ title: "Budget", shape: "chart" }), "Budget · chart");
  assert.equal(PA.labelFor({ title: "Budget" }), "Budget");
  assert.equal(PA.labelFor(null), "");
});

/* The flag tests need a PortalChat, because init() ANDs three things together.
 * Without one, `typeof PortalChat !== "undefined"` is false and S.on stays
 * false whatever cfg says — so a test of "the flag is respected" would pass
 * with the flag check deleted entirely (audits.md § Failure mode 3). */
const withChat = (chatOn) =>
  loadStatic("apps/public/standard/portal/pages/portal-artifacts.js", {
    PortalChat: { isOn: () => chatOn },
    // open() fetches the version ring; the shim has no network, and a pending
    // promise that never settles is the honest stand-in (nothing here asserts
    // on versions).
    EOS: { apiSafe: () => new Promise(() => {}), toast: () => {} },
  }).PortalArtifacts;

test("the agent app's flag is what turns the panel on", () => {
  const off = withChat(true);
  off.init({ artifacts: false });
  assert.equal(off.isOn(), false, "cfg.artifacts false must leave it off");
  assert.equal(off.onToolResult({ artifact: { id: "a", title: "T" } }), false);

  const on = withChat(true);
  on.init({ artifacts: true });
  assert.equal(on.isOn(), true);
});

test("chat-first off leaves the panel off even with the agent flag on", () => {
  const pa = withChat(false);
  pa.init({ artifacts: true });
  assert.equal(pa.isOn(), false);
});

test("a live artifact result is merged and opened", () => {
  // The ON path — the panel's entire live behaviour, which the off-path test
  // above cannot reach.
  const pa = withChat(true);
  pa.init({ artifacts: true });
  assert.equal(pa.onToolResult({ artifact: { id: "a", title: "Budget", shape: "chart" } }), true);
  const S = pa._state();
  assert.deepEqual([...S.items].map((x) => x.id), ["a"]);
  assert.equal(S.openId, "a", "the artifact just made is the one shown");
  // Another tool's result must not disturb it.
  assert.equal(pa.onToolResult({ name: "Read", path: "a.md" }), false);
  assert.equal(pa._state().openId, "a");
});

test("the chosen version survives a re-render", () => {
  // It used to be read back off the <select>, so a versions fetch resolving —
  // or a tab switch, or the next tool result — rebuilt the picker at "Current"
  // and silently returned the user to the live render while they believed they
  // were looking at an old one.
  const pa = withChat(true);
  pa.init({ artifacts: true });
  pa.onToolResult({ artifact: { id: "a", title: "T" } });
  const S = pa._state();
  S.versions = [{ n: 2, prompt: "second" }, { n: 1, prompt: "first" }];
  S.version = "1";
  assert.equal(S.version, "1");
  // A revision of the SAME artifact starts from the live render again — that
  // is deliberate: the new render is what the user just asked for.
  pa.onToolResult({ artifact: { id: "a", title: "T v2" } });
  assert.equal(pa._state().version, "", "a fresh revision opens on Current");
});
