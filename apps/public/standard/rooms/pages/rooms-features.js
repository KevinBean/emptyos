// rooms-features.js — feature islands: inbound dashboard, context inspector,
// reminders, scheduled rooms, bulk select/archive/delete, snippet library,
// room lifecycle (archive/export/distill), chat header overflow, room-task.

// --- Inbound dashboard (Phase 12) ---
//
// Replaces the static welcome panel with a live "since you were last here"
// surface. Composes existing data: _unreadMap (loaded by loadAgents),
// /rooms/api/pending (global pending), and history mtime ordering.
// Re-runs whenever the welcome panel is shown.

async function renderInboundDashboard() {
    var w = document.getElementById('chat-welcome');
    if (!w) return;
    if (currentAgent) return;  // chat panel showing instead

    // Compose stats from already-loaded state.
    var unreadEntries = Object.entries(_unreadMap || {});
    var unreadRooms = unreadEntries.length;
    var unreadMessages = unreadEntries.reduce(function(s, kv){
        return s + (kv[1].count || 0);
    }, 0);
    // Pending — fetch fresh in case the user resolved some elsewhere.
    var pending = [];
    try {
        pending = await EOS.api('/rooms/api/pending') || [];
    } catch(e) { pending = []; }
    // Reminders — fired (overdue) + upcoming. Both surfaced separately;
    // fired ones are the most-urgent attention call.
    var reminders = [];
    try {
        reminders = await EOS.api('/rooms/api/reminders?include_fired=1') || [];
    } catch(e) { reminders = []; }
    var firedReminders = reminders.filter(function(r){ return r.fired; });
    var upcomingReminders = reminders.filter(function(r){ return !r.fired; });
    var pendingByRoom = {};
    pending.forEach(function(p) {
        (pendingByRoom[p.room_id] = pendingByRoom[p.room_id] || []).push(p);
    });
    var totalRooms = (agents || []).filter(function(a){
        return a.status !== 'archived';
    }).length;

    // Greeting — adapts to the time of day.
    var hour = new Date().getHours();
    var greeting = hour < 5 ? 'Up late' :
                   hour < 12 ? 'Good morning' :
                   hour < 17 ? 'Good afternoon' :
                   hour < 22 ? 'Good evening' : 'Good night';
    var sub = (unreadRooms || pending.length || firedReminders.length)
        ? 'Some things waiting for you.'
        : 'All caught up.';

    // Section: Fired reminders (most urgent — surface first).
    var firedHtml = '';
    if (firedReminders.length) {
        firedHtml = '<div class="inbound-section"><h3>⏰ Reminders fired (' + firedReminders.length + ')</h3>' +
            firedReminders.slice(0, 6).map(function(r) {
                var room = (agents || []).find(function(a){ return a.id === r.room_id; });
                var name = room ? room.name : r.room_id;
                var when = new Date(r.due_ts).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
                var noteHtml = r.note ? '<div class="row-sub">' + esc(r.note) + '</div>' : '';
                var color = room ? agentColor(name) : 'var(--accent)';
                var icon = room && room.tier === 'group' ? '👥' : (room ? initial(name) : '⏰');
                return '<div class="inbound-row" onclick="dismissAndOpenReminder(\'' + escAttr(r.id) + '\',\'' + escAttr(r.room_id) + '\')">' +
                    '<div class="row-avatar" style="background:' + color + '">' + esc(icon) + '</div>' +
                    '<div class="row-meta">' +
                        '<div class="row-title">' + esc(name) + '</div>' +
                        (noteHtml || '<div class="row-sub">' + esc(when) + '</div>') +
                    '</div>' +
                    '<span class="row-tag">due ' + esc(when) + '</span>' +
                    '</div>';
            }).join('') +
            '</div>';
    }

    // Section: Unread.
    var unreadHtml = '';
    if (unreadEntries.length) {
        var unreadRows = unreadEntries.slice().sort(function(a,b){
            return (b[1].last_ts || '').localeCompare(a[1].last_ts || '');
        }).slice(0, 6).map(function(kv) {
            var rid = kv[0];
            var info = kv[1];
            var room = (agents || []).find(function(a){ return a.id === rid; });
            if (!room) return '';
            var color = agentColor(room.name);
            var icon = (room.tier === 'group') ? '👥' : initial(room.name);
            var when = info.last_ts ? new Date(info.last_ts).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
            return '<div class="inbound-row" onclick="openChat(\'' + escAttr(rid) + '\')">' +
                '<div class="row-avatar" style="background:' + color + '">' + esc(icon) + '</div>' +
                '<div class="row-meta">' +
                    '<div class="row-title">' + esc(room.name) + '</div>' +
                    '<div class="row-sub">' + esc(when) + '</div>' +
                '</div>' +
                '<span class="row-tag">' + info.count + ' new</span>' +
                '</div>';
        }).filter(Boolean).join('');
        unreadHtml = '<div class="inbound-section">' +
            '<h3>What\'s new (' + unreadRooms + ')</h3>' +
            unreadRows +
            '</div>';
    }

    // Section: Pending review.
    var pendingHtml = '';
    var pendingRoomIds = Object.keys(pendingByRoom);
    if (pendingRoomIds.length) {
        var pendingRows = pendingRoomIds.slice(0, 6).map(function(rid) {
            var entries = pendingByRoom[rid];
            var room = (agents || []).find(function(a){ return a.id === rid; });
            var roomName = room ? room.name : rid;
            var color = room ? agentColor(roomName) : 'var(--accent)';
            var icon = room && room.tier === 'group' ? '👥' : (room ? initial(roomName) : '⏳');
            var verbs = entries.slice(0, 3).map(function(p){
                return p.app + '.' + p.method;
            }).join(', ');
            return '<div class="inbound-row" onclick="openChat(\'' + escAttr(rid) + '\')">' +
                '<div class="row-avatar" style="background:' + color + '">' + esc(icon) + '</div>' +
                '<div class="row-meta">' +
                    '<div class="row-title">' + esc(roomName) + '</div>' +
                    '<div class="row-sub">' + esc(verbs) + (entries.length > 3 ? ' +' + (entries.length - 3) + ' more' : '') + '</div>' +
                '</div>' +
                '<span class="row-tag">' + entries.length + ' to review</span>' +
                '</div>';
        }).join('');
        pendingHtml = '<div class="inbound-section">' +
            '<h3>Pending review (' + pending.length + ')</h3>' +
            pendingRows +
            '<div style="text-align:right;margin-top:6px"><button class="eos-btn-sm eos-btn-ghost" onclick="openGlobalPending()">Review all →</button></div>' +
            '</div>';
    }

    // Section: Recent (rooms not in unread, sorted by some recency proxy).
    var unreadIds = new Set(unreadEntries.map(function(kv){ return kv[0]; }));
    var recentCandidates = (agents || []).filter(function(a){
        return a.status !== 'archived' && !unreadIds.has(a.id);
    });
    // Best-effort recency: created or builtin? we don't have last_ts on agents
    // here, so just sort alphabetically and cap at 5 — non-time-sensitive.
    recentCandidates.sort(function(a, b) {
        return (a.name || '').localeCompare(b.name || '');
    });
    var recentRows = recentCandidates.slice(0, 5).map(function(a) {
        var color = agentColor(a.name);
        var icon = (a.tier === 'group') ? '👥' : initial(a.name);
        var preview = (a.system_prompt || '').replace(/\s+/g,' ').slice(0, 90);
        if (a.tier === 'group') {
            var rs = (a.participants || []).filter(function(p){
                return p.type === 'agent' || p.type === 'cli';
            }).length;
            preview = rs + ' participants';
        }
        return '<div class="inbound-row" onclick="openChat(\'' + escAttr(a.id) + '\')">' +
            '<div class="row-avatar" style="background:' + color + '">' + esc(icon) + '</div>' +
            '<div class="row-meta">' +
                '<div class="row-title">' + esc(a.name) + '</div>' +
                '<div class="row-sub">' + esc(preview || 'No prompt set') + '</div>' +
            '</div></div>';
    }).join('');
    var recentHtml = recentRows ? '<div class="inbound-section">' +
        '<h3>Other rooms</h3>' + recentRows + '</div>' : '';

    // Stat strip.
    var statHtml =
        '<div class="inbound-stats">' +
            '<div class="inbound-stat' + (unreadMessages ? ' has-attn' : '') + '">' +
                '<div class="v">' + unreadMessages + '</div>' +
                '<div class="l">Unread</div>' +
            '</div>' +
            '<div class="inbound-stat' + (pending.length ? ' has-attn' : '') + '">' +
                '<div class="v">' + pending.length + '</div>' +
                '<div class="l">Pending</div>' +
            '</div>' +
            '<div class="inbound-stat' + (firedReminders.length ? ' has-attn' : '') + '">' +
                '<div class="v">' + firedReminders.length + '</div>' +
                '<div class="l">Reminders</div>' +
            '</div>' +
            '<div class="inbound-stat">' +
                '<div class="v">' + totalRooms + '</div>' +
                '<div class="l">Rooms</div>' +
            '</div>' +
        '</div>';

    // Empty case — no unread, no pending, no fired reminders.
    var allCaughtUp = !unreadEntries.length && !pending.length && !firedReminders.length;
    var dashboard =
        '<div class="inbound-dashboard">' +
            '<div class="inbound-greeting">' + esc(greeting) + '</div>' +
            '<div class="inbound-sub">' + esc(sub) + '</div>' +
            statHtml +
            firedHtml +
            unreadHtml +
            pendingHtml +
            (allCaughtUp ? '<div class="inbound-empty">No unread messages, no pending actions, no due reminders. Pick a room from the sidebar — or hit + Agent / + Group to start a new one.</div>' : '') +
            recentHtml +
        '</div>';
    w.innerHTML = dashboard;
    w.style.display = 'flex';
    w.style.padding = '0';
    w.style.alignItems = 'stretch';
}

