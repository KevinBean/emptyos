// Projects — core view state, card/view rendering, filters, timeline/calendar/stats, detail frame.
// Tab content loaders live in tabs.js; modal/dialog code lives in dialogs.js.

var allProjects = [];
var currentCategory = '';  // tag-based category filter ('' = all)
var CATEGORY_RESERVED = {'project': 1, 'inbox': 1};  // baseline tags, not useful as categories

function projectTags(p) {
    if (!p || !p.tags) return [];
    if (Array.isArray(p.tags)) return p.tags.map(function(t){return String(t).toLowerCase().trim()}).filter(Boolean);
    return String(p.tags).toLowerCase().split(/[,\s]+/).map(function(t){return t.trim()}).filter(Boolean);
}

function collectCategories() {
    var counts = {};
    allProjects.forEach(function(p) {
        if (!showArchived && p.status === 'archived') return;
        projectTags(p).forEach(function(t) {
            if (CATEGORY_RESERVED[t]) return;
            counts[t] = (counts[t] || 0) + 1;
        });
    });
    return counts;
}

function renderCategoryBar() {
    var bar = document.getElementById('category-bar');
    if (!bar) return;
    var counts = collectCategories();
    var tags = Object.keys(counts).sort(function(a,b){return counts[b]-counts[a] || a.localeCompare(b)});
    var total = (showArchived ? allProjects : allProjects.filter(function(p){return p.status!=='archived'})).length;
    if (!tags.length) { bar.innerHTML = ''; bar.style.display = 'none'; return; }
    bar.style.display = 'flex';
    function chip(label, value, count, active) {
        var bg = active ? 'var(--accent)' : 'var(--bg-card)';
        var fg = active ? 'var(--accent-ink)' : 'var(--text-secondary)';
        var bd = active ? 'var(--accent)' : 'var(--border)';
        return '<span onclick="setCategory(\'' + escAttr(value) + '\')" style="cursor:pointer;padding:4px 10px;border-radius:999px;font-size:12px;background:' + bg + ';color:' + fg + ';border:1px solid ' + bd + '">' +
               esc(label) + ' <span style="opacity:0.7">(' + count + ')</span></span>';
    }
    var html = chip('All', '', total, currentCategory === '');
    tags.forEach(function(t) { html += chip(t, t, counts[t], currentCategory === t); });
    bar.innerHTML = html;
}

function setCategory(tag) {
    currentCategory = tag || '';
    renderCategoryBar();
    filterProjects();
}
var currentView = localStorage.getItem('eos-projects-view') || 'kanban';
var showArchived = false;
var filteredProjects = [];
var typeConfig = {};
var toolsByType = {};
var featureRegistry = {};

var ALL_STATUSES = ['idea', 'active', 'blocked', 'shelved', 'completed', 'archived'];
var STATUS_LABELS = { idea: 'Ideas', active: 'Active', blocked: 'Blocked', shelved: 'Shelved', completed: 'Done', archived: 'Archived' };
function getStatusOrder() { return showArchived ? ALL_STATUSES : ALL_STATUSES.filter(function(s){return s!=='archived'}); }

function toggleArchived() {
    showArchived = !showArchived;
    document.getElementById('btn-archive').textContent = showArchived ? 'Hide Archived' : 'Show Archived';
    renderCategoryBar();
    filterProjects();
}

// Open the project-tracker preset as a board. Idempotent: creates the board on
// first click, opens it directly on every subsequent click.
async function openAsBoard() {
    try {
        var j = await EOS.post('/boards/api/boards/from-preset', {preset_id: 'project-tracker'});
        if (j && j.id) {
            window.location.href = '/boards/#' + encodeURIComponent(j.id);
        } else {
            EOS_UI.toast(j && j.error ? j.error : 'Could not open board', false);
        }
    } catch (e) {
        EOS_UI.toast('Could not open board: ' + e, false);
    }
}

function setView(v) {
    currentView = v;
    localStorage.setItem('eos-projects-view', v);
    ['kanban','list','timeline','calendar'].forEach(function(b) {
        var el = document.getElementById('btn-' + b);
        if (el) el.classList.toggle('active', v === b);
    });
    renderView();
}

function deadlineLabel(p) {
    if (!p.deadline) return '';
    var d = p.days_until_deadline;
    if (d === null) return '';
    if (d < 0) return '<span class="project-deadline overdue">Overdue ' + Math.abs(d) + 'd</span>';
    if (d <= 7) return '<span class="project-deadline soon">Due in ' + d + 'd</span>';
    return '<span class="project-deadline">' + p.deadline + '</span>';
}

