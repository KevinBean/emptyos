/* Pins the true-scale 3D path of a Cable Pulling+ route.
 *
 * Expected positions are derived by hand from circle geometry, never read
 * back from the module: a 90° arc of radius R advances R along the old
 * heading and R across it; a 90° vertical entry climbs R.
 *
 * `cable-pulling` is a held engineering app (apps/extension/, dropped from
 * public snapshots), so every case skips where that tree is absent. See
 * tests/test_unit_js_suite.py.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { loadStatic, REPO_ROOT } from "./shim.mjs";

const REL = "apps/extension/engineering/cable-pulling/pages/pulling-geom3d.js";
const PRESENT = fs.existsSync(path.join(REPO_ROOT, REL));
const G = PRESENT ? loadStatic(REL).PP_ROUTE3D : null;
const skip = { skip: !PRESENT && "held app absent (public snapshot)" };

const end = (p) => Array.from(p.points[p.points.length - 1]);
const near = (a, b, tol = 1e-6) =>
  a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < tol, `axis ${i}: ${v} vs ${b[i]}`));

test("the module under test is actually loaded", skip, () => {
  assert.equal(typeof G.buildPath, "function");
});

test("a straight runs along the initial heading", skip, () => {
  const p = G.buildPath([{ type: "straight", length: 100 }]);
  near(end(p), [100, 0, 0]);
  assert.equal(p.length, 100);
});

test("a 90° right bend ends R ahead and R across, on its circle", skip, () => {
  const p = G.buildPath([{ type: "straight", length: 100 },
    { type: "horizontal_bend", angle: 90, radius: 10, direction: "right" }]);
  near(end(p), [110, 0, 10]);
  // arc length is Rθ = 5π, measured along exact chords (≤ chord shortfall)
  assert.ok(Math.abs(p.length - (100 + 5 * Math.PI)) < 0.05);
});

test("left and right bends mirror across the heading", skip, () => {
  const L = G.buildPath([{ type: "horizontal_bend", angle: 90, radius: 10, direction: "left" }]);
  near(end(L), [10, 0, -10]);
});

test("a vertical up entry climbs, and its exit levels out at the right height", skip, () => {
  // entry 30° at R=10: rise R(1-cos30), run R sin30. Slope 20 m at 30°.
  // exit 30° mirrors the entry. Total rise = 2·R(1-cos30) + 20 sin30.
  const c = Math.cos(Math.PI / 6), s = Math.sin(Math.PI / 6);
  const p = G.buildPath([
    { type: "vertical_up_entry", angle: 30, radius: 10 },
    { type: "upward_slope", angle: 30, length: 20 },
    { type: "vertical_up_exit", angle: 30, radius: 10 },
    { type: "straight", length: 5 },
  ]);
  const rise = 2 * 10 * (1 - c) + 20 * s;
  const run = 2 * 10 * s + 20 * c + 5;
  near(end(p), [run, rise, 0]);
});

test("a downward slope descends at its angle", skip, () => {
  // 20 m at 30°: run 20·cos30, drop 20·sin30 = 10. At 90° there would be no
  // run at all, which is why the angle is not 90 here.
  const p = G.buildPath([{ type: "downward_slope", angle: 30, length: 20 }]);
  near(end(p), [20 * Math.cos(Math.PI / 6), -10, 0]);
});

test("a vertical down entry and exit mirror the up pair", skip, () => {
  // entry 90° at R=4: run 4, drop 4, now pointing down; exit back to level:
  // run 4, drop 4. End (8, -8, 0).
  const p = G.buildPath([
    { type: "vertical_down_entry", angle: 90, radius: 4 },
    { type: "vertical_down_exit", angle: 90, radius: 4 },
  ]);
  near(end(p), [8, -8, 0]);
});

test("a full vertical-down dips and levels out", skip, () => {
  const p = G.buildPath([{ type: "vertical_down", angle: 90, radius: 3 },
    { type: "straight", length: 1 }]);
  near(end(p), [7, -6, 0]);
});

test("a bend made while pitched keeps its pitch", skip, () => {
  // Climb to 90° (vertical), then a 90° 'horizontal' bend at R=5. With pitch
  // at 90° every chord points straight up, so the bend adds the SUM of its
  // chords — ARC_STEPS × 2R·sin(θ/2/ARC_STEPS) — as height, and no plan
  // movement. (A dropped pitch would move it in plan instead.)
  const p = G.buildPath([
    { type: "vertical_up_entry", angle: 90, radius: 2 },
    { type: "horizontal_bend", angle: 90, radius: 5, direction: "right" },
  ]);
  const e = end(p);
  const chords = G.ARC_STEPS * 2 * 5 * Math.sin(Math.PI / 2 / G.ARC_STEPS / 2);
  assert.ok(Math.abs(e[0] - 2) < 1e-6 && Math.abs(e[2]) < 1e-6);
  assert.ok(Math.abs(e[1] - (2 + chords)) < 1e-6);
});

test("a full vertical-up is an entry then an exit — back to horizontal", skip, () => {
  // 90° at R=3: the entry runs R sin90 = 3 and climbs R(1-cos90) = 3 to
  // vertical; the exit runs R(1-cos90) = 3 and climbs R sin90 = 3 back to
  // level; the straight adds 1. End = (7, 6, 0).
  const p = G.buildPath([{ type: "vertical_up", angle: 90, radius: 3 },
    { type: "straight", length: 1 }]);
  near(end(p), [7, 6, 0]);
  // …and the following straight is horizontal: last two points share y.
  const pts = p.points;
  assert.ok(Math.abs(pts[pts.length - 1][1] - pts[pts.length - 2][1]) < 1e-9);
});

test("a pit with no length still occupies space", skip, () => {
  const p = G.buildPath([{ type: "pit_section" }]);
  near(end(p), [2, 0, 0]);
});

test("section starts index into the path", skip, () => {
  const p = G.buildPath([{ type: "straight", length: 10 }, { type: "straight", length: 5 }]);
  near(Array.from(p.points[p.sectionStart[2]]), [10, 0, 0]);
});

test("tension interpolates along a section from its own start", skip, () => {
  const p = G.buildPath([{ type: "horizontal_bend", angle: 90, radius: 10, direction: "right" }]);
  const v = G.sectionValues({ results: [{ section: 1, entryTension: 100, exitTension: 200 }] }, "tension");
  assert.equal(G.valueAtPoint(p, 0, v), 100);
  assert.equal(G.valueAtPoint(p, p.points.length - 1, v), 200);
  // point 0 is the section's start; arc point k sits at fraction k/ARC_STEPS
  const mid = G.valueAtPoint(p, G.ARC_STEPS / 2, v);
  assert.ok(Math.abs(mid - 150) < 1e-9);
});

test("each section opens with its own start point, so boundaries jump, not smear", skip, () => {
  // SWP 900 on the bend, 0 on the straight after it: the straight's first
  // point must read 0, not a blend down from 900.
  const p = G.buildPath([{ type: "horizontal_bend", angle: 90, radius: 10, direction: "right" },
    { type: "straight", length: 50 }]);
  const v = G.sectionValues({ results: [
    { section: 1, entryTension: 0, exitTension: 10, sidewallPressure: 900 },
    { section: 2, entryTension: 10, exitTension: 20, sidewallPressure: 0 }] }, "swp");
  const s2 = p.sectionStart[2];
  near(Array.from(p.points[s2]), Array.from(p.points[s2 - 1]));   // same place…
  assert.equal(G.valueAtPoint(p, s2 - 1, v), 900);                  // …bend's end
  assert.equal(G.valueAtPoint(p, s2, v), 0);                        // straight's start
});

test("segmented results are read from every segment; a pit has no value", skip, () => {
  const p = G.buildPath([{ type: "straight", length: 10 }, { type: "pit_section", length: 2 },
    { type: "straight", length: 10 }]);
  const v = G.sectionValues({ segments: [
    { section_results: [{ section: 1, entryTension: 0, exitTension: 50 }] },
    { section_results: [{ section: 3, entryTension: 5, exitTension: 70 }] }] }, "tension");
  assert.equal(G.valueAtPoint(p, p.sectionStart[2] - 1, v), 50);
  assert.equal(G.valueAtPoint(p, p.sectionStart[2], v), null);      // the pit
  assert.equal(G.valueAtPoint(p, p.sectionStart[3], v), 5);         // restarts after it
  assert.equal(G.valueAtPoint(p, p.points.length - 1, v), 70);
});

test("Reverse puts each value where it physically occurs", skip, () => {
  // Pulled from the far end, section 1 is entered at its user-order END.
  // The engine returns entry=14418 (at user end), exit=17418 (at user start).
  const p = G.buildPath([{ type: "straight", length: 100 }]);
  const v = G.sectionValues({ reverse: true,
    results: [{ section: 1, entryTension: 14418, exitTension: 17418 }] }, "tension");
  assert.equal(G.valueAtPoint(p, 0, v), 17418);
  assert.equal(G.valueAtPoint(p, p.points.length - 1, v), 14418);
});

test("a full vertical peaks at the engine's intermediate tension", skip, () => {
  const p = G.buildPath([{ type: "vertical_up", angle: 90, radius: 3 }]);
  const v = G.sectionValues({ results: [
    { section: 1, entryTension: 100, exitTension: 200, T2_intermediate: 500 }] }, "tension");
  // the entry arc ends at fraction 0.5 — that point carries T2_intermediate
  assert.equal(G.valueAtPoint(p, G.ARC_STEPS, v), 500);
});

test("SWP is flat across a section", skip, () => {
  const v = G.sectionValues({ results: [{ section: 1, entryTension: 1, exitTension: 9, sidewallPressure: 400 }] }, "swp");
  assert.deepEqual(Array.from(v[1]).slice(0, 2), [400, 400]);
});

test("an absurd length cannot build an absurd path", skip, () => {
  // 1e9 m (a unit slip) would be 2e8 points at 5 m; the cap holds it.
  const p = G.buildPath([{ type: "straight", length: 1e9 }]);
  assert.equal(p.points.length, G.MAX_PIECES + 1);
  near(end(p), [1e9, 0, 0], 1e-3);                 // …and still ends in the right place
});

test("long straights are split so hover can find their middle", skip, () => {
  const p = G.buildPath([{ type: "straight", length: 100 }]);
  // 100 m at ≤ MAX_STEP_M per piece, plus the section's start point
  assert.equal(p.points.length, 100 / G.MAX_STEP_M + 1);
  for (let i = 1; i < p.points.length; i++) {
    assert.ok(p.points[i][0] - p.points[i - 1][0] <= G.MAX_STEP_M + 1e-9);
  }
});

test("auto exaggeration lifts shallow relief to ~35 % of plan, within 1..20", skip, () => {
  // 300 m long, 10 m deep → 0.35·300/10 = 10.5
  assert.equal(G.autoExaggeration({ min: [0, -10, 0], max: [300, 0, 0] }), 10.5);
  // flat route → 1; very flat → capped at 20; steep → never below 1
  assert.equal(G.autoExaggeration({ min: [0, 0, 0], max: [600, 0, 0] }), 1);
  assert.equal(G.autoExaggeration({ min: [0, -0.1, 0], max: [600, 0, 0] }), 20);
  assert.equal(G.autoExaggeration({ min: [0, -300, 0], max: [100, 0, 0] }), 1);
  // plan extent is the larger of x and z: 100 × 300 in plan, 10 deep → 10.5
  assert.equal(G.autoExaggeration({ min: [0, -10, 0], max: [100, 0, 300] }), 10.5);
});

/* ── 2D route sheet helpers ─────────────────────────────────────────── */

