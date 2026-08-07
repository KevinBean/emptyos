// portal.js — extracted from pages/index.html (frontend monolith split).
// The page's inline <script> grew past the ~2000L readability threshold;
// moved verbatim to this sibling so index.html is markup+style only.
// Loaded via <script src="/portal/pages/portal.js"></script> at the same
// position the inline block held, so load order + globals (EOS, EOS_UI,
// esc, EOS_CONV) are unchanged. Static file — hot-reloads, no daemon restart.

// ── Verb handlers ──────────────────────────────────────────────────
// Each chip is a capability verb that dispatches differently:
//   think   → create a new room thread + chat-stream (today's flow)
//   capture → "Save as ..." picker → POST to capture/task/journal/kb
//   find    → vault search, render results inline as cards
//   learn   → "Process as ..." picker → POST to video-digest/kb
// (Adaptive routing across these is V3 — needs user_intent recommender.)
var VERB_HANDLERS = {
    think:   { label: 'Think',   handler: _handleThink },
    capture: { label: 'Capture', handler: _handleCapture },
    find:    { label: 'Find',    handler: _handleFind },
    learn:   { label: 'Learn',   handler: _handleLearn }
};

var ACTIVE_VERB = 'think';     // hero-state chip selection
var currentAgent = null;       // {id, name, system_prompt, ...} when a thread is open
var sending = false;
var allAgents = [];            // sidebar cache
var _pendingFolderId = null;   // when set, hero-submit lands in this folder

// ── Conversation backend (the unified-door mode switch) ─────────────
// Which chat backend a NEW conversation starts on. A thread remembers its
// backend on the thread object (`._backend`); send dispatches on it, so one
// portal shell drives several backends without merging them.
//   rooms     → apps/rooms      (multi-participant + [DO:] verbs) — default
//   assistant → apps/assistant  (vault Q&A + multi-provider)
// agent + code modes are native in later phases (WS tool-loop).
var CONV_BACKENDS = {
    rooms:     { label: 'Rooms',     icon: '\u{1F3E0}' },
    assistant: { label: 'Assistant', icon: '\u{1F4AC}' },
    agent:     { label: 'Agent',     icon: '\u{1F916}' }
};
var ACTIVE_BACKEND = 'rooms';
try {
    var _savedBackend = localStorage.getItem('portal.backend.v1');
    if (_savedBackend && CONV_BACKENDS[_savedBackend]) ACTIVE_BACKEND = _savedBackend;
} catch (e) { /* localStorage unavailable — default stands */ }
var allAsstSessions = [];      // assistant-backend sidebar cache

// ── Composer helpers (shared for hero + chat inputs) ────────────────

function _wireComposer(inputId, sendId) {
    var input = document.getElementById(inputId);
    var sendBtn = document.getElementById(sendId);
    if (!input || !sendBtn) return;
    input.addEventListener('input', function() {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 240) + 'px';
        sendBtn.classList.toggle('ready', !!input.value.trim());
    });
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendBtn.click();
        }
    });
}
_wireComposer('hero-input', 'hero-send');
_wireComposer('chat-input', 'chat-send');

// ── Chip selection (hero) ───────────────────────────────────────────

document.querySelectorAll('.portal-chip').forEach(function(chip) {
    chip.addEventListener('click', function() {
        if (chip.classList.contains('disabled')) return;
        document.querySelectorAll('.portal-chip').forEach(function(c) {
            c.classList.remove('active');
            c.setAttribute('aria-checked', 'false');
        });
        chip.classList.add('active');
        chip.setAttribute('aria-checked', 'true');
        ACTIVE_VERB = chip.dataset.verb;
        document.getElementById('hero-mode-label').textContent = chip.textContent.trim();
        _syncBackendUI();
        document.getElementById('hero-input').focus();
    });
});

// ── Conversation-backend selector (the mode switch) ─────────────────
// Reflects ACTIVE_BACKEND into the segmented control, and hides the control
// for one-shot verbs (capture/find/learn) where a backend is meaningless.
function _syncBackendUI() {
    document.querySelectorAll('.portal-bk').forEach(function(b) {
        var on = b.dataset.backend === ACTIVE_BACKEND;
        b.classList.toggle('active', on);
        // Code is an action button, not part of the radio set — no aria-checked.
        if (b.getAttribute('role') === 'radio') b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    var wrap = document.getElementById('portal-backend-select');
    if (wrap) wrap.style.display = (ACTIVE_VERB === 'think') ? '' : 'none';
}
document.querySelectorAll('.portal-bk').forEach(function(btn) {
    btn.addEventListener('click', function() {
        var bk = btn.dataset.backend;
        // Code is workspace-shaped, not composer-shaped: open the /code/ IDE in
        // the iframe pane (embed mode) rather than arming the composer.
        if (bk === 'code') { _navToApp('code'); return; }
        if (!CONV_BACKENDS[bk]) return;
        ACTIVE_BACKEND = bk;
        try { localStorage.setItem('portal.backend.v1', ACTIVE_BACKEND); } catch (e) {}
        _syncBackendUI();
        var hi = document.getElementById('hero-input');
        if (hi) hi.focus();
    });
});

// Feature-detect only OPTIONAL backends (extension-track apps like `code`,
// added in Phase 3). rooms/assistant/agent are always-present public/standard
// apps and are NEVER hidden here — /api/apps is an unreliable presence signal
// (it omits `rooms` despite it being installed and working), so gating the
// core backends on it would wrongly hide them. A store-disabled core backend
// simply toasts on click. Fail-open on an empty catalog.
var _OPTIONAL_BACKENDS = { code: true };  // extension-track apps feature-detected via APP_CATALOG
function _syncBackendAvailability() {
    if (!APP_CATALOG || !Object.keys(APP_CATALOG).length) return;
    document.querySelectorAll('.portal-bk').forEach(function(b) {
        var appId = b.dataset.backend;
        if (!_OPTIONAL_BACKENDS[appId]) return;   // core backends always shown
        var ok = !!APP_CATALOG[appId];
        b.style.display = ok ? '' : 'none';
        if (!ok && ACTIVE_BACKEND === appId) {
            ACTIVE_BACKEND = 'rooms';
            try { localStorage.setItem('portal.backend.v1', ACTIVE_BACKEND); } catch (e) {}
        }
    });
    _syncBackendUI();
}

// ── State transitions ──────────────────────────────────────────────

// Sidebar-pinned iframe shortcuts (Projects + Customize live in the Apps
// section). Any OTHER app is still reachable: search → click app result →
// loads in-pane via _navToApp. The APP_CATALOG cache (populated from
// /api/apps on boot) supplies the display label for those dynamic loads.
var APP_LABELS = {
    projects: 'Projects',
    settings: 'Customize'
};
var APP_CATALOG = {};  // {id: name} for every discovered app

async function loadAppCatalog() {
    try {
        var res = await EOS.api('/api/apps');
        (res || []).forEach(function(a) {
            if (a && a.id) APP_CATALOG[a.id] = a.name || a.id;
        });
    } catch (e) { /* non-fatal — _showApp falls back to the raw id */ }
}

function _hideAllStates() {
    // Any state change leaves the current agent thread — close its socket so it
    // can't leak. Re-opening an agent thread reconnects right after (_openAgent
    // runs _hideAllStates via _showChat, then the caller calls _agentConnect).
    if (typeof _agentCloseWs === 'function') _agentCloseWs();
    document.getElementById('portal-hero').style.display = 'none';
    document.getElementById('portal-chat').classList.remove('open');
    document.getElementById('portal-iframe-pane').classList.remove('open');
    var f = document.getElementById('portal-find');
    if (f) f.classList.remove('open');
}

function _showFind() {
    _hideAllStates();
    var pane = document.getElementById('portal-find');
    if (pane) pane.classList.add('open');
    currentAgent = null;
    _renderSidebar();
    _maybeCloseMobileSidebar();
}

function _showHero() {
    _hideAllStates();
    document.getElementById('portal-hero').style.display = '';
    currentAgent = null;
    setTimeout(function(){ document.getElementById('hero-input').focus(); }, 30);
    _renderSidebar();
    // Refresh the continue row from the in-memory caches (no fetches) so it
    // reflects the thread the user just left. Guarded — undefined at first boot.
    if (typeof _renderContinue === 'function') _renderContinue();
    _maybeCloseMobileSidebar();
}

function _showChat() {
    _hideAllStates();
    document.getElementById('portal-chat').classList.add('open');
    setTimeout(function(){ document.getElementById('chat-input').focus(); }, 30);
    _maybeCloseMobileSidebar();
}

function _showApp(appId) {
    _hideAllStates();
    var label = APP_LABELS[appId] || APP_CATALOG[appId] || appId;
    document.getElementById('portal-iframe-name').textContent = label;
    document.getElementById('portal-iframe-open').href = '/' + appId + '/';
    var frame = document.getElementById('portal-iframe-frame');
    // ?embed=1 is a hint for apps that honor it (V3 work) to strip their own
    // chrome (top nav, sidebar) when rendered inside the portal shell.
    // Apps that ignore it render unchanged — fully backwards compatible.
    var target = '/' + appId + '/?embed=1';
    if (frame.getAttribute('src') !== target) frame.setAttribute('src', target);
    document.getElementById('portal-iframe-pane').classList.add('open');
    currentAgent = null;
    _renderSidebar();
    _maybeCloseMobileSidebar();
}

function newThread() {
    // Top-level new thread — clear any folder context, then clear the hash.
    _pendingFolderId = null;
    _updateFolderContextPill();
    if (location.hash) {
        history.pushState(null, '', location.pathname);
        _onHashChange();
    } else {
        _showHero();
    }
    var hi = document.getElementById('hero-input');
    var ci = document.getElementById('chat-input');
    if (hi) { hi.value = ''; hi.style.height = 'auto'; }
    if (ci) { ci.value = ''; ci.style.height = 'auto'; }
    document.getElementById('hero-send').classList.remove('ready');
    document.getElementById('chat-send').classList.remove('ready');
}

// ── Hash routing ────────────────────────────────────────────────────

function _onHashChange() {
    var hashId = (location.hash || '').replace(/^#/, '');
    if (!hashId) {
        _showHero();
        return;
    }
    var raw = decodeURIComponent(hashId);
    if (raw.indexOf('app:') === 0) {
        var appId = raw.slice(4);
        if (appId) {
            _showApp(appId);
            return;
        }
    }
    if (raw.indexOf('find:') === 0) {
        var query = raw.slice(5);
        if (query) {
            _runFind(query);
            return;
        }
    }
    openThread(raw);
}
window.addEventListener('hashchange', _onHashChange);

// ── Hero submit dispatcher ─────────────────────────────────────────
//
// The hero composer is a universal router: it doesn't always create a
// thread. Active verb decides which handler fires. Verb handlers each
// own their own UX (modal picker, inline results, stream, etc.).

async function submitFromHero() {
    if (sending) return;
    var input = document.getElementById('hero-input');
    var text = input.value.trim();
    if (!text) return;
    var verb = VERB_HANDLERS[ACTIVE_VERB] || VERB_HANDLERS.think;
    var folder = _pendingFolderId
        ? allFolders.find(function(f){ return f.id === _pendingFolderId; })
        : null;
    try {
        await verb.handler(text, folder);
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) {
            EOS.toast(verb.label + ' failed: ' + (err.message || err), false);
        } else {
            console.error(err);
        }
    }
}

// ── Verb: Think ───────────────────────────────────────────────────
// Creates a new room thread, streams the first message. Inherits the
// folder's system_prompt + model if a folder context is active.

async function _handleThink(text, folder) {
    // The conversation verb fans out to the selected backend. Rooms is the
    // original flow below; assistant/agent own their own start helpers.
    if (ACTIVE_BACKEND === 'assistant') return _startAssistantThread(text, folder);
    if (ACTIVE_BACKEND === 'agent') return _startAgentThread(text, folder);
    var sendBtn = document.getElementById('hero-send');
    sendBtn.disabled = true;
    var prevLabel = sendBtn.textContent;
    sendBtn.textContent = 'Creating…';
    var input = document.getElementById('hero-input');
    try {
        var titleSeed = text.length > 40 ? text.slice(0, 40).trim() + '…' : text;
        var body = { name: titleSeed };
        if (folder && folder.system_prompt) body.system_prompt = folder.system_prompt;
        if (folder && folder.model) body.model = folder.model;
        var res = await fetch('/rooms/api/agents', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        var agent = await res.json();
        if (!agent || !agent.id) {
            throw new Error((agent && agent.error) || 'create failed');
        }
        agent._mode_label = folder ? folder.name : 'Think';
        allAgents.unshift(agent);
        if (folder) {
            try {
                await fetch('/portal/api/folders/' + encodeURIComponent(folder.id) + '/threads', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ thread_id: agent.id })
                });
                await loadFolders();
            } catch (e) { /* non-fatal */ }
        }
        _pendingFolderId = null;
        _updateFolderContextPill();
        sendBtn.disabled = false;
        sendBtn.textContent = prevLabel;
        history.pushState(null, '', location.pathname + '#' + encodeURIComponent(agent.id));
        _openAgent(agent, []);
        input.value = '';
        input.style.height = 'auto';
        document.getElementById('hero-send').classList.remove('ready');
        await _sendText(text);
    } catch (err) {
        sendBtn.disabled = false;
        sendBtn.textContent = prevLabel;
        throw err;
    }
}

