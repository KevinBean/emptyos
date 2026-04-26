// Projects — tab content loaders. Called from app.js via TAB_RENDERERS.

function renderTaskMeta(meta) {
    if (!meta || !meta.length) return '';
    var filtered = meta.filter(function(m) { return m.type !== 'depends_on' && m.type !== 'blocks'; });
    if (!filtered.length) return '';
    return '<div class="task-meta">' + filtered.map(function(m) {
        if (m.type === 'sprint') return '<div class="task-meta-item"><span class="dep-badge" style="background:rgba(59,130,246,0.15);color:#3b82f6">Sprint ' + esc(m.value) + '</span></div>';
        if (m.type === 'milestone') return '<div class="task-meta-item"><span class="dep-badge" style="background:color-mix(in srgb, var(--purple) 15%, transparent);color:var(--purple)">' + esc(m.value) + '</span></div>';
        var cls = 'meta-type' + (m.type === 'need' ? ' meta-type-need' : m.type === 'calc' ? ' meta-type-calc' : '');
        return '<div class="task-meta-item"><span class="' + cls + '">' + m.type + ':</span> ' + esc(m.value) + '</div>';
    }).join('') + '</div>';
}

function renderTasksTab(p, id) {
    var hasDeps = p.has_dependencies;
    var tasksHtml = (p.tasks || []).map(function(t) {
        var itemCls = 'task-item';
        if (!t.done && t.blocked_by && t.blocked_by.length) itemCls += ' task-blocked';
        else if (!t.done && t.ready && hasDeps) itemCls += ' task-ready';

        var depInfo = '';
        if (t.blocked_by && t.blocked_by.length) {
            depInfo = '<div class="blocked-label">Blocked by: ' + t.blocked_by.map(esc).join(', ') + '</div>';
        }
        if (t.depends_on && t.depends_on.length) {
            depInfo += '<div style="margin-top:1px">' + t.depends_on.map(function(d) {
                return '<span class="dep-badge dep-badge-depends">' + (d.done ? '✓ ' : '') + esc(d.text.substring(0,30)) + '</span>';
            }).join('') + '</div>';
        }
        if (t.blocks && t.blocks.length) {
            depInfo += '<div style="margin-top:1px">' + t.blocks.map(function(b) {
                return '<span class="dep-badge dep-badge-blocks">→ ' + esc(b.text.substring(0,30)) + '</span>';
            }).join('') + '</div>';
        }

        return '<div class="' + itemCls + '" onclick="toggleTask(\'' + escAttr(id) + '\',' + t.line + ')">' +
            '<div class="task-check ' + (t.done ? 'done' : '') + '">' + (t.done ? '✓' : '') + '</div>' +
            '<div style="flex:1">' +
                '<div class="task-text ' + (t.done ? 'done' : '') + '">' + esc(t.text) + '</div>' +
                depInfo +
                renderTaskMeta(t.meta) +
            '</div>' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="event.stopPropagation();openAddMeta(\'' + escAttr(id) + '\',' + t.line + ')" title="Add info" style="font-size:10px;padding:2px 6px">+meta</button>' +
        '</div>';
    }).join('') || '<div style="color:var(--text-muted);font-size:13px;padding:8px 0">No tasks yet</div>';

    var depSummary = '';
    if (hasDeps) {
        depSummary = '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">' +
            '<span style="color:var(--success)">' + (p.ready_count || 0) + ' ready</span> · ' +
            '<span style="color:var(--danger)">' + (p.blocked_count || 0) + ' blocked</span>' +
        '</div>';
    }

    document.getElementById('tab-content').innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
            depSummary +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="openAddTask(\'' + escAttr(id) + '\')">+ Add Task</button>' +
        '</div>' +
        tasksHtml;
}

