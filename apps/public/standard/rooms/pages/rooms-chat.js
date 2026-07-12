// rooms-chat.js — chat thread: render, send, message actions.
// Owns: openChat, renderMessages, appendMessage, showTyping, sendMessage,
// clearHistory, exportChat, showSidebar, startReply, cancelReply, voice
// playback (speakText etc.), drafts, pin, catch-up banner.

// --- Chat ---

async function openChat(agentId) {
    currentAgent = agents.find(function(a) { return a.id === agentId; });
    if (!currentAgent) return;

    // Reflect the open room in the URL hash so deep links + browser back work
    // (CLAUDE.md §Deep-linking Detail Views). Idempotent — set() short-circuits
    // when the hash already matches, so callers from the hashRoute listener
    // don't recurse.
    if (typeof _route !== 'undefined') _route.set(agentId);

    renderSidebar();

    // Show chat panel, hide welcome
    document.getElementById('chat-welcome').style.display = 'none';
    document.getElementById('chat-header').style.display = '';
    document.getElementById('chat-messages').style.display = '';
    document.getElementById('chat-input-bar').style.display = '';

    var tier = currentAgent.tier || 'user';
    var isGroup = tier === 'group';
    var memberCount = '';
    if (isGroup) {
        var rs = (currentAgent.participants || []).filter(function(p){
            return p.type === 'agent' || p.type === 'cli';
        }).length;
        memberCount = ' <span style="color:var(--text-muted);font-size:13px;font-weight:400;margin-left:6px">' +
                      rs + ' member' + (rs !== 1 ? 's' : '') + '</span>';
    }
    var titleIcon = isGroup ? '👥 ' : '';
    document.getElementById('chat-name').innerHTML = titleIcon + esc(currentAgent.name) +
        ' <span class="tier-badge ' + tier + '">' + tier + '</span>' + memberCount;
    var modelEl = document.getElementById('chat-model');
    if (currentAgent.model) { modelEl.textContent = currentAgent.model; modelEl.style.display = ''; }
    else { modelEl.style.display = 'none'; }

    renderMembers();
    // Forum button: only for rooms with 2+ AGENT participants (run_panel's floor).
    var forumBtn = document.getElementById('chat-forum-btn');
    if (forumBtn) {
        var agentCount = (currentAgent.participants || []).filter(function(p){
            return p.type === 'agent';
        }).length;
        forumBtn.style.display = agentCount >= 2 ? '' : 'none';
    }
    loadRoomTaskCount(currentAgent.id);
    // Refresh the ⚡ auto-accept chip for this room (autopilot grant issuer).
    if (typeof apRefresh === 'function') apRefresh();
    // Seed the Knowledge tab count from the room record (already in cache).
    var kbBadge = document.getElementById('activity-tab-knowledge-count');
    var kbN = ((currentAgent.knowledge_files || []).length);
    if (kbBadge) kbBadge.textContent = kbN ? '(' + kbN + ')' : '';

    // Mobile: hide sidebar, show chat
    if (isMobile) {
        document.getElementById('sidebar').classList.add('hidden');
        document.getElementById('chat-panel').classList.remove('hidden');
    }

    // Load history. Fetch pending FIRST so renderMessages has the cache
    // populated when it walks each message's `pending: [ids]` list.
    var msgEl = document.getElementById('chat-messages');
    msgEl.innerHTML = '<div style="text-align:center;padding:20px"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>';

    try {
        await loadPendingForRoom(currentAgent.id);
        var data = await EOS.api('/rooms/api/history/' + agentId);
        // Capture last_visited BEFORE we ping the visit endpoint so the
        // catch-me-up banner can compute "messages since last visit".
        var lastVisited = (await EOS.api('/rooms/api/visits') || {})[agentId] || '';
        renderMessages(data.messages || []);
        maybeRenderCatchUpBanner(data.messages || [], lastVisited);
        // Mark visited (idempotent server-side) and clear local unread state
        // so the dot disappears immediately without a full reload.
        EOS.post('/rooms/api/rooms/' + encodeURIComponent(agentId) + '/visit', {});
        if (_unreadMap[agentId]) {
            delete _unreadMap[agentId];
            renderSidebar();
        }
    } catch(e) { renderMessages([]); }
    // Restore the draft (if any) — sits untouched in the input for the user
    // to keep typing, edit, or hit send. Show a small toast so it's clear
    // the typing didn't vanish.
    var draft = loadDraft(agentId);
    var input = document.getElementById('chat-input');
    if (input && draft && draft.text) {
        input.value = draft.text;
        // Trigger auto-resize.
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        EOS_UI.toast('Draft restored');
    } else if (input) {
        input.value = '';
        input.style.height = 'auto';
    }
    setTimeout(function() {
        var inp = document.getElementById('chat-input');
        if (inp) {
            inp.focus();
            // Move cursor to end so the user can keep typing.
            var end = inp.value.length;
            inp.selectionStart = inp.selectionEnd = end;
        }
    }, 150);
}

function showSidebar() {
    document.getElementById('sidebar').classList.remove('hidden');
    if (isMobile) document.getElementById('chat-panel').classList.add('hidden');
}

