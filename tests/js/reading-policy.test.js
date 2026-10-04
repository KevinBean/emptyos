// Unit tests for the reading layer's decisions. Milliseconds, no browser.
//
// Every case below is a bug that SHIPPED (2026-07-14) and was caught by the reader
// rather than by a test, because the rule lived inline in a content script and the
// only coverage was a 50-second Chromium walk. Run: `node tests/js/reading-policy.test.js`

const assert = require("node:assert/strict");
const path = require("node:path");

const P = require(path.join(__dirname, "..", "..", "tools", "chrome-extension", "reading-policy.js"));

let ran = 0;
function test(name, fn) {
  fn();
  ran += 1;
  console.log("  ok  " + name);
}

// ── scanDelay: the debounce needs a ceiling ─────────────────────────────────

test("a page that mutates forever still gets read", () => {
  // The starvation bug. The MutationObserver calls scheduleScan on every DOM change,
  // and each call re-armed the timer 900ms out — so on a page with a ticking clock
  // the scan was perpetually a moment away and NEVER FIRED ONCE.
  let now = 1_000_000;
  let queuedAt = 0;
  let wasQueued = false;
  let fired = false;

  for (let tick = 0; tick < 100; tick++) {          // 100 mutations, 200ms apart
    const d = P.scanDelay({ now, lastScanAt: 0, queuedAt, wasQueued, delay: 1800 });
    queuedAt = d.queuedAt;
    wasQueued = true;
    if (d.wait === 0) { fired = true; break; }      // the ceiling let it go
    now += 200;
  }
  assert.ok(fired, "the scan was deferred forever — this is the starvation bug");
  assert.ok(now - queuedAt <= P.MAX_SCAN_DEFER + 200,
    "it must fire within MAX_SCAN_DEFER of when the reader began waiting");
});

test("the ceiling is measured from the FIRST queue, not the latest", () => {
  const first = P.scanDelay({ now: 1000, delay: 900 });
  assert.equal(first.queuedAt, 1000);
  // 2.5s of continuous activity later, still the same start point.
  const later = P.scanDelay({ now: 3500, queuedAt: first.queuedAt, wasQueued: true, delay: 900 });
  assert.equal(later.queuedAt, 1000, "re-queuing must not reset the reader's wait");
  assert.equal(later.wait, 500, "only 500ms of the 3000ms ceiling is left");
});

test("the throttle is a floor: one view is not paid for twice", () => {
  const d = P.scanDelay({ now: 5000, lastScanAt: 1000, delay: 0 });
  assert.equal(d.wait, P.MIN_SCAN_INTERVAL - 4000,
    "a scan 4s ago means waiting out the rest of the interval");
});

test("a teardown does not stall the next scan", () => {
  // deactivate() resets lastScanAt to 0. Before that, the replacement scan was
  // throttled against the scan it had just killed — up to 20s of an empty rail.
  const d = P.scanDelay({ now: 1_000_000, lastScanAt: 0, delay: 250 });
  assert.equal(d.wait, 250, "nothing has been paid for, so nothing is owed");
});

// ── railState: never report a verdict we did not seek ───────────────────────

test("a pending scan never renders as a verdict", () => {
  for (const state of [{ scanning: true }, { scanQueued: true }]) {
    const r = P.railState(state);
    assert.equal(r.kind, "reading");
    assert.notEqual(r.message, P.RAIL_TEXT.empty,
      "'Nothing flagged yet' on a scan that has not happened is a lie");
  }
});

test("a screen with too little text says so, and does not blame the model", () => {
  const r = P.railState({ tooLittleText: true });
  assert.equal(r.kind, "too-little");
  assert.match(r.message, /Not enough text/);
});

test("words found but none on screen is not the same as none found", () => {
  assert.equal(P.railState({ itemCount: 4, visibleCount: 0 }).kind, "none-here");
  assert.equal(P.railState({ itemCount: 0, visibleCount: 0 }).kind, "empty");
});

test("rows win over everything, and an error beats silence", () => {
  assert.equal(P.railState({ visibleCount: 2, railError: "boom" }).kind, "rows");
  assert.equal(P.railState({ railError: "boom" }).kind, "error");
  // An error must not be masked by a scan we then queued.
  assert.equal(P.railState({ railError: "boom", scanQueued: true }).kind, "error");
});