function progressBar(p) {
    if (p.total_tasks === 0) return '';
    var cls = p.progress === 100 ? 'progress-fill done' : 'progress-fill';
    return '<div class="progress-bar"><div class="' + cls + '" style="width:' + p.progress + '%"></div></div>';
}

function typeBadge(p) {
    if (!p.type || p.type === 'personal') return '';
    return '<span class="type-badge type-' + p.type + '">' + p.type + '</span> ';
}

function stageLabel(p) {
    if (!p.stage || !p.type || p.type === 'personal') return '';
    var tc = (typeConfig[p.type] || {}).labels || {};
    return '<span style="font-size:10px;color:var(--text-muted)">' + (tc[p.stage] || p.stage) + '</span>';
}

function projectCardHtml(p) {
    var metaBits = [];
    if (p.total_tasks > 0) metaBits.push('<span>' + p.done_tasks + '/' + p.total_tasks + ' tasks</span>');
    var sl = stageLabel(p); if (sl) metaBits.push(sl);
    var dl = deadlineLabel(p); if (dl) metaBits.push(dl);
    var badges = [];
    if (p.type && p.type !== 'personal') badges.push({label: p.type, variant: 'neutral'});
    var body = progressBar(p);
    return EOS_UI.entityCard({
        title: p.name,
        badges: badges,
        meta: metaBits.join(''),
        body: body || undefined,
        onClick: "showDetail('" + escAttr(p.id) + "')",
    });
}

// Status → eos-pill palette. Mirrors the col-* CSS at the top of index.html so
// the kanban header pill matches the per-status accent users expect.
var STATUS_COLORS = {
    idea: 'gray', active: 'green', blocked: 'red',
    shelved: 'amber', completed: 'purple', archived: 'gray',
};

function renderKanban(projects) {
    var order = getStatusOrder();
    var groups = order.map(function(s) {
        return {key: s, label: STATUS_LABELS[s], color: STATUS_COLORS[s] || 'gray'};
    });
    var mount = document.getElementById('main-view');
    mount.innerHTML = '<div id="projects-kanban-mount"' + (showArchived ? ' data-show-archived="true"' : '') + '></div>';
    EOS_UI.kanbanLayout({
        mountId: 'projects-kanban-mount',
        items: projects,
        groups: groups,
        getGroup: function(p) { return p.status; },
        getItemId: function(p) { return p.id; },
        wrapCards: false,                       // entityCard already provides the card surface
        renderCard: projectCardHtml,
        onMove: async function(p, newStatus) {
            try {
                await EOS.post('/projects/api/projects/' + encodeURIComponent(p.id) + '/status',
                               {status: newStatus});
                p.status = newStatus;            // optimistic; reload syncs anyway
                EOS_UI.toast('Moved to ' + STATUS_LABELS[newStatus]);
                await load();                    // refresh stats + columns
            } catch (e) {
                EOS_UI.toast('Failed to move project: ' + e, false);
            }
        },
    });
}

function renderList(projects) {
    var html = '<div class="project-list">';
    html += projects.map(function(p) {
        var metaBits = [];
        if (p.total_tasks > 0) metaBits.push('<span>' + p.done_tasks + '/' + p.total_tasks + ' tasks (' + p.progress + '%)</span>');
        else metaBits.push('<span>No tasks</span>');
        var dl = deadlineLabel(p); if (dl) metaBits.push(dl);
        if (p.stale_days > 30) metaBits.push('<span style="color:var(--warning)">Stale ' + p.stale_days + 'd</span>');
        return EOS_UI.entityCard({
            title: p.name,
            badges: [{label: p.status, variant: 'status-' + p.status}],
            meta: metaBits.join(''),
            body: progressBar(p) || undefined,
            onClick: "showDetail('" + escAttr(p.id) + "')",
            className: 'list-card',
        });
    }).join('');
    html += '</div>';
    document.getElementById('main-view').innerHTML = html || '<div class="eos-empty">No projects</div>';
}

function renderView() {
    if (currentView === 'kanban') renderKanban(filteredProjects);
    else if (currentView === 'list') renderList(filteredProjects);
    else if (currentView === 'timeline') loadTimeline();
    else if (currentView === 'calendar') loadCalendar();
}

// --- Timeline View ---
var _timelineData = null;