test("chainage runs along the conduit and does not advance through a pit", skip, () => {
  const secs = [{ type: "straight", length: 150 },
    { type: "pit_section", length: 2 },
    { type: "straight", length: 100 }];
  const p = G.buildPath(secs);
  const c = G.chainage(p, secs);
  // The engine gives a pit no conduit length, so the far end is 250 m, not
  // 252 m, and the pit sits at 150 m ("Pit at 150 m from the route start").
  assert.ok(Math.abs(c[c.length - 1] - 250) < 1e-9, `end ${c[c.length - 1]}`);
  const pitIdx = p.sectionStart[2];
  assert.ok(Math.abs(c[pitIdx] - 150) < 1e-9);
  assert.ok(Math.abs(c[p.sectionStart[3]] - 150) < 1e-9);
});

test("chainage along an arc is its arc length, R·θ", skip, () => {
  const secs = [{ type: "horizontal_bend", angle: 90, radius: 10, direction: "right" }];
  const c = G.chainage(G.buildPath(secs, { arcSteps: 200 }), secs);
  // 200 chords of a quarter circle: 2·R·sin(θ/400)·200 → R·π/2 = 15.708 m.
  assert.ok(Math.abs(c[c.length - 1] - 10 * Math.PI / 2) < 1e-3);
});

