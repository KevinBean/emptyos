/* EOS_AGENT_VIEW — the shared agent-session renderers behind /agent/ and /portal/.
 *
 * The case that justified the extraction: every message the agent persists is a
 * "full-message dict" ({content, tool_calls} / {content, tool_call_id}), and
 * /agent/'s history walk only knew strings and block lists, so a reopened
 * session drew nothing. The fixtures below are the three encodings as they sit
 * in the table — the dict ones copied from a live session's shape (2026-09-12).
 *
 * Pure functions only — see the header of shim.mjs.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { loadStatic, REPO_ROOT } from "./shim.mjs";

// eos-agent-view.js escapes through EOS_UI.esc, so it shares the page's scope
// with eos-components.js exactly as both pages load them.
const ctx = loadStatic("emptyos/web/static/eos-components.js");
const VIEW_PATH = "emptyos/web/static/eos-agent-view.js";
vm.runInContext(fs.readFileSync(path.join(REPO_ROOT, VIEW_PATH), "utf8"), ctx, { filename: VIEW_PATH });
const V = ctx.EOS_AGENT_VIEW;

/* Arrays and objects built inside the vm carry the sandbox's prototypes, which
 * assert/strict's deepEqual compares — normalise at the boundary. */
const plain = (x) => JSON.parse(JSON.stringify(x));

function walk(messages, { withNotice = true } = {}) {
  const out = [];
  const h = {
    user: (text) => out.push(["user", text]),
    assistant: (segs) => out.push(["assistant", plain(segs)]),
    toolResult: (id, text, isError) => out.push(["tool", id, text, isError]),
  };
  if (withNotice) h.notice = (text) => out.push(["notice", text]);
  V.walkHistory(messages, h);
  return out;
}

test("the view loads and exposes its helpers", () => {
  assert.ok(V, "EOS_AGENT_VIEW missing — the file failed to load");
  for (const name of ["diffHtml", "toolExtrasHtml", "fmtCost", "footerParts", "resultText", "walkHistory"]) {
    assert.equal(typeof V[name], "function", `${name} missing`);
  }
});

test("the full-message dict encoding replays (the /agent/ blank-history bug)", () => {
  const got = walk([
    { role: "user", content: { content: "list the files" } },
    {
      role: "assistant",
      content: {
        content: null,
        tool_calls: [{ id: "call_1", type: "function", function: { name: "Glob", arguments: '{"pattern":"*.py"}' } }],
      },
    },
    { role: "tool", content: { content: "a.py\nb.py", tool_call_id: "call_1" } },
    { role: "assistant", content: { content: "Two files." } },
  ]);
  assert.deepEqual(got, [
    ["user", "list the files"],
    ["assistant", [{ type: "tool_use", id: "call_1", name: "Glob", input: { pattern: "*.py" } }]],
    ["tool", "call_1", "a.py\nb.py", false],
    ["assistant", [{ type: "text", text: "Two files." }]],
  ]);
});

test("the Anthropic block-list encoding replays, text before and after a tool", () => {
  const got = walk([
    { role: "user", content: "hi" },
    {
      role: "assistant",
      content: [
        { type: "text", text: "Looking" },
        { type: "text", text: " now." },
        { type: "tool_use", id: "tu_1", name: "Read", input: { path: "x.md" } },
        { type: "text", text: "Done." },
      ],
    },
    { role: "user", content: [{ type: "tool_result", tool_use_id: "tu_1", content: [{ type: "text", text: "body" }], is_error: true }] },
  ]);
  assert.deepEqual(got, [
    ["user", "hi"],
    ["assistant", [
      { type: "text", text: "Looking now." },   // adjacent blocks merge
      { type: "tool_use", id: "tu_1", name: "Read", input: { path: "x.md" } },
      { type: "text", text: "Done." },
    ]],
    ["tool", "tu_1", "body", true],
  ]);
});

test("an Anthropic message stored as a dict unwraps to its block list", () => {
  const got = walk([
    { role: "user", content: { content: [{ type: "tool_result", tool_use_id: "t9", content: "ok" }] } },
    { role: "assistant", content: { content: [{ type: "text", text: "fine" }] } },
  ]);
  assert.deepEqual(got, [["tool", "t9", "ok", false], ["assistant", [{ type: "text", text: "fine" }]]]);
});

test("legacy top-level tool_calls and tool_call_id still replay", () => {
  const got = walk([
    { role: "assistant", content: "", tool_calls: [{ id: "c2", function: { name: "Bash", arguments: "not json" } }] },
    { role: "tool", content: "out", tool_call_id: "c2" },
  ]);
  assert.deepEqual(got, [
    ["assistant", [{ type: "tool_use", id: "c2", name: "Bash", input: { arguments: "not json" } }]],
    ["tool", "c2", "out", false],
  ]);
});

test("an assistant message with nothing to show draws no empty turn", () => {
  assert.deepEqual(walk([{ role: "assistant", content: { content: "" } }, { role: "assistant", content: [] }]), []);
});

test("the user's typed text wins over the context the server prepended", () => {
  // agent/sessions.py turn_display_marks stores display_text beside a first
  // turn whose content carries the orient / episodic block.
  const got = walk([
    { role: "user", content: { content: "[Orient — pre-turn analysis]\n...\n\nfix the bug" }, display_text: "fix the bug", origin: "user" },
  ]);
  assert.deepEqual(got, [["user", "fix the bug"]]);
});

test("a system-origin nudge is a notice, never the user's words", () => {
  const nudge = { role: "user", content: { content: "[Plan reminder — steps you said you'd take: 1. read]" }, origin: "system" };
  assert.deepEqual(walk([nudge]), [["notice", "[Plan reminder — steps you said you'd take: 1. read]"]]);
  assert.deepEqual(walk([nudge], { withNotice: false }), []);  // no callback → dropped, not drawn as user
});