// Single source of truth for "drop back to the welcome panel". Called by the
// hashRoute onHide (browser back / hash cleared) and by every site that
// dismisses the current room (delete agent, bulk delete, archive). The early
// return + the helper's `clear()` short-circuit-on-empty-hash together guard
// against re-entry when _route.clear() triggers onHide which calls this again.
function _hideChat() {
    if (!currentAgent) return;
    currentAgent = null;
    document.getElementById('chat-welcome').style.display = '';
    document.getElementById('chat-header').style.display = 'none';
    document.getElementById('chat-messages').style.display = 'none';
    document.getElementById('chat-input-bar').style.display = 'none';
    var mb = document.getElementById('chat-members');
    if (mb) { mb.style.display = 'none'; mb.innerHTML = ''; }
    if (typeof _route !== 'undefined') _route.clear();
    renderSidebar();
}

function renderMessages(messages) {
    var el = document.getElementById('chat-messages');
    if (!messages.length) {
        var name = currentAgent ? currentAgent.name : 'this agent';
        var isGroup = currentAgent && currentAgent.tier === 'group';
        var headline, sub, suggestionsHtml = '';
        if (isGroup) {
            var responders = (currentAgent.participants || []).filter(function(p){
                return p.type === 'agent' || p.type === 'cli';
            });
            var first = responders[0];
            var firstId = first ? first.id : null;
            headline = esc(name);
            sub = responders.length + ' participant' + (responders.length !== 1 ? 's' : '') + ' · use <code style="background:var(--bg-elevated);padding:1px 5px;border-radius:3px;font-size:12px">@name</code> to direct your message';
            // Suggestion chips — quick-fire @-mentions for each responder.
            suggestionsHtml = '<div style="display:flex;gap:6px;justify-content:center;flex-wrap:wrap;margin-top:18px">' +
                responders.slice(0, 4).map(function(p) {
                    var v = participantVisual(p);
                    return '<button class="eos-btn-sm eos-btn-ghost" style="display:inline-flex;align-items:center;gap:6px" onclick="insertMention(\'' + escAttr(v.id) + '\')">' +
                           '<span class="member-avatar" style="background:' + v.color + ';width:18px;height:18px;font-size:9px">' + esc(v.icon) + '</span>' +
                           '<span>@' + esc(v.id) + '</span>' +
                           '</button>';
                }).join('') +
                '</div>';
        } else {
            headline = esc(name);
            var sp = (currentAgent && currentAgent.system_prompt) || '';
            var spPreview = sp.replace(/\s+/g,' ').slice(0, 140);
            sub = spPreview ? esc(spPreview) + (sp.length > 140 ? '…' : '') : 'Ready when you are.';
        }
        el.innerHTML =
            '<div class="chat-welcome" style="padding:60px 20px;text-align:center">' +
                '<div class="icon" style="font-size:48px;margin-bottom:18px">' +
                    (isGroup ? '👥' : '💬') +
                '</div>' +
                '<h2 style="font-size:20px;font-weight:600;margin-bottom:8px">' + headline + '</h2>' +
                '<p style="color:var(--text-muted);font-size:13px;max-width:380px;margin:0 auto;line-height:1.5">' + sub + '</p>' +
                suggestionsHtml +
            '</div>';
        return;
    }
    var isGroup = currentAgent && (currentAgent.tier === 'group');
    var pinnedSet = new Set((currentAgent && currentAgent.pinned_ts) || []);
    // Phase 25 — index messages by ts so reply quotes can resolve their parent.
    _msgIndexByTs = {};
    messages.forEach(function(m) { if (m.ts) _msgIndexByTs[m.ts] = m; });
    // Pinned panel above the thread — pulls live messages from the same array.
    var pinnedHtml = '';
    if (pinnedSet.size) {
        var pinnedRows = messages.filter(function(m){ return m.ts && pinnedSet.has(m.ts); }).map(function(m) {
            var actor = m.actor || {};
            var speakerId = actor.id || (m.role === 'user' ? 'me' : 'assistant');
            var preview = _stripTaskMarkers(m.text || '').replace(/\s+/g, ' ');
            return '<div class="pinned-row" onclick="scrollToMessage(\'' + escAttr(m.ts) + '\')">' +
                '<span style="color:var(--accent);flex-shrink:0">📌</span>' +
                '<div style="flex:1;min-width:0">' +
                    '<div class="pinned-speaker">' + esc(speakerId) + '</div>' +
                    '<div class="pinned-text">' + esc(preview) + '</div>' +
                '</div>' +
                '<button class="pinned-unpin" onclick="event.stopPropagation();unpinMessage(\'' + escAttr(m.ts) + '\')" title="Unpin">×</button>' +
                '</div>';
        }).join('');
        if (pinnedRows) {
            pinnedHtml = '<div class="pinned-panel">' +
                '<div class="pinned-panel-head">📌 Pinned · ' + pinnedSet.size + '</div>' +
                pinnedRows +
                '</div>';
        }
    }
    // Segmentation — interleave day dividers + session dividers into the
    // message stream. A "segment" starts either at a day boundary, or at a
    // same-day time gap > SESSION_GAP_MS (a fresh sit-down). Both kinds are
    // scroll targets via data-segment-id; the picker lists them all together.
    // (Topic-level segmentation needs LLM analysis — deferred to V3.)
    var SESSION_GAP_MS = 90 * 60 * 1000;
    var _segments = [];
    var _msgsByDay = {};
    var _orderedDays = [];
    messages.forEach(function(m) {
        var day = (m.ts || '').slice(0, 10);
        if (!day) return;
        if (!(day in _msgsByDay)) {
            _msgsByDay[day] = 0;
            _orderedDays.push(day);
        }
        _msgsByDay[day]++;
    });
    var jumpChip = document.getElementById('chat-jump-label');

    var _seenDay = null;
    var _prevTsMs = null;
    var messagesHtml = messages.map(function(m, idx) {
        var dayKey = (m.ts || '').slice(0, 10);
        var curMs = m.ts ? new Date(m.ts).getTime() : NaN;
        var gapMs = (!isNaN(curMs) && _prevTsMs !== null) ? (curMs - _prevTsMs) : null;
        var dividerHtml = '';
        var isNewDay = dayKey && dayKey !== _seenDay;
        var isSessionBreak = !isNewDay && gapMs !== null && gapMs > SESSION_GAP_MS;
        if (isNewDay) {
            var segId = 'seg-' + _segments.length;
            dividerHtml = '<div class="day-divider" data-segment-id="' + segId +
                          '" data-day="' + dayKey + '"><span>' +
                          esc(_formatDay(dayKey)) + '</span></div>';
            _segments.push({
                id: segId, kind: 'day', day: dayKey,
                ts: m.ts, label: _formatDay(dayKey),
                preview: _segmentPreview(m)
            });
            _seenDay = dayKey;
        } else if (isSessionBreak) {
            var segId2 = 'seg-' + _segments.length;
            var gapLabel = _formatGap(gapMs);
            dividerHtml = '<div class="session-divider" data-segment-id="' + segId2 +
                          '"><span>&middot; &middot; &middot; ' + esc(gapLabel) +
                          ' later &middot; &middot; &middot;</span></div>';
            _segments.push({
                id: segId2, kind: 'session', day: dayKey,
                ts: m.ts, label: _segmentTimeLabel(m.ts),
                preview: _segmentPreview(m)
            });
        }
        if (!isNaN(curMs)) _prevTsMs = curMs;
        var cls = m.role === 'user' ? 'msg-user' : 'msg-assistant';
        var actor = m.actor || {};
        var isCli = actor.type === 'cli';
        if (isCli) cls += ' msg-cli';
        // Forum/panel turns (apps/rooms/panel.py): tag chip + distinct styling.
        // The backend prefixes moderator/synthesis text with a **bold** marker;
        // the chip replaces it, so strip the redundant leading line.
        var panel = m.panel || {};
        var panelTag = '';
        var panelText = null;       // overrides displayed text when set
        var isPanelTurn = false;
        if (panel.kind === 'moderator') {
            cls += ' msg-panel msg-panel-moderator'; isPanelTurn = true;
            panelTag = '<span class="panel-tag panel-tag-mod">&#9878; Moderator</span>';
            panelText = (m.text || '').replace(/^\*\*Moderator\*\*\s*/, '');
        } else if (panel.kind === 'synthesis') {
            cls += ' msg-panel msg-panel-synthesis'; isPanelTurn = true;
            panelTag = '<span class="panel-tag panel-tag-syn">&#10003; Panel synthesis</span>';
            panelText = (m.text || '').replace(/^\*\*Panel synthesis \(moderator\)\*\*\s*/, '');
        } else if (panel.round) {
            cls += ' msg-panel msg-panel-take'; isPanelTurn = true;
            panelTag = '<span class="panel-tag panel-tag-round">Round ' + esc(String(panel.round)) + '</span>';
        }
        var isModSyn = panel.kind === 'moderator' || panel.kind === 'synthesis';
        var isPinned = m.ts && pinnedSet.has(m.ts);
        if (isPinned) cls += ' is-pinned';
        var time = m.ts ? new Date(m.ts).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';
        var who = '';
        if (isGroup && m.role !== 'user' && !isModSyn) {
            var pseudoPart = {type: actor.type || 'agent', id: actor.id || ''};
            var v = participantVisual(pseudoPart);
            var modelChip = m.model ? '<span class="msg-model">· ' + esc(m.model) + '</span>' : '';
            who = '<div class="msg-speaker-row">' +
                  '<span class="member-avatar" style="background:' + v.color + '">' + esc(v.icon) + '</span>' +
                  '<span style="font-size:11px;color:var(--text-muted);font-weight:600">' + esc(v.label) + modelChip + '</span>' +
                  '</div>';
        }
        var bodyStyle = isCli ? ' style="font-family:var(--font-mono,monospace);font-size:13px;white-space:pre-wrap"' : (isPanelTurn ? ' style="white-space:pre-wrap"' : '');
        var pendingHtml = '';
        if (Array.isArray(m.pending) && m.pending.length) {
            pendingHtml = m.pending.map(function(actionId) {
                var action = _pendingMap[actionId];
                return action ? renderPendingCard(action) : '';
            }).join('');
        }
        // Auto-exec [DO:] results — per-action chip with view/edit links to the
        // touched note + an "→ <app>" exit link. Mirrors the page-assistant
        // server_results block for the rooms-chat surface.
        var resultsHtml = '';
        if (Array.isArray(m.server_results) && m.server_results.length) {
            resultsHtml = m.server_results.map(function(r) {
                if (!r.ok) return '';
                return _pendingLinksHtml(r.links);
            }).filter(Boolean).join('');
        }
        // Pin toggle — only render on messages with a stable ts.
        var pinBtn = m.ts ? '<button class="msg-pin" onclick="togglePin(\'' + escAttr(m.ts) + '\')" title="' + (isPinned ? 'Unpin' : 'Pin') + '">' + (isPinned ? '📌' : '📍') + '</button>' : '';
        // Voice playback — only on non-user turns.
        var speakBtn = (m.role !== 'user' && m.text) ?
            '<button class="msg-speak" data-ts="' + escAttr(m.ts || '') + '" onclick="event.stopPropagation();toggleSpeakMessage(\'' + escAttr(m.ts || '') + '\', this.dataset.text)" data-text="' + escAttr(m.text) + '" title="Speak">🔊</button>' : '';
        // Phase 25 — reply button + quoted parent.
        var replyBtn = m.ts ? '<button class="msg-reply" onclick="startReply(\'' + escAttr(m.ts) + '\')" title="Reply">↩</button>' : '';
        var quoteHtml = '';
        if (m.reply_to) {
            var parent = _msgIndexByTs[m.reply_to];
            if (parent) {
                var pActor = parent.actor || {};
                var pSpeaker = pActor.id || (parent.role === 'user' ? 'me' : 'assistant');
                var pPreview = (parent.text || '').replace(/\s+/g, ' ').slice(0, 90);
                quoteHtml = '<div class="msg-reply-quote" onclick="scrollToMessage(\'' + escAttr(m.reply_to) + '\')">' +
                    '<span class="rq-speaker">' + esc(pSpeaker) + '</span>' +
                    '<span class="rq-text">' + esc(pPreview) + '</span>' +
                    '</div>';
            } else {
                quoteHtml = '<div class="msg-reply-quote" style="cursor:default">' +
                    '<span class="rq-text">↩ replied to a message no longer in scrollback</span>' +
                    '</div>';
            }
        }
        return dividerHtml + '<div class="msg ' + cls + '" data-ts="' + escAttr(m.ts || '') + '">' +
            pinBtn + speakBtn + replyBtn + quoteHtml + panelTag + who + '<div' + bodyStyle + '>' + esc(panelText !== null ? panelText : m.text) + '</div>' +
            pendingHtml +
            resultsHtml +
            (time ? '<div class="msg-time">' + time + '</div>' : '') + '</div>';
    }).join('');
    _lastRenderedSegments = _segments;
    if (jumpChip) {
        var n = _segments.length;
        jumpChip.textContent = n > 1 ? (n + ' segments') : '';
    }
    el.innerHTML = pinnedHtml + messagesHtml;
    el.scrollTop = el.scrollHeight;
}

