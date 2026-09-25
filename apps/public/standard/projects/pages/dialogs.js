// Projects — modals, settings panels, template picker, task metadata, health.

function openCreateAI() {
    var schema = [
        {key: 'name', label: 'Name', type: 'text', required: true, placeholder: 'Project name'},
        {key: 'goal', label: 'Goal / purpose', type: 'textarea'},
        {key: 'type', label: 'Type', type: 'select', options: ['personal','engineering','development']},
        {key: 'status', label: 'Status', type: 'select', options: ['idea','active']},
        {key: 'deadline', label: 'Deadline (YYYY-MM-DD)', type: 'date'},
        {key: 'employer', label: 'Company / employer', type: 'text'},
        {key: 'repo', label: 'Repo path (for development projects only)', type: 'text'},
    ];
    var submit = async function(vals) {
        if (!vals.name) { EOS_UI.toast('Name required', false); return; }
        var payload = {
            name: vals.name,
            goal: vals.goal || '',
            status: vals.status || 'idea',
            deadline: vals.deadline || '',
            employer: vals.employer || '',
            type: vals.type || 'personal',
        };
        if (payload.type === 'development') payload.repo = vals.repo || '';
        var r = await EOS.post('/projects/api/create', payload);
        if (r.error) { EOS_UI.toast(r.error, false); throw new Error(r.error); }
        EOS_UI.toast('Created: ' + payload.name);
        if (typeof load === 'function') load();
    };
    EOS_UI.aiFormFill({
        title: '+ New Project',
        intro: "Describe the project — name, what it's for, what type (personal / engineering / development), and a deadline if any.",
        schema: schema,
        initial: {type: 'personal', status: 'idea'},
        submitLabel: 'Create Project',
        onSubmit: submit,
        onManual: function() {
            // Fall back to the existing inline modal flow.
            openCreate();
        },
    });
}

function openCreate() {
    document.getElementById('new-name').value = '';
    document.getElementById('new-goal').value = '';
    document.getElementById('new-status').value = 'idea';
    document.getElementById('new-deadline').value = '';
    document.getElementById('new-employer').value = '';
    EOS_UI.openModal('create-modal');
    setTimeout(function() { document.getElementById('new-name').focus(); }, 200);
}

async function submitCreate() {
    var name = document.getElementById('new-name').value.trim();
    if (!name) return;
    var projectType = document.getElementById('new-type').value;
    var templateId = document.getElementById('create-modal').dataset.template || '';
    var payload = {
        name: name,
        goal: document.getElementById('new-goal').value.trim(),
        status: document.getElementById('new-status').value,
        deadline: document.getElementById('new-deadline').value,
        employer: document.getElementById('new-employer').value.trim(),
        type: projectType,
    };
    if (projectType === 'development') {
        payload.repo = document.getElementById('new-repo').value.trim();
    }
    try {
        var endpoint = templateId ? '/projects/api/from-template' : '/projects/api/create';
        if (templateId) payload.template = templateId;
        var r = await EOS.post(endpoint, payload);
        if (r.error) { EOS_UI.toast(r.error, false); return; }
        EOS_UI.closeModal('create-modal');
        document.getElementById('create-modal').dataset.template = '';
        EOS_UI.toast('Created: ' + name);
        load();
    } catch(e) {
        EOS_UI.toast('Failed to create', false);
    }
}

function openAddTask(projectId) {
    document.getElementById('task-text').value = '';
    document.getElementById('task-due').value = '';
    document.getElementById('task-project-id').value = projectId;
    EOS_UI.openModal('task-modal');
    setTimeout(function() { document.getElementById('task-text').focus(); }, 200);
}

async function submitTask() {
    var projectId = document.getElementById('task-project-id').value;
    var text = document.getElementById('task-text').value.trim();
    if (!text) return;
    try {
        await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/tasks/add', {
            text: text,
            due: document.getElementById('task-due').value,
        });
        EOS_UI.closeModal('task-modal');
        EOS_UI.toast('Task added');
        openProjectWorkspace(projectId);
    } catch(e) {
        EOS_UI.toast('Failed to add task', false);
    }
}

