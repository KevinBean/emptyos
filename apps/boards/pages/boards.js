if (window.EOS && EOS.nav && !window.EOS_IS_EXPORT) EOS.nav('boards');
var boardConfig = null, boardItems = [], currentView = 'table', currentBoardId = '', sortCol = '', sortDesc = false;

function isReadonly() { return !!(window._embedForceReadonly || (boardConfig && boardConfig.readonly)); }
function toggleReadonly() {
    if (!boardConfig) return;
    boardConfig.readonly = !boardConfig.readonly;
    applyReadonlyChrome();
    switchView(currentView);
    // Persist on the board config so the choice survives reload.
    fetch('/boards/api/boards/' + encodeURIComponent(currentBoardId), {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({readonly: !!boardConfig.readonly}),
    }).catch(function(){});
}
function applyReadonlyChrome() {
    document.body.classList.toggle('board-readonly', isReadonly());
    var banner = document.getElementById('board-readonly-banner');
    if (banner) {
        // Source-app failure beats readonly — without the source app the
        // "View only" message is misleading (there's nothing to view).
        if (boardSourceStatus && boardSourceStatus.ok === false) {
            var app = boardSourceStatus.app || 'source app';
            banner.style.display = '';
            banner.classList.add('board-source-missing');
            banner.innerHTML = '⚠ Source app <code>' + app + '</code> is not available. ' +
                'Items cannot be displayed. Install or load the app, then reload this board.';
        } else {
            banner.classList.remove('board-source-missing');
            banner.style.display = isReadonly() ? '' : 'none';
            banner.innerHTML = 'View only — <a href="#" onclick="toggleReadonly();return false;">enable editing</a>';
        }
    }
    var toggle = document.getElementById('readonly-toggle-btn');
    if (toggle) toggle.innerHTML = isReadonly() ? '🔒 View' : '✏ Edit';
}
var boardAllList = []; // list of {id, name} used by the left nav
var searchQuery = '';
var personFilter = '';   // '' = no filter; otherwise a person id (narrows to rows where they appear in any person column)
var HIDDEN_COLUMNS = {};   // {col_id: true} — hidden from table+kanban display; still editable in forms
var SELECTED_FILES = {};   // {filename: true} — row selection for bulk edit
var _bulkEditCol = null;   // col object currently chosen in bulk modal
var groupByField = '';
var collapsedGroups = {};
var currentDetailFile = null;
var PILL_COLORS = {blue:'eos-pill-blue',amber:'eos-pill-amber',green:'eos-pill-green',emerald:'eos-pill-emerald',red:'eos-pill-red',purple:'eos-pill-purple',orange:'eos-pill-orange',gray:'eos-pill-gray'};
var CHART_COLORS = ['#7c6af7','#50c878','#ffbf00','#dc5050','#a855f7','#fb923c','#6495ed','#34d399'];

// ── Navigation ──
function goHome() { window.location.hash = ''; }

async function init() {
    var hash = window.location.hash.slice(1);
    // Embed mode: another app iframes /boards/?id=X&embed=1 to compose a
    // board view inline. Hides nav/topbar/detail/EOS-FAB so only the rendered
    // view is visible. ?embed=1&chrome=0 also hides the view-tabs row.
    // ?embed=1&readonly=1 forces read-only at boot regardless of board config.
    var qp = new URLSearchParams(window.location.search);
    var embed = qp.get('embed') === '1';
    if (embed) {
        document.body.classList.add('embed-mode');
        if (qp.get('chrome') === '0') document.body.classList.add('embed-no-chrome');
        if (qp.get('readonly') === '1') window._embedForceReadonly = true;
    }
    // ?id=X is the embed/link entry point (used by sibling apps' "⊞ Board view"
    // links); falls back to hash navigation for in-app routing.
    var paramId = qp.get('id');

    // Legacy single-board export (from GET /boards/api/export/{id}) — EOS_EXPORT_DATA.board is singular.
    if (window.EOS_IS_EXPORT && window.EOS_EXPORT_DATA && window.EOS_EXPORT_DATA.board) {
        boardConfig = window.EOS_EXPORT_DATA.board;
        boardItems = window.EOS_EXPORT_DATA.items;
        currentBoardId = boardConfig.id;
        showBoard();
        return;
    }
    // Full workspace export (new architecture) or online mode — use normal routing;
    // the shim serves /api/* from the snapshot transparently.
    var target = hash || paramId;
    if (target) { await loadBoard(target); }
    else if (embed) {
        // Embed mode without ?id is meaningless — the host has no UI to pick a
        // board. Render a tiny hint instead of the full home view.
        document.body.innerHTML = '<div style="padding:24px;color:var(--text-muted);font:13px system-ui">Specify <code>?id=&lt;board-id&gt;</code> to embed a board.</div>';
    }
    else { await loadHome(); }
}

async function loadHome() {
    document.getElementById('home-view').style.display = '';
    document.getElementById('board-view').style.display = 'none';
    closeItemDetail();
    try {
        var res = await fetch('/boards/api/boards');
        var data = await res.json();
        boardAllList = data.boards || [];
        renderHome(boardAllList, data.presets || []);
        renderSideNav();
    } catch(e) { console.error(e); }
}

// ── Left nav (A1) ─────────────────────────────────────
function renderSideNav() {
    var el = document.getElementById('boards-nav-list');
    if (!el) return;
    el.innerHTML = boardAllList.map(function(b) {
        var active = b.id === currentBoardId ? ' active' : '';
        return '<a class="boards-nav-item'+active+'" href="#'+esc(b.id)+'" onclick="navGo(\''+esc(b.id)+'\');return false;" title="'+esc(b.description||'')+'">' +
            '<span class="boards-nav-icon">📋</span>' +
            '<span class="boards-nav-label">'+esc(b.name)+'</span>' +
            '</a>';
    }).join('');
}
function navGo(id) { window.location.hash = id; }
function toggleNav() {
    var nav = document.getElementById('boards-nav');
    nav.classList.toggle('collapsed');
    try { localStorage.setItem('boards.nav.collapsed', nav.classList.contains('collapsed') ? '1' : '0'); } catch (_) {}
}
function navCycle(delta) {
    if (!boardAllList.length) return;
    var idx = boardAllList.findIndex(function(b){return b.id===currentBoardId;});
    if (idx < 0) idx = 0;
    idx = (idx + delta + boardAllList.length) % boardAllList.length;
    navGo(boardAllList[idx].id);
}

function renderHome(boards, presets) {
    var g = document.getElementById('boards-grid');
    g.innerHTML = boards.map(function(b) {
        return '<div class="board-card" onclick="window.location.hash=\''+b.id+'\'">' +
            '<div class="board-card-title">'+esc(b.name)+'</div>' +
            '<div class="board-card-desc">'+esc(b.description||'')+'</div>' +
            '<div class="board-card-meta"><span>'+b.column_count+' cols</span><span>'+b.view_count+' views</span><span>🏷 '+esc(b.source_tag)+'</span></div></div>';
    }).join('') + '<div class="board-card board-card-new" onclick="openModal(\'create-modal\')">+ New Board</div>';

    var p = document.getElementById('presets-grid');
    p.innerHTML = presets.map(function(pr) {
        return '<div class="board-card" onclick="createFromPreset(\''+pr.id+'\')">' +
            '<div class="board-card-title">'+esc(pr.name)+'</div>' +
            '<div class="board-card-desc">'+esc(pr.description)+'</div>' +
            '<div class="board-card-meta"><span>'+pr.columns+' cols</span><span>'+pr.views+' views</span></div></div>';
    }).join('');
}

async function loadBoard(id) {
    currentBoardId = id;
    // Ensure sidebar knows about all boards even if we landed on a deep link.
    if (!boardAllList.length) {
        try {
            var r = await fetch('/boards/api/boards');
            var d = await r.json();
            boardAllList = d.boards || [];
        } catch (_) {}
    }
    try {
        var [cfgR, itmR, srcR] = await Promise.all([
            fetch('/boards/api/boards/'+id),
            fetch('/boards/api/boards/'+id+'/items'),
            fetch('/boards/api/boards/'+id+'/source-status')
        ]);
        boardConfig = await cfgR.json();
        boardItems = await itmR.json();
        boardSourceStatus = await srcR.json().catch(function(){return {ok:true};});
        if (boardConfig.error) { goHome(); return; }
        showBoard();
    } catch(e) { console.error(e); goHome(); }
}

// Set by loadBoard(); read by applyReadonlyChrome to render the missing-app
// banner. Default-ok so vault_tag boards never trip the banner path.
var boardSourceStatus = {ok: true};

function showBoard() {
    document.getElementById('home-view').style.display = 'none';
    var bv = document.getElementById('board-view');
    bv.style.display = 'flex';
    document.getElementById('board-title').textContent = boardConfig.name || '';
    document.getElementById('board-desc').textContent = boardConfig.description || '';
    applyReadonlyChrome();
    renderSideNav();
    renderViewTabs();
    ensureColumnTypesLoaded().then(renderGroupBySelect);
    renderGroupBySelect();
    // Reset per-board UI state
    searchQuery = '';
    var searchInput = document.getElementById('board-search');
    if (searchInput) searchInput.value = '';
    // Pre-load roster if this board has any person-family columns (cheap fetch; ignored on failure).
    var needsPeople = (boardConfig.columns || []).some(function(c){
        return isPersonSingle(c.type) || isPersonMulti(c.type);
    });
    if (needsPeople) ensurePeopleLoaded();
    // Clear any filter carried over from a previous board + render the pill slot.
    personFilter = '';
    ACTIVE_FILTERS = [];
    HIDDEN_COLUMNS = {};
    SELECTED_FILES = {};
    renderFilterPill();
    renderActiveFilterChips();
    renderHiddenColumnsChip();
    renderBulkChip();
    var views = boardConfig.views || [{type:'table'}];
    var def = views.find(function(v){return v.default}) || views[0];
    switchView(def.type);
    // Load saved views list for the dropdown (non-blocking).
    loadSavedViews();
}

// ── Saved views ──
var SAVED_VIEWS = [];

async function loadSavedViews() {
    var sel = document.getElementById('view-select');
    if (!sel) return;
    SAVED_VIEWS = [];
    try {
        var res = await fetch('/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/views');
        var data = await res.json();
        SAVED_VIEWS = (data && data.views) || [];
    } catch(e) { SAVED_VIEWS = []; }
    renderViewDropdown();
}

function renderViewDropdown() {
    var sel = document.getElementById('view-select');
    if (!sel) return;
    var opts = ['<option value="">Views…</option>'];
    SAVED_VIEWS.forEach(function(v) {
        opts.push('<option value="'+esc(v.id)+'">'+esc(v.name)+'</option>');
    });
    if (SAVED_VIEWS.length) {
        opts.push('<option disabled>──────</option>');
        opts.push('<option value="__delete__">🗑 Delete current view…</option>');
    }
    sel.innerHTML = opts.join('');
}

function onViewSelectChange() {
    var sel = document.getElementById('view-select');
    var vid = sel.value;
    sel.value = '';
    if (!vid) return;
    if (vid === '__delete__') {
        deleteSavedViewPrompt();
        return;
    }
    var v = SAVED_VIEWS.find(function(x){return x.id===vid;});
    if (v) applyView(v);
}

function applyView(v) {
    // Restore persisted state.
    if (v.view_type) currentView = v.view_type;
    if ('search' in v) {
        searchQuery = v.search || '';
        var si = document.getElementById('board-search');
        if (si) si.value = searchQuery;
    }
    if ('person_filter' in v) personFilter = v.person_filter || '';
    if ('filters' in v) {
        ACTIVE_FILTERS = Array.isArray(v.filters) ? v.filters.slice() : [];
        renderActiveFilterChips();
    }
    if ('hidden_columns' in v) {
        HIDDEN_COLUMNS = {};
        (v.hidden_columns || []).forEach(function(cid){ HIDDEN_COLUMNS[cid] = true; });
        renderHiddenColumnsChip();
    }
    if ('sort_col'  in v) sortCol  = v.sort_col || '';
    if ('sort_desc' in v) sortDesc = !!v.sort_desc;
    if ('group_by'  in v) {
        groupByField = v.group_by || '';
        var gsel = document.getElementById('board-groupby');
        if (gsel) gsel.value = groupByField;
    }
    if ('kanban_group_by' in v && v.kanban_group_by) {
        boardConfig.kanban_group_by = v.kanban_group_by;
    }
    renderFilterPill();
    switchView(currentView);
}

function openSaveViewModal() {
    document.getElementById('save-view-name').value = '';
    document.getElementById('save-view-desc').value = '';
    openModal('save-view-modal');
    setTimeout(function(){ document.getElementById('save-view-name').focus(); }, 50);
}

async function submitSaveView() {
    var name = document.getElementById('save-view-name').value.trim();
    if (!name) { EOS_UI.toast('Name required', false); return; }
    var body = {
        name: name,
        description: document.getElementById('save-view-desc').value.trim(),
        view_type: currentView,
        search: searchQuery || '',
        person_filter: personFilter || '',
        filters: ACTIVE_FILTERS,
        hidden_columns: Object.keys(HIDDEN_COLUMNS),
        sort_col: sortCol || '',
        sort_desc: !!sortDesc,
        group_by: groupByField || '',
        kanban_group_by: boardConfig.kanban_group_by || '',
    };
    try {
        await fetch('/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/views', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify(body),
        });
        closeModal('save-view-modal');
        await loadSavedViews();
        EOS_UI.toast('View saved', true);
    } catch(e) { console.error(e); EOS_UI.toast('Save failed', false); }
}

function _ensureManageViewsModal() {
    var el = document.getElementById('manage-views-modal');
    if (el) return el;
    el = document.createElement('div');
    el.className = 'modal-overlay';
    el.id = 'manage-views-modal';
    el.innerHTML = '<div class="modal-box">' +
        '<h2>Manage saved views</h2>' +
        '<div id="manage-views-list" class="manage-views-list"></div>' +
        '<div class="form-actions"><button class="btn" onclick="closeModal(\'manage-views-modal\')">Close</button></div>' +
        '</div>';
    document.body.appendChild(el);
    return el;
}

function renderManageViewsList() {
    var host = document.getElementById('manage-views-list');
    if (!host) return;
    if (!SAVED_VIEWS.length) {
        host.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem">No saved views.</div>';
        return;
    }
    host.innerHTML = SAVED_VIEWS.map(function(v) {
        return '<div class="manage-view-row">' +
            '<span class="manage-view-name">'+esc(v.name)+'</span>' +
            '<button class="btn btn-sm btn-danger" onclick="confirmDeleteSavedView(\''+esc(v.id)+'\')">Delete</button>' +
            '</div>';
    }).join('');
}

