// portal-recent.js — the sidebar's one "Recent" list (2026-10-03).
//
// The sidebar used to list conversations by BACKEND — Rooms, then Assistant
// chats, then Agent runs — each in its own order, with no dates. On a real
// install that put 39 room threads (newest two months old) above every recent
// chat, listed 42 empty sessions ("New chat", 0 messages), and gave no row a
// time. Which backend a conversation runs on is an implementation detail; when
// it was last touched is not.
//
// So: one list across all three backends, newest ACTIVITY first, grouped
// Today / Yesterday / Last 7 days / Last 30 days / Older, a small glyph for the
// kind and a relative time per row. Empty sessions are hidden (unless pinned or
// open). The first LIMIT rows show; the rest are behind "Show N more".
//
// Activity time per kind: rooms → `last_active` (history mtime, rooms API) or
// `created`; assistant / agent → `last_message` or `created`.
//
// One namespaced global — PortalRecent — per .claude/rules/multi-module-apps.md
// § frontend counterpart. The pure half (items, bucket, group, rel, filter) is
// exported for tests/js/portal_recent.test.mjs; portal-sidebar.js renders.
var PortalRecent = (function () {
    'use strict';

    var LIMIT = 15;
    var GLYPH = { rooms: '\u{1F3E0}', assistant: '\u{1F4AC}', agent: '\u{1F916}' };
    var KIND_LABEL = { rooms: 'Room', assistant: 'Assistant chat', agent: 'Agent run' };
    var BUCKETS = ['Today', 'Yesterday', 'Last 7 days', 'Last 30 days', 'Older'];

    function _ms(iso) {
        var t = iso ? Date.parse(iso) : NaN;
        return isNaN(t) ? 0 : t;
    }

    // Pure. Merge the three backends into [{tid, name, kind, ts, empty}].
    // `excluded` is a set of thread ids shown elsewhere (in a room folder, or
    // chat-first's own list); `keep` is a set that is never hidden as empty.
    function items(rooms, asst, agent, opts) {
        opts = opts || {};
        var excluded = opts.excluded || {};
        var keep = opts.keep || {};
        var out = [];
        (rooms || []).forEach(function (r) {
            if (!r || !r.id || excluded[r.id]) return;
            out.push({ tid: r.id, name: r.name || r.id, kind: 'rooms',
                       ts: _ms(r.last_active) || _ms(r.created), empty: false });
        });
        [['assistant', 'asst:', asst, 'Assistant chat'], ['agent', 'agent:', agent, 'Agent run']].forEach(function (k) {
            (k[2] || []).forEach(function (s) {
                if (!s || !s.id) return;
                var tid = k[1] + s.id;
                if (excluded[tid]) return;
                // message_count absent (an older backend) is not evidence of empty.
                var empty = s.message_count === 0 && !keep[tid];
                out.push({ tid: tid, name: s.name || k[3], kind: k[0],
                           ts: _ms(s.last_message) || _ms(s.created), empty: empty });
            });
        });
        return out.filter(function (x) { return !x.empty; })
                  .sort(function (a, b) { return b.ts - a.ts; });
    }

    // Local calendar day boundaries, not 24h windows: "Yesterday" means the
    // previous date on the wall, which is how people read a sidebar.
    function _dayStart(ms) { var d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); }

    function bucket(ts, now) {
        if (!ts) return 'Older';
        var days = Math.round((_dayStart(now) - _dayStart(ts)) / 86400000);
        if (days <= 0) return 'Today';
        if (days === 1) return 'Yesterday';
        if (days < 7) return 'Last 7 days';
        if (days < 30) return 'Last 30 days';
        return 'Older';
    }

    // Pure. [{label, rows}] in bucket order, empty buckets dropped, plus how
    // many rows the limit held back.
    function group(list, now, limit) {
        var shown = limit ? list.slice(0, limit) : list;
        var by = {};
        shown.forEach(function (x) { (by[bucket(x.ts, now)] = by[bucket(x.ts, now)] || []).push(x); });
        return {
            groups: BUCKETS.filter(function (b) { return by[b]; }).map(function (b) { return { label: b, rows: by[b] }; }),
            hidden: list.length - shown.length,
        };
    }

    // Pure. A short relative time: "now", "12m", "5h", "3d", then a date.
    function rel(ts, now) {
        if (!ts) return '';
        var m = Math.floor((now - ts) / 60000);
        if (m < 1) return 'now';
        if (m < 60) return m + 'm';
        var h = Math.floor(m / 60);
        if (h < 24) return h + 'h';
        var d = Math.floor(h / 24);
        if (d < 7) return d + 'd';
        var dt = new Date(ts), nw = new Date(now);
        var opts = { month: 'short', day: 'numeric' };
        if (dt.getFullYear() !== nw.getFullYear()) opts.year = 'numeric';
        return dt.toLocaleDateString(undefined, opts);
    }

    // Pure. Case-insensitive name filter for the sidebar search box.
    function filter(list, q) {
        q = String(q || '').trim().toLowerCase();
        if (!q) return list;
        return list.filter(function (x) { return String(x.name).toLowerCase().indexOf(q) >= 0; });
    }

    return {
        LIMIT: LIMIT, GLYPH: GLYPH, KIND_LABEL: KIND_LABEL,
        items: items, bucket: bucket, group: group, rel: rel, filter: filter,
    };
})();
