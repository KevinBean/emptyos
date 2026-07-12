// Planner ⇄ boards mapping data. SINGLE SOURCE OF TRUTH for the Microsoft
// Planner "Export plan to Excel" column layout — consumed by planner-xlsx.js
// (browser, live + export modes) and parseable from Python by stripping the
// one-line wrapper below and json.loads-ing the body (pinned by
// tests/test_planner_roundtrip.py — do not reformat the wrapper).
//
// "aliases" are lowercase header candidates for auto-guessing the mapping;
// non-English tenants export localized headers, which the import wizard's
// manual mapping step covers — aliases only need to catch the common case.
// "header" is the canonical en-US header written on export.
window.EOS_PLANNER_MAP = {
  "version": 1,
  "delimiter": ";",
  "required": ["task_name"],
  "progress_values": ["Not started", "In progress", "Completed"],
  "priority_values": ["Urgent", "Important", "Medium", "Low"],
  "fields": {
    "planner_id": {
      "header": "Task ID",
      "aliases": ["task id", "taskid", "id"],
      "col": {"id": "planner_id", "label": "Planner ID", "type": "text"},
      "hidden": true
    },
    "task_name": {
      "header": "Task Name",
      "aliases": ["task name", "name", "title"],
      "col": {"id": "name", "label": "Name", "type": "text"}
    },
    "bucket": {
      "header": "Bucket Name",
      "aliases": ["bucket name", "bucket"],
      "col": {"id": "bucket", "label": "Bucket", "type": "select"}
    },
    "progress": {
      "header": "Progress",
      "aliases": ["progress"],
      "col": {"id": "status", "label": "Progress", "type": "select",
              "options": ["Not started", "In progress", "Completed"],
              "color_map": {"Not started": "gray", "In progress": "blue", "Completed": "green"}}
    },
    "priority": {
      "header": "Priority",
      "aliases": ["priority"],
      "col": {"id": "priority", "label": "Priority", "type": "select",
              "options": ["Urgent", "Important", "Medium", "Low"],
              "color_map": {"Urgent": "red", "Important": "orange", "Medium": "blue", "Low": "gray"}}
    },
    "assigned": {
      "header": "Assigned To",
      "aliases": ["assigned to", "assigned"],
      "col": {"id": "assigned", "label": "Assigned to", "type": "multi-person"},
      "list": true
    },
    "created_by": {
      "header": "Created By",
      "aliases": ["created by"],
      "col": {"id": "created_by", "label": "Created by", "type": "text"},
      "optional": true
    },
    "created_date": {
      "header": "Created Date",
      "aliases": ["created date"],
      "col": {"id": "created", "label": "Created", "type": "date"},
      "date": true, "optional": true
    },
    "start_date": {
      "header": "Start Date",
      "aliases": ["start date"],
      "col": {"id": "start_date", "label": "Start", "type": "date"},
      "date": true
    },
    "due_date": {
      "header": "Due Date",
      "aliases": ["due date", "due"],
      "col": {"id": "due_date", "label": "Due", "type": "date"},
      "date": true
    },
    "late": {
      "header": "Late",
      "aliases": ["late"],
      "col": {"id": "late", "label": "Late", "type": "text"},
      "optional": true
    },
    "completed_date": {
      "header": "Completed Date",
      "aliases": ["completed date"],
      "col": {"id": "completed_date", "label": "Completed", "type": "date"},
      "date": true, "optional": true
    },
    "completed_by": {
      "header": "Completed By",
      "aliases": ["completed by", "completed checked off by"],
      "col": {"id": "completed_by", "label": "Completed by", "type": "text"},
      "optional": true
    },
    "checklist_done": {
      "header": "Completed Checklist Items",
      "aliases": ["completed checklist items"],
      "meta": true, "list": true
    },
    "checklist": {
      "header": "Checklist Items",
      "aliases": ["checklist items", "checklist"],
      "col": {"id": "checklist", "label": "Checklist", "type": "checklist"},
      "list": true
    },
    "labels": {
      "header": "Labels",
      "aliases": ["labels", "label"],
      "col": {"id": "labels", "label": "Labels", "type": "multi-select"},
      "list": true
    },
    "description": {
      "header": "Description",
      "aliases": ["description"],
      "body": true
    }
  },
  "export_order": [
    "planner_id", "task_name", "bucket", "progress", "priority", "assigned",
    "created_by", "created_date", "start_date", "due_date", "late",
    "completed_date", "completed_by", "checklist_done", "checklist",
    "labels", "description"
  ]
};