test("only user / assistant / tool roles are drawn", () => {
  assert.deepEqual(walk([{ role: "system", content: "persona" }, { role: "developer", content: "x" }]), []);
});

test("a text block in a user message is drawn beside its tool results", () => {
  const got = walk([{ role: "user", content: [
    { type: "tool_result", tool_use_id: "t1", content: "r" },
    { type: "text", text: "and also this" },
  ] }]);
  assert.deepEqual(got, [["tool", "t1", "r", false], ["user", "and also this"]]);
});

test("OpenAI tool_calls follow the assistant's text", () => {
  const got = walk([{ role: "assistant", content: {
    content: "Let me look.",
    tool_calls: [{ id: "c1", function: { name: "Read", arguments: "{}" } }],
  } }]);
  assert.deepEqual(got, [["assistant", [
    { type: "text", text: "Let me look." },
    { type: "tool_use", id: "c1", name: "Read", input: {} },
  ]]]);
});

test("resultText flattens every shape a tool result arrives in", () => {
  assert.equal(V.resultText("plain"), "plain");
  assert.equal(V.resultText([{ type: "text", text: "a" }, { type: "text", text: "b" }]), "ab");
  assert.equal(V.resultText({ content: [{ type: "text", text: "inner" }] }), "inner");
  assert.equal(V.resultText(null), "");
  assert.equal(V.resultText({ exit: 1 }), '{"exit":1}');
});

test("diffHtml escapes every line and classes it by its prefix", () => {
  const html = V.diffHtml("--- a\n+++ b\n@@ -1 +1 @@\n-<old>\n+<script>x</script>\n ctx");
  assert.ok(html.startsWith('<pre class="diff">'));
  assert.ok(html.includes('<span class="d-hdr">--- a</span>'));
  assert.ok(html.includes('<span class="d-hunk">@@ -1 +1 @@</span>'));
  assert.ok(html.includes('<span class="d-del">-&lt;old&gt;</span>'));
  assert.ok(html.includes('<span class="d-add">+&lt;script&gt;x&lt;/script&gt;</span>'));
  assert.ok(!html.includes("<script>"), "diff content reached the page unescaped");
  assert.equal(V.diffHtml(""), "");
});

test("toolExtrasHtml renders diff, write metadata and exit code, escaped", () => {
  const html = V.toolExtrasHtml({
    diff: "+<b>",
    path: "<p>.md",
    bytes_delta: 12,
    action: "edit",
    exit_code: 2,
    command: "rm <x>",
  });
  assert.ok(html.includes('<span class="d-add">+&lt;b&gt;</span>'));
  assert.ok(html.includes("&lt;p&gt;.md · +12 bytes · edit"));
  assert.ok(html.includes('<span class="tc-exit bad">exit 2</span> <code>rm &lt;x&gt;</code>'));
  assert.ok(V.toolExtrasHtml({ exit_code: 0 }).includes('tc-exit ok'));
  assert.equal(V.toolExtrasHtml(null), "");
});

test("toolExtrasHtml: preview, a shrinking file, and non-numeric fields escaped", () => {
  const html = V.toolExtrasHtml({
    preview: "<i>new</i>",
    path: "a.md",
    bytes_delta: -5,
    replacements: "<2>",
    exit_code: "<9>",
  });
  assert.ok(html.includes('<div class="tc-section-label">preview</div><pre class="diff">&lt;i&gt;new&lt;/i&gt;</pre>'));
  assert.ok(html.includes("a.md · -5 bytes"), "a negative delta must not render as +-5");
  assert.ok(html.includes("&lt;2&gt; replacement(s)"));
  assert.ok(html.includes("exit &lt;9&gt;"));
  assert.ok(!/<2>|<9>/.test(html), "a field value reached the page unescaped");
});

test("footerParts reports what it shows, and never fabricates a cost", () => {
  const f = V.footerParts(
    { prompt_tokens: 1000, completion_tokens: 234, cached_tokens: 500, cost: 0.01234 },
    { elapsedS: 2.25, tools: 1, planMode: true },
  );
  assert.deepEqual(plain(f.parts), ["2.3s", (1234).toLocaleString() + " tokens", "50% cache", "1 tool", "$0.0123", "plan mode"]);
  assert.deepEqual([f.prompt, f.completion, f.cost], [1000, 234, 0.01234]);

  const bare = V.footerParts({ input_tokens: 5, output_tokens: 5, cost: "nope" }, { tools: 2 });
  assert.deepEqual(plain(bare.parts), ["0.0s", "10 tokens", "2 tools"]);
  assert.equal(bare.cost, 0);
});

test("a user turn is replayed with its stored row, so chips can come back", () => {
  // portal draws the attachment names (eos_attached) a reopened chat would
  // otherwise lose; a caller that ignores the second argument is unaffected.
  const seen = [];
  V.walkHistory(
    [{ role: "user", content: "look at this", display_text: "", eos_attached: ["a.png"] },
     { role: "user", content: { content: "and this" }, display_text: "typed words" }],
    { user: (text, m) => seen.push([text, m && plain(m.eos_attached || (m.content || {}).eos_attached || null)]),
      assistant: () => {}, toolResult: () => {} },
  );
  assert.deepEqual(plain(seen), [["look at this", ["a.png"]], ["typed words", null]]);
});

test("fmtCost keeps tiny spend visible without inventing precision", () => {
  assert.equal(V.fmtCost(0), "$0");
  assert.equal(V.fmtCost(0.00001), "<$0.0001");
  assert.equal(V.fmtCost(0.5), "$0.5000");
});
