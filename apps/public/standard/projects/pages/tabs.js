// Projects — tab content loaders. Called from app.js via TAB_RENDERERS.

// Assignee bridge to staff's workflow-agent dispatch engine — see
// .claude/rules dev notes / docs/OPEN-SOURCE-BORROWING-PLAN.md (Multica
// borrow verdict). `queued/running/done/error` mirrors staff's job status
// verbatim; `unknown` covers staff-absent or a job that aged out of its
// 200-job retention window.
var ASSIGN_STATUS_MAP = {queued: 'draft', running: 'running', done: 'completed', error: 'fail', unknown: ''};

function renderTaskMeta(meta, taskLine) {
    if (!meta || !meta.length) return '';
    var assigned = meta.find(function(m) { return m.type === 'assigned'; });
    var filtered = meta.filter(function(m) { return m.type !== 'depends_on' && m.type !== 'blocks' && m.type !== 'assigned'; });
    var out = [];
    if (assigned) {
        var agentId = (assigned.value || '').split(':')[0];
        out.push('<div class="task-meta-item" id="assign-badge-' + taskLine + '">' +
            EOS_UI.statusBadge('🤖 ' + agentId, 'queued', ASSIGN_STATUS_MAP) +
        '</div>');
    }
    filtered.forEach(function(m) {
        if (m.type === 'sprint') { out.push('<div class="task-meta-item"><span class="dep-badge" style="background:rgba(59,130,246,0.15);color:var(--info)">Sprint ' + esc(m.value) + '</span></div>'); return; }
        if (m.type === 'milestone') { out.push('<div class="task-meta-item"><span class="dep-badge" style="background:color-mix(in srgb, var(--purple) 15%, transparent);color:var(--purple)">' + esc(m.value) + '</span></div>'); return; }
        var cls = 'meta-type' + (m.type === 'need' ? ' meta-type-need' : m.type === 'calc' ? ' meta-type-calc' : '');
        out.push('<div class="task-meta-item"><span class="' + cls + '">' + m.type + ':</span> ' + esc(m.value) + '</div>');
    });
    if (!out.length) return '';
    return '<div class="task-meta">' + out.join('') + '</div>';
}

// Lazily hydrate each "assigned" badge with live staff job status. No
// websocket for v1 — one cheap fetch per assigned task, fired once per
// tab render (staged-pipeline.md's "no consumer yet" bar for a live
// stream isn't met: staff jobs are single-shot think() calls, not a
// multi-step tool loop with a transcript to stream).
function refreshAssignmentBadges(id, tasks) {
    (tasks || []).forEach(function(t) {
        var hasAssign = (t.meta || []).some(function(m) { return m.type === 'assigned'; });
        if (!hasAssign) return;
        EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/tasks/' + t.line + '/assignment').then(function(r) {
            var el = document.getElementById('assign-badge-' + t.line);
            if (!el || !r || !r.assigned) return;
            var status = r.status || 'unknown';
            // EOS_UI.statusBadge escapes `label` internally — pass raw text.
            var label = '🤖 ' + (r.agent_id || '') + ' · ' + status;
            el.innerHTML = EOS_UI.statusBadge(label, status, ASSIGN_STATUS_MAP);
        }).catch(function() {});
    });
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

        return '<div class="' + itemCls + '" title="Click to complete · right-click to complete with a note" ' +
            'onclick="toggleTask(\'' + escAttr(id) + '\',' + t.line + ',' + escAttr(JSON.stringify(t.text || '')) + ')" ' +
            'oncontextmenu="event.preventDefault();completeProjectTaskNote(\'' + escAttr(id) + '\',' + t.line + ',' + escAttr(JSON.stringify(t.text || '')) + ');return false">' +
            '<div class="task-check ' + (t.done ? 'done' : '') + '">' + (t.done ? '✓' : '') + '</div>' +
            '<div style="flex:1">' +
                '<div class="task-text ' + (t.done ? 'done' : '') + '">' + esc(t.text) + '</div>' +
                depInfo +
                renderTaskMeta(t.meta, t.line) +
            '</div>' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="event.stopPropagation();openAssignTask(\'' + escAttr(id) + '\',' + t.line + ')" title="Assign to an agent" style="font-size:10px;padding:2px 6px">🤖</button>' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="event.stopPropagation();openAddMeta(\'' + escAttr(id) + '\',' + t.line + ')" title="Add info" style="font-size:10px;padding:2px 6px">+meta</button>' +
        '</div>';
    }).join('') || '<div style="color:var(--text-muted);font-size:13px;padding:8px 0">No tasks yet</div>';

    var depSummary = '';
    if (hasDeps) {
        depSummary = '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">' +
            '<span style="color:var(--success)">' + (p.ready_count || 0) + ' ready</span> · ' +
            '<span style="color:var(--danger)">' + (p.blocked_count || 0) + ' blocked</span> · ' +
            '<a href="#" onclick="toggleDepMap(event,\'' + escAttr(id) + '\')" style="color:var(--accent);text-decoration:none">🔗 Dependency map</a> · ' +
            '<a href="#" onclick="checkCascadeDates(event,\'' + escAttr(id) + '\')" style="color:var(--accent);text-decoration:none" title="Find tasks due before a blocker">🗓 Check dates</a>' +
        '</div>';
    }

    document.getElementById('tab-content').innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
            depSummary +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="openAddTask(\'' + escAttr(id) + '\')">+ Add Task</button>' +
        '</div>' +
        '<div id="dep-map" style="display:none;margin-bottom:10px"></div>' +
        tasksHtml;
    refreshAssignmentBadges(id, p.tasks || []);
}