function appendMessage(role, text) {
    var el = document.getElementById('chat-messages');
    var welcome = el.querySelector('.chat-welcome');
    if (welcome) welcome.remove();
    var cls = role === 'user' ? 'msg-user' : 'msg-assistant';
    var time = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    el.insertAdjacentHTML('beforeend', '<div class="msg ' + cls + '">' + esc(text) +
        '<div class="msg-time">' + time + '</div></div>');
    el.scrollTop = el.scrollHeight;
}

function showTyping() {
    var el = document.getElementById('chat-messages');
    el.insertAdjacentHTML('beforeend', '<div class="typing-indicator" id="typing-ind">' +
        '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>');
    el.scrollTop = el.scrollHeight;
}

function removeTyping() { var t = document.getElementById('typing-ind'); if (t) t.remove(); }

async function sendMessage() {
    if (sending || !currentAgent) return;
    var input = document.getElementById('chat-input');
    var text = input.value.trim();
    if (!text) return;

    input.value = '';
    input.style.height = 'auto';
    // Sent — drop the draft so a future openChat doesn't re-restore it.
    clearDraft(currentAgent.id);
    sending = true;
    document.getElementById('send-btn').disabled = true;

    appendMessage('user', text);

    // Create streaming message bubble
    var isGroup = currentAgent && (currentAgent.tier === 'group');
    var msgEl = document.createElement('div');
    msgEl.className = 'msg msg-assistant';
    msgEl.innerHTML =
        (isGroup ? '<div class="msg-speaker-row" id="stream-speaker-row">' +
                   '<span class="member-avatar" id="stream-avatar" style="background:var(--text-muted)">…</span>' +
                   '<span id="stream-speaker" style="font-size:11px;color:var(--text-muted);font-weight:600">…</span>' +
                   '</div>' : '') +
        '<div class="msg-text" id="stream-target"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>' +
        '<div id="stream-tools" style="display:flex;flex-direction:column;gap:4px;margin-top:6px"></div>' +
        '<div id="stream-pending" style="display:flex;flex-direction:column;gap:4px"></div>';
    document.getElementById('chat-messages').appendChild(msgEl);
    var _cm=document.getElementById('chat-messages');if(_cm)_cm.scrollTop=_cm.scrollHeight;

    var targetEl = document.getElementById('stream-target');
    // Render the accumulated reply as markdown via the shared helper, falling
    // back to escaped plain text if the shared bundle hasn't loaded yet.
    var paintMarkdown = function(s) {
        if (typeof EOS_UI !== 'undefined' && EOS_UI.paintMarkdown) EOS_UI.paintMarkdown(targetEl, s);
        else targetEl.innerHTML = esc(s);
    };
    var speakerEl = document.getElementById('stream-speaker');
    var avatarEl = document.getElementById('stream-avatar');
    var toolsEl = document.getElementById('stream-tools');
    var fullText = '';
    var sawCliMarkers = false;
    var toolCardsById = {};

    var streamPayload = { agent_id: currentAgent.id, text: text };
    if (_replyTo && _replyTo.ts) streamPayload.reply_to = _replyTo.ts;
    var sentReplyTo = _replyTo;
    cancelReply();  // clear chip; the ts is captured in payload above
    try {
        for await (var chunk of EOS.streamPost('/rooms/api/chat/stream', streamPayload)) {
            // CLI tool-use card — small inline card, expands the input on click.
            if (chunk.tool_use) {
                sawCliMarkers = true;
                var tu = chunk.tool_use;
                var tid = tu.id || ('tool-' + Math.random().toString(36).slice(2));
                var inputStr = '';
                try { inputStr = JSON.stringify(tu.input || {}, null, 2); } catch(e) { inputStr = String(tu.input); }
                var card = document.createElement('div');
                card.id = 'tool-card-' + tid;
                card.style.cssText = 'border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-family:var(--font-mono,monospace);font-size:12px;background:var(--bg-elevated)';
                card.innerHTML = '<div style="font-weight:600;color:var(--accent)">→ ' + esc(tu.name || 'tool') + '</div>' +
                                 '<details style="margin-top:2px"><summary style="cursor:pointer;color:var(--text-muted)">input</summary>' +
                                 '<pre style="margin:4px 0 0 0;white-space:pre-wrap;font-size:11px">' + esc(inputStr) + '</pre></details>';
                toolsEl.appendChild(card);
                toolCardsById[tid] = card;
                var _cm=document.getElementById('chat-messages');if(_cm)_cm.scrollTop=_cm.scrollHeight;
                continue;
            }
            if (chunk.tool_result) {
                sawCliMarkers = true;
                var tr = chunk.tool_result;
                var card = toolCardsById[tr.tool_use_id];
                if (card) {
                    card.insertAdjacentHTML('beforeend',
                        '<details style="margin-top:2px"><summary style="cursor:pointer;color:var(--text-muted)">result</summary>' +
                        '<pre style="margin:4px 0 0 0;white-space:pre-wrap;font-size:11px">' + esc(tr.content || '') + '</pre></details>');
                }
                continue;
            }
            // Phase 5 review-gate card — CLI emitted [DO:app.method({...})]
            // and the backend saved it as a pending action. Render Apply/Reject
            // inline so the user can resolve it without leaving the message.
            if (chunk.pending_action) {
                var action = chunk.pending_action;
                _pendingMap[action.id] = action;
                var pendingEl = document.getElementById('stream-pending');
                if (pendingEl) {
                    pendingEl.insertAdjacentHTML('beforeend', renderPendingCard(action));
                }
                updatePendingBadge();
                continue;
            }
            // Backend stripped [DO:] tokens from the reply after the stream
            // finished — replace what we already painted so the user doesn't
            // see the raw token sitting above the pending card.
            if (typeof chunk.text_replace === 'string') {
                fullText = chunk.text_replace;
                if (fullText) { paintMarkdown(fullText); } else { targetEl.innerHTML = ''; }
                continue;
            }
            var t = chunk.text || '';
            if (t) {
                fullText += t;
                // Stream cheaply as plain text; the full markdown renders once
                // after the loop. Mirrors the agent app (agent.js appendAssistantText)
                // — avoids re-parsing the whole accumulated reply on every chunk.
                targetEl.textContent = fullText;
                var _cm=document.getElementById('chat-messages');if(_cm)_cm.scrollTop=_cm.scrollHeight;
            }
            // Final chunk carries responder_id (and actor_type for CLI).
            if (speakerEl && chunk.responder_id) {
                var v = participantVisual({type: chunk.actor_type || 'agent', id: chunk.responder_id});
                speakerEl.textContent = v.label;
                var modelLabel = (chunk.model || '').trim();
                if (modelLabel) {
                    var modelEl = document.createElement('span');
                    modelEl.className = 'msg-model';
                    modelEl.textContent = '· ' + modelLabel;
                    speakerEl.appendChild(modelEl);
                }
                if (avatarEl) {
                    avatarEl.textContent = v.icon;
                    avatarEl.style.background = v.color;
                }
            }
            if (chunk.actor_type === 'cli' || sawCliMarkers) {
                msgEl.classList.add('msg-cli');
                targetEl.style.fontFamily = 'var(--font-mono,monospace)';
                targetEl.style.fontSize = '13px';
                targetEl.style.whiteSpace = 'pre-wrap';
            }
            if (chunk.done) break;
        }
        // Final markdown pass — streaming painted plain text (cheap); render the
        // full markdown once here. A `text_replace` chunk may already have rendered
        // markdown (after [DO:] stripping); this is idempotent.
        if (fullText) { paintMarkdown(fullText); }
        else { targetEl.textContent = 'No response'; }
        targetEl.removeAttribute('id');
    } catch(e) {
        // Fallback to non-streaming
        try {
            var r = await EOS.post('/rooms/api/chat', { agent_id: currentAgent.id, text: text });
            targetEl.innerHTML = typeof EOS_UI !== 'undefined' && EOS_UI.renderMarkdown ? EOS_UI.renderMarkdown(r.response || 'No response') : esc(r.response || 'No response');
        } catch(e2) {
            targetEl.textContent = 'Error: could not get response';
        }
        targetEl.removeAttribute('id');
    }
    sending = false;
    document.getElementById('send-btn').disabled = false;
    input.focus();
    // Auto-speak the just-arrived reply if the room has it enabled. Uses
    // fullText so we read the assistant's plain answer (not the user's
    // outgoing prompt). speakText cancels any prior queued utterance.
    if (currentAgent && fullText && isAutoSpeakOn(currentAgent.id)) {
        speakText(fullText);
    }
    renderSidebar();
}