async function deleteSavedViewPrompt() {
    if (!SAVED_VIEWS.length) return;
    _ensureManageViewsModal();
    renderManageViewsList();
    openModal('manage-views-modal');
}

async function confirmDeleteSavedView(viewId) {
    var v = SAVED_VIEWS.find(function(x){return x.id === viewId;});
    if (!v) return;
    if (!await EOS_UI.confirm({message:'Delete saved view "'+v.name+'"?', action:'Delete', danger:true})) return;
    try {
        await fetch('/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/views/'+encodeURIComponent(v.id), {method:'DELETE'});
        await loadSavedViews();
        // If the manage-views modal is still open, re-render the list in place
        // (list-render function, not a re-open of the whole flow).
        if (document.getElementById('manage-views-modal').classList.contains('open')) {
            renderManageViewsList();
        }
    } catch(e) { console.error(e); EOS_UI.toast('Delete failed', false); }
}

// ── Column editor ──
// Mirrors emptyos/sdk/column_types.py registration. Each entry: id, label, hint.
var COLUMN_TYPE_CATALOG = [
    {id:'text',         label:'Text'},
    {id:'number',       label:'Number'},
    {id:'date',         label:'Date'},
    {id:'checkbox',     label:'Checkbox'},
    {id:'select',       label:'Single select'},
    {id:'multi-select', label:'Multi select'},
    {id:'person',       label:'Person'},
    {id:'multi-person', label:'Multi person'},
    {id:'designer',     label:'Person — Designer'},
    {id:'checker',      label:'Person — Checker'},
    {id:'approver',     label:'Person — Approver'},
    {id:'reviewer',     label:'Person — Reviewer'},
    {id:'skills',       label:'Skills (tags)'},
    {id:'dependencies', label:'Dependencies (legacy)'},
    {id:'link',         label:'URL link'},
    {id:'link-record',  label:'Link record (cross-board)'},
    {id:'formula',      label:'Formula'},
    {id:'timeline',     label:'Timeline'},
];

var _columnModalMode = 'add';           // 'add' | 'edit'
var _columnModalOriginalId = '';
var _colMenuTargetId = '';

function openColumnModal(mode, existingCol) {
    _columnModalMode = mode;
    _columnModalOriginalId = existingCol ? existingCol.id : '';
    var typeSel = document.getElementById('col-modal-type');
    typeSel.innerHTML = COLUMN_TYPE_CATALOG.map(function(t){
        return '<option value="'+t.id+'">'+esc(t.label)+'</option>';
    }).join('');

    var idIn = document.getElementById('col-modal-id');
    var labelIn = document.getElementById('col-modal-label');
    var errHost = document.getElementById('col-modal-error');
    errHost.textContent = '';
    document.getElementById('column-modal-title').textContent = mode === 'edit' ? 'Edit column' : 'Add column';
    document.getElementById('col-modal-delete-btn').style.display = mode === 'edit' ? '' : 'none';

    if (mode === 'edit' && existingCol) {
        idIn.value = existingCol.id;
        idIn.readOnly = true;
        labelIn.value = existingCol.label || '';
        typeSel.value = existingCol.type || 'text';
    } else {
        idIn.value = '';
        idIn.readOnly = false;
        labelIn.value = '';
        typeSel.value = 'text';
    }

    onColumnTypeChange(existingCol);
    openModal('column-modal');
    setTimeout(function(){ (mode === 'edit' ? labelIn : idIn).focus(); }, 50);
}

function onColumnTypeChange(existingCol) {
    var type = document.getElementById('col-modal-type').value;
    var host = document.getElementById('col-modal-extras');
    var hint = document.getElementById('col-modal-hint');
    host.innerHTML = '';
    hint.textContent = '';
    var existing = existingCol || {};

    function group(label, inner) {
        return '<div class="form-group"><label>'+esc(label)+'</label>'+inner+'</div>';
    }

    if (type === 'number' || type === 'formula') {
        host.innerHTML += group('Prefix (optional)',
            '<input class="form-input" id="col-extra-prefix" value="'+esc(existing.prefix||'')+'" placeholder="$">') +
            group('Suffix (optional)',
            '<input class="form-input" id="col-extra-suffix" value="'+esc(existing.suffix||'')+'" placeholder="%">');
    }
    if (type === 'select' || type === 'multi-select') {
        hint.textContent = 'One option per line. Add a color with "Option = blue" (blue/green/amber/red/purple/orange/gray/emerald).';
        var existingLines = (existing.options || []).map(function(o) {
            var c = (existing.color_map || {})[o];
            return c ? (o + ' = ' + c) : o;
        }).join('\n');
        host.innerHTML += group('Options',
            '<textarea class="form-input" id="col-extra-options" rows="6" placeholder="To Do\nIn Progress = blue\nDone = green">'+esc(existingLines)+'</textarea>');
    }
    if (type === 'person' || type === 'multi-person' || type === 'designer' ||
        type === 'checker' || type === 'approver' || type === 'reviewer') {
        host.innerHTML += group('Weight hours (per assignment, for workload calc)',
            '<input class="form-input" type="number" id="col-extra-weight" value="'+esc(existing.weight_hours||5)+'">');
    }
    if (type === 'link-record') {
        var boardOpts = (boardAllList || [])
            .filter(function(b){return b.id !== currentBoardId;})
            .map(function(b){
                var sel = (existing.target_board === b.id) ? ' selected' : '';
                return '<option value="'+esc(b.id)+'"'+sel+'>'+esc(b.name)+' ('+b.id+')</option>';
            }).join('');
        host.innerHTML += group('Target board',
            '<select class="form-input" id="col-extra-target" onchange="onLinkTargetChange()"><option value="">— pick one —</option>'+boardOpts+'</select>') +
            group('Multi-select?',
            '<label style="display:flex;gap:0.5rem;align-items:center"><input type="checkbox" id="col-extra-multi"'+(existing.multi?' checked':'')+'> Allow linking multiple items</label>') +
            group('Inverse column on target (optional)',
            '<select class="form-input" id="col-extra-inverse"><option value="">— none —</option></select>');
        // Populate inverse dropdown if a target is already set.
        if (existing.target_board) setTimeout(function(){ onLinkTargetChange(existing.inverse||''); }, 10);
    }
    if (type === 'formula') {
        hint.innerHTML = 'Use bare field names and functions. Link-walk via <code>linkcol.field</code>. Functions: SUM, AVG, COUNT, MIN, MAX, IF, IS_EMPTY, TODAY, CONCAT, LEN, ROUND, LOOKUP.';
        host.innerHTML += group('Expression',
            '<textarea class="form-input" id="col-extra-expr" rows="4" placeholder="SUM(tasks.est_hours)">'+esc(existing.expression||existing.expr||'')+'</textarea>');
    }

    // Group-by override — explicit per-column. Shown for every type.
    var meta = COLUMN_TYPE_META[type];
    var defaultGroupable = meta ? meta.groupable : false;
    var current = (typeof existing.groupable === 'boolean') ? existing.groupable : defaultGroupable;
    host.innerHTML += group('Group-by',
        '<label style="display:flex;gap:0.5rem;align-items:center">' +
        '<input type="checkbox" id="col-extra-groupable"' + (current?' checked':'') + '>' +
        ' Available in the "Group by…" dropdown (default for ' + type + ': ' + (defaultGroupable?'on':'off') + ')</label>');
}

async function onLinkTargetChange(preferInverseId) {
    var target = document.getElementById('col-extra-target').value;
    var invSel = document.getElementById('col-extra-inverse');
    if (!invSel) return;
    invSel.innerHTML = '<option value="">— none —</option>';
    if (!target) return;
    try {
        var res = await fetch('/boards/api/boards/'+encodeURIComponent(target));
        var cfg = await res.json();
        var links = (cfg.columns || []).filter(function(c){return c.type === 'link-record';});
        invSel.innerHTML += links.map(function(c){
            var sel = (c.id === preferInverseId) ? ' selected' : '';
            return '<option value="'+esc(c.id)+'"'+sel+'>'+esc(c.label||c.id)+' ('+c.id+')</option>';
        }).join('');
    } catch(e) { /* silent */ }
}

function _buildColumnFromModal() {
    var type = document.getElementById('col-modal-type').value;
    var col = {
        id: document.getElementById('col-modal-id').value.trim().toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,''),
        label: document.getElementById('col-modal-label').value.trim(),
        type: type,
    };
    if (!col.id) throw new Error('ID is required');
    if (!col.label) col.label = col.id.replace(/-/g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});

    var p = document.getElementById('col-extra-prefix');  if (p && p.value) col.prefix = p.value;
    var s = document.getElementById('col-extra-suffix');  if (s && s.value) col.suffix = s.value;

    if (type === 'select' || type === 'multi-select') {
        var raw = (document.getElementById('col-extra-options').value || '').split('\n');
        var options = [];
        var color_map = {};
        raw.forEach(function(line){
            line = line.trim();
            if (!line) return;
            var m = line.split('=');
            var opt = (m[0]||'').trim();
            var color = (m[1]||'').trim().toLowerCase();
            if (!opt) return;
            options.push(opt);
            if (color) color_map[opt] = color;
        });
        col.options = options;
        if (Object.keys(color_map).length) col.color_map = color_map;
    }
    if (['person','multi-person','designer','checker','approver','reviewer'].indexOf(type) >= 0) {
        var w = document.getElementById('col-extra-weight');
        if (w && w.value) col.weight_hours = parseFloat(w.value);
    }
    if (type === 'link-record') {
        col.target_board = document.getElementById('col-extra-target').value;
        col.multi = document.getElementById('col-extra-multi').checked;
        var inv = document.getElementById('col-extra-inverse').value;
        if (inv) col.inverse = inv;
    }
    if (type === 'formula') {
        col.expression = document.getElementById('col-extra-expr').value.trim();
    }

    // Group-by override: only persist when it differs from the type default,
    // so unchanged columns don't accumulate noise in the saved config.
    var gb = document.getElementById('col-extra-groupable');
    if (gb) {
        var meta = COLUMN_TYPE_META[type];
        var defaultGroupable = meta ? meta.groupable : false;
        if (gb.checked !== defaultGroupable) col.groupable = gb.checked;
    }
    return col;
}

async function saveColumnFromModal() {
    var errHost = document.getElementById('col-modal-error');
    errHost.textContent = '';
    var col;
    try { col = _buildColumnFromModal(); }
    catch(e) { errHost.textContent = e.message; return; }

    var url, method;
    if (_columnModalMode === 'edit') {
        url = '/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/columns/'+encodeURIComponent(_columnModalOriginalId);
        method = 'PATCH';
    } else {
        url = '/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/columns';
        method = 'POST';
    }
    try {
        var res = await fetch(url, {method: method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(col)});
        var data = await res.json();
        if (data.error) { errHost.textContent = data.error; return; }
        closeModal('column-modal');
        await loadBoard(currentBoardId);
    } catch(e) { errHost.textContent = 'Save failed: ' + e.message; }
}

async function deleteColumnFromModal() {
    if (_columnModalMode !== 'edit') return;
    var ok = await EOS_UI.confirm({
        message: 'Delete column "'+_columnModalOriginalId+'"? Item frontmatter is preserved — re-adding the column with the same id will restore values.',
        action: 'Delete column',
        danger: true,
    });
    if (!ok) return;
    try {
        await fetch('/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/columns/'+encodeURIComponent(_columnModalOriginalId), {method:'DELETE'});
        closeModal('column-modal');
        await loadBoard(currentBoardId);
    } catch(e) { EOS_UI.toast('Delete failed: ' + e.message, false); }
}

// ── Column header ⋮ menu ──
function openColMenu(event, colId) {
    _colMenuTargetId = colId;
    // Tag the active trigger so the filter popover can anchor to the same button.
    document.querySelectorAll('.col-menu-btn[data-active]').forEach(function(b){ b.removeAttribute('data-active'); });
    event.currentTarget.setAttribute('data-active', '1');
    var menu = document.getElementById('col-menu');
    menu.style.display = 'block';
    var rect = event.currentTarget.getBoundingClientRect();
    menu.style.left = (rect.left) + 'px';
    menu.style.top  = (rect.bottom + 2) + 'px';
    setTimeout(function(){ document.addEventListener('click', _closeColMenuOnce, {once:true}); }, 0);
}
function _closeColMenuOnce() {
    var menu = document.getElementById('col-menu');
    if (menu) menu.style.display = 'none';
}
function colMenuAction(action) {
    _closeColMenuOnce();
    var cid = _colMenuTargetId;
    var col = (boardConfig.columns || []).find(function(c){return c.id === cid;});
    if (!col) return;
    if (action === 'filter') { openFilterPopover(col); return; }
    if (action === 'hide')   { hideColumn(col.id); return; }
    if (action === 'edit') { openColumnModal('edit', col); return; }
    if (action === 'delete') {
        EOS_UI.confirm({
            message: 'Delete column "'+(col.label||col.id)+'"? Item data for this field will be orphaned but preserved in the vault.',
            action: 'Delete column',
            danger: true,
        }).then(function(ok) {
            if (!ok) return;
            return fetch('/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/columns/'+encodeURIComponent(col.id), {method:'DELETE'})
                .then(function(){ return loadBoard(currentBoardId); });
        }).catch(function(e){ EOS_UI.toast('Delete failed: '+e.message, false); });
    }
}

// ── Column hide / reveal (Phase G2) ──
function hideColumn(colId) {
    HIDDEN_COLUMNS[colId] = true;
    renderHiddenColumnsChip();
    switchView(currentView);
}
function showColumn(colId) {
    delete HIDDEN_COLUMNS[colId];
    renderHiddenColumnsChip();
    switchView(currentView);
}
function showAllColumns() {
    HIDDEN_COLUMNS = {};
    renderHiddenColumnsChip();
    switchView(currentView);
}

function renderHiddenColumnsChip() {
    // Render a "N hidden" chip alongside filter chips. Click opens a popover with checkboxes.
    var host = document.getElementById('filters-chip-host');
    if (!host) return;
    // Find existing hidden chip if any — replace; otherwise append.
    var existing = host.querySelector('.hidden-cols-chip');
    if (existing) existing.remove();
    var ids = Object.keys(HIDDEN_COLUMNS);
    if (!ids.length) return;
    var chip = document.createElement('button');
    chip.className = 'filter-pill hidden-cols-chip';
    chip.title = 'Show hidden columns';
    chip.innerHTML = '👁 ' + ids.length + ' hidden';
    chip.onclick = function(ev) { ev.stopPropagation(); openHiddenColumnsPopover(ev); };
    host.appendChild(chip);
}