// ── Backend: Assistant (vault Q&A + multi-provider) ─────────────────
// A portal "thread" over an apps/assistant session. Thread id is
// `asst:<session_id>` so hash-routing + openThread can tell backends apart
// from rooms threads (bare ids). Backends stay untouched; portal just points
// the composer at /assistant/api/* over HTTP.

function _asstAgentObj(session) {
    return {
        id: 'asst:' + session.id,
        name: session.name || 'Assistant chat',
        _backend: 'assistant',
        _sid: session.id,
        _mode_label: 'Assistant',
        created: session.created || ''
    };
}

async function _startAssistantThread(text, folder) {
    var sendBtn = document.getElementById('hero-send');
    sendBtn.disabled = true;
    var prevLabel = sendBtn.textContent;
    sendBtn.textContent = 'Creating…';
    var input = document.getElementById('hero-input');
    try {
        var titleSeed = text.length > 40 ? text.slice(0, 40).trim() + '…' : text;
        var session = await fetch('/assistant/api/sessions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name: titleSeed, backend: 'auto' })
        }).then(function(r){ return r.json(); });
        if (!session || !session.id) {
            throw new Error((session && session.error) || 'create failed');
        }
        var agent = _asstAgentObj(session);
        allAsstSessions.unshift({
            id: session.id, name: agent.name, created: agent.created, last_message: ''
        });
        sendBtn.disabled = false;
        sendBtn.textContent = prevLabel;
        history.pushState(null, '', location.pathname + '#' + encodeURIComponent(agent.id));
        _openAgent(agent, []);
        input.value = '';
        input.style.height = 'auto';
        sendBtn.classList.remove('ready');
        await _sendText(text);
        _renderSidebar();
    } catch (err) {
        sendBtn.disabled = false;
        sendBtn.textContent = prevLabel;
        throw err;
    }
}

async function _openAssistantSession(sid) {
    try {
        var session = await EOS.api('/assistant/api/sessions/' + encodeURIComponent(sid));
        if (session && session.error) throw new Error(session.error);
        var agent = _asstAgentObj(session);
        var msgs = (session.messages || []).map(function(m) {
            return { role: m.role, text: m.text, ts: m.ts };
        });
        _openAgent(agent, msgs);
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) {
            EOS.toast('Could not open chat: ' + (err.message || err), false);
        }
        history.pushState(null, '', location.pathname);
        _showHero();
    }
}

// Assistant is blocking single-shot (vault-context injection + optional
// two-phase verify); render the whole reply when it lands.
async function _sendAssistant(text) {
    _appendTurn('user', text, { streaming: false });
    var bodyEl = _appendTurn('assistant', '', { streaming: true });
    try {
        var data = await fetch('/assistant/api/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ message: text, session_id: currentAgent._sid, context: true })
        }).then(function(r){ return r.json(); });
        if (data && data.error) throw new Error(data.error);
        var answer = (data && (data.response || data.message)) || 'No response.';
        bodyEl.innerHTML = _renderMarkdown(answer);
        if (data && data.provider) {
            var chip = document.createElement('div');
            chip.className = 'portal-prov';
            chip.textContent = data.provider;
            bodyEl.appendChild(chip);
        }
        bodyEl.classList.remove('streaming');
    } catch (err) {
        bodyEl.textContent = 'Error: ' + (err && err.message ? err.message : err);
        bodyEl.classList.remove('streaming');
    }
    _scrollMessagesToBottom();
}

// ── Backend: Agent (autonomous tool-loop, native WS) ────────────────
// A portal thread over an apps/agent session. Thread id is `agent:<sid>`.
// Portal creates + drives its OWN agent sessions (the backend keeps a single
// live-turn slot per session — sharing a session across two frontends would
// race turn persistence; opening an old session read-only is the same
// exposure /agent/ already has with two tabs).
//
// This is a DELIBERATE SUBSET of /agent/ (streaming text, tool cards,
// permission modal, turn footer, cancel, notices, history replay). The full
// power surface — slash palette, plan mode, TaskList panel, undo, session
// CRUD — stays on /agent/. The three pure render helpers below
// (_agentDiffHtml / _agentToolExtras / _agentFmtCost) are copied verbatim
// from apps/public/standard/agent/pages/agent.js (renderDiffHtml,
// renderToolDisplayExtras, _fmtCost); if both pages later prove identical,
// that is the rule-9 moment to extract a shared eos-agent-view.js.

var _agentWs = null;          // single live socket; closed on every thread switch
var _agentSid = null;
var _agentCur = null;         // current assistant turn body (.body element)
var _agentCurTextEl = null;   // the .portal-md run currently accumulating streamed text
var _agentCurText = '';       // markdown accumulated for _agentCurTextEl
var _agentToolEls = {};       // tool_use id -> .portal-tc card element
var _agentTurn = { start: 0, tools: 0 };
var allAgentSessions = [];    // agent-backend sidebar cache

function _agentAgentObj(session) {
    return {
        id: 'agent:' + session.id,
        name: session.name || 'Agent run',
        _backend: 'agent',
        _sid: session.id,
        _mode_label: 'Agent',
        // Who's answering — the session's provider · model, shown by _openAgent's
        // existing #portal-chat-model chip. (Deliberately NOT EOS_UI.modelPill:
        // portal spends no think of its own — the backends do — so a pill would
        // configure `think.app.portal`, a knob nothing reads.)
        model: [session.provider, session.model].filter(Boolean).join(' · '),
        created: session.created || ''
    };
}

async function _startAgentThread(text, folder) {
    var sendBtn = document.getElementById('hero-send');
    sendBtn.disabled = true;
    var prevLabel = sendBtn.textContent;
    sendBtn.textContent = 'Creating…';
    var input = document.getElementById('hero-input');
    try {
        var titleSeed = text.length > 40 ? text.slice(0, 40).trim() + '…' : text;
        var session = await fetch('/agent/api/sessions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name: titleSeed })
        }).then(function(r){ return r.json(); });
        if (!session || !session.id) {
            throw new Error((session && session.error) || 'create failed');
        }
        var agent = _agentAgentObj(session);
        allAgentSessions.unshift({
            id: session.id, name: agent.name, created: agent.created, last_message: ''
        });
        sendBtn.disabled = false;
        sendBtn.textContent = prevLabel;
        history.pushState(null, '', location.pathname + '#' + encodeURIComponent(agent.id));
        _openAgent(agent, []);
        input.value = '';
        input.style.height = 'auto';
        sendBtn.classList.remove('ready');
        // Show the user's first turn immediately; the WS sends it on open.
        _appendTurn('user', text, { streaming: false });
        _agentConnect(session.id, text);
        _renderSidebar();
    } catch (err) {
        sendBtn.disabled = false;
        sendBtn.textContent = prevLabel;
        throw err;
    }
}

async function _openAgentSession(sid) {
    try {
        var session = await EOS.api('/agent/api/sessions/' + encodeURIComponent(sid));
        if (session && session.error) throw new Error(session.error);
        var agent = _agentAgentObj(session);
        _openAgent(agent, session.messages || []);  // _openAgent replays via _renderAgentHistory
        _agentConnect(sid);
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) {
            EOS.toast('Could not open agent run: ' + (err.message || err), false);
        }
        history.pushState(null, '', location.pathname);
        _showHero();
    }
}

// ── Agent WebSocket lifecycle ───────────────────────────────────────

function _agentConnect(sid, initialText) {
    _agentCloseWs();
    _agentSid = sid;
    _agentCur = null; _agentCurTextEl = null; _agentCurText = ''; _agentToolEls = {};
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + location.host + '/agent/ws/' + encodeURIComponent(sid);
    var ws;
    try { ws = new WebSocket(url); } catch (e) { _agentSetStatus('connection error'); return; }
    _agentWs = ws;
    ws._pending = initialText || null;
    ws.onopen = function() {
        _agentSetStatus('connected');
        if (ws._pending) {
            ws.send(JSON.stringify({ type: 'message', text: ws._pending }));
            ws._pending = null;
            _agentSetSending(true);
        }
    };
    ws.onclose = function() { if (_agentWs === ws) { _agentSetStatus('disconnected'); _agentSetSending(false); } };
    ws.onerror = function() { if (_agentWs === ws) _agentSetStatus('connection error'); };
    ws.onmessage = function(ev) {
        var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
        _agentWsEvent(msg);
    };
}

function _agentCloseWs() {
    if (_agentWs) { try { _agentWs.close(); } catch (e) {} _agentWs = null; }
    _agentSid = null;
    // Closing the socket ends any agent turn — clear the composer gate
    // UNCONDITIONALLY. We may be leaving agent mode for a rooms/assistant thread,
    // and _agentSetSending's backend guard would otherwise strand `sending=true`,
    // blocking the next thread's composer (submitFromHero / sendInThread guard it).
    sending = false;
    var cs = document.getElementById('chat-send'); if (cs) cs.disabled = false;
    var stop = document.getElementById('portal-agent-stop'); if (stop) stop.style.display = 'none';
    _agentSetStatus('');
}

function _agentSetStatus(text) {
    var el = document.getElementById('portal-agent-status');
    if (!el) return;
    el.textContent = text || '';
    el.style.display = text ? '' : 'none';
}

function _agentSetSending(on) {
    // Only gate the composer while an AGENT turn runs; leave rooms/assistant alone.
    if (currentAgent && currentAgent._backend === 'agent') {
        sending = !!on;
        var cs = document.getElementById('chat-send');
        if (cs) cs.disabled = !!on;
    }
    var stop = document.getElementById('portal-agent-stop');
    if (stop) stop.style.display = (on && currentAgent && currentAgent._backend === 'agent') ? '' : 'none';
}

function _agentCancel() {
    if (_agentWs && _agentWs.readyState === WebSocket.OPEN) {
        _agentWs.send(JSON.stringify({ type: 'cancel' }));
    }
}

// Lazily create the streaming assistant turn (matches agent.js — no bubble
// until the first text/tool arrives; the header status shows "thinking…").
function _agentEnsureTurn() {
    if (!_agentCur) {
        _agentCur = _appendTurn('assistant', '', { streaming: true });
        _agentCur.innerHTML = '';
        _agentCurTextEl = null;
    }
    return _agentCur;
}