async function clearHistory() {
    if (!currentAgent) return;
    if (!await EOS_UI.confirm({message: 'Clear all messages with ' + currentAgent.name + '?', action: 'Clear', danger: true})) return;
    try {
        await EOS.api('/rooms/api/history/' + currentAgent.id, { method:'DELETE' });
        renderMessages([]);
        EOS_UI.toast('History cleared');
    } catch(e) { EOS_UI.toast('Failed to clear', false); }
}

function exportChat() {
    if (!currentAgent) return;
    var msgs = document.querySelectorAll('#chat-messages .msg');
    var lines = [];
    msgs.forEach(function(m) {
        var role = m.classList.contains('msg-user') ? 'You' : currentAgent.name;
        var text = m.childNodes[0].textContent;
        lines.push(role + ': ' + text);
    });
    if (!lines.length) { EOS_UI.toast('No messages to export', false); return; }
    var blob = new Blob([lines.join('\n\n')], {type:'text/plain'});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = currentAgent.name.replace(/\s+/g,'-') + '-chat.txt';
    a.click();
    EOS_UI.toast('Chat exported');
}


// --- Jump-to navigation (Phase 26) ---
//
// Long-running rooms accumulate weeks of conversation. Two kinds of dividers
// break the stream:
//   day      — first message of a new YYYY-MM-DD
//   session  — same-day gap > 90 min (a fresh sit-down)
// The picker lists both together (newest first), each row scrolls to the
// matching divider. Topic-level segmentation needs LLM analysis (read the
// room's text, group by subject shift) — deferred to V3; a placeholder
// "Analyze topics" entry could plug in here later without changing the shape.

