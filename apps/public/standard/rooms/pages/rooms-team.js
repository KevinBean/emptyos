// rooms-team.js — Team mode panel (lead + workers + shared task list +
// auto-dispatch run control). Renders inside the activity drawer's "Team"
// tab, which only appears when the current room has a lead participant.
//
// Backend: apps/rooms/team.py. Calls /rooms/api/rooms/<id>/team* routes.
// Reuses currentAgent (rooms-chat.js) + agents/agentNameById (rooms-agents.js).

var _teamData = null;          // last GET /team payload
var _teamPollTimer = null;     // live poll while a run is active

// Does the current room have a lead? (team mode is active for it)
function roomHasLead(room) {
    var parts = (room && room.participants) || [];
    return parts.some(function(p) { return p && p.role === 'lead'; });
}

// A room is a team CANDIDATE if it has 2+ responders (agent/cli) — so the
// Team tab is reachable to assign the first lead (bootstrap), or if it
// already has a lead. Single-responder rooms never show the tab.
function roomIsTeamCandidate(room) {
    var parts = (room && room.participants) || [];
    var responders = parts.filter(function(p){ return p && (p.type === 'agent' || p.type === 'cli'); });
    return responders.length >= 2 || roomHasLead(room);
}

// Show/hide the Team tab button based on the loaded room. Called from
// openActivityDrawer (rooms-pending.js) via the hook below.
function syncTeamTabVisibility() {
    var btn = document.getElementById('activity-tab-team');
    if (!btn) return;
    btn.style.display = (currentAgent && roomIsTeamCandidate(currentAgent)) ? '' : 'none';
}

async function renderActivityTeamTab() {
    var body = document.getElementById('activity-drawer-body');
    if (!body || !currentAgent) return;
    body.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:20px;text-align:center">Loading team…</div>';
    try {
        _teamData = await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/team');
    } catch (e) {
        body.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:20px;text-align:center">Team data unavailable.</div>';
        return;
    }
    if (!_teamData || _teamData.error) {
        body.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:20px;text-align:center">' + esc((_teamData && _teamData.error) || 'Not a team room.') + '</div>';
        return;
    }
    body.innerHTML = _teamPanelHtml(_teamData);
    _maybeScheduleTeamPoll(_teamData);
}

function _teamRoleChip(role) {
    var map = {lead: 'var(--accent)', worker: 'var(--success, #2e7d32)', peer: 'var(--text-muted)'};
    var c = map[role] || 'var(--text-muted)';
    return '<span style="font-size:10px;text-transform:uppercase;letter-spacing:0.5px;color:' + c +
        ';border:1px solid ' + c + ';border-radius:10px;padding:1px 7px;margin-left:6px">' + esc(role) + '</span>';
}

function _teamStatusBadge(status) {
    var map = {todo: 'var(--text-muted)', doing: 'var(--accent)', done: 'var(--success, #2e7d32)', blocked: 'var(--danger)'};
    var c = map[status] || 'var(--text-muted)';
    return '<span style="font-size:11px;color:' + c + '">[' + esc(status) + ']</span>';
}