// ── Date-cascade check: surface tasks due before a blocker, offer to fix ──
async function checkCascadeDates(ev, id) {
    if (ev) ev.preventDefault();
    var el = document.getElementById('dep-map');
    if (!el) return;
    el.style.display = 'block';
    el.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px">Checking dates…</div>';
    var res;
    try {
        res = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/cascade/check');
    } catch (e) {
        el.innerHTML = EOS_UI.errorState({message: 'Could not check dates.'});
        return;
    }
    if (res.cycle) {
        el.innerHTML = '<div style="color:var(--danger);font-size:12px;padding:8px">Dependency cycle — fix the loop before cascading dates.</div>';
        return;
    }
    // An error carries no `violations`, so it must be caught before the
    // empty-list check below — otherwise a failure renders as a green all-clear.
    if (res.error || !res.ok) {
        el.innerHTML = '<div style="color:var(--danger);font-size:12px;padding:8px">' +
            esc(res.error || 'Could not check dates.') + '</div>';
        return;
    }
    var v = res.violations || [];
    if (!v.length) {
        el.innerHTML = '<div style="color:var(--success);font-size:12px;padding:8px">✓ Every task is due on or after its blockers.</div>';
        return;
    }
    var rows = v.map(function(r) {
        return '<div style="padding:4px 0">' +
            '<strong>' + esc(r.text) + '</strong> — due <span style="color:var(--danger)">' + esc(r.old_due) +
            '</span> before a blocker → move to <span style="color:var(--success)">' + esc(r.new_due) + '</span></div>';
    }).join('');
    el.innerHTML =
        '<div style="border:1px solid var(--border);border-radius:8px;padding:12px;font-size:12px">' +
        '<div style="margin-bottom:8px;color:var(--text-secondary)">' + v.length +
        ' task' + (v.length === 1 ? '' : 's') + ' due before a blocker:</div>' + rows +
        '<div style="margin-top:10px;display:flex;gap:8px">' +
            '<button class="eos-btn-sm eos-btn-primary" id="cascade-apply-btn">Fix ' + v.length + ' date' + (v.length === 1 ? '' : 's') + '</button>' +
            '<button class="eos-btn-sm eos-btn-ghost" onclick="document.getElementById(\'dep-map\').style.display=\'none\'">Dismiss</button>' +
        '</div></div>';
    document.getElementById('cascade-apply-btn').onclick = function() { applyCascade(id, v); };
}