async function loadDocsTab(id) {
    var tc = document.getElementById('tab-content');
    tc.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:8px 0">Loading docs...</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/docs');
        var docs = r.docs || [];
        document.getElementById('docs-count').textContent = docs.length;

        if (!docs.length) {
            tc.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:16px 0;text-align:center">No documents yet</div>' +
                (r.is_directory ? '<div style="text-align:center"><button class="eos-btn-sm" onclick="openNewDoc(\'' + escAttr(id) + '\')">+ New Document</button></div>' : '');
            return;
        }

        var html = '';
        if (r.is_directory) {
            html += '<div style="display:flex;justify-content:flex-end;margin-bottom:8px">' +
                '<button class="eos-btn-sm eos-btn-ghost" onclick="openNewDoc(\'' + escAttr(id) + '\')">+ New Doc</button></div>';
        }
        docs.forEach(function(d) {
            var icon = d.is_main ? '📋' : '📄';
            var sizeKb = d.size > 1024 ? Math.round(d.size/1024) + ' KB' : d.size + ' B';
            html += '<div class="doc-item" onclick="EOS.viewNote(\'' + escAttr(d.path) + '\')">' +
                '<span class="doc-icon">' + icon + '</span>' +
                '<div style="flex:1;min-width:0">' +
                    '<div class="doc-name' + (d.is_main ? ' main' : '') + '">' + esc(d.name) + '</div>' +
                    (d.rel_path !== d.name ? '<div style="font-size:11px;color:var(--text-muted)">' + esc(d.rel_path) + '</div>' : '') +
                '</div>' +
                (d.is_main ? '<span class="doc-badge">main</span>' : '') +
                '<div class="doc-meta">' + d.modified.split(' ')[0] + ' · ' + sizeKb + '</div>' +
            '</div>';
        });
        tc.innerHTML = html;
    } catch(e) {
        tc.innerHTML = '<div class="eos-empty">Failed to load docs: ' + esc(e.message || String(e)) + '</div>';
    }
}

// --- Tools tab (engineering) ---
async function loadToolsTab(p, id) {
    var tc = document.getElementById('tab-content');
    var tools = (toolsByType[p.type] || []);
    if (!tools.length) {
        tc.innerHTML = '<div class="eos-empty">No tools available for ' + p.type + ' projects.<br>Apps can declare <code>[provides.project-tools]</code> in their manifest.</div>';
        return;
    }
    var html = '<div style="margin-bottom:12px;font-size:13px;color:var(--text-secondary)">Available tools for ' + p.type + ' projects:</div>';
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px">';
    tools.forEach(function(t) {
        html += '<div class="card" style="padding:12px;cursor:pointer" onclick="openTool(\'' + escAttr(id) + '\',\'' + escAttr(t.app) + '\',\'' + escAttr(t.method) + '\',\'' + escAttr(t.label) + '\')">' +
            '<div style="font-weight:600;font-size:14px;color:var(--text-heading)">' + esc(t.label) + '</div>' +
            '<div style="font-size:11px;color:var(--text-muted)">' + esc(t.app) + ' / ' + esc(t.id) + '</div>' +
        '</div>';
    });
    html += '</div>';
    tc.innerHTML = html;
}

function openTool(projectId, appId, method, label) {
    window.open('/' + appId + '/', '_blank');
    EOS_UI.toast('Opened ' + label + ' — run calc, then attach result here');
}

// --- Calculations tab ---
async function loadCalcsTab(id) {
    var tc = document.getElementById('tab-content');
    tc.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:8px 0">Loading...</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/calculations');
        var calcs = r.calculations || [];
        if (!calcs.length) {
            tc.innerHTML = '<div class="eos-empty">No calculation results attached yet.<br>Run a tool from the Tools tab, then attach results to tasks.</div>';
            return;
        }
        var html = calcs.map(function(c) {
            return '<div class="calc-item">' +
                '<div style="flex:1">' +
                    '<div style="font-weight:500;font-size:13px">' + esc(c.app) + ' / ' + esc(c.method) + '</div>' +
                    '<div style="font-size:11px;color:var(--text-muted)">' + esc(c.summary) + '</div>' +
                '</div>' +
                '<div style="font-size:11px;color:var(--text-muted)">' + EOS.noteActions(c.file) + '</div>' +
            '</div>';
        }).join('');
        tc.innerHTML = html;
    } catch(e) {
        tc.innerHTML = '<div class="eos-empty">Failed to load calculations</div>';
    }
}

// --- Code tab (development) ---
async function loadCodeTab(p, id) {
    var tc = document.getElementById('tab-content');
    if (!p.repo) {
        tc.innerHTML = '<div class="eos-empty">No repository path set.<br>Add <code>repo: D:/path</code> to project frontmatter.</div>';
        return;
    }
    tc.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:8px 0">Loading git status...</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/dev-status');
        if (r.error) { tc.innerHTML = '<div class="eos-empty">' + esc(r.error) + '</div>'; return; }
        var html = '<div style="margin-bottom:12px"><strong style="font-size:13px">Repository:</strong> <span style="font-size:12px;color:var(--text-secondary)">' + esc(r.repo) + '</span></div>';

        html += '<div style="margin-bottom:16px"><div style="font-weight:600;font-size:13px;margin-bottom:6px">Uncommitted Changes</div>';
        if (r.status && r.status.trim()) {
            html += '<div style="background:var(--bg-card);padding:8px 12px;border-radius:8px;border:1px solid var(--border)">';
            r.status.trim().split('\n').forEach(function(line) {
                var cls = 'git-line';
                if (line.startsWith('A') || line.startsWith('?')) cls += ' git-added';
                else if (line.startsWith('M')) cls += ' git-modified';
                else if (line.startsWith('D')) cls += ' git-deleted';
                html += '<div class="' + cls + '">' + esc(line) + '</div>';
            });
            html += '</div>';
        } else {
            html += '<div style="font-size:12px;color:var(--text-muted)">Clean working tree</div>';
        }
        html += '</div>';

        html += '<div><div style="font-weight:600;font-size:13px;margin-bottom:6px">Recent Commits</div>';
        html += '<div style="background:var(--bg-card);padding:8px 12px;border-radius:8px;border:1px solid var(--border)">';
        if (r.log && r.log.trim()) {
            r.log.trim().split('\n').forEach(function(line) {
                html += '<div class="git-line">' + esc(line) + '</div>';
            });
        } else {
            html += '<div class="git-line">No commits</div>';
        }
        html += '</div></div>';
        tc.innerHTML = html;
    } catch(e) {
        tc.innerHTML = '<div class="eos-empty">Failed to load git data: ' + esc(e.message || String(e)) + '</div>';
    }
}

