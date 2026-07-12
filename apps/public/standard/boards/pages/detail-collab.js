// Boards — detail-pane collaboration surfaces (comments, attachments,
// checklists). Sibling of boards.js (loaded AFTER it, same global scope) so
// the 2.5k-line main file doesn't grow — see .claude/rules/multi-module-apps.md
// "Frontend counterpart". Shares globals: currentBoardId, currentDetailFile,
// boardItems, boardConfig, COLUMN_RENDERERS, COLUMN_FORM_INPUTS, esc, escAttr,
// EOS_UI, isReadonly, switchView.

// ── Shared helpers ─────────────────────────────────────────

function parseChecklist(val) {
    // Tolerant of: JSON string (vault storage), array (app-source / local
    // state), null/empty. Always returns [{text, done}]. The one parser
    // lives in planner-xlsx.js (EOS_PLANNER.normChecklist — loaded before
    // this file); defensive [] if that script ever fails to load.
    return window.EOS_PLANNER ? EOS_PLANNER.normChecklist(val) : [];
}

// PATCH one field without updateField()'s EOS_IS_EXPORT early-return — the
// export shim intercepts the fetch, so detail-pane edits work offline too.
async function collabPatchField(file, field, value) {
    if (typeof isReadonly === 'function' && isReadonly()) return false;
    try {
        var r = await fetch('/boards/api/boards/' + currentBoardId + '/items/' + encodeURIComponent(file), {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({updates: {[field]: value}})
        });
        var resp = await r.json();
        if (resp.error) { EOS_UI.toast(resp.error, false); return false; }
        var item = boardItems.find(function(i) { return (i.file || i.id) === file; });
        if (item) item[field] = value;
        return true;
    } catch (e) { console.error('collabPatchField', e); return false; }
}

function _itemUrl(file, tail) {
    return '/boards/api/boards/' + encodeURIComponent(currentBoardId) +
        '/items/' + encodeURIComponent(file) + (tail || '');
}

// ── Checklist column ───────────────────────────────────────

COLUMN_RENDERERS['checklist'] = function(item, col, val) {
    var items = parseChecklist(val);
    var key = item.file || item.id;
    if (!items.length) {
        return '<span class="cell-editable" style="color:var(--text-muted);font-size:0.75rem" ' +
            'onclick="openItemDetail(' + escAttr(JSON.stringify(key)) + ')">+ checklist</span>';
    }
    var done = items.filter(function(i) { return i.done; }).length;
    var pct = Math.round(done / items.length * 100);
    return '<span class="cell-editable checklist-cell" onclick="openItemDetail(' + escAttr(JSON.stringify(key)) + ')" ' +
        'title="' + escAttr(done + ' of ' + items.length + ' done') + '" style="display:inline-flex;align-items:center;gap:0.35rem">' +
        '<span class="checklist-bar" style="width:42px;height:5px;border-radius:3px;background:var(--border);overflow:hidden;display:inline-block">' +
        '<span style="display:block;height:100%;width:' + pct + '%;background:var(--success)"></span></span>' +
        '<span style="font-size:0.74rem;color:var(--text-secondary)">' + done + '/' + items.length + '</span></span>';
};

// Fields-form slot: display-only pointer (the live widget is its own detail
// section below). No data-field attr so saveItemDetail() never collects it.
if (typeof COLUMN_FORM_INPUTS !== 'undefined') {
    COLUMN_FORM_INPUTS['checklist'] = function(col, val) {
        var items = parseChecklist(val);
        var done = items.filter(function(i) { return i.done; }).length;
        return '<div style="color:var(--text-muted);font-size:0.78rem;padding:0.3rem 0">' +
            (items.length ? done + '/' + items.length + ' done — edit in the Checklist section below' : 'Edit in the Checklist section below') + '</div>';
    };
    COLUMN_FORM_INPUTS['attachment'] = function(col, val) {
        return '<div style="color:var(--text-muted);font-size:0.78rem;padding:0.3rem 0">Managed in the Attachments section below</div>';
    };
}