async function applyCascade(id, shifts) {
    var btn = document.getElementById('cascade-apply-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Applying…'; }
    try {
        var res = await EOS.post('/projects/api/projects/' + encodeURIComponent(id) + '/cascade/apply', { shifts: shifts });
        if (res && res.ok) {
            EOS_UI.toast('Moved ' + res.applied + ' date' + (res.applied === 1 ? '' : 's') +
                (res.skipped ? ' (' + res.skipped + ' skipped)' : ''), true);
            // Re-render the workspace with fresh dates (same path as toggleTask),
            // then re-check to surface the next violation in a dependency chain.
            if (typeof openProjectWorkspace === 'function') { openProjectWorkspace(id); }
            setTimeout(function() { checkCascadeDates(null, id); }, 300);
        } else {
            EOS_UI.toast((res && res.error) || 'Could not apply', false);
        }
    } catch (e) {
        EOS_UI.toast('Could not apply: ' + e, false);
        if (btn) { btn.disabled = false; btn.textContent = 'Fix dates'; }
    }
}

// ── Dependency graph (layered SVG, no external lib) ─────────────────────
async function toggleDepMap(ev, id) {
    if (ev) ev.preventDefault();
    var el = document.getElementById('dep-map');
    if (!el) return;
    if (el.style.display !== 'none') { el.style.display = 'none'; return; }
    el.style.display = 'block';
    el.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px">Loading dependency map…</div>';
    try {
        var g = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) + '/dependency-graph');
        el.innerHTML = renderDepGraphSvg(g.nodes || [], g.edges || []);
    } catch (e) {
        el.innerHTML = EOS_UI.errorState({message: 'Could not load dependency map.'});
    }
}

// Pure layout: assign each node a layer = longest precedence-path depth from a
// root, so dependencies sit left of the tasks that need them. Cycle-safe (caps
// the relaxation passes at node-count). Returns {layers:{id:int}, edges:deduped}.
function depGraphLayers(nodes, edges) {
    var dedup = {}, clean = [];
    edges.forEach(function(e) {
        // Both depends_on and blocks already encode "from precedes to".
        var key = e.from + '>' + e.to;
        if (e.from === e.to || dedup[key]) return;
        dedup[key] = 1; clean.push({from: e.from, to: e.to});
    });
    var layer = {};
    nodes.forEach(function(n) { layer[n.id] = 0; });
    for (var pass = 0; pass < nodes.length + 1; pass++) {
        var changed = false;
        clean.forEach(function(e) {
            if (layer[e.to] < layer[e.from] + 1) { layer[e.to] = layer[e.from] + 1; changed = true; }
        });
        if (!changed) break;
    }
    return {layers: layer, edges: clean};
}