var _lastRenderedSegments = [];

// Thin delegates over EOS_CONVERSATION (emptyos/web/static/eos-conversation.js).
// The shared module owns the actual formatting + modal + scroll logic; these
// wrappers preserve the call-site names already used inside renderMessages
// + inline onclick handlers in this file.
function _formatDay(d)         { return EOS_CONVERSATION.formatDay(d); }
function _formatGap(ms)        { return EOS_CONVERSATION.formatGap(ms); }
function _segmentTimeLabel(ts) { return EOS_CONVERSATION.segmentTimeLabel(ts); }
function _segmentPreview(m)    { return EOS_CONVERSATION.segmentPreview(m); }

function openJumpToSegment() {
    EOS_CONVERSATION.openJumpModal(_lastRenderedSegments || [], _jumpToSegment);
}
function _jumpToSegment(segId) {
    EOS_CONVERSATION.scrollToSegment(segId);
}

// Back-compat aliases — older inline handlers may still call these names.
function openJumpToDay() { openJumpToSegment(); }
function _jumpToDay(day) {
    var seg = (_lastRenderedSegments || []).find(function(s){ return s.kind === 'day' && s.day === day; });
    if (seg) _jumpToSegment(seg.id);
}

// --- Reply threading (Phase 25) ---
// --- Reply threading (Phase 25) ---
//
// Composing a reply: clicking the ↩ on a message stamps `_replyTo` and
// shows a context chip above the input. sendMessage attaches reply_to to
// the user message before persisting. Render walks the message list in
// renderMessages and prepends a quote box to any message with reply_to.

