// rooms-pending.js — review-gate pending actions + Activity drawer.
// Owns: global pending dashboard, per-room pending cache (_pendingMap),
// renderPendingCard, applyAction, rejectAction, activity drawer with
// Pending/Tasks/Memory/Knowledge tabs.

// --- Global pending dashboard (Phase 6.6) ---
//
// Aggregates pending actions across every room into one review surface.
// Reuses renderPendingCard + applyAction / rejectAction so the cards look
// and behave the same as inline ones. Grouped by room id with the room
// name as a header.

async function refreshGlobalPendingBadge() {
    var btn = document.getElementById('global-pending-btn');
    var countEl = document.getElementById('global-pending-count');
    if (!btn || !countEl) return;
    try {
        var list = await EOS.api('/rooms/api/pending') || [];
        var n = list.length;
        countEl.textContent = String(n);
        btn.style.display = n > 0 ? '' : 'none';
    } catch(e) {
        btn.style.display = 'none';
    }
}

async function openGlobalPending() {
    var list = [];
    try {
        list = await EOS.api('/rooms/api/pending?status=all') || [];
    } catch(e) {
        EOS_UI.toast('Failed to load pending actions', false);
        return;
    }
    // Hydrate _pendingMap so apply/reject DOM updates pick the latest record.
    list.forEach(function(a) { _pendingMap[a.id] = a; });
    // Group by room id; preserve room names from `agents`.
    var byRoom = {};
    list.forEach(function(a) {
        var rid = a.room_id || '(unknown)';
        (byRoom[rid] = byRoom[rid] || []).push(a);
    });
    var pendingTotal = list.filter(isOpenAction).length;
    var roomIds = Object.keys(byRoom).sort(function(x, y) {
        // Pending-first room ordering: rooms with any pending sort to top.
        var hx = byRoom[x].some(isOpenAction);
        var hy = byRoom[y].some(isOpenAction);
        if (hx !== hy) return hx ? -1 : 1;
        return (agentNameById(x) || x).localeCompare(agentNameById(y) || y);
    });
    var sections;
    if (!roomIds.length) {
        sections = '<div style="color:var(--text-muted);font-size:13px;padding:30px;text-align:center">No pending or resolved actions yet. CLI participants emit [DO:] tags that show up here once the user reviews them.</div>';
    } else {
        sections = roomIds.map(function(rid) {
            var entries = byRoom[rid].slice().sort(function(a, b) {
                if (isOpenAction(a) !== isOpenAction(b)) return isOpenAction(a) ? -1 : 1;
                return (a.ts || '').localeCompare(b.ts || '');
            });
            var pendingForRoom = entries.filter(isOpenAction).length;
            var roomName = agentNameById(rid);
            var openLink = '<button class="eos-btn-sm eos-btn-ghost" style="margin-left:auto;font-size:11px" onclick="EOS_UI.closeModal();openChat(\'' + escAttr(rid) + '\')">Open room →</button>';
            return '<div style="margin-bottom:18px">' +
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border)">' +
                    '<span style="font-weight:600;font-size:13px">' + esc(roomName) + '</span>' +
                    (pendingForRoom > 0
                        ? '<span style="font-size:11px;color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent);padding:1px 8px;border-radius:10px">' + pendingForRoom + ' pending</span>'
                        : '<span style="font-size:11px;color:var(--text-muted)">all reviewed</span>') +
                    openLink +
                '</div>' +
                entries.map(renderPendingCard).join('') +
                '</div>';
        }).join('');
    }
    var headerNote = '<div style="font-size:11px;color:var(--text-muted);margin-bottom:12px">' +
        pendingTotal + ' pending across ' + roomIds.length + ' room' + (roomIds.length !== 1 ? 's' : '') +
        ' · click a card\'s Apply or Reject to resolve' +
        '</div>';
    EOS_UI.modal({
        title: '⏳ Pending across all rooms',
        body: headerNote + sections,
        width: '600px',
    });
}

// --- Review gate: pending [DO:] actions (Phase 5) ---

// Per-room cache of pending-action records (any status). Keyed by action id.
// Populated on openChat + replenished after each apply/reject.
var _pendingMap = {};

async function loadPendingForRoom(roomId) {
    _pendingMap = {};
    try {
        var list = await EOS.api('/rooms/api/rooms/' + encodeURIComponent(roomId) + '/pending?status=all') || [];
        list.forEach(function(a) { _pendingMap[a.id] = a; });
    } catch(e) { /* projects/rooms missing — treat as empty */ }
    updatePendingBadge();
}

// An action still needs the user in two shapes: `pending` (awaiting
// Apply/Reject) and `approving` (claimed, then the process died mid-execution
// — outcome unknown). Both must be counted and surfaced; only the first is
// re-appliable. Filtering `status === 'pending'` exactly is what made an
// interrupted action vanish from every count.
function isOpenAction(a) {
    return a.status === 'pending' || a.status === 'approving';
}

function updatePendingBadge() {
    var pending = Object.values(_pendingMap).filter(isOpenAction);
    // Hidden compatibility node — kept so other code paths reading
    // chat-pending-count don't break, but the visible counter is the
    // unified Activity badge.
    var legacy = document.getElementById('chat-pending-count');
    if (legacy) legacy.textContent = pending.length ? '(' + pending.length + ')' : '';
    updateActivityBadge();
    // Refresh the drawer tab count if the drawer is open.
    var tab = document.getElementById('activity-tab-pending-count');
    if (tab) tab.textContent = pending.length ? '(' + pending.length + ')' : '';
}

