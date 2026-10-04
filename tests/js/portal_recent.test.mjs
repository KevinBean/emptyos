/* portal-recent.js — the sidebar's one Recent list.
 *
 * What it replaced listed conversations by backend (Rooms, then Assistant,
 * then Agent), each in its own order, with no times and every empty session
 * included. These pin the three properties that fixed it: one list ordered by
 * last ACTIVITY across backends, empty sessions hidden (unless pinned/open),
 * and day buckets on the LOCAL calendar.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStatic } from "./shim.mjs";

const { PortalRecent: R } = loadStatic("apps/public/standard/portal/pages/portal-recent.js");
const tids = (list) => Array.from(list, (x) => x.tid);

// A fixed "now": local noon, so day arithmetic is unambiguous.
const NOW = new Date(2026, 9, 3, 12, 0, 0).getTime();
const at = (daysAgo, hour = 12) => {
  const d = new Date(NOW);
  d.setDate(d.getDate() - daysAgo);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
};

test("the module loaded with its pure helpers", () => {
  for (const k of ["items", "bucket", "group", "rel", "filter"]) assert.equal(typeof R[k], "function", k);
});

test("one list across backends, newest ACTIVITY first", () => {
  const list = R.items(
    [{ id: "r-old", name: "Old room", created: at(60) },
     { id: "r-used", name: "Used room", created: at(90), last_active: at(0, 9) }],
    [{ id: "a1", name: "Asst", created: at(40), last_message: at(1), message_count: 4 }],
    [{ id: "g1", name: "Agent", created: at(2), last_message: "", message_count: 2 }],
  );
  // r-used was created first but used today; activity, not creation, orders.
  assert.deepEqual(tids(list), ["r-used", "asst:a1", "agent:g1", "r-old"]);
  assert.deepEqual(Array.from(list, (x) => x.kind), ["rooms", "assistant", "agent", "rooms"]);
});

test("empty sessions are hidden unless pinned or open; unknown counts are kept", () => {
  const asst = [
    { id: "e", name: "New chat", created: at(1), message_count: 0 },
    { id: "k", name: "Kept", created: at(1), message_count: 0 },
    { id: "u", name: "Old backend", created: at(1) },          // no message_count at all
  ];
  const list = R.items([], asst, [], { keep: { "asst:k": 1 } });
  assert.deepEqual(tids(list).sort(), ["asst:k", "asst:u"]);
  // the agent backend follows the same rule
  const agent = R.items([], [], [{ id: "x", created: at(1), message_count: 0 }, { id: "y", created: at(1), message_count: 5 }]);
  assert.deepEqual(tids(agent), ["agent:y"]);
});

test("threads listed elsewhere (a room folder, chat-first) are excluded", () => {
  const list = R.items([{ id: "r1", created: at(1) }, { id: "r2", created: at(1) }], [],
    [{ id: "c", created: at(1), message_count: 3 }], { excluded: { r1: 1, "agent:c": 1 } });
  assert.deepEqual(tids(list), ["r2"]);
});

test("buckets follow the local calendar day, not 24-hour windows", () => {
  assert.equal(R.bucket(Date.parse(at(0, 0)), NOW), "Today");          // just after midnight
  assert.equal(R.bucket(Date.parse(at(1, 23)), NOW), "Yesterday");     // 13h ago, previous date
  assert.equal(R.bucket(Date.parse(at(2)), NOW), "Last 7 days");
  assert.equal(R.bucket(Date.parse(at(6)), NOW), "Last 7 days");
  assert.equal(R.bucket(Date.parse(at(7)), NOW), "Last 30 days");
  assert.equal(R.bucket(Date.parse(at(29)), NOW), "Last 30 days");
  assert.equal(R.bucket(Date.parse(at(30)), NOW), "Older");
  assert.equal(R.bucket(0, NOW), "Older");
});

test("group keeps bucket order, drops empty buckets, and counts what the limit held back", () => {
  const list = R.items([], [], [0, 0, 1, 40, 41].map((d, i) => ({ id: "s" + i, created: at(d), message_count: 1 })));
  const g = R.group(list, NOW, 3);
  assert.deepEqual(Array.from(g.groups, (x) => x.label), ["Today", "Yesterday"]);
  assert.deepEqual(Array.from(g.groups, (x) => x.rows.length), [2, 1]);
  assert.equal(g.hidden, 2);
  const all = R.group(list, NOW, 0);
  assert.equal(all.hidden, 0);
  assert.deepEqual(Array.from(all.groups, (x) => x.label), ["Today", "Yesterday", "Older"]);
});

test("relative times: minutes, hours, days, then a date", () => {
  const m = 60000;
  assert.equal(R.rel(NOW - 20 * 1000, NOW), "now");
  assert.equal(R.rel(NOW - 12 * m, NOW), "12m");
  assert.equal(R.rel(NOW - 5 * 60 * m, NOW), "5h");
  assert.equal(R.rel(NOW - 3 * 1440 * m, NOW), "3d");
  // A calendar date from a week on — "Aug 24" in en; month + day, no year this year.
  const d40 = new Date(Date.parse(at(40)));
  assert.equal(R.rel(d40.getTime(), NOW), d40.toLocaleDateString(undefined, { month: "short", day: "numeric" }));
  const lastYear = new Date(2025, 4, 1).getTime();
  assert.match(R.rel(lastYear, NOW), /2025/, "another year shows the year");
  assert.equal(R.rel(0, NOW), "");
});

test("filter matches names case-insensitively; blank returns everything", () => {
  const list = [{ tid: "1", name: "Cable sizing" }, { tid: "2", name: "Travel budget" }];
  assert.deepEqual(tids(R.filter(list, "CABLE")), ["1"]);
  assert.equal(R.filter(list, "  ").length, 2);
  assert.equal(R.filter(list, "zzz").length, 0);
});