function renderChecklistSection(file) {
    var section = document.getElementById('detail-checklist-section');
    var host = document.getElementById('detail-checklists');
    if (!section || !host) return;
    var cols = (boardConfig.columns || []).filter(function(c) { return c.type === 'checklist'; });
    if (!cols.length) { section.style.display = 'none'; host.innerHTML = ''; return; }
    var item = boardItems.find(function(i) { return (i.file || i.id) === file; }) || {};
    section.style.display = '';
    host.innerHTML = cols.map(function(c) {
        var items = parseChecklist(item[c.id]);
        var rows = items.map(function(it, idx) {
            return '<div class="checklist-row">' +
                '<label style="display:flex;align-items:center;gap:0.45rem;flex:1;cursor:pointer">' +
                '<input type="checkbox"' + (it.done ? ' checked' : '') +
                ' onchange="toggleChecklistItem(' + escAttr(JSON.stringify(c.id)) + ',' + idx + ',this.checked)">' +
                '<span style="' + (it.done ? 'text-decoration:line-through;color:var(--text-muted)' : '') + '">' + esc(it.text) + '</span></label>' +
                '<button class="btn btn-sm checklist-del" title="Remove" ' +
                'onclick="removeChecklistItem(' + escAttr(JSON.stringify(c.id)) + ',' + idx + ')">×</button></div>';
        }).join('');
        return '<div class="checklist-block" data-col="' + escAttr(c.id) + '">' +
            (cols.length > 1 ? '<div class="checklist-col-label">' + esc(c.label || c.id) + '</div>' : '') +
            rows +
            '<div class="checklist-add" style="display:flex;gap:0.4rem;margin-top:0.35rem">' +
            '<input class="form-input" style="flex:1" placeholder="Add item…" ' +
            'onkeydown="if(event.key===\'Enter\'){addChecklistItem(' + escAttr(JSON.stringify(c.id)) + ',this)}">' +
            '</div></div>';
    }).join('');
}

function _checklistOf(colId) {
    var item = boardItems.find(function(i) { return (i.file || i.id) === currentDetailFile; }) || {};
    return parseChecklist(item[colId]);
}

async function toggleChecklistItem(colId, idx, checked) {
    var list = _checklistOf(colId);
    if (!list[idx]) return;
    list[idx].done = !!checked;
    if (await collabPatchField(currentDetailFile, colId, list)) {
        renderChecklistSection(currentDetailFile);
        switchView(currentView);
    }
}

async function addChecklistItem(colId, inputEl) {
    var text = (inputEl.value || '').trim();
    if (!text) return;
    var list = _checklistOf(colId);
    list.push({text: text, done: false});
    if (await collabPatchField(currentDetailFile, colId, list)) {
        renderChecklistSection(currentDetailFile);
        switchView(currentView);
    }
}

async function removeChecklistItem(colId, idx) {
    var list = _checklistOf(colId);
    list.splice(idx, 1);
    if (await collabPatchField(currentDetailFile, colId, list)) {
        renderChecklistSection(currentDetailFile);
        switchView(currentView);
    }
}

// ── Comments ───────────────────────────────────────────────

async function loadItemComments(file) {
    var host = document.getElementById('detail-comments');
    if (!host) return;
    host.innerHTML = '<em>Loading…</em>';
    try {
        var r = await fetch(_itemUrl(file, '/comments'));
        var data = await r.json();
        renderComments(data.comments || []);
    } catch (e) {
        host.innerHTML = '<p style="color:var(--text-muted);font-size:0.78rem">Comments unavailable.</p>';
    }
}

function renderComments(comments) {
    var host = document.getElementById('detail-comments');
    if (!host) return;
    if (!comments.length) {
        host.innerHTML = '<p style="color:var(--text-muted);font-size:0.78rem;margin:0.2rem 0">No comments yet.</p>';
        return;
    }
    host.innerHTML = comments.map(function(c) {
        var t = String(c.created || '').slice(0, 16).replace('T', ' ');
        return '<div class="comment-row" data-cid="' + escAttr(c.id) + '">' +
            '<div class="comment-head">' +
            '<span class="comment-author">' + esc(c.author || 'me') + '</span>' +
            '<span class="comment-time">' + esc(t) + (c.edited ? ' · edited' : '') + '</span>' +
            '<span class="comment-actions">' +
            '<button class="btn btn-sm" title="Edit" onclick="startEditComment(' + escAttr(JSON.stringify(c.id)) + ')">✎</button>' +
            '<button class="btn btn-sm" title="Delete" onclick="deleteComment(' + escAttr(JSON.stringify(c.id)) + ')">×</button>' +
            '</span></div>' +
            '<div class="comment-text">' + esc(c.text) + '</div></div>';
    }).join('');
}

async function submitComment() {
    var input = document.getElementById('comment-input');
    if (!input || !currentDetailFile) return;
    var text = (input.value || '').trim();
    if (!text) return;
    try {
        var r = await fetch(_itemUrl(currentDetailFile, '/comments'), {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: text})
        });
        var resp = await r.json();
        if (resp.error) { EOS_UI.toast(resp.error, false); return; }
        input.value = '';
        loadItemComments(currentDetailFile);
        if (typeof loadItemActivity === 'function') loadItemActivity(currentDetailFile);
    } catch (e) { console.error('submitComment', e); }
}