async function loadTimeline() {
    var mv = document.getElementById('main-view');
    mv.innerHTML = '<div class="eos-empty">Loading timeline...</div>';
    try {
        _timelineData = await EOS.api('/projects/api/timeline');
        renderTimeline(_timelineData);
    } catch(e) {
        mv.innerHTML = '<div class="eos-empty">Failed to load timeline</div>';
    }
}

function renderTimeline(data) {
    var projects = data.projects;
    var rangeMin = new Date(data.range.min + 'T00:00:00');
    var rangeMax = new Date(data.range.max + 'T00:00:00');
    var today = new Date(data.today + 'T00:00:00');
    var totalDays = Math.max(1, (rangeMax - rangeMin) / 86400000);

    function dateToX(dateStr) {
        var d = new Date(dateStr + 'T00:00:00');
        return Math.max(0, Math.min(100, ((d - rangeMin) / 86400000 / totalDays) * 100));
    }
    var todayX = dateToX(data.today);

    var months = [];
    var cursor = new Date(rangeMin);
    cursor.setDate(1);
    while (cursor <= rangeMax) {
        months.push(cursor.toLocaleDateString('en', {month: 'short', year: '2-digit'}));
        cursor.setMonth(cursor.getMonth() + 1);
    }

    var statusColors = {idea:'var(--text-muted)',active:'var(--success)',blocked:'var(--danger)',shelved:'var(--warning)',completed:'var(--accent)'};
    var typeColors = {personal:'var(--accent)',engineering:'var(--warning)',development:'#3b82f6'};

    var html = '<div class="timeline-container">';
    html += '<div class="timeline-axis">' + months.map(function(m) { return '<div class="timeline-month">' + m + '</div>'; }).join('') + '</div>';
    html += '<div style="position:relative">';
    html += '<div class="timeline-today" style="left:calc(160px + ' + todayX + '% * (100% - 160px) / 100)"></div>';

    projects.sort(function(a,b) { return a.start < b.start ? -1 : 1; });
    projects.forEach(function(p) {
        var left = dateToX(p.start);
        var right = dateToX(p.end);
        var width = Math.max(1, right - left);
        var color = statusColors[p.status] || 'var(--accent)';
        var borderColor = typeColors[p.type] || 'var(--accent)';

        html += '<div class="timeline-row">' +
            '<div class="timeline-label" onclick="showDetail(\'' + escAttr(p.id) + '\')" title="' + escAttr(p.name) + '">' +
                typeBadge(p) + esc(p.name) +
            '</div>' +
            '<div class="timeline-bar-area">' +
                '<div class="timeline-bar" style="left:' + left + '%;width:' + width + '%;background:' + color + ';border:1px solid ' + borderColor + '" ' +
                    'onclick="showDetail(\'' + escAttr(p.id) + '\')" title="' + escAttr(p.name) + ' (' + p.progress + '%)">' +
                    '<div class="timeline-fill" style="width:' + p.progress + '%;background:' + color + '"></div>' +
                '</div>' +
            '</div>' +
        '</div>';
    });
    html += '</div></div>';
    document.getElementById('main-view').innerHTML = html;
}

// --- Calendar View ---
var _calendarMonth = new Date().toISOString().slice(0,7);

async function loadCalendar() {
    var mv = document.getElementById('main-view');
    mv.innerHTML = '<div class="eos-empty">Loading calendar...</div>';
    try {
        var data = await EOS.api('/projects/api/calendar?month=' + _calendarMonth);
        renderCalendar(data);
    } catch(e) {
        mv.innerHTML = '<div class="eos-empty">Failed to load calendar</div>';
    }
}

