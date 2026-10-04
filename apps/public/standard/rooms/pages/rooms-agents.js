// rooms-agents.js — agent CRUD modal, group room modal, members chip strip.
// Owns: openModal, editCurrentAgent, openGroupModal, onGroupTitleInput,
// acceptSuggestion, submitGroupRoom, agentNameById, participantVisual,
// renderMembers, saveAgent, deleteAgent, loadAgents.

// --- Agent CRUD ---

function openModal(editAgent) {
    var isEdit = !!editAgent;
    document.getElementById('modal-title').textContent = isEdit ? 'Edit Agent' : 'New Agent';
    document.getElementById('f-name').value = isEdit ? editAgent.name : '';
    document.getElementById('f-tier').value = isEdit ? (editAgent.tier || 'user') : 'user';
    document.getElementById('f-prompt').value = isEdit ? editAgent.system_prompt : '';
    document.getElementById('f-files').value = isEdit ? (editAgent.knowledge_files || []).join(', ') : '';
    document.getElementById('f-kdir').value = isEdit ? (editAgent.knowledge_dir || '') : '';
    document.getElementById('f-model').value = isEdit ? (editAgent.model || '') : '';
    document.getElementById('f-tools').value = isEdit ? (editAgent.tools || []).join(', ') : '';
    document.getElementById('f-temp').value = isEdit && editAgent.temperature != null ? editAgent.temperature : '';
    document.getElementById('f-effort').value = isEdit ? (editAgent.effort || '') : '';
    document.getElementById('f-id').value = isEdit ? editAgent.id : '';
    document.getElementById('f-builtin').value = isEdit ? (editAgent.builtin ? '1' : '') : '';
    // Show delete button only for non-builtin agents
    var showDelete = isEdit && !editAgent.builtin;
    document.getElementById('modal-delete').style.display = showDelete ? '' : 'none';
    EOS_UI.openModal('agent-modal');
    setTimeout(function() { document.getElementById('f-name').focus(); }, 200);
}

function editCurrentAgent() {
    if (currentAgent) openModal(currentAgent);
}

// --- Group rooms (Phase 2) ---

