/* portal-attach.js — what a chat-first composer sends with a message.
 *
 * Pinned: an empty composer adds NO fields (a plain send stays the message it
 * always was), paths are de-duplicated and capped, images are told apart from
 * documents, and nothing is taken while chat-first is off.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const load = (extra = {}) => loadStatic("apps/public/standard/portal/pages/portal-attach.js", extra).PortalAttach;
const PA = load();

test("the file loads and exposes its surface", () => {
  for (const n of ["init", "sync", "take", "kindOf", "payloadOf", "addItem"]) {
    assert.equal(typeof PA[n], "function", `${n} missing`);
  }
});

test("an empty composer adds no fields to the message", () => {
  assert.deepEqual({ ...PA.payloadOf({ items: [], vault: false }) }, {});
});

test("attachments and the vault toggle ride the message", () => {
  const slot = { items: [], vault: true };
  PA.addItem(slot, { path: "00_Inbox/_attachments/shot.PNG", name: "shot.PNG" });
  PA.addItem(slot, { path: "10_Projects/x/x.md" });
  const p = PA.payloadOf(slot);
  assert.deepEqual(Array.from(p.attachments), ["00_Inbox/_attachments/shot.PNG", "10_Projects/x/x.md"]);
  assert.equal(p.vault_context, true);
  assert.deepEqual(slot.items.map((i) => i.kind), ["image", "file"]);
  assert.equal(slot.items[1].name, "x.md");  // a nameless pick is named by its file
});

test("duplicates collapse and the cap refuses the ninth", () => {
  const slot = { items: [], vault: false };
  assert.equal(PA.addItem(slot, { path: "a.md" }), true);
  assert.equal(PA.addItem(slot, { path: "a.md" }), true);
  assert.equal(slot.items.length, 1);
  for (let i = 0; i < 7; i++) PA.addItem(slot, { path: `f${i}.md` });
  assert.equal(slot.items.length, 8);
  assert.equal(PA.addItem(slot, { path: "one-too-many.md" }), false);
  assert.equal(PA.addItem(slot, {}), false);
});

test("switching threads empties the reply composer, never the hero's", () => {
  // Review B3 #5: files picked in chat A would otherwise ride the next
  // message in chat B — possibly to a different, cloud model.
  const ctx = loadStatic("apps/public/standard/portal/pages/portal-attach.js", {
    PortalChat: { isOn: () => true },
    currentAgent: { id: "agent:a", _backend: "agent", _profile: "chat" },
    ACTIVE_BACKEND: "chat", ACTIVE_VERB: "think", _pendingFolderId: null,
    EOS_UI: { esc: String, escAttr: String },
  });
  const PA = ctx.PortalAttach;
  PA.init();
  PA.addItem(PA.slot("chat"), { path: "a.md" });
  PA.addItem(PA.slot("hero"), { path: "b.md" });
  PA.sync();                                   // same thread: both stay
  assert.equal(PA.slot("chat").items.length, 1);
  ctx.currentAgent = { id: "agent:b", _backend: "agent", _profile: "chat" };
  PA.sync();                                   // a different chat is open now
  assert.equal(PA.slot("chat").items.length, 0);
  assert.equal(PA.slot("hero").items.length, 1, "the hero composer is not a thread");
});

test("nothing is taken while chat-first is off", () => {
  const off = load({ PortalChat: { isOn: () => false } });
  off.init();
  assert.deepEqual({ ...off.take("hero").payload }, {});
});