test("nice steps are 1, 2 or 5 × 10^k", skip, () => {
  assert.equal(G.niceStep(640, 7), 100);   // 91.4 → 100
  assert.equal(G.niceStep(640, 4), 200);   // 160 → 200
  assert.equal(G.niceStep(23, 4), 10);     // 5.75 → 10
  assert.equal(G.niceStep(9, 4), 5);       // 2.25 → 5
  assert.equal(G.niceStep(3, 2), 2);       // 1.5 → 2
  assert.equal(G.niceStep(0, 4), 1);
});

test("profile exaggeration: the largest standard factor that fits", skip, () => {
  // 20 m relief at 1 px/m in a 170 px plot, 70 % fill = 119 px:
  // x5 = 100 px fits, x10 = 200 px does not.
  assert.equal(G.profileExaggeration(20, 1, 170, 0.7), 5);
  // 12 m: x10 = 120 px — over the 119 px fill, though under the 170 px plot.
  assert.equal(G.profileExaggeration(12, 1, 170, 0.7), 5);
  assert.equal(G.profileExaggeration(0, 1, 170, 0.7), 1);       // level route
  // 500 m of rise at 1 px/m: x0.2 = 100 px fits, x0.5 = 250 px does not — a
  // steep route is compressed rather than drawn off the sheet.
  assert.equal(G.profileExaggeration(500, 1, 170, 0.7), 0.2);
  assert.equal(G.profileExaggeration(0.5, 1, 170, 0.7), 100);   // capped at x100
});