function openHiddenColumnsPopover(ev) {
    var pop = document.getElementById('hidden-cols-popover');
    if (!pop) {
        pop = document.createElement('div');
        pop.id = 'hidden-cols-popover';
        pop.className = 'filter-popover';
        document.body.appendChild(pop);
    }
    var cols = boardConfig.columns || [];
    pop.innerHTML =
        '<div class="filter-popover-title">Hidden columns</div>' +
        cols.map(function(c) {
            var hidden = !!HIDDEN_COLUMNS[c.id];
            return '<label class="hidden-col-row"><input type="checkbox"' +
                (hidden ? '' : ' checked') +
                ' onchange="this.checked ? showColumn(\''+esc(c.id)+'\') : hideColumn(\''+esc(c.id)+'\')">' +
                ' ' + esc(c.label || c.id) + '</label>';
        }).join('') +
        '<div class="filter-popover-actions" style="margin-top:0.6rem">' +
        '<button class="btn btn-sm" onclick="showAllColumns(); closeHiddenColsPopover()">Show all</button>' +
        '<button class="btn btn-sm" onclick="closeHiddenColsPopover()">Done</button></div>';
    pop.style.display = 'block';
    var rect = ev.currentTarget.getBoundingClientRect();
    pop.style.left = (rect.left) + 'px';
    pop.style.top  = (rect.bottom + 6) + 'px';
    setTimeout(function(){
        document.addEventListener('click', _maybeCloseHiddenColsPopover, true);
    }, 0);
}
function _maybeCloseHiddenColsPopover(e) {
    var pop = document.getElementById('hidden-cols-popover');
    if (pop && !pop.contains(e.target)) closeHiddenColsPopover();
}
function closeHiddenColsPopover() {
    var pop = document.getElementById('hidden-cols-popover');
    if (pop) pop.style.display = 'none';
    document.removeEventListener('click', _maybeCloseHiddenColsPopover, true);
}

// ── Column reorder (Phase G1) ──
var _colDragId = '';
function onColDragStart(e, colId) {
    _colDragId = colId;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', colId);   // required by Firefox
    e.currentTarget.classList.add('col-dragging');
}
function onColDragOver(e) {
    if (!_colDragId) return;
    e.preventDefault();
    e.currentTarget.classList.add('col-drop-target');
    e.dataTransfer.dropEffect = 'move';
}
function onColDragLeave(e) {
    e.currentTarget.classList.remove('col-drop-target');
}
function onColDrop(e, targetColId) {
    e.preventDefault();
    e.currentTarget.classList.remove('col-drop-target');
    document.querySelectorAll('.col-dragging').forEach(function(el){ el.classList.remove('col-dragging'); });
    if (!_colDragId || _colDragId === targetColId) { _colDragId = ''; return; }

    var cols = boardConfig.columns.slice();
    var fromIdx = cols.findIndex(function(c){return c.id === _colDragId;});
    var toIdx   = cols.findIndex(function(c){return c.id === targetColId;});
    if (fromIdx < 0 || toIdx < 0) { _colDragId = ''; return; }
    var moving = cols.splice(fromIdx, 1)[0];
    cols.splice(toIdx, 0, moving);
    _colDragId = '';

    // Optimistic local update + server persist.
    boardConfig.columns = cols;
    switchView(currentView);
    fetch('/boards/api/boards/'+encodeURIComponent(currentBoardId), {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({columns: cols}),
    }).catch(function(err){ console.error('Column reorder persist failed:', err); });
}

