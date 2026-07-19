// Projects — the multi-pane WORKSPACE layout (standalone /projects/workspace/<id>).
//
// Loaded ONLY by workspace.html. workspace.js's renderProjectView() calls
// renderWorkspaceLayout() (defined here); the list page never loads this file,
// so renderProjectView() is a no-op there.
//
// This is the "work surface" that makes the workspace MORE than the tab view:
//   • left rail — command-center Overview, project-grounded AI companion,
//     4D timeline, related links
//   • main pane — the existing tab suite (reuses TAB_RENDERERS / #tab-content),
//     with Docs gaining an inline read/edit editor (openDocInline)
//
// Backends are all in workspace.py (overview/ask/doc/timeline4d/related). This
// file is pure UI composition; it reuses helpers from workspace.js (typeBadge,
// progressBar, renderStageBar, getDetailTabs, TAB_RENDERERS, ALL_STATUSES,
// changeStatus, toggleTask, loadActivityHeatmap) and dialogs.js (openAddTask,
// openNewDoc, showHealth, visualizeProjectScene, openProjectSettings).

var _wsChat = [];          // AI companion history: [{role, content, provenance?}]
var _wsThinking = false;

function renderWorkspaceLayout() {
    var p = _detailProject;
    var id = p._id;
    var dv = document.getElementById('detail-view');
    var tabs = getDetailTabs(p);
    if (!tabs.find(function(t){ return t.id === _detailTab; })) {
        _detailTab = tabs.length ? tabs[0].id : 'tasks';
    }

    var tabBar = tabs.map(function(tab) {
        var count = tab.id === 'tasks' ? ' <span class="tab-count">' + (p.total_tasks||0) + '</span>'
                  : (tab.id === 'docs' ? ' <span class="tab-count" id="docs-count">…</span>' : '');
        return '<div class="detail-tab' + (_detailTab===tab.id?' active':'') + '" onclick="wsSwitchTab(\'' + tab.id + '\')">' + tab.label + count + '</div>';
    }).join('') || '<div style="font-size:12px;color:var(--text-muted);padding:8px 0">No feature tabs enabled — use ⚙ to turn some on.</div>';

    dv.innerHTML =
        '<div class="ws-head">' +
            '<div style="display:flex;align-items:center;gap:8px;min-width:0">' + typeBadge(p) +
                '<div class="detail-title" style="margin:0">' + esc(p.name) + '</div></div>' +
            '<div class="ws-actions">' +
                '<button class="eos-btn-sm" onclick="openAddTask(\'' + escAttr(id) + '\')">+ Task</button>' +
                '<button class="eos-btn-sm" onclick="openProjectWorklog(\'' + escAttr(id) + '\')">Log work</button>' +
                '<button class="eos-btn-sm eos-btn-ghost" onclick="openNewDoc(\'' + escAttr(id) + '\')">+ Doc</button>' +
                '<select class="status-select" onchange="changeStatus(\'' + escAttr(id) + '\',this.value)">' +
                    ALL_STATUSES.map(function(s){ return '<option value="' + s + '"' + (s===p.status?' selected':'') + '>' + s + '</option>'; }).join('') +
                '</select>' +
                '<button class="eos-btn-sm eos-btn-ghost" onclick="visualizeProjectScene(\'' + escAttr(id) + '\')" title="Mermaid Gantt scene" style="font-size:14px;padding:4px 8px">📊</button>' +
                '<button class="eos-btn-sm eos-btn-ghost" onclick="openProjectSettings(\'' + escAttr(id) + '\')" title="Project settings" style="font-size:14px;padding:4px 8px">⚙</button>' +
                '<span id="ws-model-pill"></span>' +
            '</div>' +
        '</div>' +
        renderStageBar(p) +
        (p.goal ? '<div class="detail-goal">' + esc(p.goal) + '</div>' : '') +
        '<div class="ws-grid">' +
            '<div class="ws-rail">' +
                '<div class="ws-panel" id="ws-overview"><div class="ws-panel-head">📊 Overview</div><div class="ws-muted">Loading…</div></div>' +
                '<div class="ws-panel" id="ws-worklog"><div class="ws-panel-head">Worklog</div><div class="ws-muted">Loading...</div></div>' +
                '<div class="ws-panel ws-chat-panel">' +
                    '<div class="ws-panel-head">🤖 Project AI</div>' +
                    '<div class="ws-chat-log" id="ws-chat-log"></div>' +
                    '<div class="ws-chat-input">' +
                        '<textarea id="ws-chat-q" rows="2" placeholder="Ask about this project… (Enter to send)" ' +
                            'onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();wsAsk();}"></textarea>' +
                        '<button class="eos-btn-sm" onclick="wsAsk()">Ask</button>' +
                    '</div>' +
                '</div>' +
                '<div class="ws-panel" id="ws-timeline"><div class="ws-panel-head">🕑 Timeline</div><div class="ws-muted">Loading…</div></div>' +
                '<div class="ws-panel" id="ws-related"><div class="ws-panel-head">🔗 Related</div><div class="ws-muted">Loading…</div></div>' +
            '</div>' +
            '<div class="ws-main">' +
                '<div class="detail-tabs">' + tabBar + '</div>' +
                '<div id="tab-content"></div>' +
            '</div>' +
        '</div>';

    // Hydrate
    var renderer = TAB_RENDERERS[_detailTab];
    if (renderer) renderer(p, id);
    wsRenderChat();
    if (typeof EOS_UI !== 'undefined' && EOS_UI.modelPill) {
        try { EOS_UI.modelPill({app: 'projects', mount: '#ws-model-pill', domain: 'text'}); } catch(e) {}
    }
    loadWsOverview(id);
    loadWsWorklog(id);
    loadWsTimeline(id);
    loadWsRelated(id);
}