function renderCalendar(data) {
    var parts = _calendarMonth.split('-');
    var year = parseInt(parts[0]), month = parseInt(parts[1]);
    var firstDay = new Date(year, month - 1, 1);
    var lastDay = new Date(year, month, 0);
    var startDow = (firstDay.getDay() + 6) % 7;
    var daysInMonth = lastDay.getDate();
    var todayStr = new Date().toISOString().slice(0,10);
    var monthLabel = firstDay.toLocaleDateString('en', {month: 'long', year: 'numeric'});

    var typeColors = {personal:'var(--accent)',engineering:'var(--warning)',development:'#3b82f6'};

    var html = '<div class="cal-header">' +
        '<button class="cal-nav" onclick="calNav(-1)">&lt;</button>' +
        '<div style="font-size:16px;font-weight:600">' + monthLabel + '</div>' +
        '<button class="cal-nav" onclick="calNav(1)">&gt;</button>' +
    '</div>';

    html += '<div class="cal-grid">';
    ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(function(d) {
        html += '<div class="cal-day-header">' + d + '</div>';
    });

    for (var i = 0; i < startDow; i++) html += '<div class="cal-cell empty"></div>';

    for (var d = 1; d <= daysInMonth; d++) {
        var dateStr = _calendarMonth + '-' + String(d).padStart(2, '0');
        var isToday = dateStr === todayStr;
        var tasks = data.calendar[dateStr] || [];

        html += '<div class="cal-cell' + (isToday ? ' today' : '') + '">';
        html += '<div class="cal-date">' + d + '</div>';
        tasks.slice(0, 3).forEach(function(t) {
            var color = typeColors[t.type] || 'var(--accent)';
            var cls = 'cal-task' + (t.is_deadline ? ' is-deadline' : '');
            html += '<div class="' + cls + '" onclick="showDetail(\'' + escAttr(t.project_id) + '\')">' +
                '<span class="cal-dot" style="background:' + color + '"></span>' +
                esc(t.task.substring(0, 25)) +
            '</div>';
        });
        if (tasks.length > 3) html += '<div style="font-size:9px;color:var(--text-muted)">+' + (tasks.length - 3) + ' more</div>';
        html += '</div>';
    }

    var totalCells = startDow + daysInMonth;
    var remainder = totalCells % 7;
    if (remainder > 0) for (var i = 0; i < 7 - remainder; i++) html += '<div class="cal-cell empty"></div>';

    html += '</div>';
    document.getElementById('main-view').innerHTML = html;
}

function calNav(delta) {
    var parts = _calendarMonth.split('-');
    var d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1 + delta, 1);
    _calendarMonth = d.toISOString().slice(0, 7);
    loadCalendar();
}

var _portfolioStats = null;

async function renderStats(projects) {
    var total = projects.length;
    var active = projects.filter(function(p) { return p.status === 'active'; }).length;
    var overdue = projects.filter(function(p) { return p.overdue; }).length;
    var totalTasks = projects.reduce(function(s, p) { return s + p.total_tasks; }, 0);
    var doneTasks = projects.reduce(function(s, p) { return s + p.done_tasks; }, 0);
    var pct = totalTasks > 0 ? Math.round(doneTasks / totalTasks * 100) : 0;

    var byType = {};
    projects.forEach(function(p) { var t = p.type || 'personal'; byType[t] = (byType[t]||0) + 1; });
    var typeStr = Object.keys(byType).map(function(t) { return t + ': ' + byType[t]; }).join(', ');

    document.getElementById('stats-row').innerHTML =
        '<div class="eos-hero-card card-in"><div class="eos-hero-label">Portfolio Health</div><div id="health-ring" style="height:60px;display:flex;align-items:center;justify-content:center"><span class="eos-hero-val">...</span></div></div>' +
        '<div class="eos-hero-card card-in"><div class="eos-hero-label">Projects</div><div class="eos-hero-val">' + total + '</div><div class="eos-hero-sub">' + active + ' active · ' + typeStr + '</div></div>' +
        '<div class="eos-hero-card card-in"><div class="eos-hero-label">Tasks</div><div class="eos-hero-val">' + doneTasks + '<span style="font-size:16px;color:var(--text-muted)">/' + totalTasks + '</span></div><div class="eos-hero-sub">' + pct + '% complete</div></div>' +
        '<div class="eos-hero-card card-in"><div class="eos-hero-label">Overdue</div><div class="eos-hero-val" style="color:' + (overdue > 0 ? 'var(--danger)' : 'var(--success)') + '">' + overdue + '</div></div>' +
        '<div class="eos-hero-card card-in"><div class="eos-hero-label">Velocity</div><div class="eos-hero-val" id="velocity-val">...</div><div class="eos-hero-sub">tasks / 7 days</div></div>';

    try {
        var [health, stats] = await Promise.all([
            EOS.api('/projects/api/portfolio-health'),
            EOS.api('/projects/api/stats'),
        ]);
        _portfolioStats = stats;
        if (typeof EOS_UI !== 'undefined' && EOS_UI.ring) {
            EOS_UI.ring('health-ring', health.score, health.max, health.score > 70 ? 'var(--success)' : health.score > 40 ? 'var(--warning)' : 'var(--danger)');
        } else {
            document.getElementById('health-ring').innerHTML = '<span class="eos-hero-val" style="color:' + (health.score > 70 ? 'var(--success)' : 'var(--warning)') + '">' + health.score + '</span>';
        }
        document.getElementById('velocity-val').textContent = stats.velocity_7d || 0;
    } catch(e) {}
}

