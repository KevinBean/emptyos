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
            // Hero only: Enter on one exact app name opens that app, as the
            // hint under the box says (portal-launch.js). Only the KEY — the
            // Send button always sends, which is how a user sends "focus" as
            // a message. Attachments make it a chat whatever the words say.
            if (inputId === 'hero-input' && !_hasAttachments('hero')
                && typeof PortalLaunch !== 'undefined' && PortalLaunch.intercept()) return;
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
    // The open-an-app hint is a Think-verb affordance; re-read it on a verb
    // switch. This is also its redraw when the app catalog lands
    // (_syncBackendAvailability ends here), for text typed before it did.
    if (typeof PortalLaunch !== 'undefined') PortalLaunch.render();
    document.querySelectorAll('.portal-bk').forEach(function(b) {
        var on = b.dataset.backend === ACTIVE_BACKEND;
        b.classList.toggle('active', on);
        // Code is an action button, not part of the radio set — no aria-checked.
        if (b.getAttribute('role') === 'radio') b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    var wrap = document.getElementById('portal-backend-select');
    if (wrap) wrap.style.display = (ACTIVE_VERB === 'think') ? '' : 'none';
    if (typeof PortalChat !== 'undefined') PortalChat.afterSync();   // chat-first: visible radios + hero model pill
}
document.querySelectorAll('.portal-bk').forEach(function(btn) {
    btn.addEventListener('click', function() {
        var bk = btn.dataset.backend;
        if (bk === 'more') return;   // portal-chat.js binds More's own onclick
        // Code is workspace-shaped, not composer-shaped: open the /code/ IDE in
        // the iframe pane (embed mode) rather than arming the composer.
        if (bk === 'code') { _navToApp('code'); return; }
        // Chat-first (portal-chat.js) keeps its choice under its own key, so
        // the flag-off default in portal.backend.v1 is never touched.
        if (typeof PortalChat !== 'undefined' && PortalChat.isOn() && (bk === 'chat' || CONV_BACKENDS[bk])) {
            PortalChat.selectBackend(bk);
            return;
        }
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

// Sidebar-footer iframe shortcuts (Projects + Settings). Any OTHER app is still reachable: search → click app result →
// loads in-pane via _navToApp. The APP_CATALOG cache (populated from
// /api/apps on boot) supplies the display label for those dynamic loads.
var APP_LABELS = {
    projects: 'Projects',
    settings: 'Settings'
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
    // Leaving the thread closes its artifact panel — the home screen belongs
    // to no conversation, so a panel left open would be showing a stranger's.
    if (typeof PortalArtifacts !== 'undefined') PortalArtifacts.openFor('');
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
    // Top-level new thread — clear any folder / project context, then the hash.
    _pendingFolderId = null;
    if (typeof PortalProjects !== 'undefined') PortalProjects.clearPending();
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

// Does this composer have files waiting (chat-first, portal-attach.js)?
function _hasAttachments(slot) {
    if (typeof PortalAttach === 'undefined' || ACTIVE_BACKEND !== 'chat') return false;
    var s = PortalAttach.slot(slot);
    return !!(s && s.items.length);
}

async function submitFromHero() {
    if (sending) return;
    var input = document.getElementById('hero-input');
    var text = input.value.trim();
    // A chat may be attachments alone ("read this"); every other verb needs words.
    if (!text && !_hasAttachments('hero')) return;
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
    // A chat in a folder falls through to Rooms below: Rooms owns folders
    // (system prompt, model, the thread link) until they become chat projects.
    if (ACTIVE_BACKEND === 'chat') {
        if (!folder) return PortalChat.start(text);
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Folder threads start in Rooms', true);
    }
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
        // A chat's chip becomes a model switcher (chat-first only).
        if (typeof PortalChat !== 'undefined') PortalChat.decorateChip(agent);
        if (typeof PortalConnectors !== 'undefined') PortalConnectors.decorateChip(agent);
    }
    if (agent._backend === 'agent') {
        _renderAgentHistory(messages);   // block-list encoding, tool cards
    } else {
        _renderMessages(messages);       // rooms/assistant {role,text,ts}
    }
    // The artifacts this chat produced (B4). Driven by the session id rather
    // than the replayed messages: history carries no tool `display` payload,
    // so the index is the only thing that survives a reload.
    if (typeof PortalArtifacts !== 'undefined') {
        PortalArtifacts.openFor(agent._backend === 'agent' ? agent._sid : '');
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
    // A room agent's name is its persona; an agent session's name is the
    // thread title, so its replies are labelled by kind instead.
    var label = role === 'user' ? 'You'
        : (currentAgent && currentAgent._backend === 'agent') ? (currentAgent._profile === 'chat' ? 'Assistant' : 'Agent')
        : (currentAgent && currentAgent.name) || 'Assistant';
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
    if (!text && !_hasAttachments('chat')) return;
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
        _agentSend(text);   // portal-agent.js
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

function _renderContinue() {
    var el = document.getElementById('portal-continue');
    if (!el) return 0;
    // The three most recently ACTIVE conversations — the sidebar's model
    // (portal-recent.js), so the strip and the list agree on order and time.
    var now = Date.now();
    var items = _allConversations().filter(function(x){ return x.ts; }).slice(0, 3);
    el.innerHTML = items.map(function(x) {
        return '<a class="portal-cont-card" href="#' + encodeURIComponent(x.tid) + '"' +
            ' onclick="event.preventDefault();_navTo(' + EOS_UI.jsArg(x.tid) + ')">' +
            '<span class="cc-ico">' + PortalRecent.GLYPH[x.kind] + '</span>' +
            '<span class="cc-name">' + esc(x.name) + '</span>' +
            '<span class="cc-time">' + esc(PortalRecent.rel(x.ts, now)) + '</span>' +
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
        placeholder: 'Search chats, apps + vault',
        // Universal sidebar: searching for an app opens it in the right pane
        // (iframe) instead of hard-navigating away from /portal/.
        onSelectApp: function(app) {
            if (app && app.id) _navToApp(app.id);
        },
        // Conversations first, from every backend and every room folder — in
        // the dropdown, not by filtering the list under it (the dropdown
        // covered the filtered rows).
        extra: {
            title: 'Chats',
            items: function(q) {
                var now = Date.now();
                return PortalRecent.filter(_allConversations(), q).map(function(x) {
                    return {
                        name: x.name, icon: PortalRecent.GLYPH[x.kind],
                        desc: PortalRecent.KIND_LABEL[x.kind] + (x.ts ? ' \u00B7 ' + PortalRecent.rel(x.ts, now) : ''),
                        run: function() { _navTo(x.tid); },
                    };
                });
            },
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
            ' class="apps-drawer-row" onclick="_navToApp(' + EOS_UI.jsArg(a.id) + ');_closeAppsDrawer();"' +
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
// Chat-first home: one GET /portal/api/config, and nothing further while the
// flag is off (PortalChat.init starts PortalProjects once the flag answers).
if (typeof PortalChat !== 'undefined') PortalChat.init();
// Load the app catalog in parallel with the sidebar — its result feeds the
// iframe-pane title for any app reached via the search bar, and gates which
// backend buttons are shown (a store-disabled backend hides its button).
loadAppCatalog().then(function() {
    _syncBackendAvailability();
    // A pane opened from the URL (#app:<id>) before the catalog arrived is
    // titled with the raw id; give it the app's name now.
    var m = /^#app:(.+)$/.exec(decodeURIComponent(location.hash));   // as _onHashChange reads it
    var nameEl = document.getElementById('portal-iframe-name');
    if (m && nameEl) {
        var id = m[1];
        nameEl.textContent = APP_LABELS[id] || APP_CATALOG[id] || id;
    }
});
loadSidebar().then(function() {
    _onHashChange();
    _loadHomeStrip();   // after the caches exist; fail-soft, hidden when empty
});

// ── quick entry (?quick=1, ?launcher=1) — strip to the composer ─────────
// A page-owned, targeted-hide chrome rather than the generic body.launcher-mode
// (eos-keys.js) full-page hide-and-show-palette, which would replace Portal's
// composer with the generic app picker — defeating the point of pointing a
// global hotkey at Portal specifically. The mode moved into portal-quick.js
// when the desktop shell's preloaded quick window joined command-launcher as a
// second host; this call is all that remains here.
PortalQuick.init();
// After PortalQuick.init(): init skips its ranking fetch in quick mode, which
// it can only see once that call has added the class.
PortalLaunch.init();
// The home board (hub lanes + panels) is independent of the sidebar caches —
// fail-soft, hidden until something answers. AFTER PortalQuick.init(): quick
// mode is detected by the class that call adds, and a hotkey composer must
// not fetch a dashboard it will never show.
PortalHome.init();