// Pick ≥2 existing agents + give the room a title. Posts to /rooms/api/rooms,
// then reloads the sidebar. Skips already-group rooms in the picker so we
// can't nest a group inside a group.
//
// Layout: CLI tools section first (sticky / above the fold), then agents in
// a 2-column dense grid. Avatar-first row, checkbox in top-right corner.
// Clicking anywhere on a row toggles the checkbox.
function openGroupModal() {
    var pickable = (agents || []).filter(function(a) {
        return (a.tier || 'user') !== 'group';
    });
    if (pickable.length < 2) {
        EOS_UI.toast('Need at least 2 participants to make a group', false);
        return;
    }
    var agentRows = pickable.map(function(a) {
        var color = agentColor(a.name);
        var preview = (a.system_prompt || '').replace(/\s+/g,' ').slice(0, 60);
        return '<label class="roster-pick" data-pick="agent" data-id="' + escAttr(a.id) + '">' +
            '<input type="checkbox" data-kind="agent" value="' + escAttr(a.id) + '" onchange="this.closest(\'.roster-pick\').classList.toggle(\'checked\', this.checked)">' +
            '<div class="roster-avatar" style="background:' + color + '">' + initial(a.name) + '</div>' +
            '<div class="roster-meta">' +
                '<div class="roster-name">' + esc(a.name) + '</div>' +
                '<div class="roster-sub">' + esc(preview || 'No prompt set') + '</div>' +
            '</div></label>';
    }).join('');
    // CLI tools section — distinct from agents. Claude has streaming + tool
    // events + model/effort knobs; codex/gemini run in buffered text mode
    // and are configured via emptyos.toml [plugins.agent-runtime.clis.<id>].
    var simpleCliRow = function(id, label, sub, icon) {
        return '<div class="roster-pick" data-pick="cli" data-id="' + escAttr(id) + '" id="roster-cli-' + escAttr(id) + '">' +
            '<label style="display:flex;align-items:center;gap:10px;cursor:pointer;width:100%">' +
                '<input type="checkbox" data-kind="cli" value="' + escAttr(id) + '" onchange="document.getElementById(\'roster-cli-' + escAttr(id) + '\').classList.toggle(\'checked\', this.checked)">' +
                '<div class="roster-avatar" style="background:var(--text-muted)">' + esc(icon) + '</div>' +
                '<div class="roster-meta">' +
                    '<div class="roster-name">' + esc(label) + '</div>' +
                    '<div class="roster-sub">' + esc(sub) + '</div>' +
                '</div>' +
            '</label>' +
        '</div>';
    };
    var cliRow =
        '<div class="roster-pick" data-pick="cli" data-id="claude-cli" id="roster-cli-claude-cli" style="flex-direction:column;align-items:stretch;gap:0">' +
            '<label style="display:flex;align-items:center;gap:10px;cursor:pointer;width:100%">' +
                '<input type="checkbox" data-kind="cli" value="claude-cli" onchange="document.getElementById(\'roster-cli-claude-cli\').classList.toggle(\'checked\', this.checked)">' +
                '<div class="roster-avatar" style="background:var(--accent)">⚡</div>' +
                '<div class="roster-meta">' +
                    '<div class="roster-name">Claude Code CLI</div>' +
                    '<div class="roster-sub">Streaming · tool events · review gate</div>' +
                '</div>' +
            '</label>' +
            '<div class="roster-cli-config">' +
                '<select id="cli-claude-model">' +
                    '<option value="">Model: default</option>' +
                    '<option value="haiku">haiku (fastest)</option>' +
                    '<option value="sonnet">sonnet</option>' +
                    '<option value="opus">opus (slowest)</option>' +
                '</select>' +
                '<select id="cli-claude-effort">' +
                    '<option value="">Effort: default</option>' +
                    '<option value="low">low (fastest)</option>' +
                    '<option value="medium">medium</option>' +
                    '<option value="high">high</option>' +
                    '<option value="max">max (slowest)</option>' +
                '</select>' +
            '</div>' +
        '</div>' +
        simpleCliRow('codex', 'Codex (OpenAI)', 'Buffered text · configure binary in emptyos.toml', '○') +
        simpleCliRow('gemini', 'Gemini (Google)', 'Buffered text · configure binary in emptyos.toml', '◇') +
        simpleCliRow('aider', 'Aider', 'Repo-aware coding · needs git cwd · configure in emptyos.toml', '▣') +
        simpleCliRow('sgpt', 'shell-gpt', 'Buffered text · configure binary in emptyos.toml', '▢') +
        simpleCliRow('mods', 'mods (Charm)', 'Buffered text · pipe-friendly · configure in emptyos.toml', '◈') +
        simpleCliRow('aichat', 'aichat', 'Buffered text · configure binary in emptyos.toml', '◊') +
        simpleCliRow('goose', 'goose (Block)', 'Buffered text · configure binary in emptyos.toml', '◉') +
        simpleCliRow('opencode', 'opencode (sst)', 'Buffered text · configure binary in emptyos.toml', '◐') +
        simpleCliRow('pi', 'Pi (Earendil)', 'Coding agent · native --append-system-prompt · configure in emptyos.toml', '▲');
    var body =
        '<div class="eos-form-group">' +
            '<label class="eos-form-label">Room title</label>' +
            '<input id="group-title" class="eos-form-input" type="text" placeholder="e.g. Brainstorm with Curator + Reviewer" autofocus oninput="onGroupTitleInput(this.value)">' +
            '<div id="group-suggestions" style="display:none;margin-top:8px;font-size:12px"></div>' +
        '</div>' +
        '<div class="roster-section">' +
            '<div class="roster-label">CLI tools <span class="count-pill">9</span></div>' +
            '<div class="roster-list">' + cliRow + '</div>' +
        '</div>' +
        '<div class="roster-section">' +
            '<div class="roster-label">Agents <span class="count-pill">' + pickable.length + '</span></div>' +
            '<div class="roster-list dense" id="group-pick-list">' + agentRows + '</div>' +
        '</div>' +
        '<div class="group-modal-bar">' +
            '<div class="group-modal-status" id="group-modal-status">Select at least 2 participants and enter a title.</div>' +
            '<button class="eos-btn eos-btn-primary" onclick="submitGroupRoom()">Create room</button>' +
        '</div>';
    // The group-pick-list id is reused by submitGroupRoom — we still want the
    // CLI row's checkbox in the same query, so put both lists under that id.
    body = body.replace(
        'id="group-pick-list">',
        'id="group-pick-list" data-section="agents">',
    );
    EOS_UI.modal({title: 'New Group', body: body, width: '600px'});
    // Live status bar — refresh whenever the user toggles a checkbox or edits
    // the title. Event delegation off the CURRENT modal so we catch dynamic
    // CLI rows AND don't bind onto a stale `.eos-modal` element.
    var modalEl = _currentModal();
    if (modalEl && modalEl !== document) {
        modalEl.addEventListener('change', function(ev) {
            if (ev.target && ev.target.matches('input[type=checkbox][data-kind]')) {
                updateGroupModalStatus();
            }
        });
        modalEl.addEventListener('input', function(ev) {
            if (ev.target && ev.target.id === 'group-title') updateGroupModalStatus();
        });
    }
    updateGroupModalStatus();
}

