// portal-sidebar.js — split out of portal.js (B0, 2026-09-12). Owns the sidebar: folders (rooms), thread lists per backend, pins, row menus, and the hero's folder-context pill.
//
// Loads BEFORE portal.js, in the same global scope (not a module), so the
// page's markup handlers and portal.js's boot code resolve these names
// unchanged. Top-level names must stay unique across portal.js and every
// portal-*.js — tests/test_unit_page_script_globals.py fails on a
// collision, because the script that parses last would silently win.

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
        // Chat-first: "+ New chat in this project" claims the next send the
        // same way (portal-projects.js), and reads the same way.
        var project = typeof PortalProjects !== 'undefined' ? PortalProjects.pendingName() : '';
        if (pill) pill.style.display = project ? '' : 'none';
        if (project && nameEl) nameEl.textContent = project;
        if (chips) chips.style.display = project ? 'none' : '';
    }
    // Chat-first: a folder thread starts in Rooms, so the chat model pill hides.
    if (typeof PortalChat !== 'undefined') PortalChat.afterSync();
}

function _clearFolderContext() {
    _pendingFolderId = null;
    if (typeof PortalProjects !== 'undefined') PortalProjects.clearPending();
    _updateFolderContextPill();
    var input = document.getElementById('hero-input');
    if (input) input.focus();
}

