// portal-home.js — the home board under the composer: the hub's companion
// lanes (greeting · Next move · Now · Today · Continue) and its panel
// contributions (Explore), painted by the shared /static/eos-hub-renderers.js
// from the same /hub/api/* payloads /hub/ reads. Portal became the landing
// surface on 2026-10-03; the hub app stays the aggregator the 90-odd
// contributing manifests declare into, so nothing here knows which apps
// contribute what.
//
// Fail-soft throughout: a fetch that fails leaves its lane empty, and the
// board stays hidden until some lane has actually painted something. On a
// normal install that is almost immediately — the Today lane paints its
// chips whenever /hub/api/digest answers — so it is an "is the hub there?"
// test, not a "has the user got data?" one. Quick-entry mode (?quick=1) never
// loads it: a hotkey composer is not a dashboard.
//
// One namespaced global — PortalHome — per .claude/rules/multi-module-apps.md
// § frontend counterpart. Loads before portal.js, whose boot calls init().
var PortalHome = (function () {
    'use strict';

    function $(id) { return document.getElementById(id); }

    // GET → parsed JSON, or null on any failure (network, non-2xx, bad JSON).
    function grab(url) {
        return fetch(url)
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; });
    }

    // Did any of these elements end up with content? What a lane loader
    // reports, so the board shows on what was painted, not on what answered.
    function painted() {
        for (var i = 0; i < arguments.length; i++) {
            var el = $(arguments[i]);
            if (el && el.innerHTML.trim()) return 1;
        }
        return 0;
    }

    function lanes() { return EOS_HUB_RENDERERS.lanes; }

    // The Next card, without its own greeting — the eyebrow above the
    // composer carries it. One call site for both the template move and the
    // Aura swap, so the two paints cannot disagree about it.
    function paintNext(d) { lanes().next($('portal-next'), d, { greeting: false }); }

    // ② ③ ④ — one GET /hub/api/digest paints the greeting and three lanes at
    // once (template Next move, no LLM); the Aura move hydrates separately.
    async function loadDigest() {
        var d = await grab('/hub/api/digest');
        if (!d) return 0;
        var L = lanes();
        L.greeting($('portal-greeting'), d);
        paintNext(d);
        L.now($('portal-now'), d.now);
        L.today($('portal-today'), d.today);
        loadNextMove(d);
        return painted('portal-next', 'portal-now', 'portal-today');
    }

    // Swap the template Next move for the Aura-synthesized one once it lands
    // (cached server-side per cadence; a cold call can take ~20s).
    async function loadNextMove(d) {
        var j = await grab('/hub/api/next-move');
        if (j && j.move && j.move.title) {
            d.next_move = j.move;
            paintNext(d);
        }
    }

    // ⑤ — recently touched vault notes.
    async function loadContinue() {
        var items = await grab('/app-analytics/api/vault/recent?limit=6');
        lanes().continueNotes($('portal-continue-notes'), items);
        return painted('portal-continue-notes');
    }

    // ⑥ — the panel aggregator, through the shared visibility policy
    // (ambient dropped, pinned-refs kept, quick-add forms into the quick zone).
    async function loadExplore() {
        var json = await grab('/hub/api/panels');
        if (!json) return 0;
        var R = EOS_HUB_RENDERERS;
        var split = R.visibleBlocks(json.blocks || []);
        // hero-weather fills #hub-hero-weather as a side effect; never a block.
        split.weather.forEach(function (b) { try { R.map['hero-weather'](b.items, b); } catch (e) {} });
        var qEl = $('portal-quick');
        if (qEl) qEl.innerHTML = split.quick.map(R.renderBlock).join('');
        var el = $('portal-explore');
        if (el) {
            el.innerHTML = split.rest.length
                ? ('<div class="hub-lane-head hub-explore-head">Explore</div>' + split.rest.map(R.renderBlock).join(''))
                : '';
        }
        return painted('portal-quick', 'portal-explore');
    }

    function show() {
        var board = $('portal-board');
        if (board) board.style.display = '';
    }

    // Each lane reveals the board as soon as IT has painted. Waiting on all
    // of them (Promise.all) held the digest lanes back behind /hub/api/panels —
    // ~3s on a full install, far longer cold. The board only ever turns on.
    function load() {
        [loadDigest, loadContinue, loadExplore].forEach(function (fn) {
            fn().then(function (got) { if (got) show(); });
        });
    }

    function init() {
        if (typeof EOS_HUB_RENDERERS === 'undefined' || !$('portal-board')) return;
        if (document.documentElement.classList.contains('portal-quick-mode')) return;
        // Top-align the hero NOW, before any lane answers. Doing it when the
        // board appeared moved the composer ~120px up under a user already
        // aiming at it (measured 312→192px at 390×844). A board tall enough to
        // scroll must start at the top anyway — a centred flex column clips
        // its own head once its content overflows.
        var hero = $('portal-hero');
        if (hero) hero.classList.add('has-board');
        // A hub quick-add form (expense log…) refreshes its host after a save
        // through window.refreshAll — the hub page's name for "repaint the
        // board" (eos-hub-renderers.js _refreshAfterAdd). Portal has no other
        // refreshAll, so the board answers it.
        if (typeof window.refreshAll !== 'function') window.refreshAll = load;
        load();
    }

    return { init: init };
})();