async function loadWsWorklog(id) {
    var el = document.getElementById('ws-worklog');
    if (!el) return;
    try {
        var d = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/worklog?days=30&limit=6');
        if (!d.available) {
            el.innerHTML = '<div class="ws-panel-head">Worklog</div><div class="ws-muted">Worklog is unavailable.</div>';
            return;
        }
        var emoji = {complete:'&#9989;', 'in-progress':'&#128260;', review:'&#128064;', blocked:'&#9940;', waiting:'&#9203;', next:'&#9193;', todo:'&#11036;', note:'&middot;'};
        var rows = (d.items || []).map(function(item) {
            return '<div class="ws-tl-item"><span class="ws-tl-when">' + esc(item.date || '') + '</span>' +
                '<span title="' + escAttr(item.status || 'note') + '">' + (emoji[item.status] || '&middot;') + '</span> ' +
                esc(item.text || '') + '</div>';
        }).join('');
        if (!rows) rows = '<div class="ws-muted">No entries yet. Log the first update from this workspace.</div>';
        el.innerHTML = '<div class="ws-panel-head">Worklog' +
            '<a class="eos-btn-sm eos-btn-ghost" style="float:right;font-size:11px;padding:2px 8px" href="/worklog/">Open</a>' +
            '</div>' + (d.employer ? '<div class="ws-muted">Company: ' + esc(d.employer) + '</div>' :
                '<div class="ws-muted">Company not set; new entries use the Worklog default.</div>') + rows;
    } catch(e) {
        el.innerHTML = '<div class="ws-panel-head">Worklog</div><div class="ws-muted">Failed to load</div>';
    }
}

function wsSwitchTab(tab) {
    _detailTab = tab;
    document.querySelectorAll('.ws-main .detail-tab').forEach(function(el){ el.classList.remove('active'); });
    // Re-mark active by matching onclick arg is fiddly; simplest: re-find by text isn't reliable,
    // so just re-render the tab bar active state via a class toggle on click target.
    var tabs = getDetailTabs(_detailProject);
    var bar = document.querySelector('.ws-main .detail-tabs');
    if (bar) {
        bar.innerHTML = tabs.map(function(t) {
            var count = t.id === 'tasks' ? ' <span class="tab-count">' + (_detailProject.total_tasks||0) + '</span>'
                      : (t.id === 'docs' ? ' <span class="tab-count" id="docs-count">…</span>' : '');
            return '<div class="detail-tab' + (_detailTab===t.id?' active':'') + '" onclick="wsSwitchTab(\'' + t.id + '\')">' + t.label + count + '</div>';
        }).join('');
    }
    var renderer = TAB_RENDERERS[tab];
    if (renderer) renderer(_detailProject, _detailProject._id);
}

