/* renderMarkdown's note links must not let note text escape their onclick.
 *
 * The three rules that turn a mention into `onclick="EOS.viewNote('…')"` —
 * default wikilinks, full vault paths, bare `x.md` names — once put the path
 * through EOS.escPath alone. escPath makes a JS string (it escapes `'`), but a
 * `"` in the note text closed the ATTRIBUTE and the rest became live markup:
 * `/a"onmouseover="alert(1)"b.md` rendered a working handler on the daemon
 * origin. Found by the notebook adversarial review, 2026-09-28.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

// Mirror of eos.js EOS.escPath / normPath / fileName — the renderer's inputs.
const EOS = {
  vaultPath: "",
  normPath: (p) => String(p || "").replace(/\\/g, "/"),
  escPath(p) { return this.normPath(p).replace(/\\/g, "/").replace(/'/g, "\\'"); },
  fileName: (p) => String(p || "").split("/").pop().replace(/\.md$/, ""),
};
EOS.escPath = EOS.escPath.bind(EOS);

const EOS_UI = loadStatic("emptyos/web/static/eos-components.js", { EOS }).EOS_UI;

function onclickAttrs(html) {
  // Every onclick value exactly as an HTML parser would delimit it.
  return [...html.matchAll(/onclick="([^"]*)"/g)].map((m) => m[1]);
}

test("renderMarkdown is loaded", () => {
  assert.equal(typeof EOS_UI.renderMarkdown, "function");
  assert.ok(onclickAttrs(EOS_UI.renderMarkdown("see [[Plain]]")).length === 1,
    "precondition: a plain wikilink renders one onclick");
});

for (const [label, text] of [
  ["vault path", 'x /a"onmouseover="alert&#40;1&#41;"b.md y'],
  ["default wikilink", 'x [[a"onmouseover="alert(1)]] y'],
]) {
  test(`a ${label} with a double quote cannot add an attribute`, () => {
    const html = EOS_UI.renderMarkdown(text);
    assert.ok(onclickAttrs(html).length >= 1, `precondition: ${label} rendered a link: ${html}`);
    // Only the anchor's own attributes, never its text (which may legitimately
    // show the quote the author typed).
    for (const tag of html.match(/<a[^>]*>/g) || []) {
      // A browser starts a new attribute right after a closing quote, space or not.
      const names = [...tag.matchAll(/[\s"']([a-z-]+)=/g)].map((m) => m[1]);
      assert.deepEqual(names.filter((n) => !["href", "onclick", "class", "title"].includes(n)), [],
        `attribute breakout in ${tag}`);
    }
    for (const v of onclickAttrs(html)) {
      assert.ok(v.startsWith("EOS.viewNote('") && v.endsWith("');return false"),
        `onclick truncated or altered: ${v}`);
    }
  });
}

test("a single quote in a path still reaches viewNote intact", () => {
  const html = EOS_UI.renderMarkdown("see /notes/it's.md");
  const [v] = onclickAttrs(html);
  const decoded = v.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
  assert.equal(decoded, "EOS.viewNote('/notes/it\\'s.md');return false");
});

test("hashRoute survives a hash that is not valid percent-encoding", () => {
  const ctx = loadStatic("emptyos/web/static/eos-components.js", {
    EOS,
    location: { hash: "#50%", pathname: "/x/", search: "" },
  });
  const seen = [];
  const r = ctx.EOS_UI.hashRoute({ onShow: (id) => seen.push(id), onHide: () => seen.push(null) });
  assert.equal(r.current(), "50%");
  r.init();
  assert.deepEqual(Array.from(seen), ["50%"]);
});