// Combined Tasks + Pending counter that drives the chat header pill.
// Tasks count comes from the data attribute set by loadRoomTaskCount; the
// pending count is computed from _pendingMap.
function updateActivityBadge() {
    var pill = document.getElementById('chat-activity-count');
    if (!pill) return;
    var pending = Object.values(_pendingMap).filter(isOpenAction).length;
    var tasksOpen = parseInt(pill.dataset.tasksOpen || '0', 10) || 0;
    var total = pending + tasksOpen;
    pill.textContent = total ? '(' + total + ')' : '';
}


// --- Activity drawer (Pending + Tasks + Memory + Knowledge) ---
// --- Activity drawer (Pending + Tasks tabs) ---

var _activityTab = 'pending';

function openActivityDrawer() {
    if (!currentAgent) return;
    document.getElementById('activity-drawer-title').textContent =
        'Activity · ' + (currentAgent.name || currentAgent.id);
    document.getElementById('activity-drawer').classList.add('open');
    document.getElementById('activity-backdrop').classList.add('open');
    document.getElementById('activity-drawer').setAttribute('aria-hidden', 'false');
    // Team tab only shows for rooms with a lead. If the persisted tab was
    // 'team' but this room isn't a team, fall back to pending.
    if (typeof syncTeamTabVisibility === 'function') syncTeamTabVisibility();
    if (_activityTab === 'team' && !(typeof roomIsTeamCandidate === 'function' && roomIsTeamCandidate(currentAgent))) {
        _activityTab = 'pending';
    }
    switchActivityTab(_activityTab);
}

function closeActivityDrawer() {
    document.getElementById('activity-drawer').classList.remove('open');
    document.getElementById('activity-backdrop').classList.remove('open');
    document.getElementById('activity-drawer').setAttribute('aria-hidden', 'true');
}

function switchActivityTab(tab) {
    _activityTab = tab;
    document.getElementById('activity-tab-pending').classList.toggle('active', tab === 'pending');
    document.getElementById('activity-tab-tasks').classList.toggle('active', tab === 'tasks');
    document.getElementById('activity-tab-knowledge').classList.toggle('active', tab === 'knowledge');
    document.getElementById('activity-tab-memory').classList.toggle('active', tab === 'memory');
    var teamBtn = document.getElementById('activity-tab-team');
    if (teamBtn) teamBtn.classList.toggle('active', tab === 'team');
    if (tab === 'pending') {
        renderActivityPendingTab();
    } else if (tab === 'tasks') {
        renderActivityTasksTab();
    } else if (tab === 'knowledge') {
        renderActivityKnowledgeTab();
    } else if (tab === 'memory') {
        renderActivityMemoryTab();
    } else if (tab === 'team') {
        renderActivityTeamTab();
    }
}

function renderActivityPendingTab() {
    var body = document.getElementById('activity-drawer-body');
    if (!body) return;
    var all = Object.values(_pendingMap).sort(function(a,b){
        return (a.ts || '').localeCompare(b.ts || '');
    });
    var pending = all.filter(isOpenAction);
    var resolved = all.filter(function(a){ return !isOpenAction(a); });
    var html = '';
    if (!pending.length && !resolved.length) {
        html = '<div style="color:var(--text-muted);font-size:13px;padding:20px;text-align:center">No pending actions yet. CLI participants emit [DO:] tags that show up here for review.</div>';
    } else {
        if (pending.length) {
            html += '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Awaiting review (' + pending.length + ')</div>';
            html += pending.map(renderPendingCard).join('');
        }
        if (resolved.length) {
            html += '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin:18px 0 8px">History (' + resolved.length + ')</div>';
            html += resolved.map(renderPendingCard).join('');
        }
    }
    body.innerHTML = html;
}

// Tasks tab has two sub-views: tasks attached to THIS room vs the universal
// vault-wide pool. Sub-tab + filter state persist for the drawer's lifetime
// so flipping back and forth doesn't lose context.
var _taskSubTab = 'attached';   // 'attached' | 'all'
var _allTasksFilter = 'open';   // 'open' | 'today' | 'overdue' | 'done'
var _allTasksQuery = '';

async function renderActivityTasksTab() {
    var body = document.getElementById('activity-drawer-body');
    if (!body || !currentAgent) return;
    var subToggle =
        '<div style="display:flex;gap:4px;background:var(--bg-elevated);padding:3px;border-radius:8px;margin-bottom:14px">' +
            '<button class="eos-btn-sm" style="flex:1;background:' + (_taskSubTab === 'attached' ? 'var(--bg-card)' : 'transparent') + ';border:0;font-weight:' + (_taskSubTab === 'attached' ? '600' : '400') + '" onclick="setTaskSubTab(\'attached\')">In this room</button>' +
            '<button class="eos-btn-sm" style="flex:1;background:' + (_taskSubTab === 'all' ? 'var(--bg-card)' : 'transparent') + ';border:0;font-weight:' + (_taskSubTab === 'all' ? '600' : '400') + '" onclick="setTaskSubTab(\'all\')">All vault</button>' +
        '</div>';
    body.innerHTML = subToggle + '<div id="task-subview" style="color:var(--text-muted);font-size:12px;padding:20px;text-align:center">Loading…</div>';
    if (_taskSubTab === 'attached') {
        await _renderAttachedTasksSubview();
    } else {
        await _renderAllTasksSubview();
    }
}

function setTaskSubTab(name) {
    _taskSubTab = name;
    renderActivityTasksTab();
}