// ── Overview panel ──────────────────────────────────────────────────
async function loadWsOverview(id) {
    var el = document.getElementById('ws-overview');
    if (!el) return;
    try {
        var o = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/overview');
        if (o.error) { el.innerHTML = '<div class="ws-panel-head">📊 Overview</div><div class="ws-muted">' + esc(o.error) + '</div>'; return; }

        var deadlineHtml = '';
        if (o.deadline) {
            var dl = o.days_left;
            var cls = (dl != null && dl < 0) ? 'ws-danger' : (dl != null && dl <= 7) ? 'ws-warn' : '';
            var txt = (dl == null) ? o.deadline : (dl < 0 ? 'Overdue ' + Math.abs(dl) + 'd' : 'Due in ' + dl + 'd');
            deadlineHtml = '<div class="ws-ov-row"><span>Deadline</span><span class="' + cls + '">' + esc(txt) + '</span></div>';
        }
        var employerHtml = _detailProject.employer
            ? '<div class="ws-ov-row"><span>Company</span><span>' + esc(_detailProject.employer) + '</span></div>'
            : '';

        var moves = (o.next_actions || []).map(function(t) {
            return '<div class="ws-move" ' +
                'onclick="toggleTask(\'' + escAttr(id) + '\',' + t.line + ',' + escAttr(JSON.stringify(t.text || '')) + ')" ' +
                'oncontextmenu="event.preventDefault();completeProjectTaskNote(\'' + escAttr(id) + '\',' + t.line + ',' + escAttr(JSON.stringify(t.text || '')) + ');return false" ' +
                'title="Click to mark done · right-click to complete with a note">' +
                '<span class="ws-move-check">☐</span>' + esc(t.text) + '</div>';
        }).join('') || '<div class="ws-muted">No open tasks 🎉</div>';

        var sprintHtml = o.active_sprint
            ? '<div class="ws-ov-row"><span>Sprint</span><span>' + esc(o.active_sprint.name || ('#' + o.active_sprint.num)) + '</span></div>'
            : '';
        var noteHtml = o.next_action_note
            ? '<div class="ws-ov-note">▶ ' + esc(o.next_action_note) + '</div>' : '';

        el.innerHTML =
            '<div class="ws-panel-head">📊 Overview' +
                '<button class="eos-btn-sm eos-btn-ghost" style="float:right;font-size:11px;padding:2px 8px" onclick="wsHealth(\'' + escAttr(id) + '\')">Health</button>' +
            '</div>' +
            '<div class="ws-ov-row"><span>Status</span><span class="eos-badge eos-badge-' + EOS_UI.statusVariant(o.status) + '">' + esc(o.status) + '</span></div>' +
            employerHtml +
            deadlineHtml +
            '<div class="ws-ov-row"><span>Progress</span><span>' + o.progress + '%</span></div>' +
            '<div class="progress-bar" style="margin:2px 0 8px"><div class="progress-fill' + (o.progress===100?' done':'') + '" style="width:' + o.progress + '%"></div></div>' +
            '<div class="ws-ov-chips">' +
                '<span class="ws-chip ws-chip-ok">' + (o.ready_count||0) + ' ready</span>' +
                (o.has_dependencies ? '<span class="ws-chip ws-chip-bad">' + (o.blocked_count||0) + ' blocked</span>' : '') +
                '<span class="ws-chip">' + (o.open||0) + ' open · ' + (o.done||0) + ' done</span>' +
            '</div>' +
            sprintHtml +
            noteHtml +
            '<div id="ws-health" class="ws-health"></div>' +
            '<div class="ws-ov-sub">Next moves</div>' +
            moves +
            '<div class="ws-ov-sub">Activity</div>' +
            '<div id="detail-activity"></div>';
        loadActivityHeatmap(id);
    } catch(e) {
        el.innerHTML = '<div class="ws-panel-head">📊 Overview</div><div class="ws-muted">Failed to load</div>';
    }
}

async function wsHealth(id) {
    var el = document.getElementById('ws-health');
    if (!el) return;
    el.innerHTML = '<div class="ws-muted">Analyzing…</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/health', {timeout: 60000});
        var prov = '';
        if (r.provenance && r.provenance.mode && typeof EOS_UI !== 'undefined' && EOS_UI.provenance) {
            prov = '<div style="margin-top:4px">' + EOS_UI.provenance(r.provenance) + '</div>';
        }
        el.innerHTML = '<div class="ws-health-box">' + esc(r.health || 'No assessment') + prov + '</div>';
    } catch(e) {
        el.innerHTML = '<div class="ws-muted">AI unavailable</div>';
    }
}