function openProjectWorklog(projectId) {
    document.getElementById('worklog-project-id').value = projectId;
    document.getElementById('worklog-text').value = '';
    document.getElementById('worklog-status').value = 'in-progress';
    document.getElementById('worklog-date').value = new Date().toISOString().slice(0, 10);
    document.getElementById('worklog-employer').value =
        (_detailProject && _detailProject.employer) || '';
    EOS_UI.openModal('worklog-modal');
    setTimeout(function() { document.getElementById('worklog-text').focus(); }, 200);
}

async function submitProjectWorklog() {
    var projectId = document.getElementById('worklog-project-id').value;
    var text = document.getElementById('worklog-text').value.trim();
    if (!text) { EOS_UI.toast('Describe the work first', false); return; }
    try {
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/worklog', {
            text: text,
            status: document.getElementById('worklog-status').value,
            date: document.getElementById('worklog-date').value,
            employer: document.getElementById('worklog-employer').value.trim(),
        });
        if (r.error) { EOS_UI.toast(r.error, false); return; }
        EOS_UI.closeModal('worklog-modal');
        EOS_UI.toast('Logged to Worklog');
        if (typeof loadWsWorklog === 'function') loadWsWorklog(projectId);
    } catch(e) {
        EOS_UI.toast('Failed to log work', false);
    }
}

function openNewDoc(projectId) {
    document.getElementById('doc-title').value = '';
    document.getElementById('doc-template').value = 'blank';
    document.getElementById('doc-project-id').value = projectId;
    EOS_UI.openModal('doc-modal');
    setTimeout(function() { document.getElementById('doc-title').focus(); }, 200);
}

async function submitDoc() {
    var projectId = document.getElementById('doc-project-id').value;
    var title = document.getElementById('doc-title').value.trim();
    if (!title) return;
    try {
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/docs/create', {
            title: title,
            template: document.getElementById('doc-template').value,
        });
        if (r.error) { EOS_UI.toast(r.error, false); return; }
        EOS_UI.closeModal('doc-modal');
        EOS_UI.toast('Created: ' + title);
        loadDocsTab(projectId);
        if (r.path) EOS.viewNote(r.path);
    } catch(e) {
        EOS_UI.toast('Failed to create doc', false);
    }
}

function openCreateSprint(projectId) {
    var schema = [
        {key: 'name', label: 'Sprint Name', type: 'text', required: true, placeholder: 'e.g. Foundation'},
        {key: 'start', label: 'Start Date', type: 'date'},
        {key: 'end', label: 'End Date', type: 'date', required: true},
        {key: 'goal', label: 'Sprint Goal', type: 'text', placeholder: 'What we aim to deliver'},
    ];
    var today = new Date().toISOString().split('T')[0];
    var submit = async function(data) {
        if (!data.name) { EOS_UI.toast('Name required', false); return; }
        if (!data.end) { EOS_UI.toast('End date required', false); return; }
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/sprints', data);
        if (r.error) { EOS_UI.toast(r.error, false); throw new Error(r.error); }
        EOS_UI.toast('Sprint ' + r.num + ' created');
        openProjectWorkspace(projectId);
    };
    EOS_UI.aiFormFill({
        title: 'New Sprint',
        intro: "Describe the sprint — name, dates (start defaults to today), and the goal you're chasing.",
        schema: schema,
        initial: {start: today},
        submitLabel: 'Create Sprint',
        onSubmit: submit,
        onManual: function(prefill) {
            EOS_UI.formModal('New Sprint (manual)', schema.map(function(f) {
                return Object.assign({}, f, {value: prefill[f.key] || (f.key === 'start' ? today : '')});
            }), submit);
        },
    });
}