async function _renderAttachedTasksSubview() {
    var sub = document.getElementById('task-subview');
    if (!sub || !currentAgent) return;
    var tasks = [];
    try {
        tasks = await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/tasks') || [];
    } catch(e) {
        sub.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:20px;text-align:center">Tasks unavailable (projects app missing).</div>';
        return;
    }
    var openCount = tasks.filter(function(t){ return !t.done; }).length;
    document.getElementById('activity-tab-tasks-count').textContent = tasks.length ? '(' + openCount + ')' : '';
    var listHtml;
    if (!tasks.length) {
        listHtml = '<div style="color:var(--text-muted);font-size:13px;padding:14px;text-align:center">No tasks attached yet. Use "All vault" to attach existing ones, or add a fresh task below.</div>';
    } else {
        listHtml = tasks.map(function(t) {
            var stateIcon = t.done ? '✅' : '⬜';
            var due = t.due ? '<span style="color:var(--text-muted);font-size:11px;margin-left:6px">📅 ' + esc(t.due) + '</span>' : '';
            var proj = t.project ? '<span style="color:var(--text-muted);font-size:11px;margin-left:6px">[' + esc(t.project) + ']</span>' : '';
            var displayText = _stripTaskMarkers(t.text || '');
            var detachBtn = '<button class="eos-btn-sm eos-btn-ghost" title="Detach from this room" style="font-size:11px;flex-shrink:0" onclick="detachTaskFromRoom(\'' + escAttr(t.file || '') + '\',' + (t.line || 0) + ')">×</button>';
            return '<div style="display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;margin-bottom:6px">' +
                '<span style="flex-shrink:0;cursor:pointer" title="Click to complete · right-click to complete with a note" ' +
                'onclick="toggleTaskFromDrawer(\'' + escAttr(t.file || '') + '\',' + (t.line || 0) + ',' + escAttr(JSON.stringify(t.text || '')) + ')" ' +
                'oncontextmenu="event.preventDefault();completeTaskFromDrawer(\'' + escAttr(t.file || '') + '\',' + (t.line || 0) + ',' + escAttr(JSON.stringify(t.text || '')) + ');return false">' + stateIcon + '</span>' +
                '<div style="flex:1;min-width:0">' +
                    '<div' + (t.done ? ' style="text-decoration:line-through;color:var(--text-muted)"' : '') + '>' + esc(displayText) + due + proj + '</div>' +
                '</div>' + detachBtn + '</div>';
        }).join('');
    }
    sub.innerHTML =
        '<div>' + listHtml + '</div>' +
        '<div style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">' +
            '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Add task</div>' +
            '<input id="task-text" class="eos-form-input" type="text" placeholder="What needs doing?" style="width:100%">' +
            '<div style="display:flex;gap:6px;margin-top:6px">' +
                '<input id="task-project" class="eos-form-input" type="text" placeholder="Project (default: inbox)" style="flex:2">' +
                '<input id="task-due" class="eos-form-input" type="date" style="flex:1">' +
            '</div>' +
            '<button class="eos-btn eos-btn-primary" style="margin-top:10px;width:100%" onclick="submitAttachTaskFromDrawer()">Add</button>' +
        '</div>';
}