function _agentWsEvent(msg) {
    var t = msg.type;
    if (t === 'agent:turn_start') {
        _agentCur = null; _agentCurTextEl = null; _agentCurText = '';
        _agentTurn = { start: Date.now(), tools: 0 };
        _agentSetStatus('thinking…');
    } else if (t === 'agent:text') {
        _agentEnsureTurn();
        if (!_agentCurTextEl) {
            _agentCurTextEl = document.createElement('div');
            _agentCurTextEl.className = 'portal-md';
            _agentCur.appendChild(_agentCurTextEl);
            _agentCurText = '';
        }
        _agentCurText += (msg.delta || '');
        _agentCurTextEl.innerHTML = _renderMarkdown(_agentCurText);
        _scrollMessagesToBottom();
    } else if (t === 'agent:tool_call') {
        _agentEnsureTurn();
        _appendAgentToolCall(_agentCur, msg.id, msg.name, msg.input);
        _agentCurTextEl = null;  // any text after this call starts a new run below the card
        _agentTurn.tools += 1;
        _agentSetStatus('calling ' + (msg.name || 'tool') + '…');
    } else if (t === 'agent:tool_result') {
        _markAgentToolResult(msg.id, !!msg.is_error, msg.content || '', msg.display || {});
    } else if (t === 'agent:permission_requested') {
        if (typeof EOS_UI !== 'undefined' && EOS_UI.agentPermission) {
            EOS_UI.agentPermission({
                id: msg.id, session_id: msg.session_id,
                tool: msg.tool, input: msg.input, summary: msg.summary
            });
        }
    } else if (t === 'agent:skill_loaded') {
        _agentNotice('⚑ loaded skill ' + (msg.name || ''));
    } else if (t === 'agent:compacted') {
        _agentNotice('· compacted history — saved ~' + ((msg.chars_saved || 0).toLocaleString()) + ' chars');
    } else if (t === 'agent:orient') {
        var plan = msg.plan || {}; var lines = [];
        if (plan.task_type) { var h = plan.task_type; if (plan.subject) h += ': ' + plan.subject; lines.push(h); }
        (plan.relevant_rules || []).forEach(function(r){ lines.push('• ' + r); });
        if (plan.success_criteria) lines.push('Done when: ' + plan.success_criteria);
        (plan.risk_flags || []).forEach(function(f){ lines.push('⚠ ' + f); });
        if (lines.length) _agentNotice(lines.join('\n'));
    } else if (t === 'agent:done') {
        _agentTurnFooter(msg.usage || {});
        _agentSetStatus('connected');
        _agentFinalize();
        _agentSetSending(false);
    } else if (t === 'agent:cancelled') {
        _agentSetStatus('cancelled'); _agentFinalize(); _agentSetSending(false);
    } else if (t === 'agent:max_iters') {
        _agentSetStatus('stopped (max iterations)'); _agentFinalize(); _agentSetSending(false);
    } else if (t === 'agent:error') {
        _agentSetStatus('error'); _agentNotice('Error: ' + (msg.error || '')); _agentFinalize(); _agentSetSending(false);
    } else if (t === 'agent:status') {
        _agentSetStatus(msg.status || '');
    } else if (t === 'error') {
        _agentSetStatus('error'); _agentNotice('Error: ' + (msg.message || '')); _agentSetSending(false);
    }
    // Deliberately ignored (power-surface-only): plan_mode, slash_result,
    // iter_start, usage. They no-op cleanly in the door subset.
}

function _agentFinalize() {
    if (_agentCur) _agentCur.classList.remove('streaming');
    _agentCur = null; _agentCurTextEl = null; _agentCurText = '';
}

function _agentNotice(text) {
    var inner = document.getElementById('portal-messages-inner');
    if (!inner) return;
    var el = document.createElement('div');
    el.className = 'portal-agent-notice';
    el.textContent = text;
    inner.appendChild(el);
    _scrollMessagesToBottom();
}

function _agentToolSummary(input) {
    if (!input || typeof input !== 'object') return '';
    var v = input.path || input.file_path || input.command || input.pattern
        || input.query || input.url || '';
    v = String(v || '');
    return v.length > 90 ? v.slice(0, 90) + '…' : v;
}

function _appendAgentToolCall(turnBody, id, name, input) {
    var card = document.createElement('div');
    card.className = 'portal-tc';
    card.setAttribute('data-tc-id', id || '');
    var summary = _agentToolSummary(input);
    var inputJson = '';
    try { inputJson = JSON.stringify(input || {}, null, 2); } catch (e) {}
    card.innerHTML =
        '<div class="portal-tc-head">' +
            '<span class="portal-tc-name">' + esc(name || 'tool') + '</span>' +
            (summary ? '<span class="portal-tc-arg">' + esc(summary) + '</span>' : '') +
            '<span class="portal-tc-status">running…</span>' +
        '</div>' +
        (inputJson && inputJson !== '{}'
            ? '<details class="portal-tc-input"><summary>input</summary><pre>' + esc(inputJson) + '</pre></details>'
            : '') +
        '<div class="portal-tc-result" style="display:none"></div>';
    turnBody.appendChild(card);
    if (id) _agentToolEls[id] = card;
    _scrollMessagesToBottom();
}

function _markAgentToolResult(id, isError, resultText, display) {
    var card = _agentToolEls[id];
    if (!card) return;
    var st = card.querySelector('.portal-tc-status');
    if (st) st.textContent = isError ? 'error' : 'done';
    if (isError) card.classList.add('portal-tc-err');
    var res = card.querySelector('.portal-tc-result');
    if (!res) return;
    var text = typeof resultText === 'string' ? resultText
        : (Array.isArray(resultText)
            ? resultText.map(function(b){ return (b && b.text) || ''; }).join('')
            : JSON.stringify(resultText));
    var extras = _agentToolExtras(display);
    var inner = extras;
    if (text) {
        inner += '<details' + (extras ? '' : ' open') + '><summary>output</summary><pre>' + esc(text) + '</pre></details>';
    }
    res.innerHTML = inner;
    res.style.display = '';
    _scrollMessagesToBottom();
}

function _agentTurnFooter(usage) {
    if (!_agentCur) return;
    var pt = parseInt(usage.prompt_tokens || usage.input_tokens || 0, 10) || 0;
    var ct = parseInt(usage.completion_tokens || usage.output_tokens || 0, 10) || 0;
    var cached = parseInt(usage.cached_tokens || usage.cache_read_input_tokens || 0, 10) || 0;
    var elapsed = _agentTurn.start ? (Date.now() - _agentTurn.start) / 1000 : 0;
    var cost = parseFloat(usage.cost); if (!isFinite(cost) || cost < 0) cost = 0;
    var parts = [elapsed.toFixed(1) + 's'];
    var total = pt + ct;
    if (total > 0) parts.push(total.toLocaleString() + ' tokens');
    if (cached > 0 && pt > 0) parts.push(Math.round(100 * cached / pt) + '% cache');
    if (_agentTurn.tools > 0) parts.push(_agentTurn.tools + ' tool' + (_agentTurn.tools === 1 ? '' : 's'));
    if (cost > 0) parts.push(_agentFmtCost(cost));
    var footer = document.createElement('div');
    footer.className = 'portal-turn-footer';
    footer.textContent = '· ' + parts.join(' · ');
    _agentCur.appendChild(footer);
    _scrollMessagesToBottom();
}

// History replay — mirrors agent.js renderHistoricalMessage's three-encoding
// walk (Anthropic block-lists, OpenAI tool_calls/tool-role, plain strings).
// Replayed tool cards carry no `display` (diff/exit chips are live-WS only),
// same as /agent/.
function _renderAgentHistory(messages) {
    var inner = document.getElementById('portal-messages-inner');
    if (inner) inner.innerHTML = '';
    _agentToolEls = {}; _agentCur = null; _agentCurTextEl = null; _agentCurText = '';
    (messages || []).forEach(_renderAgentHistMessage);
    _updateJumpButton();
    _scrollMessagesToBottom();
}

function _renderAgentHistMessage(m) {
    var role = m.role;
    var content = m.content;
    // Encoding 3 — "full-message dict" (OpenAI path): the real text lives under
    // `.content`, with `tool_calls` (assistant) / `tool_call_id` (tool) alongside.
    if (content && typeof content === 'object' && !Array.isArray(content)) {
        var inner = content.content;
        if (role === 'tool') { _histAgentToolResult(content.tool_call_id, inner, false); return; }
        if (role === 'assistant') { _renderAgentAssistantHist(inner, content.tool_calls); return; }
        _renderAgentUserHist(inner);   // user / anything else
        return;
    }
    // Encodings 1 & 2 — Anthropic block-list, or plain string.
    if (role === 'tool') { _histAgentToolResult(m.tool_call_id, content, false); }
    else if (role === 'assistant') { _renderAgentAssistantHist(content, null); }
    else { _renderAgentUserHist(content); }
}

function _renderAgentUserHist(content) {
    if (typeof content === 'string') { _appendTurn('user', content, { streaming: false }); return; }
    if (Array.isArray(content)) {
        content.forEach(function(b) {
            if (!b) return;
            if (b.type === 'tool_result') _histAgentToolResult(b.tool_use_id, b.content, !!b.is_error);
            else if (b.type === 'text' && b.text) _appendTurn('user', b.text, { streaming: false });
        });
    }
}

function _renderAgentAssistantHist(content, openaiToolCalls) {
    var body = _appendTurn('assistant', '', { streaming: false });
    body.innerHTML = '';
    var buf = '';
    var flush = function() {
        if (!buf) return;
        var el = document.createElement('div');
        el.className = 'portal-md';
        el.innerHTML = _renderMarkdown(buf);
        body.appendChild(el);
        buf = '';
    };
    if (typeof content === 'string') { buf = content; flush(); }
    else if (Array.isArray(content)) {
        content.forEach(function(b) {
            if (!b) return;
            if (b.type === 'text') buf += (b.text || '');
            else if (b.type === 'tool_use') { flush(); _appendAgentToolCall(body, b.id, b.name, b.input); }
        });
        flush();
    }
    // OpenAI tool_calls (dict encoding): [{id, function:{name, arguments:"json"}}].
    (openaiToolCalls || []).forEach(function(tc) {
        if (!tc) return;
        var id = tc.id;
        var name = (tc.function && tc.function.name) || tc.name || 'tool';
        var raw = tc.function ? tc.function.arguments : tc.input;
        var input;
        try { input = typeof raw === 'string' ? JSON.parse(raw) : (raw || {}); }
        catch (e) { input = { arguments: raw }; }
        _appendAgentToolCall(body, id, name, input);
    });
}

function _histAgentToolResult(id, content, isError) {
    // History tool_result has no live `display` object — pass empty. `content`
    // may be a dict's inner value (string) or a raw string.
    var text = (content && typeof content === 'object' && !Array.isArray(content))
        ? (content.content != null ? content.content : JSON.stringify(content))
        : content;
    _markAgentToolResult(id, isError, text, {});
}

// ── Pure render helpers (copied verbatim from agent/pages/agent.js) ──
//   _agentDiffHtml   <- renderDiffHtml        (agent.js:834-847)
//   _agentToolExtras <- renderToolDisplayExtras (agent.js:849-880)
//   _agentFmtCost    <- _fmtCost              (agent.js:939-943)
function _agentDiffHtml(diff) {
    if (!diff) return '';
    var out = diff.split('\n').map(function(line) {
        var cls = 'd-ctx';
        if (line.startsWith('+++') || line.startsWith('---')) cls = 'd-hdr';
        else if (line.startsWith('@@')) cls = 'd-hunk';
        else if (line.startsWith('+')) cls = 'd-add';
        else if (line.startsWith('-')) cls = 'd-del';
        return '<span class="' + cls + '">' + esc(line) + '</span>';
    }).join('\n');
    return '<pre class="diff">' + out + '</pre>';
}

function _agentToolExtras(display) {
    if (!display || typeof display !== 'object') return '';
    var parts = [];
    if (display.diff) {
        parts.push('<div class="tc-section"><div class="tc-section-label">diff</div>' +
            _agentDiffHtml(display.diff) + '</div>');
    }
    if (display.preview) {
        parts.push('<div class="tc-section"><div class="tc-section-label">preview</div>' +
            '<pre class="diff">' + esc(display.preview) + '</pre></div>');
    }
    if (display.path && (display.bytes_delta !== undefined || display.action)) {
        var meta = esc(display.path);
        if (display.bytes_delta !== undefined) {
            var d = display.bytes_delta;
            meta += ' · ' + (d >= 0 ? '+' : '') + d + ' bytes';
        }
        if (display.action) meta += ' · ' + esc(display.action);
        if (display.replacements !== undefined) meta += ' · ' + display.replacements + ' replacement(s)';
        parts.push('<div class="tc-meta">' + meta + '</div>');
    }
    if (display.exit_code !== undefined) {
        var ok = display.exit_code === 0;
        parts.push('<div class="tc-meta">' +
            '<span class="tc-exit ' + (ok ? 'ok' : 'bad') + '">exit ' + display.exit_code + '</span>' +
            (display.command ? ' <code>' + esc(display.command) + '</code>' : '') +
            '</div>');
    }
    return parts.join('');
}

function _agentFmtCost(c) {
    if (!c || c <= 0) return '$0';
    if (c < 0.0001) return '<$0.0001';
    return '$' + c.toFixed(4);
}

// ── Verb: Capture ──────────────────────────────────────────────────
// Opens a "Save as" picker — text gets POSTed to the chosen app's
// existing add endpoint. No thread is created.

var _captureModal = null;
function _closeCaptureModal() {
    if (_captureModal && _captureModal.close) { try { _captureModal.close(); } catch(e){} }
    _captureModal = null;
}