function renderDepGraphSvg(nodes, edges) {
    if (!nodes.length) return '<div style="color:var(--text-muted);font-size:12px;padding:8px">No tasks to map.</div>';
    var lay = depGraphLayers(nodes, edges);
    var BW = 168, BH = 34, COLGAP = 56, ROWGAP = 14, PAD = 12;
    // Bucket nodes by layer, preserving order for stable rows.
    var cols = {};
    nodes.forEach(function(n) { (cols[lay.layers[n.id]] = cols[lay.layers[n.id]] || []).push(n); });
    var maxLayer = Math.max.apply(null, nodes.map(function(n){ return lay.layers[n.id]; }));
    var pos = {}; // id -> {x,y,cx,cy}
    var maxRows = 0;
    for (var L = 0; L <= maxLayer; L++) {
        var list = cols[L] || [];
        maxRows = Math.max(maxRows, list.length);
        list.forEach(function(n, r) {
            var x = PAD + L * (BW + COLGAP);
            var y = PAD + r * (BH + ROWGAP);
            pos[n.id] = {x: x, y: y, cx: x, cyin: y + BH / 2, cxout: x + BW, cy: y + BH / 2};
        });
    }
    var W = PAD * 2 + (maxLayer + 1) * BW + maxLayer * COLGAP;
    var H = PAD * 2 + maxRows * BH + (maxRows - 1) * ROWGAP;
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" style="max-width:' + W +
        'px;border:1px solid var(--border);border-radius:8px;background:var(--bg)">';
    // Edges first (under nodes). Source right-edge → target left-edge.
    lay.edges.forEach(function(e) {
        var a = pos[e.from], b = pos[e.to];
        if (!a || !b) return;
        var x1 = a.cxout, y1 = a.cy, x2 = b.cx, y2 = b.cyin;
        var mx = (x1 + x2) / 2;
        svg += '<path d="M' + x1 + ' ' + y1 + ' C' + mx + ' ' + y1 + ' ' + mx + ' ' + y2 + ' ' + x2 + ' ' + y2 +
            '" fill="none" stroke="var(--border-strong)" stroke-width="1.5" marker-end="url(#dep-arrow)"/>';
    });
    svg += '<defs><marker id="dep-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">' +
        '<path d="M0,0 L7,3 L0,6 Z" fill="var(--border-strong)"/></marker></defs>';
    // Nodes.
    nodes.forEach(function(n) {
        var pn = pos[n.id]; if (!pn) return;
        var fill, stroke;
        if (n.done) { fill = 'color-mix(in srgb,var(--success) 18%,transparent)'; stroke = 'var(--success)'; }
        else if (n.ready) { fill = 'color-mix(in srgb,var(--accent) 14%,transparent)'; stroke = 'var(--accent)'; }
        else { fill = 'color-mix(in srgb,var(--danger) 14%,transparent)'; stroke = 'var(--danger)'; }
        var label = (n.text || '').length > 24 ? n.text.slice(0, 23) + '…' : (n.text || '');
        svg += '<g>' +
            '<rect x="' + pn.x + '" y="' + pn.y + '" width="' + BW + '" height="' + BH + '" rx="7" ' +
                'fill="' + fill + '" stroke="' + stroke + '" stroke-width="1.5"/>' +
            '<text x="' + (pn.x + 9) + '" y="' + (pn.y + BH / 2 + 4) + '" font-size="11" fill="var(--text)">' +
                (n.done ? '✓ ' : '') + esc(label) + '</text>' +
        '</g>';
    });
    svg += '</svg>';
    var legend = '<div style="font-size:10px;color:var(--text-muted);margin-top:4px">' +
        '<span style="color:var(--accent)">■</span> ready · ' +
        '<span style="color:var(--danger)">■</span> blocked · ' +
        '<span style="color:var(--success)">■</span> done — arrows point dependency → dependent</div>';
    return svg + legend;
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
        // When openDocInline (workspace-page.js) is loaded, open + edit the doc
        // in-page; otherwise fall back to opening it externally (defensive).
        var inlineEdit = typeof openDocInline === 'function';
        docs.forEach(function(d) {
            var icon = d.is_main ? '📋' : '📄';
            var sizeKb = d.size > 1024 ? Math.round(d.size/1024) + ' KB' : d.size + ' B';
            var open = inlineEdit
                ? 'openDocInline(\'' + escAttr(id) + '\',\'' + escAttr(d.rel_path) + '\',\'' + escAttr(d.name) + '\')'
                : 'EOS.viewNote(\'' + escAttr(d.path) + '\')';
            html += '<div class="doc-item" onclick="' + open + '">' +
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
        tc.innerHTML = EOS_UI.errorState({message: 'Failed to load docs: ' + (e.message || String(e)), onRetry: 'loadDocsTab(' + JSON.stringify(id) + ')'});
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
            var fid = 'calc-' + String(c.file).replace(/[^a-z0-9]/gi, '_');
            var when = c.timestamp ? ' · ' + esc(c.timestamp) : '';
            return '<div class="calc-item" style="flex-direction:column;align-items:stretch">' +
                '<div style="display:flex;gap:8px;align-items:center">' +
                    '<div style="flex:1">' +
                        '<div style="font-weight:500;font-size:13px">' + esc(c.app) + ' / ' + esc(c.method) + '</div>' +
                        '<div style="font-size:11px;color:var(--text-muted)">' + esc(c.summary) + when + '</div>' +
                    '</div>' +
                    '<button class="eos-btn-sm eos-btn-ghost" onclick="toggleCalc(this,\'' + escAttr(id) + '\',\'' + escAttr(c.file) + '\',\'' + fid + '\')">View</button>' +
                '</div>' +
                '<div id="' + fid + '" style="display:none;margin-top:8px"></div>' +
            '</div>';
        }).join('');
        tc.innerHTML = html;
    } catch(e) {
        tc.innerHTML = EOS_UI.errorState({message: 'Failed to load calculations', onRetry: 'loadCalcsTab(' + JSON.stringify(id) + ')'});
    }
}