function filterProjects(q) {
    q = q || document.getElementById('search').value.toLowerCase();
    var base = showArchived ? allProjects : allProjects.filter(function(p){return p.status!=='archived'});
    if (currentCategory) {
        base = base.filter(function(p) { return projectTags(p).indexOf(currentCategory) !== -1; });
    }
    if (!q) { filteredProjects = base; }
    else {
        filteredProjects = base.filter(function(p) {
            return p.name.toLowerCase().includes(q) ||
                projectTags(p).join(' ').includes(q) ||
                p.status.toLowerCase().includes(q);
        });
    }
    renderView();
}

var _detailProject = null;
var _detailTab = 'tasks';

async function showDetail(id) {
    _route.set(id);
    document.getElementById('main-view').style.display = 'none';
    var dv = document.getElementById('detail-view');
    dv.style.display = 'block';
    dv.innerHTML = '<div class="eos-empty">Loading...</div>';
    _detailTab = 'tasks';

    try {
        var p = await EOS.api('/projects/api/projects/' + encodeURIComponent(id));
        if (p.error) { dv.innerHTML = '<div class="eos-empty">' + esc(p.error) + '</div>'; return; }
        _detailProject = p;
        _detailProject._id = id;
        renderDetail();
    } catch(e) {
        dv.innerHTML = '<div class="eos-empty">Failed to load project</div>';
    }
}

function renderStageBar(p) {
    if (!p.features || !p.features.stages) return '';
    var tc = typeConfig[p.type];
    if (!tc || !tc.stages || !tc.stages.length) return '';
    var currentIdx = tc.stages.indexOf(p.stage);
    var html = '<div class="stage-bar">';
    tc.stages.forEach(function(s, i) {
        var cls = 'stage-step';
        if (i < currentIdx) cls += ' completed';
        else if (i === currentIdx) cls += ' current';
        html += '<div class="' + cls + '" onclick="changeStage(\'' + escAttr(p._id) + '\',\'' + s + '\')" style="cursor:pointer" title="Click to set stage">' + (tc.labels[s] || s) + '</div>';
    });
    html += '</div>';
    return html;
}

function getDetailTabs(p) {
    var tabs = [];
    var fr = featureRegistry || {};
    Object.keys(fr).forEach(function(fid) {
        var fdef = fr[fid];
        if (fdef.tab && p.features && p.features[fid]) {
            tabs.push({id: fid, label: fdef.label, order: fdef.order});
        }
    });
    tabs.sort(function(a, b) { return a.order - b.order; });
    return tabs;
}

var TAB_RENDERERS = {
    tasks: function(p, id) { renderTasksTab(p, id); },
    docs: function(p, id) { loadDocsTab(id); },
    tools: function(p, id) { loadToolsTab(p, id); },
    calculations: function(p, id) { loadCalcsTab(id); },
    code: function(p, id) { loadCodeTab(p, id); },
    sprints: function(p, id) { loadSprintsTab(p, id); },
    milestones: function(p, id) { loadMilestonesTab(p, id); },
    releases: function(p, id) { loadReleasesTab(p, id); },
};