// --- Context inspector (Phase 19) ---
//
// Pure visualization — fetches /api/rooms/<id>/inspect and renders sections
// with char counts so the user can see what's being sent (and how much).
// No LLM call is made; this just rebuilds the prompt structure.

async function inspectRoomContext() {
    if (!currentAgent) return;
    var data;
    try {
        data = await EOS.api('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/inspect');
    } catch (e) {
        EOS_UI.toast('Failed to load context', false);
        return;
    }
    if (!data || data.error) {
        EOS_UI.toast(data && data.error ? data.error : 'No context', false);
        return;
    }
    function section(label, body, chars, extras) {
        var k = chars != null ? '<span style="margin-left:auto;font-size:11px;color:var(--text-muted)">' + chars + ' chars</span>' : '';
        return '<details open style="margin-bottom:12px">' +
            '<summary style="cursor:pointer;display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;color:var(--text-muted);margin-bottom:6px">' +
                esc(label) + (extras ? '<span style="color:var(--text);font-weight:500;text-transform:none;font-size:12px">' + extras + '</span>' : '') + k +
            '</summary>' +
            '<pre style="margin:0;padding:10px 12px;background:var(--bg-surface);border-radius:6px;font-size:12px;line-height:1.5;white-space:pre-wrap;font-family:var(--font-mono,monospace);max-height:240px;overflow-y:auto">' + esc(body || '(empty)') + '</pre>' +
            '</details>';
    }
    var meta = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:14px;padding:10px;border:1px solid var(--border);border-radius:6px;background:var(--bg-surface);font-size:11px">' +
        '<div><div style="color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px">Responder</div><div style="font-weight:500">' + esc(data.responder_name) + '</div></div>' +
        '<div><div style="color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px">Model</div><div style="font-weight:500">' + esc(data.model) + '</div></div>' +
        '<div><div style="color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px">Effort</div><div style="font-weight:500">' + esc(data.effort) + '</div></div>' +
        '<div><div style="color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px">History</div><div style="font-weight:500">' + data.history_count + ' msgs</div></div>' +
        '<div><div style="color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px">Total</div><div style="font-weight:500;color:var(--accent)">' + data.total_chars + ' chars</div></div>' +
        '</div>';
    var kbExtras = data.knowledge_files && data.knowledge_files.length
        ? ' from ' + data.knowledge_files.length + ' file' + (data.knowledge_files.length !== 1 ? 's' : '')
        : '';
    var body =
        '<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px">What the LLM sees on the next turn (excluding the user\'s incoming message).</div>' +
        meta +
        section('System prompt', data.system_prompt, data.system_prompt_chars) +
        section('Knowledge', data.knowledge, data.knowledge_chars, kbExtras) +
        section('Recent transcript', data.transcript, data.transcript_chars,
            ' (last ' + Math.min(20, data.history_count) + ' of ' + data.history_count + ')');
    EOS_UI.modal({title: '🔍 Context · ' + (currentAgent.name || currentAgent.id),
                  body: body, width: '720px'});
}