async function _renderAllTasksSubview() {
    var sub = document.getElementById('task-subview');
    if (!sub || !currentAgent) return;
    var roomId = currentAgent.id;
    var statusParam = _allTasksFilter;  // backend handles open/today/overdue/done
    var url = '/task/api/list';
    var tasks = [];
    try {
        tasks = await EOS.api(url) || [];
    } catch(e) {
        sub.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:20px;text-align:center">Task aggregator unavailable. The /task/ app may not be loaded.</div>';
        return;
    }
    var today = new Date().toISOString().slice(0, 10);
    function matchesFilter(t) {
        if (_allTasksFilter === 'done') return !!t.done;
        if (t.done) return false;
        if (_allTasksFilter === 'today') return t.due === today || (t.overdue_days || 0) > 0;
        if (_allTasksFilter === 'overdue') return (t.overdue_days || 0) > 0;
        return true; // open
    }
    var q = (_allTasksQuery || '').toLowerCase().trim();
    var filtered = tasks.filter(function(t) {
        if (!matchesFilter(t)) return false;
        if (!q) return true;
        return (t.text || '').toLowerCase().indexOf(q) >= 0 ||
               (t.project || '').toLowerCase().indexOf(q) >= 0 ||
               (t.file || '').toLowerCase().indexOf(q) >= 0;
    });
    // Cap to 200 so the drawer stays responsive on large vaults.
    var capped = filtered.slice(0, 200);
    var counts = {
        open: tasks.filter(function(t){ return !t.done; }).length,
        today: tasks.filter(function(t){ return !t.done && (t.due === today || (t.overdue_days || 0) > 0); }).length,
        overdue: tasks.filter(function(t){ return !t.done && (t.overdue_days || 0) > 0; }).length,
        done: tasks.filter(function(t){ return t.done; }).length,
    };
    function chip(name, label) {
        var active = _allTasksFilter === name;
        return '<button class="eos-btn-sm" style="background:' + (active ? 'var(--accent)' : 'var(--bg-elevated)') + ';color:' + (active ? 'var(--accent-ink)' : 'var(--text)') + ';border:0;font-size:12px" onclick="setAllTasksFilter(\'' + name + '\')">' + label + ' <span style="opacity:0.7">' + counts[name] + '</span></button>';
    }
    var listHtml;
    if (!capped.length) {
        listHtml = '<div style="color:var(--text-muted);font-size:13px;padding:20px;text-align:center">No tasks match.</div>';
    } else {
        listHtml = capped.map(function(t) {
            var stateIcon = t.done ? '✅' : '⬜';
            var due = t.due ? '<span style="color:var(--text-muted);font-size:11px;margin-left:6px">📅 ' + esc(t.due) + '</span>' : '';
            var proj = t.project ? '<span style="color:var(--text-muted);font-size:11px;margin-left:6px">[' + esc(t.project) + ']</span>' : '';
            var displayText = _stripTaskMarkers(t.text || '');
            var attachedHere = (t.room_id === roomId);
            var roomTag = '';
            if (t.room_id && !attachedHere) {
                roomTag = '<span style="color:var(--text-muted);font-size:11px;margin-left:6px">🗨️ ' + esc(agentNameById(t.room_id)) + '</span>';
            }
            var attachBtn = attachedHere
                ? '<span style="color:var(--accent);font-size:11px;flex-shrink:0;align-self:center">✓ here</span>'
                : '<button class="eos-btn-sm eos-btn-ghost" title="Attach this task to this room" style="font-size:11px;flex-shrink:0" onclick="attachTaskToRoom(\'' + escAttr(t.file || '') + '\',' + (t.line || 0) + ')">+ Attach</button>';
            return '<div style="display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;margin-bottom:6px">' +
                '<span style="flex-shrink:0;cursor:pointer" title="Click to complete · right-click to complete with a note" ' +
                'onclick="toggleTaskFromDrawer(\'' + escAttr(t.file || '') + '\',' + (t.line || 0) + ',' + escAttr(JSON.stringify(t.text || '')) + ')" ' +
                'oncontextmenu="event.preventDefault();completeTaskFromDrawer(\'' + escAttr(t.file || '') + '\',' + (t.line || 0) + ',' + escAttr(JSON.stringify(t.text || '')) + ');return false">' + stateIcon + '</span>' +
                '<div style="flex:1;min-width:0">' +
                    '<div' + (t.done ? ' style="text-decoration:line-through;color:var(--text-muted)"' : '') + '>' + esc(displayText) + due + proj + roomTag + '</div>' +
                '</div>' + attachBtn + '</div>';
        }).join('');
        if (filtered.length > capped.length) {
            listHtml += '<div style="color:var(--text-muted);font-size:11px;text-align:center;padding:6px">Showing first 200 of ' + filtered.length + '. Refine the search to narrow.</div>';
        }
    }
    sub.innerHTML =
        '<input id="task-search" class="eos-form-input" type="text" placeholder="Search vault tasks…" value="' + escAttr(_allTasksQuery) + '" oninput="onAllTasksSearch(this.value)" style="width:100%;margin-bottom:10px">' +
        '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:12px">' +
            chip('open', 'Open') + chip('today', 'Today') + chip('overdue', 'Overdue') + chip('done', 'Done') +
        '</div>' +
        '<div>' + listHtml + '</div>';
}

function setAllTasksFilter(f) {
    _allTasksFilter = f;
    _renderAllTasksSubview();
}

var _allTasksSearchTimer = null;
function onAllTasksSearch(v) {
    _allTasksQuery = v || '';
    if (_allTasksSearchTimer) clearTimeout(_allTasksSearchTimer);
    _allTasksSearchTimer = setTimeout(function() { _renderAllTasksSubview(); }, 180);
}

function _stripTaskMarkers(text) {
    return (text || '')
        .replace(/\s*🗨️\s*\S+/g, '')
        .replace(/\s*📅\s*\d{4}-\d{2}-\d{2}/g, '')
        .replace(/\s*✅\s*\d{4}-\d{2}-\d{2}/g, '')
        .trim();
}

async function toggleTaskFromDrawer(file, line, text) {
    if (!file || !line) return;
    try {
        var res = await EOS.post('/task/api/toggle', {file: file, line: line, text: text || ''});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        // Re-render whichever sub-view is open.
        renderActivityTasksTab();
        loadRoomTaskCount(currentAgent.id);
    } catch(e) {
        EOS_UI.toast('Toggle failed', false);
    }
}

function completeTaskFromDrawer(file, line, text) {
    if (!file || !line) return;
    EOS_UI.completeTaskWithNote({ file: file, line: line, text: text || '' }, {
        onSuccess: function() {
            renderActivityTasksTab();
            loadRoomTaskCount(currentAgent.id);
        },
    });
}

async function attachTaskToRoom(file, line) {
    if (!file || !line || !currentAgent) return;
    try {
        var res = await EOS.post('/task/api/attach-room',
            {file: file, line: line, room_id: currentAgent.id});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        EOS_UI.toast('Attached to ' + (currentAgent.name || currentAgent.id));
        renderActivityTasksTab();
        loadRoomTaskCount(currentAgent.id);
    } catch(e) {
        EOS_UI.toast('Attach failed', false);
    }
}

async function detachTaskFromRoom(file, line) {
    if (!file || !line) return;
    try {
        await EOS.post('/task/api/attach-room',
            {file: file, line: line, room_id: ''});
        EOS_UI.toast('Detached');
        renderActivityTasksTab();
        loadRoomTaskCount(currentAgent.id);
    } catch(e) {
        EOS_UI.toast('Detach failed', false);
    }
}

// --- Memory tab (Phase 26) ---
//
// Per-room facts the agent should remember across turns. Surfaced in the
// LLM prompt as a "Memory" block so the agent has stable context beyond
// the rolling history window.

