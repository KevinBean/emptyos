// portal-agent.js — split out of portal.js (B0, 2026-09-12). Owns the Agent backend: session create/open, the WS turn stream, tool cards, history replay.
//
// Loads BEFORE portal.js, in the same global scope (not a module), so the
// page's markup handlers and portal.js's boot code resolve these names
// unchanged. Top-level names must stay unique across portal.js and every
// portal-*.js — tests/test_unit_page_script_globals.py fails on a
// collision, because the script that parses last would silently win.

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
// CRUD — stays on /agent/. The pure pieces both pages draw with (diff/tool
// extras HTML, cost, footer parts, the three-encoding history walk) live in
// the shared /static/eos-agent-view.js (EOS_AGENT_VIEW); this file owns only
// portal's markup.

var _agentWs = null;          // single live socket; closed on every thread switch
var _agentSid = null;
var _agentCur = null;         // current assistant turn body (.body element)
var _agentCurTextEl = null;   // the .portal-md run currently accumulating streamed text
var _agentCurText = '';       // markdown accumulated for _agentCurTextEl
var _agentToolEls = {};       // tool_use id -> .portal-tc card element
var _agentTurn = { start: 0, tools: 0 };
var _agentLastSend = null;    // the last `message` frame, for a re-send after the cloud confirm
var allAgentSessions = [];    // agent-backend sidebar cache

function _agentAgentObj(session) {
    return {
        id: 'agent:' + session.id,
        name: session.name || 'Agent run',
        _backend: 'agent',
        _sid: session.id,
        _profile: session.profile || '',
        // Which connectors THIS chat may use (B5) — per-chat, not global.
        _connectors: session.connectors || '',
        _mode_label: session.profile === 'chat' ? 'Chat' : 'Agent',
        // Who's answering — the session's provider · model, shown by _openAgent's
        // existing #portal-chat-model chip. (Deliberately NOT EOS_UI.modelPill:
        // portal spends no think of its own — the backends do — so a pill would
        // configure `think.app.portal`, a knob nothing reads.)
        model: [session.provider, session.model].filter(Boolean).join(' · '),
        created: session.created || ''
    };
}