function openCreateMilestone(projectId) {
    var schema = [
        {key: 'id', label: 'Version / ID', type: 'text', required: true, placeholder: 'e.g. v0.1'},
        {key: 'name', label: 'Name', type: 'text', required: true, placeholder: 'e.g. Feature System'},
        {key: 'target', label: 'Target Date', type: 'date'},
    ];
    var submit = async function(data) {
        if (!data.id || !data.name) { EOS_UI.toast('ID and name required', false); return; }
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/milestones', data);
        if (r.error) { EOS_UI.toast(r.error, false); throw new Error(r.error); }
        EOS_UI.toast('Milestone ' + r.id + ' created');
        openProjectWorkspace(projectId);
    };
    EOS_UI.aiFormFill({
        title: 'New Milestone',
        intro: "Describe the milestone — version/ID (like v0.1), what it delivers, and target date.",
        schema: schema,
        submitLabel: 'Create Milestone',
        onSubmit: submit,
        onManual: function(prefill) {
            EOS_UI.formModal('New Milestone (manual)', schema.map(function(f) {
                return Object.assign({}, f, {value: prefill[f.key] || ''});
            }), submit);
        },
    });
}

function openCreateRelease(projectId) {
    var schema = [
        {key: 'version', label: 'Version', type: 'text', required: true, placeholder: 'e.g. v0.1.0'},
        {key: 'date', label: 'Release Date', type: 'date'},
    ];
    var today = new Date().toISOString().split('T')[0];
    var submit = async function(data) {
        if (!data.version) { EOS_UI.toast('Version required', false); return; }
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/releases', data);
        if (r.error) { EOS_UI.toast(r.error, false); throw new Error(r.error); }
        EOS_UI.toast('Release ' + r.version + ' created');
        openProjectWorkspace(projectId);
    };
    EOS_UI.aiFormFill({
        title: 'New Release',
        intro: "What version are you releasing? Date defaults to today.",
        schema: schema,
        initial: {date: today},
        submitLabel: 'Create Release',
        onSubmit: submit,
        onManual: function(prefill) {
            EOS_UI.formModal('New Release (manual)', schema.map(function(f) {
                return Object.assign({}, f, {value: prefill[f.key] || (f.key === 'date' ? today : '')});
            }), submit);
        },
    });
}

// --- Project Settings slide-out ---
function openProjectSettings(projectId) {
    var p = _detailProject;
    if (!p || p._id !== projectId) return;

    document.getElementById('ps-title').textContent = 'Project Settings — ' + p.name;

    var statusSel = document.getElementById('ps-status');
    statusSel.innerHTML = ALL_STATUSES.map(function(s) {
        return '<option value="' + s + '"' + (s === p.status ? ' selected' : '') + '>' + s + '</option>';
    }).join('');

    var stageSel = document.getElementById('ps-stage');
    var stageHint = document.getElementById('ps-stage-hint');
    var typeDef = (typeConfig || {})[p.type] || {stages: [], labels: {}};
    var stages = typeDef.stages || [];
    if (stages.length === 0) {
        stageSel.innerHTML = '<option value="">(no stages for ' + p.type + ')</option>';
        stageSel.disabled = true;
        stageHint.textContent = 'Stages apply to engineering/development project types.';
    } else {
        stageSel.disabled = false;
        stageHint.textContent = '';
        stageSel.innerHTML = '<option value="">(none)</option>' + stages.map(function(s) {
            var label = (typeDef.labels || {})[s] || s;
            return '<option value="' + s + '"' + (s === p.stage ? ' selected' : '') + '>' + label + '</option>';
        }).join('');
    }

    document.getElementById('ps-deadline').value = p.deadline || '';
    document.getElementById('ps-employer').value = p.employer || '';

    var tagsVal = Array.isArray(p.tags) ? p.tags.join(', ') : (p.tags || '');
    document.getElementById('ps-tags').value = tagsVal;

    var fr = featureRegistry || {};
    var features = p.features || {};
    var fhtml = Object.keys(fr).sort(function(a,b){ return fr[a].order - fr[b].order; }).map(function(fid) {
        var fdef = fr[fid];
        var enabled = !!features[fid];
        return '<div class="feat-toggle">' +
            '<span class="feat-label">' + esc(fdef.label) + (fdef.tab ? '' : ' <span style="font-size:10px;color:var(--text-muted)">(widget)</span>') + '</span>' +
            '<label class="feat-switch"><input type="checkbox"' + (enabled ? ' checked' : '') +
            ' onchange="toggleFeature(\'' + escAttr(projectId) + '\',\'' + fid + '\',this.checked)">' +
            '<span class="feat-slider"></span></label>' +
        '</div>';
    }).join('');
    document.getElementById('ps-features').innerHTML = fhtml || '<div style="font-size:12px;color:var(--text-muted)">No features registered.</div>';

    document.getElementById('project-settings-panel').classList.add('open');
}