function renderDetail() {
    var p = _detailProject;
    var id = p._id;
    var dv = document.getElementById('detail-view');
    var tabs = getDetailTabs(p);

    if (!tabs.find(function(t){ return t.id === _detailTab; })) {
        _detailTab = tabs.length ? tabs[0].id : 'tasks';
    }

    dv.innerHTML =
        '<div class="detail-back" onclick="hideDetail()">← Back to projects</div>' +
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">' +
            '<div style="display:flex;align-items:center;gap:8px">' + typeBadge(p) + '<div class="detail-title">' + esc(p.name) + '</div></div>' +
            '<div style="display:flex;gap:6px;align-items:center">' +
                '<select class="status-select" onchange="changeStatus(\'' + escAttr(id) + '\',this.value)">' +
                    ALL_STATUSES.map(function(s) { return '<option value="' + s + '"' + (s===p.status?' selected':'') + '>' + s + '</option>'; }).join('') +
                '</select>' +
                '<button class="eos-btn-sm eos-btn-ghost" onclick="showHealth(\'' + escAttr(id) + '\')">Health</button>' +
                '<button class="eos-btn-sm eos-btn-ghost" onclick="openProjectSettings(\'' + escAttr(id) + '\')" title="Project settings" style="font-size:14px;padding:4px 8px">⚙</button>' +
            '</div>' +
        '</div>' +
        renderStageBar(p) +
        '<div style="display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap">' +
            '<span class="eos-badge eos-badge-status-' + p.status + '">' + p.status + '</span>' +
            (p.deadline ? '<span style="font-size:12px;color:var(--text-secondary)">Deadline: ' + p.deadline + '</span>' : '') +
            (p.total_tasks > 0 ? '<span style="font-size:12px;color:var(--text-secondary)">' + p.progress + '% complete</span>' : '') +
        '</div>' +
        (p.goal ? '<div class="detail-goal">' + esc(p.goal) + '</div>' : '') +
        progressBar(p) +
        '<div id="detail-activity" style="margin:12px 0"></div>' +
        '<div class="detail-tabs">' +
            tabs.map(function(tab) {
                var count = tab.id === 'tasks' ? ' <span class="tab-count">' + (p.total_tasks||0) + '</span>' : (tab.id === 'docs' ? ' <span class="tab-count" id="docs-count">…</span>' : '');
                return '<div class="detail-tab' + (_detailTab===tab.id?' active':'') + '" onclick="switchTab(\'' + tab.id + '\')">' + tab.label + count + '</div>';
            }).join('') +
        '</div>' +
        '<div id="tab-content"></div>';

    var renderer = TAB_RENDERERS[_detailTab];
    if (renderer) renderer(p, id);

    loadActivityHeatmap(id);
}

async function loadActivityHeatmap(projectId) {
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(projectId) + '/activity');
        var el = document.getElementById('detail-activity');
        if (!el) return;
        if (!r.activity || !Object.keys(r.activity).length) {
            el.innerHTML = '';
            return;
        }
        el.innerHTML = '<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">Activity (90 days)</div><div id="activity-heatmap"></div>';
        if (typeof EOS_UI !== 'undefined' && EOS_UI.heatmap) {
            EOS_UI.heatmap('activity-heatmap', r.activity, 90);
        }
    } catch(e) {}
}

function switchTab(tab) {
    _detailTab = tab;
    renderDetail();
}

function hideDetail() {
    document.getElementById('detail-view').style.display = 'none';
    document.getElementById('main-view').style.display = 'block';
    _detailProject = null;
    _route.clear();
}

var _route = EOS_UI.hashRoute({
    onShow: function(id) { showDetail(id); },
    onHide: function() {
        document.getElementById('detail-view').style.display = 'none';
        document.getElementById('main-view').style.display = 'block';
        _detailProject = null;
    },
});

async function toggleTask(projectId, line) {
    try {
        await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/tasks/toggle', { line: line });
        showDetail(projectId);
        EOS_UI.toast('Task toggled');
    } catch(e) {
        EOS_UI.toast('Failed to toggle', false);
    }
}

async function changeStatus(projectId, status) {
    try {
        await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/status', { status: status });
        EOS_UI.toast('Status updated to ' + status);
        load();
        showDetail(projectId);
    } catch(e) {
        EOS_UI.toast('Failed to update status', false);
    }
}

async function changeStage(projectId, stage) {
    try {
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/stage', { stage: stage });
        if (r.error) { EOS_UI.toast(r.error, false); return; }
        EOS_UI.toast('Stage: ' + stage);
        _detailProject.stage = stage;
        renderDetail();
    } catch(e) {
        EOS_UI.toast('Failed to update stage', false);
    }
}

async function load() {
    try {
        var [projects, tc, _] = await Promise.all([
            EOS.api('/projects/api/projects'),
            EOS.api('/projects/api/type-config'),
            loadTemplates(),
        ]);
        allProjects = projects;
        typeConfig = tc.types || {};
        toolsByType = tc.tools || {};
        featureRegistry = tc.features || {};
        filteredProjects = allProjects;
        document.getElementById('subtitle').textContent = allProjects.length + ' projects';
        renderStats(allProjects);
        renderCategoryBar();
        filterProjects();
    } catch(e) {
        document.getElementById('subtitle').textContent = 'Failed to load';
    }
}

// Init
['kanban','list','timeline','calendar'].forEach(function(v) {
    var el = document.getElementById('btn-' + v);
    if (el) el.classList.toggle('active', currentView === v);
});
if (EOS.keys) { EOS.keys.register('n', 'New project', function() { openCreate(); }); EOS.keys.register('r', 'Refresh', function() { load(); }); }
load().then(function() { _route.init(); });