test("under 80 characters there is nothing worth sending", () => {
  assert.equal(P.hasEnoughText("short"), false);
  assert.equal(P.hasEnoughText("x".repeat(80)), true);
});

// ── shouldRestart: a cosmetic change must not throw away a paid answer ──────

test("ticking Speak does not discard the scan in flight", () => {
  // The bug: every panel control saves, every save broadcasts, and the page answered
  // each broadcast with a full teardown — so the model call was made, paid for, its
  // answer arrived, and was dropped because the serial had moved.
  for (const key of ["pronounce", "rail", "display", "local_provider"]) {
    const restart = P.shouldRestart({
      before: { [key]: "a" }, after: { [key]: "b" },
      beforeMode: "flow", wantedMode: "flow",
    });
    assert.equal(restart, false, `${key} is cosmetic — it must not restart the engine`);
  }
});

test("a different model or language does invalidate the words", () => {
  for (const key of P.RESCAN_KEYS) {
    assert.equal(P.shouldRestart({
      before: { [key]: "a" }, after: { [key]: "b" },
      beforeMode: "flow", wantedMode: "flow",
    }), true, `${key} changes the answers — restart`);
  }
});

test("a mode change always restarts", () => {
  assert.equal(P.shouldRestart({ beforeMode: "off", wantedMode: "flow" }), true);
  assert.equal(P.shouldRestart({ beforeMode: "flow", wantedMode: "off" }), true);
});

// ── effectiveMode: the inbox is never read ─────────────────────────────────

const isPrivate = h => ["mail.google.com", "commbank.com.au"].some(
  e => h === e || h.endsWith("." + e));

test("a private host is dormant even with Flow on", () => {
  const mode = P.effectiveMode({
    settings: { mode: "flow" }, hostname: "mail.google.com", isPrivateHost: isPrivate });
  assert.equal(mode, "off", "reading an inbox would send correspondence to a model");
});

test("a private host the reader named himself is read", () => {
  const mode = P.effectiveMode({
    settings: { mode: "flow", allowed_private_hosts: ["mail.google.com"] },
    hostname: "mail.google.com", isPrivateHost: isPrivate });
  assert.equal(mode, "flow");
});

test("a per-site pause survives a broadcast that carries no mode", () => {
  const mode = P.effectiveMode({
    settings: { mode: "flow", excluded_hosts: ["example.com"] },
    nextMode: null, hostname: "news.example.com", isPrivateHost: isPrivate });
  assert.equal(mode, "off", "the exclusion is re-applied, not trusted from the message");
});

test("an ordinary article is untouched by any of it", () => {
  assert.equal(P.effectiveMode({
    settings: { mode: "flow" }, hostname: "theatlantic.com", isPrivateHost: isPrivate }), "flow");
});

// ── wordSource: only one tier means the word is the reader's ───────────────

test("a remembered answer is not a saved word", () => {
  // The bug (2026-08-17): `cache` labelled itself "saved", which is a claim about
  // OUR cost, not about the reader's dictionary. The reader saw a chip reading
  // "saved" with a Save button beside it and read the button as broken.
  assert.equal(P.wordSource("cache").saved, false);
  assert.notEqual(P.wordSource("cache").chip, "saved",
    "the cache chip must not use the word the vault tier owns");
  assert.notEqual(P.wordSource("cache").chip, P.wordSource("vault").chip,
    "the two tiers a reader confuses must not share a word");
  assert.equal(P.wordSource("vault").saved, true, "only the vault tier is theirs");
  assert.equal(P.wordSource("model").saved, false);
});

test("an unknown or missing source is treated as freshly generated", () => {
  assert.equal(P.wordSource("").chip, P.wordSource("model").chip);
  assert.equal(P.wordSource(undefined).saved, false);
  assert.equal(P.wordSource("something-new").saved, false,
    "an unrecognised tier must never claim the word is saved");
});

// ── saveVerdict: the judgement and the note are two different writes ───────

test("a verdict whose note failed does not claim it saved", () => {
  // /api/reading/feedback answers ok:true for the PROFILE write and reports the
  // vault note separately in `saved`. `ok || saved` made the second unreachable,
  // so "Known · saved" appeared over a note that was never written.
  const failed = P.saveVerdict({ action: "known", result: { ok: true, saved: false } });
  assert.equal(failed.saved, false);
  assert.equal(failed.judged, true,
    "the judgement DID land — it must still stop the word being flagged");
  assert.match(failed.label, /not saved/, "the label must say the note did not land");

  const landed = P.saveVerdict({ action: "hard", result: { ok: true, saved: true } });
  assert.equal(landed.saved, true);
  assert.equal(landed.label, "Hard · saved");

  const nothing = P.saveVerdict({ action: "known", result: null });
  assert.equal(nothing.judged, false, "no answer is not a judgement");
  assert.equal(nothing.saved, false);
});

