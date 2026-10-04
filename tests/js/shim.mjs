/* Minimal browser shim for running EmptyOS's shared page JS under `node --test`.
 *
 * WHY THIS EXISTS, AND WHAT IT DELIBERATELY IS NOT
 * ------------------------------------------------
 * `emptyos/web/static/*.js` ships as browser globals (`var EOS_UI = {...}`),
 * not modules, and a lot of genuinely pure logic lives in there — escaping,
 * ability comparison, status-variant mapping, the state store. None of it was
 * testable, because CI has no Node step and never installs Playwright browsers
 * (see .github/workflows/tests.yml: interactive tests are excluded outright),
 * so a browser-driven harness would gate nothing.
 *
 * This shim exists ONLY so those files can be *loaded* without a DOM. It is
 * not a jsdom and must never grow into one. The rule:
 *
 *     Tests built on this may exercise PURE functions only.
 *
 * If a test needs real layout, real events, or real rendering, it does not
 * belong here — that is what the `/eos-ui-walk` browser pass is for. Adding
 * DOM surface to this file to make a rendering test pass is how a small shim
 * turns into a bad, silently-wrong jsdom. Add only what a file needs at LOAD
 * time, and only to reach the pure surface underneath.
 *
 * Everything returned by the element factory is inert on purpose: a test that
 * accidentally asserts on rendered DOM gets an obviously empty answer rather
 * than a plausible-looking wrong one.
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

export const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);

const noop = () => {};

/* Timers that never hold the process open.
 *
 * Shared page JS registers polling intervals at load time (theme watchers,
 * health pings). Under a browser that is fine; under `node --test` a single
 * live interval keeps the event loop alive and the runner hangs forever with
 * no failing test to point at — which reads as an infrastructure bug rather
 * than the load-time side effect it is. Unref'd handles still fire for a test
 * that awaits them, but stop counting toward "is this process done". */
function unrefTimer(fn) {
  return (...args) => {
    const handle = fn(...args);
    if (handle && typeof handle.unref === "function") handle.unref();
    return handle;
  };
}

/* HTML text-node serialization — the ONE piece of real DOM behaviour this
 * shim implements, because `EOS_UI.esc` is
 *
 *     var d = document.createElement('div'); d.textContent = s; return d.innerHTML;
 *
 * and a large share of the bundle renders through it. With an inert element
 * `esc` returns "" and every escaping assertion passes for the wrong reason —
 * a `warningBanner` test "proved" markup was escaped when the message was
 * simply gone.
 *
 * Escapes & < > and LEAVES QUOTES ALONE. That is not a guess: measured in
 * Chrome 2026-09-01, `esc('<a>&"\'')` returns `&lt;a&gt;&amp;"'`. Do not
 * "harden" this to escape quotes — quote handling is `escAttr`'s job, and
 * making them differ here would hide the very confusion between the two that
 * .claude/rules/shared-frontend.md warns about. Order matters: & first.
 */
function escapeTextNode(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** An inert element, save for the text/HTML pair above. Every other accessor
 *  answers "nothing here", never a plausible fake. */
function makeEl() {
  let text = "";
  let html = "";
  const el = {
    style: {},
    dataset: {},
    value: "",
    checked: false,
    classList: {
      add: noop,
      remove: noop,
      toggle: noop,
      contains: () => false,
    },
    children: [],
    setAttribute: noop,
    removeAttribute: noop,
    getAttribute: () => null,
    hasAttribute: () => false,
    appendChild: noop,
    insertBefore: noop,
    prepend: noop,
    append: noop,
    removeChild: noop,
    remove: noop,
    addEventListener: noop,
    removeEventListener: noop,
    dispatchEvent: () => true,
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    focus: noop,
    blur: noop,
    click: noop,
    scrollIntoView: noop,
    getBoundingClientRect: () => ({
      top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0,
    }),
  };
  Object.defineProperty(el, "textContent", {
    get: () => text,
    set: (v) => { text = v == null ? "" : String(v); html = escapeTextNode(text); },
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => html,
    set: (v) => { html = v == null ? "" : String(v); },
  });
  return el;
}

/** Build a fresh sandbox. Never shared between tests — load-time code in the
 *  file under test may stash state, and a shared context would leak it. */
export function makeWindow(extra = {}) {
  const body = makeEl();
  const ctx = {
    console,
    fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
    setTimeout: unrefTimer(setTimeout),
    clearTimeout,
    setInterval: unrefTimer(setInterval),
    clearInterval,
    queueMicrotask,
    addEventListener: noop,
    removeEventListener: noop,
    dispatchEvent: () => true,
    Promise,
    Date,
    Math,
    JSON,
    URL,
    // The other half of the WHATWG URL pair. Absent, a page's own
    // `new URLSearchParams(location.search)` throws — or worse, is swallowed by
    // a defensive catch and every query-string test quietly answers "no".
    URLSearchParams,
    Response: globalThis.Response,
    navigator: { userAgent: "node", language: "en" },
    location: { href: "http://localhost/", pathname: "/", search: "", hash: "" },
    history: { replaceState: noop, pushState: noop },
    matchMedia: () => ({ matches: false, addEventListener: noop }),
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    document: {
      readyState: "complete",
      body,
      head: makeEl(),
      documentElement: makeEl(),
      createElement: makeEl,
      createTextNode: () => makeEl(),
      addEventListener: noop,
      removeEventListener: noop,
      querySelector: () => null,
      querySelectorAll: () => [],
      getElementById: () => null,
      getAnimations: () => [],
    },
    ...extra,
  };
  ctx.window = ctx;
  ctx.self = ctx;
  ctx.globalThis = ctx;
  return ctx;
}

/**
 * Evaluate a repo-relative browser script into a fresh sandbox and return it.
 *
 * Throws with the offending line when the file needs a global the shim does
 * not provide — that failure is informative and must NOT be swallowed: a
 * caught load error would hand every test an empty `EOS_UI` and they would all
 * pass while testing nothing.
 */
export function loadStatic(relPath, extra = {}) {
  // An array loads several scripts into ONE sandbox, in order — a page whose
  // script is split into siblings that share its global scope.
  const paths = Array.isArray(relPath) ? relPath : [relPath];
  const ctx = makeWindow(extra);
  vm.createContext(ctx);
  for (const rel of paths) {
    const src = fs.readFileSync(path.join(REPO_ROOT, rel), "utf8");
    vm.runInContext(src, ctx, { filename: rel });
  }
  return ctx;
}
