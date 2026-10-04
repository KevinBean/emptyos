/* EOS.quickHost — how a page in a small transient window reaches its host.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Two hosts put an EmptyOS page in a window that is not a tab: the desktop
 * shell's frameless always-on-top panel (a bridge, and the window is PRELOADED
 * so it hides rather than closes) and plugins/command-launcher's borderless
 * Chrome window (no bridge, transient, closes). Two consumers need to tell them
 * apart — `eos-keys.js`'s launcher mode and `portal-quick.js` — which is why the
 * probe lives in the shared bundle instead of twice.
 *
 * Each case here is a way the answer has been, or could be, silently wrong:
 * reading only the older `?launcher=1` spelling (which left the shell's window
 * running the ordinary palette); accepting `window.pywebview` as proof of a
 * quick window (the shell's MAIN window has a bridge too, without these verbs);
 * and calling `window.close()` on a preloaded window, which destroys the thing
 * the hotkey exists to show again.
 *
 * Pure/load-time surface only — see shim.mjs.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

function load(extra = {}) {
  return loadStatic("emptyos/web/static/eos.js", {
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    // eos.js schedules DOM auto-mount on timers; shim.mjs models no rendering,
    // so those callbacks would fire after the test and fail the FILE.
    setTimeout: () => 0,
    setInterval: () => 0,
    ...extra,
  });
}

const AT = (search) => load({ location: { href: "http://localhost/", pathname: "/portal/", search, hash: "" } });

test("loaded", () => {
  const { EOS } = AT("?quick=1");
  assert.equal(typeof EOS.quickHost, "object");
  assert.equal(typeof EOS.quickHost.active, "function");
});

test("both spellings mean a quick window", () => {
  assert.equal(AT("?quick=1").EOS.quickHost.active(), true);
  assert.equal(AT("?launcher=1").EOS.quickHost.active(), true);
  assert.equal(AT("?a=b&launcher=1").EOS.quickHost.active(), true);
});

test("an ordinary tab is not one", () => {
  for (const s of ["", "?", "?quick=0", "?quick=yes", "?launcher=", "?quickly=1"])
    assert.equal(AT(s).EOS.quickHost.active(), false, `search ${JSON.stringify(s)}`);
});

test("the bridge is recognised by its verbs, never by pywebview being present", () => {
  // The shell's MAIN window also exposes window.pywebview.api — with
  // shell_info/start_daemon and none of the quick verbs. Treating that as a
  // quick bridge would send `hide` to the window the user is working in.
  const main = AT("?quick=1");
  main.window.pywebview = { api: { shell_info: () => ({}), start_daemon: () => ({}) } };
  assert.equal(main.EOS.quickHost.bridge(), null);

  const quick = AT("?quick=1");
  const api = { hide: () => {}, open_main: () => {}, set_quick_height: () => {} };
  quick.window.pywebview = { api };
  assert.equal(quick.EOS.quickHost.bridge(), api);
});

test("no pywebview at all is a browser window, not an error", () => {
  assert.equal(AT("?quick=1").EOS.quickHost.bridge(), null);
});

test("dismiss HIDES through the bridge and never closes a preloaded window", () => {
  const ctx = AT("?quick=1");
  const seen = [];
  ctx.window.close = () => seen.push("close");
  ctx.window.pywebview = { api: { hide: () => seen.push("hide") } };
  ctx.EOS.quickHost.dismiss();
  assert.deepEqual([...seen], ["hide"]);
});

test("dismiss closes when there is no bridge to hide through", () => {
  const ctx = AT("?launcher=1");
  const seen = [];
  ctx.window.close = () => seen.push("close");
  ctx.EOS.quickHost.dismiss();
  assert.deepEqual([...seen], ["close"]);
});

test("a bridge that throws still puts the window away", () => {
  // A dead bridge (the shell quit, the window did not) must not strand a
  // borderless window the user has no title bar to close.
  const ctx = AT("?quick=1");
  const seen = [];
  ctx.window.close = () => seen.push("close");
  ctx.window.pywebview = { api: { hide: () => { throw new Error("gone"); } } };
  ctx.EOS.quickHost.dismiss();
  assert.deepEqual([...seen], ["close"]);
});

test("openMain hands the path to the shell and does not also open a browser", () => {
  const ctx = AT("?quick=1");
  const seen = [];
  ctx.window.open = (u) => seen.push(["open", u]);
  ctx.window.close = () => seen.push(["close"]);
  ctx.window.pywebview = { api: { hide: () => seen.push(["hide"]), open_main: (p) => seen.push(["main", p]) } };
  ctx.EOS.quickHost.openMain("/journal/");
  assert.deepEqual(seen.map((r) => [...r]), [["main", "/journal/"]]);
});

test("openMain without a bridge opens the real browser and then dismisses", () => {
  const ctx = AT("?launcher=1");
  const seen = [];
  ctx.window.open = (u, t, f) => seen.push(["open", u, t, f]);
  ctx.window.close = () => seen.push(["close"]);
  ctx.EOS.quickHost.openMain("/journal/");
  assert.deepEqual(seen.map((r) => [...r]),
    [["open", "/journal/", "_blank", "noopener"], ["close"]]);
});