function closeProjectSettings() {
    document.getElementById('project-settings-panel').classList.remove('open');
}

async function saveProjectSettings() {
    var p = _detailProject;
    if (!p) return;
    var id = p._id;
    var newStatus = document.getElementById('ps-status').value;
    var newStage = document.getElementById('ps-stage').value;
    var newDeadline = document.getElementById('ps-deadline').value;
    var newEmployer = document.getElementById('ps-employer').value.trim();
    var newTags = document.getElementById('ps-tags').value;

    try {
        var tasks = [];
        if (newStatus && newStatus !== p.status) {
            tasks.push(EOS.post('/projects/api/projects/' + encodeURIComponent(id) + '/status', {status: newStatus}));
        }
        if (!document.getElementById('ps-stage').disabled && newStage !== (p.stage || '')) {
            tasks.push(EOS.post('/projects/api/projects/' + encodeURIComponent(id) + '/stage', {stage: newStage}));
        }
        var origTags = Array.isArray(p.tags) ? p.tags.join(', ') : (p.tags || '');
        var metaBody = {};
        if (newDeadline !== (p.deadline || '')) metaBody.deadline = newDeadline;
        if (newEmployer !== (p.employer || '')) metaBody.employer = newEmployer;
        if (newTags !== origTags) metaBody.tags = newTags;
        if (Object.keys(metaBody).length) {
            tasks.push(EOS.post('/projects/api/projects/' + encodeURIComponent(id) + '/meta', metaBody));
        }
        if (tasks.length === 0) { closeProjectSettings(); return; }
        var results = await Promise.all(tasks);
        var err = results.find(function(r) { return r && r.error; });
        if (err) { EOS_UI.toast(err.error, false); return; }
        EOS_UI.toast('Project updated');
        closeProjectSettings();
        await load();
        openProjectWorkspace(id);
    } catch(e) {
        EOS_UI.toast('Failed to save', false);
    }
}

async function toggleFeature(projectId, featureId, enabled) {
    try {
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/features', {
            feature: featureId, enabled: enabled
        });
        if (r.error) { EOS_UI.toast(r.error, false); return; }
        if (_detailProject && r.features) {
            _detailProject.features = r.features;
        }
        EOS_UI.toast(featureId + ': ' + (enabled ? 'ON' : 'OFF'));
        renderProjectView();
    } catch(e) {
        EOS_UI.toast('Failed to toggle feature', false);
    }
}

// --- App Settings slide-out (shared helper) ---
var _appSettings = EOS_UI.settingsPanel({
    id: 'app-settings-panel',
    title: 'App Settings',
    fields: [
        {key: 'projects.stale_days', label: 'Stale After (days)', type: 'number', default: 90, min: 1,
         hint: 'Active projects with no activity for this many days are flagged stale.'},
    ],
});
function openAppSettings() { _appSettings.open(); }

