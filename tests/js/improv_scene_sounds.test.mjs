/* Improv — the "Sounds" section of a voice scene's end screen (pages/scene-sounds.js). */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { loadStatic, REPO_ROOT } from "./shim.mjs";

// scene-sounds.js escapes through EOS_UI.esc, so it shares a scope with
// eos-components.js exactly as the page loads them.
const ctx = loadStatic("emptyos/web/static/eos-components.js");
const P = "apps/extension/english-learning/improv/pages/scene-sounds.js";
vm.runInContext(fs.readFileSync(path.join(REPO_ROOT, P), "utf8"), ctx, { filename: P });

const TH = { phone: "TH", ipa: "θ", count: 2, heard: ["s"], words: ["think"] };

test("the surface under test actually loaded", () => {
  assert.equal(typeof ctx.sceneSoundsHtml, "function");
  assert.equal(typeof ctx.sceneSoundsRow, "function");
});

test("a missed sound names what was heard and where", () => {
  assert.equal(
    ctx.sceneSoundsRow({ phone: "TH", ipa: "θ", count: 2, heard: ["s", "t"], words: ["think", "three"] }),
    "/θ/ ×2 — heard as /s/ or /t/ · in think, three");
});

test("a dropped sound reads as left out", () => {
  assert.equal(ctx.sceneSoundsRow({ phone: "D", ipa: "d", count: 3, heard: ["—"], words: [] }),
    "/d/ ×3 — left out");
});

test("words and sounds are escaped", () => {
  const row = ctx.sceneSoundsRow({ phone: "X", ipa: "<b>", count: 2, heard: ["<i>"], words: ["<img>"] });
  assert.ok(!row.includes("<b>") && !row.includes("<i>") && !row.includes("<img>"), row);
});

test("the section counts lines, shows no grade, and calls itself a hint", () => {
  const html = ctx.sceneSoundsHtml({ lines: 4, accuracy: 0.815, weak: [TH], shared: true, soundcheck: true });
  assert.ok(html.includes("Sounds · 4 spoken lines<"), html);
  assert.ok(!html.includes("%"), "no percentage: it would read as a grade");
  assert.ok(html.includes("A hint, not a verdict"));
  const clean = ctx.sceneSoundsHtml({ lines: 1, accuracy: 1, weak: [] });
  assert.ok(clean.includes("Sounds · 1 spoken line<") &&
    clean.includes("No sound was missed often enough to look like a habit"));
});

test("Sound Check is named only when the sounds really reached it", () => {
  const to = (shared, soundcheck) =>
    ctx.sceneSoundsHtml({ lines: 2, weak: [TH], shared, soundcheck });
  assert.ok(to(true, true).includes('href="/soundcheck/"'));
  assert.ok(!to(true, false).includes("soundcheck") && to(true, false).includes("pronunciation history"));
  assert.ok(!to(false, true).includes("Recorded"), "not kept: no claim that it was");
});

test("no scored lines means no section", () => {
  assert.equal(ctx.sceneSoundsHtml(null), "");
  assert.equal(ctx.sceneSoundsHtml({ lines: 0 }), "");
});