// ── Row selection + bulk edit (Phase H) ──
function toggleRowSelection(file, checked) {
    if (checked) SELECTED_FILES[file] = true;
    else delete SELECTED_FILES[file];
    renderBulkChip();
    // Reflect on the row without a full re-render.
    var tr = document.querySelector('tr[data-file="'+cssEsc(file)+'"]');
    if (tr) tr.classList.toggle('row-selected', !!checked);
}
function cssEsc(s) { return String(s).replace(/"/g, '\\"'); }

function selectAllVisible() {
    visibleItems().forEach(function(it){ if (it.file) SELECTED_FILES[it.file] = true; });
    renderBulkChip();
    renderTable();
}
function onSelectAllToggle(checked) {
    if (checked) selectAllVisible();
    else clearSelection();
}
function clearSelection() {
    SELECTED_FILES = {};
    renderBulkChip();
    renderTable();
}

function renderBulkChip() {
    var host = document.getElementById('bulk-chip-host');
    if (!host) return;
    var n = Object.keys(SELECTED_FILES).length;
    if (!n) { host.innerHTML = ''; return; }
    // Segmented button: primary action ("Edit N rows") + clear (×).
    // Solid-filled to signal "this is a button, not a filter chip".
    host.innerHTML =
        '<span class="bulk-action">' +
            '<button class="bulk-action-main" onclick="openBulkEditModal()" title="Open bulk-edit dialog">' +
                '✎ Edit ' + n + ' row' + (n === 1 ? '' : 's') + '…' +
            '</button>' +
            '<button class="bulk-action-clear" onclick="clearSelection()" title="Clear selection">×</button>' +
        '</span>';
}

function openBulkEditModal() {
    if (!Object.keys(SELECTED_FILES).length) return;
    var sel = document.getElementById('bulk-edit-col');
    // Editable columns — same set the backend accepts, minus formulas (computed)
    // and dependencies/link-record (need special handling not built yet).
    var cols = (boardConfig.columns || []).filter(function(c) {
        return ['text','number','select','date','checkbox','person','designer',
                'checker','approver','reviewer','multi-person','link'].indexOf(c.type) >= 0;
    });
    sel.innerHTML = cols.map(function(c){
        return '<option value="'+esc(c.id)+'">'+esc(c.label || c.id)+' ('+c.type+')</option>';
    }).join('');
    document.getElementById('bulk-edit-title').textContent =
        'Bulk edit · ' + Object.keys(SELECTED_FILES).length + ' rows';
    document.getElementById('bulk-edit-error').textContent = '';
    document.getElementById('bulk-edit-progress').style.display = 'none';
    document.getElementById('bulk-edit-apply').disabled = false;
    onBulkColChange();
    openModal('bulk-edit-modal');
}

function onBulkColChange() {
    var colId = document.getElementById('bulk-edit-col').value;
    var col = (boardConfig.columns || []).find(function(c){return c.id===colId;});
    _bulkEditCol = col;
    var host = document.getElementById('bulk-edit-value-host');
    if (!col) { host.innerHTML = ''; return; }
    var label = '<label>New value</label>';

    if (col.type === 'date') {
        // Offer a choice: set absolute date OR shift by N days.
        host.innerHTML = label +
            '<div style="display:flex;gap:0.4rem;align-items:center;margin-bottom:0.4rem">' +
              '<select class="form-input" id="bulk-date-mode" onchange="onBulkDateModeChange()">' +
                '<option value="set">Set to</option>' +
                '<option value="shift">Shift by N days</option>' +
              '</select>' +
            '</div>' +
            '<div id="bulk-date-value-wrap">' +
              '<input type="date" class="form-input" id="bulk-value-input">' +
            '</div>';
        return;
    }
    if (col.type === 'select' && (col.options || []).length) {
        host.innerHTML = label +
            '<select class="form-input" id="bulk-value-input">' +
              col.options.map(function(o){return '<option value="'+esc(o)+'">'+esc(o)+'</option>';}).join('') +
            '</select>';
        return;
    }
    if (col.type === 'checkbox') {
        host.innerHTML = label +
            '<select class="form-input" id="bulk-value-input">' +
              '<option value="true">Checked (true)</option>' +
              '<option value="false">Unchecked (false)</option>' +
            '</select>';
        return;
    }
    if (col.type === 'person' || col.type === 'designer' || col.type === 'checker' ||
        col.type === 'approver' || col.type === 'reviewer') {
        var people = Object.values(window.PEOPLE_BY_ID || {});
        host.innerHTML = label +
            '<select class="form-input" id="bulk-value-input">' +
              '<option value="">— clear —</option>' +
              people.map(function(p){return '<option value="'+esc(p.id)+'">'+esc(p.name)+'</option>';}).join('') +
            '</select>';
        return;
    }
    if (col.type === 'number') {
        host.innerHTML = label + '<input type="number" class="form-input" id="bulk-value-input">';
        return;
    }
    host.innerHTML = label + '<input type="text" class="form-input" id="bulk-value-input">';
}

function onBulkDateModeChange() {
    var mode = document.getElementById('bulk-date-mode').value;
    var wrap = document.getElementById('bulk-date-value-wrap');
    if (mode === 'shift') {
        wrap.innerHTML = '<input type="number" class="form-input" id="bulk-value-input" placeholder="7" value="7" title="positive = forward, negative = backward">';
    } else {
        wrap.innerHTML = '<input type="date" class="form-input" id="bulk-value-input">';
    }
}

async function applyBulkEdit() {
    var col = _bulkEditCol;
    if (!col) return;
    var files = Object.keys(SELECTED_FILES);
    if (!files.length) return;

    var mode = 'set';
    if (col.type === 'date') {
        var m = document.getElementById('bulk-date-mode');
        if (m) mode = m.value;
    }
    var raw = document.getElementById('bulk-value-input').value;

    var errHost = document.getElementById('bulk-edit-error');
    errHost.textContent = '';
    var prog = document.getElementById('bulk-edit-progress');
    prog.style.display = '';
    document.getElementById('bulk-edit-apply').disabled = true;

    // Resolve value per-item when mode==='shift'; else use the same value.
    function resolveValue(item) {
        if (col.type === 'date' && mode === 'shift') {
            var cur = String(item[col.id] || '').slice(0, 10);
            if (!cur) return null;
            var d = new Date(cur);
            if (isNaN(d.getTime())) return null;
            d.setDate(d.getDate() + parseInt(raw || 0, 10));
            return d.getFullYear() + '-' +
                   String(d.getMonth()+1).padStart(2,'0') + '-' +
                   String(d.getDate()).padStart(2,'0');
        }
        if (col.type === 'checkbox') return raw === 'true';
        if (col.type === 'number')   return raw === '' ? '' : parseFloat(raw);
        return raw;
    }

    var ok = 0, failed = [];
    for (var i = 0; i < files.length; i++) {
        var file = files[i];
        var item = boardItems.find(function(x){return x.file === file;}) || {};
        var v = resolveValue(item);
        if (v === null) { failed.push(file + ' (no current date)'); continue; }
        prog.textContent = 'Updating ' + (i+1) + '/' + files.length + '…';
        try {
            var res = await fetch('/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/items/'+encodeURIComponent(file), {
                method: 'PATCH',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({updates: {[col.id]: v}}),
            });
            var data = await res.json();
            if (data.ok === false || data.error) {
                failed.push(file + ': ' + (data.error || 'unknown error'));
            } else {
                ok += 1;
            }
        } catch(e) {
            failed.push(file + ': ' + e.message);
        }
    }

    prog.textContent = ok + ' updated' + (failed.length ? ', ' + failed.length + ' failed' : '');
    if (failed.length) {
        errHost.innerHTML = 'Failed on ' + failed.length + ' row(s):<br><small>' +
            failed.slice(0, 6).map(esc).join('<br>') +
            (failed.length > 6 ? '<br>…+' + (failed.length - 6) + ' more' : '') + '</small>';
        document.getElementById('bulk-edit-apply').disabled = false;
    } else {
        closeModal('bulk-edit-modal');
        clearSelection();
        await loadBoard(currentBoardId);
    }
}

// ── Filter popover + chips ──
var _filterPopoverCol = null;

function openFilterPopover(col) {
    _filterPopoverCol = col;
    var pop = document.getElementById('filter-popover');
    document.getElementById('filter-popover-title').textContent = 'Filter by ' + (col.label || col.id);
    var ops = _opsForType(col.type);
    var opSel = document.getElementById('filter-op-select');
    opSel.innerHTML = ops.map(function(o){ return '<option value="'+o.op+'">'+esc(o.label)+'</option>'; }).join('');
    opSel.value = ops[0].op;
    _renderFilterValueInput(col, ops[0]);
    pop.style.display = 'block';
    // Position under the last-clicked ⋮ button.
    var anchor = document.querySelector('#table-head .col-menu-btn[data-active]');
    if (anchor) {
        var rect = anchor.getBoundingClientRect();
        pop.style.left = (rect.left) + 'px';
        pop.style.top  = (rect.bottom + 4) + 'px';
    } else {
        // Fallback — center under the topbar.
        pop.style.left = '50%';
        pop.style.top  = '90px';
        pop.style.transform = 'translateX(-50%)';
    }
    setTimeout(function(){
        document.addEventListener('click', _maybeCloseFilterPopover, true);
    }, 0);
}
function _maybeCloseFilterPopover(e) {
    var pop = document.getElementById('filter-popover');
    if (pop && !pop.contains(e.target)) closeFilterPopover();
}
function closeFilterPopover() {
    document.getElementById('filter-popover').style.display = 'none';
    document.removeEventListener('click', _maybeCloseFilterPopover, true);
    _filterPopoverCol = null;
}
function onFilterOpChange() {
    if (!_filterPopoverCol) return;
    var op = document.getElementById('filter-op-select').value;
    var ops = _opsForType(_filterPopoverCol.type);
    var spec = ops.find(function(o){return o.op === op;});
    _renderFilterValueInput(_filterPopoverCol, spec);
}

function _renderFilterValueInput(col, opSpec) {
    var host = document.getElementById('filter-value-host');
    if (!opSpec || !opSpec.takesValue) { host.innerHTML = ''; return; }
    var kind = opSpec.valueKind;

    if (kind === 'select' && (col.options || []).length) {
        host.innerHTML = '<select class="form-input" id="filter-value-input">' +
            col.options.map(function(o){return '<option value="'+esc(o)+'">'+esc(o)+'</option>';}).join('') +
            '</select>';
        return;
    }
    if (kind === 'person') {
        // Uses PEOPLE_BY_ID cached by ensurePeopleLoaded on board open.
        var people = Object.values(window.PEOPLE_BY_ID || {});
        host.innerHTML = '<select class="form-input" id="filter-value-input">' +
            people.map(function(p){return '<option value="'+esc(p.id)+'">'+esc(p.name)+'</option>';}).join('') +
            '</select>';
        return;
    }
    if (kind === 'record') {
        // Lazy-load the target board's items if we haven't already.
        var target = col.target_board || '';
        host.innerHTML = '<select class="form-input" id="filter-value-input"><option>Loading…</option></select>';
        ensureLinkItemsLoaded(target).then(function(){
            var items = LINK_ITEMS[target] || [];
            var nameColId = LINK_BOARD_NAME_COLS[target] || 'name';
            var sel = document.getElementById('filter-value-input');
            if (!sel) return;
            sel.innerHTML = items.map(function(it){
                var id = it.file || it.id;
                return '<option value="'+esc(id)+'">'+esc(it[nameColId] || id)+'</option>';
            }).join('');
        });
        return;
    }
    if (kind === 'date') {
        host.innerHTML = '<input type="date" class="form-input" id="filter-value-input">';
        return;
    }
    if (kind === 'int') {
        host.innerHTML = '<input type="number" class="form-input" id="filter-value-input" value="7" min="1">';
        return;
    }
    if (kind === 'number') {
        host.innerHTML = '<input type="number" class="form-input" id="filter-value-input">';
        return;
    }
    host.innerHTML = '<input type="text" class="form-input" id="filter-value-input" placeholder="value">';
}

function applyFilterFromPopover() {
    if (!_filterPopoverCol) return;
    var op = document.getElementById('filter-op-select').value;
    var ops = _opsForType(_filterPopoverCol.type);
    var spec = ops.find(function(o){return o.op === op;});
    var value = '';
    if (spec && spec.takesValue) {
        var inp = document.getElementById('filter-value-input');
        if (!inp) return;
        value = inp.value;
        if (value === '' && spec.valueKind !== 'text') { EOS_UI.toast('Pick a value', false); return; }
    }
    ACTIVE_FILTERS.push({col_id: _filterPopoverCol.id, op: op, value: value});
    closeFilterPopover();
    renderActiveFilterChips();
    switchView(currentView);
}

function renderActiveFilterChips() {
    var host = document.getElementById('filters-chip-host');
    if (!host) return;
    if (!ACTIVE_FILTERS.length) { host.innerHTML = ''; return; }
    host.innerHTML = ACTIVE_FILTERS.map(function(f, idx) {
        var col = (boardConfig.columns || []).find(function(c){return c.id === f.col_id;});
        var colLabel = col ? (col.label || col.id) : f.col_id;
        var ops = col ? _opsForType(col.type) : [];
        var opLabel = (ops.find(function(o){return o.op===f.op;})||{}).label || f.op;
        var valueLabel = '';
        if (f.value !== '' && f.value !== undefined && f.value !== null) {
            // Resolve person/record IDs to titles for readability.
            if (col && (isPersonSingle(col.type) || isPersonMulti(col.type))) {
                var p = (window.PEOPLE_BY_ID || {})[f.value];
                valueLabel = p ? p.name : f.value;
            } else if (col && col.type === 'link-record') {
                var map = LINK_TITLES[col.target_board] || {};
                valueLabel = map[f.value] || f.value;
            } else {
                valueLabel = String(f.value);
            }
        }
        return '<button class="filter-pill filter-pill-col" title="Remove" onclick="removeFilter('+idx+')">' +
            esc(colLabel) + ' ' + esc(opLabel) + (valueLabel ? ' ' + esc(valueLabel) : '') +
            ' <span style="margin-left:4px;opacity:0.8">×</span></button>';
    }).join('');
}
function removeFilter(idx) {
    ACTIVE_FILTERS.splice(idx, 1);
    renderActiveFilterChips();
    switchView(currentView);
}
function clearAllFilters() {
    ACTIVE_FILTERS = [];
    renderActiveFilterChips();
    switchView(currentView);
}

// ── View Tabs ── (delegates to EOS_UI.viewSwitcher; switchView is the controller)
function renderViewTabs() {
    var views = boardConfig.views || [{type:'table'}];
    EOS_UI.viewSwitcher({
        mountId: 'view-tabs',
        views: views,
        active: currentView,
        onChange: function(v) { switchView(v); },
    });
}

function switchView(view) {
    currentView = view;
    document.querySelectorAll('.view-panel').forEach(function(el){el.classList.remove('active');});
    document.querySelectorAll('.eos-view-tab').forEach(function(el){el.classList.remove('active');});
    var panel = document.getElementById('view-'+view);
    if (panel) panel.classList.add('active');
    var tab = document.querySelector('.eos-view-tab[data-view="'+view+'"]');
    if (tab) tab.classList.add('active');
    if (view === 'table') renderTable();
    else if (view === 'kanban') {
        // Person-axis kanban needs the roster loaded to render columns.
        ensurePeopleLoaded().then(renderKanban);
        renderKanban();
    }
    else if (view === 'calendar') renderCalendar();
    else if (view === 'timeline') renderTimeline();
    else if (view === 'chart') renderChart();
    else if (view === 'gallery') renderGallery();
}

// ── Gallery View ── (cover-grid card view; configurable image / title / subtitle / badge / meta fields)
function renderGallery() {
    var viewCfg = (boardConfig.views||[]).find(function(v){return v.type==='gallery';}) || {};
    var imageField = viewCfg.image_field || 'cover';
    var imageFallback = viewCfg.image_fallback_field || 'cover_url';
    var cols = (boardConfig.columns||[]);
    var titleField = viewCfg.title_field || (cols[0] && cols[0].id) || 'name';
    var subtitleField = viewCfg.subtitle_field || '';
    var badgeField = viewCfg.badge_field || '';
    var metaFields = viewCfg.meta_fields || [];
    var badgeCol = badgeField ? cols.find(function(c){return c.id===badgeField;}) : null;

    var items = sortItems(visibleItems());
    var container = document.getElementById('gallery-container');
    if (!items.length) {
        container.innerHTML = EOS_UI.emptyState({icon:'▦', message: searchQuery?'No items match this search.':'No items yet. Click "+ Add Item" to create one.'});
        return;
    }

    container.innerHTML = items.map(function(item) {
        var fileId = item.file || item.id || '';
        var img = item[imageField] || (imageFallback ? item[imageFallback] : '') || '';
        var title = item[titleField] || fileId || '(untitled)';
        var subtitle = subtitleField ? (item[subtitleField] || '') : '';
        var badgeVal = badgeField ? (item[badgeField] || '') : '';
        var badge = '';
        if (badgeVal) {
            badge = '<div class="board-gallery-badge">' +
                    EOS_UI.pillBadge(badgeVal, (badgeCol && badgeCol.color_map) || {}) +
                '</div>';
        }
        var meta = '';
        if (metaFields.length) {
            var bits = metaFields.map(function(f) {
                var v = item[f]; if (!v && v !== 0) return '';
                return '<span class="board-gallery-meta-item">'+esc(String(v))+'</span>';
            }).filter(Boolean).join('');
            if (bits) meta = '<div class="board-gallery-meta">'+bits+'</div>';
        }
        var cover = img
            ? '<img class="board-gallery-cover" src="'+esc(img)+'" alt="" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';">'
              + '<div class="board-gallery-cover-fallback" style="display:none">'+esc((title||'?').charAt(0).toUpperCase())+'</div>'
            : '<div class="board-gallery-cover-fallback">'+esc((title||'?').charAt(0).toUpperCase())+'</div>';
        return '<div class="board-gallery-card" onclick="openItemDetail(\''+esc(fileId)+'\')">' +
            '<div class="board-gallery-cover-wrap">' + cover + badge + '</div>' +
            '<div class="board-gallery-body">' +
                '<div class="board-gallery-title">'+esc(String(title))+'</div>' +
                (subtitle ? '<div class="board-gallery-subtitle">'+esc(String(subtitle))+'</div>' : '') +
                meta +
            '</div></div>';
    }).join('');
}

// ── Filtering + search (A5) ──
// ── Column-level filters (Phase F) ───────────────────────────────────
// Each filter: {col_id, op, value}. Multiple filters on one column compose as OR;
// filters across columns compose as AND. Keeps the composition intuitive: "give
// me IFR OR IFA items, due before Friday" works as {status=IFR OR status=IFA} AND {due<Friday}.
var ACTIVE_FILTERS = [];

// Type-aware operator catalog. Each entry: {op, label, takesValue, valueKind}.
// valueKind hints what input widget to render ('text', 'number', 'date',
// 'select', 'person', 'record', 'int' for day-count).
function _opsForType(colType) {
    var t = colType || 'text';
    if (t === 'number') return [
        {op:'eq',      label:'=',  takesValue:true, valueKind:'number'},
        {op:'neq',     label:'≠',  takesValue:true, valueKind:'number'},
        {op:'gt',      label:'>',  takesValue:true, valueKind:'number'},
        {op:'gte',     label:'≥',  takesValue:true, valueKind:'number'},
        {op:'lt',      label:'<',  takesValue:true, valueKind:'number'},
        {op:'lte',     label:'≤',  takesValue:true, valueKind:'number'},
        {op:'empty',   label:'is empty', takesValue:false},
        {op:'not_empty', label:'is not empty', takesValue:false},
    ];
    if (t === 'date') return [
        {op:'on',       label:'is',           takesValue:true, valueKind:'date'},
        {op:'before',   label:'is before',    takesValue:true, valueKind:'date'},
        {op:'after',    label:'is after',     takesValue:true, valueKind:'date'},
        {op:'within',   label:'within next N days', takesValue:true, valueKind:'int'},
        {op:'overdue',  label:'is overdue (past today)', takesValue:false},
        {op:'empty',    label:'is empty', takesValue:false},
    ];
    if (t === 'select' || t === 'multi-select') return [
        {op:'is',       label:'is',     takesValue:true, valueKind:'select'},
        {op:'not',      label:'is not', takesValue:true, valueKind:'select'},
        {op:'empty',    label:'is empty', takesValue:false},
        {op:'not_empty', label:'is not empty', takesValue:false},
    ];
    if (t === 'checkbox') return [
        {op:'is_true',  label:'is checked', takesValue:false},
        {op:'is_false', label:'is not checked', takesValue:false},
    ];
    if (isPersonSingle(t) || isPersonMulti(t)) return [
        {op:'is',       label:'is',     takesValue:true, valueKind:'person'},
        {op:'not',      label:'is not', takesValue:true, valueKind:'person'},
        {op:'empty',    label:'is empty', takesValue:false},
        {op:'not_empty', label:'is not empty', takesValue:false},
    ];
    if (t === 'link-record') return [
        {op:'includes',     label:'includes',     takesValue:true, valueKind:'record'},
        {op:'not_includes', label:'does not include', takesValue:true, valueKind:'record'},
        {op:'empty',        label:'is empty', takesValue:false},
        {op:'not_empty',    label:'is not empty', takesValue:false},
    ];
    if (t === 'skills' || t === 'dependencies') return [
        {op:'includes',     label:'includes',     takesValue:true, valueKind:'text'},
        {op:'not_includes', label:'does not include', takesValue:true, valueKind:'text'},
        {op:'empty',        label:'is empty', takesValue:false},
        {op:'not_empty',    label:'is not empty', takesValue:false},
    ];
    // text, link, formula, timeline, anything else — treat as text.
    return [
        {op:'contains',     label:'contains',       takesValue:true, valueKind:'text'},
        {op:'not_contains', label:'does not contain', takesValue:true, valueKind:'text'},
        {op:'equals',       label:'equals',         takesValue:true, valueKind:'text'},
        {op:'not_equals',   label:'does not equal', takesValue:true, valueKind:'text'},
        {op:'empty',        label:'is empty', takesValue:false},
        {op:'not_empty',    label:'is not empty', takesValue:false},
    ];
}

function _todayIso() {
    var d = new Date();
    return d.getFullYear() + '-' +
        String(d.getMonth()+1).padStart(2,'0') + '-' +
        String(d.getDate()).padStart(2,'0');
}
function _addDaysIso(n) {
    var d = new Date();
    d.setDate(d.getDate() + n);
    return d.getFullYear() + '-' +
        String(d.getMonth()+1).padStart(2,'0') + '-' +
        String(d.getDate()).padStart(2,'0');
}

function _isEmptyVal(v) {
    if (v === null || v === undefined || v === '') return true;
    if (Array.isArray(v) && v.length === 0) return true;
    return false;
}

function matchesFilter(item, col, f) {
    var v = item[col.id];
    switch (f.op) {
        case 'empty':          return _isEmptyVal(v);
        case 'not_empty':      return !_isEmptyVal(v);
        // Text ops
        case 'contains':       return String(v||'').toLowerCase().indexOf(String(f.value||'').toLowerCase()) !== -1;
        case 'not_contains':   return String(v||'').toLowerCase().indexOf(String(f.value||'').toLowerCase()) === -1;
        case 'equals':         return String(v||'') === String(f.value||'');
        case 'not_equals':     return String(v||'') !== String(f.value||'');
        // Number ops
        case 'eq':   return parseFloat(v) === parseFloat(f.value);
        case 'neq':  return parseFloat(v) !== parseFloat(f.value);
        case 'gt':   return parseFloat(v) >  parseFloat(f.value);
        case 'gte':  return parseFloat(v) >= parseFloat(f.value);
        case 'lt':   return parseFloat(v) <  parseFloat(f.value);
        case 'lte':  return parseFloat(v) <= parseFloat(f.value);
        // Date ops (ISO string compare — works because YYYY-MM-DD sorts correctly)
        case 'on':       return String(v||'').slice(0,10) === String(f.value||'').slice(0,10);
        case 'before':   return v && String(v).slice(0,10) < String(f.value||'').slice(0,10);
        case 'after':    return v && String(v).slice(0,10) > String(f.value||'').slice(0,10);
        case 'within':   return v && String(v).slice(0,10) >= _todayIso() && String(v).slice(0,10) <= _addDaysIso(parseInt(f.value||0,10));
        case 'overdue':  return v && String(v).slice(0,10) < _todayIso();
        // Select / person ops — membership
        case 'is':
            if (Array.isArray(v)) return v.indexOf(f.value) !== -1;
            return String(v||'') === String(f.value||'');
        case 'not':
            if (Array.isArray(v)) return v.indexOf(f.value) === -1;
            return String(v||'') !== String(f.value||'');
        // List inclusion ops (link-record, skills, dependencies)
        case 'includes':
            if (Array.isArray(v)) return v.indexOf(f.value) !== -1;
            return String(v||'').toLowerCase().indexOf(String(f.value||'').toLowerCase()) !== -1;
        case 'not_includes':
            if (Array.isArray(v)) return v.indexOf(f.value) === -1;
            return String(v||'').toLowerCase().indexOf(String(f.value||'').toLowerCase()) === -1;
        // Checkbox
        case 'is_true':  return v === true || v === 'true';
        case 'is_false': return !(v === true || v === 'true');
    }
    return true;
}

function visibleItems() {
    var items = boardItems;
    // 1. Person pill filter (legacy — tries every person-family column).
    if (personFilter) {
        items = items.filter(function(item) {
            return (boardConfig.columns || []).some(function(col) {
                if (!isPersonSingle(col.type) && !isPersonMulti(col.type)) return false;
                var v = item[col.id];
                if (isPersonMulti(col.type)) {
                    return Array.isArray(v) ? v.indexOf(personFilter) !== -1 : v === personFilter;
                }
                return v === personFilter;
            });
        });
    }
    // 2. Column filters — grouped by col_id, within-column OR, across-column AND.
    if (ACTIVE_FILTERS.length) {
        var byCol = {};
        ACTIVE_FILTERS.forEach(function(f){
            (byCol[f.col_id] = byCol[f.col_id] || []).push(f);
        });
        items = items.filter(function(item) {
            for (var cid in byCol) {
                var col = (boardConfig.columns || []).find(function(c){return c.id===cid;});
                if (!col) continue;
                var any = byCol[cid].some(function(f){ return matchesFilter(item, col, f); });
                if (!any) return false;
            }
            return true;
        });
    }
    // 3. Free-text search — any field substring match.
    var q = (searchQuery || '').trim().toLowerCase();
    if (!q) return items;
    return items.filter(function(item) {
        for (var k in item) {
            if (!Object.prototype.hasOwnProperty.call(item, k)) continue;
            var v = item[k];
            if (v && String(v).toLowerCase().indexOf(q) !== -1) return true;
        }
        return false;
    });
}
function onSearchInput() {
    searchQuery = (document.getElementById('board-search').value || '');
    switchView(currentView);
}
// ── Grouping (A3) ──
// Filled once by ensureColumnTypesLoaded(). Maps type id → registry metadata.
var COLUMN_TYPE_META = {};
async function ensureColumnTypesLoaded() {
    if (Object.keys(COLUMN_TYPE_META).length) return;
    try {
        var res = await fetch('/boards/api/column-types');
        var data = await res.json();
        (data.types || []).forEach(function(t){ COLUMN_TYPE_META[t.id] = t; });
    } catch(e) { /* silent — renderGroupBySelect falls back to type allowlist */ }
}

function _isGroupable(col) {
    // Per-column override wins.
    if (typeof col.groupable === 'boolean') return col.groupable;
    var meta = COLUMN_TYPE_META[col.type];
    if (meta) return !!meta.groupable;
    // Fallback allowlist if type metadata hasn't loaded yet.
    return ['select','text','checkbox','person','multi-person','designer',
            'checker','approver','reviewer','link-record','date','formula',
            'multi-select','skills'].indexOf(col.type) >= 0;
}

function renderGroupBySelect() {
    var sel = document.getElementById('board-groupby');
    if (!sel) return;
    var cols = (boardConfig.columns || []).filter(_isGroupable);
    sel.innerHTML = '<option value="">No groups</option>' + cols.map(function(c) {
        return '<option value="'+esc(c.id)+'">Group by '+esc(c.label)+'</option>';
    }).join('');
    sel.value = groupByField;
}
function onGroupByChange() {
    groupByField = document.getElementById('board-groupby').value || '';
    collapsedGroups = {};
    if (currentView === 'table') renderTable();
}
function toggleGroup(name) {
    collapsedGroups[name] = !collapsedGroups[name];
    renderTable();
}

// ── Table View ──
function visibleCols() {
    return (boardConfig.columns || []).filter(function(c){ return !HIDDEN_COLUMNS[c.id]; });
}

function renderTable() {
    var cols = visibleCols();
    var thead = document.getElementById('table-head');
    var selTotal = visibleItems().length;
    var selN = visibleItems().filter(function(it){return SELECTED_FILES[it.file];}).length;
    var allChecked = selTotal > 0 && selN === selTotal;
    // File header gets the select-all checkbox inline, so the body rows
    // (one combined File cell with checkbox + expand + filename) still align.
    thead.innerHTML = '<th class="th-file"><input type="checkbox" class="th-select-all" title="Select all visible"' +
        (allChecked ? ' checked' : '') +
        ' onclick="onSelectAllToggle(this.checked)"> File</th>' + cols.map(function(c) {
        var sorted = sortCol === c.id;
        return '<th class="'+(sorted?'sorted':'')+'" draggable="true" data-col-id="'+esc(c.id)+'"' +
            ' ondragstart="onColDragStart(event,\''+esc(c.id)+'\')"' +
            ' ondragover="onColDragOver(event)"' +
            ' ondragleave="onColDragLeave(event)"' +
            ' ondrop="onColDrop(event,\''+esc(c.id)+'\')">' +
            '<span class="th-label" onclick="sortTable(\''+esc(c.id)+'\')">' +
                esc(c.label) +
                '<span class="sort-icon">'+(sorted?(sortDesc?'▼':'▲'):'⇅')+'</span>' +
            '</span>' +
            '<button class="col-menu-btn" title="Column options" onclick="event.stopPropagation(); openColMenu(event, \''+esc(c.id)+'\')">⋮</button>' +
        '</th>';
    }).join('');

    var items = sortItems(visibleItems());
    var tbody = document.getElementById('table-body');
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="'+(cols.length+1)+'">'+EOS_UI.emptyState({icon:'📋', message: searchQuery?'No items match this search.':'No items yet. Click "+ Add Item" to create one.'})+'</td></tr>';
        return;
    }

    function renderRow(item) {
        var nameCol = cols[0];
        var primaryId = nameCol ? nameCol.id : 'name';
        var checked = SELECTED_FILES[item.file] ? ' checked' : '';
        var fileCell = '<td class="board-table-file">' +
            '<span class="file-cell-inner">' +
                '<input type="checkbox" class="row-select" data-file="'+esc(item.file)+'"' + checked +
                ' onclick="event.stopPropagation(); toggleRowSelection(\''+esc(item.file)+'\', this.checked)" title="Select row">' +
                '<button class="board-table-expand" onclick="openItemDetail(\''+esc(item.file)+'\')" title="Open detail">⋯</button>' +
                '<span class="file-cell-name">'+esc(item.file||'')+'</span>' +
            '</span></td>';
        var rowCls = 'board-table-row' + (SELECTED_FILES[item.file] ? ' row-selected' : '');
        return '<tr data-file="'+esc(item.file)+'" class="'+rowCls+'">' + fileCell +
            cols.map(function(c) { return '<td>'+renderCell(item, c)+'</td>'; }).join('') + '</tr>';
    }

    if (!groupByField) {
        tbody.innerHTML = items.map(renderRow).join('');
        return;
    }

    // Grouped rendering
    var groupCol = cols.find(function(c){return c.id===groupByField;});
    var groups = {};
    items.forEach(function(item) {
        var key = item[groupByField];
        if (key === undefined || key === null || key === '') key = '(empty)';
        key = String(key);
        if (!groups[key]) groups[key] = [];
        groups[key].push(item);
    });
    var numeric = cols.filter(function(c){return c.type==='number';});
    tbody.innerHTML = Object.keys(groups).map(function(g) {
        var gItems = groups[g];
        var collapsed = !!collapsedGroups[g];
        var color = (groupCol && groupCol.color_map && groupCol.color_map[g]) || 'gray';
        var aggs = numeric.map(function(c) {
            var sum = gItems.reduce(function(a,i){var n=parseFloat(i[c.id]);return a + (isNaN(n)?0:n);}, 0);
            if (!sum) return '';
            return '<span class="group-agg">Σ '+esc(c.label)+': '+sum.toFixed(1).replace(/\.0$/,'')+'</span>';
        }).filter(Boolean).join('');
        var header = '<tr class="group-header" onclick="toggleGroup('+JSON.stringify(g)+')">' +
            '<td colspan="'+(cols.length+1)+'">' +
            '<span class="group-chevron">'+(collapsed?'▸':'▾')+'</span>' +
            '<span class="eos-pill '+(PILL_COLORS[color]||'eos-pill-gray')+'">'+esc(g)+'</span>' +
            '<span class="group-count">'+gItems.length+'</span>' +
            aggs + '</td></tr>';
        var body = collapsed ? '' : gItems.map(renderRow).join('');
        return header + body;
    }).join('');
}

// Person-type families — all renderable with a capacity-dot chip.
var PERSON_SINGLE_TYPES = ['person','designer','checker','approver','reviewer'];
var PERSON_MULTI_TYPES  = ['multi-person'];
function isPersonSingle(t) { return PERSON_SINGLE_TYPES.indexOf(t) !== -1; }
function isPersonMulti(t)  { return PERSON_MULTI_TYPES.indexOf(t) !== -1; }

// People lookup cache — fetched once per board load. Key = person id.
var PEOPLE_BY_ID = {};
async function ensurePeopleLoaded() {
    if (Object.keys(PEOPLE_BY_ID).length) return;
    try {
        var list = await fetch('/people/api/people').then(function(r){return r.json();});
        if (Array.isArray(list)) list.forEach(function(p){ PEOPLE_BY_ID[p.id] = p; });
    } catch (e) { /* people app may not be loaded; render ids as-is */ }
}

function personChip(personId, role) {
    if (!personId) return '<span style="color:var(--text-muted);font-size:0.75rem">—</span>';
    var p = PEOPLE_BY_ID[personId];
    var displayName = p ? (p.name || p.id) : personId;
    var initial = displayName.charAt(0).toUpperCase();
    var band = (p && p.band) || 'ok';
    var pctStr = p ? (' · ' + Math.round((p.load_ratio||0)*100) + '%') : '';
    var title = displayName + pctStr + (role ? ' · ' + role : '') + ' · Click to filter · Shift+click to edit';
    var cls = p ? 'person-chip' : 'person-chip person-chip-unknown';
    return '<span class="'+cls+'" onclick="handlePersonChipClick(event, \''+esc(personId)+'\', this)" title="'+esc(title)+'">' +
        '<span class="person-avatar">'+esc(initial)+'</span>' +
        '<span class="person-name">'+esc(displayName)+'</span>' +
        '<span class="person-dot person-dot-'+esc(band)+'"></span>' +
        '</span>';
}

function handlePersonChipClick(e, personId, chipEl) {
    e.stopPropagation();
    if (e.shiftKey) {
        // Escape hatch: Shift+click falls through to the inline picker (old behaviour).
        var cell = chipEl.closest('.cell-editable');
        if (cell && cell.onclick) cell.onclick(e);
        return;
    }
    filterByPerson(personId);
}

function filterByPerson(personId) {
    personFilter = (personFilter === personId) ? '' : (personId || '');
    renderFilterPill();
    switchView(currentView);
}

function renderFilterPill() {
    var host = document.getElementById('filter-pill-host');
    if (!host) return;
    if (!personFilter) { host.innerHTML = ''; return; }
    var p = PEOPLE_BY_ID[personFilter];
    var name = p ? p.name : personFilter;
    host.innerHTML = '<button class="filter-pill" onclick="filterByPerson(\'\')" title="Clear filter (Esc)">👤 '
        + esc(name) + ' <span style="margin-left:4px;opacity:0.75">×</span></button>';
}

// Column-type dispatch tables. Each entry is (item, col, val) -> HTML string.
// Phase A: registry-shaped lookup; behaviour identical to prior inline switch.
// Add new column types by registering additional keys — no core edits needed.
var COLUMN_RENDERERS = {
    'person': function(item, col, val) {
        var chip = personChip(val, col.type);
        return '<span class="cell-editable" onclick="editPersonCell(this,\''+esc(item.file||item.id)+'\',\''+esc(col.id)+'\',\''+esc(col.type)+'\',false)">'+chip+'</span>';
    },
    'multi-person': function(item, col, val) {
        var arr = Array.isArray(val) ? val : (val ? [val] : []);
        var chips = arr.slice(0,3).map(function(pid){return personChip(pid, col.type);}).join('');
        var extra = arr.length > 3 ? '<span class="person-overflow">+'+(arr.length-3)+'</span>' : '';
        var empty = !arr.length ? '<span style="color:var(--text-muted);font-size:0.75rem">+ assign</span>' : '';
        return '<span class="cell-editable person-stack" onclick="editPersonCell(this,\''+esc(item.file||item.id)+'\',\''+esc(col.id)+'\',\''+esc(col.type)+'\',true)">'+chips+extra+empty+'</span>';
    },
    'select': function(item, col, val) {
        if (col.color_map) {
            var color = col.color_map[val] || 'gray';
            return '<span class="eos-pill '+(PILL_COLORS[color]||'eos-pill-gray')+' cell-editable" onclick="editSelectCell(this,\''+item.file+'\',\''+col.id+'\','+JSON.stringify(col.options||[])+')">'+esc(String(val))+'</span>';
        }
        return '<span class="cell-editable" onclick="editSelectCell(this,\''+item.file+'\',\''+col.id+'\','+JSON.stringify(col.options||[])+')">'+esc(String(val))+'</span>';
    },
    'number': function(item, col, val) {
        var display = (col.prefix||'') + val + (col.suffix||'');
        return '<span class="cell-editable" onclick="editTextCell(this,\''+item.file+'\',\''+col.id+'\')">'+esc(display)+'</span>';
    },
    'checkbox': function(item, col, val) {
        return '<input type="checkbox" '+(val==='true'||val===true?'checked':'')+' onchange="updateField(\''+item.file+'\',\''+col.id+'\',this.checked)">';
    },
    'link': function(item, col, val) {
        if (val) return '<a href="'+esc(String(val))+'" target="_blank" style="color:var(--accent)">🔗 Link</a>';
        return COLUMN_RENDERERS.text(item, col, val);
    },
    'text': function(item, col, val) {
        return '<span class="cell-editable" onclick="editTextCell(this,\''+item.file+'\',\''+col.id+'\')">'+esc(String(val))+'</span>';
    },
    'link-record': function(item, col, val) {
        var ids = Array.isArray(val) ? val : (val ? [val] : []);
        var targetBoard = col.target_board || '';
        var chips = ids.slice(0,3).map(function(id){
            var title = (LINK_TITLES[targetBoard] || {})[id] || id;
            return '<span class="link-record-chip" title="'+esc(id)+'" onclick="event.stopPropagation(); openLinkedItem(\''+esc(targetBoard)+'\',\''+esc(id)+'\')">'+esc(title)+'</span>';
        }).join('');
        var extra = ids.length > 3 ? '<span class="person-overflow">+'+(ids.length-3)+'</span>' : '';
        var empty = !ids.length ? '<span style="color:var(--text-muted);font-size:0.75rem">+ link</span>' : '';
        return '<span class="cell-editable link-record-stack" onclick="editLinkRecordCell(this,\''+esc(item.file||item.id)+'\',\''+esc(col.id)+'\')">'+chips+extra+empty+'</span>';
    }
};

// Cache of target-board items keyed by id → title, populated lazily when a
// link-record cell or picker is opened. Shape: {boardId: {itemId: title}}.
var LINK_TITLES = {};
var LINK_ITEMS  = {};   // {boardId: [full item objects]} for the picker
// Role-variant person columns share the 'person' renderer.
COLUMN_RENDERERS['designer'] = COLUMN_RENDERERS.person;
COLUMN_RENDERERS['checker']  = COLUMN_RENDERERS.person;
COLUMN_RENDERERS['approver'] = COLUMN_RENDERERS.person;
COLUMN_RENDERERS['reviewer'] = COLUMN_RENDERERS.person;

function renderCell(item, col) {
    var val = item[col.id];
    if (val === undefined || val === null) val = '';
    var renderer = COLUMN_RENDERERS[col.type] || COLUMN_RENDERERS.text;
    return renderer(item, col, val);
}

// Person picker: replaces the cell with a dropdown of active people.
async function editPersonCell(el, file, field, colType, multi) {
    if (isReadonly()) return;
    if (el.querySelector('select') || el.querySelector('.person-picker')) return;
    await ensurePeopleLoaded();
    var options = Object.values(PEOPLE_BY_ID);
    if (!options.length) { EOS_UI.toast('No people in roster yet. Add one via /people/.', false); return; }

    // Current value
    var item = boardItems.find(function(i){return (i.file||i.id) === file;});
    var current = item ? item[field] : '';
    if (multi && !Array.isArray(current)) current = current ? [current] : [];

    var html = '<select class="eos-cell-edit-input person-picker">';
    if (!multi) html += '<option value="">(none)</option>';
    options.forEach(function(p) {
        var selected = multi ? (current.indexOf(p.id) !== -1) : (p.id === current);
        var loadPct = Math.round((p.load_ratio||0)*100);
        html += '<option value="'+esc(p.id)+'"'+(selected?' selected':'')+'>'+esc(p.name)+' ('+loadPct+'%)</option>';
    });
    html += '</select>';
    el.innerHTML = html;
    var sel = el.querySelector('select');
    if (multi) sel.multiple = true;
    sel.focus();
    sel.onchange = function() {
        var v = multi ? Array.from(sel.selectedOptions).map(function(o){return o.value;}) : sel.value;
        updateField(file, field, v);
    };
    sel.onblur = function() { setTimeout(function(){ switchView(currentView); }, 100); };
}

// ── Link-record picker ──
var LINK_BOARD_NAME_COLS = {};   // {boardId: nameColId}

async function ensureLinkItemsLoaded(boardId) {
    if (LINK_ITEMS[boardId]) return;
    try {
        var cfgRes = await fetch('/boards/api/boards/'+encodeURIComponent(boardId));
        var cfg = await cfgRes.json();
        LINK_BOARD_NAME_COLS[boardId] = (cfg.columns || [{}])[0].id || 'name';
        var itemsRes = await fetch('/boards/api/boards/'+encodeURIComponent(boardId)+'/items');
        var items = await itemsRes.json();
        if (Array.isArray(items)) {
            LINK_ITEMS[boardId] = items;
            var nameCol = LINK_BOARD_NAME_COLS[boardId];
            var map = {};
            items.forEach(function(it){
                var id = it.file || it.id;
                if (id) map[id] = it[nameCol] || id;
            });
            LINK_TITLES[boardId] = map;
        }
    } catch(e) { console.error('ensureLinkItemsLoaded', boardId, e); }
}

async function editLinkRecordCell(el, file, field) {
    if (isReadonly()) return;
    if (el.querySelector('select')) return;
    var col = (boardConfig.columns||[]).find(function(c){return c.id===field;});
    if (!col || !col.target_board) return;

    await ensureLinkItemsLoaded(col.target_board);
    var options = LINK_ITEMS[col.target_board] || [];
    if (!options.length) { EOS_UI.toast('Target board "' + col.target_board + '" has no items yet.', false); return; }

    var item = boardItems.find(function(i){return (i.file||i.id) === file;});
    var current = (item && item[field]) || [];
    if (!Array.isArray(current)) current = current ? [current] : [];
    var currentSet = {}; current.forEach(function(id){currentSet[id]=true;});

    var multi = col.multi !== false;
    var html = '<select class="eos-cell-edit-input link-picker"' + (multi ? ' multiple' : '') + '>';
    if (!multi) html += '<option value="">(none)</option>';
    options.forEach(function(o) {
        var id = o.file || o.id;
        var nameColId = LINK_BOARD_NAME_COLS[col.target_board] || 'name';
        var title = o[nameColId] || id;
        var selected = currentSet[id] ? ' selected' : '';
        html += '<option value="'+esc(id)+'"'+selected+'>'+esc(title)+'</option>';
    });
    html += '</select>';
    el.innerHTML = html;
    var sel = el.querySelector('select');
    if (multi) sel.size = Math.min(8, options.length + 1);
    sel.focus();
    sel.onchange = function() {
        var v = multi
            ? Array.from(sel.selectedOptions).map(function(o){return o.value;})
            : (sel.value ? [sel.value] : []);
        updateField(file, field, v);
    };
    sel.onblur = function() { setTimeout(function(){ switchView(currentView); }, 150); };
}

function openLinkedItem(boardId, itemId) {
    window.location.hash = boardId + '/' + encodeURIComponent(itemId);
    if (boardId !== currentBoardId) {
        loadBoard(boardId).then(function(){
            if (typeof openItemDetail === 'function') openItemDetail(itemId);
        });
    } else {
        if (typeof openItemDetail === 'function') openItemDetail(itemId);
    }
}

function sortTable(colId) {
    if (sortCol === colId) sortDesc = !sortDesc;
    else { sortCol = colId; sortDesc = false; }
    renderTable();
}

function sortItems(items) {
    if (!sortCol) return items;
    var sorted = items.slice();
    sorted.sort(function(a,b) {
        var av = a[sortCol]||'', bv = b[sortCol]||'';
        if (typeof av === 'number' && typeof bv === 'number') return sortDesc ? bv-av : av-bv;
        return sortDesc ? String(bv).localeCompare(String(av)) : String(av).localeCompare(String(bv));
    });
    return sorted;
}

// ── Inline Editing ── (delegates to EOS_UI.inlineCellEdit)
function editTextCell(el, file, field) {
    if (isReadonly()) return;
    EOS_UI.inlineCellEdit({
        el: el, type: 'text',
        onSave: function(v) { updateField(file, field, v); },
    });
}

function editSelectCell(el, file, field, options) {
    if (isReadonly()) return;
    EOS_UI.inlineCellEdit({
        el: el, type: 'select', options: options,
        onSave: function(v) { updateField(file, field, v); },
    });
}

async function updateField(file, field, value) {
    if (window.EOS_IS_EXPORT) return;
    if (isReadonly()) return;
    try {
        await fetch('/boards/api/boards/'+currentBoardId+'/items/'+encodeURIComponent(file), {
            method: 'PATCH', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({updates:{[field]:value}})
        });
        // Update local data
        var item = boardItems.find(function(i){return i.file===file;});
        if (item) item[field] = value;
        switchView(currentView);
    } catch(e) { console.error(e); }
}

// ── Kanban View ──
function _kanbanAxisOptions() {
    // Same rule as group-by dropdown — every groupable column plus a handful
    // of rendering-safe types.
    return (boardConfig.columns || []).filter(_isGroupable).filter(function(c) {
        return ['select','checkbox','person','designer','checker','approver','reviewer']
            .indexOf(c.type) >= 0;
    });
}

function renderKanbanAxisSelect() {
    var sel = document.getElementById('kanban-axis-select');
    if (!sel) return;
    var cur = boardConfig.kanban_group_by || 'status';
    var cols = _kanbanAxisOptions();
    if (!cols.length) {
        sel.innerHTML = '<option value="">(no groupable columns)</option>';
        return;
    }
    sel.innerHTML = cols.map(function(c) {
        return '<option value="'+esc(c.id)+'">'+esc(c.label || c.id)+'</option>';
    }).join('');
    sel.value = cols.some(function(c){return c.id === cur;}) ? cur : cols[0].id;
    if (sel.value !== cur) boardConfig.kanban_group_by = sel.value;
}

function onKanbanAxisChange() {
    boardConfig.kanban_group_by = document.getElementById('kanban-axis-select').value;
    renderKanban();
}

function _kanbanGroupsFor(col) {
    // Returns [{key, label, color, personId}] — one entry per kanban column.
    if (!col) return [];
    if (col.type === 'select' && (col.options || []).length) {
        return col.options.map(function(o) {
            return { key: o, label: o, color: (col.color_map && col.color_map[o]) || 'gray' };
        });
    }
    if (col.type === 'checkbox') {
        return [
            { key: 'true',  label: 'Checked',   color: 'green' },
            { key: 'false', label: 'Unchecked', color: 'gray'  },
        ];
    }
    if (isPersonSingle(col.type) || isPersonMulti(col.type)) {
        var people = Object.values(window.PEOPLE_BY_ID || {});
        var groups = people.map(function(p) {
            return { key: p.id, label: p.name, personId: p.id, color: 'blue' };
        });
        groups.push({ key: '', label: '— unassigned —', color: 'gray' });
        return groups;
    }
    // Fallback — derive unique values from items.
    var set = {};
    visibleItems().forEach(function(it) {
        var v = it[col.id];
        if (v === null || v === undefined || v === '') { set['(empty)'] = true; return; }
        if (Array.isArray(v)) v.forEach(function(x) { set[String(x)] = true; });
        else set[String(v)] = true;
    });
    return Object.keys(set).sort().map(function(k) {
        return { key: (k === '(empty)' ? '' : k), label: k, color: 'gray' };
    });
}

function _itemInGroup(item, col, groupKey) {
    var v = item[col.id];
    if (col.type === 'checkbox') return String(v === true || v === 'true') === groupKey;
    if (isPersonMulti(col.type)) {
        var arr = Array.isArray(v) ? v : (v ? [v] : []);
        if (!groupKey) return arr.length === 0;
        return arr.indexOf(groupKey) !== -1;
    }
    if (isPersonSingle(col.type)) {
        if (!groupKey) return !v;
        return v === groupKey;
    }
    // select / fallback
    return (v === null || v === undefined || v === '') ? groupKey === '' : String(v) === groupKey;
}

function renderKanban() {
    renderKanbanAxisSelect();
    var groupBy = boardConfig.kanban_group_by || 'status';
    var groupCol = (boardConfig.columns||[]).find(function(c){return c.id===groupBy;});
    var nameCol = (boardConfig.columns||[])[0];
    var src = visibleItems();
    // Map boards' palette names → EOS palette names. Boards' .pill-* and the
    // shared .eos-pill-* share the same eight tokens (blue/amber/green/emerald/
    // red/purple/orange/gray) so identity mapping is correct.
    var groups = _kanbanGroupsFor(groupCol);

    EOS_UI.kanbanLayout({
        mountId: 'kanban-board',
        items: src,
        groups: groups,
        inGroup: function(it, key) { return _itemInGroup(it, groupCol, key); },
        getItemId: function(it) { return it.file; },
        renderCard: function(item) {
            var title = item[nameCol ? nameCol.id : 'name'] || item.file;
            var meta = visibleCols().slice(1, 4)
                .filter(function(c) { return c.id !== groupBy && item[c.id]; })
                .map(function(c) {
                    var v = item[c.id];
                    if (c.type === 'number') v = (c.prefix || '') + v + (c.suffix || '');
                    return '<span>' + esc(String(v)) + '</span>';
                }).join('');
            // Card root is added by EOS_UI.kanbanLayout — we only contribute the
            // body. Click → detail (registered post-render below).
            return '<div class="eos-kanban-card-title">' + esc(String(title)) + '</div>' +
                   (meta ? '<div class="eos-kanban-card-meta">' + meta + '</div>' : '');
        },
        onMove: function(item, newKey) {
            var col = groupCol;
            var newValue;
            if (col && col.type === 'checkbox') newValue = (newKey === 'true');
            else if (col && isPersonMulti(col.type)) {
                var arr = Array.isArray(item[groupBy]) ? item[groupBy].slice()
                                                       : (item[groupBy] ? [item[groupBy]] : []);
                if (!newKey) newValue = [];                    // unassigned → clear
                else if (arr.indexOf(newKey) === -1) { arr.push(newKey); newValue = arr; }
                else return;                                    // already in
            } else newValue = newKey;
            updateField(item.file, groupBy, newValue);
        },
    });

    // Card-level click → openItemDetail. EOS_UI.kanbanLayout sets data-id; we
    // wire click handlers here so the helper stays click-handler-agnostic.
    document.querySelectorAll('#kanban-board .eos-kanban-card').forEach(function(card) {
        card.addEventListener('click', function() {
            var fileId = card.getAttribute('data-id') || '';
            if (fileId) openItemDetail(fileId);
        });
    });
}

// ── Calendar View ──
function renderCalendar() {
    var viewCfg = (boardConfig.views||[]).find(function(v){return v.type==='calendar';}) || {};
    var dateField = viewCfg.date_field || 'date';
    var today = new Date();
    var year = today.getFullYear(), month = today.getMonth();
    var first = new Date(year, month, 1), last = new Date(year, month+1, 0);
    var startDay = first.getDay();
    var days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    var html = '<div class="calendar-grid">';
    html += days.map(function(d){return '<div class="calendar-day-header">'+d+'</div>';}).join('');
    var d = new Date(first); d.setDate(d.getDate() - startDay);
    for (var i = 0; i < 42; i++) {
        var iso = d.toISOString().slice(0,10);
        var isToday = iso === today.toISOString().slice(0,10);
        var isOther = d.getMonth() !== month;
        html += '<div class="calendar-day'+(isOther?' other-month':'')+(isToday?' today':'')+'">';
        html += '<div class="calendar-day-num">'+d.getDate()+'</div>';
        visibleItems().forEach(function(item) {
            if ((item[dateField]||'').slice(0,10) === iso) {
                var nameCol = (boardConfig.columns||[])[0];
                html += '<div class="calendar-item" onclick="openItemDetail(\''+esc(item.file)+'\')">'+esc(String(item[nameCol?nameCol.id:'name']||item.file))+'</div>';
            }
        });
        html += '</div>';
        d.setDate(d.getDate()+1);
    }
    html += '</div>';
    document.getElementById('calendar-container').innerHTML = html;
}

// ── Chart View ──
async function renderChart() {
    var container = document.getElementById('chart-container');
    var groupBy = boardConfig.kanban_group_by || 'status';
    var groupCol = (boardConfig.columns||[]).find(function(c){return c.id===groupBy;});

    // Count by group
    var counts = {};
    var src = visibleItems();
    src.forEach(function(item) {
        var key = String(item[groupBy]||'unset');
        counts[key] = (counts[key]||0) + 1;
    });
    var total = src.length || 1;
    var entries = Object.entries(counts).sort(function(a,b){return b[1]-a[1];});

    // Bar chart
    var barHtml = '<div class="chart-widget"><h3>Items by '+esc(groupCol?groupCol.label:groupBy)+'</h3><div class="bar-chart">';
    entries.forEach(function(e, i) {
        var pct = Math.round(e[1]/total*100);
        var color = CHART_COLORS[i % CHART_COLORS.length];
        barHtml += '<div class="bar-row"><div class="bar-label">'+esc(e[0])+'</div><div class="bar-track"><div class="bar-fill" style="width:'+pct+'%;background:'+color+'">'+e[1]+'</div></div></div>';
    });
    barHtml += '</div></div>';

    // Donut chart via SVG
    var donutHtml = '<div class="chart-widget"><h3>Distribution</h3><div class="donut-wrap"><svg class="donut-svg" viewBox="0 0 42 42">';
    var offset = 0, radius = 15.915;
    entries.forEach(function(e, i) {
        var pct = e[1]/total*100;
        var color = CHART_COLORS[i % CHART_COLORS.length];
        donutHtml += '<circle cx="21" cy="21" r="'+radius+'" fill="none" stroke="'+color+'" stroke-width="5" stroke-dasharray="'+pct+' '+(100-pct)+'" stroke-dashoffset="'+(-offset)+'" />';
        offset += pct;
    });
    donutHtml += '</svg><div class="donut-legend">';
    entries.forEach(function(e, i) {
        donutHtml += '<div class="donut-legend-item"><div class="donut-legend-dot" style="background:'+CHART_COLORS[i%CHART_COLORS.length]+'"></div>'+esc(e[0])+' ('+e[1]+')</div>';
    });
    donutHtml += '</div></div></div>';

    container.innerHTML = barHtml + donutHtml;
    // Animate bars
    setTimeout(function(){ container.querySelectorAll('.bar-fill').forEach(function(el){el.style.width=el.style.width;}); }, 50);
}

// Form-input dispatch. (col, value) -> input HTML. Value may be '' for the
// add form. Called by both addItem() and the detail slide-out.
var COLUMN_FORM_INPUTS = {
    'select': function(c, val) {
        if (!c.options) return COLUMN_FORM_INPUTS.text(c, val);
        return '<select class="form-input" data-field="'+esc(c.id)+'">' +
            c.options.map(function(o){return '<option'+(o===val?' selected':'')+'>'+esc(o)+'</option>';}).join('') + '</select>';
    },
    'date': function(c, val) {
        var v = val ? String(val).slice(0,10) : '';
        return '<input type="date" class="form-input" data-field="'+esc(c.id)+'" value="'+esc(v)+'">';
    },
    'number': function(c, val) {
        var v = (val === '' || val === null || val === undefined) ? '' : String(val);
        return '<input type="number" class="form-input" data-field="'+esc(c.id)+'" value="'+esc(v)+'">';
    },
    'checkbox': function(c, val) {
        var checked = (val===true || val==='true') ? ' checked' : '';
        return '<input type="checkbox" data-field="'+esc(c.id)+'"'+checked+'>';
    },
    'link': function(c, val) {
        var v = (val === '' || val === null || val === undefined) ? '' : String(val);
        return '<input type="url" class="form-input" data-field="'+esc(c.id)+'" value="'+esc(v)+'">';
    },
    'text': function(c, val) {
        var v = (val === '' || val === null || val === undefined) ? '' : String(val);
        return '<input type="text" class="form-input" data-field="'+esc(c.id)+'" value="'+esc(v)+'">';
    }
};

function renderFormInput(col, val) {
    var fn = COLUMN_FORM_INPUTS[col.type] || COLUMN_FORM_INPUTS.text;
    return fn(col, val);
}

// ── Add Item ──
function addItem() {
    if (isReadonly()) return;
    var fields = document.getElementById('add-item-fields');
    fields.innerHTML = (boardConfig.columns||[]).map(function(c) {
        return '<div class="form-group"><label>'+esc(c.label)+'</label>'+renderFormInput(c, '')+'</div>';
    }).join('');
    openModal('add-modal');
}

async function submitAddItem() {
    var data = {};
    document.querySelectorAll('#add-item-fields [data-field]').forEach(function(el) {
        var f = el.dataset.field;
        if (el.type === 'checkbox') data[f] = el.checked;
        else data[f] = el.value;
    });
    try {
        await fetch('/boards/api/boards/'+currentBoardId+'/items', {
            method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)
        });
        closeModal('add-modal');
        await loadBoard(currentBoardId);
    } catch(e) { console.error(e); }
}

