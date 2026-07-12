// Boards — Planner import wizard + xlsx export button (DOM layer).
// Sibling of boards.js/detail-collab.js. Pure parse/serialize logic lives in
// planner-xlsx.js (EOS_PLANNER) with mapping data in planner-map.js
// (EOS_PLANNER_MAP); this file only wires them to the modal + toolbar.
// Wizard shape: file pick → header mapping → dry-run diff → confirm POST
// (propose/preview/confirm — nothing is written until the final step).

var _planner = { rows: null, headerIndex: -1, headers: [], mapping: {}, records: [], planName: '', target: null };

function openPlannerImport() {
    _planner = { rows: null, headerIndex: -1, headers: [], mapping: {}, records: [], planName: '', target: null };
    var f = document.getElementById('planner-file');
    if (f) f.value = '';
    _plannerShowStep('file');
    openModal('planner-modal');
}

function _plannerShowStep(step) {
    ['file', 'map', 'diff', 'done'].forEach(function (s) {
        var el = document.getElementById('planner-step-' + s);
        if (el) el.style.display = s === step ? '' : 'none';
    });
}

function plannerFileChosen(inputEl) {
    var file = inputEl.files && inputEl.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function (e) {
        try {
            var wb = XLSX.read(e.target.result, { type: 'array', cellDates: true });
            var ws = wb.Sheets[wb.SheetNames[0]];
            var rows = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: '' });
            var head = EOS_PLANNER.detectHeaderRow(rows);
            if (head.index < 0) {
                EOS_UI.toast('Could not find a Planner header row in this file.', false);
                return;
            }
            _planner.rows = rows;
            _planner.headerIndex = head.index;
            _planner.headers = head.headers;
            // Plan name from the metadata rows above the header.
            for (var i = 0; i < head.index; i++) {
                var r = rows[i] || [];
                if (String(r[0] || '').trim().toLowerCase() === 'plan name') {
                    _planner.planName = String(r[1] || '').trim();
                    break;
                }
            }
            // Auto-guess, then overlay the board's saved mapping if one exists
            // and its indices still point at matching headers.
            _planner.mapping = EOS_PLANNER.guessMapping(head.headers);
            var saved = (typeof boardConfig !== 'undefined' && boardConfig && boardConfig.planner && boardConfig.planner.mapping) || null;
            if (saved) {
                Object.keys(saved).forEach(function (f2) {
                    var idx = saved[f2];
                    if (idx >= 0 && idx < head.headers.length) _planner.mapping[f2] = idx;
                });
            }
            _plannerRenderMapping();
            _plannerShowStep('map');
        } catch (err) {
            console.error('planner parse', err);
            EOS_UI.toast('Could not read this file as .xlsx', false);
        }
    };
    reader.readAsArrayBuffer(file);
}

function _plannerRenderMapping() {
    var host = document.getElementById('planner-mapping-rows');
    var map = window.EOS_PLANNER_MAP;
    var fields = Object.keys(map.fields);
    host.innerHTML = fields.map(function (f) {
        var spec = map.fields[f];
        var label = spec.header || f;
        var sel = _planner.mapping[f];
        var required = (map.required || []).indexOf(f) !== -1;
        return '<div class="filter-panel-row">' +
            '<span style="flex:0 0 42%;font-size:0.82rem">' + esc(label) + (required ? ' <span style="color:var(--danger)">*</span>' : '') + '</span>' +
            '<select class="form-input" style="flex:1" onchange="plannerMapChange(' + escAttr(JSON.stringify(f)) + ',this.value)">' +
            '<option value="-1">(not mapped)</option>' +
            _planner.headers.map(function (h, i) {
                return '<option value="' + i + '"' + (sel === i ? ' selected' : '') + '>' + esc(h || ('column ' + (i + 1))) + '</option>';
            }).join('') +
            '</select></div>';
    }).join('');
    var mapped = {};
    Object.keys(_planner.mapping).forEach(function (f) { mapped[_planner.mapping[f]] = true; });
    var unmapped = _planner.headers.filter(function (h, i) { return h && !mapped[i]; });
    document.getElementById('planner-map-warnings').textContent =
        unmapped.length ? 'Unmapped source columns (ignored): ' + unmapped.join(', ') : '';
}

function plannerMapChange(field, idx) {
    idx = parseInt(idx, 10);
    if (idx < 0) delete _planner.mapping[field];
    else _planner.mapping[field] = idx;
}

function _plannerTarget() {
    // Import into the open board when it's a writable vault_tag board;
    // otherwise create a new board named after the plan.
    if (typeof currentBoardId !== 'undefined' && currentBoardId && typeof boardConfig !== 'undefined' && boardConfig) {
        var srcType = (boardConfig.source || {}).type;
        if (srcType !== 'app' && srcType !== 'mixed' && !boardConfig.readonly) {
            return { id: currentBoardId, create: false, name: boardConfig.name || currentBoardId };
        }
    }
    var slug = String(_planner.planName || 'planner-import').toLowerCase()
        .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'planner-import';
    return { id: slug, create: true, name: _planner.planName || slug };
}