// --- Task metadata ---
// Assign a task to a staff workflow agent — soft bridge, invisible-in-spirit
// when staff isn't installed (empty roster -> a toast, not a broken modal).
async function openAssignTask(projectId, taskLine) {
    var agents = [];
    try {
        var r = await EOS.api('/projects/api/assignable-agents');
        agents = (r && r.agents) || [];
    } catch(e) {}
    if (!agents.length) {
        EOS_UI.toast('No workflow agents available — install/enable Staff with a workflow-mode agent', false);
        return;
    }
    EOS_UI.formModal('Assign task to agent', [
        {key: 'agent_id', label: 'Agent', type: 'select', options: agents.map(function(a) { return a.id; })},
    ], async function(values) {
        var agentId = (values.agent_id || '').trim();
        if (!agentId) return;
        try {
            var res = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/tasks/' + taskLine + '/assign', {
                agent_id: agentId,
            });
            if (res.error) { EOS_UI.toast(res.error, false); return; }
            EOS_UI.toast('Assigned to ' + agentId + ' — job started');
            openProjectWorkspace(projectId);
        } catch(e) {
            EOS_UI.toast('Failed to assign', false);
        }
    });
}

function openAddMeta(projectId, taskLine) {
    EOS_UI.formModal('Add task metadata', [
        {key: 'type',  label: 'Type',  type: 'select', options: ['info','need','calc','ref','sprint','milestone']},
        {key: 'value', label: 'Value', placeholder: 'e.g. waiting on X, $500, sprint-3'},
    ], async function(values) {
        var metaType = (values.type || '').trim();
        var value = (values.value || '').trim();
        if (!metaType || !value) return;
        try {
            var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/tasks/' + taskLine + '/meta', {
                type: metaType, value: value
            });
            if (r.error) { EOS_UI.toast(r.error, false); return; }
            EOS_UI.toast('Added ' + metaType);
            openProjectWorkspace(projectId);
        } catch(e) {
            EOS_UI.toast('Failed to add metadata', false);
        }
    });
}

// --- Create modal type handling ---
function onTypeChange() {
    var t = document.getElementById('new-type').value;
    document.getElementById('dev-fields').classList.toggle('hidden', t !== 'development');
    renderTemplatePicker(t);
}

function renderTemplatePicker(projectType) {
    var picker = document.getElementById('template-picker');
    if (!_allTemplates || !_allTemplates.length) { picker.innerHTML = ''; return; }
    var matching = _allTemplates.filter(function(t) { return t.type === projectType; });
    if (!matching.length) { picker.innerHTML = ''; return; }
    picker.innerHTML = '<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">Quick start from template:</div>' +
        '<div style="display:flex;gap:4px;flex-wrap:wrap">' +
        matching.map(function(t) {
            return '<button class="eos-btn-sm eos-btn-ghost" onclick="useTemplate(\'' + escAttr(t.id) + '\')" style="font-size:11px">' + esc(t.name) + '</button>';
        }).join('') + '</div>';
}

var _allTemplates = [];
async function loadTemplates() {
    try { _allTemplates = await EOS.api('/projects/api/templates'); } catch(e) {}
}

function useTemplate(templateId) {
    var tmpl = _allTemplates.find(function(t) { return t.id === templateId; });
    if (!tmpl) return;
    if (!document.getElementById('new-goal').value && tmpl.goal) {
        document.getElementById('new-goal').value = tmpl.goal;
    }
    document.getElementById('new-type').value = tmpl.type || 'personal';
    onTypeChange();
    EOS_UI.toast('Template: ' + tmpl.name);
    document.getElementById('create-modal').dataset.template = templateId;
}

async function showHealth(projectId) {
    EOS_UI.openModal('health-modal');
    document.getElementById('health-content').textContent = 'Analyzing project health...';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(projectId) + '/health');
        var el = document.getElementById('health-content');
        el.textContent = r.health || 'No assessment available';
        var pv = EOS_UI.provenanceLine(r.provenance, {className: '', style: 'margin-top:10px'});
        if (pv) el.insertAdjacentHTML('beforeend', pv);
    } catch(e) {
        document.getElementById('health-content').textContent = 'AI unavailable';
    }
}