// --- Sprints tab ---
async function loadSprintsTab(p, id) {
    var tc = document.getElementById('tab-content');
    tc.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:8px 0">Loading sprints...</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/sprints');
        var sprints = r.sprints || [];
        var html = '<div style="display:flex;justify-content:flex-end;margin-bottom:10px">' +
            '<button class="eos-btn-sm" onclick="openCreateSprint(\'' + escAttr(id) + '\')">+ New Sprint</button></div>';

        if (!sprints.length) {
            html += '<div class="eos-empty">No sprints yet. Create one to start tracking iterations.</div>';
            tc.innerHTML = html;
            return;
        }

        if (r.velocity && r.velocity.length) {
            var maxDone = Math.max.apply(null, r.velocity.map(function(v){ return v.done; })) || 1;
            html += '<div style="margin-bottom:16px;background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:12px">' +
                '<div style="font-size:12px;font-weight:600;margin-bottom:8px">Velocity <span style="font-weight:400;color:var(--text-muted)">(avg: ' + r.avg_velocity + ' tasks/sprint)</span></div>' +
                '<div style="height:60px;display:flex;align-items:flex-end;gap:3px">';
            r.velocity.forEach(function(v) {
                var h = Math.round(v.done / maxDone * 50) + 4;
                html += '<div style="text-align:center;flex:1"><div class="vel-bar" style="height:' + h + 'px;width:100%"></div><div style="font-size:9px;color:var(--text-muted);margin-top:2px">S' + v.sprint + '</div></div>';
            });
            html += '</div></div>';
        }

        sprints.forEach(function(s) {
            var total = s.open + s.done;
            var progress = total > 0 ? Math.round(s.done / total * 100) : 0;
            var isActive = s.status === 'active';
            html += '<div class="sprint-card' + (isActive ? ' active' : '') + '">' +
                '<div class="sprint-header">' +
                    '<div>' +
                        '<span class="sprint-name">Sprint ' + s.num + ': ' + esc(s.name) + '</span>' +
                        (isActive ? ' <span class="eos-badge eos-badge-status-active">active</span>' : ' <span class="eos-badge eos-badge-status-completed">closed</span>') +
                    '</div>' +
                    '<span class="sprint-dates">' + s.start + ' — ' + s.end + '</span>' +
                '</div>' +
                (s.goal ? '<div class="sprint-goal">' + esc(s.goal) + '</div>' : '') +
                '<div class="sprint-stats">' +
                    '<span class="sprint-stat"><strong>' + s.done + '</strong> done</span>' +
                    '<span class="sprint-stat"><strong>' + s.open + '</strong> open</span>' +
                    '<span class="sprint-stat"><strong>' + progress + '%</strong></span>' +
                '</div>' +
                '<div class="progress-bar" style="margin-top:8px"><div class="progress-fill' + (progress===100?' done':'') + '" style="width:' + progress + '%"></div></div>';
            if (isActive && total > 0) {
                html += '<div style="margin-top:8px;text-align:right"><button class="eos-btn-sm eos-btn-ghost" onclick="closeSprint(\'' + escAttr(id) + '\',' + s.num + ')">Close Sprint</button></div>';
            }
            if (s.tasks && s.tasks.length) {
                html += '<div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px">';
                s.tasks.forEach(function(t) {
                    html += '<div style="font-size:12px;padding:3px 0;color:' + (t.done ? 'var(--text-muted)' : 'var(--text)') + '">' +
                        (t.done ? '<span style="color:var(--success)">✓</span> <s>' : '<span style="color:var(--border-strong)">○</span> ') +
                        esc(t.text) + (t.done ? '</s>' : '') + '</div>';
                });
                html += '</div>';
            }
            html += '</div>';
        });

        tc.innerHTML = html;
    } catch(e) {
        tc.innerHTML = '<div class="eos-empty">Failed to load sprints: ' + esc(e.message || String(e)) + '</div>';
    }
}