// ── Create Board ──
async function createFromPreset(presetId) {
    try {
        var res = await fetch('/boards/api/boards', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({preset: presetId})
        });
        var data = await res.json();
        if (data.ok) window.location.hash = data.id;
    } catch(e) { console.error(e); }
}

async function submitCreateBoard() {
    var id = document.getElementById('new-board-id').value.trim();
    var name = document.getElementById('new-board-name').value.trim();
    var tag = document.getElementById('new-board-tag').value.trim();
    if (!id) return;
    try {
        var res = await fetch('/boards/api/boards', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({id:id, name:name||id, source_tag:tag||id})
        });
        var data = await res.json();
        if (data.ok) { closeModal('create-modal'); window.location.hash = data.id; }
    } catch(e) { console.error(e); }
}

function exportBoard() {
    if (window.EOS_IS_EXPORT) { EOS_UI.toast('Already in offline mode.'); return; }
    window.location.href = '/boards/api/export/'+currentBoardId;
}

// ── Modals ──
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function esc(s) { var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// ── Timeline view (A4) ─────────────────────────────────────
function renderTimeline() {
    var container = document.getElementById('timeline-container');
    var cols = boardConfig.columns || [];
    var dateCol = cols.find(function(c){return c.type==='date' || c.type==='timeline';});
    if (!dateCol) {
        container.innerHTML = EOS_UI.emptyState({icon:'⏳', message:'Add a date or timeline column to use this view.'});
        return;
    }
    var items = visibleItems().filter(function(i){return i[dateCol.id];});
    if (!items.length) {
        container.innerHTML = EOS_UI.emptyState({icon:'⏳', message:'No items with a date value yet.'});
        return;
    }
    var nameCol = cols[0];
    // Order rows so dependencies cascade down the page.
    // 1) If the board has a `project` column, group by project (stable block per project).
    // 2) Within each group (or globally), sort by due date ascending; ties break by
    //    dependency depth (items that *block* others come above items they unlock),
    //    and finally by title for determinism.
    (function orderRowsForGantt() {
        var projCol = cols.find(function(c){ return c.id === 'project'; });
        // Depth = 0 for root items; 1 + max(depth(blockers)) otherwise. Used as
        // a tiebreaker so SLDs sit above their own Cable Schedules even when
        // dues are close.
        var depthCache = {};
        function depth(item, seen) {
            var id = item.file || item.id;
            if (depthCache[id] !== undefined) return depthCache[id];
            seen = seen || {};
            if (seen[id]) return 0;          // cycle safety — caller's guard rejects real cycles
            seen[id] = true;
            var by = item.blocked_by || [];
            if (typeof by === 'string') by = by.split(',').map(function(s){return s.trim();}).filter(Boolean);
            if (!by.length) { depthCache[id] = 0; return 0; }
            var d = 0;
            by.forEach(function(bid) {
                var parent = items.find(function(it){ return (it.file||it.id) === bid; });
                if (parent) d = Math.max(d, 1 + depth(parent, seen));
            });
            depthCache[id] = d;
            return d;
        }
        items.sort(function(a, b) {
            if (projCol) {
                var pa = String(a.project || ''); var pb = String(b.project || '');
                if (pa !== pb) return pa < pb ? -1 : 1;
            }
            var da = new Date(a[dateCol.id]).getTime() || 0;
            var db = new Date(b[dateCol.id]).getTime() || 0;
            if (da !== db) return da - db;
            var depA = depth(a); var depB = depth(b);
            if (depA !== depB) return depA - depB;
            var na = String(a[nameCol?nameCol.id:'name'] || '');
            var nb = String(b[nameCol?nameCol.id:'name'] || '');
            return na.localeCompare(nb);
        });
    })();
    var statusCol = cols.find(function(c){return c.id==='status' || c.type==='select';});

    // View config may declare `start_field` / `end_field`. Fallbacks:
    //   start → item.start_date → item.created → (end - 7 days)
    //   end   → dateCol's field (the "Due" column by default)
    var tlView = (boardConfig.views || []).find(function(v){return v.type === 'timeline';}) || {};
    var startField = tlView.start_field || 'start_date';
    var endField   = tlView.end_field   || dateCol.id;

    function parseT(v) { if (!v) return NaN; var t = new Date(v).getTime(); return isNaN(t) ? NaN : t; }
    function itemSpan(item) {
        var endT = parseT(item[endField]);
        var startT = parseT(item[startField]) || parseT(item.start_date) || parseT(item.created);
        if (isNaN(endT) && !isNaN(startT)) endT = startT;
        if (isNaN(startT) && !isNaN(endT)) startT = endT - 7 * 86400000;  // default 1-week span
        return { startT: startT, endT: endT };
    }

    // Global date range — earliest start to latest end across all visible items.
    var minT = Infinity, maxT = -Infinity;
    items.forEach(function(it) {
        var s = itemSpan(it);
        if (!isNaN(s.startT) && s.startT < minT) minT = s.startT;
        if (!isNaN(s.endT)   && s.endT   > maxT) maxT = s.endT;
    });
    if (!isFinite(minT) || !isFinite(maxT)) {
        container.innerHTML = EOS_UI.emptyState({message:'No parseable dates.'}); return;
    }
    var dayMs = 86400000;
    minT -= 3 * dayMs; maxT += 3 * dayMs;
    var totalDays = Math.max(14, Math.ceil((maxT - minT) / dayMs));

    // Header — day ticks (label every 7 days + month starts)
    var ticks = '';
    for (var d = 0; d <= totalDays; d++) {
        var ts = minT + d*dayMs;
        var dt = new Date(ts);
        var label = '';
        if (d === 0 || dt.getDate() === 1 || d % 7 === 0) {
            label = dt.toLocaleDateString(undefined, {month:'short', day:'numeric'});
        }
        ticks += '<div class="timeline-tick"'+(d%7===0?' style="border-left-color:var(--accent);opacity:0.5"':'')+'>'+esc(label)+'</div>';
    }

    // Row rendering. Bars below a readability threshold (NARROW_PCT) render
    // the label outside-right instead of trying to fit it inside the pill.
    var NARROW_PCT = 8;       // if widthPct < 8% (~1 week on a 14-wk chart), label goes outside
    var rows = items.map(function(item) {
        var span = itemSpan(item);
        if (isNaN(span.startT) || isNaN(span.endT)) return '';       // unplottable
        var offset = (span.startT - minT) / dayMs;
        var duration = Math.max(1, (span.endT - span.startT) / dayMs);
        var leftPct = (offset / totalDays) * 100;
        var widthPct = (duration / totalDays) * 100;
        if (widthPct < 1.5) widthPct = 1.5;                         // keep a clickable minimum
        var status = statusCol ? item[statusCol.id] : '';
        var color = (statusCol && statusCol.color_map && statusCol.color_map[status]) || 'purple';
        var title = item[nameCol?nameCol.id:'name'] || item.file;
        var itemId = item.file || item.id || '';

        // Tooltip lines — full info, since the bar may be tiny.
        var startStr = new Date(span.startT).toLocaleDateString(undefined, {month:'short', day:'numeric'});
        var endStr   = new Date(span.endT).toLocaleDateString(undefined, {month:'short', day:'numeric'});
        var tooltip = title + '\n' + startStr + ' → ' + endStr +
                      (status ? '  ·  ' + status : '') +
                      '\n' + Math.round(duration) + ' days';

        var outsideLabel = widthPct < NARROW_PCT;
        var barInner = outsideLabel ? '' : esc(String(title));
        var outsideHtml = outsideLabel
            ? '<span class="timeline-outside-label" style="left:calc('+(leftPct+widthPct)+'% + 6px)">'+esc(String(title))+'</span>'
            : '';

        return '<div class="timeline-row" data-item-id="'+esc(itemId)+'">' +
            '<div class="timeline-row-label" onclick="openItemDetail(\''+esc(itemId)+'\')">'+esc(String(title))+'</div>' +
            '<div class="timeline-row-track">' +
                '<div class="timeline-bar eos-pill '+(PILL_COLORS[color]||'eos-pill-purple')+'" ' +
                     'data-item-id="'+esc(itemId)+'" ' +
                     'style="left:'+leftPct+'%;width:'+widthPct+'%;" ' +
                     'title="'+esc(tooltip)+'" ' +
                     'onclick="openItemDetail(\''+esc(itemId)+'\')">' +
                    barInner + '</div>' +
                outsideHtml +
            '</div></div>';
    }).join('');
    container.innerHTML =
        '<div class="timeline-wrap"><div class="timeline-header">' +
            '<div class="timeline-row-label"></div>' +
            '<div class="timeline-header-ticks" style="grid-template-columns:repeat('+(totalDays+1)+', 1fr)">'+ticks+'</div>' +
        '</div>' +
        '<div class="timeline-body">'+rows+'</div></div>';
    // Draw dependency arrows once layout settles. Redraw on window resize.
    requestAnimationFrame(function(){ drawGanttArrows(items); });
    if (!window._ganttResizeBound) {
        window._ganttResizeBound = true;
        window.addEventListener('resize', function(){ if (currentView === 'timeline') drawGanttArrows(boardItems); });
    }
}

function drawGanttArrows(items) {
    var body = document.querySelector('#timeline-container .timeline-body');
    if (!body) return;
    body.style.position = 'relative';
    var old = body.querySelector('svg.gantt-arrows');
    if (old) old.remove();
    var bodyRect = body.getBoundingClientRect();
    if (bodyRect.width < 10) return;

    var svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('class','gantt-arrows');
    svg.setAttribute('width', bodyRect.width);
    svg.setAttribute('height', bodyRect.height);
    svg.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:5;overflow:visible';
    svg.innerHTML =
        '<defs><marker id="gantt-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">' +
        '<path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker></defs>';

    var drawn = 0;
    items.forEach(function(item){
        var blocks = item.blocks || [];
        if (typeof blocks === 'string') blocks = blocks.split(',').map(function(s){return s.trim();}).filter(Boolean);
        if (!blocks.length) return;
        var fromBar = body.querySelector('.timeline-bar[data-item-id="'+(item.file||item.id)+'"]');
        if (!fromBar) return;
        var fr = fromBar.getBoundingClientRect();
        blocks.forEach(function(blockerId){
            var toBar = body.querySelector('.timeline-bar[data-item-id="'+blockerId+'"]');
            if (!toBar) return;
            var tr = toBar.getBoundingClientRect();
            var x1 = fr.right - bodyRect.left;
            var y1 = fr.top + fr.height/2 - bodyRect.top;
            var x2 = tr.left - bodyRect.left;
            var y2 = tr.top + tr.height/2 - bodyRect.top;
            // Route so the arrowhead always points INTO the target bar. Three cases
            // based on the horizontal relationship between predecessor end (x1)
            // and target start (x2):
            //
            //   (a) Plenty of slack (x2 - x1 ≥ 20px):
            //       elbow at `x2 - margin`; arrow approaches from the LEFT.
            //   (b) Tight FS handoff (x1 ≈ x2):
            //       drop straight down onto the target's TOP edge. Arrow points DOWN.
            //       This is the standard Gantt visual for a zero-slack handoff.
            //   (c) Out-of-order (x2 < x1, unusual):
            //       detour BELOW and approach target from the left.
            var margin = 10;
            var d, dashed = false, arrowDirection = 'right';
            if (x2 - x1 >= margin * 2) {
                // (a) normal — elbow near target, approach rightward.
                var elbowX = x2 - margin;
                var approachX = x2 - 4;
                d = 'M '+x1+' '+y1+' L '+elbowX+' '+y1+' L '+elbowX+' '+y2+' L '+approachX+' '+y2;
            } else if (x2 >= x1 - 2) {
                // (b) tight handoff — land on target's top edge with a down-arrow.
                var landX = Math.max(x2, x1) + 4;       // a few px inside target's left edge
                var topY = y2 - 10;                      // bar is 20px tall, y2 is vertical centre
                d = 'M '+x1+' '+y1+' L '+landX+' '+y1+' L '+landX+' '+topY;
                arrowDirection = 'down';
            } else {
                // (c) out-of-order — route below predecessor, come back up into target.
                var detourY = Math.max(y1, y2) + 14;
                var approachBack = x2 - 4;
                d = 'M '+x1+' '+y1+
                    ' L '+(x1+4)+' '+y1+
                    ' L '+(x1+4)+' '+detourY+
                    ' L '+(x2-margin)+' '+detourY+
                    ' L '+(x2-margin)+' '+y2+
                    ' L '+approachBack+' '+y2;
                dashed = true;
            }
            var path = document.createElementNS('http://www.w3.org/2000/svg','path');
            path.setAttribute('d', d);
            path.setAttribute('fill','none');
            path.setAttribute('stroke','var(--accent)');
            path.setAttribute('stroke-width','1.5');
            if (dashed) path.setAttribute('stroke-dasharray', '4 3');
            path.setAttribute('marker-end','url(#gantt-arrow)');
            path.setAttribute('opacity','0.7');
            svg.appendChild(path);
            drawn++;
        });
    });
    if (drawn) body.appendChild(svg);
}

// ── Item detail slide-out (A2) ─────────────────────────────
async function openItemDetail(file) {
    if (!file) return;
    currentDetailFile = file;
    var item = boardItems.find(function(i){return i.file===file;});
    if (!item) {
        try {
            var r = await fetch('/boards/api/boards/'+currentBoardId+'/items/'+encodeURIComponent(file));
            item = await r.json();
        } catch (_) {}
    }
    if (!item || item.error) return;
    var panel = document.getElementById('board-detail');
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    var nameCol = (boardConfig.columns || [])[0];
    document.getElementById('detail-title').textContent = String(item[nameCol?nameCol.id:'name'] || item.file || 'Item');
    document.getElementById('detail-path').textContent = item.path || item.file || '';
    // Fields form
    var cols = boardConfig.columns || [];
    document.getElementById('detail-fields').innerHTML = cols.map(function(c) {
        var val = item[c.id];
        if (val === undefined || val === null) val = '';
        return '<div class="form-group"><label>'+esc(c.label)+'</label>'+renderFormInput(c, val)+'</div>';
    }).join('');
    // Hash deep-link
    if (window.location.hash.indexOf('#'+currentBoardId) === 0) {
        window.location.hash = currentBoardId + '/' + encodeURIComponent(file);
    }
    // Show Suggest-candidates button only if this item has skills_required.
    var skills = item.skills_required || [];
    if (typeof skills === 'string') skills = skills.split(',').map(function(s){return s.trim();}).filter(Boolean);
    document.getElementById('suggest-btn').style.display = skills.length ? '' : 'none';
    document.getElementById('suggest-results').innerHTML = '';
    // Load backlinks + activity
    loadItemBacklinks(file);
    loadItemActivity(file);
}

async function loadItemBacklinks(file) {
    var section = document.getElementById('detail-backlinks-section');
    var host = document.getElementById('detail-backlinks');
    if (!section || !host) return;
    section.style.display = 'none';
    host.innerHTML = '';
    if (window.EOS_IS_EXPORT) return;
    try {
        var res = await fetch('/boards/api/boards/'+encodeURIComponent(currentBoardId)+'/items/'+encodeURIComponent(file)+'/backlinks');
        var data = await res.json();
        var items = (data && data.backlinks) || [];
        if (!items.length) return;
        host.innerHTML = items.map(function(b){
            return '<div class="backlink-row" onclick="openLinkedItem(\''+esc(b.board)+'\',\''+esc(b.file)+'\')">' +
                '<span class="backlink-board">'+esc(b.board_name || b.board)+'</span>' +
                '<span class="backlink-title">'+esc(b.title)+'</span>' +
                '<span class="backlink-col">via '+esc(b.col)+'</span></div>';
        }).join('');
        section.style.display = '';
    } catch(e) { console.error('backlinks', e); }
}

async function suggestCandidates() {
    if (!currentDetailFile) return;
    var item = boardItems.find(function(i){return (i.file||i.id) === currentDetailFile;}) || {};
    var skills = item.skills_required || [];
    if (typeof skills === 'string') skills = skills.split(',').map(function(s){return s.trim();}).filter(Boolean);
    if (!skills.length) return;
    var need = parseFloat(item.weight_hours || 0) || 0;
    var q = '/people/api/match?skills=' + encodeURIComponent(skills.join(',')) + '&need_hours=' + need;
    var results = await fetch(q).then(function(r){return r.json();});
    var out = document.getElementById('suggest-results');
    if (!Array.isArray(results) || !results.length) {
        out.innerHTML = EOS_UI.emptyState({message:'No candidates. Add people with matching skills via /people/.'});
        return;
    }
    out.innerHTML = '<h3 style="margin:0.8rem 0 0.3rem;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-secondary)">Candidates</h3>' +
        results.slice(0, 5).map(function(c) {
            var pct = Math.round((c.load_ratio||0)*100);
            var col = c.band === 'overloaded' ? 'var(--danger)' : c.band === 'busy' ? 'var(--warning)' : 'var(--success)';
            return '<div class="suggest-row" onclick="assignToCandidate(\''+esc(c.id)+'\')" style="display:flex;justify-content:space-between;padding:0.4rem 0.2rem;border-bottom:1px solid var(--border);cursor:pointer">' +
                '<span>'+esc(c.name)+' <span style="color:var(--text-muted);font-size:0.72rem">'+esc(c.role||'')+'</span></span>' +
                '<span style="font-size:0.76rem">🎯 '+c.overlap+'/'+skills.length+' · <span style="color:'+col+'">'+pct+'%</span></span>' +
                '</div>';
        }).join('');
}

async function assignToCandidate(personId) {
    // Find the first person-type column and set that person.
    var cols = boardConfig.columns || [];
    var col = cols.find(function(c){return isPersonSingle(c.type);});
    if (!col) { EOS_UI.toast('This board has no person column to assign into.', false); return; }
    var resp = await fetch('/boards/api/boards/'+currentBoardId+'/items/'+encodeURIComponent(currentDetailFile), {
        method: 'PATCH', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({updates: {[col.id]: personId}})
    }).then(function(r){return r.json();});
    if (resp.error) { EOS_UI.toast(resp.error, false); return; }
    await loadBoard(currentBoardId);
    openItemDetail(currentDetailFile);
}

function closeItemDetail() {
    var panel = document.getElementById('board-detail');
    if (!panel) return;
    panel.classList.remove('open');
    panel.setAttribute('aria-hidden', 'true');
    currentDetailFile = null;
    // Strip the item segment from the hash
    if (currentBoardId && window.location.hash.indexOf('/') !== -1) {
        window.location.hash = currentBoardId;
    }
}

async function saveItemDetail() {
    if (!currentDetailFile) return;
    var updates = {};
    document.querySelectorAll('#detail-fields [data-field]').forEach(function(el) {
        var f = el.dataset.field;
        updates[f] = (el.type === 'checkbox') ? el.checked : el.value;
    });
    try {
        var r = await fetch('/boards/api/boards/'+currentBoardId+'/items/'+encodeURIComponent(currentDetailFile), {
            method: 'PATCH', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({updates: updates})
        });
        var resp = await r.json();
        if (resp.error) { EOS_UI.toast(resp.error, false); return; }
        // Refresh local items and active view
        var item = boardItems.find(function(i){return i.file===currentDetailFile;});
        if (item) Object.assign(item, updates);
        switchView(currentView);
        loadItemActivity(currentDetailFile);
    } catch (e) { console.error(e); }
}

async function archiveCurrentItem() {
    if (!currentDetailFile) return;
    if (!await EOS_UI.confirm({message: 'Archive this item?', action: 'Archive', danger: false})) return;
    await fetch('/boards/api/boards/'+currentBoardId+'/items/'+encodeURIComponent(currentDetailFile), {method: 'DELETE'});
    closeItemDetail();
    await loadBoard(currentBoardId);
}

async function loadItemActivity(file) {
    var out = document.getElementById('detail-activity');
    out.innerHTML = '<em>Loading…</em>';
    try {
        var r = await fetch('/boards/api/boards/'+currentBoardId+'/items/'+encodeURIComponent(file)+'/activity');
        var data = await r.json();
        var events = (data && data.events) || [];
        if (!events.length) { out.innerHTML = EOS_UI.emptyState({message:'No activity yet.'}); return; }
        out.innerHTML = events.map(function(e) {
            var t = (e.timestamp || '').slice(0, 19).replace('T', ' ');
            var label = e.type.replace('board:item_', '').replace('_', ' ');
            var detail = '';
            if (e.updates && Object.keys(e.updates).length) {
                detail = Object.keys(e.updates).map(function(k){ return esc(k)+' → '+esc(String(e.updates[k])); }).join(', ');
            }
            return '<div class="activity-row"><span class="activity-label">'+esc(label)+'</span>' +
                   (detail?'<span class="activity-detail">'+detail+'</span>':'') +
                   '<span class="activity-time">'+esc(t)+'</span></div>';
        }).join('');
    } catch (e) {
        out.innerHTML = '<p style="color:var(--board-text-dim)">Activity unavailable in offline mode.</p>';
    }
}

// ── Keyboard shortcuts ─────────────────────────────────────
document.addEventListener('keydown', function(e) {
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) {
        if (e.key === 'Escape' && e.target.id === 'board-search') { e.target.value=''; e.target.blur(); onSearchInput(); }
        return;
    }
    if (e.key === 'Escape') {
        // Clear person filter first — it's the most common temporary state.
        if (personFilter) { filterByPerson(''); return; }
        var anyOpen = document.querySelector('.modal-overlay.open');
        if (anyOpen) anyOpen.classList.remove('open');
        else if (currentDetailFile) closeItemDetail();
        return;
    }
    if (e.key === '/') {
        var s = document.getElementById('board-search');
        if (s && document.getElementById('board-view').style.display !== 'none') { e.preventDefault(); s.focus(); s.select(); }
        return;
    }
    if (e.key === '[') { toggleNav(); return; }
    if (e.key === 'j' || e.key === 'ArrowDown') { if (e.altKey) navCycle(1); }
    if (e.key === 'k' || e.key === 'ArrowUp') { if (e.altKey) navCycle(-1); }
});