function _renderProjectSceneModal(projectId, data) {
    // Footer formatting: "Generated 2 days ago" if cached, "Just generated" if fresh.
    var when = data.generated_at || '';
    var whenLabel = when ? _humanAgo(when) : 'unknown';
    var statusChip = data.cached
        ? '<span style="color:var(--text-muted); font-size:11px;">📌 cached · generated ' + EOS_UI.esc(whenLabel) + '</span>'
        : '<span style="color:var(--accent); font-size:11px;">✨ fresh · generated ' + EOS_UI.esc(whenLabel) + '</span>';
    return '<div style="height:75vh; background:var(--bg-card, #0d1117); border-radius:8px; overflow:hidden;">' +
              '<iframe id="proj-scene-iframe" src="' + EOS_UI.escAttr(data.embed_url) + '?t=' + Date.now() + '" ' +
                'style="width:100%; height:100%; border:0;" sandbox="allow-scripts"></iframe>' +
           '</div>' +
           '<div style="display:flex; align-items:center; gap:8px; margin-top:8px; font-size:11px; color:var(--text-muted);">' +
              statusChip +
              '<span style="flex:1"></span>' +
              '<a href="/viz/?id=' + EOS_UI.escAttr(data.record_id) + '" target="_blank" style="color:var(--text-muted);">' +
                'open in viz ↗</a>' +
              '<button class="eos-btn-sm" onclick="visualizeProjectScene(' + escAttr(JSON.stringify(projectId)) + ', true)" title="Re-run the LLM and replace the cached artifact">↻ Regenerate</button>' +
           '</div>';
}

function _humanAgo(iso) {
    try {
        var then = new Date(iso);
        var ms = Date.now() - then.getTime();
        var s = Math.floor(ms / 1000);
        if (s < 60) return s + 's ago';
        if (s < 3600) return Math.floor(s/60) + 'm ago';
        if (s < 86400) return Math.floor(s/3600) + 'h ago';
        var d = Math.floor(s / 86400);
        if (d < 30) return d + 'd ago';
        return then.toISOString().slice(0, 10);
    } catch (e) { return iso; }
}

async function visualizeProjectScene(projectId, force) {
    // POST → /api/projects/<id>/visualize-scene.
    // Default: returns cached scene (instant, no LLM). force=true regenerates.
    // First click: cache hit if a prior scene exists, otherwise generates.
    // ↻ Regenerate inside the modal calls with force=true.
    var btn = event && event.target;
    var orig = btn ? btn.textContent : '';
    var regenerating = !!force;
    if (btn) {
        btn.disabled = true;
        btn.textContent = regenerating ? '⏳ regenerating…' : '⏳';
    }
    try {
        var res = await fetch(
            '/projects/api/projects/' + encodeURIComponent(projectId) + '/visualize-scene',
            {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({force: regenerating}),
            }
        );
        var data = await res.json();
        if (!data.ok) {
            EOS_UI.toast('Scene render failed: ' + (data.error || 'unknown'), false);
            return;
        }
        // If a modal is already open (regenerate path), swap the iframe + footer
        // in place instead of closing + reopening.
        var existing = document.getElementById('proj-scene-iframe');
        if (regenerating && existing) {
            var container = existing.closest('.eos-modal-body') || document.body;
            // Re-render the inner block — find a stable wrapper.
            var wrap = existing.closest('div').parentElement;
            if (wrap) wrap.innerHTML = _renderProjectSceneModal(projectId, data);
            EOS_UI.toast('Regenerated', true);
        } else {
            EOS_UI.modal({
                title: 'Project scene: ' + projectId,
                width: '90vw',
                body: _renderProjectSceneModal(projectId, data),
            });
        }
    } catch (e) {
        EOS_UI.toast('Scene render error: ' + e.message, false);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = orig; }
    }
}