// --- Reminders ---
// --- Reminders (Phase 17) ---
//
// Lightweight duration parser for slash command input. Accepts:
//   <num><unit>  m/h/d/w  (e.g. 30m, 2h, 1d, 1w)
//   tomorrow                (default 09:00 next day, local time)
//   "next week" / nextweek  (7 days from now)
//   <h>:<m>                 (today at HH:MM, or tomorrow if past)
// Returns ISO timestamp (UTC) or null when unparseable.

function parseReminderWhen(s) {
    var raw = (s || '').trim();
    if (!raw) return null;
    var lower = raw.toLowerCase();

    // Numeric duration: 30m / 2h / 1d / 1w (optional spaces, full words ok).
    var dur = lower.match(/^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|wk|week|weeks)$/);
    if (dur) {
        var n = parseInt(dur[1], 10);
        var u = dur[2];
        var ms = 0;
        if (u[0] === 'm') ms = n * 60 * 1000;
        else if (u[0] === 'h') ms = n * 60 * 60 * 1000;
        else if (u[0] === 'd') ms = n * 24 * 60 * 60 * 1000;
        else if (u[0] === 'w') ms = n * 7 * 24 * 60 * 60 * 1000;
        return new Date(Date.now() + ms).toISOString();
    }
    if (lower === 'tomorrow' || lower === 'tmr') {
        var t = new Date();
        t.setDate(t.getDate() + 1);
        t.setHours(9, 0, 0, 0);
        return t.toISOString();
    }
    if (lower === 'next week' || lower === 'nextweek') {
        return new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
    }
    // HH:MM today (or tomorrow if past).
    var hm = lower.match(/^(\d{1,2}):(\d{2})$/);
    if (hm) {
        var t2 = new Date();
        t2.setHours(parseInt(hm[1], 10), parseInt(hm[2], 10), 0, 0);
        if (t2.getTime() <= Date.now()) t2.setDate(t2.getDate() + 1);
        return t2.toISOString();
    }
    return null;
}

// Dismiss a fired reminder and jump into its room. Removes the reminder
// (it's done its job) so the dashboard stays clean on next refresh.
async function dismissAndOpenReminder(reminderId, roomId) {
    try {
        await EOS.api('/rooms/api/reminders/' + encodeURIComponent(reminderId), {method:'DELETE'});
    } catch(e) { /* ignore — opening the room is the main goal */ }
    if (roomId) openChat(roomId);
}

async function scheduleReminder(rest) {
    if (!currentAgent) return;
    var input = (rest || '').trim();
    // Split on the first whitespace AFTER a recognized when-token.
    // Simplest: try the whole input as a duration; if that fails, split on
    // first space and use the head as the duration, the tail as the note.
    var when = parseReminderWhen(input);
    var note = '';
    if (!when) {
        var idx = input.search(/\s/);
        if (idx > 0) {
            when = parseReminderWhen(input.slice(0, idx));
            note = input.slice(idx + 1).trim();
        }
    }
    if (!when) {
        EOS_UI.toast('Usage: /remind <30m|2h|1d|tomorrow|14:30> [note]', false);
        return;
    }
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/remind',
            {due_ts: when, note: note});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        var local = new Date(when).toLocaleString([], {
            month:'short', day:'numeric', hour:'2-digit', minute:'2-digit',
        });
        EOS_UI.toast('Reminder set: ' + local);
    } catch(e) {
        EOS_UI.toast('Failed to set reminder', false);
    }
}


// --- Scheduled rooms ---
// --- Scheduled rooms (Phase 22) ---
//
// Per-room cron + prompt. On fire, the agent self-monologues into history.
// Modal lets the user pick a preset cadence or type a raw cron.

var SCHEDULE_PRESETS = [
    {label: 'Every weekday morning (9am)', cron: '0 9 * * 1-5'},
    {label: 'Daily at 9am',                cron: '0 9 * * *'},
    {label: 'Daily at 9pm',                cron: '0 21 * * *'},
    {label: 'Weekly Monday 9am',           cron: '0 9 * * 1'},
    {label: 'Weekly Sunday 8pm',           cron: '0 20 * * 0'},
    {label: 'Hourly during work hours',    cron: '0 9-17 * * 1-5'},
];