function startEditComment(cid) {
    var row = document.querySelector('.comment-row[data-cid="' + CSS.escape(cid) + '"]');
    if (!row) return;
    var textEl = row.querySelector('.comment-text');
    if (!textEl || row.querySelector('textarea')) return;
    var current = textEl.textContent;
    textEl.innerHTML = '<textarea class="form-input" style="width:100%;min-height:52px"></textarea>' +
        '<div style="display:flex;gap:0.4rem;margin-top:0.3rem;justify-content:flex-end">' +
        '<button class="btn btn-sm" onclick="loadItemComments(currentDetailFile)">Cancel</button>' +
        '<button class="btn btn-sm btn-primary" onclick="saveEditComment(' + escAttr(JSON.stringify(cid)) + ',this)">Save</button></div>';
    var ta = textEl.querySelector('textarea');
    ta.value = current;
    ta.focus();
}

async function saveEditComment(cid, btnEl) {
    var row = btnEl.closest('.comment-row');
    var ta = row && row.querySelector('textarea');
    var next = ta ? ta.value.trim() : '';
    if (!next) return;
    try {
        var r = await fetch(_itemUrl(currentDetailFile, '/comments/' + encodeURIComponent(cid)), {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: next})
        });
        var resp = await r.json();
        if (resp.error) { EOS_UI.toast(resp.error, false); return; }
        loadItemComments(currentDetailFile);
    } catch (e) { console.error('saveEditComment', e); }
}

async function deleteComment(cid) {
    if (!await EOS_UI.confirm({message: 'Delete this comment?', action: 'Delete', danger: true})) return;
    try {
        await fetch(_itemUrl(currentDetailFile, '/comments/' + encodeURIComponent(cid)), {method: 'DELETE'});
        loadItemComments(currentDetailFile);
    } catch (e) { console.error('deleteComment', e); }
}

// ── Attachments ────────────────────────────────────────────

var ATTACH_COUNTS = {};   // {item_key: count} for the 📎N column renderer

COLUMN_RENDERERS['attachment'] = function(item, col, val) {
    var key = item.file || item.id;
    var n = ATTACH_COUNTS[key] || 0;
    var label = n ? '📎 ' + n : '<span style="color:var(--text-muted);font-size:0.75rem">📎 —</span>';
    return '<span class="cell-editable" onclick="openItemDetail(' + escAttr(JSON.stringify(key)) + ')">' + label + '</span>';
};

async function loadAttachmentCounts() {
    // Called after board load when an attachment column exists; one request.
    if (window.EOS_IS_EXPORT) return;
    if (!(boardConfig.columns || []).some(function(c) { return c.type === 'attachment'; })) return;
    try {
        var r = await fetch('/boards/api/boards/' + encodeURIComponent(currentBoardId) + '/attachments-index');
        var data = await r.json();
        ATTACH_COUNTS = (data && data.counts) || {};
        switchView(currentView);
    } catch (_) {}
}

async function loadItemAttachments(file) {
    var host = document.getElementById('detail-attachments');
    var section = document.getElementById('detail-attachments-section');
    if (!host || !section) return;
    section.style.display = '';
    var uploadRow = document.getElementById('attachment-upload-row');
    if (uploadRow) uploadRow.style.display = window.EOS_IS_EXPORT ? 'none' : '';
    host.innerHTML = '<em>Loading…</em>';
    try {
        var r = await fetch(_itemUrl(file, '/attachments'));
        var data = await r.json();
        var items = (data && data.attachments) || [];
        if (!items.length) {
            host.innerHTML = '<p style="color:var(--text-muted);font-size:0.78rem;margin:0.2rem 0">' +
                (window.EOS_IS_EXPORT ? 'Attachments are not bundled in offline exports.' : 'No attachments.') + '</p>';
            return;
        }
        host.innerHTML = items.map(function(a) {
            var kb = a.size >= 1048576 ? (a.size / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round((a.size || 0) / 1024)) + ' KB';
            var name = esc(a.name);
            var body = window.EOS_IS_EXPORT
                ? '<span class="attachment-name">' + name + '</span>'
                : '<a class="attachment-name" href="' + escAttr(a.url) + '" download>' + name + '</a>';
            var del = window.EOS_IS_EXPORT ? '' :
                '<button class="btn btn-sm" title="Delete" onclick="deleteAttachment(' + escAttr(JSON.stringify(a.name)) + ')">×</button>';
            return '<div class="attachment-row">📎 ' + body +
                '<span class="attachment-size">' + kb + '</span>' + del + '</div>';
        }).join('');
    } catch (e) {
        host.innerHTML = '<p style="color:var(--text-muted);font-size:0.78rem">Attachments unavailable offline.</p>';
    }
}