async function _handleCapture(text, folder) {
    var rowBtn = 'display:flex;flex-direction:column;gap:2px;align-items:flex-start;padding:12px 14px;border-radius:8px;background:none;border:1px solid var(--border);font-family:inherit;cursor:pointer;text-align:left;width:100%;color:var(--text)';
    var rowHover = 'this.style.borderColor=getComputedStyle(this).getPropertyValue(\'--accent\')';
    var body =
        '<div style="display:flex;flex-direction:column;gap:8px">' +
        '<div style="padding:10px 12px;border-radius:6px;background:var(--bg);border:1px solid var(--border);font-size:12.5px;color:var(--text-secondary);max-height:120px;overflow-y:auto;white-space:pre-wrap">' +
            esc(text) + '</div>' +
        '<button style="' + rowBtn + '" onclick="_captureTo(\'capture\')">' +
            '<span style="font-weight:500">&#x1F4E5; Capture</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Drop into the inbox (untagged)</span>' +
        '</button>' +
        '<button style="' + rowBtn + '" onclick="_captureTo(\'task\')">' +
            '<span style="font-weight:500">&#x2705; Task</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Add as a task (tag=task)</span>' +
        '</button>' +
        '<button style="' + rowBtn + '" onclick="_captureTo(\'journal\')">' +
            '<span style="font-weight:500">&#x1F4D3; Journal</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Append to today\'s journal</span>' +
        '</button>' +
        '<button style="' + rowBtn + '" onclick="_captureTo(\'kb\')">' +
            '<span style="font-weight:500">&#x1F4A1; KB note</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Create a knowledge-base note</span>' +
        '</button>' +
        '<div style="display:flex;justify-content:flex-end;padding-top:6px">' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="_closeCaptureModal()">Cancel</button>' +
        '</div></div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _captureModal = EOS_UI.modal({ title: 'Save as…', body: body });
        // Stash the text so _captureTo can read it without re-fetching from the input.
        _captureModal._text = text;
    }
}

async function _captureTo(target) {
    var text = (_captureModal && _captureModal._text) || '';
    _closeCaptureModal();
    if (!text) return;
    var route, body, label;
    if (target === 'capture') {
        // Let quick-action's classifier pick the tag/route. It falls back to a
        // plain add when no think provider is reachable, so this never dead-ends.
        route = '/quick-action/api/smart-add'; body = { text: text }; label = 'Capture';
    } else if (target === 'task') {
        route = '/quick-action/api/add'; body = { text: text, tag: 'task' }; label = 'Task';
    } else if (target === 'journal') {
        route = '/journal/api/add'; body = { text: text, mood: 'okay' }; label = 'Journal';
    } else if (target === 'kb') {
        // KB notes need a title — derive from the first line.
        var firstLine = text.split('\n')[0].trim();
        var title = firstLine.length > 60 ? firstLine.slice(0, 60) + '…' : firstLine;
        route = '/kb/api/notes'; label = 'KB note';
        body = { kind: 'concept', title: title, body: text };
    } else { return; }
    try {
        var res = await fetch(route, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        var data = await res.json();
        if (data && data.error) throw new Error(data.error);
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Saved to ' + label, true);
        var input = document.getElementById('hero-input');
        if (input) {
            input.value = '';
            input.style.height = 'auto';
            document.getElementById('hero-send').classList.remove('ready');
        }
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Save failed: ' + (err.message || err), false);
    }
}

// ── Verb: Find ────────────────────────────────────────────────────
// Vault search; results render inline in a new pane state.

async function _handleFind(text, folder) {
    history.pushState(null, '', location.pathname + '#find:' + encodeURIComponent(text));
    await _runFind(text);
}

async function _runFind(query) {
    var qEl = document.getElementById('portal-find-query');
    if (qEl) qEl.textContent = '"' + query + '"';
    _showFind();
    var resultsEl = document.getElementById('portal-find-results');
    if (!resultsEl) return;
    resultsEl.innerHTML = '<div class="portal-find-empty">Searching&hellip;</div>';
    try {
        var res = await EOS.api('/search/api/search?q=' + encodeURIComponent(query) + '&top=20');
        var paths = (res && res.results) || [];
        // Also surface threads whose name matches the query — across ALL
        // conversation backends (rooms, assistant, agent), not just rooms.
        var q = query.toLowerCase();
        var threadMatches = []
            .concat((allAgents || []).map(function(a) {
                return { id: a.id, name: a.name || a.id, kind: 'thread' };
            }))
            .concat((allAsstSessions || []).map(function(s) {
                return { id: 'asst:' + s.id, name: s.name || 'Assistant chat', kind: 'assistant chat' };
            }))
            .concat((allAgentSessions || []).map(function(s) {
                return { id: 'agent:' + s.id, name: s.name || 'Agent run', kind: 'agent run' };
            }))
            .filter(function(t) {
                return (t.name || '').toLowerCase().indexOf(q) >= 0;
            }).slice(0, 12);
        // And apps whose label/id matches.
        var appMatches = Object.keys(APP_CATALOG).filter(function(id) {
            var n = (APP_CATALOG[id] || '').toLowerCase();
            return id.toLowerCase().indexOf(query.toLowerCase()) >= 0
                || n.indexOf(query.toLowerCase()) >= 0;
        }).slice(0, 10).map(function(id) {
            return { id: id, name: APP_CATALOG[id] || id };
        });
        _renderFindResults(query, paths, threadMatches, appMatches);
    } catch (err) {
        resultsEl.innerHTML = '<div class="portal-find-empty">Search failed: ' + esc(err.message || String(err)) + '</div>';
    }
}

function _renderFindResults(query, paths, threads, apps) {
    var el = document.getElementById('portal-find-results');
    if (!el) return;
    var groups = [];
    if (paths.length) {
        groups.push('<div class="portal-find-group">' +
            '<h3>Vault notes &middot; ' + paths.length + '</h3>' +
            '<div class="portal-find-stack">' +
            paths.map(function(r) {
                var p = r.path || r;
                var title = p.split('/').pop().replace(/\.md$/i, '');
                var dir = p.indexOf('/') >= 0 ? p.slice(0, p.lastIndexOf('/')) : '';
                return '<a class="portal-find-card" href="#" ' +
                    'onclick="event.preventDefault();_openVaultNote(\'' + escAttr(p) + '\')">' +
                    '<span class="fc-title">' + esc(title) + '</span>' +
                    '<span class="fc-path">' + esc(dir || '/') + '</span>' +
                    '</a>';
            }).join('') +
            '</div></div>');
    }
    if (threads.length) {
        groups.push('<div class="portal-find-group">' +
            '<h3>Threads &middot; ' + threads.length + '</h3>' +
            '<div class="portal-find-stack">' +
            threads.map(function(t) {
                return '<a class="portal-find-card" href="#" ' +
                    'onclick="event.preventDefault();_navTo(\'' + escAttr(t.id) + '\')">' +
                    '<span class="fc-title">' + esc(t.name || t.id) + '</span>' +
                    '<span class="fc-meta">' + esc(t.kind || 'thread') + ' &middot; ' + esc(t.id) + '</span>' +
                    '</a>';
            }).join('') +
            '</div></div>');
    }
    if (apps.length) {
        groups.push('<div class="portal-find-group">' +
            '<h3>Apps &middot; ' + apps.length + '</h3>' +
            '<div class="portal-find-stack">' +
            apps.map(function(a) {
                return '<a class="portal-find-card" href="#" ' +
                    'onclick="event.preventDefault();_navToApp(\'' + escAttr(a.id) + '\')">' +
                    '<span class="fc-title">' + esc(a.name) + '</span>' +
                    '<span class="fc-meta">app &middot; /' + esc(a.id) + '/</span>' +
                    '</a>';
            }).join('') +
            '</div></div>');
    }
    if (!groups.length) {
        el.innerHTML = '<div class="portal-find-empty">No matches for &ldquo;' + esc(query) + '&rdquo;.</div>';
    } else {
        el.innerHTML = groups.join('');
    }
}

function _openVaultNote(relPath) {
    // EOS.viewNote routes through the kernel's note-viewer (which knows the
    // active vault). EOS.openInViewer opens in the external viewer (obsidian://).
    // Prefer in-page viewing — keeps the portal shell.
    if (typeof EOS !== 'undefined' && EOS.viewNote) {
        EOS.viewNote(relPath);
    } else {
        window.open('/note-viewer/?path=' + encodeURIComponent(relPath), '_blank');
    }
}

// ── Verb: Learn ────────────────────────────────────────────────────
// "Process as" picker: video digest (URL) or KB note (any text).

var _learnModal = null;
function _closeLearnModal() {
    if (_learnModal && _learnModal.close) { try { _learnModal.close(); } catch(e){} }
    _learnModal = null;
}

async function _handleLearn(text, folder) {
    var isUrl = /^https?:\/\//i.test(text);
    var rowBtn = 'display:flex;flex-direction:column;gap:2px;align-items:flex-start;padding:12px 14px;border-radius:8px;background:none;border:1px solid var(--border);font-family:inherit;cursor:pointer;text-align:left;width:100%;color:var(--text)';
    var disabledStyle = 'opacity:0.45;cursor:not-allowed';
    var ytBtn = isUrl
        ? '<button style="' + rowBtn + '" onclick="_learnTo(\'video-digest\')">' +
            '<span style="font-weight:500">&#x1F3A5; Video digest</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Queue this URL for transcript + summary</span>' +
          '</button>'
        : '<button style="' + rowBtn + ';' + disabledStyle + '" disabled title="Paste a YouTube URL to enable">' +
            '<span style="font-weight:500">&#x1F3A5; Video digest</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Paste a URL to enable</span>' +
          '</button>';
    var body =
        '<div style="display:flex;flex-direction:column;gap:8px">' +
        '<div style="padding:10px 12px;border-radius:6px;background:var(--bg);border:1px solid var(--border);font-size:12.5px;color:var(--text-secondary);max-height:120px;overflow-y:auto;white-space:pre-wrap">' +
            esc(text) + '</div>' +
        ytBtn +
        '<button style="' + rowBtn + '" onclick="_learnTo(\'kb\')">' +
            '<span style="font-weight:500">&#x1F4A1; KB note</span>' +
            '<span style="font-size:11.5px;color:var(--text-muted)">Save as a structured knowledge note</span>' +
        '</button>' +
        '<div style="display:flex;justify-content:flex-end;padding-top:6px">' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="_closeLearnModal()">Cancel</button>' +
        '</div></div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _learnModal = EOS_UI.modal({ title: 'Process as…', body: body });
        _learnModal._text = text;
    }
}

async function _learnTo(target) {
    var text = (_learnModal && _learnModal._text) || '';
    _closeLearnModal();
    if (!text) return;
    var route, body, label;
    if (target === 'video-digest') {
        route = '/video-digest/api/queue'; body = { url: text }; label = 'Video digest';
    } else if (target === 'kb') {
        var firstLine = text.split('\n')[0].trim();
        var title = firstLine.length > 60 ? firstLine.slice(0, 60) + '…' : firstLine;
        route = '/kb/api/notes'; label = 'KB note';
        body = { kind: 'concept', title: title, body: text };
    } else { return; }
    try {
        var res = await fetch(route, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        var data = await res.json();
        if (data && (data.error || data.ok === false)) throw new Error(data.error || 'failed');
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Sent to ' + label, true);
        var input = document.getElementById('hero-input');
        if (input) {
            input.value = '';
            input.style.height = 'auto';
            document.getElementById('hero-send').classList.remove('ready');
        }
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Send failed: ' + (err.message || err), false);
    }
}

function _updateFolderContextPill() {
    var pill = document.getElementById('hero-folder-pill');
    var nameEl = document.getElementById('hero-folder-name');
    var chips = document.getElementById('portal-chips');
    if (_pendingFolderId) {
        var f = allFolders.find(function(x){ return x.id === _pendingFolderId; });
        if (!f) {
            _pendingFolderId = null;
            if (pill) pill.style.display = 'none';
            if (chips) chips.style.display = '';
            return;
        }
        if (pill) pill.style.display = '';
        if (nameEl) nameEl.textContent = f.name;
        // Folder instructions win — hide mode chips so the UI is honest about
        // which system prompt is in play.
        if (chips) chips.style.display = 'none';
    } else {
        if (pill) pill.style.display = 'none';
        if (chips) chips.style.display = '';
    }
}

function _clearFolderContext() {
    _pendingFolderId = null;
    _updateFolderContextPill();
    var input = document.getElementById('hero-input');
    if (input) input.focus();
}

function _newThreadInFolder(fid) {
    _pendingFolderId = fid;
    // Go to hero state; if a thread is currently open, this exits it.
    if (location.hash) {
        history.pushState(null, '', location.pathname);
        _onHashChange();
    } else {
        _showHero();
    }
    _updateFolderContextPill();
    var input = document.getElementById('hero-input');
    if (input) setTimeout(function(){ input.focus(); }, 30);
}

// ── Open a thread (from sidebar click or hash route) ────────────────

async function openThread(agentId) {
    if (currentAgent && currentAgent.id === agentId) return;
    // Backend-tagged thread ids: `asst:` → assistant, `agent:` → agent.
    if (agentId.indexOf('asst:') === 0) {
        return _openAssistantSession(agentId.slice(5));
    }
    if (agentId.indexOf('agent:') === 0) {
        return _openAgentSession(agentId.slice(6));
    }
    try {
        // Hit the agent record first (history endpoint doesn't return the room itself).
        var agent = null;
        var cached = allAgents.find(function(a){ return a.id === agentId; });
        if (cached) agent = cached;
        else {
            agent = await EOS.api('/rooms/api/agents/' + encodeURIComponent(agentId));
            if (agent && agent.error) throw new Error(agent.error);
        }
        var hist = await EOS.api('/rooms/api/history/' + encodeURIComponent(agentId));
        _openAgent(agent, (hist && hist.messages) || []);
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) {
            EOS.toast('Could not open thread: ' + (err.message || err), false);
        }
        // Bad hash — fall back to hero.
        history.pushState(null, '', location.pathname);
        _showHero();
    }
}