function openScheduleModal() {
    if (!currentAgent) return;
    var sched = currentAgent.schedule || {};
    var presetOpts = SCHEDULE_PRESETS.map(function(p) {
        var sel = (p.cron === sched.cron) ? ' selected' : '';
        return '<option value="' + escAttr(p.cron) + '"' + sel + '>' + esc(p.label) + ' — ' + esc(p.cron) + '</option>';
    }).join('');
    var lastFired = sched.last_fired
        ? '<div style="font-size:11px;color:var(--text-muted);margin-top:6px">Last fired: ' + new Date(sched.last_fired).toLocaleString() + '</div>'
        : '';
    var enabled = sched.enabled !== false;
    var body =
        '<div style="font-size:12px;color:var(--text-muted);margin-bottom:14px">A scheduled check-in fires the agent on a cron — they post a self-initiated message into the room. The unread badge shows when one\'s waiting.</div>' +
        '<div class="eos-form-group">' +
            '<label class="eos-form-label">Preset</label>' +
            '<select id="sched-preset" class="eos-form-input" onchange="document.getElementById(\'sched-cron\').value = this.value">' +
                '<option value="">— choose preset —</option>' + presetOpts +
            '</select>' +
        '</div>' +
        '<div class="eos-form-group">' +
            '<label class="eos-form-label">Cron expression</label>' +
            '<input id="sched-cron" class="eos-form-input" type="text" placeholder="0 9 * * 1-5" value="' + escAttr(sched.cron || '') + '">' +
            '<div style="font-size:11px;color:var(--text-muted);margin-top:4px">Format: minute hour day month weekday. <a href="https://crontab.guru" target="_blank" style="color:var(--accent)">crontab.guru</a> if unsure.</div>' +
        '</div>' +
        '<div class="eos-form-group">' +
            '<label class="eos-form-label">Prompt sent on each fire</label>' +
            '<textarea id="sched-prompt" class="eos-form-input" rows="3" placeholder="What should the agent open with? e.g. \'What\'s on my plate today? Reflect on yesterday\'s commits.\'">' + esc(sched.prompt || '') + '</textarea>' +
        '</div>' +
        '<div class="eos-form-group">' +
            '<label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">' +
                '<input type="checkbox" id="sched-enabled"' + (enabled ? ' checked' : '') + '>' +
                'Enabled' +
            '</label>' +
            lastFired +
        '</div>' +
        '<div class="eos-form-actions">' +
            (sched.cron ? '<button class="eos-btn eos-btn-ghost" onclick="clearRoomSchedule()">Remove</button>' : '') +
            (sched.cron ? '<button class="eos-btn eos-btn-ghost" onclick="fireScheduleNow()">Fire now</button>' : '') +
            '<button class="eos-btn eos-btn-primary" onclick="saveRoomSchedule()">Save</button>' +
        '</div>';
    EOS_UI.modal({title: '⏲️  Schedule · ' + (currentAgent.name || ''), body: body, width: '560px'});
}

async function saveRoomSchedule() {
    if (!currentAgent) return;
    var cron = (document.getElementById('sched-cron').value || '').trim();
    var prompt = (document.getElementById('sched-prompt').value || '').trim();
    var enabled = document.getElementById('sched-enabled').checked;
    if (!cron || !prompt) { EOS_UI.toast('Cron and prompt required', false); return; }
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/schedule',
            {cron: cron, prompt: prompt, enabled: enabled});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        currentAgent.schedule = res.schedule;
        var cached = (agents || []).find(function(a){ return a.id === currentAgent.id; });
        if (cached) cached.schedule = res.schedule;
        EOS_UI.closeModal();
        EOS_UI.toast(enabled ? 'Schedule saved' : 'Schedule saved (disabled)');
    } catch(e) { EOS_UI.toast('Failed to save', false); }
}

async function clearRoomSchedule() {
    if (!currentAgent) return;
    if (!await EOS_UI.confirm({message: 'Remove the schedule for this room?', action: 'Remove', danger: true})) return;
    try {
        await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) +
            '/schedule', {method:'DELETE'});
        delete currentAgent.schedule;
        var cached = (agents || []).find(function(a){ return a.id === currentAgent.id; });
        if (cached) delete cached.schedule;
        EOS_UI.closeModal();
        EOS_UI.toast('Schedule cleared');
    } catch(e) { EOS_UI.toast('Failed to clear', false); }
}

async function fireScheduleNow() {
    if (!currentAgent) return;
    EOS_UI.toast('Firing scheduled prompt…');
    try {
        await EOS.post('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) +
            '/fire-schedule', {});
        EOS_UI.closeModal();
        EOS_UI.toast('Fired — open the room to see the message');
    } catch(e) { EOS_UI.toast('Fire failed', false); }
}


// --- Bulk operations ---
// --- Bulk operations (Phase 24) ---
//
// Body class `bulk-select-on` triggers the per-item checkbox styling.
// Clicks on .agent-item are intercepted in bulk mode (delegation in
// chatPanelDelegate) and toggle a `bulk-selected` class instead of opening
// the chat. The action bar at the bottom-left fires Archive / Delete on
// every selected room sequentially.

var _bulkSelected = new Set();

function setBulkSelect(on) {
    document.body.classList.toggle('bulk-select-on', !!on);
    var btn = document.getElementById('bulk-toggle-btn');
    if (btn) btn.textContent = on ? '☑' : '☐';
    if (!on) {
        _bulkSelected.clear();
        document.querySelectorAll('.agent-item.bulk-selected').forEach(function(el){
            el.classList.remove('bulk-selected');
        });
        var bar = document.getElementById('bulk-action-bar');
        if (bar) bar.classList.remove('open');
    } else {
        // Show the bar with 0 selected — visual hint.
        var bar2 = document.getElementById('bulk-action-bar');
        if (bar2) bar2.classList.add('open');
        updateBulkSelectedCount();
    }
}

function toggleBulkSelect() {
    setBulkSelect(!document.body.classList.contains('bulk-select-on'));
}

function updateBulkSelectedCount() {
    var n = _bulkSelected.size;
    var el = document.getElementById('bulk-selected-count');
    if (el) el.textContent = String(n);
}

// Click on an agent-item in bulk mode toggles selection instead of opening.
// Wired via event delegation on the sidebar.
document.addEventListener('click', function(ev) {
    if (!document.body.classList.contains('bulk-select-on')) return;
    var item = ev.target.closest('.agent-item');
    if (!item) return;
    // Need to extract the room id — the existing inline onclick is
    // openChat('<id>') so we read it from there to avoid restructuring
    // every render path.
    var oc = item.getAttribute('onclick') || '';
    var m = oc.match(/openChat\('([^']+)'\)/);
    if (!m) return;
    ev.preventDefault();
    ev.stopPropagation();
    var rid = m[1];
    if (_bulkSelected.has(rid)) {
        _bulkSelected.delete(rid);
        item.classList.remove('bulk-selected');
    } else {
        _bulkSelected.add(rid);
        item.classList.add('bulk-selected');
    }
    updateBulkSelectedCount();
}, true);  // capture phase so we beat the inline onclick