async function closeSprint(projectId, num) {
    if (!await EOS_UI.confirm('Close Sprint ' + num + '?')) return;
    try {
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/sprints/' + num + '/close', {});
        if (r.error) { EOS_UI.toast(r.error, false); return; }
        EOS_UI.toast('Sprint ' + num + ' closed');
        showDetail(projectId);
    } catch(e) {
        EOS_UI.toast('Failed to close sprint', false);
    }
}

// --- Milestones tab ---
async function loadMilestonesTab(p, id) {
    var tc = document.getElementById('tab-content');
    tc.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:8px 0">Loading milestones...</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/milestones');
        var milestones = r.milestones || [];
        var html = '<div style="display:flex;justify-content:flex-end;margin-bottom:10px">' +
            '<button class="eos-btn-sm" onclick="openCreateMilestone(\'' + escAttr(id) + '\')">+ New Milestone</button></div>';

        if (!milestones.length) {
            html += '<div class="eos-empty">No milestones yet. Create one to group tasks by deliverable.</div>';
            tc.innerHTML = html;
            return;
        }

        milestones.forEach(function(ms) {
            var isClosed = ms.status === 'closed';
            html += '<div class="ms-card' + (isClosed ? ' closed' : '') + '">' +
                '<div class="ms-header">' +
                    '<div><span class="ms-id">' + esc(ms.id) + '</span> <span class="ms-name">' + esc(ms.name) + '</span></div>' +
                    '<div>' +
                        (ms.target ? '<span class="ms-target">Target: ' + ms.target + '</span> ' : '') +
                        '<span class="eos-badge eos-badge-status-' + (isClosed ? 'completed' : 'active') + '">' + ms.status + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="sprint-stats">' +
                    '<span class="sprint-stat"><strong>' + ms.done + '</strong> done</span>' +
                    '<span class="sprint-stat"><strong>' + ms.open + '</strong> open</span>' +
                    '<span class="sprint-stat"><strong>' + ms.progress + '%</strong></span>' +
                '</div>' +
                '<div class="progress-bar" style="margin-top:8px"><div class="progress-fill' + (ms.progress===100?' done':'') + '" style="width:' + ms.progress + '%"></div></div>';
            if (ms.tasks && ms.tasks.length) {
                html += '<div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px">';
                ms.tasks.forEach(function(t) {
                    html += '<div style="font-size:12px;padding:3px 0;color:' + (t.done ? 'var(--text-muted)' : 'var(--text)') + '">' +
                        (t.done ? '<span style="color:var(--success)">✓</span> <s>' : '<span style="color:var(--border-strong)">○</span> ') +
                        esc(t.text) + (t.done ? '</s>' : '') + '</div>';
                });
                html += '</div>';
            }
            html += '</div>';
        });

        tc.innerHTML = html;
    } catch(e) {
        tc.innerHTML = '<div class="eos-empty">Failed to load milestones: ' + esc(e.message || String(e)) + '</div>';
    }
}

// --- Releases tab ---
async function loadReleasesTab(p, id) {
    var tc = document.getElementById('tab-content');
    tc.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:8px 0">Loading releases...</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/releases');
        var releases = r.releases || [];
        var html = '<div style="display:flex;justify-content:flex-end;margin-bottom:10px">' +
            '<button class="eos-btn-sm" onclick="openCreateRelease(\'' + escAttr(id) + '\')">+ New Release</button></div>';

        if (!releases.length) {
            html += '<div class="eos-empty">No releases yet. Create one to track version history.</div>';
            tc.innerHTML = html;
            return;
        }

        releases.forEach(function(rel) {
            html += '<div class="rel-card">' +
                '<div class="rel-header">' +
                    '<span class="rel-version">' + esc(rel.version) + '</span>' +
                    '<span class="rel-date">' + rel.date + '</span>' +
                '</div>';
            if (rel.notes && rel.notes.length) {
                html += '<ul class="rel-notes" style="margin:4px 0 0 16px;padding:0">';
                rel.notes.forEach(function(n) {
                    html += '<li>' + esc(n) + '</li>';
                });
                html += '</ul>';
            }
            html += '</div>';
        });

        tc.innerHTML = html;
    } catch(e) {
        tc.innerHTML = '<div class="eos-empty">Failed to load releases: ' + esc(e.message || String(e)) + '</div>';
    }
}
