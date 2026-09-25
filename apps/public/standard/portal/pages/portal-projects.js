// portal-projects.js — chat projects + chat search in the chat-first sidebar
// (desktop GUI plan B2). Runs only when PortalChat.isOn() (the
// [apps.portal] feature.chat-first.enabled dark flag); otherwise its section
// stays hidden and the sidebar is unchanged.
//
// A project is an agent-app record — a name plus standing instructions every
// chat in it receives (apps/public/standard/agent/projects.py). The section
// shows a search box, the projects (each expandable to its chats) and Recent
// chats (chat sessions in no project). Chats come from portal-agent.js's
// allAgentSessions cache, so a chat started a moment ago appears at once.
// Search is the agent's own index: what the user typed and the assistant's
// replies, CJK substrings included.
//
// One namespaced global — PortalProjects — per .claude/rules/multi-module-apps.md
// § frontend counterpart. Loads before portal.js. The pure helpers at the top
// are exported for tests/js/portal_projects.test.mjs.
var PortalProjects = (function () {
    'use strict';

    // Recent chats shown before the list would outgrow the sidebar (the
    // agent-runs group shows 20; chats are the primary list here, so a little more).
    var RECENT_MAX = 25;
    // One search per pause in typing: each keystroke would be a request.
    var SEARCH_DEBOUNCE_MS = 250;
    var EXPAND_KEY = 'portal.projects.expanded.v1';
    var S = { on: false, projects: [], query: '', results: null, expanded: {}, pending: '', searchFocused: false };
    var _searchTimer = null;

    // ── Pure helpers (node-tested) ─────────────────────────────────────

    // Chat sessions → {byProject: {pid: [...]}, recent: [...]}. A chat whose
    // project no longer exists counts as recent, never as lost.
    function groupChats(sessions, projects, recentMax) {
        var known = {};
        (projects || []).forEach(function (p) { known[p.id] = true; });
        var byProject = {}, recent = [];
        (sessions || []).forEach(function (s) {
            if (s.profile !== 'chat') return;
            if (s.project_id && known[s.project_id]) (byProject[s.project_id] = byProject[s.project_id] || []).push(s);
            else recent.push(s);
        });
        return { byProject: byProject, recent: recent.slice(0, recentMax) };
    }

    // Portal folders not yet copied into a project (api_migrate_folders).
    function unmigrated(folders) {
        return (folders || []).filter(function (f) { return !f.chat_project_id; });
    }

    // ── State + rendering ──────────────────────────────────────────────

    function _esc(s) { return EOS_UI.esc(s == null ? '' : String(s)); }
    function _mount() { return document.getElementById('portal-projects-section'); }
    function _project(pid) { return S.projects.find(function (p) { return p.id === pid; }) || null; }
    function _sessions() { return typeof allAgentSessions !== 'undefined' ? allAgentSessions : []; }
    function _folders() { return typeof allFolders !== 'undefined' ? allFolders : []; }

    function pendingProject() { return S.pending; }
    function pendingName() { var p = S.pending && _project(S.pending); return p ? p.name : ''; }

    function _loadExpanded() {
        try { S.expanded = JSON.parse(localStorage.getItem(EXPAND_KEY) || '{}') || {}; } catch (e) { S.expanded = {}; }
    }
    function _saveExpanded() { try { localStorage.setItem(EXPAND_KEY, JSON.stringify(S.expanded)); } catch (e) {} }

    async function load() {
        if (!S.on) return;
        var pr = await EOS.apiSafe('/agent/api/projects');
        if (pr && !pr.error) S.projects = pr.projects || [];
        render();
    }

    function _chatRow(s) {
        var tid = 'agent:' + s.id;
        var active = typeof currentAgent !== 'undefined' && currentAgent && currentAgent.id === tid;
        return '<a class="portal-room' + (active ? ' active' : '') + '" href="#' + _esc(encodeURIComponent(tid)) + '"' +
            ' data-open="' + EOS_UI.escAttr(tid) + '" title="' + EOS_UI.escAttr(s.name || 'Chat') + '">' +
            '<span class="r-name">' + _esc(s.name || 'Chat') + '</span>' +
            '<button type="button" class="r-menu" data-chat-menu="' + EOS_UI.escAttr(s.id) + '" title="Move to project&hellip;" aria-label="Move to project">&#x22EF;</button>' +
            '</a>';
    }

    function render() {
        var el = _mount();
        if (!el) return;
        if (!S.on) { el.style.display = 'none'; return; }
        el.style.display = '';
        // Re-rendering replaces the search box; give focus back only if it
        // had it (the sidebar re-renders after every send, mid-conversation).
        var active = document.activeElement;
        S.searchFocused = !!(active && active.id === 'portal-projects-search');
        var html = '<input type="search" class="portal-projects-search" id="portal-projects-search" placeholder="Search chats&hellip;" aria-label="Search chats" value="' + EOS_UI.escAttr(S.query) + '">';
        if (S.results) {
            html += S.results.length
                ? S.results.map(function (h) {
                    var tid = 'agent:' + h.session_id;
                    return '<a class="portal-room portal-search-hit" href="#' + _esc(encodeURIComponent(tid)) + '" data-open="' + EOS_UI.escAttr(tid) + '">' +
                        '<span class="r-name">' + _esc(h.name || 'Chat') + '</span>' +
                        (h.snippet ? '<span class="portal-search-snippet">' + _esc(h.snippet) + '</span>' : '') + '</a>';
                }).join('')
                : '<div class="portal-empty">No chats mention that.</div>';
            el.innerHTML = html;
            _wire(el);
            return;
        }
        var g = groupChats(_sessions(), S.projects, RECENT_MAX);
        html += '<div class="portal-section-header"><h3>Projects</h3>' +
            '<button type="button" class="portal-section-add" data-new-project="1" title="New project" aria-label="New project">+</button></div>';
        var left = unmigrated(_folders());
        if (left.length) {
            html += '<button type="button" class="portal-folder-newthread" data-migrate="1">Copy ' + left.length +
                ' room' + (left.length === 1 ? '' : 's') + ' into projects&hellip;</button>';
        }
        if (!S.projects.length) {
            html += '<div class="portal-empty">No projects yet &mdash; a project gives every chat in it the same instructions.</div>';
        }
        S.projects.forEach(function (p) {
            var open = !!S.expanded[p.id];
            var chats = g.byProject[p.id] || [];
            // Same row markup as portal-sidebar.js's rooms, so the room CSS applies.
            html += '<div class="portal-folder' + (open ? ' open' : '') + '" data-toggle-project="' + EOS_UI.escAttr(p.id) + '" role="button" tabindex="0" aria-expanded="' + open + '">' +
                '<span class="chev">&#x25B6;</span>' +
                '<span class="f-name" title="' + EOS_UI.escAttr(p.name) + '">' + _esc(p.name) + '</span>' +
                '<span class="f-count">' + chats.length + '</span>' +
                '<button type="button" class="f-menu" data-project-menu="' + EOS_UI.escAttr(p.id) + '" title="Manage project" aria-label="Manage project">&#x22EF;</button>' +
                '</div>';
            if (open) {
                html += '<button type="button" class="portal-folder-newthread" data-new-chat-in="' + EOS_UI.escAttr(p.id) + '">+ New chat in this project</button>' +
                    (chats.length ? chats.map(_chatRow).join('') : '<div class="portal-empty" style="padding-left:22px">No chats yet.</div>');
            }
        });
        html += '<div class="portal-section-header"><h3>Recent chats</h3></div>' +
            (g.recent.length ? g.recent.map(_chatRow).join('') : '<div class="portal-empty">No chats yet.</div>');
        el.innerHTML = html;
        _wire(el);
    }

    // Delegated handlers — no inline on*="" attributes to escape.
    function _wire(el) {
        var box = el.querySelector('#portal-projects-search');
        if (box) {
            box.oninput = function () {
                S.query = box.value;
                clearTimeout(_searchTimer);
                _searchTimer = setTimeout(_runSearch, SEARCH_DEBOUNCE_MS);
            };
            if (S.searchFocused) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
        }
        el.onkeydown = function (ev) {
            var t = ev.target.closest('[data-toggle-project]');
            if (t && (ev.key === 'Enter' || ev.key === ' ') && ev.target === t) { ev.preventDefault(); _toggle(t.dataset.toggleProject); }
        };
        el.onclick = function (ev) {
            var t = ev.target.closest('[data-chat-menu],[data-project-menu],[data-new-project],[data-new-chat-in],[data-migrate],[data-toggle-project],[data-open]');
            if (!t || !el.contains(t)) return;
            ev.preventDefault();
            ev.stopPropagation();
            if (t.dataset.chatMenu) return _moveChatMenu(t.dataset.chatMenu);
            if (t.dataset.projectMenu) return _projectMenu(t.dataset.projectMenu);
            if (t.dataset.newProject) return _newProject();
            if (t.dataset.newChatIn) return newChatIn(t.dataset.newChatIn);
            if (t.dataset.migrate) return _migrate();
            if (t.dataset.toggleProject) return _toggle(t.dataset.toggleProject);
            if (t.dataset.open) { _navTo(t.dataset.open); if (typeof _maybeCloseMobileSidebar === 'function') _maybeCloseMobileSidebar(); }
        };
    }

    function _toggle(pid) {
        S.expanded[pid] = !S.expanded[pid];
        _saveExpanded();
        render();
    }

    async function _runSearch() {
        var q = S.query.trim();
        if (!q) { S.results = null; return render(); }
        var d = await EOS.apiSafe('/agent/api/search?profile=chat&q=' + encodeURIComponent(q));
        if (S.query.trim() !== q) return;  // a newer keystroke owns the list
        if (!d || d.error) { EOS.toast((d && d.error) || 'Search failed', false); return; }
        S.results = d.results || [];
        render();
    }

    function _newProject() {
        EOS_UI.formModal('New project', [
            { key: 'name', label: 'Name', placeholder: 'e.g. Thesis', required: true },
            { key: 'instructions', label: 'Instructions for every chat in it', type: 'textarea',
              placeholder: 'e.g. Answer in Chinese. Cite the note you used.' }
        ], async function (vals) {
            var p = await EOS.apiSafe('/agent/api/projects', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: vals.name || '', instructions: vals.instructions || '' })
            });
            if (!p || p.error) { EOS.toast((p && p.error) || 'Could not create the project', false); return; }
            S.expanded[p.id] = true;
            _saveExpanded();
            await load();
        });
    }

    function _projectMenu(pid) {
        var p = _project(pid);
        if (!p) return;
        EOS_UI.choiceMenu({
            title: p.name, width: '360px',
            rows: [
                { value: 'edit', label: 'Edit name & instructions…' },
                { value: 'delete', label: 'Delete project', sub: 'its chats stay, in Recent chats' }
            ],
            onPick: function (act) { if (act === 'edit') _projectSettings(p); else _deleteProject(p); }
        });
    }

    function _projectSettings(p) {
        EOS_UI.formModal('Project settings', [
            { key: 'name', label: 'Name', value: p.name, required: true },
            { key: 'instructions', label: 'Instructions for every chat in it', type: 'textarea', value: p.instructions || '' }
        ], async function (vals) {
            var r = await EOS.apiSafe('/agent/api/projects/' + encodeURIComponent(p.id), {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: vals.name || '', instructions: vals.instructions || '' })
            });
            if (!r || r.error) { EOS.toast((r && r.error) || 'Could not save', false); return; }
            await load();
        });
    }

    async function _deleteProject(p) {
        var ok = await EOS_UI.confirm({
            title: 'Delete project', danger: true, action: 'Delete',
            message: 'Delete "' + p.name + '"? Its chats stay, back in Recent chats.'
        });
        if (!ok) return;
        var r = await EOS.apiSafe('/agent/api/projects/' + encodeURIComponent(p.id), { method: 'DELETE' });
        if (!r || r.error) { EOS.toast((r && r.error) || 'Could not delete', false); return; }
        if (S.pending === p.id) clearPending();
        await Promise.all([loadAgentSessions(), load()]);   // its chats' project_id is now blank
        _renderSidebar();
    }

    function _moveChatMenu(sid) {
        var chat = _sessions().find(function (s) { return s.id === sid; }) || {};
        EOS_UI.choiceMenu({
            title: 'Move chat to…', width: '380px',
            rows: [{ id: '', name: 'No project (Recent chats)' }].concat(S.projects).map(function (p) {
                return { value: p.id, label: p.name, current: (chat.project_id || '') === p.id };
            }),
            onPick: async function (pid) {
                var r = await EOS.apiSafe('/agent/api/sessions/' + encodeURIComponent(sid), {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ project_id: pid })
                });
                if (!r || r.error) { EOS.toast((r && r.error) || 'Could not move', false); return; }
                chat.project_id = pid;
                render();
            }
        });
    }

    // Portal "rooms" (folders) → projects: the server copies each folder's
    // name + instructions and moves its agent chats; Rooms threads stay put.
    async function _migrate() {
        var n = unmigrated(_folders()).length;
        var ok = await EOS_UI.confirm({
            title: 'Copy rooms into projects', action: 'Copy',
            message: 'Make a project from each of your ' + n + ' room' + (n === 1 ? '' : 's') +
                ' (same name, same instructions), and move its agent chats in — a chat you already put ' +
                'in a project stays there. Rooms and their threads stay as they are, and a room\'s model ' +
                'choice is not copied (each chat picks its own). The rooms list is backed up first; the ' +
                'new projects can be deleted from their menus.'
        });
        if (!ok) return;
        var r = await EOS.apiSafe('/portal/api/folders/migrate', { method: 'POST' });
        if (!r || r.error) { EOS.toast((r && r.error) || 'Copy failed', false); return; }
        var made = (r.migrated || []).length;
        var failed = r.failed || [];
        EOS.toast('Copied ' + made + ' room' + (made === 1 ? '' : 's') + ' into projects' +
            (failed.length ? ' — not copied: ' + failed.map(function (f) { return '"' + f.name + '" (' + f.error + ')'; }).join(', ') : ''),
            !failed.length);
        await Promise.all([loadFolders(), loadAgentSessions(), load()]);
        _renderSidebar();
    }

    // "+ New chat in this project": the next hero send starts a chat in it.
    function newChatIn(pid) {
        _pendingFolderId = null;       // a project and a room never both claim the next send
        S.pending = pid;
        if (location.hash) { history.pushState(null, '', location.pathname); _onHashChange(); } else { _showHero(); }
        PortalChat.selectBackend('chat');
        _updateFolderContextPill();    // shows "Creating in <project>"
        var hi = document.getElementById('hero-input');
        if (hi) setTimeout(function () { hi.focus(); }, 30);
    }

    function clearPending() {
        if (!S.pending) return;
        S.pending = '';
        _updateFolderContextPill();
    }

    function init() {
        if (typeof PortalChat === 'undefined' || !PortalChat.isOn()) return;
        S.on = true;
        _loadExpanded();
        // The sidebar's shortcut to the PARA projects app, renamed so it does
        // not read as these chat projects.
        var link = document.getElementById('side-link-projects');
        if (link && link.lastChild && link.lastChild.nodeType === 3) link.lastChild.textContent = 'Project tracker';
        load();
    }

    return {
        init: init,
        load: load,
        render: render,
        pendingProject: pendingProject,
        pendingName: pendingName,
        clearPending: clearPending,
        newChatIn: newChatIn,
        // pure — exported for tests/js/portal_projects.test.mjs
        groupChats: groupChats,
        unmigrated: unmigrated
    };
})();