// opts (chat-first, portal-chat.js): {profile: 'chat', provider: '<name>',
// project_id?, attach?: {attachments, vault_context}, attached?: [chips]}.
async function _startAgentThread(text, folder, opts) {
    opts = opts || {};
    var sendBtn = document.getElementById('hero-send');
    sendBtn.disabled = true;
    var prevLabel = sendBtn.textContent;
    sendBtn.textContent = 'Creating…';
    var input = document.getElementById('hero-input');
    try {
        var titleSeed = text.length > 40 ? text.slice(0, 40).trim() + '…' : text;
        var body = { name: titleSeed };
        if (opts.profile) body.profile = opts.profile;
        if (opts.provider) body.provider = opts.provider;
        if (opts.project_id) body.project_id = opts.project_id;
        var session = await fetch('/agent/api/sessions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        }).then(function(r){ return r.json(); });
        if (!session || !session.id) {
            throw new Error((session && session.error) || 'create failed');
        }
        var agent = _agentAgentObj(session);
        allAgentSessions.unshift({
            id: session.id, name: agent.name, created: agent.created, last_message: '',
            profile: session.profile || '', project_id: session.project_id || ''
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
        _agentShowAttached(opts.attached);
        _agentConnect(session.id, text, opts.attach);
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

// The opening `message` frame a fresh socket sends, or null when the thread
// was opened with nothing to say (reopening an old one). The first message
// may carry the composer's attachments / vault toggle (portal-attach.js), and
// it may be attachments ALONE ("read this") — so an empty text is not the same
// as nothing to send. Pure: tests/js/portal_agent.test.mjs.
function _agentOpeningFrame(initialText, initialExtras) {
    var extras = initialExtras || {};
    var hasFiles = !!(extras.attachments && extras.attachments.length);
    if (!initialText && !hasFiles) return null;
    return Object.assign({ type: 'message', text: initialText || '' }, extras);
}

function _agentConnect(sid, initialText, initialExtras) {
    _agentCloseWs();
    _agentSid = sid;
    _agentCur = null; _agentCurTextEl = null; _agentCurText = ''; _agentToolEls = {};
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + location.host + '/agent/ws/' + encodeURIComponent(sid);
    var ws;
    try { ws = new WebSocket(url); } catch (e) { _agentSetStatus('connection error'); return; }
    _agentWs = ws;
    ws._pending = _agentOpeningFrame(initialText, initialExtras);
    ws.onopen = function() {
        _agentSetStatus('connected');
        if (ws._pending) {
            ws.send(JSON.stringify(ws._pending));
            _agentLastSend = ws._pending;
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
        // A CreateArtifact result opens the side panel (B4); every other tool
        // passes straight through.
        if (typeof PortalArtifacts !== 'undefined') PortalArtifacts.onToolResult(msg.display || {});
    } else if (t === 'agent:permission_requested') {
        if (typeof EOS_UI !== 'undefined' && EOS_UI.agentPermission) {
            EOS_UI.agentPermission({
                id: msg.id, session_id: msg.session_id,
                tool: msg.tool, input: msg.input, summary: msg.summary
            });
        }
    } else if (t === 'agent:needs_confirmation') {
        _agentCloudConfirm(msg);
        _agentSetSending(false);
    } else if (t === 'agent:attachment_problems') {
        _agentNotice('⚠ ' + (msg.problems || []).join('\n⚠ '));
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
    var text = EOS_AGENT_VIEW.resultText(resultText);
    var extras = EOS_AGENT_VIEW.toolExtrasHtml(display);
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
    var f = EOS_AGENT_VIEW.footerParts(usage, {
        elapsedS: _agentTurn.start ? (Date.now() - _agentTurn.start) / 1000 : 0,
        tools: _agentTurn.tools
    });
    var footer = document.createElement('div');
    footer.className = 'portal-turn-footer';
    footer.textContent = '· ' + f.parts.join(' · ');
    _agentCur.appendChild(footer);
    _scrollMessagesToBottom();
}

// History replay — EOS_AGENT_VIEW.walkHistory owns the three stored encodings
// (plain string, Anthropic block list, full-message dict); this only draws.
// Replayed tool cards carry no `display` (diff/exit chips are live-WS only),
// same as /agent/.
function _renderAgentHistory(messages) {
    var inner = document.getElementById('portal-messages-inner');
    if (inner) inner.innerHTML = '';
    _agentToolEls = {}; _agentCur = null; _agentCurTextEl = null; _agentCurText = '';
    EOS_AGENT_VIEW.walkHistory(messages, {
        user: function(text, m) {
            _appendTurn('user', text, { streaming: false });
            // What rode with it (stored as eos_attached), so a reopened chat
            // still shows the files rather than a bare question.
            var names = m && (m.eos_attached || (m.content && m.content.eos_attached));
            if (names && names.length) {
                _agentShowAttached(names.map(function(n) {
                    return { name: n, kind: /\.(png|jpe?g|gif|webp|bmp)$/i.test(n) ? 'image' : 'file' };
                }));
            }
        },
        assistant: _renderAgentAssistantHist,
        toolResult: function(id, text, isError) { _markAgentToolResult(id, isError, text, {}); },
        notice: _agentNotice
    });
    _updateJumpButton();
    _scrollMessagesToBottom();
}

function _renderAgentAssistantHist(segments) {
    var body = _appendTurn('assistant', '', { streaming: false });
    body.innerHTML = '';
    segments.forEach(function(s) {
        if (s.type === 'text') {
            var el = document.createElement('div');
            el.className = 'portal-md';
            el.innerHTML = _renderMarkdown(s.text);
            body.appendChild(el);
        } else {
            _appendAgentToolCall(body, s.id, s.name, s.input);
        }
    });
}

// Send into an open agent thread — called by portal.js's _sendText.
function _agentSend(text) {
    if (!_agentWs || _agentWs.readyState !== WebSocket.OPEN) {
        if (typeof EOS !== 'undefined' && EOS.toast) EOS.toast('Agent not connected — reopen the run', false);
        return;
    }
    var att = typeof PortalAttach !== 'undefined' ? PortalAttach.take('chat') : { payload: {}, items: [] };
    _appendTurn('user', text, { streaming: false });
    _agentShowAttached(att.items);
    _agentLastSend = Object.assign({ type: 'message', text: text }, att.payload);
    _agentWs.send(JSON.stringify(_agentLastSend));
    _agentSetSending(true);
}

// The files that rode with the message the user just sent.
function _agentShowAttached(items) {
    if (!items || !items.length) return;
    var inner = document.getElementById('portal-messages-inner');
    if (!inner) return;
    var el = document.createElement('div');
    el.className = 'portal-att-sent';
    el.textContent = items.map(function(i) { return (i.kind === 'image' ? '🖼 ' : '📎 ') + i.name; }).join('  ');
    inner.appendChild(el);
    _scrollMessagesToBottom();
}

// Cloud + vault content needs an explicit yes (CLAUDE.md rule 19). The server
// refused the turn and said why; offer the two ways forward.
function _agentCloudConfirm(msg) {
    var inner = document.getElementById('portal-messages-inner');
    if (!inner) return;
    var el = document.createElement('div');
    el.className = 'portal-agent-notice portal-agent-confirm';
    var text = document.createElement('div');
    text.textContent = msg.message || 'Send this to the cloud model?';
    var send = document.createElement('button');
    send.type = 'button';
    send.className = 'eos-btn-sm';
    send.textContent = 'Send anyway';
    send.onclick = function() {
        if (!_agentLastSend || !_agentWs || _agentWs.readyState !== WebSocket.OPEN) return;
        el.remove();
        _agentLastSend = Object.assign({}, _agentLastSend, { cloud_ok: true });
        _agentWs.send(JSON.stringify(_agentLastSend));
        _agentSetSending(true);
    };
    var pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'eos-btn-sm';
    pick.textContent = 'Pick a local model';
    pick.onclick = function() {
        if (typeof PortalChat !== 'undefined') PortalChat.pickThreadModel(currentAgent);
    };
    el.appendChild(text);
    el.appendChild(send);
    el.appendChild(pick);
    inner.appendChild(el);
    _scrollMessagesToBottom();
}