function _openAgent(agent, messages) {
    currentAgent = agent;
    document.getElementById('portal-chat-name').textContent = agent.name || agent.id;
    var modeEl = document.getElementById('portal-chat-mode');
    if (agent._mode_label) {
        modeEl.textContent = agent._mode_label;
        modeEl.style.display = '';
    } else {
        modeEl.style.display = 'none';
    }
    // Model pill — explicit when the thread has a model set; hidden when
    // empty (= falls through to the kernel's default think provider chain,
    // which can't be summarised in one label without a probe).
    var modelEl = document.getElementById('portal-chat-model');
    if (modelEl) {
        if (agent.model) {
            modelEl.textContent = agent.model;
            modelEl.style.display = '';
        } else {
            modelEl.style.display = 'none';
        }
    }
    if (agent._backend === 'agent') {
        _renderAgentHistory(messages);   // block-list encoding, tool cards
    } else {
        _renderMessages(messages);       // rooms/assistant {role,text,ts}
    }
    _showChat();
    _renderSidebar();
}

// ── Message rendering ───────────────────────────────────────────────

function _renderMarkdown(text) {
    if (typeof EOS_UI !== 'undefined' && EOS_UI.renderMarkdown) {
        try { return EOS_UI.renderMarkdown(text); } catch (e) {}
    }
    return esc(text || '');
}

var SESSION_GAP_MS = EOS_CONVERSATION.SESSION_GAP_MS;
var _portalSegments = [];

function _renderMessages(messages) {
    var inner = document.getElementById('portal-messages-inner');
    inner.innerHTML = '';
    _portalSegments = [];
    var seenDay = null;
    var prevTsMs = null;
    (messages || []).forEach(function(m) {
        var dayKey = (m.ts || '').slice(0, 10);
        var curMs = m.ts ? new Date(m.ts).getTime() : NaN;
        var gapMs = (!isNaN(curMs) && prevTsMs !== null) ? (curMs - prevTsMs) : null;
        if (dayKey && dayKey !== seenDay) {
            _appendDivider('day', dayKey, m);
            seenDay = dayKey;
        } else if (gapMs !== null && gapMs > SESSION_GAP_MS) {
            _appendDivider('session', dayKey, m, gapMs);
        }
        if (!isNaN(curMs)) prevTsMs = curMs;
        _appendTurn(m.role || 'assistant', m.text || '', { streaming: false });
    });
    _updateJumpButton();
    _scrollMessagesToBottom();
}

function _appendDivider(kind, dayKey, m, gapMs) {
    var inner = document.getElementById('portal-messages-inner');
    var segId = 'seg-' + _portalSegments.length;
    var el = document.createElement('div');
    if (kind === 'day') {
        el.className = 'portal-day-divider';
        el.setAttribute('data-segment-id', segId);
        el.setAttribute('data-day', dayKey);
        el.innerHTML = '<span>' + esc(EOS_CONVERSATION.formatDay(dayKey)) + '</span>';
        _portalSegments.push({
            id: segId, kind: 'day', day: dayKey, ts: m.ts,
            label: EOS_CONVERSATION.formatDay(dayKey),
            preview: EOS_CONVERSATION.segmentPreview(m)
        });
    } else {
        el.className = 'portal-session-divider';
        el.setAttribute('data-segment-id', segId);
        el.innerHTML = '<span>&middot; &middot; &middot; ' + esc(EOS_CONVERSATION.formatGap(gapMs)) +
                       ' later &middot; &middot; &middot;</span>';
        _portalSegments.push({
            id: segId, kind: 'session', day: dayKey, ts: m.ts,
            label: EOS_CONVERSATION.segmentTimeLabel(m.ts),
            preview: EOS_CONVERSATION.segmentPreview(m)
        });
    }
    inner.appendChild(el);
}

function _updateJumpButton() {
    var btn = document.getElementById('portal-jump-btn');
    var lbl = document.getElementById('portal-jump-label');
    if (!btn) return;
    var n = _portalSegments.length;
    if (n > 1) {
        btn.style.display = '';
        if (lbl) lbl.textContent = n + ' segments';
    } else {
        btn.style.display = 'none';
    }
}

// Jump-to-segment picker delegates to EOS_CONVERSATION. The shared module
// owns modal rendering + scroll/flash; portal supplies the segment cache.
function openJumpToSegment() {
    EOS_CONVERSATION.openJumpModal(_portalSegments || [], _jumpToSegment);
}
function _jumpToSegment(segId) {
    EOS_CONVERSATION.scrollToSegment(segId);
}

function _appendTurn(role, text, opts) {
    opts = opts || {};
    var inner = document.getElementById('portal-messages-inner');
    var turn = document.createElement('div');
    turn.className = 'portal-turn ' + (role === 'user' ? 'user' : 'assistant');
    var label = role === 'user' ? 'You' : (currentAgent && currentAgent.name) || 'Assistant';
    var body = document.createElement('div');
    body.className = 'body' + (opts.streaming ? ' streaming' : '');
    if (role === 'user') {
        body.textContent = text;
    } else {
        body.innerHTML = text ? _renderMarkdown(text) : '<span class="portal-typing"><span></span><span></span><span></span></span>';
    }
    var labelEl = document.createElement('div');
    labelEl.className = 'role';
    labelEl.textContent = label;
    turn.appendChild(labelEl);
    turn.appendChild(body);
    inner.appendChild(turn);
    _scrollMessagesToBottom();
    return body;  // caller can mutate this for streaming
}

function _scrollMessagesToBottom() {
    var box = document.getElementById('portal-messages');
    if (box) box.scrollTop = box.scrollHeight;
}

// ── Send into an open thread ────────────────────────────────────────

async function sendInThread() {
    if (sending || !currentAgent) return;
    var input = document.getElementById('chat-input');
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    input.style.height = 'auto';
    document.getElementById('chat-send').classList.remove('ready');
    await _sendText(text);
}

async function _sendText(text) {
    if (!currentAgent) return;
    // Dispatch to the thread's backend. Rooms streams (below); assistant blocks;
    // agent drives its WebSocket (events render the reply).
    if (currentAgent._backend === 'agent') {
        if (!_agentWs || _agentWs.readyState !== WebSocket.OPEN) {
            if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Agent not connected — reopen the run', false);
            return;
        }
        _appendTurn('user', text, { streaming: false });
        _agentWs.send(JSON.stringify({ type: 'message', text: text }));
        _agentSetSending(true);
        return;
    }
    if (currentAgent._backend === 'assistant') {
        sending = true;
        document.getElementById('chat-send').disabled = true;
        try { await _sendAssistant(text); }
        finally {
            sending = false;
            document.getElementById('chat-send').disabled = false;
        }
        return;
    }
    sending = true;
    document.getElementById('chat-send').disabled = true;
    _appendTurn('user', text, { streaming: false });
    // Empty assistant bubble that'll fill as chunks arrive.
    var bodyEl = _appendTurn('assistant', '', { streaming: true });
    var fullText = '';
    try {
        for await (var chunk of EOS.streamPost('/rooms/api/chat/stream', {
            agent_id: currentAgent.id,
            text: text
        })) {
            if (typeof chunk.text_replace === 'string') {
                fullText = chunk.text_replace;
                bodyEl.innerHTML = fullText ? _renderMarkdown(fullText) : '';
                continue;
            }
            var t = chunk.text || '';
            if (t) {
                fullText += t;
                bodyEl.innerHTML = _renderMarkdown(fullText);
                _scrollMessagesToBottom();
            }
            if (chunk.done) break;
        }
        if (!fullText) bodyEl.textContent = 'No response.';
        bodyEl.classList.remove('streaming');
    } catch (err) {
        bodyEl.textContent = 'Error: ' + (err && err.message ? err.message : err);
        bodyEl.classList.remove('streaming');
    }
    sending = false;
    document.getElementById('chat-send').disabled = false;
    _scrollMessagesToBottom();
    // Bump this agent to the top of the sidebar list.
    if (currentAgent) {
        var idx = allAgents.findIndex(function(a){ return a.id === currentAgent.id; });
        if (idx > 0) {
            var a = allAgents.splice(idx, 1)[0];
            allAgents.unshift(a);
            _renderSidebar();
        }
    }
}

// ── Sidebar — threads list ──────────────────────────────────────────

// ── Folders (= "Rooms" in UI) ───────────────────────────────────────
//
// Folders are portal's organisational layer over apps/rooms/ agents.
// A folder is `{id, name, default_mode, thread_ids: [...]}`. Threads can
// belong to at most one folder; threads not in any folder render under
// the "Other threads" group at the bottom. Expand state is per-browser
// (localStorage), so refreshing doesn't collapse every folder.

var allFolders = [];
var folderExpanded = {};  // {folder_id: bool}

function _loadExpandState() {
    try {
        var raw = localStorage.getItem('portal.folders.expanded.v1');
        if (raw) folderExpanded = JSON.parse(raw) || {};
    } catch (e) { folderExpanded = {}; }
}
function _saveExpandState() {
    try { localStorage.setItem('portal.folders.expanded.v1', JSON.stringify(folderExpanded)); }
    catch (e) {}
}

async function loadFolders() {
    try {
        var res = await EOS.api('/portal/api/folders');
        allFolders = (res && res.folders) || [];
    } catch (e) {
        allFolders = [];
    }
}

async function loadAgents() {
    var el = document.getElementById('portal-rooms');
    try {
        var rooms = await EOS.api('/rooms/api/agents?status=active');
        allAgents = (rooms || []).filter(function(r) {
            return r.tier !== 'builtin';
        });
        allAgents.sort(function(a, b) {
            return (b.created || '').localeCompare(a.created || '');
        });
    } catch (e) {
        el.innerHTML = '<div class="portal-empty">Could not load threads.</div>';
        allAgents = [];
        _bkErrors.rooms = true;
        return;
    }
    _bkErrors.rooms = false;
}

// Per-backend load-failure flags — distinguishes "backend unreachable" from
// "no sessions yet" so the mode buttons can signal degraded state (C1).
var _bkErrors = { rooms: false, assistant: false, agent: false };

async function loadAsstSessions() {
    try {
        var res = await EOS.api('/assistant/api/sessions');
        allAsstSessions = Array.isArray(res) ? res : [];
        _bkErrors.assistant = false;
    } catch (e) { allAsstSessions = []; _bkErrors.assistant = true; }
}

async function loadAgentSessions() {
    try {
        var res = await EOS.api('/agent/api/sessions');
        allAgentSessions = Array.isArray(res) ? res : [];
        _bkErrors.agent = false;
    } catch (e) { allAgentSessions = []; _bkErrors.agent = true; }
}

function _syncBackendHealth() {
    document.querySelectorAll('.portal-bk').forEach(function(b) {
        var bk = b.dataset.backend;
        if (!(bk in _bkErrors)) return;   // code has no list probe
        var bad = !!_bkErrors[bk];
        b.classList.toggle('degraded', bad);
        b.title = bad
            ? (CONV_BACKENDS[bk].label + ' unreachable — its session list failed to load')
            : b.getAttribute('data-title-ok') || b.title;
        if (!b.getAttribute('data-title-ok') && !bad) b.setAttribute('data-title-ok', b.title);
    });
}