// Phase 27 — suggest agents based on the room title the user is typing.
var _suggestTimer = null;
function onGroupTitleInput(v) {
    if (_suggestTimer) clearTimeout(_suggestTimer);
    var q = (v || '').trim();
    var box = document.getElementById('group-suggestions');
    if (!box) return;
    if (q.length < 3) { box.style.display = 'none'; box.innerHTML = ''; return; }
    _suggestTimer = setTimeout(async function() {
        var hits = [];
        try {
            hits = await EOS.api('/rooms/api/suggest-agents?q=' +
                encodeURIComponent(q) + '&limit=3') || [];
        } catch (e) { hits = []; }
        if (!hits.length) { box.style.display = 'none'; box.innerHTML = ''; return; }
        box.innerHTML =
            '<div style="color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Suggested for this title</div>' +
            '<div style="display:flex;flex-wrap:wrap;gap:6px">' +
            hits.map(function(h) {
                var color = agentColor(h.name);
                return '<button class="eos-btn-sm" style="display:inline-flex;align-items:center;gap:6px;background:var(--bg-surface);border:1px solid var(--border);border-radius:14px;padding:4px 10px;font-size:12px;cursor:pointer" onclick="acceptSuggestion(\'' + escAttr(h.id) + '\')">' +
                    '<span class="member-avatar" style="background:' + color + ';width:18px;height:18px;font-size:9px">' + esc(initial(h.name)) + '</span>' +
                    '<span>' + esc(h.name) + '</span>' +
                    '<span style="color:var(--accent);font-weight:600">+</span>' +
                    '</button>';
            }).join('') +
            '</div>';
        box.style.display = 'block';
    }, 250);
}

function acceptSuggestion(agentId) {
    // Find the agent's checkbox in the roster and tick it.
    var cb = document.querySelector('.roster-pick[data-pick="agent"][data-id="' + agentId + '"] input[type=checkbox]');
    if (cb) {
        cb.checked = true;
        cb.dispatchEvent(new Event('change', {bubbles: true}));
        // Scroll into view so user sees confirmation.
        cb.closest('.roster-pick').scrollIntoView({behavior: 'smooth', block: 'center'});
    } else {
        EOS_UI.toast('Agent not in picker', false);
    }
}

function _groupStatus(msg, isError) {
    var el = document.getElementById('group-modal-status');
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('err', !!isError);
}

// Scope queries to the CURRENT modal (the one EOS_UI.modal() just opened),
// not any `.eos-modal` in the DOM — stale legacy `.eos-modal-bg` overlays
// or unrelated modal class collisions otherwise hijack the picked-count.
function _currentModal() {
    var ov = document.getElementById('eos-modal-overlay');
    if (ov) return ov.querySelector('.eos-modal') || ov;
    // Fallback: most-recently-added visible .eos-modal.
    var modals = Array.prototype.slice.call(document.querySelectorAll('.eos-modal'))
        .filter(function(m) { return m.offsetParent !== null; });
    return modals[modals.length - 1] || document;
}

function _groupPickedCount() {
    return _currentModal().querySelectorAll('input[type=checkbox][data-kind]:checked').length;
}

// Refresh the inline status line whenever the user toggles a checkbox or
// edits the title. Bound via event delegation in openGroupModal.
function updateGroupModalStatus() {
    var titleEl = document.getElementById('group-title');
    if (!titleEl) return;
    var title = (titleEl.value || '').trim();
    var picked = _groupPickedCount();
    var missing = [];
    if (!title) missing.push('a title');
    if (picked < 2) missing.push((picked === 0 ? 'at least 2 participants' : (2 - picked) + ' more participant' + (2 - picked === 1 ? '' : 's')));
    if (missing.length === 0) {
        _groupStatus('Ready · ' + picked + ' picked', false);
    } else {
        _groupStatus('Add ' + missing.join(' and '), false);
    }
}