// Restore collapsed-nav state
(function restoreNav() {
    try {
        if (localStorage.getItem('boards.nav.collapsed') === '1') {
            var nav = document.getElementById('boards-nav');
            if (nav) nav.classList.add('collapsed');
        }
    } catch (_) {}
})();

window.onload = init;
window.addEventListener('hashchange', function() {
    // Parse hash into boardId / itemFile
    var hash = window.location.hash.slice(1);
    var parts = hash.split('/');
    var boardId = parts[0] || '';
    var file = parts[1] ? decodeURIComponent(parts[1]) : '';
    if (!boardId) { loadHome(); return; }
    if (boardId !== currentBoardId) {
        loadBoard(boardId).then(function() { if (file) openItemDetail(file); });
    } else if (file && file !== currentDetailFile) {
        openItemDetail(file);
    } else if (!file && currentDetailFile) {
        closeItemDetail();
    }
});

// ----- App Settings (shared helper) -----
var _appSettings = EOS_UI.settingsPanel({
    id: 'app-settings-panel',
    title: 'Boards Settings',
    fields: [
        {key: 'boards.items_dir', label: 'Items Directory', type: 'text', default: '30_Resources/EmptyOS/boards-data',
         hint: 'Vault folder for board item notes.'},
    ],
});
function openAppSettings() { _appSettings.open(); }
