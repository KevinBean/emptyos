// Planner ⇄ boards xlsx logic — PURE functions, no DOM, no fetch.
// The ONLY xlsx parse/serialize implementation for the Planner round-trip:
// used by the import wizard + export button in both live and standalone
// (export) modes, so daemon and bundle can never drift. Node-loadable for
// tests (module.exports guard at the bottom). Mapping data comes from
// planner-map.js (window.EOS_PLANNER_MAP) or an explicit `map` argument.
//
// Canonical record shape (keys = board column ids from the map, plus body):
//   {planner_id, name, bucket, status, priority, assigned: [names],
//    created, start_date, due_date, completed_date, late, created_by,
//    completed_by, checklist: [{text, done}], labels: [names], body}

(function (root) {
    'use strict';

    function _map(map) {
        return map || root.EOS_PLANNER_MAP;
    }

    function _norm(s) {
        return String(s === undefined || s === null ? '' : s).trim().toLowerCase();
    }

    // ── Date normalization → ISO yyyy-mm-dd ──
    // Cells arrive as Date objects (SheetJS cellDates:true), ISO/locale text,
    // or Excel serial numbers.
    function normDate(v) {
        if (v === undefined || v === null || v === '') return '';
        if (v instanceof Date && !isNaN(v)) {
            // Use UTC parts shifted by the local offset SheetJS applies? No —
            // SheetJS cellDates produces local-ish dates; take Y-M-D directly.
            var y = v.getFullYear(), m = v.getMonth() + 1, d = v.getDate();
            return y + '-' + (m < 10 ? '0' : '') + m + '-' + (d < 10 ? '0' : '') + d;
        }
        if (typeof v === 'number' && isFinite(v) && v > 20000 && v < 80000) {
            // Excel serial date (1900 epoch, day 25569 = 1970-01-01).
            var ms = Math.round((v - 25569) * 86400000);
            var dt = new Date(ms);
            return dt.toISOString().slice(0, 10);
        }
        var s = String(v).trim();
        if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
        // m/d/yyyy or d/m/yyyy — ambiguous; Planner en-US exports m/d/yyyy.
        var m1 = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
        if (m1) {
            var mm = parseInt(m1[1], 10), dd = parseInt(m1[2], 10), yy = m1[3];
            return yy + '-' + (mm < 10 ? '0' : '') + mm + '-' + (dd < 10 ? '0' : '') + dd;
        }
        var parsed = new Date(s);
        if (!isNaN(parsed)) return normDate(parsed);
        return s;
    }

    function _splitList(v, delim) {
        if (Array.isArray(v)) return v.map(function (x) { return String(x).trim(); }).filter(Boolean);
        return String(v === undefined || v === null ? '' : v)
            .split(delim)
            .map(function (x) { return x.trim(); })
            .filter(Boolean);
    }

    function _normChecklist(val) {
        if (Array.isArray(val)) {
            return val.map(function (it) {
                if (it && typeof it === 'object') return { text: String(it.text || ''), done: !!it.done };
                return { text: String(it), done: false };
            });
        }
        if (typeof val === 'string' && val.trim()) {
            try {
                var parsed = JSON.parse(val);
                if (Array.isArray(parsed)) return _normChecklist(parsed);
            } catch (_) {}
        }
        return [];
    }

    // ── 1. Header row detection ──
    // Planner exports carry metadata rows (plan name / id / export time) above
    // the header row. Scan the first N rows; the header row is the one with
    // the most alias hits (require >= 3 to accept).
    function detectHeaderRow(rows, map) {
        map = _map(map);
        var aliasSet = {};
        Object.keys(map.fields).forEach(function (f) {
            (map.fields[f].aliases || []).forEach(function (a) { aliasSet[_norm(a)] = true; });
        });
        var best = { index: -1, hits: 0, headers: [] };
        var scanTo = Math.min(rows.length, 10);
        for (var i = 0; i < scanTo; i++) {
            var row = rows[i] || [];
            var hits = 0;
            for (var j = 0; j < row.length; j++) {
                if (aliasSet[_norm(row[j])]) hits++;
            }
            if (hits > best.hits) {
                best = { index: i, hits: hits, headers: row.map(function (h) { return String(h === undefined || h === null ? '' : h).trim(); }) };
            }
        }
        if (best.hits < 3) return { index: -1, headers: [], hits: best.hits };
        return best;
    }

    // ── 2. Mapping auto-guess ──
    // Returns {field_id: headerIndex}. Unmatched fields are absent; the wizard
    // shows them for manual mapping. First alias match wins per field; a
    // header already claimed by one field isn't reused by another.
    function guessMapping(headers, map) {
        map = _map(map);
        var mapping = {};
        var claimed = {};
        Object.keys(map.fields).forEach(function (f) {
            var aliases = (map.fields[f].aliases || []).map(_norm);
            for (var i = 0; i < headers.length; i++) {
                if (claimed[i]) continue;
                if (aliases.indexOf(_norm(headers[i])) !== -1) {
                    mapping[f] = i;
                    claimed[i] = true;
                    break;
                }
            }
        });
        return mapping;
    }

    // ── 3. Rows → canonical records ──
    function toCanonicalRecords(rows, headerIndex, mapping, map) {
        map = _map(map);
        var delim = map.delimiter || ';';
        var out = [];
        for (var r = headerIndex + 1; r < rows.length; r++) {
            var row = rows[r] || [];
            if (!row.length || row.every(function (c) { return _norm(c) === ''; })) continue;
            var rec = { body: '' };
            var doneItems = [];
            Object.keys(mapping).forEach(function (f) {
                var spec = map.fields[f];
                if (!spec) return;
                var raw = row[mapping[f]];
                if (raw === undefined || raw === null) raw = '';
                if (f === 'checklist_done') {
                    doneItems = _splitList(raw, delim);
                    return;
                }
                if (spec.body) { rec.body = String(raw); return; }
                if (!spec.col) return;
                var v;
                if (spec.date) v = normDate(raw);
                else if (spec.list) v = _splitList(raw, delim);
                else v = String(raw).trim();
                rec[spec.col.id] = v;
            });
            // Zip checklist: done = membership in the completed-items list.
            if (Object.prototype.hasOwnProperty.call(rec, 'checklist')) {
                var doneSet = {};
                doneItems.forEach(function (t) { doneSet[_norm(t)] = true; });
                rec.checklist = (rec.checklist || []).map(function (t) {
                    return { text: String(t), done: !!doneSet[_norm(t)] };
                });
                // Completed items missing from the open list are still items.
                doneItems.forEach(function (t) {
                    var present = rec.checklist.some(function (it) { return _norm(it.text) === _norm(t); });
                    if (!present) rec.checklist.push({ text: String(t), done: true });
                });
            }
            if (!rec.name && !rec.planner_id) continue;   // ghost row
            out.push(rec);
        }
        return out;
    }

    // ── 4. Diff against current board items ──
    // Upsert key: planner_id when both sides have one, else exact name match.
    function _valForCompare(v) {
        if (v === undefined || v === null) return '';
        if (Array.isArray(v)) return JSON.stringify(v);
        return String(v);
    }

    function diffAgainstItems(records, items, map) {
        map = _map(map);
        var byPid = {}, byName = {};
        (items || []).forEach(function (it) {
            if (it.planner_id) byPid[String(it.planner_id)] = it;
            var nm = _norm(it.name);
            if (nm && !byName[nm]) byName[nm] = it;
        });
        var res = { new: [], updated: [], unchanged: [], peopleToCreate: [], newOptions: { bucket: [], labels: [] } };
        var knownPeople = {};
        var seenBuckets = {}, seenLabels = {};
        (items || []).forEach(function (it) {
            _splitList(it.assigned || [], map.delimiter || ';').forEach(function (p) { knownPeople[_norm(p)] = true; });
        });
        records.forEach(function (rec) {
            var existing = (rec.planner_id && byPid[String(rec.planner_id)]) || byName[_norm(rec.name)] || null;
            (rec.assigned || []).forEach(function (p) {
                if (!knownPeople[_norm(p)]) { knownPeople[_norm(p)] = true; res.peopleToCreate.push(p); }
            });
            if (rec.bucket && !seenBuckets[rec.bucket]) { seenBuckets[rec.bucket] = true; res.newOptions.bucket.push(rec.bucket); }
            (rec.labels || []).forEach(function (l) { if (!seenLabels[l]) { seenLabels[l] = true; res.newOptions.labels.push(l); } });
            if (!existing) { res.new.push(rec); return; }
            var changed = [];
            Object.keys(rec).forEach(function (k) {
                if (k === 'body') return;
                var a = k === 'checklist' ? JSON.stringify(_normChecklist(existing[k])) : _valForCompare(existing[k]);
                var b = k === 'checklist' ? JSON.stringify(_normChecklist(rec[k])) : _valForCompare(rec[k]);
                if (a !== b) changed.push(k);
            });
            if (changed.length) res.updated.push({ record: rec, existing: existing, changed: changed, key: existing.file || existing.id });
            else res.unchanged.push(rec);
        });
        return res;
    }

    // ── 5. Items → export rows (array-of-arrays, Planner layout) ──
    function buildRows(items, config, map) {
        map = _map(map);
        var delim = map.delimiter || ';';
        var order = map.export_order || Object.keys(map.fields);
        var headers = order.map(function (f) { return map.fields[f].header || f; });
        var meta = [
            ['Plan name', (config && config.name) || ''],
            ['Exported from', 'EmptyOS Boards'],
            [],
        ];
        var rows = meta.concat([headers]);
        (items || []).forEach(function (it) {
            var row = order.map(function (f) {
                var spec = map.fields[f];
                if (spec.body) return it.body || it.description || '';
                if (f === 'checklist_done') {
                    return _normChecklist(it.checklist).filter(function (c) { return c.done; })
                        .map(function (c) { return c.text; }).join(delim);
                }
                if (f === 'checklist') {
                    return _normChecklist(it.checklist).map(function (c) { return c.text; }).join(delim);
                }
                var v = spec.col ? it[spec.col.id] : '';
                if (v === undefined || v === null) return '';
                if (spec.list || Array.isArray(v)) return _splitList(v, delim).join(delim);
                return String(v);
            });
            rows.push(row);
        });
        return rows;
    }

    // buildWorkbook needs the XLSX global (vendored SheetJS); kept thin so
    // everything above stays testable without it.
    function buildWorkbook(items, config, map, xlsxLib) {
        var X = xlsxLib || root.XLSX;
        if (!X) throw new Error('SheetJS (XLSX) not loaded');
        var rows = buildRows(items, config, map);
        var ws = X.utils.aoa_to_sheet(rows);
        var wb = X.utils.book_new();
        X.utils.book_append_sheet(wb, ws, 'Tasks');
        return wb;
    }

    var api = {
        normDate: normDate,
        normChecklist: _normChecklist,   // single owner — detail-collab.js delegates
        detectHeaderRow: detectHeaderRow,
        guessMapping: guessMapping,
        toCanonicalRecords: toCanonicalRecords,
        diffAgainstItems: diffAgainstItems,
        buildRows: buildRows,
        buildWorkbook: buildWorkbook,
    };
    root.EOS_PLANNER = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