async function submitGroupRoom() {
    var titleEl = document.getElementById('group-title');
    var title = (titleEl.value || '').trim();
    if (!title) {
        _groupStatus('Title is required', true);
        EOS_UI.toast('Title is required', false);
        try { titleEl.focus(); titleEl.scrollIntoView({behavior:'smooth', block:'center'}); } catch(e) {}
        return;
    }
    // Both the CLI tools section and the agents grid carry data-kind on
    // their checkboxes. Scope to the CURRENT modal (not any `.eos-modal`
    // in the DOM) so stale modal residue doesn't tank the count.
    var picked = Array.prototype.slice.call(
        _currentModal().querySelectorAll('input[type=checkbox][data-kind]:checked')
    ).map(function(el) {
        var kind = el.getAttribute('data-kind') || 'agent';
        var entry = {type: kind, id: el.value};
        if (kind === 'cli' && el.value === 'claude-cli') {
            var m = document.getElementById('cli-claude-model');
            var e = document.getElementById('cli-claude-effort');
            if (m && m.value) entry.model = m.value;
            if (e && e.value) entry.effort = e.value;
        }
        return entry;
    });
    if (picked.length < 2) {
        var need = 2 - picked.length;
        var msg = 'Pick ' + need + ' more participant' + (need === 1 ? '' : 's') + ' (need at least 2)';
        _groupStatus(msg, true);
        EOS_UI.toast(msg, false);
        try {
            var firstRoster = document.querySelector('.roster-section');
            if (firstRoster) firstRoster.scrollIntoView({behavior:'smooth', block:'start'});
        } catch(e) {}
        return;
    }
    try {
        var room = await EOS.post('/rooms/api/rooms', {title: title, participants: picked});
        if (room && room.error) {
            _groupStatus(room.error, true);
            EOS_UI.toast(room.error, false);
            return;
        }
        EOS_UI.closeModal();
        EOS_UI.toast('Group created');
        await loadAgents();
        if (room && room.id) openChat(room.id);
    } catch(e) {
        var err = 'Failed to create group';
        _groupStatus(err, true);
        EOS_UI.toast(err, false);
    }
}

// Look up display name for a participant id (agent or cli). Falls back to id.
function agentNameById(id) {
    if (id === 'claude-cli') return 'Claude Code CLI';
    if (id === 'codex') return 'Codex';
    if (id === 'gemini') return 'Gemini';
    if (id === 'aider') return 'Aider';
    if (id === 'sgpt') return 'shell-gpt';
    if (id === 'mods') return 'mods';
    if (id === 'aichat') return 'aichat';
    if (id === 'goose') return 'goose';
    if (id === 'opencode') return 'opencode';
    if (id === 'pi') return 'Pi';
    var a = (agents || []).find(function(x) { return x.id === id; });
    return a ? a.name : id;
}

// Visual descriptor for a participant — used in member chips and assistant
// bubbles. Returns {label, icon, color, type}.
function participantVisual(p) {
    var type = p.type || 'agent';
    var id = p.id || '';
    if (type === 'user') {
        return {label: id || 'me', icon: '👤', color: 'var(--text-muted)', type: type, id: id || 'me'};
    }
    if (type === 'cli') {
        return {label: agentNameById(id), icon: '⚡', color: 'var(--accent)', type: type, id: id};
    }
    var name = agentNameById(id);
    return {label: name, icon: initial(name), color: agentColor(name), type: type, id: id};
}