test("labels: a clash drops the lower priority; ties keep input order", skip, () => {
  const a = { x: 0, y: 0, w: 20, h: 10, priority: 1 };
  const b = { x: 10, y: 0, w: 20, h: 10, priority: 5 };   // overlaps a
  const c = { x: 60, y: 0, w: 20, h: 10, priority: 0 };   // clear of both
  assert.deepEqual(Array.from(G.placeLabels([a, b, c], 2)), [false, true, true]);
  const d = { x: 0, y: 0, w: 20, h: 10 }, e = { x: 5, y: 0, w: 20, h: 10 };
  assert.deepEqual(Array.from(G.placeLabels([d, e], 0)), [true, false]);
  // Padding counts: 21 px apart with 2 px pad clash; 23 px apart do not.
  const f = { x: 0, y: 0, w: 20, h: 10 };
  assert.deepEqual(Array.from(G.placeLabels([f, { x: 21, y: 0, w: 5, h: 5 }], 2)), [true, false]);
  assert.deepEqual(Array.from(G.placeLabels([f, { x: 23, y: 0, w: 5, h: 5 }], 2)), [true, true]);
});

test("plan fit: uniform scale, aspect height clamped, drawing centred", skip, () => {
  // 400 m x 100 m route into 460 px with 30 px pad: scale 1 px/m, height
  // 100 + 60 = 160 px (inside 150..420), centred with 30 px either side.
  const f = G.planFit({ min: [0, 0, 0], max: [400, 0, 100] }, 460, 150, 420, 30);
  assert.equal(f.scale, 1);
  assert.equal(f.height, 160);
  assert.equal(f.offX, 30);
  assert.equal(f.offY, 30);
  // Tall route: height capped at 420, scale shrinks so it still fits.
  const t = G.planFit({ min: [0, 0, 0], max: [100, 0, 1000] }, 460, 150, 420, 30);
  assert.equal(t.height, 420);
  assert.ok(Math.abs(t.scale - 360 / 1000) < 1e-12);
  assert.ok(Math.abs(t.offX - (460 - 100 * t.scale) / 2) < 1e-9);
});