async function renderActivityMemoryTab() {
    var body = document.getElementById('activity-drawer-body');
    if (!body || !currentAgent) return;
    body.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:20px;text-align:center">Loading…</div>';
    var memory = [];
    try {
        memory = await EOS.api('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/memory') || [];
    } catch (e) { memory = []; }
    var countEl = document.getElementById('activity-tab-memory-count');
    if (countEl) countEl.textContent = memory.length ? '(' + memory.length + ')' : '';
    var listHtml;
    if (!memory.length) {
        listHtml = '<div style="color:var(--text-muted);font-size:13px;padding:14px;text-align:center">No memories yet. Use <code style="background:var(--bg-elevated);padding:1px 5px;border-radius:3px">/remember &lt;fact&gt;</code> to add one.</div>';
    } else {
        listHtml = memory.map(function(m) {
            var when = m.ts ? new Date(m.ts).toLocaleDateString([], {month:'short', day:'numeric'}) : '';
            return '<div style="display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;margin-bottom:6px">' +
                '<span style="flex-shrink:0;color:var(--accent)">🧠</span>' +
                '<div style="flex:1;min-width:0">' +
                    '<div style="line-height:1.4">' + esc(m.fact) + '</div>' +
                    '<div style="font-size:11px;color:var(--text-muted);margin-top:2px">' + esc(when) + '</div>' +
                '</div>' +
                '<button class="eos-btn-sm eos-btn-ghost" title="Forget" style="font-size:14px;padding:2px 6px;flex-shrink:0" onclick="forgetMemory(\'' + escAttr(m.id) + '\')">×</button>' +
                '</div>';
        }).join('');
    }
    body.innerHTML =
        '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Persistent memory</div>' +
        '<div>' + listHtml + '</div>' +
        '<div style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">' +
            '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Add a memory</div>' +
            '<input id="mem-fact" class="eos-form-input" type="text" placeholder="e.g. Kevin prefers tabs over spaces" style="width:100%">' +
            '<button class="eos-btn eos-btn-primary" style="margin-top:10px;width:100%" onclick="submitMemoryFromDrawer()">Remember</button>' +
        '</div>' +
        '<div style="margin-top:14px;font-size:11px;color:var(--text-muted);line-height:1.5">' +
            'Memories are prepended to the LLM context as a "Memory" block on every turn. Capped at 30 per room — oldest drop first.' +
        '</div>';
}

async function submitMemoryFromDrawer() {
    if (!currentAgent) return;
    var fact = (document.getElementById('mem-fact').value || '').trim();
    if (!fact) { EOS_UI.toast('Memory text required', false); return; }
    await rememberFact(fact);
    renderActivityMemoryTab();
}

async function rememberFact(fact) {
    if (!currentAgent || !fact) return;
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/memory', {fact: fact});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        EOS_UI.toast('Remembered');
    } catch (e) { EOS_UI.toast('Failed to remember', false); }
}

async function forgetMemory(memoryId) {
    if (!currentAgent || !memoryId) return;
    if (!await EOS_UI.confirm({message: 'Forget this memory?', action: 'Forget', danger: true})) return;
    try {
        await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) +
            '/memory/' + encodeURIComponent(memoryId), {method:'DELETE'});
        renderActivityMemoryTab();
    } catch (e) { EOS_UI.toast('Failed to forget', false); }
}

// --- Knowledge tab (Phase 18) ---
//
// Lists the room's knowledge_files with × to remove + an inline picker to
// add. Picker fires the same /rooms/api/vault-search endpoint used by the
// [[ wikilink mechanism. Saves every add/remove to the room record so it
// persists into knowledge_files for future LLM turns.

var _knowledgeSearchTimer = null;
var _knowledgePickerSeq = 0;

async function renderActivityKnowledgeTab() {
    var body = document.getElementById('activity-drawer-body');
    if (!body || !currentAgent) return;
    var files = (currentAgent.knowledge_files || []).slice();
    var countEl = document.getElementById('activity-tab-knowledge-count');
    if (countEl) countEl.textContent = files.length ? '(' + files.length + ')' : '';
    var listHtml;
    if (!files.length) {
        listHtml = '<div style="color:var(--text-muted);font-size:13px;padding:14px;text-align:center">No persistent knowledge attached. Add a vault note below — it\'s included as context in every turn.</div>';
    } else {
        listHtml = files.map(function(p) {
            var name = p.split('/').pop().replace(/\.md$/i, '');
            return '<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;margin-bottom:6px">' +
                '<span style="font-size:14px;flex-shrink:0">📎</span>' +
                '<div style="flex:1;min-width:0">' +
                    '<div style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(name) + '</div>' +
                    '<div style="font-size:11px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(p) + '</div>' +
                '</div>' +
                '<button class="eos-btn-sm eos-btn-ghost" title="Remove" style="font-size:14px;padding:2px 6px;flex-shrink:0" onclick="removeKnowledgeFromDrawer(\'' + escAttr(p) + '\')">×</button>' +
                '</div>';
        }).join('');
    }
    body.innerHTML =
        '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Attached knowledge</div>' +
        '<div>' + listHtml + '</div>' +
        '<div style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">' +
            '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Add from vault</div>' +
            '<input id="kb-search" class="eos-form-input" type="text" placeholder="Search vault notes…" oninput="onKnowledgePickerSearch(this.value)" style="width:100%">' +
            '<div id="kb-picker-results" style="max-height:240px;overflow-y:auto;margin-top:8px"></div>' +
        '</div>' +
        '<div style="margin-top:14px;font-size:11px;color:var(--text-muted);line-height:1.5">' +
            'Tip: every attached note is included in this room\'s LLM context on every turn. For one-off references, type <code style="background:var(--bg-elevated);padding:1px 5px;border-radius:3px">[[</code> in the chat input instead.' +
        '</div>';
    // Trigger an empty search to populate the picker with recent notes.
    onKnowledgePickerSearch('');
}