async function uploadAttachmentFiles(fileList) {
    if (!currentDetailFile || !fileList || !fileList.length) return;
    for (var i = 0; i < fileList.length; i++) {
        var fd = new FormData();
        fd.append('file', fileList[i]);
        try {
            var r = await fetch(_itemUrl(currentDetailFile, '/attachments'), {method: 'POST', body: fd});
            var resp = await r.json();
            if (resp.error) EOS_UI.toast(resp.error, false);
        } catch (e) { console.error('uploadAttachment', e); }
    }
    loadItemAttachments(currentDetailFile);
    loadAttachmentCounts();
}

function onAttachmentInputChange(inputEl) {
    uploadAttachmentFiles(inputEl.files);
    inputEl.value = '';
}

async function deleteAttachment(name) {
    if (!await EOS_UI.confirm({message: 'Delete attachment "' + name + '"?', action: 'Delete', danger: true})) return;
    try {
        await fetch(_itemUrl(currentDetailFile, '/attachments/' + encodeURIComponent(name)), {method: 'DELETE'});
        loadItemAttachments(currentDetailFile);
        loadAttachmentCounts();
    } catch (e) { console.error('deleteAttachment', e); }
}

// Drag-drop onto the attachments section.
(function wireAttachmentDrop() {
    document.addEventListener('dragover', function(e) {
        var section = e.target && e.target.closest && e.target.closest('#detail-attachments-section');
        if (section) { e.preventDefault(); section.classList.add('drop-hover'); }
    });
    document.addEventListener('dragleave', function(e) {
        var section = e.target && e.target.closest && e.target.closest('#detail-attachments-section');
        if (section) section.classList.remove('drop-hover');
    });
    document.addEventListener('drop', function(e) {
        var section = e.target && e.target.closest && e.target.closest('#detail-attachments-section');
        if (!section) return;
        e.preventDefault();
        section.classList.remove('drop-hover');
        if (!window.EOS_IS_EXPORT && e.dataTransfer && e.dataTransfer.files.length) {
            uploadAttachmentFiles(e.dataTransfer.files);
        }
    });
})();

// ── Richer activity renderer ───────────────────────────────
// Replaces boards.js loadItemActivity: understands the durable log's
// {old, new} update shape (the old renderer would print [object Object]),
// shows the actor, and labels comment/attachment events.

loadItemActivity = async function(file) {
    var out = document.getElementById('detail-activity');
    if (!out) return;
    out.innerHTML = '<em>Loading…</em>';
    function fmtVal(v) {
        if (v === null || v === undefined || v === '') return '∅';
        if (Array.isArray(v)) return v.length + ' item' + (v.length === 1 ? '' : 's');
        var s = String(v);
        return s.length > 40 ? s.slice(0, 37) + '…' : s;
    }
    try {
        var r = await fetch(_itemUrl(file, '/activity'));
        var data = await r.json();
        var events = (data && data.events) || [];
        if (!events.length) { out.innerHTML = EOS_UI.emptyState({message: 'No activity yet.'}); return; }
        out.innerHTML = events.map(function(e) {
            var t = (e.timestamp || '').slice(0, 16).replace('T', ' ');
            var label = String(e.type || '').replace('board:', '').replace(/_/g, ' ');
            var actor = e.actor ? '<span class="activity-actor">' + esc(e.actor) + '</span> ' : '';
            var parts = [];
            var updates = e.updates || {};
            Object.keys(updates).forEach(function(k) {
                var u = updates[k];
                if (u && typeof u === 'object' && ('old' in u || 'new' in u)) {
                    parts.push(esc(k) + ': ' + esc(fmtVal(u.old)) + ' → ' + esc(fmtVal(u.new)));
                } else {
                    parts.push(esc(k) + ' → ' + esc(fmtVal(u)));
                }
            });
            var d = e.data || {};
            if (d.name) parts.push(esc(fmtVal(d.name)));
            return '<div class="activity-row">' + actor +
                '<span class="activity-label">' + esc(label) + '</span>' +
                (parts.length ? '<span class="activity-detail">' + parts.join(', ') + '</span>' : '') +
                '<span class="activity-time">' + esc(t) + '</span></div>';
        }).join('');
    } catch (e) {
        out.innerHTML = '<p style="color:var(--board-text-dim)">Activity unavailable in offline mode.</p>';
    }
};

// ── Hook into the detail-pane lifecycle ────────────────────

var _collabOrigOpenItemDetail = openItemDetail;
openItemDetail = async function(file) {
    await _collabOrigOpenItemDetail(file);
    if (!currentDetailFile) return;   // original bailed (item not found)
    loadItemComments(file);
    loadItemAttachments(file);
    renderChecklistSection(file);
};

var _collabOrigLoadBoard = typeof loadBoard === 'function' ? loadBoard : null;
if (_collabOrigLoadBoard) {
    loadBoard = async function(id) {
        var out = await _collabOrigLoadBoard(id);
        loadAttachmentCounts();
        return out;
    };
}
