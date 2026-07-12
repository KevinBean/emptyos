// Node driver for tests/test_planner_roundtrip.py — parses the fixture with
// the SAME planner-map.js + planner-xlsx.js the browser runs, prints JSON:
//   {records, headerIndex, mappedFields, idempotence: {new, updated, unchanged}}
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const BOARDS_PAGES = path.join(__dirname, '../../apps/public/standard/boards/pages');
const XLSX = require(path.join(BOARDS_PAGES, 'vendor/xlsx.mini.min.js'));

// planner-map.js is `window.EOS_PLANNER_MAP = {...};` — run it with a window shim.
const ctx = { window: {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(BOARDS_PAGES, 'planner-map.js'), 'utf-8'), ctx);
const MAP = ctx.window.EOS_PLANNER_MAP;

const P = require(path.join(BOARDS_PAGES, 'planner-xlsx.js'));

// Mini build has no Node fs wiring — read the buffer ourselves.
const buf = fs.readFileSync(process.argv[2] || path.join(__dirname, 'planner-sample.xlsx'));
const wb = XLSX.read(buf, { type: 'buffer', cellDates: true });
const ws = wb.Sheets[wb.SheetNames[0]];
const rows = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: '' });

const head = P.detectHeaderRow(rows, MAP);
const mapping = P.guessMapping(head.headers, MAP);
const records = P.toCanonicalRecords(rows, head.index, mapping, MAP);

// Idempotence at the pure layer: records → export rows → re-parse → diff
// against the originals must yield 0 new / 0 updated.
const items = records.map(r => Object.assign({ file: (r.name || 'x') + '.md' }, r));
const rows2 = P.buildRows(items, { name: 'Roundtrip' }, MAP);
const head2 = P.detectHeaderRow(rows2, MAP);
const mapping2 = P.guessMapping(head2.headers, MAP);
const records2 = P.toCanonicalRecords(rows2, head2.index, mapping2, MAP);
const diff = P.diffAgainstItems(records2, items, MAP);

console.log(JSON.stringify({
    headerIndex: head.index,
    mappedFields: Object.keys(mapping).sort(),
    records: records,
    idempotence: { new: diff.new.length, updated: diff.updated.length, unchanged: diff.unchanged.length },
}, null, 2));