test("chainage follows the ENGINE's conduit length, not the drawn path", skip, () => {
  // conduit_length_m (pulling_lubrication.py): a full vertical_up is R·θ
  // (10 · π/6 = 5.236 m) even though buildPath draws it as two θ arcs;
  // an entered length on a bend wins over R·θ; hasPit adds nothing.
  const secs = [{ type: "vertical_up", angle: 30, radius: 10 },
    { type: "straight", length: 100, hasPit: true },
    { type: "horizontal_bend", angle: 90, radius: 10, length: 40, direction: "right" },
    { type: "horizontal_bend", angle: 90, radius: 0, length: 12, direction: "left" }];
  const p = G.buildPath(secs);
  const c = G.chainage(p, secs);
  const at = (n) => c[p.sectionStart[n]];
  assert.ok(Math.abs(at(2) - 10 * Math.PI / 6) < 1e-9, `after the vertical: ${at(2)}`);
  assert.ok(Math.abs(at(3) - (10 * Math.PI / 6 + 100)) < 1e-9);
  assert.ok(Math.abs(at(4) - (10 * Math.PI / 6 + 140)) < 1e-9);
  // The R = 0 bend draws no points, so its end comes from the spans.
  const sp = G.sectionSpans(secs);
  assert.ok(Math.abs(sp[3].from - (10 * Math.PI / 6 + 140)) < 1e-9);
  assert.ok(Math.abs(sp[3].to - (10 * Math.PI / 6 + 152)) < 1e-9);
  assert.equal(G.conduitLength({ type: "pit_section", length: 2 }), 0);
});

test("scale bar length: the largest 1/2/5 step that fits the pixels", skip, () => {
  assert.equal(G.niceBelow(150, 0.4), 200);   // 375 m allowed → 200 m (80 px)
  assert.equal(G.niceBelow(150, 1), 100);     // 150 m allowed → 100 m
  assert.equal(G.niceBelow(150, 0.25), 500);  // 600 m allowed → 500 m
  assert.equal(G.niceBelow(150, 10), 10);     // 15 m allowed → 10 m
  assert.ok(G.niceBelow(150, 0.4) * 0.4 <= 150);
});

test("a segment crossing a box hits it even with both ends outside", skip, () => {
  const box = { x: 10, y: 10, w: 10, h: 10 };
  assert.equal(G.segmentHitsBox(0, 15, 100, 15, box, 0), true);    // passes straight through
  assert.equal(G.segmentHitsBox(0, 0, 5, 5, box, 0), false);       // stops short
  assert.equal(G.segmentHitsBox(0, 25, 100, 25, box, 0), false);   // passes below
  assert.equal(G.segmentHitsBox(0, 22, 100, 22, box, 3), true);    // within the 3 px margin
  assert.equal(G.segmentHitsBox(15, 0, 15, 100, box, 0), true);    // vertical through
});

test("the tension band is one shared value", skip, () => {
  assert.equal(G.WARN_AT, 0.8);
});