function _newThreadInFolder(fid) {
    if (typeof PortalProjects !== 'undefined') PortalProjects.clearPending();
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

// ── Sidebar — threads list ──────────────────────────────────────────

// ── Folders (= "Rooms" in UI) ───────────────────────────────────────
//
// Folders are portal's organisational layer over apps/rooms/ agents.
// A folder is `{id, name, default_mode, thread_ids: [...]}`. Threads can
// belong to at most one folder; threads not in any folder are in the
// Recent list (portal-recent.js). Expand state is per-browser
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
        el.innerHTML = EOS_UI.errorState({message: 'Could not load threads.', onRetry: 'loadAgents()'});
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
        var probe = bk === 'chat' ? 'agent' : bk;   // chat-first's Chat runs on agent sessions
        if (!(probe in _bkErrors)) return;   // code / more have no list probe
        var bad = !!_bkErrors[probe];
        b.classList.toggle('degraded', bad);
        b.title = bad
            ? ((bk === 'chat' ? 'Chat' : CONV_BACKENDS[bk].label) + ' unreachable — its session list failed to load')
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
// opts.recent: {glyph, kindLabel, time} — the Recent list's kind glyph and
// relative time (portal-recent.js); absent for nested folder rows.
function _rowMeta(opts) {
    var r = opts && opts.recent;
    if (!r) return { ico: '', time: '' };
    return {
        ico: '<span class="r-ico" role="img" title="' + escAttr(r.kindLabel) + '" aria-label="' + escAttr(r.kindLabel) + '">' + r.glyph + '</span>',
        time: r.time ? '<span class="r-time">' + esc(r.time) + '</span>' : '',
    };
}

function _renderThreadRow(thread, opts) {
    opts = opts || {};
    var meta = _rowMeta(opts);
    var name = esc(thread.name || thread.id);
    var currentId = currentAgent ? currentAgent.id : null;
    var active = (thread.id === currentId) ? ' active' : '';
    var nested = opts.nested ? ' nested' : '';
    var pinned = _isPinned(thread.id);
    var pinClass = pinned ? 'r-pin pinned' : 'r-pin';
    var pinIcon = pinned ? '&#x2605;' : '&#x2606;';  // ★ filled / ☆ outline
    return '<a class="portal-room' + nested + active + '"' +
        ' href="#' + encodeURIComponent(thread.id) + '"' +
        ' onclick="event.preventDefault();_navTo(' + EOS_UI.jsArg(thread.id) + ');"' +
        ' title="' + name + '">' +
        meta.ico + '<span class="r-name">' + name + '</span>' + meta.time +
        '<button class="' + pinClass + '" onclick="event.preventDefault();event.stopPropagation();_togglePin(' + EOS_UI.jsArg(thread.id) + ')" title="' + (pinned ? 'Unpin' : 'Pin') + '">' + pinIcon + '</button>' +
        '<button class="r-menu" onclick="event.preventDefault();event.stopPropagation();_threadMenu(' + EOS_UI.jsArg(thread.id) + ', event)" title="Move&hellip;">&#x22EF;</button>' +
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
    var meta = _rowMeta(opts);
    var nm = esc(name || tid);
    var active = (currentAgent && currentAgent.id === tid) ? ' active' : '';
    var pinned = _isPinned(tid);
    var pinClass = pinned ? 'r-pin pinned' : 'r-pin';
    var pinIcon = pinned ? '&#x2605;' : '&#x2606;';
    return '<a class="portal-room' + active + '"' +
        ' href="#' + encodeURIComponent(tid) + '"' +
        ' onclick="event.preventDefault();_navTo(' + EOS_UI.jsArg(tid) + ');"' +
        ' title="' + nm + '">' +
        meta.ico + '<span class="r-name">' + nm + '</span>' + meta.time +
        '<button class="' + pinClass + '" onclick="event.preventDefault();event.stopPropagation();_togglePin(' + EOS_UI.jsArg(tid) + ')" title="' + (pinned ? 'Unpin' : 'Pin') + '">' + pinIcon + '</button>' +
        '<button class="r-menu" onclick="event.preventDefault();event.stopPropagation();_extThreadMenu(' + EOS_UI.jsArg(tid) + ')" title="Rename / delete&hellip;">&#x22EF;</button>' +
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
        '<button style="' + rowBtn + ';color:var(--text)" onclick="_renameExtThread(' + EOS_UI.jsArg(tid) + ')">Rename&hellip;</button>' +
        '<button style="' + rowBtn + ';color:var(--danger)" onclick="_deleteExtThread(' + EOS_UI.jsArg(tid) + ')">Delete</button>' +
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

    // ── Folders (Rooms) — the heading only when rooms exist; the "New room"
    // button beside "+ New thread" works whether or not any do.
    if (allFolders.length) {
        parts.push('<div class="portal-group-head">Rooms</div>');
        allFolders.forEach(function(f) {
            var isOpen = !!folderExpanded[f.id];
            var threads = (f.thread_ids || [])
                .map(_agentById)
                .filter(Boolean);
            parts.push(
                '<div class="portal-folder' + (isOpen ? ' open' : '') + '"' +
                ' onclick="_toggleFolder(' + EOS_UI.jsArg(f.id) + ')">' +
                '<span class="chev">&#x25B6;</span>' +
                '<span class="f-name" title="' + escAttr(f.name) + '">' + esc(f.name) + '</span>' +
                '<span class="f-count">' + threads.length + '</span>' +
                '<button class="f-menu" onclick="event.stopPropagation();_folderMenu(' + EOS_UI.jsArg(f.id) + ', event)" title="Manage room">&#x22EF;</button>' +
                '</div>'
            );
            if (isOpen) {
                // "+ New thread in this room" — always shown so an empty folder
                // is immediately useful, not just a dead label.
                parts.push(
                    '<button class="portal-folder-newthread" onclick="_newThreadInFolder(' + EOS_UI.jsArg(f.id) + ')">+ New thread in this room</button>'
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

    // ── Recent: every backend in one list, newest activity first ──
    // (portal-recent.js). Threads filed in a room folder are listed there, and
    // chat-first lists its chat sessions under its own Recent chats.
    var chatFirst = typeof PortalChat !== 'undefined' && PortalChat.isOn();
    var excluded = {};
    Object.keys(grouped).forEach(function(tid) { excluded[tid] = 1; });
    if (chatFirst) allAgentSessions.forEach(function(s) { if (s.profile === 'chat') excluded['agent:' + s.id] = 1; });
    var keep = {};
    _pinsCache.forEach(function(tid) { keep[tid] = 1; });
    if (currentId) keep[currentId] = 1;
    var recent = PortalRecent.items(allAgents, allAsstSessions, allAgentSessions, { excluded: excluded, keep: keep });
    var now = Date.now();
    var g = PortalRecent.group(recent, now, _recentAll ? 0 : PortalRecent.LIMIT);
    g.groups.forEach(function(grp) {
        parts.push('<div class="portal-group-head">' + esc(grp.label) + '</div>');
        grp.rows.forEach(function(x) {
            var opts = { recent: { glyph: PortalRecent.GLYPH[x.kind], kindLabel: PortalRecent.KIND_LABEL[x.kind],
                                   time: PortalRecent.rel(x.ts, now) } };
            var room = x.kind === 'rooms' ? _agentById(x.tid) : null;
            var html = room ? _renderThreadRow(room, opts) : _renderExtRow(x.tid, x.name, opts);
            // data-ts: the activity time the row was ordered by (tests read it).
            parts.push(html.replace('<a class="portal-room', '<a data-ts="' + x.ts + '" class="portal-room'));
        });
    });
    if (g.hidden > 0) {
        parts.push('<button type="button" class="portal-show-more" onclick="_showAllRecent()">Show ' +
            g.hidden + ' more</button>');
    }
    if (!recent.length && !allFolders.length) {
        parts.push('<div class="portal-empty">No conversations yet.</div>');
    }

    el.innerHTML = parts.join('');
    if (typeof PortalProjects !== 'undefined') PortalProjects.render();

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

// Recent list expanded past its limit (not persisted — a fresh load is short).
var _recentAll = false;
function _showAllRecent() { _recentAll = true; _renderSidebar(); }

// Every conversation, every backend, every room folder, newest activity first —
// what search and the home Continue strip read. Empty sessions stay hidden
// unless pinned or open, as in the list.
function _allConversations() {
    var keep = {};
    _pinsCache.forEach(function(tid) { keep[tid] = 1; });
    if (currentAgent) keep[currentAgent.id] = 1;
    return PortalRecent.items(allAgents, allAsstSessions, allAgentSessions, { keep: keep });
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
        '<button style="' + rowBtn + ';color:var(--text)" onclick="_editFolderInstructions(' + EOS_UI.jsArg(fid) + ')">Edit room instructions&hellip;</button>' +
        '<button style="' + rowBtn + ';color:var(--text)" onclick="_renameFolder(' + EOS_UI.jsArg(fid) + ')">Rename room&hellip;</button>' +
        '<button style="' + rowBtn + ';color:var(--danger,#da3633)" onclick="_deleteFolder(' + EOS_UI.jsArg(fid) + ')">Delete room</button>' +
        '<div style="padding:8px 12px;font-size:11px;color:var(--text-muted);border-top:1px solid var(--border);margin-top:4px">Deleting a room releases its threads back to Recent. Conversation history is unaffected.</div>' +
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
            '<button class="eos-btn-sm" style="background:var(--accent);color: var(--accent-ink, #fff);border:0;padding:6px 14px;border-radius:6px;font-family:inherit;font-size:13px;cursor:pointer" onclick="_saveFolderInstructions(' + EOS_UI.jsArg(fid) + ')">Save</button>' +
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
        message: 'Delete room "' + folder.name + '"? Its threads go back to Recent.',
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
               ' onclick="_moveThread(' + EOS_UI.jsArg(threadId) + ',' + EOS_UI.jsArg(f.id) + ')">' +
               prefix + esc(f.name) + '</button>';
    }).join('');
    var removeRow = currentFolder
        ? '<button class="jump-day-item" style="display:flex;justify-content:flex-start;padding:9px 12px;border-radius:6px;background:none;border:none;color:var(--text-secondary);font-family:inherit;font-size:13px;cursor:pointer;text-align:left;gap:8px"' +
          ' onclick="_detachThread(' + EOS_UI.jsArg(threadId) + ',' + EOS_UI.jsArg(currentFolder.id) + ')">&nbsp;&nbsp;&nbsp;&nbsp;Remove from &ldquo;' + esc(currentFolder.name) + '&rdquo;</button>'
        : '';
    var newRoomRow = '<button class="jump-day-item" style="display:flex;justify-content:flex-start;padding:9px 12px;border-radius:6px;background:none;border:none;color:var(--accent);font-family:inherit;font-size:13px;cursor:pointer;text-align:left;gap:8px"' +
        ' onclick="_moveThreadToNewRoom(' + EOS_UI.jsArg(threadId) + ')">+ New room&hellip;</button>';
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