function _teamPanelHtml(d) {
    var roster = d.roster || [];
    var tasks = d.tasks || [];
    var run = d.run || {};
    var responders = roster.filter(function(p){ return p.type === 'agent' || p.type === 'cli'; });

    // ── Run control ──
    var runHtml;
    if (run.active) {
        runHtml = '<div style="display:flex;align-items:center;gap:8px;padding:10px 12px;border:1px solid var(--accent);border-radius:8px;margin-bottom:14px;background:var(--accent-tint-weak, transparent)">' +
            '<span style="flex:1;font-size:13px">⚡ Team run active · <b>' + (run.turns_left || 0) + '</b> / ' + (run.max_turns || 0) + ' turns left</span>' +
            '<button class="eos-btn-sm" style="background:var(--danger);color:var(--accent-ink);border:0" onclick="teamStopRun()">Stop</button>' +
        '</div>';
    } else {
        var reason = run.reason ? ' <span style="color:var(--text-muted);font-size:11px">(last run: ' + esc(run.reason) + ')</span>' : '';
        runHtml = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px">' +
            '<button class="eos-btn eos-btn-primary" style="flex:1" onclick="teamStartRun()">▶ Start team run</button>' +
            '<input id="team-max-turns" class="eos-form-input" type="number" value="12" min="1" max="40" title="Turn budget" style="width:64px">' +
        '</div>' + (reason ? '<div style="margin:-8px 0 12px">' + reason + '</div>' : '');
    }

    // ── Roster with role controls ──
    var rosterHtml = '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Roster</div>';
    rosterHtml += roster.map(function(p) {
        var name = p.type === 'user' ? (p.id || 'me') : agentNameById(p.id);
        var roleSelect = '';
        if (p.type !== 'user') {
            roleSelect = '<select class="eos-form-input" style="font-size:11px;padding:2px 4px;width:auto" onchange="teamSetRole(\'' + escAttr(p.id) + '\', this.value)">' +
                ['lead', 'worker', 'peer'].map(function(r) {
                    return '<option value="' + r + '"' + (p.role === r ? ' selected' : '') + '>' + r + '</option>';
                }).join('') + '</select>';
        }
        return '<div style="display:flex;align-items:center;gap:6px;padding:6px 8px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;font-size:13px">' +
            '<span style="flex:1">' + esc(name) + ' <span style="color:var(--text-muted);font-size:10px">' + esc(p.type) + '</span>' + _teamRoleChip(p.role) + '</span>' +
            roleSelect +
        '</div>';
    }).join('');

    // ── Task list grouped by status ──
    var tasksHtml = '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 8px">Shared tasks (' + tasks.length + ')</div>';
    if (!tasks.length) {
        tasksHtml += '<div style="color:var(--text-muted);font-size:12px;padding:8px 0">No tasks yet. The lead adds + assigns them, or add one below.</div>';
    } else {
        tasksHtml += tasks.map(function(t) {
            var who = t.assignee ? (' · @' + esc(agentNameById(t.assignee) || t.assignee)) : ' · <span style="color:var(--text-muted)">unassigned</span>';
            var result = t.result ? '<div style="color:var(--text-muted);font-size:11px;margin-top:3px">→ ' + esc(t.result) + '</div>' : '';
            var assignSel = '<select class="eos-form-input" style="font-size:11px;padding:2px 4px;width:auto;margin-top:4px" onchange="teamAssign(\'' + escAttr(t.id) + '\', this.value)">' +
                '<option value="">— assign —</option>' +
                responders.map(function(p) {
                    return '<option value="' + escAttr(p.id) + '"' + (t.assignee === p.id ? ' selected' : '') + '>' + esc(agentNameById(p.id) || p.id) + '</option>';
                }).join('') + '</select>';
            return '<div style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;font-size:13px">' +
                '<div style="display:flex;gap:6px;align-items:baseline"><span style="flex:1">' + esc(t.content) + '</span>' + _teamStatusBadge(t.status) + '</div>' +
                '<div style="color:var(--text-muted);font-size:11px">' + esc(t.id) + who + '</div>' +
                result + assignSel +
            '</div>';
        }).join('');
    }
    // Add-task row
    tasksHtml += '<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:12px">' +
        '<input id="team-task-content" class="eos-form-input" type="text" placeholder="New task…" style="width:100%">' +
        '<div style="display:flex;gap:6px;margin-top:6px">' +
            '<select id="team-task-assignee" class="eos-form-input" style="flex:1;font-size:12px">' +
                '<option value="">Unassigned</option>' +
                responders.map(function(p){ return '<option value="' + escAttr(p.id) + '">' + esc(agentNameById(p.id) || p.id) + '</option>'; }).join('') +
            '</select>' +
            '<button class="eos-btn eos-btn-primary" onclick="teamAddTask()">Add</button>' +
        '</div>' +
    '</div>';

    return runHtml + rosterHtml + tasksHtml;
}

// Poll while a run is active so turns_left + task statuses update live.
function _maybeScheduleTeamPoll(d) {
    if (_teamPollTimer) { clearTimeout(_teamPollTimer); _teamPollTimer = null; }
    var active = d && d.run && d.run.active;
    var drawerOpen = document.getElementById('activity-drawer').classList.contains('open');
    if (active && drawerOpen && _activityTab === 'team') {
        _teamPollTimer = setTimeout(renderActivityTeamTab, 2500);
    }
}

// ── Actions ──

async function teamAddTask() {
    var content = (document.getElementById('team-task-content').value || '').trim();
    if (!content) return;
    var assignee = document.getElementById('team-task-assignee').value || '';
    await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/team/tasks',
        {method: 'POST', body: JSON.stringify({content: content, assignee: assignee})});
    renderActivityTeamTab();
}

async function teamAssign(taskId, assignee) {
    await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/team/tasks/' + encodeURIComponent(taskId) + '/assign',
        {method: 'POST', body: JSON.stringify({assignee: assignee})});
    renderActivityTeamTab();
}

async function teamSetRole(participantId, role) {
    await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/team/role',
        {method: 'POST', body: JSON.stringify({participant_id: participantId, role: role})});
    // Role change can flip team-enabled — refresh the agent record + tab vis.
    try { currentAgent = (agents || []).find(function(a){ return a.id === currentAgent.id; }) || currentAgent; } catch (e) {}
    if (typeof loadAgents === 'function') { await loadAgents(); currentAgent = (agents || []).find(function(a){ return a.id === currentAgent.id; }) || currentAgent; }
    syncTeamTabVisibility();
    renderActivityTeamTab();
}

async function teamStartRun() {
    var mt = parseInt((document.getElementById('team-max-turns') || {}).value || '12', 10) || 12;
    var r = await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/team/run/start',
        {method: 'POST', body: JSON.stringify({max_turns: mt})});
    if (r && r.error) { EOS_UI.toast(r.error, false); }
    renderActivityTeamTab();
}

async function teamStopRun() {
    await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/team/run/stop', {method: 'POST'});
    renderActivityTeamTab();
}

// Live-refresh the Team tab on team events (realtime bridge).
if (window.EOS && EOS.on) {
    ['rooms:team_task_added', 'rooms:team_task_assigned', 'rooms:team_task_status',
     'rooms:team_run_started', 'rooms:team_run_finished'].forEach(function(ev) {
        EOS.on(ev, function(data) {
            if (currentAgent && data && data.room_id === currentAgent.id &&
                _activityTab === 'team' &&
                document.getElementById('activity-drawer').classList.contains('open')) {
                renderActivityTeamTab();
            }
        });
    });
}