async function toggleCalc(btn, id, file, fid) {
    var el = document.getElementById(fid);
    if (!el) return;
    if (el.style.display !== 'none') { el.style.display = 'none'; btn.textContent = 'View'; return; }
    el.style.display = 'block'; btn.textContent = 'Hide';
    if (el.dataset.loaded) return;
    el.innerHTML = '<div style="color:var(--text-muted);font-size:12px">Loading…</div>';
    try {
        var r = await EOS.api('/projects/api/projects/' + encodeURIComponent(id) +
            '/calculations/' + encodeURIComponent(file));
        if (r.error) { el.innerHTML = '<div style="color:var(--text-muted);font-size:12px">' + esc(r.error) + '</div>'; return; }
        el.innerHTML = renderCalcDetail(r.data);
        el.dataset.loaded = '1';
    } catch (e) {
        el.innerHTML = EOS_UI.errorState({message: 'Failed to load result'});
    }
}

// Scalar fields → key/value table; nested objects → pretty-printed JSON block.
function renderCalcDetail(data) {
    if (!data || typeof data !== 'object') return '<pre class="calc-json">' + esc(String(data)) + '</pre>';
    var rows = '', nested = {}, nestedCount = 0;
    Object.keys(data).forEach(function(k) {
        var v = data[k];
        if (v && typeof v === 'object') { nested[k] = v; nestedCount++; return; }
        rows += '<tr><td style="padding:2px 12px 2px 0;color:var(--text-muted);font-size:12px;vertical-align:top">' + esc(k) + '</td>' +
                '<td style="padding:2px 0;font-size:12px;font-weight:500">' + esc(String(v)) + '</td></tr>';
    });
    var out = '';
    if (rows) out += '<table style="border-collapse:collapse">' + rows + '</table>';
    if (nestedCount) out += '<pre class="calc-json" style="margin-top:6px;max-height:260px;overflow:auto;' +
        'font-size:11px;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px">' +
        esc(JSON.stringify(nested, null, 2)) + '</pre>';
    return out || '<div style="color:var(--text-muted);font-size:12px">Empty result</div>';
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
        tc.innerHTML = EOS_UI.errorState({message: 'Failed to load git data: ' + (e.message || String(e))});
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
        tc.innerHTML = EOS_UI.errorState({message: 'Failed to load sprints: ' + (e.message || String(e))});
    }
}

async function closeSprint(projectId, num) {
    if (!await EOS_UI.confirm('Close Sprint ' + num + '?')) return;
    try {
        var r = await EOS.post('/projects/api/projects/' + encodeURIComponent(projectId) + '/sprints/' + num + '/close', {});
        if (r.error) { EOS_UI.toast(r.error, false); return; }
        EOS_UI.toast('Sprint ' + num + ' closed');
        openProjectWorkspace(projectId);
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
        tc.innerHTML = EOS_UI.errorState({message: 'Failed to load milestones: ' + (e.message || String(e))});
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
        tc.innerHTML = EOS_UI.errorState({message: 'Failed to load releases: ' + (e.message || String(e))});
    }
}