function onKnowledgePickerSearch(v) {
    if (_knowledgeSearchTimer) clearTimeout(_knowledgeSearchTimer);
    _knowledgeSearchTimer = setTimeout(async function() {
        var seq = ++_knowledgePickerSeq;
        var res;
        try {
            res = await EOS.api('/rooms/api/vault-search?q=' + encodeURIComponent(v || '') + '&limit=20');
        } catch(e) { res = null; }
        if (seq !== _knowledgePickerSeq) return;
        var results = document.getElementById('kb-picker-results');
        if (!results) return;
        var files = (res && res.files) || [];
        var attached = new Set((currentAgent && currentAgent.knowledge_files) || []);
        if (!files.length) {
            results.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px;text-align:center">No matches.</div>';
            return;
        }
        results.innerHTML = files.map(function(f) {
            var path = f.path || '';
            var isAttached = attached.has(path) || attached.has(path.replace(/\.md$/i, ''));
            return '<div style="cursor:pointer;display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:6px;font-size:12px;margin-bottom:3px;background:var(--bg-elevated);' + (isAttached ? 'opacity:0.5;cursor:default' : '') + '"' +
                (isAttached ? '' : ' onclick="addKnowledgeFromDrawer(\'' + escAttr(path) + '\')"') + '>' +
                '<span style="flex-shrink:0">📄</span>' +
                '<div style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
                    esc(f.name || '') + '<span style="color:var(--text-muted);margin-left:6px">' + esc(f.folder || '') + '</span>' +
                '</div>' +
                (isAttached ? '<span style="font-size:10px;color:var(--accent);flex-shrink:0">attached</span>' : '<span style="font-size:14px;flex-shrink:0">+</span>') +
                '</div>';
        }).join('');
    }, 180);
}

async function addKnowledgeFromDrawer(path) {
    if (!currentAgent || !path) return;
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/knowledge', {path: path});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        currentAgent.knowledge_files = res.knowledge_files || [];
        // Sync the agents cache so a re-open doesn't lose the change.
        var cached = (agents || []).find(function(a){ return a.id === currentAgent.id; });
        if (cached) cached.knowledge_files = currentAgent.knowledge_files;
        EOS_UI.toast('Attached');
        renderActivityKnowledgeTab();
    } catch(e) {
        EOS_UI.toast('Attach failed', false);
    }
}

async function removeKnowledgeFromDrawer(path) {
    if (!currentAgent || !path) return;
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/knowledge/remove', {path: path});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        currentAgent.knowledge_files = res.knowledge_files || [];
        var cached = (agents || []).find(function(a){ return a.id === currentAgent.id; });
        if (cached) cached.knowledge_files = currentAgent.knowledge_files;
        EOS_UI.toast('Removed');
        renderActivityKnowledgeTab();
    } catch(e) {
        EOS_UI.toast('Remove failed', false);
    }
}

async function submitAttachTaskFromDrawer() {
    if (!currentAgent) return;
    var text = (document.getElementById('task-text').value || '').trim();
    if (!text) { EOS_UI.toast('Task text required', false); return; }
    var project = (document.getElementById('task-project').value || 'inbox').trim() || 'inbox';
    var due = (document.getElementById('task-due').value || '').trim();
    try {
        var res = await EOS.post('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/tasks',
            {text: text, project_id: project, due: due});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        EOS_UI.toast('Task added');
        loadRoomTaskCount(currentAgent.id);
        renderActivityTasksTab();
    } catch(e) {
        EOS_UI.toast('Failed to add task', false);
    }
}

