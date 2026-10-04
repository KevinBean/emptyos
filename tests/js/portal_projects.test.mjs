/* portal-projects.js — the chat-first sidebar's grouping logic.
 *
 * Rendering is walked in a browser. Pinned here: which chats land under which
 * project, that a chat whose project vanished is shown (as recent) rather than
 * dropped, and which rooms still count as not yet copied into projects.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const PP = loadStatic("apps/public/standard/portal/pages/portal-projects.js").PortalProjects;
const ids = (rows) => Array.from(rows || [], (r) => r.id);

test("the file loads and exposes its surface", () => {
  for (const n of ["init", "load", "render", "pendingProject", "pendingName", "clearPending",
                   "newChatIn", "groupChats", "unmigrated"]) {
    assert.equal(typeof PP[n], "function", `${n} missing`);
  }
});

test("chats group under their project; others are recent; non-chats never show", () => {
  const projects = [{ id: "prj-1" }, { id: "prj-2" }];
  const sessions = [
    { id: "a", profile: "chat", project_id: "prj-1" },
    { id: "b", profile: "chat", project_id: "" },
    { id: "c", profile: "", project_id: "prj-1" },          // a coding run, never here
    { id: "d", profile: "chat", project_id: "prj-2" },
    { id: "e", profile: "chat", project_id: "prj-gone" },   // project deleted elsewhere
  ];
  const g = PP.groupChats(sessions, projects, 10);
  assert.deepEqual(ids(g.byProject["prj-1"]), ["a"]);
  assert.deepEqual(ids(g.byProject["prj-2"]), ["d"]);
  assert.deepEqual(ids(g.recent), ["b", "e"]);
});

test("recent chats are capped", () => {
  const sessions = Array.from({ length: 5 }, (_, i) => ({ id: String(i), profile: "chat" }));
  assert.deepEqual(ids(PP.groupChats(sessions, [], 3).recent), ["0", "1", "2"]);
});

test("a room already copied into a project is not offered again", () => {
  const left = PP.unmigrated([{ id: "f1" }, { id: "f2", chat_project_id: "prj-1" }, { id: "f3", chat_project_id: "" }]);
  assert.deepEqual(ids(left), ["f1", "f3"]);
});