async function bulkArchive() {
    var ids = Array.from(_bulkSelected);
    if (!ids.length) { EOS_UI.toast('Nothing selected', false); return; }
    if (!await EOS_UI.confirm({message: 'Archive ' + ids.length + ' room' + (ids.length !== 1 ? 's' : '') + '?', action: 'Archive'})) return;
    var ok = 0, fail = 0;
    for (var i = 0; i < ids.length; i++) {
        try {
            var res = await EOS.post('/rooms/api/rooms/' + encodeURIComponent(ids[i]) + '/archive', {});
            if (res && res.error) fail++; else ok++;
        } catch(e) { fail++; }
    }
    EOS_UI.toast('Archived ' + ok + (fail ? ' · ' + fail + ' failed' : ''));
    setBulkSelect(false);
    await loadAgents();
}

async function bulkDelete() {
    var ids = Array.from(_bulkSelected);
    if (!ids.length) { EOS_UI.toast('Nothing selected', false); return; }
    if (!await EOS_UI.confirm({message: 'Delete ' + ids.length + ' room' + (ids.length !== 1 ? 's' : '') + ' and all their history? Cannot be undone.', action: 'Delete', danger: true})) return;
    var ok = 0, fail = 0, builtin = 0;
    for (var i = 0; i < ids.length; i++) {
        try {
            var res = await EOS.api('/rooms/api/agents/' + encodeURIComponent(ids[i]), {method:'DELETE'});
            if (res && res.error) {
                // Builtin rooms refuse delete — count separately.
                if (/builtin/i.test(res.error)) builtin++;
                else fail++;
            } else ok++;
        } catch(e) { fail++; }
    }
    var parts = ['Deleted ' + ok];
    if (builtin) parts.push(builtin + ' builtin (skipped)');
    if (fail) parts.push(fail + ' failed');
    EOS_UI.toast(parts.join(' · '));
    setBulkSelect(false);
    // If the open room got deleted, drop back to the welcome panel.
    if (currentAgent && ids.indexOf(currentAgent.id) >= 0) _hideChat();
    await loadAgents();
}


// --- Snippet library ---
// --- Snippet library (Phase 21) ---
//
// Save reusable prompt fragments by name; recall via /snip <name>. Useful
// for repeated workflows like "review this code for security issues".
// Storage is server-side so snippets survive across browsers.

async function saveSnippetFromInput(rest) {
    var name = (rest || '').trim().toLowerCase();
    if (!name) {
        EOS_UI.toast('Usage: /save <name>', false);
        return;
    }
    var input = document.getElementById('chat-input');
    var body = input ? (input.value || '').replace(/^\/save\s+\S+\s*/i, '').trim() : '';
    // When the user typed only `/save <name>` without a body, open a modal
    // to capture it. Avoids window.prompt (forbidden under DL-6: native
    // dialogs break the design language and are unstyleable).
    if (!body || body === '/save ' + name || body.startsWith('/save')) {
        EOS_UI.formModal('Save snippet "' + name + '"', [
            {key: 'body', label: 'Snippet body', type: 'textarea', placeholder: 'The prompt fragment to save…'},
        ], async function(values) {
            var b = (values && values.body || '').trim();
            if (!b) { EOS_UI.toast('Snippet body is empty', false); return; }
            await _persistSnippet(name, b);
        });
        return;
    }
    await _persistSnippet(name, body);
}

async function _persistSnippet(name, body) {
    try {
        var res = await EOS.post('/rooms/api/snippets', {name: name, body: body});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        EOS_UI.toast(res.updated ? 'Snippet updated' : 'Snippet saved');
        var input = document.getElementById('chat-input');
        if (input) { input.value = ''; input.style.height = 'auto'; }
    } catch(e) {
        EOS_UI.toast('Failed to save', false);
    }
}

async function expandSnippetToInput(rest) {
    var name = (rest || '').trim().toLowerCase().split(/\s+/)[0];
    if (!name) { EOS_UI.toast('Usage: /snip <name>', false); return; }
    try {
        var res = await EOS.api('/rooms/api/snippets/' + encodeURIComponent(name));
        if (!res || res.error) { EOS_UI.toast(res && res.error || 'Not found', false); return; }
        var input = document.getElementById('chat-input');
        if (!input) return;
        input.value = res.body || '';
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        input.focus();
        var pos = input.value.length;
        input.selectionStart = input.selectionEnd = pos;
    } catch(e) {
        EOS_UI.toast('Failed to load snippet', false);
    }
}

async function openSnippetsLibrary() {
    var snippets = [];
    try { snippets = await EOS.api('/rooms/api/snippets') || []; }
    catch(e) { EOS_UI.toast('Failed to load library', false); return; }
    var rows;
    if (!snippets.length) {
        rows = '<div style="color:var(--text-muted);font-size:13px;padding:30px;text-align:center">No snippets yet. Type a prompt in any room\'s input, then run <code style="background:var(--bg-surface);padding:1px 5px;border-radius:4px">/save name</code>.</div>';
    } else {
        rows = snippets.map(function(s) {
            var preview = (s.body || '').replace(/\s+/g, ' ').slice(0, 100);
            var meta = (s.used_count > 0 ? 'used ' + s.used_count + '× · ' : '') +
                       (s.updated ? new Date(s.updated).toLocaleDateString([], {month:'short', day:'numeric'}) : '');
            return '<div style="display:flex;align-items:flex-start;gap:8px;padding:10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px">' +
                '<div style="flex:1;min-width:0">' +
                    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">' +
                        '<code style="background:var(--bg-surface);padding:2px 6px;border-radius:4px;font-size:12px;font-weight:600">/' + esc(s.name) + '</code>' +
                        '<span style="font-size:11px;color:var(--text-muted)">' + esc(meta) + '</span>' +
                    '</div>' +
                    '<div style="font-size:12px;color:var(--text);line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">' + esc(preview) + '</div>' +
                '</div>' +
                '<div style="display:flex;gap:4px;flex-shrink:0">' +
                    '<button class="eos-btn-sm" onclick="useSnippetFromLibrary(\'' + escAttr(s.name) + '\')" style="background:var(--accent);color:var(--accent-ink);border:0;font-size:11px">Use</button>' +
                    '<button class="eos-btn-sm eos-btn-ghost" onclick="deleteSnippetFromLibrary(\'' + escAttr(s.name) + '\')" style="font-size:11px" aria-label="Delete snippet from library">×</button>' +
                '</div>' +
                '</div>';
        }).join('');
    }
    EOS_UI.modal({
        title: '📋 Snippets',
        body: '<div style="font-size:12px;color:var(--text-muted);margin-bottom:12px">Saved prompt fragments — type <code style="background:var(--bg-surface);padding:1px 5px;border-radius:4px">/snip name</code> in any chat input to drop one in.</div>' + rows,
        width: '600px',
    });
}