// Build the inner HTML for a pending card. `cardId` lets us scope updates.
function renderPendingCard(action) {
    var st = action.status || 'pending';
    var cls = 'pending-card' + (st !== 'pending' ? ' ' + st : '');
    var argsStr = '';
    try { argsStr = JSON.stringify(action.args || {}, null, 2); } catch(e) { argsStr = String(action.args); }
    var icons = {pending:'⏳', approving:'⚠', applied:'✓', rejected:'✗', failed:'✗'};
    var icon = icons[st] || '⏳';
    var actor = action.source_actor || {};
    var actorName = actor.id === 'claude-cli' ? 'Claude Code CLI' : (actor.id || 'agent');
    var actorIcon = actor.type === 'cli' ? '⚡' : '◆';
    var buttons;
    if (st === 'pending') {
        // Diff-shaped actions (sandboxed writes) are not editable — the diff
        // was captured from the original args; backend refuses edits on them.
        var editBtn = (action.proposed_changes || []).length ? '' :
            '<button class="eos-btn-sm eos-btn-ghost" onclick="editAction(\'' + escAttr(action.id) + '\', this)">&#9998; Edit</button>';
        buttons = editBtn +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="rejectAction(\'' + escAttr(action.id) + '\')">Reject</button>' +
            '<button class="eos-btn-sm" style="background:var(--accent);color:var(--accent-ink)" onclick="applyAction(\'' + escAttr(action.id) + '\')">Apply</button>';
    } else if (st === 'approving') {
        // Claimed, then the process died mid-execution. The effect may or may
        // not have landed, so we never re-run it — the user checks and records
        // what they found. Re-applying could double-execute, which is the exact
        // bug the claim prevents.
        buttons = '<span class="pending-status" title="Started, then interrupted before the outcome was recorded">started — outcome unknown</span>' +
            // Both ghost, deliberately equal weight. The user has to go CHECK
            // whether the effect landed; giving either outcome the primary
            // treatment nudges them toward guessing instead of looking.
            '<button class="eos-btn-sm eos-btn-ghost" onclick="resolveUnknown(\'' + escAttr(action.id) + '\', \'failed\')" title="It did not take effect">Mark failed</button>' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="resolveUnknown(\'' + escAttr(action.id) + '\', \'applied\')" title="It did take effect">Mark done</button>';
    } else if (st === 'applied') {
        var resultPreview = action.result ? '<div style="font-size:11px;color:var(--text-muted);max-height:60px;overflow:hidden">' + esc(String(action.result).slice(0, 200)) + '</div>' : '';
        buttons = '<span class="pending-status">applied ' + (action.resolved_ts ? new Date(action.resolved_ts).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '') + '</span>' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="undoLastAction(\'' + escAttr(action.app + '.' + action.method) + '\')" title="Undo the most recent reversible action (newest-first)">&#8634; Undo</button>';
    } else if (st === 'rejected') {
        buttons = '<span class="pending-status">rejected</span>';
    } else {
        buttons = '<span class="pending-status">failed: ' + esc(action.error || 'error') + '</span>';
    }
    var linksHtml = (st === 'applied') ? _pendingLinksHtml(action.links) : '';
    var proposed = action.proposed_changes || [];
    var diffHtml = proposed.length ? proposed.map(_pendingDiffHtml).join('') : '';
    // When a sandboxed write is the actual payload, the raw args (path +
    // full file content) duplicate the diff and make the card huge. Drop
    // the args block in that case — the diff IS the args, more legibly.
    var argsBlock = proposed.length ? '' :
        '<div class="pending-args">' + esc(argsStr) + '</div>';
    return '<div class="pending-card ' + escAttr(st) + '" id="pending-card-' + escAttr(action.id) + '">' +
        '<div class="pending-head">' +
            '<span>' + icon + '</span>' +
            '<span style="font-size:11px;color:var(--text-muted)">' + actorIcon + ' ' + esc(actorName) + ' proposes</span>' +
            '<span class="pending-verb">' + esc(action.app + '.' + action.method) + '</span>' +
            (action.edited ? '<span style="font-size:10px;color:var(--text-muted);border:1px solid var(--border);border-radius:8px;padding:0 6px">&#9998; edited</span>' : '') +
        '</div>' +
        argsBlock +
        diffHtml +
        '<div class="pending-actions">' + buttons + '</div>' +
        linksHtml +
        (st === 'applied' && action.result ? '<div style="font-size:11px;color:var(--text-muted);font-family:var(--font-mono,monospace);max-height:80px;overflow-y:auto">' + esc(String(action.result).slice(0, 300)) + '</div>' : '') +
        '</div>';
}

// Render one proposed-change diff panel inside a pending card. Uses
// EOS.noteActions for the path header so the user can jump to the file
// (where it exists) in one click.
function _pendingDiffHtml(change) {
    var path = change.path || '';
    var lines = change.diff_lines || [];
    var kindToClass = {add:'d-add', del:'d-del', ctx:'d-ctx', hunk:'d-hunk'};
    var rendered = lines.map(function(l) {
        var cls = kindToClass[l.kind] || 'd-ctx';
        return '<span class="' + cls + '">' + esc(l.text || '') + '</span>';
    }).join('');
    if (!rendered) {
        rendered = '<span class="d-ctx" style="color:var(--text-muted)">(no changes — proposed content matches current file)</span>';
    }
    var actions = (window.EOS && EOS.noteActions) ? EOS.noteActions(path) : esc(path);
    return '<div class="pending-diff-head"><span>' + esc(path) + '</span><span>' + actions + '</span></div>' +
        '<div class="pending-diff">' + rendered + '</div>';
}

// Render the post-apply link row for an action card. Delegates to the shared
// EOS_UI.actionLinks helper so this surface and page-assistant stay in sync.
function _pendingLinksHtml(links) {
    return (window.EOS_UI && EOS_UI.actionLinks) ? EOS_UI.actionLinks(links) : '';
}

// One-click undo — the north-star's "audit + undo" back half. The shared
// undo log is newest-first (emptyos.sdk.actions_log.undo_last), so the
// button always reverses the MOST RECENT reversible action; when that isn't
// the card it sits on, the toast says exactly what was undone instead.
async function undoLastAction(cardVerb) {
    try {
        var res = await EOS.post('/rooms/api/undo', {});
        if (!res || !res.ok) {
            EOS_UI.toast((res && (res.message || res.error)) || 'Nothing to undo', false);
            return;
        }
        var undid = res.undid || {};
        var undidVerb = (undid.app || '?') + '.' + (undid.method || '?');
        if (undidVerb === cardVerb) {
            EOS_UI.toast('Undone: ' + undidVerb);
        } else {
            EOS_UI.toast('Undo is newest-first — undid ' + undidVerb);
        }
    } catch (e) {
        EOS_UI.toast('Undo failed: ' + e, false);
    }
}

