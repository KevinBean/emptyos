// Unit tests for the Browser Session page-bridge ref lifecycle. No browser.
//
// This is the bug that SHIPPED (2026-07-15) and was caught by a live CAD walk,
// not a test — exactly the reading-policy.js situation, so the logic was extracted
// to ref-registry.js to make it testable. Run: `node tests/js/ref-registry.test.js`
// (or via tests/test_unit_ref_registry.py).

const assert = require("node:assert/strict");
const path = require("node:path");

const { createRefRegistry } = require(
  path.join(__dirname, "..", "..", "tools", "chrome-extension", "ref-registry.js")
);

let ran = 0;
function test(name, fn) {
  fn();
  ran += 1;
  console.log("  ok  " + name);
}

// A mock element: opaque handle with a mutable role/name and a connected flag.
function elem(role, name, connected = true) {
  return { role, name, connected };
}

function makeRegistry() {
  let v = 0;
  return createRefRegistry({
    sigOf: (el) => el.role + " " + el.name,
    isConnected: (el) => el.connected,
    newVersion: () => "v" + ++v,
  });
}

// ── Rule 1: a ref survives UNRELATED churn ──────────────────────────────────

test("a ref stays valid while OTHER parts of the page mutate", () => {
  // The blocker. The old bridge observed the whole document and wiped every ref
  // on any mutation, so a live 3D viewport killed refs before they could be used.
  const reg = makeRegistry();
  const target = elem("combobox", "Layout");
  const viewport = elem("button", "Iso");   // a neighbour that will churn
  const ref = reg.add(target);
  reg.add(viewport);

  // Simulate 100 unrelated mutations elsewhere on the page.
  for (let i = 0; i < 100; i++) {
    viewport.name = "Iso frame " + i;        // the 3D toolbar re-labels constantly
  }

  // The target ref must STILL resolve — unrelated churn does not invalidate it.
  assert.equal(reg.lookup(ref), target, "unrelated churn killed a still-valid ref");
});

// ── Rule 2: a ref is REFUSED when ITS element changes identity or leaves ─────

test("a ref is refused once its own element is repurposed", () => {
  // The safety property: the page must not swap a different control under a ref
  // between snapshot and action.
  const reg = makeRegistry();
  const el = elem("button", "Save");
  const ref = reg.add(el);
  el.name = "Delete";                         // same node, now a different action

  assert.throws(() => reg.lookup(ref), /stale_ref/,
    "a repurposed element must not resolve");
});

test("a ref is refused once its element is disconnected", () => {
  const reg = makeRegistry();
  const el = elem("button", "Save");
  const ref = reg.add(el);
  el.connected = false;                       // removed from the DOM

  assert.throws(() => reg.lookup(ref), /stale_ref/,
    "a removed element must not resolve");
});

test("a role change alone invalidates a ref", () => {
  const reg = makeRegistry();
  const el = elem("button", "Go");
  const ref = reg.add(el);
  el.role = "link";

  assert.throws(() => reg.lookup(ref), /stale_ref/);
});

// ── Snapshot generations + version handshake ────────────────────────────────

test("reset() starts a new generation; old refs no longer resolve", () => {
  const reg = makeRegistry();
  const a = elem("button", "A");
  const oldRef = reg.add(a);
  const oldVersion = reg.version();

  reg.reset();                                // new snapshot
  assert.notEqual(reg.version(), oldVersion, "reset must bump the version");
  assert.throws(() => reg.lookup(oldRef), /stale_ref/,
    "a ref from the previous generation must not resolve");
});

test("an explicit stale document_version is refused", () => {
  const reg = makeRegistry();
  const el = elem("button", "A");
  const ref = reg.add(el);
  const v1 = reg.version();
  reg.reset();
  reg.add(el);                                // re-registered in the new generation
  // Passing the OLD version explicitly must fail even though the element is fine.
  assert.throws(() => reg.lookup(ref, v1), /stale_ref/);
});

test("serial never reuses a ref id across generations (no collision)", () => {
  const reg = makeRegistry();
  const r1 = reg.add(elem("button", "A"));
  reg.reset();
  const r2 = reg.add(elem("button", "B"));
  assert.notEqual(r1, r2, "a stale ref id must never collide with a new element");
});

console.log("\n" + ran + " passed");
