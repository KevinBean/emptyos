/* portal-launch.js — which hero-composer texts open an app instead of a chat.
 *
 * The rule exists because an Enter that opens an app where the user meant to
 * send a message is a hijack, and the hero composer starts conversations:
 *
 *  - only the WHOLE text, as one exact app id or name, opens on Enter;
 *  - a prefix ("exp") never does — it only offers suggestions — because many
 *    ordinary one-word replies ("no", "test", "plan", "read") are the prefix
 *    of some app on a real install;
 *  - punctuation or a second word means "this is a message".
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const { PortalLaunch: L } = loadStatic("apps/public/standard/portal/pages/portal-launch.js");

// A slice of the real catalog shape: {id: display name}.
const CATALOG = {
  task: "Tasks",
  journal: "Journal",
  expense: "Expense",
  explore: "Explore",
  kb: "Knowledge Base",
  "kb-butler": "KB Butler",
  cad: "CAD",
  "cable-pulling": "Cable Pulling",
  dictionary: "Dictionary",
  dictation: "Dictation",
  planner: "Day Planner",
};

const m = (text, rank) => L.match(text, CATALOG, rank);
// Normalise vm-realm values before assert/strict compares prototypes.
const ids = (list) => Array.from(list, (a) => a.id);

test("the module loaded with its pure matcher", () => {
  assert.equal(typeof L.match, "function");
});

test("an exact app id opens that app", () => {
  assert.equal(m("task").exact.id, "task");
  assert.equal(m("  CAD ").exact.id, "cad", "case and surrounding space do not matter");
});

test("an exact display name opens too, and an id beats a name", () => {
  assert.equal(m("tasks").exact.id, "task");
  // "kb" is an id; the KB Butler's name does not make it ambiguous.
  assert.equal(m("kb").exact.id, "kb");
});

test("a prefix never opens on Enter — it only suggests", () => {
  const r = m("exp");
  assert.equal(r.exact, null);
  assert.deepEqual(ids(r.suggestions).sort(), ["expense", "explore"]);
  // the measured hijack shape: a common one-word reply that prefixes an app
  assert.equal(m("plan").exact, null);
  assert.deepEqual(ids(m("plan").suggestions), ["planner"]);
});

test("suggestions are ordered by recent use, then by the shorter id", () => {
  assert.deepEqual(ids(m("dict", { dictation: 5, dictionary: 1 }).suggestions), ["dictation", "dictionary"]);
  assert.deepEqual(ids(m("dict", { dictionary: 5 }).suggestions), ["dictionary", "dictation"]);
  // no ranking: shorter id first (both length 10 here → alphabetical)
  assert.deepEqual(ids(m("dict").suggestions), ["dictation", "dictionary"]);
  assert.deepEqual(ids(m("ca").suggestions), ["cad", "cable-pulling"]);
});

test("at most three suggestions", () => {
  const many = {};
  for (let i = 0; i < 9; i++) many["note" + i] = "Note " + i;
  assert.equal(L.match("note", many).suggestions.length, 3);
});

test("a message is never an app: punctuation, a second word, one letter, empty", () => {
  // "cable pulling" IS an app's full display name — still two words, so still a
  // message: the one-word rule has no exceptions to remember.
  for (const text of ["task?", "task please", "open task", "cable pulling", "knowledge base", "t", "", "   ", "tasks!", "cad.", null, undefined]) {
    const r = m(text);
    assert.equal(r.exact, null, JSON.stringify(text));
    assert.equal(r.suggestions.length, 0, JSON.stringify(text));
  }
});

test("a word that names no app yields nothing", () => {
  const r = m("hello");
  assert.equal(r.exact, null);
  assert.equal(r.suggestions.length, 0);
});

test("an empty or missing catalog is safe", () => {
  assert.equal(L.match("task", {}).exact, null);
  assert.equal(L.match("task", undefined).exact, null);
});

test("an id beats another app's identical display name", () => {
  // Third-party apps choose their own names; one may equal another's id.
  const cat = { kb: "Knowledge", "kb-tool": "KB" };
  assert.equal(L.match("kb", cat).exact.id, "kb");
});

test("two apps with the same name: the more recently used one opens", () => {
  const cat = { "notes-a": "Notes", "notes-b": "Notes" };
  assert.equal(L.match("notes", cat, { "notes-b": 3, "notes-a": 1 }).exact.id, "notes-b");
  assert.equal(L.match("notes", cat, { "notes-a": 3 }).exact.id, "notes-a");
});
