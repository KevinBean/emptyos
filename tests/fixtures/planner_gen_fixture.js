// Regenerates planner-sample.xlsx — a Planner-like "Export plan to Excel"
// fixture (metadata rows above the header row, semicolon-joined lists).
// Run: node tests/fixtures/planner_gen_fixture.js
const path = require('path');
const XLSX = require(path.join(__dirname, '../../apps/public/standard/boards/pages/vendor/xlsx.mini.min.js'));

const rows = [
    ['Plan name', 'Website Relaunch'],
    ['Plan ID', 'AAMkAGI2TG93AAA='],
    ['Exported', '7/1/2026 10:30 AM'],
    [],
    ['Task ID', 'Task Name', 'Bucket Name', 'Progress', 'Priority', 'Assigned To',
     'Created By', 'Created Date', 'Start Date', 'Due Date', 'Late',
     'Completed Date', 'Completed By', 'Completed Checklist Items', 'Checklist Items',
     'Labels', 'Description'],
    ['t-001', 'Draft homepage copy', 'Content', 'In progress', 'Important',
     'Alice Smith;Bob Jones', 'Alice Smith', '6/1/2026', '6/10/2026', '7/10/2026', 'false',
     '', '', 'Outline sections', 'Outline sections;Write hero text;Review with team',
     'Copy;Q3', 'First pass of the homepage copy, tone: confident.'],
    ['t-002', 'Set up staging server', 'Infrastructure', 'Completed', 'Urgent',
     'Carol White', 'Bob Jones', '6/2/2026', '6/3/2026', '6/20/2026', 'false',
     '6/18/2026', 'Carol White', 'Provision VM;Install nginx', 'Provision VM;Install nginx',
     'Infra', 'Staging mirrors production config.'],
    ['t-003', 'Design system audit', 'Design', 'Not started', 'Medium',
     '', 'Alice Smith', '6/5/2026', '', '8/1/2026', 'false',
     '', '', '', '', '', ''],
];

const ws = XLSX.utils.aoa_to_sheet(rows);
const wb = XLSX.utils.book_new();
XLSX.utils.book_append_sheet(wb, ws, 'Tasks');
// The mini build has no Node fs wiring — write the base64 output ourselves.
const b64 = XLSX.write(wb, { type: 'base64', bookType: 'xlsx' });
require('fs').writeFileSync(path.join(__dirname, 'planner-sample.xlsx'), Buffer.from(b64, 'base64'));
console.log('wrote planner-sample.xlsx');
