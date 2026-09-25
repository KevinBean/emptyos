/* Pins the pure pieces of Learn's lesson-video panel (pages/lesson-video.js):
 *
 * - every run status maps to a real shared badge variant — an unknown target
 *   renders a colourless badge that reviews fine and only shows on screen;
 * - every backend pipeline stage has a label, read from video.py's own
 *   VIDEO_STAGES rather than a hand copy;
 * - the Progress row never names a stopped run's last stage as if it were
 *   still running (a paused run once read "Writing the script").
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { loadStatic, REPO_ROOT } from "./shim.mjs";

const comps = loadStatic("emptyos/web/static/eos-components.js");
const page = loadStatic("apps/public/standard/learn/pages/lesson-video.js");
const LV = page.LessonVideo;

test("lesson-video.js loaded its pure surface", () => {
  assert.equal(typeof LV, "object");
  assert.equal(typeof LV._stageText, "function");
  assert.equal(typeof comps.EOS_UI.STATUS_VARIANTS, "object");
});

test("every status maps to a real badge variant", () => {
  const variants = new Set(Array.from(comps.EOS_UI.STATUS_VARIANTS));
  for (const [status, variant] of Object.entries({ ...LV._STATUS_MAP })) {
    assert.ok(variants.has(variant), `${status} → ${variant} is not in EOS_UI.STATUS_VARIANTS`);
  }
});

test("every backend pipeline stage has a label", () => {
  const src = fs.readFileSync(path.join(REPO_ROOT, "apps/public/standard/learn/video.py"), "utf8");
  const block = src.slice(src.indexOf("VIDEO_STAGES = ["));
  const stages = Array.from(block.slice(0, block.indexOf("]")).matchAll(/Stage\("([a-z_]+)"/g), (m) => m[1]);
  assert.deepEqual(stages, ["script", "narration", "visuals", "assemble", "review"]);
  for (const s of stages) assert.ok(LV._STAGE_LABELS[s], `no label for stage ${s}`);
});

test("an active run names its current stage", () => {
  assert.equal(LV._stageText("running", "narration"), "Narration");
  assert.equal(LV._stageText("queued", "queued"), "Queued behind earlier lessons");
});

test("a paused run reads as ready for review, not as still writing", () => {
  assert.equal(LV._stageText("paused", "script"), "Script ready — review it below");
});

test("a failed or interrupted run says where it stopped", () => {
  assert.equal(LV._stageText("error", "visuals"), "Stopped at: Slides and animations");
  assert.equal(LV._stageText("interrupted", "assemble"), "Stopped at: Assembly");
  assert.equal(LV._stageText("error", ""), "");
});
