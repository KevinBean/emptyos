// portal-chat.js — the chat-first home (B1). Dark behind
// [apps.portal] feature.chat-first.enabled. With the flag off, init() makes
// one GET /portal/api/config, finds the flag off and returns; afterSync,
// selectBackend and pickThreadModel return early on !S.on, and decorateChip
// leaves the thread chip a plain label — so Chat, More and the hero pill stay
// hidden and nothing here writes to localStorage. start() is reached only
// through ACTIVE_BACKEND === 'chat', which only init() can set.
//
// With the flag on, a new conversation defaults to an agent session with
// profile "chat" (apps/public/standard/agent/profiles.py): the chat persona,
// read-and-reach tools only, no coding passes. Rooms / Assistant / Agent
// (coding) / Code move behind a "More" menu. A model picker chooses the
// session's provider; once a chat has messages it only offers providers of
// the kind its history is stored in (the session's server-reported
// history_kind — the server refuses a cross-kind switch too).
//
// One namespaced global — PortalChat — per .claude/rules/multi-module-apps.md
// § frontend counterpart. Loads before portal.js; portal.js's boot calls
// PortalChat.init(). The pure helpers at the top are exported for
// tests/js/portal_chat.test.mjs.
var PortalChat = (function () {
    'use strict';

    var MODEL_KEY = 'portal.chat.model.v1';
    var BACKEND_KEY = 'portal.chat.backend.v1';
    var LEGACY = [
        { id: 'rooms', label: 'Rooms', hint: 'Multi-participant chat + verbs' },
        { id: 'assistant', label: 'Assistant', hint: 'Vault Q&A + multi-provider' },
        { id: 'agent', label: 'Agent (coding)', hint: 'Tool-loop over the repo — reads, edits, runs' },
        { id: 'code', label: 'Code', hint: 'Code workspace — file tree, diff, terminal' }
    ];
    var S = { on: false, loaded: false, loadError: '', providers: [], chatDefault: '', model: '', ready: null };

    // ── Pure helpers (node-tested) ─────────────────────────────────────

    function findRow(providers, name) {
        return (providers || []).find(function (p) { return p.name === name; }) || null;
    }

    // The model a new chat starts on: the saved choice while it can still
    // drive a chat and answers; else the server's chat default when that can;
    // else the first usable LOCAL one, then the first usable cloud one — the
    // server's own order (a chat reads the vault; CLAUDE.md rule 19); else ''.
    function initialModel(providers, saved, chatDefault) {
        var ok = function (r) { return !!(r && r.chat_ok && r.available); };
        if (ok(findRow(providers, saved))) return saved;
        if (ok(findRow(providers, chatDefault))) return chatDefault;
        var usable = (providers || []).filter(ok);
        var first = usable.find(function (r) { return !r.is_cloud; }) || usable[0];
        return first ? first.name : '';
    }

    // Rows a chat's thread picker offers: chat-capable, and — once the chat has
    // stored messages — only the kind they are stored in.
    function threadRows(providers, historyKind) {
        return (providers || []).filter(function (p) {
            return p.chat_ok && (!historyKind || p.kind === historyKind);
        });
    }

    // The backend chat-first starts on: a remembered choice when it is still a
    // real backend, else Chat. Never reads portal.backend.v1 (the flag-off key).
    function restoreBackend(saved, convBackends) {
        return (saved && (saved === 'chat' || (convBackends && convBackends[saved]))) ? saved : 'chat';
    }

    // ── State + rendering ──────────────────────────────────────────────

    function _esc(s) { return EOS_UI.esc(s == null ? '' : String(s)); }
    function _row(name) { return findRow(S.providers, name); }
    function _save(key, val) { try { localStorage.setItem(key, val); } catch (e) {} }
    function _read(key) { try { return localStorage.getItem(key) || ''; } catch (e) { return ''; } }

    function isOn() { return S.on; }
    function model() { return S.model; }

    async function _loadProviders() {
        var d = await EOS.apiSafe('/agent/api/providers');
        if (!d || d.error) {
            S.providers = [];
            S.loadError = (d && d.error) || 'no response';
        } else {
            S.providers = d.providers || [];
            S.chatDefault = d.chat_default || '';
            S.loadError = '';
        }
        S.loaded = true;
    }

    // The modal list, drawn with the shared EOS_UI.modelRowHtml. Unreachable
    // rows are disabled here: a chat on a dead endpoint fails at its first turn.
    function _pick(title, rows, current, onPick) {
        var body;
        if (S.loadError) {
            body = '<div class="portal-empty">Could not load the model list (' + _esc(S.loadError) + '). Reload to try again.</div>';
        } else if (!rows.length) {
            body = '<div class="portal-empty">No model can drive a chat here — configure a tool-capable provider.</div>';
        } else {
            body = rows.map(function (p) {
                return EOS_UI.modelRowHtml(p, { current: p.name === current, disableUnavailable: true });
            }).join('');
        }
        EOS_UI.modal({ title: title, width: '440px', body: '<div class="eos-model-popover-body">' + body + '</div>' });
        setTimeout(function () {
            document.querySelectorAll('.eos-model-popover-body .eos-model-row').forEach(function (btn) {
                btn.onclick = function () {
                    if (btn.disabled) return;
                    EOS_UI.closeModal();
                    onPick(btn.getAttribute('data-prov'));
                };
            });
        }, 0);
    }

    function _renderHeroModel() {
        var mount = document.getElementById('hero-model');
        if (!mount) return;
        // Hidden in a folder too: a folder thread starts in Rooms (portal.js).
        var inFolder = typeof _pendingFolderId !== 'undefined' && !!_pendingFolderId;
        var show = S.on && ACTIVE_VERB === 'think' && ACTIVE_BACKEND === 'chat' && !inFolder;
        mount.style.display = show ? '' : 'none';
        if (!show) return;
        var row = _row(S.model) || { name: S.model };
        var warn = S.loadError ? 'model list unavailable' : (S.model && !row.available ? 'unreachable' : '');
        // Empty model: while loading, or when the list failed (the server then
        // picks its chat default), or when nothing can drive a chat (the
        // server then refuses the chat and says so).
        var empty = !S.loaded ? 'loading…' : (S.loadError ? 'server default' : 'no model');
        mount.innerHTML = EOS_UI.modelPillHtml(row, { title: 'Model for the next new chat', warn: warn, empty: empty });
        mount.querySelector('button').onclick = _pickHeroModel;
    }

    // Called at the end of portal.js's _syncBackendUI, which repaints every
    // .portal-bk from ACTIVE_BACKEND and would otherwise undo this.
    function afterSync() {
        if (!S.on) return;
        _showChatAndActive();
        _renderHeroModel();
        if (typeof PortalAttach !== 'undefined') PortalAttach.sync();
    }

    async function _pickHeroModel() {
        await S.ready;
        var rows = S.providers.filter(function (p) { return p.chat_ok; });
        _pick('Model for new chats', rows, S.model, function (name) {
            S.model = name;
            _save(MODEL_KEY, name);
            _renderHeroModel();
        });
    }

    // A chat thread's model chip (#portal-chat-model, portal.js _openAgent):
    // a keyboard-reachable button for a chat in chat-first mode, a plain
    // label otherwise. Called again once init() resolves, so a chat opened
    // from the URL hash before the config answered is not left inert.
    function decorateChip(agent) {
        var el = document.getElementById('portal-chat-model');
        if (!el || !agent) return;
        var on = S.on && agent._backend === 'agent' && agent._profile === 'chat';
        el.classList.toggle('switchable', on);
        el.onclick = on ? function () { pickThreadModel(agent); } : null;
        el.onkeydown = on ? function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickThreadModel(agent); }
        } : null;
        if (on) {
            el.setAttribute('role', 'button');
            el.setAttribute('tabindex', '0');
            el.setAttribute('aria-haspopup', 'dialog');
            el.title = 'Switch the model for this chat';
        } else {
            el.removeAttribute('role');
            el.removeAttribute('tabindex');
            el.removeAttribute('aria-haspopup');
            el.title = 'Active model for this thread';
        }
        if (typeof PortalAttach !== 'undefined') PortalAttach.sync();   // the thread composer
    }

    // In an open chat thread: switch that session's provider.
    async function pickThreadModel(agent) {
        if (!S.on || !agent || agent._backend !== 'agent' || agent._profile !== 'chat') return;
        await S.ready;
        var sess = await EOS.apiSafe('/agent/api/sessions/' + encodeURIComponent(agent._sid));
        if (currentAgent !== agent) return;   // the user moved on while this loaded
        if (!sess || sess.error) { EOS.toast('Could not load this chat', false); return; }
        var locked = sess.history_kind || '';
        var title = locked ? 'Model for this chat (' + locked + ' models)' : 'Model for this chat';
        _pick(title, threadRows(S.providers, locked), sess.provider, async function (name) {
            if (name === sess.provider) return;
            var res = await EOS.apiSafe('/agent/api/sessions/' + encodeURIComponent(agent._sid), {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider: name })
            });
            if (!res || res.error) { EOS.toast((res && res.error) || 'Switch failed', false); return; }
            // Same text _agentAgentObj builds on reopen: the provider name (portal
            // never writes the session's model column).
            agent.model = name;
            var chip = document.getElementById('portal-chat-model');
            if (chip && currentAgent === agent) chip.textContent = name;
            EOS.toast('Model: ' + name, true);
        });
    }

    // Rooms / Assistant / Agent (coding) / Code collapse into one menu. The
    // choice is remembered only under chat-first's own key.
    function _mountMoreMenu() {
        var more = document.getElementById('portal-bk-more');
        if (more) more.onclick = _openMore;
        ACTIVE_BACKEND = restoreBackend(_read(BACKEND_KEY), CONV_BACKENDS);
    }

    // Visible in chat-first mode: Chat, More, and the legacy radio that is
    // currently selected — so the radiogroup always shows its checked radio.
    // Re-applied on every sync because portal.js re-shows Code once the app
    // catalog loads.
    function _showChatAndActive() {
        document.querySelectorAll('#portal-backend-select .portal-bk').forEach(function (b) {
            var bk = b.dataset.backend;
            var keep = bk === 'chat' || (bk === ACTIVE_BACKEND && bk !== 'code');
            b.style.display = keep ? '' : 'none';
        });
        var more = document.getElementById('portal-bk-more');
        if (more) more.style.display = ACTIVE_VERB === 'think' ? '' : 'none';
    }

    function _openMore() {
        var avail = LEGACY.filter(function (l) {
            // `code` is extension-track: offered only when the app is installed.
            return l.id !== 'code' || !!(typeof APP_CATALOG !== 'undefined' && APP_CATALOG.code);
        });
        EOS_UI.choiceMenu({
            title: 'Other conversation modes',
            rows: avail.map(function (l) {
                return { value: l.id, label: l.label, sub: l.hint, current: l.id === ACTIVE_BACKEND };
            }),
            onPick: function (bk) {
                if (bk === 'code') { _navToApp('code'); return; }
                selectBackend(bk);
            }
        });
    }

    // Called by portal.js's backend click handler and by the More menu.
    function selectBackend(bk) {
        if (!S.on) return;
        // "New chat in this project" only means something to Chat.
        if (bk !== 'chat' && typeof PortalProjects !== 'undefined') PortalProjects.clearPending();
        ACTIVE_BACKEND = bk;
        _save(BACKEND_KEY, bk);
        _syncBackendUI();   // → afterSync
        var hi = document.getElementById('hero-input');
        if (hi) hi.focus();
    }

    // Start a chat: an agent session with profile "chat" on the chosen model.
    // A folder context is not handled here — portal.js routes a folder thread
    // to Rooms, which owns folders until they become chat projects (B2).
    async function start(text) {
        await S.ready;   // a submit during the provider probe still gets the chosen model
        // "+ New chat in this project" (portal-projects.js) claims this send.
        var projects = typeof PortalProjects !== 'undefined' ? PortalProjects : null;
        var projectId = projects ? projects.pendingProject() : '';
        var att = typeof PortalAttach !== 'undefined' ? PortalAttach.take('hero') : { payload: {}, items: [] };
        try {
            await _startAgentThread(text, null, {
                profile: 'chat', provider: S.model, project_id: projectId,
                attach: att.payload, attached: att.items
            });
        } catch (err) {
            // The project was deleted meanwhile (another tab): drop the stale
            // claim so the next send is not refused the same way.
            if (projects && projectId && /no such project/.test(String(err && err.message))) {
                projects.clearPending();
                projects.load();
            }
            throw err;
        }
        if (projects && projectId) projects.clearPending();
    }

    async function init() {
        var cfg = await EOS.apiSafe('/portal/api/config');
        if (!cfg || !cfg.chat_first) return false;
        S.on = true;
        // Mount BEFORE the providers load: their probe can take its whole cap,
        // and until the mount runs the legacy row is live and a submit would
        // start a Rooms thread.
        _mountMoreMenu();
        _syncBackendUI();   // → afterSync (pill shows "loading…")
        // A chat opened from the URL hash before the config answered.
        if (typeof currentAgent !== 'undefined') decorateChip(currentAgent);
        // Now, not after the provider probe: the sidebar moves chats out of
        // "Agent runs" as soon as isOn() is true, so Projects must show them.
        if (typeof PortalProjects !== 'undefined') PortalProjects.init();
        if (typeof PortalAttach !== 'undefined') PortalAttach.init();
        // The panel needs BOTH flags: chat-first (we are past its gate to be
        // here) and the agent app's feature.artifacts.enabled, which is what
        // decides whether the tool that fills it exists at all.
        if (typeof PortalArtifacts !== 'undefined') PortalArtifacts.init(cfg);
        if (typeof PortalConnectors !== 'undefined') PortalConnectors.init(cfg);
        if (typeof _renderSidebar === 'function') _renderSidebar();
        S.ready = _loadProviders();
        await S.ready;
        S.model = initialModel(S.providers, _read(MODEL_KEY), S.chatDefault);
        _syncBackendUI();
        return true;
    }

    return {
        init: init,
        isOn: isOn,
        model: model,
        start: start,
        selectBackend: selectBackend,
        pickThreadModel: pickThreadModel,
        decorateChip: decorateChip,
        afterSync: afterSync,
        // pure — exported for tests/js/portal_chat.test.mjs
        initialModel: initialModel,
        threadRows: threadRows,
        restoreBackend: restoreBackend
    };
})();
