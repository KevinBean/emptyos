/* portal-quick.js — the pure half of quick entry.
 *
 * What a browser walk cannot see going wrong, and what each case exists for:
 *
 *  - `?launcher=1` is an ALIAS, not a second mode. command-launcher has shipped
 *    that spelling for longer than the shell has existed, so a user's config
 *    still says it; if the alias lapses the window opens as the FULL portal in a
 *    frameless always-on-top box with no title bar to close it.
 *  - the height band matches the shell's, in both directions.
 *  - `quick=1` does not travel to the main window (it would open the main
 *    window stripped to a composer — the one thing the button exists to avoid).
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const { PortalQuick: Q } = loadStatic("apps/public/standard/portal/pages/portal-quick.js");

/* Load with a location and capture where openInMain sends the user.
 *
 * Two hosts, and both are covered on purpose: `host: true` supplies the shared
 * EOS.quickHost (what actually runs in the product), `host: false` removes it
 * (eos.js absent or failed to load). Without the first, every assertion here
 * would be about the fallback and the real delegation would be unpinned. */
function withLocation(loc, { host = true } = {}) {
  const sent = [];
  const extra = {
    location: { href: "http://localhost/", ...loc },
    open: (url) => { sent.push(["window.open", url]); return null; },
    // Recorded, not swallowed: the eos.js-absent fallbacks in BOTH dismiss and
    // openInMain end in window.close(), and a no-op stub leaves the only thing
    // that closes a title-bar-less window unpinned.
    close: () => { sent.push(["window.close"]); },
  };
  if (host) {
    extra.EOS = {
      quickHost: {
        active: () => true,
        bridge: () => null,
        dismiss: () => sent.push(["dismiss"]),
        openMain: (p) => sent.push(["openMain", p]),
      },
    };
  }
  const ctx = loadStatic("apps/public/standard/portal/pages/portal-quick.js", extra);
  return { Q: ctx.PortalQuick, sent };
}

test("loaded", () => {
  assert.equal(typeof Q, "object");
  assert.equal(typeof Q.isQuickSearch, "function");
});

test("?quick=1 and ?launcher=1 both mean quick mode", () => {
  assert.equal(Q.isQuickSearch("?quick=1"), true);
  assert.equal(Q.isQuickSearch("?launcher=1"), true);
  assert.equal(Q.isQuickSearch("?a=b&launcher=1&c=d"), true);
});

test("nothing else does", () => {
  for (const s of ["", "?", "?quick=0", "?quick=true", "?launcher=", "?quickly=1", null, undefined])
    assert.equal(Q.isQuickSearch(s), false, `search ${JSON.stringify(s)}`);
});

test("height is clamped to the band the shell enforces", () => {
  assert.equal(Q.MIN_HEIGHT, 120);
  assert.equal(Q.MAX_HEIGHT, 720);
  assert.equal(Q.START_HEIGHT, 250);
  assert.equal(Q.heightFor(0), 120);
  assert.equal(Q.heightFor(119), 120);
  assert.equal(Q.heightFor(300), 300);
  assert.equal(Q.heightFor(721), 720);
  assert.equal(Q.heightFor(99999), 720);
});

test("an unmeasurable height falls back to the OPENING size, never NaN and never the floor", () => {
  // A resize called with NaN is worse than one that does not happen: the window
  // would be handed a size no comparison can reject. And the floor is the wrong
  // fallback — a failed measurement would collapse the panel to a sliver.
  for (const v of [NaN, Infinity, -Infinity, "tall", undefined, null, {}])
    assert.equal(Q.heightFor(v), 250, `height ${String(v)}`);
});

test("the main window is opened WITHOUT quick=1", () => {
  // The query is dropped wholesale and the hash kept, because the thread lives
  // in the hash and the mode lives in the query.
  assert.equal(Q.mainPath("/portal/", "#t/abc"), "/portal/#t/abc");
  assert.equal(Q.mainPath("/portal/", ""), "/portal/");
});

test("a path that is not same-origin-absolute falls back to /portal/", () => {
  // `/\host` and a control character are the two refusals mainPath's own
  // comment calls a deliberate pair with `//host`. Chromium normalises the
  // backslash out of location.pathname today, so neither is reachable in a
  // browser — which is exactly why only a test can keep them from being
  // deleted as dead code, and why omitting them left half the pair unpinned.
  for (const p of ["//evil.example/x", "/\\evil.example/x", "/portal/\u0000x",
                   "/portal/\u007f", "https://evil.example", "", null, "portal/"])
    assert.equal(Q.mainPath(p, "#t/1"), "/portal/#t/1", `path ${JSON.stringify(p)}`);
});

test("a hash without its # is dropped rather than pasted on", () => {
  assert.equal(Q.mainPath("/portal/", "t/abc"), "/portal/");
});

test("openInMain reads the pathname and hash, and NOT the query", () => {
  // The wiring, not the helper: mainPath cannot refuse a query it is never
  // shown, so the guarantee that `quick=1` does not travel lives at the call
  // site. Without this, opening in the main window would open THAT window
  // stripped to a composer — the one outcome the button exists to avoid.
  const { Q: q, sent } = withLocation({ pathname: "/portal/", search: "?quick=1", hash: "#t/abc" });
  q.openInMain();
  assert.deepEqual([...sent], [["openMain", "/portal/#t/abc"]]);
});

test("openInMain falls back to the portal when the pathname is foreign", () => {
  const { Q: q, sent } = withLocation({ pathname: "//evil.example/x", search: "", hash: "#t/1" });
  q.openInMain();
  assert.deepEqual([...sent], [["openMain", "/portal/#t/1"]]);
});

test("the host verbs are delegated to EOS.quickHost, not re-implemented here", () => {
  // Two consumers now share that probe (this module and eos-keys.js), so it
  // lives in eos.js. If this module quietly grew its own copy back, the two
  // would drift and only one of them would learn about a new host.
  const { Q: q, sent } = withLocation({ pathname: "/portal/", search: "?quick=1", hash: "" });
  q.dismiss();
  assert.deepEqual([...sent], [["dismiss"]]);
});

test("with eos.js absent the window is still dismissable and still opens main", () => {
  const { Q: q, sent } = withLocation(
    { pathname: "/portal/", search: "?quick=1", hash: "#t/9" }, { host: false });
  q.openInMain();
  // openInMain's fallback hands the path over AND steps aside, the same pair
  // the host's openMain does for us when eos.js is present.
  assert.deepEqual([...sent], [["window.open", "/portal/#t/9"], ["window.close"]]);
  sent.length = 0;
  // The "dismissable" half of this test's own name — previously never called,
  // so the only route out of a frameless window with no eos.js was unpinned.
  q.dismiss();
  assert.deepEqual([...sent], [["window.close"]]);
});

test("with eos.js present dismiss delegates to the host rather than closing", () => {
  const { Q: q, sent } = withLocation({ pathname: "/portal/", search: "?quick=1" });
  q.dismiss();
  // The shell HIDES a preloaded window; closing it would destroy the thing the
  // next hotkey press is supposed to show instantly.
  assert.deepEqual([...sent], [["dismiss"]]);
});

test("resize is debounced by size, so a streaming answer is not a resize per frame", () => {
  assert.equal(Q.worthResizing(200, 200), false);
  assert.equal(Q.worthResizing(207, 200), false);
  assert.equal(Q.worthResizing(208, 200), true);
  assert.equal(Q.worthResizing(120, 0), true);   // first measurement always lands
});
