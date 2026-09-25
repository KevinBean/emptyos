// portal-artifacts.js — the side panel for artifacts a chat produced (B4).
//
// When an answer is a THING rather than prose — a chart, a diagram, an
// interactive explainer — the model writes a standalone page through the
// CreateArtifact tool, viz stores it, and it opens here beside the
// conversation. Revising it keeps the previous render (viz's version ring),
// so "no, make it blue" is undoable.
//
// Two ways the panel fills:
//   live     — an `agent:tool_result` carrying display.artifact (onToolResult)
//   reopened — GET /agent/api/sessions/<sid>/artifacts (openFor), because
//              history replay carries no display payload at all, so a panel
//              driven only by the live event would vanish on refresh.
//
// The preview is EOS_UI.sandboxFrame: an opaque origin, so model-written
// script can never read the session cookie or call /api/ as the user. The
// Source tab reads /viz/api/source/<id>, which is served as text/plain for
// the same reason — a second HTML route would be a second way to run it.
//
// One namespaced global — PortalArtifacts — per .claude/rules/multi-module-apps.md
// § frontend counterpart. Loads before portal.js. The pure helpers at the top
// are exported for tests/js/portal_artifacts.test.mjs.
var PortalArtifacts = (function () {
    'use strict';

    var S = { on: false, sid: '', items: [], openId: '', tab: 'preview', versions: [], version: '' };

    // ── Pure helpers (node-tested) ─────────────────────────────────────

    // Merge one artifact into the list, newest-touched first. A revision moves
    // the existing entry rather than adding a second — the same rule the
    // server's index row follows, so the live list and a reload agree.
    function mergeArtifact(items, art) {
        if (!art || !art.id) return items;
        var rest = (items || []).filter(function (a) { return a.id !== art.id; });
        var prev = (items || []).filter(function (a) { return a.id === art.id; })[0] || {};
        return [{
            id: art.id,
            title: art.title || prev.title || 'Untitled artifact',
            shape: art.shape || prev.shape || '',
            url: art.url || '/viz/api/html/' + encodeURIComponent(art.id),
            source_url: art.source_url || '/viz/api/source/' + encodeURIComponent(art.id),
            open_url: art.open_url || '/viz/#' + encodeURIComponent(art.id)
        }].concat(rest);
    }

    // The artifact a tool result announced, or null for every other tool.
    function artifactOf(display) {
        var art = display && display.artifact;
        return (art && art.id) ? art : null;
    }

    function labelFor(art) {
        if (!art) return '';
        return art.shape ? art.title + ' · ' + art.shape : art.title;
    }

    // ── DOM ─────────────────────────────────────────────────────────────

    function _pane() { return document.getElementById('portal-artifact'); }
    function _main() { return document.querySelector('.portal-main'); }

    function _current() {
        var id = S.openId;
        return S.items.filter(function (a) { return a.id === id; })[0] || null;
    }

    function close() {
        S.openId = '';
        var m = _main();
        if (m) m.classList.remove('with-artifact');
        render();
    }

    function open(id) {
        S.openId = id;
        S.tab = 'preview';
        S.versions = [];
        S.version = '';
        var m = _main();
        if (m) m.classList.add('with-artifact');
        render();
        _loadVersions(id);
    }

    function _loadVersions(id) {
        EOS.apiSafe('/viz/api/versions/' + encodeURIComponent(id)).then(function (d) {
            if (S.openId !== id) return;                 // the user moved on
            // A failed fetch leaves the picker as it was rather than emptying
            // it: an empty picker means "this artifact has never been revised",
            // which is a claim, not the absence of an answer.
            if (d && d.error) { EOS.toast('Could not list earlier versions', false); return; }
            S.versions = (d && d.versions) || [];
            render();
        });
    }

    function render() {
        var pane = _pane();
        if (!pane) return;
        _renderChip();   // before the early return: a CLOSED panel still needs
                         // its chip to say whether there is anything to reopen
        var art = _current();
        if (!art) { pane.innerHTML = ''; return; }

        var tabs = ['preview', 'source'].map(function (t) {
            return '<button type="button" class="portal-art-tab' + (S.tab === t ? ' active' : '') +
                '" data-tab="' + t + '">' + (t === 'preview' ? 'Preview' : 'Source') + '</button>';
        }).join('');

        var picker = '';
        if (S.versions.length) {
            picker = '<select class="portal-art-versions" title="Earlier renders of this artifact">' +
                '<option value=""' + (S.version ? '' : ' selected') + '>Current</option>' +
                S.versions.map(function (v) {
                    var n = String(v.n);
                    return '<option value="' + EOS_UI.escAttr(n) + '"' + (S.version === n ? ' selected' : '') +
                        '>v' + EOS_UI.esc(n) + (v.prompt ? ' · ' + EOS_UI.esc(v.prompt) : '') + '</option>';
                }).join('') + '</select>';
        }

        var others = S.items.length > 1
            ? '<select class="portal-art-list" title="Artifacts in this chat">' +
                S.items.map(function (a) {
                    return '<option value="' + EOS_UI.escAttr(a.id) + '"' +
                        (a.id === art.id ? ' selected' : '') + '>' + EOS_UI.esc(a.title) + '</option>';
                }).join('') + '</select>'
            : '';

        pane.innerHTML =
            '<div class="portal-chat-header">' +
                '<span class="portal-chat-name" title="' + EOS_UI.escAttr(labelFor(art)) + '">' +
                    EOS_UI.esc(art.title) + '</span>' +
                others +
                '<button class="portal-chat-close" data-act="close" title="Close the panel">&times;</button>' +
            '</div>' +
            '<div class="portal-art-bar">' + tabs + picker +
                '<span class="portal-art-spacer"></span>' +
                '<button type="button" class="eos-btn-sm" data-act="copy">Copy</button>' +
                '<a class="eos-btn-sm" href="' + EOS_UI.escAttr(art.open_url) + '" target="_blank" rel="noopener">Open in Viz</a>' +
            '</div>' +
            '<div class="portal-art-body" id="portal-art-body"></div>';
        _renderBody(art);
    }

    function _renderBody(art) {
        var body = document.getElementById('portal-art-body');
        if (!body) return;
        var v = _selectedVersion();
        if (S.tab === 'source') {
            body.innerHTML = '<pre class="portal-art-source">Loading&hellip;</pre>';
            // ?v= so Source shows the markup of whatever Preview is showing;
            // without it, picking an old version left the two panes describing
            // different documents with nothing saying so.
            _fetchText(art.source_url + (v ? '?v=' + encodeURIComponent(v) : '')).then(function (text) {
                var pre = body.querySelector('.portal-art-source');
                if (pre) pre.textContent = text;
            }, function (err) {
                // A failure is not an empty document — say so in the failure
                // vocabulary, never in the one that means "nothing here".
                body.innerHTML = EOS_UI.errorState('Could not read the artifact source (' + err.message + ').');
            });
            return;
        }
        var src = v
            ? '/viz/api/versions/' + encodeURIComponent(art.id) + '/' + encodeURIComponent(v)
            : art.url;
        body.innerHTML = EOS_UI.sandboxFrame({
            src: src + (src.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now(),
            title: art.title,
            className: 'portal-art-frame',
            height: 0,          // overridden by the inline height:100% below
            style: 'height:100%;border-radius:0;'
        });
    }

    // The source endpoint answers text/plain, so EOS.apiSafe (which always
    // parses JSON, and would hand back an empty object) is the wrong tool.
    function _fetchText(url) {
        return fetch(url).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.text();
        });
    }

    // The chosen version is STATE, not a DOM read. It used to be read back off
    // the <select>, so any re-render — a versions fetch resolving, a tab
    // switch, the next tool result — rebuilt the picker at "Current" and
    // silently put the user back on the live render while they were looking at
    // an old one.
    function _selectedVersion() { return S.version; }

    function _onClick(ev) {
        var t = ev.target.closest('[data-act], [data-tab]');
        if (!t) return;
        var tab = t.getAttribute('data-tab');
        if (tab) { S.tab = tab; render(); return; }
        var act = t.getAttribute('data-act');
        if (act === 'close') close();
        if (act === 'copy') _copySource();
    }

    function _copySource() {
        var art = _current();
        if (!art) return;
        _fetchText(art.source_url).then(function (text) {
            return navigator.clipboard.writeText(text);
        }).then(function () {
            EOS.toast('Artifact HTML copied', true);
        }, function () { EOS.toast('Could not copy the artifact', false); });
    }

    function _onChange(ev) {
        if (ev.target.classList.contains('portal-art-list')) { open(ev.target.value); return; }
        if (ev.target.classList.contains('portal-art-versions')) {
            S.version = ev.target.value || '';
            var art = _current();
            if (art) _renderBody(art);
        }
    }

    // ── Entry points ────────────────────────────────────────────────────

    // A tool just finished. Only CreateArtifact carries display.artifact; every
    // other tool result passes straight through.
    function onToolResult(display) {
        if (!S.on) return false;
        var art = artifactOf(display);
        if (!art) return false;
        S.items = mergeArtifact(S.items, art);
        open(art.id);
        return true;
    }

    // A thread was opened (or reopened). The index is what survives a reload.
    function openFor(sid) {
        if (!S.on) return;
        // Same thread AND we already have its index: nothing to do. Without the
        // second half, a chat whose first fetch raced an in-flight turn would
        // never try again.
        if (sid && sid === S.sid && S.items.length) return;
        S.sid = sid;
        S.items = [];
        close();
        if (!sid) return;
        EOS.apiSafe('/agent/api/sessions/' + encodeURIComponent(sid) + '/artifacts').then(function (d) {
            if (S.sid !== sid) return;                    // the user switched threads
            S.items = (d && d.artifacts) || [];
            // Reopening does NOT force the panel open: a chat that made an
            // artifact three days ago should come back as a conversation, with
            // the artifact one click away (the header chip).
            render();
        });
    }

    // A small button in the chat header, so a closed panel is still findable.
    function _renderChip() {
        var chip = document.getElementById('portal-art-chip');
        if (!chip) return;
        chip.style.display = S.items.length ? '' : 'none';
        chip.textContent = S.items.length === 1
            ? '◱ Artifact'
            : '◱ Artifacts (' + S.items.length + ')';
        chip.onclick = function () {
            if (S.openId) close();
            else if (S.items.length) open(S.items[0].id);
        };
    }

    // Two flags, both required, and they are NOT interchangeable:
    // [apps.agent] feature.artifacts.enabled decides whether the tool exists
    // at all (cfg.artifacts), and [apps.portal] feature.chat-first.enabled
    // decides whether this chat surface exists to show it. init() is reached
    // from PortalChat.init, which already returned if chat-first is off — so
    // the PortalChat check here is belt-and-braces, not the portal gate.
    // With only the agent flag on, the tool still runs on /agent/ and eos chat,
    // which have no panel; that is why its result text carries a /viz/ link
    // instead of promising one.
    function init(cfg) {
        S.on = !!(cfg && cfg.artifacts) && typeof PortalChat !== 'undefined' && PortalChat.isOn();
        if (!S.on) return;
        var pane = _pane();
        if (pane) {
            pane.addEventListener('click', _onClick);
            pane.addEventListener('change', _onChange);
        }
    }

    return {
        init: init,
        openFor: openFor,
        onToolResult: onToolResult,
        close: close,
        isOn: function () { return S.on; },
        // pure — exported for tests/js/portal_artifacts.test.mjs
        mergeArtifact: mergeArtifact,
        artifactOf: artifactOf,
        labelFor: labelFor,
        _state: function () { return S; }
    };
})();