// Render the per-room member chip strip. Agents/CLI chips are clickable (insert
// @mention). The user chip is shown but non-clickable.
function renderMembers() {
    var bar = document.getElementById('chat-members');
    if (!bar) return;
    if (!currentAgent) { bar.style.display = 'none'; bar.innerHTML = ''; return; }
    var parts = currentAgent.participants;
    if (!parts) {
        // 1:1 (legacy) — synthesise so the UI still shows you + the agent.
        parts = [{type:'user', id:'me'}, {type:'agent', id: currentAgent.id}];
    }
    // Show only when there are 3+ entries (user + ≥2 responders) — i.e. group.
    var responderCount = parts.filter(function(p){
        return p.type === 'agent' || p.type === 'cli';
    }).length;
    if (responderCount < 2) { bar.style.display = 'none'; return; }
    bar.innerHTML = '<span class="chat-members-label">in this room</span>' +
        parts.map(function(p) {
            var v = participantVisual(p);
            var isSelf = v.type === 'user';
            var cls = isSelf ? 'member-chip is-self' : 'member-chip clickable';
            var click = isSelf ? '' : ' onclick="insertMention(\'' + escAttr(v.id) + '\')"';
            return '<span class="' + cls + '"' + click + ' title="' + (isSelf ? 'You' : 'Click to @mention') + '">' +
                '<span class="member-avatar" style="background:' + v.color + '">' + esc(v.icon) + '</span>' +
                '<span>' + esc(v.label) + '</span>' +
                (isSelf ? '' : '<span class="member-id">@' + esc(v.id) + '</span>') +
                '</span>';
        }).join('');
    bar.style.display = '';
}


// --- Save / delete / load agents ---
async function saveAgent() {
    var name = document.getElementById('f-name').value.trim();
    var prompt = document.getElementById('f-prompt').value.trim();
    var filesRaw = document.getElementById('f-files').value.trim();
    var kdir = document.getElementById('f-kdir').value.trim();
    var model = document.getElementById('f-model').value.trim();
    var toolsRaw = document.getElementById('f-tools').value.trim();
    var tempVal = document.getElementById('f-temp').value;
    var effortVal = document.getElementById('f-effort').value;
    var tier = document.getElementById('f-tier').value;
    var editId = document.getElementById('f-id').value;
    if (!name) { EOS_UI.toast('Name is required', false); return; }

    var files = filesRaw ? filesRaw.split(',').map(function(f) { return f.trim(); }).filter(Boolean) : [];
    var tools = toolsRaw ? toolsRaw.split(',').map(function(t) { return t.trim(); }).filter(Boolean) : [];
    var body = {
        name: name,
        tier: tier,
        system_prompt: prompt || 'You are a helpful assistant.',
        knowledge_files: files,
        knowledge_dir: kdir,
        tools: tools,
        temperature: tempVal !== '' ? parseFloat(tempVal) : null,
        effort: effortVal,
    };
    if (model) body.model = model;

    try {
        if (editId) {
            await EOS.api('/rooms/api/agents/' + editId, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
        } else {
            await EOS.post('/rooms/api/agents', body);
        }
        EOS_UI.closeModal('agent-modal');
        EOS_UI.toast(editId ? 'Agent updated' : 'Agent created');
        await loadAgents();
        if (editId && currentAgent && currentAgent.id === editId) {
            currentAgent = agents.find(function(a) { return a.id === editId; });
            document.getElementById('chat-name').textContent = currentAgent.name;
        }
    } catch(e) { EOS_UI.toast('Failed to save', false); }
}

async function deleteAgent() {
    var editId = document.getElementById('f-id').value;
    if (!editId) return;
    var agent = agents.find(function(a) { return a.id === editId; });
    if (!await EOS_UI.confirm({message: 'Delete "' + (agent ? agent.name : editId) + '" and all chat history?', action: 'Delete', danger: true})) return;
    try {
        await EOS.api('/rooms/api/agents/' + editId, { method:'DELETE' });
        EOS_UI.closeModal('agent-modal');
        EOS_UI.toast('Agent deleted');
        if (currentAgent && currentAgent.id === editId) _hideChat();
        await loadAgents();
    } catch(e) { EOS_UI.toast('Failed to delete', false); }
}

// --- Load ---

async function loadAgents() {
    try {
        // ?status=all so archived rooms are present in the cache; sidebar
        // filters them out of default views, the Archived tab opts in.
        agents = await EOS.api('/rooms/api/agents?status=all');
        // Pull unread state in parallel so the dot lands with the first paint.
        try { _unreadMap = await EOS.api('/rooms/api/unread') || {}; }
        catch(e) { _unreadMap = {}; }
        renderSidebar();
    } catch(e) {
        document.getElementById('agent-list').innerHTML = EOS_UI.errorState({message: 'Failed to load', onRetry: 'loadAgents()'});
    }
    // Refresh the global pending badge whenever the agent list reloads —
    // covers room creation/deletion + post-action mutations.
    refreshGlobalPendingBadge();
    // Refresh the inbound dashboard if it's showing.
    if (!currentAgent) renderInboundDashboard();
}
