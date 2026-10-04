// portal-connectors.js — MCP connectors in the chat home (desktop GUI B5).
//
// A connector is an external MCP server whose tools the model can call. Two
// surfaces, because they answer different questions:
//
//   the chip   — "which connectors may THIS chat use?" (per-chat, the session's
//                `connectors` column, read by profiles.select_session_tools)
//   the panel  — "which connectors does this machine have?" (add, connect,
//                disconnect, remove)
//
// Every add is propose → confirm, and the page cannot assert its own approval:
// the server holds the row and returns a one-shot token, and confirming sends
// back only that token. So the card and the write are one object, not two
// inputs that happen to agree.
//
// A literal secret comes back masked — in headers, env, argv and a URL's query
// string — while a `${NAME}` reference comes back as written, because the
// variable's name is not the secret and is the one thing the user must check.
//
// One namespaced global — PortalConnectors — per .claude/rules/multi-module-apps.md
// § frontend counterpart. Loads before portal.js. The pure helpers at the top
// are exported for tests/js/portal_connectors.test.mjs.
var PortalConnectors = (function () {
    'use strict';

    var S = { on: false, loaded: false, rows: [], error: '' };

    // ── Pure helpers (node-tested) ─────────────────────────────────────

    // A session's stored `connectors` column ("a,b") ↔ a list. Kept here so
    // the page and the server agree on one spelling of the empty case: "" is
    // no connectors, never [""].
    function parseEnabled(value) {
        return String(value || '').split(',').map(function (s) { return s.trim(); })
            .filter(Boolean);
    }

    function joinEnabled(list) {
        var seen = {};
        return (list || []).map(function (s) { return String(s).trim(); })
            .filter(function (s) { return s && !seen[s] && (seen[s] = 1); })
            .join(',');
    }

    // The chip's label. A count, not a list: a chat with four connectors would
    // otherwise push the model chip off the header.
    function chipLabel(enabled, total) {
        var n = (enabled || []).length;
        if (!total) return '';
        return n ? '⚭ MCP ' + n : '⚭ MCP';
    }

    // One row's status line. `connected` is about the machine, `enabled` about
    // this chat — a connector can be connected and not offered to this chat,
    // and the two must not be shown as one thing.
    function rowStatus(row, enabled) {
        if (!row) return '';
        var parts = [row.transport === 'http' ? 'http' : 'stdio'];
        parts.push(row.connected ? (row.tools || []).length + ' tools' : 'not connected');
        if (row.source === 'config') parts.push('from emptyos.toml');
        if (enabled) parts.push('on in this chat');
        return parts.join(' · ');
    }

    // ── Data ────────────────────────────────────────────────────────────

    function load() {
        return EOS.apiSafe('/agent/api/connectors').then(function (d) {
            S.loaded = true;
            if (!d || d.error) { S.error = (d && d.error) || 'no response'; S.rows = []; return S.rows; }
            S.error = '';
            S.rows = d.connectors || [];
            return S.rows;
        });
    }

    function _enabledFor(agent) {
        return parseEnabled(agent && agent._connectors);
    }

    // ── The per-chat chip ───────────────────────────────────────────────

    function decorateChip(agent) {
        var chip = document.getElementById('portal-chat-mcp');
        if (!chip) return;
        var isChat = !!(S.on && agent && agent._backend === 'agent' && agent._profile === 'chat');
        if (!isChat || !S.rows.length) { chip.style.display = 'none'; return; }
        chip.style.display = '';
        chip.textContent = chipLabel(_enabledFor(agent), S.rows.length);
        chip.title = 'Which connectors this chat may use';
        chip.onclick = function () { pickForChat(agent); };
    }

    // Which connectors this chat may use. Shown only for a chat profile, which
    // is the only one that reads the column — /agent/ and `eos chat` take the
    // whole registry, so for them connecting IS the decision.
    function pickForChat(agent) {
        if (!agent || !agent._sid) return;
        var enabled = _enabledFor(agent);
        var rows = S.rows.map(function (r) {
            return {
                label: r.id,
                sub: rowStatus(r, enabled.indexOf(r.id) >= 0),
                icon: enabled.indexOf(r.id) >= 0 ? '✓' : '',
                current: enabled.indexOf(r.id) >= 0,
                value: r.id
            };
        });
        rows.push({ label: 'Manage connectors…', sub: 'add, connect or remove', value: '__manage' });
        EOS_UI.choiceMenu({
            title: 'Connectors for this chat',
            rows: rows,
            // choiceMenu calls onPick(value, row) — the VALUE first. Reading
            // `.value` off the first argument silently did nothing.
            onPick: function (value) {
                if (value === '__manage') { openPanel(); return; }
                var next = enabled.slice();
                var i = next.indexOf(value);
                if (i >= 0) next.splice(i, 1); else next.push(value);
                _saveForChat(agent, next);
            }
        });
    }

    function _saveForChat(agent, list) {
        EOS.apiSafe('/agent/api/sessions/' + encodeURIComponent(agent._sid) + '/connectors', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ connectors: list })
        }).then(function (d) {
            if (!d || d.error) { EOS.toast((d && d.error) || 'Could not save', false); return; }
            agent._connectors = joinEnabled(d.connectors || []);
            decorateChip(agent);
            // The tool set is chosen per TURN from the session row, so the next
            // message already sees the change — no reconnect, nothing to restart.
            EOS.toast(d.connectors.length
                ? 'This chat can use: ' + d.connectors.join(', ')
                : 'No connectors in this chat', true);
        });
    }

    // ── The manage panel ────────────────────────────────────────────────

    function openPanel() {
        load().then(function () {
            EOS_UI.modal({ title: 'Connectors', width: '560px', body: _panelHtml() });
            setTimeout(_bindPanel, 0);
        });
    }

    function _panelHtml() {
        // errorState takes an OPTIONS object — a bare string is truthy, so
        // `opts.message` reads undefined and the user sees the generic
        // "Something went wrong" instead of the one line that matters.
        if (S.error) return EOS_UI.errorState({ message: 'Could not load connectors: ' + S.error });
        var rows = S.rows.map(function (r) {
            var acts = [];
            if (r.connected) acts.push('<button type="button" class="eos-btn-sm" data-act="disconnect" data-id="' + EOS_UI.escAttr(r.id) + '">Disconnect</button>');
            else acts.push('<button type="button" class="eos-btn-sm" data-act="connect" data-id="' + EOS_UI.escAttr(r.id) + '">Connect</button>');
            if (r.source !== 'config') acts.push('<button type="button" class="eos-btn-sm" data-act="remove" data-id="' + EOS_UI.escAttr(r.id) + '">Remove</button>');
            return '<div class="portal-conn-row">' +
                '<div><b>' + EOS_UI.esc(r.id) + '</b>' +
                '<div class="portal-conn-sub">' + EOS_UI.esc(rowStatus(r, false)) + '</div>' +
                '<div class="portal-conn-sub portal-conn-cmd">' +
                    EOS_UI.esc(r.transport === 'http' ? r.url : (r.command + ' ' + (r.args || []).join(' ')).trim()) +
                '</div></div>' +
                '<div class="portal-conn-acts">' + acts.join('') + '</div></div>';
        }).join('');
        return (rows || '<div class="portal-empty">No connectors yet.</div>') +
            '<div class="portal-conn-add">' +
            '<button type="button" class="eos-btn-sm" data-act="add-http">+ Add an HTTP server</button>' +
            '<button type="button" class="eos-btn-sm" data-act="add-stdio">+ Add a local server</button>' +
            '</div>';
    }

    function _bindPanel() {
        document.querySelectorAll('.eos-modal [data-act]').forEach(function (b) {
            b.onclick = function () {
                var act = b.getAttribute('data-act');
                var id = b.getAttribute('data-id');
                if (act === 'connect') return _connect(id, true);
                if (act === 'disconnect') return _connect(id, false);
                if (act === 'remove') return _remove(id);
                if (act === 'add-http') return _addHttp();
                if (act === 'add-stdio') return _addStdio();
            };
        });
    }

    // Re-open rather than patch. A nested modal (add / remove) REPLACES the
    // overlay and closes on submit, so by the time the POST resolves there is
    // no .eos-modal-body to write into — the panel simply vanished, and the
    // row the user just added was never shown.
    function _refreshPanel() {
        load().then(function () {
            var body = document.querySelector('.eos-modal-body');
            if (body) { body.innerHTML = _panelHtml(); _bindPanel(); return; }
            EOS_UI.modal({ title: 'Connectors', width: '560px', body: _panelHtml() });
            setTimeout(_bindPanel, 0);
        });
    }

    function _connect(id, on) {
        EOS.apiSafe('/agent/api/connectors/' + encodeURIComponent(id) + '/connect', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ connect: !!on })
        }).then(function (d) {
            if (!d || d.error || d.ok === false) {
                EOS.toast((d && (d.error)) || 'Could not reach that server', false);
            } else {
                EOS.toast(on ? (id + ' connected (' + (d.tools || []).length + ' tools)') : (id + ' disconnected'), true);
            }
            _refreshPanel();
        });
    }

    function _remove(id) {
        EOS_UI.confirm({
            title: 'Remove ' + id + '?',
            message: 'The server is forgotten and disconnected. Nothing it wrote is deleted.',
            confirmLabel: 'Remove',
            onConfirm: function () {
                EOS.apiSafe('/agent/api/connectors/' + encodeURIComponent(id) + '/remove', { method: 'POST' })
                    .then(function (d) {
                        if (!d || d.error) EOS.toast((d && d.error) || 'Could not remove', false);
                        _refreshPanel();
                    });
            }
        });
    }

    function _post(body) {
        return EOS.apiSafe('/agent/api/connectors', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
    }

    // Every add is propose → confirm, http included: the server holds the row
    // and hands back a one-shot token, and confirming sends ONLY that token.
    // The page cannot assert its own approval, and what is written is what was
    // shown — one object, not two inputs that happen to agree.
    function _propose(body, name) {
        _post(body).then(function (d) {
            if (!d) { EOS.toast('Could not add', false); return; }
            if (d.error) { EOS.toast(d.error, false); return; }
            if (!d.token) { _refreshPanel(); return; }
            EOS_UI.confirm({
                title: d.transport === 'stdio' ? 'Run this program?' : 'Add this connector?',
                message: _confirmText(d),
                confirmLabel: 'Add connector',
                danger: d.transport === 'stdio' || !!(d.env_refs || []).length,
                onConfirm: function () {
                    _post({ token: d.token }).then(function (r) {
                        if (!r || r.error) { EOS.toast((r && r.error) || 'Could not add', false); return; }
                        EOS.toast(name + ' added', true);
                        _refreshPanel();
                    });
                }
            });
        });
    }

    // What the card says. The command (or URL) sits on its own line because it
    // is the thing being inspected, not part of the prose around it — which is
    // why EOS_UI.confirm now renders its message pre-wrap.
    function _confirmText(d) {
        var lines = [d.message, ''];
        if (d.transport === 'stdio') {
            lines.push(d.command_line);
            lines.push('');
            lines.push('in ' + d.cwd + (d.cwd_inherited ? ' (inherited from the daemon)' : ''));
        } else {
            lines.push(d.url);
        }
        return lines.join('\n');
    }

    function _addHttp() {
        EOS_UI.formModal({
            title: 'Add an HTTP MCP server',
            fields: [
                { key: 'id', label: 'Name', placeholder: 'velorn', required: true },
                { key: 'url', label: 'URL', placeholder: 'http://127.0.0.1:19790/mcp', required: true },
                // formHtml renders label/placeholder/value/type/options/required —
                // there is no `hint`, so guidance passed that way is invisible.
                // This is the ONE instruction that keeps users from pasting a raw
                // token, so it goes where it will actually be seen.
                { key: 'auth',
                  label: 'Authorization header — optional. Write ${NAME} to read an environment variable; the token itself is never stored.',
                  placeholder: 'Bearer ${MY_TOKEN}' }
            ],
            onSubmit: function (v) {
                var body = { id: v.id, url: v.url };
                if (v.auth) body.headers = { Authorization: v.auth };
                // Proposed like a local server, for a different reason: a
                // ${ENV} header means adding this sends a named secret to the
                // host in the URL, and the card names both.
                _propose(body, v.id);
            }
        });
    }

    // A local server is a PROGRAM. The server proposes; the user confirms the
    // exact line (.claude/rules/proposed-action.md — the command IS the thing
    // to inspect, so it is shown verbatim rather than summarised).
    function _addStdio() {
        EOS_UI.formModal({
            title: 'Add a local MCP server',
            fields: [
                { key: 'id', label: 'Name', placeholder: 'files', required: true },
                { key: 'command', label: 'Command', placeholder: 'npx', required: true },
                { key: 'args',
                  label: 'Arguments — space-separated. Run by name, never through a shell.',
                  placeholder: '-y @modelcontextprotocol/server-filesystem /data' }
            ],
            onSubmit: function (v) {
                _propose({
                    id: v.id,
                    command: v.command,
                    args: (v.args || '').split(' ').filter(Boolean)
                }, v.id);
            }
        });
    }

    function init(cfg) {
        S.on = !!(cfg && cfg.connectors) && typeof PortalChat !== 'undefined' && PortalChat.isOn();
        if (!S.on) return;
        load();
    }

    return {
        init: init,
        isOn: function () { return S.on; },
        decorateChip: decorateChip,
        openPanel: openPanel,
        // pure — exported for tests/js/portal_connectors.test.mjs
        parseEnabled: parseEnabled,
        joinEnabled: joinEnabled,
        chipLabel: chipLabel,
        rowStatus: rowStatus,
        _state: function () { return S; }
    };
})();