var _replyTo = null;  // {ts, speaker, preview}
var _msgIndexByTs = {};  // built in renderMessages, used by reply quote lookups

function startReply(ts) {
    if (!ts) return;
    var m = _msgIndexByTs[ts];
    if (!m) return;
    var actor = m.actor || {};
    var speaker = actor.id || (m.role === 'user' ? 'me' : 'assistant');
    var preview = (m.text || '').replace(/\s+/g, ' ').slice(0, 80);
    _replyTo = {ts: ts, speaker: speaker, preview: preview};
    var ctx = document.getElementById('reply-context');
    var txt = document.getElementById('reply-context-text');
    if (ctx && txt) {
        txt.innerHTML = '<strong>' + esc(speaker) + ':</strong> ' + esc(preview);
        ctx.classList.add('open');
    }
    var input = document.getElementById('chat-input');
    if (input) input.focus();
}

function cancelReply() {
    _replyTo = null;
    var ctx = document.getElementById('reply-context');
    if (ctx) ctx.classList.remove('open');
}


// --- Voice / drafts / pin / catch-up ---
// --- Voice playback (Phase 20) ---
//
// Browser-native speechSynthesis. Per-message 🔊 button speaks the bubble
// content. Per-room auto-speak toggle (localStorage) auto-fires after the
// streaming reply completes. New utterance always cancels any queued one.

