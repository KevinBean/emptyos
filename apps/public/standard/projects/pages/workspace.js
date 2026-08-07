// Projects — project renderer + shared list helpers.
//
// Loaded by BOTH pages/index.html (the project list) and pages/workspace.html
// (the full-page workspace), but its two roles split by page:
//   • On the LIST page it only provides the small shared helpers the cards need
//     (typeBadge / progressBar / ALL_STATUSES). The render path is never invoked
//     there — the list navigates to the workspace instead of rendering detail.
//   • On the WORKSPACE page, openProjectWorkspace() fetches the project and
//     renderProjectView() → renderWorkspaceLayout() (workspace-page.js) draws
//     the multi-pane surface into #detail-view. Tab loaders live in tabs.js;
//     modal/dialog code (openAddTask, openNewDoc, showHealth, …) in dialogs.js.
//
// Depends on the globals typeConfig / featureRegistry / toolsByType (set by the
// workspace.html boot, or by app.js load() on the list page). LOAD ORDER:
// index.html loads this BEFORE app.js so the shared helpers are defined for the
// list-view code.

var _detailProject = null;
var _detailTab = 'tasks';

var ALL_STATUSES = ['idea', 'active', 'spec-ready', 'blocked', 'shelved', 'completed', 'archived'];

function progressBar(p) {
    if (p.total_tasks === 0) return '';
    var cls = p.progress === 100 ? 'progress-fill done' : 'progress-fill';
    return '<div class="progress-bar"><div class="' + cls + '" style="width:' + p.progress + '%"></div></div>';
}

function typeBadge(p) {
    if (!p.type || p.type === 'personal') return '';
    return '<span class="type-badge type-' + p.type + '">' + p.type + '</span> ';
}

// Fetch one project and render it into #detail-view. Called by the workspace.html
// boot and by re-render-after-mutation (toggleTask / changeStatus / dialogs).
async function openProjectWorkspace(id) {
    var dv = document.getElementById('detail-view');
    if (dv) dv.innerHTML = '<div class="eos-empty">Loading...</div>';
    _detailTab = 'tasks';
    try {
        var p = await EOS.api('/projects/api/projects/' + encodeURIComponent(id));
        if (p.error) { if (dv) dv.innerHTML = '<div class="eos-empty">' + esc(p.error) + '</div>'; return; }
        _detailProject = p;
        _detailProject._id = id;
        // 4D-timeline auto-mount: see .claude/rules/app-ui-patterns.md
        if (p._vault_path && dv) dv.setAttribute('data-entity-path', p._vault_path);
        renderProjectView();
    } catch(e) {
        if (dv) dv.innerHTML = '<div class="eos-empty">Failed to load project</div>';
    }
}

// The project view IS the multi-pane workspace (workspace-page.js). The old
// in-list single-column detail was retired; this renders only on the workspace
// page, where renderWorkspaceLayout is defined.
function renderProjectView() {
    if (typeof renderWorkspaceLayout === 'function') renderWorkspaceLayout();
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
        if (typeof EOS_UI !== 'undefined' && EOS_UI.yearHeatmap) {
            EOS_UI.yearHeatmap({
                mount: '#activity-heatmap',
                data: r.activity,
                months: 3,
                showMonthLabels: false,
                tooltipFor: function(date, count) { return date + ': ' + count + ' edits'; },
            });
        }
    } catch(e) {}
}

async function toggleTask(projectId, line, text) {
    try {
        var res = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/tasks/toggle', { line: line, text: text || '' });
        if (res && res.error) { EOS_UI.toast(res.error, false); return; }
        openProjectWorkspace(projectId);   // re-render current host (list or standalone)
        EOS_UI.toast('Task toggled');
    } catch(e) {
        EOS_UI.toast('Failed to toggle', false);
    }
}

// Right-click alternative to toggleTask — project tasks live in
// 10_Projects/**, so they're addressable by the *task app's* (file, line)
// exactly like any other vault task. Reuses the same shared modal as every
// other surface via the task app's endpoint instead of the project's own
// 0-based toggle route; projects' task lines are 0-based (see toggleTask
// above), while the task app is 1-based, hence the +1.
function completeProjectTaskNote(projectId, line, text) {
    var file = _detailProject && _detailProject._id === projectId ? _detailProject._vault_path : '';
    if (!file) { EOS_UI.toast('Could not resolve project file', false); return; }
    EOS_UI.completeTaskWithNote({ file: file, line: line + 1, text: text || '' }, {
        onSuccess: function() { openProjectWorkspace(projectId); },
    });
}

async function changeStatus(projectId, status) {
    try {
        await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/status', { status: status });
        EOS_UI.toast('Status updated to ' + status);
        if (typeof load === 'function') load();   // refresh the list (list page only)
        openProjectWorkspace(projectId);
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
        renderProjectView();
    } catch(e) {
        EOS_UI.toast('Failed to update stage', false);
    }
}