// ── AI companion ────────────────────────────────────────────────────
function wsRenderChat() {
    var log = document.getElementById('ws-chat-log');
    if (!log) return;
    if (!_wsChat.length && !_wsThinking) {
        log.innerHTML = '<div class="ws-muted" style="padding:8px">Ask anything about this project — its tasks, blockers, decisions, or "what should I do next?"</div>';
        return;
    }
    var html = _wsChat.map(function(m) {
        if (m.role === 'user') {
            return '<div class="ws-msg ws-msg-user">' + esc(m.content) + '</div>';
        }
        // The in-flight streaming turn gets a stable body node so paintMarkdown
        // can repaint just that node per chunk instead of re-rendering the log.
        if (m.streaming) {
            return '<div class="ws-msg ws-msg-ai"><div id="ws-stream-body"></div></div>';
        }
        var bodyHtml = (typeof EOS_UI !== 'undefined' && EOS_UI.renderMarkdown) ? EOS_UI.renderMarkdown(m.content) : esc(m.content);
        var prov = (m.provenance && m.provenance.mode && EOS_UI.provenance) ? '<div class="ws-msg-prov">' + EOS_UI.provenance(m.provenance) + '</div>' : '';
        return '<div class="ws-msg ws-msg-ai">' + bodyHtml + prov + '</div>';
    }).join('');
    if (_wsThinking) html += '<div class="ws-msg ws-msg-ai ws-muted">Thinking…</div>';
    log.innerHTML = html;
    log.scrollTop = log.scrollHeight;
}

async function wsAsk() {
    var ta = document.getElementById('ws-chat-q');
    if (!ta) return;
    var q = ta.value.trim();
    if (!q || _wsThinking) return;
    ta.value = '';
    _wsChat.push({role: 'user', content: q});
    _wsThinking = true;
    wsRenderChat();
    var id = _detailProject._id;
    var history = _wsChat.slice(0, -1).map(function(m){ return {role: m.role, content: m.content}; });
    // Streaming: append an AI turn flagged `streaming` (renders a stable
    // #ws-stream-body node), then paint accumulated markdown into just that
    // node per chunk via EOS_UI.paintMarkdown — no whole-log re-render.
    var aiMsg = {role: 'assistant', content: '', streaming: true};
    function paint() {
        var node = document.getElementById('ws-stream-body');
        if (!node) return;
        if (typeof EOS_UI !== 'undefined' && EOS_UI.paintMarkdown) EOS_UI.paintMarkdown(node, aiMsg.content);
        else node.textContent = aiMsg.content;
        var log = document.getElementById('ws-chat-log');
        if (log) log.scrollTop = log.scrollHeight;
    }
    try {
        var resp = await fetch('/projects/api/projects/' + encodeURIComponent(id) + '/ask-stream', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question: q, history: history})
        });
        if (!resp.ok || !resp.body) throw new Error('stream unavailable');
        _wsThinking = false;
        _wsChat.push(aiMsg);
        wsRenderChat();
        var reader = resp.body.getReader();
        var dec = new TextDecoder();
        var buf = '';
        var sawText = false;
        while (true) {
            var step = await reader.read();
            if (step.done) break;
            buf += dec.decode(step.value, {stream: true});
            var nl;
            while ((nl = buf.indexOf('\n')) >= 0) {
                var line = buf.slice(0, nl).trim();
                buf = buf.slice(nl + 1);
                if (!line) continue;
                var msg;
                try { msg = JSON.parse(line); } catch(_) { continue; }
                if (msg.type === 'chunk') { aiMsg.content += (msg.text || ''); sawText = true; paint(); }
                else if (msg.type === 'error') { aiMsg.content = '_' + (msg.error || 'AI error') + '_'; paint(); }
                else if (msg.type === 'done') { aiMsg.provenance = msg.provenance; }
            }
        }
        if (!sawText && !aiMsg.content) aiMsg.content = '_(no answer)_';
    } catch(e) {
        _wsThinking = false;
        if (!_wsChat.length || _wsChat[_wsChat.length - 1] !== aiMsg) _wsChat.push(aiMsg);
        aiMsg.content = aiMsg.content || '_AI request failed_';
    }
    // Finalise: drop the streaming flag so the turn renders normally (markdown
    // body + provenance chip) in one last full render.
    aiMsg.streaming = false;
    wsRenderChat();
}