async function useSnippetFromLibrary(name) {
    EOS_UI.closeModal();
    if (!currentAgent) {
        EOS_UI.toast('Open a room first', false);
        return;
    }
    expandSnippetToInput(name);
}

async function deleteSnippetFromLibrary(name) {
    if (!await EOS_UI.confirm({message: 'Delete snippet /' + name + '?', action: 'Delete', danger: true})) return;
    try {
        await EOS.api('/rooms/api/snippets/' + encodeURIComponent(name), {method:'DELETE'});
        EOS_UI.toast('Deleted');
        openSnippetsLibrary();  // refresh
    } catch(e) { EOS_UI.toast('Delete failed', false); }
}


// --- Room lifecycle + header overflow ---
// --- Room lifecycle: archive / export / distill (Phase 8) ---

async function toggleRoomArchive() {
    if (!currentAgent) return;
    var isArchived = currentAgent.status === 'archived';
    var verb = isArchived ? 'unarchive' : 'archive';
    if (!isArchived && !await EOS_UI.confirm({message: 'Archive ' + currentAgent.name +
        '? It will move to the Archived tab. Reversible.', action: 'Archive'})) return;
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/' + verb, {});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        EOS_UI.toast(isArchived ? 'Restored' : 'Archived');
        await loadAgents();
        // Drop the chat panel back to welcome — archived rooms shouldn't
        // stay open as the active conversation.
        if (!isArchived) {
            _hideChat();
        } else {
            // Reload the current room with fresh status.
            currentAgent = (agents || []).find(function(a){ return a.id === currentAgent.id; });
        }
    } catch(e) {
        EOS_UI.toast(verb + ' failed', false);
    }
}

async function exportChatToVault() {
    if (!currentAgent) return;
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/export', {});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        EOS_UI.toast('Exported: ' + (res.path || 'vault note created'));
    } catch(e) {
        EOS_UI.toast('Export failed', false);
    }
}

async function distillRoom() {
    if (!currentAgent) return;
    if (!await EOS_UI.confirm('Distill this room into a KB note? Uses the room\'s default model — may take ~10s.')) return;
    EOS_UI.toast('Distilling…');
    try {
        var res = await EOS.post('/rooms/api/rooms/' +
            encodeURIComponent(currentAgent.id) + '/distill', {});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        EOS_UI.toast('Distilled: ' + (res.path || 'KB note created'));
    } catch(e) {
        EOS_UI.toast('Distill failed', false);
    }
}

// --- Chat header overflow menu ---

function toggleChatOverflow(ev) {
    if (ev) ev.stopPropagation();
    var m = document.getElementById('chat-overflow-menu');
    var btn = document.getElementById('chat-overflow-btn');
    if (!m) return;
    var willOpen = !m.classList.contains('open');
    m.classList.toggle('open', willOpen);
    if (btn) btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    if (willOpen) {
        // Re-label Archive ↔ Unarchive based on current room status.
        var ab = document.getElementById('chat-archive-btn');
        if (ab && currentAgent) {
            ab.textContent = currentAgent.status === 'archived' ? 'Unarchive' : 'Archive';
        }
        // Re-label Auto-speak based on the room's saved preference.
        var asb = document.getElementById('chat-autospeak-btn');
        if (asb && currentAgent) {
            asb.textContent = 'Auto-speak: ' + (isAutoSpeakOn(currentAgent.id) ? 'on' : 'off');
        }
        document.addEventListener('click', _outsideOverflow, {capture:true, once:true});
    }
}
function _outsideOverflow(ev) {
    var m = document.getElementById('chat-overflow-menu');
    if (m && !m.contains(ev.target) && ev.target.id !== 'chat-overflow-btn') {
        m.classList.remove('open');
        var btn = document.getElementById('chat-overflow-btn');
        if (btn) btn.setAttribute('aria-expanded', 'false');
    }
}
function closeChatOverflow() {
    var m = document.getElementById('chat-overflow-menu');
    if (m) m.classList.remove('open');
    var btn = document.getElementById('chat-overflow-btn');
    if (btn) btn.setAttribute('aria-expanded', 'false');
}


// --- Room ↔ task pointer ---
async function loadRoomTaskCount(roomId) {
    var pill = document.getElementById('chat-activity-count');
    if (pill) pill.dataset.tasksOpen = '0';
    try {
        var tasks = await EOS.api('/rooms/api/rooms/' + encodeURIComponent(roomId) + '/tasks');
        if (Array.isArray(tasks)) {
            var openCount = tasks.filter(function(t){ return !t.done; }).length;
            if (pill) pill.dataset.tasksOpen = String(openCount);
            // Also keep the drawer-tab counter fresh.
            var tab = document.getElementById('activity-tab-tasks-count');
            if (tab) tab.textContent = tasks.length ? '(' + openCount + ')' : '';
        }
    } catch(e) { /* projects unavailable — silent */ }
    updateActivityBadge();
}

// Legacy entrypoint kept for any external callers — opens the Activity
// drawer's Tasks tab instead of the standalone modal it used to render.
function openRoomTasks() {
    _activityTab = 'tasks';
    openActivityDrawer();
}

// ── Session branching (/fork, /branches, tree drawer) ─────────────────
// Minimal-viable UI for the tree-aware history landed in 3.1-3.4. The
// chat backend always knows about parent_entry_id; this layer surfaces
// it: pick a past message → POST /api/rooms/<id>/fork → next chat turn
// creates a sibling branch.

async function _fetchRoomTree() {
    if (!currentAgent) return null;
    try {
        return await EOS.api('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/tree');
    } catch (e) {
        EOS_UI.toast('Tree fetch failed: ' + e, false);
        return null;
    }
}