var _autoSpeakKey = function(rid) { return 'rooms.autospeak.v1.' + rid; };

function isAutoSpeakOn(roomId) {
    try { return localStorage.getItem(_autoSpeakKey(roomId)) === '1'; }
    catch (e) { return false; }
}

function setAutoSpeak(roomId, on) {
    try {
        if (on) localStorage.setItem(_autoSpeakKey(roomId), '1');
        else localStorage.removeItem(_autoSpeakKey(roomId));
    } catch (e) {}
}

function speakStop() {
    try { window.speechSynthesis && window.speechSynthesis.cancel(); }
    catch (e) {}
}

function speakText(text) {
    if (!text || !window.speechSynthesis) return;
    speakStop();
    // Strip task markers + wikilinks + code blocks for cleaner TTS.
    var clean = String(text)
        .replace(/```[\s\S]*?```/g, ' code block ')
        .replace(/\[\[([^\]]+)\]\]/g, '$1')
        .replace(/🗨️\s*\S+/g, '')
        .replace(/📅\s*\d{4}-\d{2}-\d{2}/g, '')
        .replace(/✅\s*\d{4}-\d{2}-\d{2}/g, '')
        .trim();
    if (!clean) return;
    try {
        var u = new SpeechSynthesisUtterance(clean);
        u.rate = 1.05;
        u.pitch = 1.0;
        window.speechSynthesis.speak(u);
    } catch (e) {}
}

function toggleSpeakMessage(ts, text) {
    if (!window.speechSynthesis) {
        EOS_UI.toast('Browser TTS not available', false);
        return;
    }
    if (window.speechSynthesis.speaking) {
        speakStop();
        return;
    }
    speakText(text);
}

function toggleAutoSpeak() {
    if (!currentAgent) return;
    var wasOn = isAutoSpeakOn(currentAgent.id);
    setAutoSpeak(currentAgent.id, !wasOn);
    EOS_UI.toast('Auto-speak ' + (wasOn ? 'off' : 'on'));
    speakStop();
}

// --- Drafts (Phase 16) ---
//
// localStorage-backed per-room drafts so accidental tab close, browser quit,
// or daemon restart don't lose typing. Saved on every input event (debounced
// 400ms), restored on openChat, cleared on successful send.
//
// Format: localStorage["rooms.draft.v1.<room_id>"] = JSON({text, ts}).
// Cap: prune entries older than 30 days on load to keep storage tidy.

var _draftSaveTimer = null;
var DRAFT_PREFIX = 'rooms.draft.v1.';
var DRAFT_MAX_AGE_DAYS = 30;

function _draftKey(roomId) { return DRAFT_PREFIX + roomId; }

function saveDraft(roomId, text) {
    if (!roomId) return;
    if (!text || !text.trim()) {
        try { localStorage.removeItem(_draftKey(roomId)); } catch(e) {}
        return;
    }
    try {
        localStorage.setItem(_draftKey(roomId), JSON.stringify({
            text: text, ts: new Date().toISOString(),
        }));
    } catch(e) { /* quota exceeded — silent */ }
}

function loadDraft(roomId) {
    if (!roomId) return null;
    try {
        var raw = localStorage.getItem(_draftKey(roomId));
        if (!raw) return null;
        var d = JSON.parse(raw);
        return d && typeof d.text === 'string' ? d : null;
    } catch(e) { return null; }
}

function clearDraft(roomId) {
    if (!roomId) return;
    try { localStorage.removeItem(_draftKey(roomId)); } catch(e) {}
}

