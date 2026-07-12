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
        });
        chip.classList.add('active');
        ACTIVE_VERB = chip.dataset.verb;
        document.getElementById('hero-mode-label').textContent = chip.textContent.trim();
        document.getElementById('hero-input').focus();
    });
});

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
        // Also surface threads whose name matches the query.
        var threadMatches = (allAgents || []).filter(function(a) {
            return (a.name || '').toLowerCase().indexOf(query.toLowerCase()) >= 0;
        }).slice(0, 10);
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
                    '<span class="fc-meta">thread &middot; ' + esc(t.id) + '</span>' +
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
    _renderMessages(messages);
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
    }
}

async function loadSidebar() {
    await Promise.all([loadAgents(), loadFolders(), _loadPinsFromServer()]);
    _renderSidebar();
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

function _renderSidebar() {
    var el = document.getElementById('portal-rooms');
    var currentId = currentAgent ? currentAgent.id : null;

    // ── Pinned section (above Rooms) ──
    var pinnedThreads = _pinsCache.map(_agentById).filter(Boolean);
    var pinnedSection = document.getElementById('portal-pinned-section');
    var pinnedListEl = document.getElementById('portal-pinned-list');
    if (pinnedSection && pinnedListEl) {
        if (pinnedThreads.length) {
            pinnedSection.style.display = '';
            pinnedListEl.innerHTML = pinnedThreads.map(function(t) {
                return _renderThreadRow(t, {});
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
    } else if (!allFolders.length && !allAgents.length) {
        parts.push('<div class="portal-empty">No threads yet.</div>');
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
// Load the app catalog in parallel with the sidebar — its result feeds the
// iframe-pane title for any app reached via the search bar.
loadAppCatalog();
loadSidebar().then(function() { _onHashChange(); });
