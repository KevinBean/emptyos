/* dictionary — "Talk about it" pure helpers (pages/talk-about.js). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const W = loadStatic("apps/public/englishos/dictionary/pages/talk-about.js");

test("the surface under test actually loaded", () => {
  assert.equal(typeof W.talkAboutTimerLabel, "function");
  assert.equal(typeof W.talkAboutHints, "function");
});

test("the clock asks for more until the target, then lets go", () => {
  assert.equal(W.talkAboutTimerLabel(12500, 30), "0:12 · keep going to 0:30");
  assert.equal(W.talkAboutTimerLabel(29999, 30), "0:29 · keep going to 0:30");
  assert.equal(W.talkAboutTimerLabel(30000, 30), "0:30 · stop whenever you like");
  assert.equal(W.talkAboutTimerLabel(65000, 30), "1:05 · stop whenever you like");
});

test("hints never include the object itself and are capped", () => {
  const items = Array.from({ length: 20 }, (_, i) => ({ slug: "s" + i, name: "n" + i }));
  const hints = Array.from(W.talkAboutHints(items, "s3", 8, () => 0.3));
  assert.equal(hints.length, 8);
  assert.ok(!hints.includes("n3"));
  assert.equal(new Set(hints).size, 8);
  assert.deepEqual(items.map((i) => i.slug), Array.from({ length: 20 }, (_, i) => "s" + i),
    "the caller's list is not shuffled in place");
});

test("the clock defaults to the shared target", () => {
  assert.equal(W.talkAboutTimerLabel(5000), "0:05 · keep going to 0:30");
});

test("scene pack: the browsed pack when the object is in it, else its own first pack", () => {
  const sink = { packs: ["scene-kitchen", "scene-bathroom"], pack: "scene-kitchen" };
  assert.equal(W.talkAboutScenePack(sink, "scene-bathroom"), "scene-bathroom");
  assert.equal(W.talkAboutScenePack(sink, "animals"), "scene-kitchen");
  assert.equal(W.talkAboutScenePack(sink, ""), "scene-kitchen");
  assert.equal(W.talkAboutScenePack(null, "x"), "");
});

test("the Improv scene button renders only where Improv is loaded", () => {
  assert.equal(typeof W.improvSceneButton, "function");
  assert.equal(W.improvSceneButton("🎭 Improv scene", false), "");
  const html = W.improvSceneButton("🎭 Improv scene", true);
  assert.match(html, /onclick="improvSceneOpen\(\)"/);
  assert.match(html, /🎭 Improv scene/);
  // No EOS in the sandbox: the default must read as "not loaded", never throw.
  assert.equal(W.improvSceneButton("x"), "");
});

test("pictures.js builds every Improv button through the helper", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("../../apps/public/englishos/dictionary/pages/pictures.js", import.meta.url), "utf8");
  assert.equal((src.match(/improvSceneButton\(/g) || []).length, 2, "both scene buttons go through the helper");
  // Any direct call, whatever the quoting or a trailing `;`, would be a button
  // that bypasses the "is Improv loaded" check. The file may define the
  // handler; it may not name it anywhere else — only the helper does.
  const uses = src.replace(/function\s+improvSceneOpen\s*\(/, "");
  assert.ok(!/improvSceneOpen/.test(uses), "no unconditional Improv button");
});