test("save is the one action where ok IS the note", () => {
  assert.deepEqual(P.saveVerdict({ action: "save", result: { ok: true } }),
    { judged: true, saved: true, rating: null, label: "Saved" });
  assert.equal(P.saveVerdict({ action: "save", result: { error: "boom" } }).saved, false);
  assert.equal(P.saveVerdict({ action: "save", result: null }).saved, false,
    "a torn-down worker answers nothing; that is not a save");
});

test("a rating the daemon did not report is not a rating of zero", () => {
  // A verdict ROUTES a word; only the star row rates it. So the daemon reports
  // the number the note already holds and OMITS the key when it could not read
  // the note. Collapsing that absence to 0 repaints a 3-star row empty, which
  // looks exactly like the click having eaten the rating — and the note still
  // holds the 3, so it comes back on the next open.
  //
  // This lived inline in reading-assist.js for one commit and was got wrong
  // there: one of the two paint branches was guarded and the other built a fresh
  // row from an undefined rating, rendering a definite 0. That is the whole
  // reason the rule belongs here, where it can be executed.
  const absent = P.saveVerdict({ action: "hard", result: { ok: true, saved: true } });
  assert.equal(absent.rating, null, "no key reported is 'not told', never 0");

  const zero = P.saveVerdict({ action: "hard", result: { ok: true, saved: true, difficulty: 0 } });
  assert.equal(zero.rating, 0, "0 is a real value — the reader cleared the rating");

  const three = P.saveVerdict({ action: "known", result: { ok: true, saved: true, difficulty: 3 } });
  assert.equal(three.rating, 3);

  // A save reports it too — the row is inserted on the same click.
  assert.equal(P.saveVerdict({ action: "save", result: { ok: true, difficulty: 4 } }).rating, 4);

  // Nothing usable came back at all: there is no note to have read.
  assert.equal(P.saveVerdict({ action: "known", result: null }).rating, null);
  assert.equal(P.saveVerdict({ action: "hard", result: { error: "boom" } }).rating, null);

  // A non-number must never be believed — a string "3" would paint through
  // Number() and a stray true would paint 1. NaN and Infinity are the ones that
  // slip a bare `typeof === "number"` check and then paint as a definite 0.
  for (const junk of ["3", true, [3], {}, NaN, Infinity]) {
    assert.equal(P.saveVerdict({ action: "hard", result: { ok: true, saved: true, difficulty: junk } }).rating,
      null, "a non-number rating is not told, not coerced");
  }
});

// ── acceptResponse ─────────────────────────────────────────────────────────

test("an answer a newer scan superseded is not the one the rail awaits", () => {
  assert.equal(P.acceptResponse({ serial: 3, currentSerial: 3, mode: "flow" }), true);
  assert.equal(P.acceptResponse({ serial: 2, currentSerial: 3, mode: "flow" }), false);
  assert.equal(P.acceptResponse({ serial: 3, currentSerial: 3, mode: "ask" }), false);
});

// ── linkIsProse ────────────────────────────────────────────────────────────

test("a menu item IS its link; a link in a sentence is not", () => {
  // Menu: the link text is the whole block.
  assert.equal(P.linkIsProse({ blockLen: 16, linkLen: 16 }), false);
  // Prose: a few words inside a sentence that goes on without them.
  assert.equal(P.linkIsProse({ blockLen: 180, linkLen: 14 }), true);
  // A short block that happens to contain a short link is still not prose.
  assert.equal(P.linkIsProse({ blockLen: 60, linkLen: 5 }), false);
});

// ── fingerprint ────────────────────────────────────────────────────────────

test("the same view fingerprints the same, a changed one does not", () => {
  assert.equal(P.fingerprint("hello world"), P.fingerprint("hello world"));
  assert.notEqual(P.fingerprint("hello world"), P.fingerprint("hello worlds"));
  assert.equal(P.fingerprint(""), P.fingerprint(""));
});

console.log(`\n${ran} passed`);