// ── Timeline panel (4D: past / future / now) ────────────────────────
async function loadWsTimeline(id) {
    var el = document.getElementById('ws-timeline');
    if (!el) return;
    try {
        var t = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/timeline4d');
        function items(list) {
            if (!list || !list.length) return '<div class="ws-muted">—</div>';
            return list.slice(0, 6).map(function(it) {
                var when = it.date || it.when || it.ts || it.due || '';
                var label = it.label || it.text || it.title || it.summary || it.subject || JSON.stringify(it).slice(0, 60);
                return '<div class="ws-tl-item">' + (when ? '<span class="ws-tl-when">' + esc(String(when).slice(0,10)) + '</span>' : '') + esc(String(label)) + '</div>';
            }).join('');
        }
        el.innerHTML =
            '<div class="ws-panel-head">🕑 Timeline</div>' +
            '<div class="ws-tl-sub">Past</div>' + items(t.past) +
            '<div class="ws-tl-sub">Upcoming</div>' + items(t.future);
    } catch(e) {
        el.innerHTML = '<div class="ws-panel-head">🕑 Timeline</div><div class="ws-muted">—</div>';
    }
}

// ── Related panel ───────────────────────────────────────────────────
async function loadWsRelated(id) {
    var el = document.getElementById('ws-related');
    if (!el) return;
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/related');
        var projs = (r.related_projects || []).map(function(p) {
            return '<a class="ws-link" href="/projects/workspace/' + encodeURIComponent(p.id) + '">📁 ' + esc(p.name) + '</a>';
        }).join('');
        var links = (r.links || []).map(function(l) {
            return '<span class="ws-link ws-link-wiki">🔗 ' + esc(l.target) + '</span>';
        }).join('');
        var body = (projs || links) ? (projs + links) : '<div class="ws-muted">No related notes yet. Add a <code>related:</code> field or [[wikilinks]] in the project note.</div>';
        el.innerHTML = '<div class="ws-panel-head">🔗 Related</div><div class="ws-links">' + body + '</div>';
    } catch(e) {
        el.innerHTML = '<div class="ws-panel-head">🔗 Related</div><div class="ws-muted">—</div>';
    }
}

// ── Inline doc editor (hook used by tabs.js loadDocsTab) ────────────
async function openDocInline(projectId, rel, name) {
    var tc = document.getElementById('tab-content');
    if (!tc) return;
    tc.innerHTML = '<div class="ws-muted" style="padding:8px">Loading ' + esc(name) + '…</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(projectId) + '/doc?path=' + encodeURIComponent(rel));
        if (r.error) { tc.innerHTML = '<div class="eos-empty">' + esc(r.error) + '</div>'; return; }
        tc.innerHTML =
            '<div class="ws-doc-bar">' +
                '<button class="eos-btn-sm eos-btn-ghost" onclick="wsCloseDoc(\'' + escAttr(projectId) + '\')">← Docs</button>' +
                '<span class="ws-doc-name">' + esc(name) + '</span>' +
                '<span style="flex:1"></span>' +
                '<button class="eos-btn-sm eos-btn-ghost" id="ws-doc-toggle" onclick="wsToggleDocPreview()">👁 Preview</button>' +
                '<button class="eos-btn-sm" onclick="wsSaveDoc(\'' + escAttr(projectId) + '\',\'' + escAttr(rel) + '\')">Save</button>' +
            '</div>' +
            '<textarea class="ws-doc-edit" id="ws-doc-body">' + esc(r.body || '') + '</textarea>' +
            '<div class="ws-doc-preview" id="ws-doc-preview" style="display:none"></div>';
    } catch(e) {
        tc.innerHTML = '<div class="eos-empty">Failed to load document</div>';
    }
}

function wsToggleDocPreview() {
    var ed = document.getElementById('ws-doc-body');
    var pv = document.getElementById('ws-doc-preview');
    var btn = document.getElementById('ws-doc-toggle');
    if (!ed || !pv) return;
    if (pv.style.display === 'none') {
        pv.innerHTML = (typeof EOS_UI !== 'undefined' && EOS_UI.renderMarkdown) ? EOS_UI.renderMarkdown(ed.value) : esc(ed.value);
        pv.style.display = ''; ed.style.display = 'none';
        if (btn) btn.textContent = '✎ Edit';
    } else {
        pv.style.display = 'none'; ed.style.display = '';
        if (btn) btn.textContent = '👁 Preview';
    }
}

async function wsSaveDoc(projectId, rel) {
    var ed = document.getElementById('ws-doc-body');
    if (!ed) return;
    try {
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/doc',
            {path: rel, body: ed.value});
        if (r.error) { EOS_UI.toast(r.error, false); return; }
        EOS_UI.toast('Saved ' + (r.name || 'document'));
    } catch(e) {
        EOS_UI.toast('Failed to save', false);
    }
}

function wsCloseDoc(projectId) {
    if (typeof loadDocsTab === 'function') loadDocsTab(projectId);
}
