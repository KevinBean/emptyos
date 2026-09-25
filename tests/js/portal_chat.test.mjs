/* portal-chat.js — the chat-first home's pure logic and its flag-off contract.
 *
 * The rendering is walked in a browser (/eos-ui-walk). What is pinned here is
 * the logic a browser walk cannot see going wrong: which models a chat's picker
 * offers, which model a new chat starts on, which backend chat-first restores,
 * that nothing happens with the flag off, and that the flag-on boot claims the
 * composer BEFORE the (slow) provider probe answers.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const CONV = { rooms: {}, assistant: {}, agent: {} };

function load(apiSafe, extra = {}, stored = {}) {
  const writes = [];
  const ctx = loadStatic("apps/public/standard/portal/pages/portal-chat.js", {
    EOS: { apiSafe, toast: () => {} },
    localStorage: {
      getItem: (k) => (k in stored ? stored[k] : null),
      setItem: (k, v) => writes.push([k, v]),
    },
    ACTIVE_BACKEND: "rooms",
    ACTIVE_VERB: "think",
    CONV_BACKENDS: CONV,
    currentAgent: null,
    _syncBackendUI: () => {},
    _startAgentThread: () => {},
    ...extra,
  });
  return { ctx, PC: ctx.PortalChat, writes };
}

const P = [
  { name: "claude-cli", kind: "native", chat_ok: false, available: true },
  { name: "openai-mini", kind: "openai", chat_ok: true, available: true },
  { name: "openai", kind: "openai", chat_ok: true, available: false },
  { name: "anthropic_sdk", kind: "anthropic", chat_ok: true, available: true },
];

test("the file loads and exposes its surface", () => {
  const { PC } = load(async () => ({}));
  for (const n of ["init", "isOn", "start", "selectBackend", "pickThreadModel", "decorateChip",
                   "afterSync", "initialModel", "threadRows", "restoreBackend"]) {
    assert.equal(typeof PC[n], "function", `${n} missing`);
  }
});

test("a chat with history is offered only models of its stored kind", () => {
  const { PC } = load(async () => ({}));
  const names = (rows) => Array.from(rows, (r) => r.name);
  assert.deepEqual(names(PC.threadRows(P, "openai")), ["openai-mini", "openai"]);
  assert.deepEqual(names(PC.threadRows(P, "anthropic")), ["anthropic_sdk"]);
  // No history yet: every chat-capable model, never the native one.
  assert.deepEqual(names(PC.threadRows(P, "")), ["openai-mini", "openai", "anthropic_sdk"]);
});

test("a new chat starts on a model that can drive it and answers", () => {
  const { PC } = load(async () => ({}));
  assert.equal(PC.initialModel(P, "anthropic_sdk", "openai-mini"), "anthropic_sdk");
  assert.equal(PC.initialModel(P, "openai", "openai-mini"), "openai-mini");        // saved one is down
  assert.equal(PC.initialModel(P, "claude-cli", "openai-mini"), "openai-mini");    // saved one is native
  assert.equal(PC.initialModel(P, "gone", "claude-cli"), "openai-mini");           // default is native
  assert.equal(PC.initialModel([P[0]], "", "claude-cli"), "");                     // nothing usable
});

test("with the default down, a local model is preferred over an earlier cloud one", () => {
  // The server's own order (sessions.py _chat_default_provider): a chat reads
  // the vault, so rule 19 keeps it local when a local model answers.
  const { PC } = load(async () => ({}));
  const rows = [
    { name: "openai-mini", kind: "openai", chat_ok: true, available: true, is_cloud: true },
    { name: "openai", kind: "openai", chat_ok: true, available: false, is_cloud: true },
    { name: "ollama", kind: "openai", chat_ok: true, available: true, is_cloud: false },
  ];
  assert.equal(PC.initialModel(rows, "", "openai"), "ollama");
  assert.equal(PC.initialModel(rows.slice(0, 2), "", "openai"), "openai-mini");   // no local: cloud
});

test("chat-first restores its own remembered backend, else Chat", () => {
  const { PC } = load(async () => ({}));
  assert.equal(PC.restoreBackend("assistant", CONV), "assistant");
  assert.equal(PC.restoreBackend("chat", CONV), "chat");
  assert.equal(PC.restoreBackend("code", CONV), "chat");     // an action, not a backend
  assert.equal(PC.restoreBackend("", CONV), "chat");
});

test("with the flag off nothing changes and nothing is written", async () => {
  const calls = [];
  const { ctx, PC, writes } = load(async (url) => { calls.push(url); return { chat_first: false }; });
  assert.equal(await PC.init(), false);
  PC.selectBackend("chat");
  PC.afterSync();
  await PC.pickThreadModel({ _backend: "agent", _profile: "chat", _sid: "s" });
  assert.equal(PC.isOn(), false);
  assert.equal(ctx.ACTIVE_BACKEND, "rooms");
  assert.deepEqual(writes, []);
  assert.deepEqual(calls, ["/portal/api/config"]);   // one config GET, no provider probe
});

test("flag on: the composer switches to Chat before the provider probe answers", async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const apiSafe = async (url) => {
    if (url === "/portal/api/config") return { chat_first: true };
    if (url === "/agent/api/providers") { await gate; return { providers: P, chat_default: "openai-mini" }; }
    return {};
  };
  const { ctx, PC } = load(apiSafe);
  const done = PC.init();
  // Let init() get past the config await; the providers call is still pending.
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(PC.isOn(), true);
  assert.equal(ctx.ACTIVE_BACKEND, "chat", "a submit now must start a chat, not a Rooms thread");
  assert.equal(PC.model(), "");
  release();
  assert.equal(await done, true);
  assert.equal(PC.model(), "openai-mini");
});

// P2's first usable row is NOT the chat default, so a boot that ignored
// initialModel (or the saved key) would land on a different name.
const P2 = [
  { name: "claude-cli", kind: "native", chat_ok: false, available: true },
  { name: "anthropic_sdk", kind: "anthropic", chat_ok: true, available: true, is_cloud: true },
  { name: "openai-mini", kind: "openai", chat_ok: true, available: true, is_cloud: true },
];

function flagOn(extra, stored, session) {
  return load(async (url) => {
    if (url === "/portal/api/config") return { chat_first: true };
    if (url === "/agent/api/providers") return { providers: P2, chat_default: "openai-mini" };
    if (url.startsWith("/agent/api/sessions/")) return session || {};
    return {};
  }, extra, stored);
}

test("boot restores the saved backend and model through their keys", async () => {
  const saved = flagOn({}, { "portal.chat.backend.v1": "assistant", "portal.chat.model.v1": "anthropic_sdk" });
  await saved.PC.init();
  assert.equal(saved.ctx.ACTIVE_BACKEND, "assistant");
  assert.equal(saved.PC.model(), "anthropic_sdk");
  const fresh = flagOn({}, {});
  await fresh.PC.init();
  assert.equal(fresh.ctx.ACTIVE_BACKEND, "chat");
  assert.equal(fresh.PC.model(), "openai-mini");   // the chat default, not the first usable row
  // The flag-off key is never read or written by chat-first.
  assert.ok(!fresh.writes.some(([k]) => k === "portal.backend.v1"));
});

test("the thread picker offers the session's stored kind, with dead rows disabled", async () => {
  const drawn = [];
  const agent = { _backend: "agent", _profile: "chat", _sid: "s1" };
  const { ctx, PC } = flagOn({
    EOS_UI: {
      esc: String, escAttr: String,
      modelRowHtml: (p, o) => { drawn.push([p.name, o]); return ""; },
      modal: () => {}, closeModal: () => {},
    },
  }, {}, { provider: "openai-mini", history_kind: "openai", messages: [{}] });
  await PC.init();
  ctx.currentAgent = agent;
  await PC.pickThreadModel(agent);
  assert.deepEqual(drawn.map((d) => d[0]), ["openai-mini"]);   // anthropic_sdk filtered out
  assert.equal(drawn[0][1].current, true);
  assert.equal(drawn[0][1].disableUnavailable, true);
});

test("a chat submitted during the provider probe still gets the chosen model", async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const started = [];
  const { PC } = load(async (url) => {
    if (url === "/portal/api/config") return { chat_first: true };
    if (url === "/agent/api/providers") { await gate; return { providers: P2, chat_default: "openai-mini" }; }
    return {};
  }, { _startAgentThread: (text, folder, opts) => started.push(opts) });
  const booted = PC.init();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  const sent = PC.start("hello");
  release();
  await booted; await sent;
  assert.equal(started[0].profile, "chat");
  assert.equal(started[0].provider, "openai-mini");   // not "" — the probe's answer was waited for
});

test("a pending project rides the chat it starts, then clears", async () => {
  const started = [];
  let cleared = 0;
  const { PC } = flagOn({
    _startAgentThread: async (text, folder, opts) => started.push(opts),
    PortalProjects: { pendingProject: () => "prj-7", clearPending: () => { cleared++; }, init: () => {} },
  }, {});
  await PC.init();
  await PC.start("hi");
  assert.equal(started[0].project_id, "prj-7");
  assert.equal(cleared, 1);
});

test("a pending project is dropped when it no longer exists, or when Chat is left", async () => {
  let cleared = 0, reloaded = 0;
  const stub = { pendingProject: () => "prj-gone", clearPending: () => { cleared++; },
                 load: () => { reloaded++; }, init: () => {} };
  const failing = flagOn({
    _startAgentThread: async () => { throw new Error("no such project"); },
    PortalProjects: stub,
  }, {});
  await failing.PC.init();
  await assert.rejects(failing.PC.start("hi"), /no such project/);
  assert.equal(cleared, 1);
  assert.equal(reloaded, 1);
  // Another failure (the network) keeps the claim: the user may retry.
  const flaky = flagOn({ _startAgentThread: async () => { throw new Error("offline"); }, PortalProjects: stub }, {});
  await flaky.PC.init();
  await assert.rejects(flaky.PC.start("hi"), /offline/);
  assert.equal(cleared, 1);
  failing.PC.selectBackend("rooms");
  assert.equal(cleared, 2);
  failing.PC.selectBackend("chat");
  assert.equal(cleared, 2);
});

test("a picker whose thread was left meanwhile never opens", async () => {
  let opened = 0;
  const agent = { _backend: "agent", _profile: "chat", _sid: "s1" };
  const { ctx, PC } = flagOn({
    EOS_UI: { esc: String, escAttr: String, modelRowHtml: () => "", modal: () => { opened++; } },
  }, {}, { provider: "openai-mini", history_kind: "" });
  await PC.init();
  ctx.currentAgent = { _backend: "agent", _sid: "other" };
  await PC.pickThreadModel(agent);
  assert.equal(opened, 0);
});