function plannerShowDiff() {
    var map = window.EOS_PLANNER_MAP;
    var missing = (map.required || []).filter(function (f) { return !(f in _planner.mapping); });
    if (missing.length) {
        EOS_UI.toast('Map the required column(s): ' + missing.map(function (f) { return map.fields[f].header || f; }).join(', '), false);
        return;
    }
    _planner.records = EOS_PLANNER.toCanonicalRecords(_planner.rows, _planner.headerIndex, _planner.mapping);
    if (!_planner.records.length) {
        EOS_UI.toast('No task rows found below the header.', false);
        return;
    }
    _planner.target = _plannerTarget();
    var items = _planner.target.create ? [] : (typeof boardItems !== 'undefined' ? boardItems : []);
    var diff = EOS_PLANNER.diffAgainstItems(_planner.records, items);
    var s = document.getElementById('planner-diff-summary');
    var lines = [
        '<div class="planner-diff-line"><strong>' + diff.new.length + '</strong> new · <strong>' +
            diff.updated.length + '</strong> updated · <strong>' + diff.unchanged.length + '</strong> unchanged</div>',
        '<div class="planner-diff-line">Target: ' + (_planner.target.create
            ? 'new board “' + esc(_planner.target.name) + '”'
            : 'this board (“' + esc(_planner.target.name) + '”)') + '</div>',
    ];
    if (diff.peopleToCreate.length) {
        lines.push('<div class="planner-diff-line">' + diff.peopleToCreate.length + ' people not in the roster: ' +
            esc(diff.peopleToCreate.join(', ')) + '</div>');
    }
    if (diff.newOptions.bucket.length && !_planner.target.create) {
        lines.push('<div class="planner-diff-line">New buckets: ' + esc(diff.newOptions.bucket.join(', ')) + '</div>');
    }
    if (diff.updated.length) {
        lines.push('<details style="margin-top:0.4rem"><summary style="cursor:pointer;font-size:0.8rem">Changed items</summary>' +
            diff.updated.slice(0, 20).map(function (u) {
                return '<div class="planner-diff-row">' + esc(u.record.name || u.key) + ' <span style="color:var(--text-muted)">(' + esc(u.changed.join(', ')) + ')</span></div>';
            }).join('') + (diff.updated.length > 20 ? '<div class="planner-diff-row">…and ' + (diff.updated.length - 20) + ' more</div>' : '') +
            '</details>');
    }
    s.innerHTML = lines.join('');
    _plannerShowStep('diff');
}

function plannerBackToMap() { _plannerShowStep('map'); }

async function plannerApply() {
    var btn = document.getElementById('planner-apply-btn');
    btn.disabled = true;
    try {
        var createPeople = !!(document.getElementById('planner-create-people') || {}).checked;
        var r = await fetch('/boards/api/boards/' + encodeURIComponent(_planner.target.id) + '/planner/apply', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                records: _planner.records,
                mapping: _planner.mapping,
                options: {
                    create_board: _planner.target.create,
                    board_name: _planner.target.name,
                    plan_name: _planner.planName,
                    create_people: createPeople,
                },
            }),
        });
        var resp = await r.json();
        if (resp.error) { EOS_UI.toast(resp.error, false); btn.disabled = false; return; }
        var done = document.getElementById('planner-step-done');
        done.innerHTML = '<div class="planner-diff-line">✅ Imported: <strong>' + resp.created + '</strong> created, <strong>' +
            resp.updated + '</strong> updated, ' + resp.unchanged + ' unchanged' +
            (resp.people_created && resp.people_created.length ? ' · people created: ' + esc(resp.people_created.join(', ')) : '') + '</div>' +
            '<div class="form-actions"><button class="btn btn-primary" onclick="plannerFinish()">Open board</button></div>';
        _plannerShowStep('done');
    } catch (e) {
        console.error('plannerApply', e);
        EOS_UI.toast('Import failed', false);
        btn.disabled = false;
    }
}

async function plannerFinish() {
    closeModal('planner-modal');
    var id = _planner.target.id;
    if (typeof currentBoardId !== 'undefined' && currentBoardId === id) {
        await loadBoard(id);
    } else {
        window.location.hash = id;
    }
}

// ── Export current board to a Planner-layout .xlsx ──
function plannerExportXlsx() {
    if (typeof boardItems === 'undefined' || !boardItems.length) {
        EOS_UI.toast('Nothing to export.', false);
        return;
    }
    try {
        var wb = EOS_PLANNER.buildWorkbook(boardItems, boardConfig || {});
        var name = ((boardConfig && boardConfig.name) || 'board').replace(/[^A-Za-z0-9 _-]+/g, '') || 'board';
        XLSX.writeFile(wb, name + '.xlsx');
    } catch (e) {
        console.error('plannerExportXlsx', e);
        EOS_UI.toast('Export failed', false);
    }
}