async function loadSidebar() {
    await Promise.all([
        loadAgents(), loadFolders(), loadAsstSessions(),
        loadAgentSessions(), _loadPinsFromServer()
    ]);
    _renderSidebar();
    _syncBackendHealth();
}

function _agentById(id) {
    for (var i = 0; i < allAgents.length; i++) {
        if (allAgents[i].id === id) return allAgents[i];
    }
    return null;
}

// ── Pin store ───────────────────────────────────────────────────────
// Server-backed via /portal/api/pins (cross-device sync). The client
// keeps an in-memory cache so _isPinned() can stay sync (used during
// _renderSidebar). Toggles are optimistic — UI updates first, server
// catches up; on sync failure the change reverts + a toast surfaces.
//
// V2.11 used localStorage; on first load post-V2.12 we migrate any
// legacy local pins up to the server (idempotent — POST is a no-op
// for already-pinned ids) then clear the legacy key.

var LEGACY_PINNED_KEY = 'portal.pinned.threads.v1';
var _pinsCache = [];

async function _loadPinsFromServer() {
    try {
        var res = await EOS.api('/portal/api/pins');
        _pinsCache = (res && res.threads) || [];
        // One-time migration of any V2.11 localStorage pins.
        try {
            var raw = localStorage.getItem(LEGACY_PINNED_KEY);
            if (raw) {
                var legacy = JSON.parse(raw);
                if (Array.isArray(legacy) && legacy.length) {
                    // Push in reverse so the ordering ends up newest-first
                    // (POST inserts at position 0 server-side).
                    for (var i = legacy.length - 1; i >= 0; i--) {
                        var tid = legacy[i];
                        if (tid && _pinsCache.indexOf(tid) < 0) {
                            await fetch('/portal/api/pins/' + encodeURIComponent(tid), { method: 'POST' });
                        }
                    }
                    var r2 = await EOS.api('/portal/api/pins');
                    _pinsCache = (r2 && r2.threads) || _pinsCache;
                }
                localStorage.removeItem(LEGACY_PINNED_KEY);
            }
        } catch (e) { /* migration is non-fatal */ }
    } catch (e) {
        // Server unreachable — fall back to legacy localStorage so the
        // user doesn't lose their V2.11 pins while the daemon is down.
        try {
            var raw = localStorage.getItem(LEGACY_PINNED_KEY);
            var arr = raw ? JSON.parse(raw) : [];
            _pinsCache = Array.isArray(arr) ? arr : [];
        } catch (e2) { _pinsCache = []; }
    }
}

function _isPinned(threadId) {
    return _pinsCache.indexOf(threadId) >= 0;
}

async function _togglePin(threadId) {
    var wasPinned = _isPinned(threadId);
    // Optimistic: mutate cache first so the UI feels instant.
    if (wasPinned) {
        _pinsCache = _pinsCache.filter(function(t) { return t !== threadId; });
    } else {
        _pinsCache = [threadId].concat(_pinsCache.filter(function(t) { return t !== threadId; }));
    }
    _renderSidebar();
    try {
        var method = wasPinned ? 'DELETE' : 'POST';
        var res = await fetch('/portal/api/pins/' + encodeURIComponent(threadId), { method: method });
        var data = await res.json();
        if (data && Array.isArray(data.threads)) _pinsCache = data.threads;
        _renderSidebar();
    } catch (err) {
        // Revert + surface.
        if (wasPinned) {
            _pinsCache = [threadId].concat(_pinsCache);
        } else {
            _pinsCache = _pinsCache.filter(function(t) { return t !== threadId; });
        }
        _renderSidebar();
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Pin sync failed', false);
    }
}

// Helper: render one thread row (used in folders + ungrouped + pinned).
function _renderThreadRow(thread, opts) {
    opts = opts || {};
    var name = esc(thread.name || thread.id);
    var currentId = currentAgent ? currentAgent.id : null;
    var active = (thread.id === currentId) ? ' active' : '';
    var nested = opts.nested ? ' nested' : '';
    var pinned = _isPinned(thread.id);
    var pinClass = pinned ? 'r-pin pinned' : 'r-pin';
    var pinIcon = pinned ? '&#x2605;' : '&#x2606;';  // ★ filled / ☆ outline
    return '<a class="portal-room' + nested + active + '"' +
        ' href="#' + encodeURIComponent(thread.id) + '"' +
        ' onclick="event.preventDefault();_navTo(\'' + escAttr(thread.id) + '\');"' +
        ' title="' + name + '">' +
        '<span class="r-name">' + name + '</span>' +
        '<button class="' + pinClass + '" onclick="event.preventDefault();event.stopPropagation();_togglePin(\'' + escAttr(thread.id) + '\')" title="' + (pinned ? 'Unpin' : 'Pin') + '">' + pinIcon + '</button>' +
        '<button class="r-menu" onclick="event.preventDefault();event.stopPropagation();_threadMenu(\'' + escAttr(thread.id) + '\', event)" title="Move&hellip;">&#x22EF;</button>' +
        '</a>';
}

// ── Cross-backend sidebar rows (assistant + agent threads) ──────────
// Rooms rows use _renderThreadRow (folder machinery). Asst/agent rows get the
// same daily affordances — pin, rename, delete — via the backends' existing
// session endpoints. No folder membership (folders are rooms-only).

// Resolve any thread id (bare rooms id, asst:<sid>, agent:<sid>) to a
// renderable {id, name, kind}. Used by the pinned section so a pinned
// asst:/agent: id no longer silently drops (the old _agentById-only resolve).
function _threadById(tid) {
    if (tid.indexOf('asst:') === 0) {
        var s = allAsstSessions.find(function(x){ return 'asst:' + x.id === tid; });
        return s ? { id: tid, name: s.name || 'Assistant chat', kind: 'assistant' } : null;
    }
    if (tid.indexOf('agent:') === 0) {
        var a = allAgentSessions.find(function(x){ return 'agent:' + x.id === tid; });
        return a ? { id: tid, name: a.name || 'Agent run', kind: 'agent' } : null;
    }
    var r = _agentById(tid);
    return r ? { id: r.id, name: r.name || r.id, kind: 'rooms', _room: r } : null;
}

function _renderExtRow(tid, name, opts) {
    opts = opts || {};
    var nm = esc(name || tid);
    var active = (currentAgent && currentAgent.id === tid) ? ' active' : '';
    var pinned = _isPinned(tid);
    var pinClass = pinned ? 'r-pin pinned' : 'r-pin';
    var pinIcon = pinned ? '&#x2605;' : '&#x2606;';
    return '<a class="portal-room' + active + '"' +
        ' href="#' + encodeURIComponent(tid) + '"' +
        ' onclick="event.preventDefault();_navTo(\'' + escAttr(tid) + '\');"' +
        ' title="' + nm + '">' +
        '<span class="r-name">' + nm + '</span>' +
        '<button class="' + pinClass + '" onclick="event.preventDefault();event.stopPropagation();_togglePin(\'' + escAttr(tid) + '\')" title="' + (pinned ? 'Unpin' : 'Pin') + '">' + pinIcon + '</button>' +
        '<button class="r-menu" onclick="event.preventDefault();event.stopPropagation();_extThreadMenu(\'' + escAttr(tid) + '\')" title="Rename / delete&hellip;">&#x22EF;</button>' +
        '</a>';
}

var _extMenuModal = null;
function _closeExtMenu() {
    if (_extMenuModal && _extMenuModal.close) { try { _extMenuModal.close(); } catch (e) {} }
    _extMenuModal = null;
}

function _extThreadMenu(tid) {
    var t = _threadById(tid);
    if (!t) return;
    var rowBtn = 'display:flex;justify-content:flex-start;padding:9px 12px;border-radius:6px;background:none;border:none;font-family:inherit;font-size:13px;cursor:pointer;text-align:left;gap:8px;width:100%';
    var body = '<div style="display:flex;flex-direction:column;gap:4px">' +
        '<button style="' + rowBtn + ';color:var(--text)" onclick="_renameExtThread(\'' + escAttr(tid) + '\')">Rename&hellip;</button>' +
        '<button style="' + rowBtn + ';color:var(--danger)" onclick="_deleteExtThread(\'' + escAttr(tid) + '\')">Delete</button>' +
        '</div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _extMenuModal = EOS_UI.modal({ title: t.name, body: body });
    }
}

function _extEndpoint(tid) {
    // → {url, method} for the rename call; delete uses the same url with DELETE.
    if (tid.indexOf('asst:') === 0) {
        return { url: '/assistant/api/sessions/' + encodeURIComponent(tid.slice(5)), rename: 'PUT' };
    }
    return { url: '/agent/api/sessions/' + encodeURIComponent(tid.slice(6)), rename: 'PATCH' };
}

function _renameExtThread(tid) {
    _closeExtMenu();
    var t = _threadById(tid);
    if (!t) return;
    EOS_UI.formModal('Rename', [
        {key: 'name', label: 'Name', value: t.name}
    ], async function(vals) {
        var name = (vals.name || '').trim();
        if (!name || name === t.name) return;
        try {
            var ep = _extEndpoint(tid);
            var res = await fetch(ep.url, {
                method: ep.rename,
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: name })
            }).then(function(r){ return r.json(); });
            if (res && res.error) throw new Error(res.error);
            // Update the cache in place + any open header.
            var cache = tid.indexOf('asst:') === 0 ? allAsstSessions : allAgentSessions;
            var sid = tid.slice(tid.indexOf(':') + 1);
            var row = cache.find(function(x){ return x.id === sid; });
            if (row) row.name = name;
            if (currentAgent && currentAgent.id === tid) {
                currentAgent.name = name;
                var h = document.getElementById('portal-chat-name');
                if (h) h.textContent = name;
            }
            _renderSidebar();
            if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Renamed', true);
        } catch (err) {
            if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Rename failed: ' + (err.message || err), false);
        }
    });
}

async function _deleteExtThread(tid) {
    _closeExtMenu();
    var t = _threadById(tid);
    if (!t) return;
    var go = true;
    if (typeof EOS_UI !== 'undefined' && EOS_UI.confirm) {
        go = await EOS_UI.confirm({
            message: 'Delete "' + (t.name || tid) + '"? The conversation history is removed from its backend and cannot be undone.',
            action: 'Delete',
            danger: true
        });
    }
    if (!go) return;
    try {
        var ep = _extEndpoint(tid);
        await fetch(ep.url, { method: 'DELETE' });
        var sid = tid.slice(tid.indexOf(':') + 1);
        if (tid.indexOf('asst:') === 0) {
            allAsstSessions = allAsstSessions.filter(function(x){ return x.id !== sid; });
        } else {
            allAgentSessions = allAgentSessions.filter(function(x){ return x.id !== sid; });
        }
        if (_isPinned(tid)) _togglePin(tid);
        if (currentAgent && currentAgent.id === tid) newThread();
        _renderSidebar();
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Deleted', true);
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Delete failed: ' + (err.message || err), false);
    }
}

