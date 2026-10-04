// portal-launch.js — opening an app from the hero composer, the one thing the
// hub's search bar did that portal's chat box did not (2026-10-03, when portal
// became the landing page).
//
// The rule is deliberately narrower than the hub's. The hub's bar is a search
// box, so `exp` + Enter opening Expense surprises nobody; here the same Enter
// starts a conversation, and a one-word message is a normal thing to send.
// Measured on the real app list: of 60 common one-word chat messages, 9 are
// the PREFIX of an app id or name ("no", "test", "plan", "fix", "read"…), so
// prefix-on-Enter would hijack ordinary replies. Hence:
//
//   * the WHOLE text is one app's exact id or name (one ASCII word — the id
//     shape; a non-ASCII or multi-word name never matches) → the Enter KEY
//     opens that app, and a hint under the box says so before it is pressed.
//     The Send button always sends: that is how "focus" goes out as a chat;
//   * it is only a prefix → Enter still chats; up to three matching apps are
//     offered as buttons under the box.
//
// Exact app names that are ordinary words do exist (search, focus, task, run,
// note…: 17 of 80 common one-word messages, review 2026-10-03), so exact-on-
// Enter was checked against history: 0 of 147 past threads (rooms, agent,
// assistant) began with one. It applies only where a new conversation starts
// with no other declared intent:
//   * the Think verb, in the hero composer (inside a thread the box is a reply);
//   * not the Agent backend — "run", "tests", "release" are instructions there;
//   * not while a room or project is pending ("new thread in…" is a stated
//     intent to talk);
//   * never in quick-entry mode (the hotkey window has no app pane).
//
// One namespaced global — PortalLaunch — per .claude/rules/multi-module-apps.md
// § frontend counterpart. Loads before portal.js. `match` is pure and exported
// for tests/js/portal_launch.test.mjs; the rest reads portal.js's globals
// (APP_CATALOG, ACTIVE_VERB, _navToApp) at call time, never at load.
var PortalLaunch = (function () {
    'use strict';

    // One token of the shape an app id has. Punctuation ("task?") or a second
    // word is how a user says "this is a message, not an app".
    var TOKEN = /^[a-z0-9][a-z0-9-]*$/;
    var MAX_SUGGESTIONS = 3;
    var ranking = {};

    function norm(s) { return String(s == null ? '' : s).trim().toLowerCase(); }

    // Pure. catalog: {id: name}; rank: {id: score}. Returns
    // {exact: {id, name} | null, suggestions: [{id, name}]}.
    function match(text, catalog, rank) {
        var out = { exact: null, suggestions: [] };
        var t = norm(text);
        if (t.length < 2 || !TOKEN.test(t)) return out;
        rank = rank || {};
        var ids = Object.keys(catalog || {});
        var byRank = function (a, b) {
            return ((rank[b] || 0) - (rank[a] || 0)) || (a.length - b.length) || (a < b ? -1 : a > b ? 1 : 0);
        };
        var entry = function (id) { return { id: id, name: catalog[id] || id }; };

        // An id is unique; a display name may not be, so an id wins over a name.
        if (ids.indexOf(t) >= 0) { out.exact = entry(t); return out; }
        var named = ids.filter(function (id) { return norm(catalog[id]) === t; }).sort(byRank);
        if (named.length) { out.exact = entry(named[0]); return out; }

        out.suggestions = ids.filter(function (id) {
            return id.indexOf(t) === 0 || norm(catalog[id]).indexOf(t) === 0;
        }).sort(byRank).slice(0, MAX_SUGGESTIONS).map(entry);
        return out;
    }

    function $(id) { return document.getElementById(id); }

    function active() {
        if (typeof ACTIVE_VERB === 'undefined' || ACTIVE_VERB !== 'think') return false;
        if (document.documentElement.classList.contains('portal-quick-mode')) return false;
        if (typeof ACTIVE_BACKEND !== 'undefined' && ACTIVE_BACKEND === 'agent') return false;
        if (typeof _pendingFolderId !== 'undefined' && _pendingFolderId) return false;
        if (typeof PortalProjects !== 'undefined' && PortalProjects.pendingProject
            && PortalProjects.pendingProject()) return false;
        return true;
    }

    function current() {
        var input = $('hero-input');
        if (!input || !active()) return { exact: null, suggestions: [] };
        return match(input.value, typeof APP_CATALOG !== 'undefined' ? APP_CATALOG : {}, ranking);
    }

    function open(id) {
        var input = $('hero-input');
        if (input) { input.value = ''; input.dispatchEvent(new Event('input')); }
        _navToApp(id);
    }

    function render() {
        var el = $('hero-launch');
        if (!el) return;
        var m = current();
        if (m.exact) {
            el.innerHTML =
                '<button type="button" class="portal-launch-btn primary" data-app="' + escAttr(m.exact.id) + '"' +
                    ' title="Open ' + escAttr(m.exact.name) + ' (Enter)">' +
                    '&#x21B5; opens <strong>' + esc(m.exact.name) + '</strong></button>' +
                '<span class="portal-launch-note">Send to chat it instead</span>';
        } else if (m.suggestions.length) {
            el.innerHTML = '<span class="portal-launch-note">Open</span>' + m.suggestions.map(function (a) {
                return '<button type="button" class="portal-launch-btn" data-app="' + escAttr(a.id) + '"' +
                    ' title="Open ' + escAttr(a.name) + '">' + esc(a.name) + '</button>';
            }).join('') + '<span class="portal-launch-note">&#x21B5; still sends a chat</span>';
        } else {
            el.innerHTML = '';
        }
    }

    // submitFromHero asks first: true means Enter opened an app and the chat
    // must not start. Attachments make it a chat whatever the words say.
    function intercept() {
        var m = current();
        if (!m.exact) return false;
        open(m.exact.id);
        return true;
    }

    function init() {
        var input = $('hero-input');
        var el = $('hero-launch');
        if (!input || !el) return;
        input.addEventListener('input', render);
        el.addEventListener('click', function (e) {
            var b = e.target.closest ? e.target.closest('[data-app]') : null;
            if (b) open(b.getAttribute('data-app'));
        });
        // Ties between several prefix matches go to the app used most lately
        // (app-analytics' recency+frequency score). Not in quick mode, which
        // never shows the hint.
        if (document.documentElement.classList.contains('portal-quick-mode')) return;
        if (typeof EOS !== 'undefined' && EOS._fetchAppRanking) {
            EOS._fetchAppRanking().then(function (r) { ranking = r || {}; render(); });
        }
    }

    return { match: match, init: init, render: render, intercept: intercept };
})();