function _previewChip(node) {
    var who = node.role === 'user' ? 'me'
            : (node.actor_id ? node.actor_id : 'agent');
    var preview = (node.preview || '').replace(/\s+/g, ' ').trim() || '(empty)';
    if (preview.length > 70) preview = preview.slice(0, 67) + '...';
    return '<span style="font-weight:500;color:var(--text-muted)">' + esc(who) + ':</span> ' + esc(preview);
}

async function openForkPicker(prefilledEntryId) {
    var tree = await _fetchRoomTree();
    if (!tree || tree.error) return;
    var nodes = tree.nodes || [];
    if (!nodes.length) {
        EOS_UI.toast('Room has no history to fork from yet', false);
        return;
    }
    var current = tree.current_head_entry_id;
    // List in reverse so the newest is at the top — fork targets are
    // usually recent.
    var rows = nodes.slice().reverse().map(function(n) {
        var isHead = n.entry_id === current;
        var label = isHead ? ' (current head)' : '';
        return '<div class="fork-row" data-entry-id="' + escAttr(n.entry_id) + '"' +
            ' style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;margin-bottom:6px;background:var(--bg-card);font-size:13px;cursor:pointer"' +
            ' onmouseover="this.style.background=\'var(--bg-card-hover)\'"' +
            ' onmouseout="this.style.background=\'var(--bg-card)\'"' +
            ' onclick="_acceptFork(\'' + escAttr(n.entry_id) + '\')">' +
            '<div style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _previewChip(n) + label + '</div>' +
            '<span style="color:var(--text-muted);font-size:11px">' + esc((n.ts || '').slice(11, 19)) + '</span>' +
            '</div>';
    }).join('');
    EOS_UI.modal({
        title: 'Fork from…',
        body: '<div style="margin-bottom:10px;font-size:12px;color:var(--text-muted)">' +
              'Pick an entry. The next message you send branches from there.' +
              '</div>' +
              '<div style="max-height:50vh;overflow-y:auto">' + rows + '</div>',
        width: '560px',
    });
    if (prefilledEntryId) {
        // If called as `/fork <id>`, just fire immediately.
        setTimeout(function() { _acceptFork(prefilledEntryId); }, 0);
    }
}

async function _acceptFork(entryId) {
    if (!currentAgent || !entryId) return;
    try {
        var res = await EOS.post('/rooms/api/rooms/' + encodeURIComponent(currentAgent.id) + '/fork',
            {at_entry_id: entryId});
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        EOS_UI.closeModal();
        EOS_UI.toast('Forked — next message branches from ' + entryId.slice(0, 6));
        // Reload chat so the rendered history matches the new head.
        if (typeof openChat === 'function') openChat(currentAgent.id);
    } catch (e) {
        EOS_UI.toast('Fork failed: ' + e, false);
    }
}

async function openBranchPicker() {
    var tree = await _fetchRoomTree();
    if (!tree || tree.error) return;
    var nodes = tree.nodes || [];
    var byId = {};
    nodes.forEach(function(n) { byId[n.entry_id] = n; });
    var heads = (tree.heads || []).map(function(h) { return byId[h]; }).filter(Boolean);
    if (heads.length <= 1) {
        EOS_UI.toast('Only one branch in this room', false);
        return;
    }
    var current = tree.current_head_entry_id;
    var rows = heads.map(function(h) {
        var isCurrent = h.entry_id === current;
        var marker = isCurrent ? '● ' : '○ ';
        var bg = isCurrent ? 'var(--accent-fade,var(--bg-card))' : 'var(--bg-card)';
        return '<div data-head="' + escAttr(h.entry_id) + '"' +
            ' style="display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border);border-radius:8px;margin-bottom:6px;background:' + bg + ';cursor:pointer;font-size:13px"' +
            ' onclick="_acceptFork(\'' + escAttr(h.entry_id) + '\')">' +
            '<span style="color:var(--accent);font-weight:600">' + marker + '</span>' +
            '<div style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _previewChip(h) + '</div>' +
            '<span style="color:var(--text-muted);font-size:11px">' + esc((h.ts || '').slice(11, 19)) + '</span>' +
            '</div>';
    }).join('');
    EOS_UI.modal({
        title: 'Branches (' + heads.length + ')',
        body: '<div style="margin-bottom:10px;font-size:12px;color:var(--text-muted)">' +
              '● = current head. Click to switch.' +
              '</div>' + rows,
        width: '520px',
    });
}

// Insert `@<id> ` at the chat-input cursor (or replace any partial @mention


// --- Forum mode (moderated multi-agent panel) ---
//
// Front door for apps/rooms/panel.py::run_panel. A group room with 2+ agent
// participants gets a "Forum" header button (shown by openChat). It runs:
// blind round 1 → moderator steers between rounds → synthesis. Every turn —
// agent takes, moderator notes, the final synthesis — is appended to room
// history server-side, so we just reload the thread when it finishes.

function openForumModal() {
    if (!currentAgent) return;
    var agentCount = (currentAgent.participants || []).filter(function(p){
        return p.type === 'agent';
    }).length;
    if (agentCount < 2) {
        EOS_UI.toast('Forum needs at least 2 agent participants', false);
        return;
    }
    EOS_UI.formModal('🗣 Run forum — ' + (currentAgent.name || 'room'), [
        {key: 'question', label: 'Question for the panel', type: 'textarea',
         placeholder: 'What should the panel debate? Be specific.'},
        {key: 'rounds', label: 'Rounds (1–4)', type: 'number', value: 2},
    ], runForum);
}