function _renderSidebar() {
    var el = document.getElementById('portal-rooms');
    var currentId = currentAgent ? currentAgent.id : null;

    // ── Pinned section (above Rooms) ──
    // Resolve across ALL backends — a pinned asst:/agent: thread renders too.
    var pinnedThreads = _pinsCache.map(_threadById).filter(Boolean);
    var pinnedSection = document.getElementById('portal-pinned-section');
    var pinnedListEl = document.getElementById('portal-pinned-list');
    if (pinnedSection && pinnedListEl) {
        if (pinnedThreads.length) {
            pinnedSection.style.display = '';
            pinnedListEl.innerHTML = pinnedThreads.map(function(t) {
                return t.kind === 'rooms' ? _renderThreadRow(t._room, {}) : _renderExtRow(t.id, t.name, {});
            }).join('');
        } else {
            pinnedSection.style.display = 'none';
            pinnedListEl.innerHTML = '';
        }
    }

    // Build a set of grouped thread ids (in any folder).
    var grouped = {};
    allFolders.forEach(function(f) {
        (f.thread_ids || []).forEach(function(tid) { grouped[tid] = f.id; });
    });

    var parts = [];

    // ── Folders (Rooms) ──
    if (allFolders.length) {
        allFolders.forEach(function(f) {
            var isOpen = !!folderExpanded[f.id];
            var threads = (f.thread_ids || [])
                .map(_agentById)
                .filter(Boolean);
            parts.push(
                '<div class="portal-folder' + (isOpen ? ' open' : '') + '"' +
                ' onclick="_toggleFolder(\'' + escAttr(f.id) + '\')">' +
                '<span class="chev">&#x25B6;</span>' +
                '<span class="f-name" title="' + escAttr(f.name) + '">' + esc(f.name) + '</span>' +
                '<span class="f-count">' + threads.length + '</span>' +
                '<button class="f-menu" onclick="event.stopPropagation();_folderMenu(\'' + escAttr(f.id) + '\', event)" title="Manage room">&#x22EF;</button>' +
                '</div>'
            );
            if (isOpen) {
                // "+ New thread in this room" — always shown so an empty folder
                // is immediately useful, not just a dead label.
                parts.push(
                    '<button class="portal-folder-newthread" onclick="_newThreadInFolder(\'' +
                    escAttr(f.id) + '\')">+ New thread in this room</button>'
                );
                if (!threads.length) {
                    parts.push('<div class="portal-empty" style="padding-left:22px">No threads yet.</div>');
                } else {
                    threads.forEach(function(t) {
                        parts.push(_renderThreadRow(t, { nested: true }));
                    });
                }
            }
        });
    }

    // ── Ungrouped threads ──
    var ungrouped = allAgents.filter(function(a) { return !grouped[a.id]; }).slice(0, 30);
    if (ungrouped.length) {
        if (allFolders.length) {
            parts.push('<h3 style="margin:12px 0 4px;padding:0 10px;font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-secondary);opacity:0.7;font-weight:600">Other threads</h3>');
        }
        ungrouped.forEach(function(r) {
            parts.push(_renderThreadRow(r, {}));
        });
    } else if (!allFolders.length && !allAgents.length && !allAsstSessions.length && !allAgentSessions.length) {
        parts.push('<div class="portal-empty">No threads yet.</div>');
    }

    // ── Assistant chats (assistant backend) ──
    // Own section — assistant sessions aren't rooms agents, so they skip the
    // folder/pin machinery (rooms concepts) and render as simple rows.
    if (allAsstSessions.length) {
        parts.push('<h3 style="margin:12px 0 4px;padding:0 10px;font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-secondary);opacity:0.7;font-weight:600">&#x1F4AC; Assistant chats</h3>');
        allAsstSessions.slice(0, 20).forEach(function(s) {
            parts.push(_renderExtRow('asst:' + s.id, s.name || 'Assistant chat', {}));
        });
    }

    // ── Agent runs (agent backend) ──
    if (allAgentSessions.length) {
        parts.push('<h3 style="margin:12px 0 4px;padding:0 10px;font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-secondary);opacity:0.7;font-weight:600">&#x1F916; Agent runs</h3>');
        allAgentSessions.slice(0, 20).forEach(function(s) {
            parts.push(_renderExtRow('agent:' + s.id, s.name || 'Agent run', {}));
        });
    }

    el.innerHTML = parts.join('');

    // Apps section highlight (iframe state)
    var hash = (location.hash || '').replace(/^#/, '');
    var activeApp = hash.indexOf('app:') === 0 ? decodeURIComponent(hash).slice(4) : null;
    Object.keys(APP_LABELS).forEach(function(appId) {
        var link = document.getElementById('side-link-' + appId);
        if (!link) return;
        if (appId === activeApp) link.classList.add('active');
        else link.classList.remove('active');
    });
}

function _toggleFolder(fid) {
    folderExpanded[fid] = !folderExpanded[fid];
    _saveExpandState();
    _renderSidebar();
}

// ── Folder actions ──────────────────────────────────────────────────

function createFolder() {
    EOS_UI.formModal('New room', [
        {key: 'name', label: 'Room name', placeholder: 'e.g. Career'}
    ], async function(vals) {
        var name = (vals.name || '').trim();
        if (!name) return;
        try {
            var f = await fetch('/portal/api/folders', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: name })
            }).then(function(r){ return r.json(); });
            if (!f || f.error) throw new Error(f && f.error || 'create failed');
            folderExpanded[f.id] = true;
            _saveExpandState();
            await loadFolders();
            _renderSidebar();
        } catch (err) {
            if (typeof EOS !== 'undefined' && EOS.toast) {
                EOS.toast('Could not create room: ' + (err.message || err), false);
            }
        }
    });
}

function _folderMenu(fid, ev) {
    var folder = allFolders.find(function(f){ return f.id === fid; });
    if (!folder) return;
    var rowBtn = 'display:flex;justify-content:flex-start;padding:9px 12px;border-radius:6px;background:none;border:none;font-family:inherit;font-size:13px;cursor:pointer;text-align:left;gap:8px;width:100%';
    var body = '<div style="display:flex;flex-direction:column;gap:4px">' +
        '<button style="' + rowBtn + ';color:var(--text)" onclick="_editFolderInstructions(\'' + escAttr(fid) + '\')">Edit room instructions&hellip;</button>' +
        '<button style="' + rowBtn + ';color:var(--text)" onclick="_renameFolder(\'' + escAttr(fid) + '\')">Rename room&hellip;</button>' +
        '<button style="' + rowBtn + ';color:var(--danger,#da3633)" onclick="_deleteFolder(\'' + escAttr(fid) + '\')">Delete room</button>' +
        '<div style="padding:8px 12px;font-size:11px;color:var(--text-muted);border-top:1px solid var(--border);margin-top:4px">Deleting a room releases its threads back to &ldquo;Other threads&rdquo;. Conversation history is unaffected.</div>' +
        '</div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _folderMenuModal = EOS_UI.modal({ title: folder.name, body: body });
    }
}

// Edit instructions — folder-level system prompt that applies to NEW threads
// created inside the room. (Existing threads keep their original prompt; live
// propagation is V2.8.) This is the claude.ai Projects "custom instructions"
// equivalent, with the honest caveat surfaced in the modal copy.

function _editFolderInstructions(fid) {
    _closeFolderMenu();
    var folder = allFolders.find(function(f){ return f.id === fid; });
    if (!folder) return;
    var modes = ['think', 'learn', 'code', 'life'];
    var defaultMode = folder.default_mode || 'think';
    var sp = folder.system_prompt || '';
    var modeOpts = modes.map(function(m) {
        var sel = (m === defaultMode) ? ' selected' : '';
        return '<option value="' + m + '"' + sel + '>' + m.charAt(0).toUpperCase() + m.slice(1) + '</option>';
    }).join('');
    var body =
        '<div style="display:flex;flex-direction:column;gap:14px">' +
        '<label style="display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--text-secondary)">' +
            'Name' +
            '<input id="fi-name" type="text" value="' + escAttr(folder.name || '') + '" ' +
            'style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-family:inherit;font-size:13.5px">' +
        '</label>' +
        '<label style="display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--text-secondary)">' +
            'Default mode (when no instructions are set)' +
            '<select id="fi-mode" style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-family:inherit;font-size:13.5px">' +
                modeOpts + '</select>' +
        '</label>' +
        '<label style="display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--text-secondary)">' +
            '<span style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">' +
                '<span>Model (leave empty for the default chain)</span>' +
                '<span style="font-size:10.5px;opacity:0.7;font-family:var(--font-mono,monospace)">e.g. claude-cli, ollama, gpt-4.1-mini</span>' +
            '</span>' +
            '<input id="fi-model" type="text" placeholder="(default — first in your think provider chain)" value="' +
                escAttr(folder.model || '') + '" ' +
                'style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-family:var(--font-mono,ui-monospace,monospace);font-size:12.5px">' +
        '</label>' +
        '<label style="display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--text-secondary)">' +
            'Instructions (applied to new threads created in this room)' +
            '<textarea id="fi-prompt" rows="8" placeholder="e.g. You are a careful pair-programmer working on the EmptyOS codebase…" ' +
            'style="padding:10px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-family:inherit;font-size:13px;line-height:1.55;resize:vertical;min-height:140px">' +
            esc(sp) + '</textarea>' +
        '</label>' +
        '<div style="padding:10px 12px;font-size:11.5px;color:var(--text-muted);background:var(--bg);border:1px solid var(--border);border-radius:6px;line-height:1.5">' +
            '<strong>Note:</strong> instructions apply to <em>new</em> threads created in this room. ' +
            'Existing threads keep the system prompt they were created with. Live propagation across existing threads is coming.' +
        '</div>' +
        '<div style="display:flex;justify-content:flex-end;gap:8px;padding-top:4px">' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="_closeFolderMenu()">Cancel</button>' +
            '<button class="eos-btn-sm" style="background:var(--accent);color: var(--accent-ink, #fff);border:0;padding:6px 14px;border-radius:6px;font-family:inherit;font-size:13px;cursor:pointer" onclick="_saveFolderInstructions(\'' + escAttr(fid) + '\')">Save</button>' +
        '</div>' +
        '</div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _folderMenuModal = EOS_UI.modal({ title: 'Edit "' + (folder.name || 'room') + '"', body: body });
    }
}

async function _saveFolderInstructions(fid) {
    var nameEl = document.getElementById('fi-name');
    var modeEl = document.getElementById('fi-mode');
    var modelEl = document.getElementById('fi-model');
    var promptEl = document.getElementById('fi-prompt');
    if (!nameEl || !modeEl || !promptEl) return;
    var name = (nameEl.value || '').trim();
    var mode = modeEl.value;
    var model = modelEl ? (modelEl.value || '').trim() : '';
    var sp = promptEl.value || '';
    if (!name) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Name is required', false);
        return;
    }
    try {
        await fetch('/portal/api/folders/' + encodeURIComponent(fid), {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                name: name, default_mode: mode,
                model: model, system_prompt: sp
            })
        });
        _closeFolderMenu();
        await loadFolders();
        _renderSidebar();
        _updateFolderContextPill();
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Room updated', true);
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Save failed', false);
    }
}
var _folderMenuModal = null;
function _closeFolderMenu() {
    if (_folderMenuModal && _folderMenuModal.close) { try { _folderMenuModal.close(); } catch (e) {} }
    _folderMenuModal = null;
}

function _renameFolder(fid) {
    _closeFolderMenu();
    var folder = allFolders.find(function(f){ return f.id === fid; });
    if (!folder) return;
    EOS_UI.formModal('Rename room', [
        {key: 'name', label: 'Room name', value: folder.name}
    ], async function(vals) {
        var name = (vals.name || '').trim();
        if (!name || name === folder.name) return;
        try {
            await fetch('/portal/api/folders/' + encodeURIComponent(fid), {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: name })
            });
            await loadFolders();
            _renderSidebar();
        } catch (err) {
            if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Rename failed', false);
        }
    });
}

async function _deleteFolder(fid) {
    _closeFolderMenu();
    var folder = allFolders.find(function(f){ return f.id === fid; });
    if (!folder) return;
    if (!await EOS_UI.confirm({
        message: 'Delete room "' + folder.name + '"? Its threads stay in "Other threads".',
        action: 'Delete', danger: true
    })) return;
    try {
        await fetch('/portal/api/folders/' + encodeURIComponent(fid), { method: 'DELETE' });
        await loadFolders();
        _renderSidebar();
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Delete failed', false);
    }
}

// ── Thread move menu ────────────────────────────────────────────────