function pruneOldDrafts() {
    var cutoff = Date.now() - DRAFT_MAX_AGE_DAYS * 86400 * 1000;
    try {
        var stale = [];
        for (var i = 0; i < localStorage.length; i++) {
            var k = localStorage.key(i);
            if (!k || !k.startsWith(DRAFT_PREFIX)) continue;
            try {
                var d = JSON.parse(localStorage.getItem(k));
                if (d && d.ts && new Date(d.ts).getTime() < cutoff) stale.push(k);
            } catch(e) { stale.push(k); }
        }
        stale.forEach(function(k){ localStorage.removeItem(k); });
    } catch(e) { /* private mode / quota — silent */ }
}

function scheduleDraftSave() {
    if (!currentAgent) return;
    if (_draftSaveTimer) clearTimeout(_draftSaveTimer);
    _draftSaveTimer = setTimeout(function() {
        var input = document.getElementById('chat-input');
        if (!input || !currentAgent) return;
        saveDraft(currentAgent.id, input.value || '');
    }, 400);
}

// Run once on page load — keep localStorage tidy.
pruneOldDrafts();

// --- Pin messages (Phase 15) ---

async function togglePin(ts) {
    if (!currentAgent || !ts) return;
    var pinned = (currentAgent.pinned_ts || []);
    var alreadyPinned = pinned.indexOf(ts) >= 0;
    var path = alreadyPinned ? '/unpin' : '/pin';
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + path, {ts: ts});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        currentAgent.pinned_ts = res.pinned_ts || [];
        // Re-render history to refresh both the pinned panel and per-message dots.
        var data = await EOS.api('/rooms/api/history/' + encodeURIComponent(currentAgent.id));
        renderMessages(data.messages || []);
    } catch(e) {
        EOS_UI.toast('Pin failed', false);
    }
}

async function unpinMessage(ts) { await togglePin(ts); }

function scrollToMessage(ts) {
    if (!ts) return;
    var node = document.querySelector('.msg[data-ts="' + ts + '"]');
    if (!node) return;
    node.scrollIntoView({behavior: 'smooth', block: 'center'});
    // Brief highlight so the eye lands.
    var prev = node.style.boxShadow;
    node.style.transition = 'box-shadow 0.6s ease';
    node.style.boxShadow = '0 0 0 3px color-mix(in srgb,var(--accent) 50%,transparent)';
    setTimeout(function() { node.style.boxShadow = prev; }, 1200);
}

// --- Catch me up (Phase 9c) ---
//
// When openChat lands a room with N>0 messages received since last visit,
// inject a banner above the thread offering to summarise. Doesn't trigger
// when N is huge (likely first visit) or when the user sent the last turn.

function maybeRenderCatchUpBanner(messages, lastVisited) {
    if (!currentAgent) return;
    var el = document.getElementById('chat-messages');
    if (!el || !messages || !messages.length) return;
    if (!lastVisited) return;  // first visit: empty-state already covers
    var newCount = 0;
    for (var i = 0; i < messages.length; i++) {
        var m = messages[i];
        var ts = m.ts || '';
        if (!ts || ts <= lastVisited) continue;
        if (m.role === 'user') continue;
        newCount++;
    }
    if (newCount < 1 || newCount > 50) return;
    var banner = document.createElement('div');
    banner.className = 'catch-up-banner';
    banner.id = 'catch-up-banner';
    banner.innerHTML =
        '<div class="cu-text"><strong>' + newCount + '</strong> new message' +
            (newCount !== 1 ? 's' : '') + ' since you were last here.</div>' +
        '<div class="cu-actions" style="display:flex;gap:6px">' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="dismissCatchUp()">Dismiss</button>' +
            '<button class="eos-btn-sm" style="background:var(--accent);color:var(--accent-ink);border:0" onclick="runCatchUp(\'' + escAttr(lastVisited) + '\')">Catch me up</button>' +
        '</div>' +
        '<div class="cu-summary" id="cu-summary"></div>';
    el.insertBefore(banner, el.firstChild);
}

function dismissCatchUp() {
    var b = document.getElementById('catch-up-banner');
    if (b) b.remove();
}

async function runCatchUp(sinceTs) {
    if (!currentAgent) return;
    var banner = document.getElementById('catch-up-banner');
    var summaryEl = document.getElementById('cu-summary');
    if (!banner || !summaryEl) return;
    summaryEl.textContent = 'Summarising…';
    banner.classList.add('expanded');
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/catch-up',
            {since: sinceTs});
        if (res && res.error) { summaryEl.textContent = 'Error: ' + res.error; return; }
        summaryEl.innerHTML = (typeof EOS_UI !== 'undefined' && EOS_UI.renderMarkdown)
            ? EOS_UI.renderMarkdown(res.summary || '(no summary)')
            : esc(res.summary || '(no summary)');
        // Add a small "dismiss" inside the expanded panel
        var dismiss = document.createElement('button');
        dismiss.className = 'eos-btn-sm eos-btn-ghost';
        dismiss.style.cssText = 'align-self:flex-end;margin-top:6px';
        dismiss.textContent = 'Got it';
        dismiss.onclick = dismissCatchUp;
        banner.appendChild(dismiss);
    } catch(e) {
        summaryEl.textContent = 'Failed to summarise.';
    }
}

