/* portal-agent.js — what a freshly-opened thread socket sends first.
 *
 * Pinned because an attachment-only first message ("read this", empty
 * composer) produced a thread with ZERO messages in the walk: the frame was
 * gated on the typed text alone, so the turn never started and the chat sat
 * at "connected" forever with nothing to show for the file the user picked.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const ctx = loadStatic("apps/public/standard/portal/pages/portal-agent.js");
const openingFrame = ctx._agentOpeningFrame;

test("the file loads and exposes the opening-frame helper", () => {
  assert.equal(typeof openingFrame, "function");
});

test("reopening an existing thread sends nothing", () => {
  assert.equal(openingFrame(undefined, undefined), null);
  assert.equal(openingFrame("", {}), null);
  assert.equal(openingFrame("", { vault_context: false, attachments: [] }), null);
});

test("typed words are sent", () => {
  assert.deepEqual({ ...openingFrame("what is this?") }, { type: "message", text: "what is this?" });
});

test("attachments ALONE are still sent, with an empty text", () => {
  const frame = openingFrame("", { attachments: ["10_Projects/budget.md"] });
  assert.notEqual(frame, null, "an attachment-only send must start a turn");
  assert.equal(frame.type, "message");
  assert.equal(frame.text, "");
  assert.deepEqual(Array.from(frame.attachments), ["10_Projects/budget.md"]);
});

test("the vault toggle alone is not a message", () => {
  // Nothing to answer: grounding a question needs the question.
  assert.equal(openingFrame("", { vault_context: true }), null);
});

test("the composer's extras ride the first frame", () => {
  const frame = openingFrame("summarise", { attachments: ["a.md"], vault_context: true });
  assert.equal(frame.text, "summarise");
  assert.equal(frame.vault_context, true);
  assert.deepEqual(Array.from(frame.attachments), ["a.md"]);
});