async function resolveUnknown(actionId, outcome) {
    // Adjudicate an interrupted action: the user checked whether the side
    // effect actually landed and records what they found. Never re-runs it.
    var verb = outcome === 'applied' ? 'done' : 'failed';
    // Object form. EOS_UI.confirm's SECOND positional arg is an onYes CALLBACK,
    // not a body string — passing text there gets it invoked as a function
    // (`if(onYes) onYes()`, no typeof guard), which throws before closeModal()
    // and leaves the dialog stuck open. The whole explanation goes in `message`.
    var ok = await EOS_UI.confirm({
        message: 'Mark this action as ' + verb + '? It was started but '
            + 'interrupted before the outcome was recorded, so it may or may '
            + 'not have taken effect. Check first — this only records what you '
            + 'found, it does NOT run the action again.',
        action: 'Mark ' + verb,
    });
    if (!ok) return;
    var safe = actionId.replace(/[^A-Za-z0-9_-]/g, '');
    document.querySelectorAll('#pending-card-' + safe).forEach(function(c){ c.style.opacity = '0.5'; });
    try {
        var res = await EOS.post('/rooms/api/pending/' + encodeURIComponent(actionId) + '/resolve',
                                 {outcome: outcome});
        if (res && res.id) _pendingMap[res.id] = res;
        document.querySelectorAll('[id="pending-card-' + actionId + '"]').forEach(function(c) {
            c.outerHTML = renderPendingCard(res);
        });
        if (res && res.error) EOS_UI.toast(res.error, false);
        else EOS_UI.toast('Recorded as ' + verb);
        updatePendingBadge();
    } catch(e) {
        EOS_UI.toast('Could not record outcome', false);
        document.querySelectorAll('#pending-card-' + safe).forEach(function(c){ c.style.opacity = ''; });
    }
}

async function applyAction(actionId) {
    var cards = document.querySelectorAll('#pending-card-' + actionId.replace(/[^A-Za-z0-9_-]/g, ''));
    cards.forEach(function(c){ c.style.opacity = '0.5'; });
    try {
        var res = await EOS.post('/rooms/api/pending/' + encodeURIComponent(actionId) + '/apply', {});
        if (res && res.id) _pendingMap[res.id] = res;
        // Replace all instances of this card on the page (inline thread + drawer + dashboard).
        document.querySelectorAll('[id="pending-card-' + actionId + '"]').forEach(function(c) {
            c.outerHTML = renderPendingCard(res);
        });
        if (res.status === 'applied') {
            EOS_UI.toast('Applied');
        } else if (res.status === 'failed') {
            EOS_UI.toast('Apply failed: ' + (res.error || 'see card'), false);
        } else if (res.error) {
            EOS_UI.toast(res.error, false);
        }
    } catch(e) {
        EOS_UI.toast('Apply failed', false);
    }
    updatePendingBadge();
    refreshGlobalPendingBadge();
}

// Edit-before-apply (AG-UI "modify params" borrow): swap the args block of
// the clicked card instance for a JSON textarea. Save POSTs the new args;
// the backend keeps args_original + sets edited=true for the audit trail.
function editAction(actionId, btn) {
    var a = _pendingMap[actionId];
    if (!a) { EOS_UI.toast('Action not loaded', false); return; }
    var card = (btn && btn.closest) ? btn.closest('.pending-card') : document.getElementById('pending-card-' + actionId);
    var argsEl = card ? card.querySelector('.pending-args') : null;
    if (!argsEl) return;
    var json = '';
    try { json = JSON.stringify(a.args || {}, null, 2); } catch(e) { json = '{}'; }
    argsEl.innerHTML =
        '<textarea style="width:100%;box-sizing:border-box;min-height:72px;font-family:var(--font-mono,monospace);font-size:11px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px">' + esc(json) + '</textarea>' +
        '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:4px">' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="cancelEditAction(\'' + escAttr(actionId) + '\')">Cancel</button>' +
            '<button class="eos-btn-sm" style="background:var(--accent);color:var(--accent-ink)" onclick="saveEditAction(\'' + escAttr(actionId) + '\', this)">Save</button>' +
        '</div>';
    var ta = argsEl.querySelector('textarea');
    if (ta) ta.focus();
}

async function saveEditAction(actionId, btn) {
    var card = (btn && btn.closest) ? btn.closest('.pending-card') : null;
    var ta = card ? card.querySelector('.pending-args textarea') : null;
    if (!ta) return;
    var args;
    try { args = JSON.parse(ta.value); } catch(e) { EOS_UI.toast('Invalid JSON', false); return; }
    if (!args || typeof args !== 'object' || Array.isArray(args)) {
        EOS_UI.toast('Args must be a JSON object', false);
        return;
    }
    try {
        var res = await EOS.post('/rooms/api/pending/' + encodeURIComponent(actionId) + '/edit', {args: args});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        if (res && res.id) _pendingMap[res.id] = res;
        document.querySelectorAll('[id="pending-card-' + actionId + '"]').forEach(function(c) {
            c.outerHTML = renderPendingCard(res);
        });
        EOS_UI.toast('Args updated');
    } catch(e) {
        EOS_UI.toast('Edit failed', false);
    }
}

function cancelEditAction(actionId) {
    var a = _pendingMap[actionId];
    if (!a) return;
    document.querySelectorAll('[id="pending-card-' + actionId + '"]').forEach(function(c) {
        c.outerHTML = renderPendingCard(a);
    });
}

async function rejectAction(actionId) {
    document.querySelectorAll('[id="pending-card-' + actionId + '"]').forEach(function(c){
        c.style.opacity = '0.5';
    });
    try {
        var res = await EOS.post('/rooms/api/pending/' + encodeURIComponent(actionId) + '/reject', {});
        if (res && res.id) _pendingMap[res.id] = res;
        document.querySelectorAll('[id="pending-card-' + actionId + '"]').forEach(function(c) {
            c.outerHTML = renderPendingCard(res);
        });
    } catch(e) {
        EOS_UI.toast('Reject failed', false);
    }
    updatePendingBadge();
    refreshGlobalPendingBadge();
}