async function runForum(vals) {
    if (!currentAgent) return;
    var roomId = currentAgent.id;
    var q = ((vals && vals.question) || '').trim();
    if (!q) { EOS_UI.toast('Enter a question for the panel', false); return; }
    var rounds = Math.max(1, Math.min(4, parseInt(vals.rounds, 10) || 2));
    var agentCount = (currentAgent.participants || []).filter(function(p){
        return p.type === 'agent';
    }).length;

    var btn = document.getElementById('chat-forum-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '🗣 Forum…'; }
    EOS_UI.toast('Forum running — ' + agentCount + ' agents × ' + rounds +
                 ' round(s). This takes a minute…');
    try {
        var r = await EOS.api('/rooms/api/rooms/' + encodeURIComponent(roomId) + '/panel',
            {method: 'POST', body: JSON.stringify({question: q, rounds: rounds})});
        if (r && r.error) {
            EOS_UI.toast('Forum failed: ' + r.error, false);
        } else {
            var conv = r && r.converged ? ' · converged early' : '';
            EOS_UI.toast('Forum complete — ' + ((r && r.rounds) || rounds) +
                         ' round(s)' + conv);
        }
    } catch (e) {
        EOS_UI.toast('Forum failed: ' + (e && e.message ? e.message : e), false);
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '🗣 Forum'; }
        // Reload the thread so the appended takes/moderator/synthesis render —
        // but only if the user is still in the same room.
        if (currentAgent && currentAgent.id === roomId &&
            typeof openChat === 'function') {
            openChat(roomId);
        }
    }
}

// ── Autopilot per-room chip — config-aware (autopilot.py) ────────────────
// One chip slot, two meanings decided by the global stable-default flag:
//   • auto_stable OFF → ⚡ GRANT mode: toggle issues session grants so this
//     room's eligible verbs auto-run (1h TTL, countdown). /api/autopilot/session
//   • auto_stable ON  → ⏸ HOLD mode: reversible verbs already auto-run, so the
//     useful control is to PAUSE them for this room (route through the review
//     gate to watch). No TTL — persists until resumed. /api/autopilot/hold
// The gate (pending.py) consumes both grants + holds via decide(); this is the
// issuer. Globals — called from the chip's inline onclick + openChat.
var _apMode = 'grant';   // 'grant' (auto_stable off) | 'hold' (auto_stable on)
var _apExpiresMs = 0;
var _apTick = null;
function _apEls() {
    var chip = document.getElementById('btn-autopilot');
    return {
        chip: chip,
        state: chip ? chip.querySelector('.ap-state') : null,
        icon: chip ? chip.querySelector('.ap-icon') : null,
    };
}
function apRender(active, secondsLeft) {
    var e = _apEls();
    if (!e.chip || !e.state) return;
    if (e.icon) e.icon.textContent = (_apMode === 'hold') ? '⏸' : '⚡';  // ⏸ / ⚡
    e.chip.classList.toggle('on', !!active);
    if (_apMode === 'hold') {
        e.state.textContent = active ? 'auto paused' : 'pause auto';
        return;
    }
    if (!active) { e.state.textContent = 'auto-accept · off'; return; }
    if (secondsLeft === Infinity) { e.state.textContent = 'auto-accept · on'; return; }
    var m = Math.max(0, Math.floor(secondsLeft / 60));
    e.state.textContent = 'auto-accept · ' + m + 'm left';
}
function apStartTick() {
    if (_apTick) clearInterval(_apTick);
    _apTick = setInterval(function () {
        var left = Math.max(0, Math.floor((_apExpiresMs - Date.now()) / 1000));
        if (left <= 0) { apRender(false, 0); clearInterval(_apTick); _apTick = null; return; }
        apRender(true, left);
    }, 1000);
}
async function apRefresh() {
    var e = _apEls();
    if (!e.chip) return;
    if (typeof currentAgent === 'undefined' || !currentAgent || !currentAgent.id) {
        e.chip.style.display = 'none';
        return;
    }
    if (_apTick) { clearInterval(_apTick); _apTick = null; }
    try {
        var data = await EOS.api('/rooms/api/autopilot?room_id=' + encodeURIComponent(currentAgent.id));
        e.chip.style.display = '';
        if (data && data.auto_stable) {
            // HOLD mode — pause/resume auto for this room.
            _apMode = 'hold';
            e.chip.title = 'Reversible actions auto-run system-wide. Pause them for THIS room to review each one (audit + undo still apply). Click to toggle.';
            apRender(!!(data && data.held), 0);
            return;
        }
        // GRANT mode — opt this room into auto-accept.
        _apMode = 'grant';
        e.chip.title = "Auto-accept eligible low-risk actions from this room's agents for this session (audit + undo still apply). Click to toggle.";
        var grants = (data && data.grants) || [];
        if (!grants.length) { apRender(false, 0); _apExpiresMs = 0; return; }
        var expiries = grants.map(function (g) {
            return g.expires_at ? new Date(g.expires_at).getTime() : Infinity;
        });
        _apExpiresMs = Math.min.apply(null, expiries);
        if (_apExpiresMs === Infinity) { apRender(true, Infinity); return; }
        var left = Math.max(0, Math.floor((_apExpiresMs - Date.now()) / 1000));
        apRender(true, left);
        apStartTick();
    } catch (err) { console.warn('autopilot status failed', err); }
}
async function apToggle() {
    var e = _apEls();
    if (!e.chip || typeof currentAgent === 'undefined' || !currentAgent || !currentAgent.id) return;
    var isOn = e.chip.classList.contains('on');
    try {
        if (_apMode === 'hold') {
            var hd = await EOS.post('/rooms/api/autopilot/hold',
                { room_id: currentAgent.id, enabled: !isOn });
            if (!hd || !hd.ok) { EOS_UI.toast('Pause toggle failed', false); return; }
            EOS_UI.toast(isOn ? 'Auto resumed for this room' : 'Auto paused — this room’s actions now need review');
        } else {
            var data = await EOS.post('/rooms/api/autopilot/session',
                isOn ? { room_id: currentAgent.id, enabled: false }
                     : { room_id: currentAgent.id, enabled: true, ttl_s: 3600 });
            if (!data || !data.ok) { EOS_UI.toast('Auto-accept toggle failed', false); return; }
            if (!isOn) {
                var n = (data.grants || []).length;
                EOS_UI.toast(n ? ('Auto-accept on · ' + n + ' verb group' + (n !== 1 ? 's' : '') + ' · 1h')
                               : 'No eligible verbs to auto-accept');
            } else { EOS_UI.toast('Auto-accept off'); }
        }
        await apRefresh();
    } catch (err) { EOS_UI.toast('Autopilot toggle error', false); }
}
