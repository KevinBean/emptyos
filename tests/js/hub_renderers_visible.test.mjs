/* Pins the shared home-surface policy in /static/eos-hub-renderers.js —
 * `visibleBlocks` (which /hub/api/panels blocks a home shows, and where) and
 * the companion lane renderers — now that TWO hosts paint them: the core hub
 * (`apps/public/core/hub/pages/hub.js`) and portal's home board
 * (`apps/public/standard/portal/pages/portal-home.js`). The policy used to
 * live inline in hub.js `loadExplore`; moving it is what makes the two homes
 * agree, and this file is what keeps them agreeing.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { loadStatic, makeWindow, REPO_ROOT } from "./shim.mjs";

// The bundle expects `esc` / `escAttr` as window globals (eos.js installs
// them); the shipped EOS_UI.escAttr is the same four replacements. `esc` is
// the hub's own copy, verbatim.
const comps = loadStatic("emptyos/web/static/eos-components.js");
const esc = (s) => s == null ? "" : String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inert = () => new Proxy({}, { get: () => () => ({}) });
const win = makeWindow({ esc, escAttr: comps.EOS_UI.escAttr, EOS_UI: inert(), EOS: inert(), URLSearchParams });
vm.createContext(win);
vm.runInContext(fs.readFileSync(path.join(REPO_ROOT, "emptyos/web/static/eos-hub-renderers.js"), "utf8"), win,
  { filename: "eos-hub-renderers.js" });
const R = win.EOS_HUB_RENDERERS;

const block = (id, renderer, priority, extra = {}) => ({ id, renderer, priority, items: [{ data: {} }], ...extra });

test("the bundle exports the map, the policy and the lanes", () => {
  // The exact set: a renderer dropped (or added) in a move must be a
  // deliberate edit here, not something a `>= N` count absorbs.
  assert.deepEqual(Object.keys(R.map).sort(), [
    "accent-card", "app-grid", "bar", "checklist", "chips", "compare-tile",
    "countdown-tile", "deadline-row", "entity-card", "garden-mini", "hero-weather",
    "media-card", "next-up", "outcome-box", "plain-list", "quick-add", "quote",
    "slot-list", "stat-tile", "task-list", "text-card", "tiles-row",
  ]);
  for (const k of Object.keys(R.map)) assert.equal(typeof R.map[k], "function", k);
  assert.equal(typeof R.visibleBlocks, "function");
  assert.equal(typeof R.renderBlock, "function");
  for (const k of ["greeting", "next", "now", "today", "continueNotes"]) assert.equal(typeof R.lanes[k], "function");
  // hub markup calls these by bare name from onclick=/onsubmit= strings.
  assert.equal(typeof win.hubOutcomeGo, "function");
  assert.equal(typeof win.hubToggleCheck, "function");
});

test("ambient-band blocks (priority ≥ 150) are dropped; cognitive ones kept", () => {
  const { rest } = R.visibleBlocks([block("a", "stat-tile", 149), block("b", "stat-tile", 150), block("c", "stat-tile", 175)]);
  assert.deepEqual(rest.map((b) => b.id), ["a"]);
});

test("pinned-refs is shown whatever its band — the 📌 pin-to-home promise", () => {
  const { rest } = R.visibleBlocks([block("pinned-refs", "chips", 300)]);
  assert.deepEqual(rest.map((b) => b.id), ["pinned-refs"]);
});

test("task / calendar sources are left to the lanes; welcome + weather are consumed elsewhere", () => {
  const blocks = [
    block("t", "task-list", 60, { items: [{ data: {}, source: "task" }] }),
    block("c", "next-up", 60, { items: [{ data: {}, source: "calendar" }] }),
    block("hub-welcome", "accent-card", 25),
    block("w", "hero-weather", 10),
    block("keep", "plain-list", 60, { items: [{ data: {}, source: "journal" }] }),
  ];
  const { rest, weather } = R.visibleBlocks(blocks);
  assert.deepEqual(rest.map((b) => b.id), ["keep"]);
  // weather is handed back separately so a host can still run its side effect
  assert.deepEqual(weather.map((b) => b.id), ["w"]);
});

test("quick-add forms go to the quick zone, outcome box first", () => {
  const { quick, rest } = R.visibleBlocks([block("q", "quick-add", 60), block("o", "outcome-box", 70), block("p", "plain-list", 60)]);
  assert.deepEqual(quick.map((b) => b.id), ["o", "q"]);
  assert.deepEqual(rest.map((b) => b.id), ["p"]);
});

test("a non-array payload yields empty lists, not a throw", () => {
  const out = R.visibleBlocks(undefined);
  // lengths, not deepEqual: the arrays come from the vm realm (other prototype)
  assert.equal(out.quick.length + out.rest.length + out.weather.length, 0);
});

test("renderBlock names an unknown renderer instead of throwing", () => {
  const html = R.renderBlock(block("x", "no-such-renderer", 60));
  assert.match(html, /Unknown renderer: <code>no-such-renderer<\/code>/);
});

const el = () => ({ innerHTML: "", querySelectorAll: () => [], querySelector: () => null });

test("today lane: overdue / due-today / journal chips + task rows, into the host element", () => {
  const e = el();
  R.lanes.today(e, { overdue: 2, due_today: 1, journaled: false, streak: 3, tasks: ["Write <b>", "Call"] });
  assert.match(e.innerHTML, /hub-chip-stat overdue" href="\/task\/">2 overdue</);
  assert.match(e.innerHTML, /1 due today</);
  assert.match(e.innerHTML, /not journaled</);
  assert.match(e.innerHTML, /3-day streak</);
  assert.match(e.innerHTML, /hub-today-task" href="\/task\/">Write &lt;b&gt;</);
  const empty = el();
  R.lanes.today(empty, null);
  assert.equal(empty.innerHTML, "");
});

test("next lane: greeting optional, so portal's eyebrow can carry it", () => {
  const d = { greeting: "Good evening", next_move: { title: "Close the day", why: "3 overdue", action_href: "/task/", action_label: "Open" } };
  const withGreet = el();
  R.lanes.next(withGreet, d);
  assert.match(withGreet.innerHTML, /hub-greeting">Good evening</);
  assert.match(withGreet.innerHTML, /hub-next-title">Close the day</);
  assert.match(withGreet.innerHTML, /Suggested because: 3 overdue/);
  const noGreet = el();
  R.lanes.next(noGreet, d, { greeting: false });
  assert.doesNotMatch(noGreet.innerHTML, /hub-greeting/);
  assert.match(noGreet.innerHTML, /hub-next-card/);
  const g = { textContent: "" };
  R.lanes.greeting(g, d);
  assert.equal(g.textContent, "Good evening");
});

test("now lane and continue lane render rows; continue strips the .md and the folder", () => {
  const now = el();
  R.lanes.now(now, [{ time: "14:00", title: "Standup" }]);
  assert.match(now.innerHTML, /hub-now-time">14:00<\/span><span class="hub-now-title">Standup</);
  const cont = el();
  R.lanes.continueNotes(cont, [{ path: "50_Journal\\2026-10-03.md" }]);
  assert.match(cont.innerHTML, /hub-cont-name">2026-10-03</);
  const none = el();
  R.lanes.continueNotes(none, "not a list");
  assert.equal(none.innerHTML, "");
});

// ── Snooze / Dismiss — the lanes' only state, now shared by two hosts ──────
// A move dismissed on the portal must stay dismissed on /hub/: both read the
// one localStorage key. The fake element hands back real Snooze/Dismiss
// buttons so the click wiring itself runs, not just the helpers.
const store = new Map();
win.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

function laneHost() {
  const buttons = [];
  let removed = false;
  return {
    innerHTML: "",
    querySelectorAll(sel) {
      if (sel !== ".hub-next-defer") return [];
      for (const mode of (this.innerHTML.match(/data-defer="(\w+)"/g) || [])) {
        const m = mode.match(/"(\w+)"/)[1];
        const btn = { m, handler: null, getAttribute: () => m, addEventListener(_, fn) { this.handler = fn; } };
        buttons.push(btn);
      }
      return buttons;
    },
    querySelector: (sel) => (sel === ".hub-next-card" ? { remove() { removed = true; } } : null),
    click(mode) { buttons.find((b) => b.m === mode).handler(); },
    get removed() { return removed; },
  };
}
const move = { greeting: "Hi", next_move: { title: "Close the day", action_href: "/task/" } };

test("Dismiss removes the card and keeps it away on the next paint (any host)", () => {
  store.clear();
  const host = laneHost();
  R.lanes.next(host, move);
  assert.match(host.innerHTML, /hub-next-card/);
  host.click("dismiss");
  assert.equal(host.removed, true, "the card is removed on click");
  assert.equal(R.moveSuppressed(move), true);
  // the same key both hosts read — a different key per host would pass the
  // line above and fail this one
  assert.ok(store.has("hub.companion.suppress"));
  const again = laneHost();
  R.lanes.next(again, move, { greeting: false });
  assert.doesNotMatch(again.innerHTML, /hub-next-card/, "a dismissed move stays dismissed");
});

test("Snooze suppresses for now but expires; a different move is not suppressed", () => {
  store.clear();
  const host = laneHost();
  R.lanes.next(host, move);
  host.click("snooze");
  assert.equal(R.moveSuppressed(move), true);
  assert.equal(R.moveSuppressed({ next_move: { title: "Something else" } }), false);
  const saved = JSON.parse(store.get("hub.companion.suppress"));
  saved.until = Date.now() - 1;           // the snooze window has passed
  store.set("hub.companion.suppress", JSON.stringify(saved));
  assert.equal(R.moveSuppressed(move), false);
});

test("the suppression day is the LOCAL date, not the UTC one", () => {
  store.clear();
  R.suppressMove(move, "dismiss");
  const key = JSON.parse(store.get("hub.companion.suppress")).key;
  const n = new Date();
  const local = n.getFullYear() + "-" + String(n.getMonth() + 1).padStart(2, "0") + "-" + String(n.getDate()).padStart(2, "0");
  assert.equal(key, local + "|Close the day");
});

test("hero-weather: an empty-string temperature renders no bare degree sign", () => {
  const chip = { textContent: "x" };
  const prev = win.document.getElementById;
  win.document.getElementById = (id) => (id === "hub-hero-weather" ? chip : null);
  try {
    R.map["hero-weather"]([{ data: { emoji: "☁️", temperature: "" } }]);
    assert.equal(chip.textContent, "☁️");
    R.map["hero-weather"]([{ data: { emoji: "☁️", temperature: 18 } }]);
    assert.equal(chip.textContent, "☁️ 18°");
    R.map["hero-weather"]([{ data: { temperature: 0 } }]);
    assert.equal(chip.textContent, "0°", "zero degrees is a real temperature");
  } finally {
    win.document.getElementById = prev;
  }
});