function _threadMenu(threadId, ev) {
    var folders = allFolders;
    // Find current folder (if any) for the "Remove from room" item.
    var currentFolder = null;
    for (var i = 0; i < folders.length; i++) {
        if ((folders[i].thread_ids || []).indexOf(threadId) >= 0) {
            currentFolder = folders[i];
            break;
        }
    }
    var rows = folders.map(function(f) {
        var isCurrent = currentFolder && currentFolder.id === f.id;
        var prefix = isCurrent ? '&check; ' : '&nbsp;&nbsp;&nbsp;&nbsp;';
        return '<button class="jump-day-item" style="display:flex;justify-content:flex-start;padding:9px 12px;border-radius:6px;background:none;border:none;color:var(--text);font-family:inherit;font-size:13px;cursor:pointer;text-align:left;gap:8px"' +
               ' onclick="_moveThread(\'' + escAttr(threadId) + '\',\'' + escAttr(f.id) + '\')">' +
               prefix + esc(f.name) + '</button>';
    }).join('');
    var removeRow = currentFolder
        ? '<button class="jump-day-item" style="display:flex;justify-content:flex-start;padding:9px 12px;border-radius:6px;background:none;border:none;color:var(--text-secondary);font-family:inherit;font-size:13px;cursor:pointer;text-align:left;gap:8px"' +
          ' onclick="_detachThread(\'' + escAttr(threadId) + '\',\'' + escAttr(currentFolder.id) + '\')">&nbsp;&nbsp;&nbsp;&nbsp;Remove from &ldquo;' + esc(currentFolder.name) + '&rdquo;</button>'
        : '';
    var newRoomRow = '<button class="jump-day-item" style="display:flex;justify-content:flex-start;padding:9px 12px;border-radius:6px;background:none;border:none;color:var(--accent);font-family:inherit;font-size:13px;cursor:pointer;text-align:left;gap:8px"' +
        ' onclick="_moveThreadToNewRoom(\'' + escAttr(threadId) + '\')">+ New room&hellip;</button>';
    var body = '<div style="display:flex;flex-direction:column;gap:2px;max-height:60vh;overflow-y:auto">' +
        (rows || '<div class="portal-empty">No rooms yet.</div>') +
        removeRow + newRoomRow +
        '</div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _threadMenuModal = EOS_UI.modal({ title: 'Move thread to&hellip;', body: body });
    }
}
var _threadMenuModal = null;
function _closeThreadMenu() {
    if (_threadMenuModal && _threadMenuModal.close) { try { _threadMenuModal.close(); } catch (e) {} }
    _threadMenuModal = null;
}

async function _moveThread(threadId, folderId) {
    _closeThreadMenu();
    try {
        await fetch('/portal/api/folders/' + encodeURIComponent(folderId) + '/threads', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ thread_id: threadId })
        });
        folderExpanded[folderId] = true;
        _saveExpandState();
        await loadFolders();
        _renderSidebar();
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Move failed', false);
    }
}

async function _detachThread(threadId, folderId) {
    _closeThreadMenu();
    try {
        await fetch('/portal/api/folders/' + encodeURIComponent(folderId) +
                    '/threads/' + encodeURIComponent(threadId), { method: 'DELETE' });
        await loadFolders();
        _renderSidebar();
    } catch (err) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Remove failed', false);
    }
}

function _moveThreadToNewRoom(threadId) {
    _closeThreadMenu();
    EOS_UI.formModal('New room', [
        {key: 'name', label: 'Room name', placeholder: 'e.g. Career'}
    ], async function(vals) {
        var name = (vals.name || '').trim();
        if (!name) return;
        try {
            var f = await fetch('/portal/api/folders', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: name })
            }).then(function(r){ return r.json(); });
            if (!f || f.error) throw new Error(f && f.error || 'create failed');
            await fetch('/portal/api/folders/' + encodeURIComponent(f.id) + '/threads', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ thread_id: threadId })
            });
            folderExpanded[f.id] = true;
            _saveExpandState();
            await loadFolders();
            _renderSidebar();
        } catch (err) {
            if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Could not create room', false);
        }
    });
}

function _navTo(agentId) {
    var newHash = '#' + encodeURIComponent(agentId);
    if (location.hash === newHash) return;
    history.pushState(null, '', location.pathname + newHash);
    _onHashChange();
}

function _navToApp(appId) {
    var newHash = '#app:' + encodeURIComponent(appId);
    if (location.hash === newHash) return;
    history.pushState(null, '', location.pathname + newHash);
    _onHashChange();
}

// ── Home strip: continue-where-you-left-off + needs-you chips ───────
// Lazy (after sidebar load), fail-soft (any fetch error → that piece hidden),
// hidden entirely when empty. Continue rides the already-loaded sidebar caches
// — zero extra requests; only the three status chips fetch.

function _relTime(iso) {
    if (!iso) return '';
    var ms = Date.now() - new Date(iso).getTime();
    if (isNaN(ms) || ms < 0) return '';
    var m = Math.floor(ms / 60000);
    if (m < 1) return 'now';
    if (m < 60) return m + 'm';
    var h = Math.floor(m / 60);
    if (h < 24) return h + 'h';
    var d = Math.floor(h / 24);
    return d < 30 ? d + 'd' : Math.floor(d / 30) + 'mo';
}

function _renderContinue() {
    var el = document.getElementById('portal-continue');
    if (!el) return 0;
    // Recency: last_message where the backend provides it (asst/agent);
    // rooms lists only carry `created` — an honest approximation.
    var items = []
        .concat((allAsstSessions || []).map(function(s) {
            return { tid: 'asst:' + s.id, name: s.name || 'Assistant chat', ico: '\u{1F4AC}', ts: s.last_message || s.created || '' };
        }))
        .concat((allAgentSessions || []).map(function(s) {
            return { tid: 'agent:' + s.id, name: s.name || 'Agent run', ico: '\u{1F916}', ts: s.last_message || s.created || '' };
        }))
        .concat((allAgents || []).map(function(a) {
            return { tid: a.id, name: a.name || a.id, ico: '\u{1F3E0}', ts: a.created || '' };
        }))
        .filter(function(x){ return x.ts; })
        .sort(function(a, b){ return b.ts.localeCompare(a.ts); })
        .slice(0, 3);
    el.innerHTML = items.map(function(x) {
        return '<a class="portal-cont-card" href="#' + encodeURIComponent(x.tid) + '"' +
            ' onclick="event.preventDefault();_navTo(\'' + escAttr(x.tid) + '\')">' +
            '<span class="cc-ico">' + x.ico + '</span>' +
            '<span class="cc-name">' + esc(x.name) + '</span>' +
            '<span class="cc-time">' + esc(_relTime(x.ts)) + '</span>' +
            '</a>';
    }).join('');
    return items.length;
}

async function _loadStatusChips() {
    var el = document.getElementById('portal-status-chips');
    if (!el) return 0;
    var chips = [];
    var grab = function(url) {
        return EOS.api(url).catch(function(){ return null; });
    };
    var res = await Promise.all([
        grab('/rooms/api/pending'),
        grab('/cockpit/api/active-sessions'),
        grab('/billing/api/today')
    ]);
    var pending = Array.isArray(res[0]) ? res[0].length : 0;
    if (pending > 0) {
        chips.push('<a class="portal-status-chip attn" href="#" onclick="event.preventDefault();_navToApp(\'rooms\')" title="Pending [DO:] actions awaiting your Apply/Reject">' +
            '⏳ ' + pending + ' approval' + (pending === 1 ? '' : 's') + '</a>');
    }
    var live = (res[1] && Array.isArray(res[1].sessions)) ? res[1].sessions.length : 0;
    if (live > 0) {
        chips.push('<a class="portal-status-chip" href="#" onclick="event.preventDefault();_navToApp(\'cockpit\')" title="Live agent sessions (cockpit)">' +
            '\u{1F916} ' + live + ' agent run' + (live === 1 ? '' : 's') + ' live</a>');
    }
    var cost = res[2] && typeof res[2].cost === 'number' ? res[2].cost : 0;
    if (cost > 0) {
        chips.push('<a class="portal-status-chip" href="#" onclick="event.preventDefault();_navToApp(\'billing\')" title="Today’s AI spend (billing)">' +
            '\u{1F4B0} $' + cost.toFixed(2) + ' today</a>');
    }
    el.innerHTML = chips.join('');
    return chips.length;
}

function _syncHomeStrip(counts) {
    var strip = document.getElementById('portal-home-strip');
    if (strip) strip.style.display = (counts > 0) ? '' : 'none';
}

async function _loadHomeStrip() {
    var n = _renderContinue();
    var c = await _loadStatusChips();
    _syncHomeStrip(n + c);
}

// ── Search ──────────────────────────────────────────────────────────

try {
    EOS_UI.searchBar({
        mount: document.getElementById('portal-search'),
        placeholder: 'Search apps + vault',
        // Universal sidebar: searching for an app opens it in the right pane
        // (iframe) instead of hard-navigating away from /portal/.
        onSelectApp: function(app) {
            if (app && app.id) _navToApp(app.id);
        }
        // Vault note results fall through to default behaviour (open the note
        // in a new tab / external viewer) — there's no in-pane render for them yet.
    });
} catch (e) {
    console.warn('searchBar mount failed', e);
}

// ── Browse-all-apps drawer ──────────────────────────────────────────
// A modal listing every installed app from /api/apps with a filter input.
// Clicking an app opens it in the iframe pane (sidebar persists).

var _appsDrawerModal = null;
function _closeAppsDrawer() {
    if (_appsDrawerModal && _appsDrawerModal.close) { try { _appsDrawerModal.close(); } catch(e){} }
    _appsDrawerModal = null;
}
async function _openAppsDrawer() {
    // Refresh the catalog opportunistically so newly installed apps appear
    // without a page reload.
    try { await loadAppCatalog(); } catch (e) {}
    var apps = Object.keys(APP_CATALOG).map(function(id) {
        return { id: id, name: APP_CATALOG[id] || id };
    }).sort(function(a, b) {
        return (a.name || a.id).localeCompare(b.name || b.id);
    });
    var rows = apps.map(function(a) {
        return '<button data-app-id="' + escAttr(a.id) + '" data-app-name="' + escAttr(a.name.toLowerCase()) + '"' +
            ' class="apps-drawer-row" onclick="_navToApp(\'' + escAttr(a.id) + '\');_closeAppsDrawer();"' +
            ' style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 12px;border-radius:6px;background:none;border:none;color:var(--text);font-family:inherit;font-size:13px;cursor:pointer;text-align:left;width:100%">' +
            '<span>' + esc(a.name) + '</span>' +
            '<span style="font-size:11px;color:var(--text-muted);opacity:0.7;font-family:var(--font-mono,monospace)">/' + esc(a.id) + '/</span>' +
        '</button>';
    }).join('');
    var body =
        '<div style="display:flex;flex-direction:column;gap:8px">' +
        '<input id="apps-drawer-filter" type="text" placeholder="Filter…" autofocus ' +
            'style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-family:inherit;font-size:13.5px"' +
            ' oninput="_filterAppsDrawer(this.value)">' +
        '<div id="apps-drawer-list" style="display:flex;flex-direction:column;gap:2px;max-height:60vh;overflow-y:auto">' +
            (rows || '<div class="portal-empty">No apps installed.</div>') +
        '</div></div>';
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modal) {
        _appsDrawerModal = EOS_UI.modal({ title: 'Browse apps', body: body });
        setTimeout(function() {
            var f = document.getElementById('apps-drawer-filter');
            if (f) f.focus();
        }, 50);
    }
}

function _filterAppsDrawer(q) {
    q = (q || '').toLowerCase().trim();
    var rows = document.querySelectorAll('.apps-drawer-row');
    rows.forEach(function(r) {
        if (!q) { r.style.display = ''; return; }
        var id = (r.dataset.appId || '').toLowerCase();
        var name = (r.dataset.appName || '').toLowerCase();
        r.style.display = (id.indexOf(q) >= 0 || name.indexOf(q) >= 0) ? '' : 'none';
    });
}

// ── Sidebar toggle (mobile drawer + desktop collapse) ───────────────

function _isMobile() {
    return window.matchMedia('(max-width: 720px)').matches;
}

function toggleSidebar() {
    var shell = document.getElementById('portal-shell');
    if (!shell) return;
    if (_isMobile()) {
        shell.classList.toggle('sidebar-open');
    } else {
        shell.classList.toggle('sidebar-hidden');
    }
}

function closeSidebar() {
    var shell = document.getElementById('portal-shell');
    if (!shell) return;
    // Only meaningful on mobile (drawer); on desktop, closeSidebar is a no-op
    // since the user explicitly toggled via the button.
    shell.classList.remove('sidebar-open');
}

function _maybeCloseMobileSidebar() {
    if (_isMobile()) closeSidebar();
}

// Ctrl/Cmd+B — sidebar toggle (VS Code analogue).
document.addEventListener('keydown', function(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'b' && !e.shiftKey && !e.altKey) {
        // Don't steal Ctrl+B inside text inputs (bold in some textareas).
        var t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
        e.preventDefault();
        toggleSidebar();
    }
});

// ── Boot ────────────────────────────────────────────────────────────

_loadExpandState();
_syncBackendUI();
// Load the app catalog in parallel with the sidebar — its result feeds the
// iframe-pane title for any app reached via the search bar, and gates which
// backend buttons are shown (a store-disabled backend hides its button).
loadAppCatalog().then(_syncBackendAvailability);
loadSidebar().then(function() {
    _onHashChange();
    _loadHomeStrip();   // after the caches exist; fail-soft, hidden when empty
});
