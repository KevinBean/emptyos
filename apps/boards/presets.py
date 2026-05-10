"""Board Presets — built-in board templates for common use cases."""

PRESETS = {
    "project-tracker": {
        "id": "project-tracker",
        "name": "Projects",
        "description": "A live view over 10_Projects/. Edits write through to the projects app — this is not a parallel store.",
        # View-layer source — projects is the system of record.
        "source": {"type": "app", "app": "projects", "method": "list_all"},
        "tags": ["board-config"],
        "columns": [
            {"id": "name", "label": "Project", "type": "text"},
            {
                "id": "status",
                "label": "Status",
                "type": "select",
                "options": ["idea", "active", "blocked", "shelved", "completed"],
                "color_map": {
                    "idea": "gray",
                    "active": "green",
                    "blocked": "red",
                    "shelved": "amber",
                    "completed": "purple",
                },
            },
            {"id": "stage", "label": "Stage", "type": "text"},
            {"id": "type", "label": "Type", "type": "text"},
            {"id": "deadline", "label": "Deadline", "type": "date"},
            {"id": "progress", "label": "Progress", "type": "number", "suffix": "%"},
            # Deliverables roll-up — inverse of engineering-deliverables.parent_project.
            {
                "id": "deliverables",
                "label": "Deliverables",
                "type": "link-record",
                "target_board": "engineering-deliverables",
                "multi": True,
                "inverse": "parent_project",
            },
            {
                "id": "deliverable_count",
                "label": "#",
                "type": "formula",
                "expression": "COUNT(deliverables)",
            },
            {
                "id": "total_est_hours",
                "label": "Hrs",
                "type": "formula",
                "expression": "SUM(deliverables.est_hours)",
                "suffix": "h",
            },
            {
                "id": "avg_deliverable_progress",
                "label": "Delivery %",
                "type": "formula",
                "expression": "ROUND(AVG(deliverables.progress) * 100, 0)",
                "suffix": "%",
            },
            {
                "id": "at_risk",
                "label": "At risk",
                "type": "formula",
                "expression": 'IF(AVG(deliverables.progress) < 0.5 AND COUNT(deliverables) > 0, "⚠", "")',
            },
        ],
        "views": [
            {"type": "table", "default": True},
            {"type": "kanban", "group_by": "status"},
            {"type": "timeline", "start_field": "created", "end_field": "deadline"},
        ],
        "kanban_group_by": "status",
    },
    "crm-pipeline": {
        "id": "crm-pipeline",
        "name": "CRM Pipeline",
        "description": "Manage sales leads from first contact to close",
        "source_tag": "lead",
        "tags": ["board-config"],
        "columns": [
            {"id": "name", "label": "Lead", "type": "text"},
            {
                "id": "status",
                "label": "Stage",
                "type": "select",
                "options": ["New", "Contacted", "Proposal", "Won", "Lost"],
                "color_map": {
                    "New": "blue",
                    "Contacted": "amber",
                    "Proposal": "purple",
                    "Won": "green",
                    "Lost": "red",
                },
            },
            {"id": "value", "label": "Deal Value", "type": "number", "prefix": "$"},
            {"id": "contact_date", "label": "Last Contact", "type": "date"},
            {"id": "assignee", "label": "Owner", "type": "text"},
            {
                "id": "priority",
                "label": "Priority",
                "type": "select",
                "options": ["Low", "Medium", "High", "Critical"],
                "color_map": {"Low": "gray", "Medium": "blue", "High": "amber", "Critical": "red"},
            },
        ],
        "views": [
            {"type": "table", "default": True},
            {"type": "kanban", "group_by": "status"},
            {"type": "chart", "group_by": "status", "agg_field": "value", "agg_fn": "sum"},
        ],
        "kanban_group_by": "status",
    },
    "bug-tracker": {
        "id": "bug-tracker",
        "name": "Bug Tracker",
        "description": "Track bugs with severity, status, and assignments",
        "source_tag": "bug",
        "tags": ["board-config"],
        "columns": [
            {"id": "title", "label": "Bug", "type": "text"},
            {
                "id": "severity",
                "label": "Severity",
                "type": "select",
                "options": ["Low", "Medium", "High", "Critical"],
                "color_map": {
                    "Low": "gray",
                    "Medium": "amber",
                    "High": "orange",
                    "Critical": "red",
                },
            },
            {
                "id": "status",
                "label": "Status",
                "type": "select",
                "options": ["Open", "In Progress", "Fixed", "Closed", "Won't Fix"],
                "color_map": {
                    "Open": "red",
                    "In Progress": "amber",
                    "Fixed": "green",
                    "Closed": "gray",
                    "Won't Fix": "gray",
                },
            },
            {"id": "assignee", "label": "Assignee", "type": "text"},
            {"id": "reported_date", "label": "Reported", "type": "date"},
        ],
        "views": [
            {"type": "table", "default": True},
            {"type": "kanban", "group_by": "status"},
        ],
        "kanban_group_by": "status",
    },
    "content-calendar": {
        "id": "content-calendar",
        "name": "Content Calendar",
        "description": "Plan and schedule content across channels",
        "source_tag": "content",
        "tags": ["board-config"],
        "columns": [
            {"id": "title", "label": "Title", "type": "text"},
            {
                "id": "status",
                "label": "Status",
                "type": "select",
                "options": ["Idea", "Drafting", "Review", "Scheduled", "Published"],
                "color_map": {
                    "Idea": "blue",
                    "Drafting": "amber",
                    "Review": "purple",
                    "Scheduled": "emerald",
                    "Published": "green",
                },
            },
            {"id": "publish_date", "label": "Publish Date", "type": "date"},
            {"id": "author", "label": "Author", "type": "text"},
            {
                "id": "channel",
                "label": "Channel",
                "type": "select",
                "options": ["Blog", "Social", "Newsletter", "Video"],
            },
        ],
        "views": [
            {"type": "table", "default": True},
            {"type": "kanban", "group_by": "status"},
            {"type": "calendar", "date_field": "publish_date"},
        ],
        "kanban_group_by": "status",
    },
    "sprint-board": {
        "id": "sprint-board",
        "name": "Sprint Board",
        "description": "Agile sprint planning with story points",
        "source_tag": "sprint-item",
        "tags": ["board-config"],
        "columns": [
            {"id": "title", "label": "Story", "type": "text"},
            {
                "id": "status",
                "label": "Status",
                "type": "select",
                "options": ["Backlog", "To Do", "In Progress", "In Review", "Done"],
                "color_map": {
                    "Backlog": "gray",
                    "To Do": "blue",
                    "In Progress": "amber",
                    "In Review": "purple",
                    "Done": "green",
                },
            },
            {"id": "assignee", "label": "Assignee", "type": "text"},
            {"id": "story_points", "label": "Points", "type": "number"},
            {"id": "sprint", "label": "Sprint", "type": "text"},
        ],
        "views": [
            {"type": "table", "default": True},
            {"type": "kanban", "group_by": "status"},
        ],
        "kanban_group_by": "status",
    },
    "personal-habits": {
        "id": "personal-habits",
        "name": "Personal Habits",
        "description": "Track daily habits and streaks",
        "source_tag": "habit",
        "tags": ["board-config"],
        "columns": [
            {"id": "name", "label": "Habit", "type": "text"},
            {
                "id": "frequency",
                "label": "Frequency",
                "type": "select",
                "options": ["Daily", "Weekly", "Monthly"],
            },
            {"id": "streak", "label": "Streak", "type": "number", "suffix": " days"},
            {"id": "last_done", "label": "Last Done", "type": "date"},
            {
                "id": "category",
                "label": "Category",
                "type": "select",
                "options": ["Health", "Learning", "Creative", "Social", "Financial"],
            },
        ],
        "views": [
            {"type": "table", "default": True},
            {"type": "chart", "group_by": "category"},
        ],
        "kanban_group_by": "category",
    },
    "engineering-deliverables": {
        "id": "engineering-deliverables",
        "name": "Engineering Deliverables",
        "description": "EPC-style deliverables register — drawings with designer/checker/approver roles and IFR → IFA → IFC workflow gates.",
        "source_tag": "deliverable",
        "tags": ["board-config"],
        "columns": [
            {"id": "name", "label": "Title", "type": "text"},
            {"id": "drawing_no", "label": "Drawing #", "type": "text"},
            {"id": "rev", "label": "Rev", "type": "text"},
            {
                "id": "status",
                "label": "Status",
                "type": "select",
                "options": ["draft", "IFR", "IFA", "IFC", "superseded"],
                "color_map": {
                    "draft": "gray",
                    "IFR": "amber",
                    "IFA": "blue",
                    "IFC": "green",
                    "superseded": "red",
                },
            },
            {"id": "designer", "label": "Designer", "type": "designer", "weight_hours": 20},
            {"id": "checker", "label": "Checker", "type": "checker", "weight_hours": 4},
            {"id": "approver", "label": "Approver", "type": "approver", "weight_hours": 1},
            {"id": "checker_signoff", "label": "Chk ✓", "type": "checkbox"},
            {"id": "approver_signoff", "label": "Apr ✓", "type": "checkbox"},
            {"id": "skills_required", "label": "Skills", "type": "skills"},
            {"id": "est_hours", "label": "Est (h)", "type": "number"},
            {"id": "progress", "label": "Progress", "type": "number", "hint": "0.0–1.0"},
            {"id": "due", "label": "Due", "type": "date"},
            {"id": "blocks", "label": "Blocks", "type": "dependencies"},
            # Parent project link — enables rollups on the projects board.
            {
                "id": "parent_project",
                "label": "Project",
                "type": "link-record",
                "target_board": "project-tracker",
                "multi": False,
                "inverse": "deliverables",
            },
            # Per-item formula: overdue only counts if the drawing isn't IFC yet.
            {
                "id": "overdue",
                "label": "Late?",
                "type": "formula",
                "expression": 'IF(due < TODAY() AND status != "IFC", "⚠ late", "")',
            },
        ],
        "views": [
            {"type": "table", "default": True},
            {"type": "kanban", "group_by": "status"},
            {"type": "timeline", "start_field": "created", "end_field": "due"},
        ],
        "kanban_group_by": "status",
        # Workflow gates — enforced pre-commit by evaluate_guards().
        "rules": [
            {
                "kind": "guard",
                "trigger": "field_change",
                "field": "status",
                "from": "IFR",
                "to": "IFA",
                "guard": "checker_signoff == true",
                "on_block": {
                    "toast": "Cannot issue for approval — checker sign-off required.",
                    "emit": "deliverable:blocked",
                },
            },
            {
                "kind": "guard",
                "trigger": "field_change",
                "field": "status",
                "from": "IFA",
                "to": "IFC",
                "guard": "approver_signoff == true",
                "on_block": {
                    "toast": "Cannot issue for construction — approver sign-off required.",
                    "emit": "deliverable:blocked",
                },
            },
            # When a due date moves forward, nudge every item in `blocks`.
            {
                "trigger": {"event": "field_changed", "field": "due"},
                "actions": [
                    {"type": "propagate_slip", "field": "due", "auto_slip_limit_days": 14},
                ],
            },
        ],
    },
}


PRESETS["job-applications"] = {
    "id": "job-applications",
    "name": "Job Applications",
    "description": "A live view over the jobs app — drag a card across columns to flip status, edit match score inline. Edits write through to the vault note's frontmatter and append a Timeline entry on status change.",
    "source": {"type": "app", "app": "jobs", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "company", "label": "Company", "type": "text"},
        {"id": "role", "label": "Role", "type": "text"},
        {
            "id": "status",
            "label": "Status",
            "type": "select",
            "options": [
                "shortlisted",
                "recruiter_contact",
                "applied",
                "phone_screen",
                "interview",
                "offer",
                "accepted",
                "rejected",
                "withdrawn",
                "not_pursuing",
            ],
            "color_map": {
                "shortlisted": "blue",
                "recruiter_contact": "purple",
                "applied": "amber",
                "phone_screen": "amber",
                "interview": "orange",
                "offer": "emerald",
                "accepted": "green",
                "rejected": "red",
                "withdrawn": "gray",
                "not_pursuing": "gray",
            },
        },
        {
            "id": "priority",
            "label": "Priority",
            "type": "select",
            "options": ["", "low", "medium", "high"],
            "color_map": {"low": "gray", "medium": "blue", "high": "red"},
        },
        {"id": "match_score", "label": "Match", "type": "number", "suffix": "%"},
        {"id": "salary", "label": "Salary", "type": "text"},
        {"id": "location", "label": "Location", "type": "text"},
        {"id": "source", "label": "Source", "type": "text"},
        {"id": "created", "label": "Created", "type": "date"},
        {"id": "updated", "label": "Updated", "type": "date"},
    ],
    "views": [
        {"type": "kanban", "group_by": "status", "default": True},
        {"type": "table"},
        {"type": "timeline", "start_field": "created", "end_field": "updated"},
    ],
    "kanban_group_by": "status",
}


PRESETS["reminders"] = {
    "id": "reminders",
    "name": "Reminders",
    "description": "A live view over the reminders app — drag a card across columns to flip status, edit due date inline. Edits write through to the reminders store and emit reminders:completed when marked done.",
    "source": {"type": "app", "app": "reminders", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "text", "label": "Reminder", "type": "text"},
        {
            "id": "status",
            "label": "Status",
            "type": "select",
            "options": ["active", "snoozed", "completed"],
            "color_map": {"active": "amber", "snoozed": "gray", "completed": "green"},
        },
        {
            "id": "priority",
            "label": "Priority",
            "type": "select",
            "options": ["low", "normal", "high"],
            "color_map": {"low": "gray", "normal": "blue", "high": "red"},
        },
        {"id": "due", "label": "Due", "type": "date"},
        {"id": "time", "label": "Time", "type": "text"},
        {
            "id": "repeat",
            "label": "Repeat",
            "type": "select",
            "options": ["", "daily", "weekly", "monthly"],
        },
    ],
    "views": [
        {"type": "kanban", "group_by": "status", "default": True},
        {"type": "table"},
        {"type": "calendar", "date_field": "due"},
        {"type": "timeline", "start_field": "created", "end_field": "due"},
    ],
    "kanban_group_by": "status",
}


PRESETS["media-library"] = {
    "id": "media-library",
    "name": "Media Library",
    "description": "A live cover-grid view over the media app — books, movies, TV. Click a cover to open the note. Edits write through to vault frontmatter.",
    "source": {"type": "app", "app": "media", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "title", "label": "Title", "type": "text"},
        {
            "id": "type",
            "label": "Type",
            "type": "select",
            "options": ["book", "movie", "tv-series"],
            "color_map": {"book": "blue", "movie": "purple", "tv-series": "amber"},
        },
        {"id": "author", "label": "Author / Director", "type": "text"},
        {
            "id": "status",
            "label": "Status",
            "type": "select",
            "options": ["", "wishlist", "reading", "watching", "completed", "dropped"],
            "color_map": {
                "wishlist": "gray",
                "reading": "amber",
                "watching": "amber",
                "completed": "green",
                "dropped": "red",
            },
        },
        {"id": "rating", "label": "Rating", "type": "number", "suffix": "/5"},
        {"id": "year", "label": "Year", "type": "text"},
        {"id": "genre", "label": "Genre", "type": "text"},
        {"id": "cover", "label": "Cover", "type": "text"},
    ],
    "views": [
        {
            "type": "gallery",
            "default": True,
            "image_field": "cover",
            "title_field": "title",
            "subtitle_field": "author",
            "badge_field": "status",
            "meta_fields": ["year", "rating"],
        },
        {"type": "kanban", "group_by": "status"},
        {"type": "table"},
    ],
    "kanban_group_by": "status",
}


PRESETS["expense-log"] = {
    "id": "expense-log",
    "name": "Expense Log",
    "description": "A live view over the current year's expenses. Edit category or description inline; date/amount changes go through the expense app's add/delete flow.",
    "source": {"type": "app", "app": "expense", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "date", "label": "Date", "type": "date"},
        {"id": "amount", "label": "Amount", "type": "number", "prefix": "$"},
        {"id": "description", "label": "Description", "type": "text"},
        {
            "id": "category",
            "label": "Category",
            "type": "select",
            "options": [
                "Dining",
                "Groceries",
                "Transport",
                "Shopping",
                "Bills",
                "Health",
                "Entertainment",
                "Recurring",
                "Other",
            ],
            "color_map": {
                "Dining": "amber",
                "Groceries": "green",
                "Transport": "blue",
                "Shopping": "purple",
                "Bills": "red",
                "Health": "emerald",
                "Entertainment": "orange",
                "Recurring": "gray",
                "Other": "gray",
            },
        },
        {"id": "source", "label": "Source", "type": "text"},
    ],
    "views": [
        {"type": "table", "default": True},
        {"type": "kanban", "group_by": "category"},
        {"type": "chart", "group_by": "category", "agg_field": "amount", "agg_fn": "sum"},
        {"type": "calendar", "date_field": "date"},
    ],
    "kanban_group_by": "category",
}


PRESETS["people-roster"] = {
    "id": "people-roster",
    "name": "People",
    "description": "A live view over the people app — roster + capacity + relationships. Edit relationship, energy, capacity inline; writes through to vault frontmatter.",
    "source": {"type": "app", "app": "people", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "name", "label": "Name", "type": "text"},
        {"id": "role", "label": "Role", "type": "text"},
        {"id": "company", "label": "Company", "type": "text"},
        {
            "id": "relationship",
            "label": "Relationship",
            "type": "select",
            "options": [
                "",
                "family",
                "friend",
                "partner",
                "colleague",
                "mentor",
                "mentee",
                "client",
                "acquaintance",
            ],
            "color_map": {
                "family": "red",
                "friend": "amber",
                "partner": "purple",
                "colleague": "blue",
                "mentor": "emerald",
                "mentee": "green",
                "client": "orange",
                "acquaintance": "gray",
            },
        },
        {
            "id": "energy",
            "label": "Energy",
            "type": "select",
            "options": ["", "gives", "neutral", "drains"],
            "color_map": {"gives": "green", "neutral": "gray", "drains": "red"},
        },
        {"id": "trust_level", "label": "Trust", "type": "number"},
        {
            "id": "band",
            "label": "Capacity",
            "type": "select",
            "options": ["ok", "busy", "overloaded"],
            "color_map": {"ok": "green", "busy": "amber", "overloaded": "red"},
        },
        {"id": "load_ratio", "label": "Load", "type": "number", "suffix": "x"},
        {"id": "capacity_hours_per_week", "label": "Hrs/wk", "type": "number"},
        {
            "id": "contact_frequency",
            "label": "Frequency",
            "type": "select",
            "options": ["", "weekly", "monthly", "quarterly", "yearly"],
        },
        {"id": "last_contact", "label": "Last contact", "type": "date"},
        {"id": "active", "label": "Active", "type": "checkbox"},
    ],
    "views": [
        {"type": "table", "default": True},
        {"type": "kanban", "group_by": "relationship"},
    ],
    "kanban_group_by": "relationship",
}


PRESETS["task-tracker"] = {
    "id": "task-tracker",
    "name": "Tasks",
    "description": "A live view over every task in the vault. Edits write through to the task app — drag a card across columns to flip done, edit a due date inline.",
    "source": {"type": "app", "app": "task", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "text", "label": "Task", "type": "text"},
        {
            "id": "done",
            "label": "Status",
            "type": "select",
            "options": ["false", "true"],
            "color_map": {"false": "amber", "true": "green"},
        },
        {"id": "project", "label": "Project", "type": "text"},
        {"id": "due", "label": "Due", "type": "date"},
        {
            "id": "tier",
            "label": "Age",
            "type": "select",
            "options": ["fresh", "aging", "stale", "zombie"],
            "color_map": {"fresh": "green", "aging": "amber", "stale": "red", "zombie": "gray"},
        },
        {"id": "focus_score", "label": "Focus", "type": "number"},
    ],
    "views": [
        {"type": "table", "default": True},
        {"type": "kanban", "group_by": "done"},
        {"type": "calendar", "date_field": "due"},
    ],
    "kanban_group_by": "done",
}


PRESETS["places"] = {
    "id": "places",
    "name": "Places",
    "description": "A live view over the places app — gallery by category, kanban by visited status. Edits write through to vault frontmatter.",
    "source": {"type": "app", "app": "places", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "name", "label": "Name", "type": "text"},
        {
            "id": "category",
            "label": "Category",
            "type": "select",
            "options": [
                "",
                "restaurant",
                "cafe",
                "bar",
                "park",
                "museum",
                "shop",
                "landmark",
                "lodging",
                "other",
            ],
            "color_map": {
                "restaurant": "amber",
                "cafe": "orange",
                "bar": "purple",
                "park": "green",
                "museum": "blue",
                "shop": "gray",
                "landmark": "emerald",
                "lodging": "red",
                "other": "gray",
            },
        },
        {"id": "rating", "label": "Rating", "type": "number", "suffix": "/5"},
        {"id": "address", "label": "Address", "type": "text"},
        {"id": "visited", "label": "Last visited", "type": "date"},
    ],
    "views": [
        {"type": "table", "default": True},
        {"type": "kanban", "group_by": "category"},
    ],
    "kanban_group_by": "category",
}


PRESETS["recipes"] = {
    "id": "recipes",
    "name": "Recipes",
    "description": "A live view over the recipes app — gallery by difficulty, kanban by favorite. Edits write through to vault frontmatter.",
    "source": {"type": "app", "app": "recipes", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "name", "label": "Recipe", "type": "text"},
        {
            "id": "difficulty",
            "label": "Difficulty",
            "type": "select",
            "options": ["easy", "medium", "hard"],
            "color_map": {"easy": "green", "medium": "amber", "hard": "red"},
        },
        {"id": "favorite", "label": "Favorite", "type": "checkbox"},
        {"id": "rating", "label": "Rating", "type": "number", "suffix": "/5"},
        {"id": "prep_min", "label": "Prep", "type": "number", "suffix": " min"},
        {"id": "cook_min", "label": "Cook", "type": "number", "suffix": " min"},
        {"id": "servings", "label": "Servings", "type": "number"},
        {"id": "times_cooked", "label": "Cooked", "type": "number", "suffix": "x"},
        {"id": "last_cooked", "label": "Last cooked", "type": "date"},
    ],
    "views": [
        {"type": "table", "default": True},
        {"type": "kanban", "group_by": "difficulty"},
    ],
    "kanban_group_by": "difficulty",
}


PRESETS["inspection-queue"] = {
    "id": "inspection-queue",
    "name": "Inspection Queue",
    "description": "A live view over the inspection-queue app — kanban by status (open / completed). Marking a card completed advances the underlying asset's last_inspected date.",
    "source": {"type": "app", "app": "inspection-queue", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "asset_id", "label": "Asset", "type": "text"},
        {
            "id": "class",
            "label": "Class",
            "type": "select",
            "options": ["pole", "span", "vegetation"],
            "color_map": {"pole": "blue", "span": "purple", "vegetation": "green"},
        },
        {
            "id": "status",
            "label": "Status",
            "type": "select",
            "options": ["open", "completed"],
            "color_map": {"open": "amber", "completed": "green"},
        },
        {"id": "priority_score", "label": "Priority", "type": "number"},
        {"id": "estimated_hours", "label": "Est (h)", "type": "number"},
        {"id": "source", "label": "Source", "type": "text"},
        {"id": "created_at", "label": "Created", "type": "date"},
    ],
    "views": [
        {"type": "kanban", "group_by": "status", "default": True},
        {"type": "table"},
    ],
    "kanban_group_by": "status",
}


PRESETS["asset-register"] = {
    "id": "asset-register",
    "name": "Asset Register",
    "description": "A live view over the asset-register app — kanban by class, sorted by condition score. Inline-edit condition or next inspection date.",
    "source": {"type": "app", "app": "asset-register", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "asset_id", "label": "Asset", "type": "text"},
        {
            "id": "class",
            "label": "Class",
            "type": "select",
            "options": ["pole", "span", "vegetation"],
            "color_map": {"pole": "blue", "span": "purple", "vegetation": "green"},
        },
        {"id": "condition_score", "label": "Condition", "type": "number", "suffix": "/100"},
        {"id": "install_year", "label": "Installed", "type": "number"},
        {"id": "age_years", "label": "Age", "type": "number", "suffix": " y"},
        {"id": "last_inspected", "label": "Last inspected", "type": "date"},
        {"id": "next_due", "label": "Next due", "type": "date"},
        {"id": "source", "label": "Source", "type": "text"},
    ],
    "views": [
        {"type": "table", "default": True},
        {"type": "kanban", "group_by": "class"},
    ],
    "kanban_group_by": "class",
}


PRESETS["cables-schedule"] = {
    "id": "cables-schedule",
    "name": "Cable schedule",
    "description": "A live view over every cable across every project — table/kanban/gallery on top of the same vault notes the cables app reads. Inline-edit basic fields like label, length, installation; ampacity and load-flow results are read-only (run them from the cables app).",
    "source": {"type": "app", "app": "cables", "method": "list_all"},
    "tags": ["board-config"],
    "columns": [
        {"id": "cable_id", "label": "Cable", "type": "text"},
        {"id": "project", "label": "Project", "type": "text"},
        {"id": "label", "label": "Label", "type": "text"},
        {"id": "library_id", "label": "Library", "type": "text"},
        {
            "id": "installation",
            "label": "Install",
            "type": "select",
            "options": ["direct_buried", "in_duct", "in_air"],
            "color_map": {"direct_buried": "amber", "in_duct": "blue", "in_air": "green"},
        },
        {
            "id": "bonding",
            "label": "Bonding",
            "type": "select",
            "options": ["single_point", "cross_bonded", "solidly_bonded"],
        },
        {"id": "length_m", "label": "Length", "type": "number", "suffix": " m"},
        {"id": "burial_depth_m", "label": "Depth", "type": "number", "suffix": " m"},
        {"id": "grouped_cables", "label": "Grouped", "type": "number"},
        {"id": "ampacity_a", "label": "Ampacity", "type": "number", "suffix": " A"},
        {"id": "ampacity_method", "label": "Method", "type": "text"},
        {"id": "ampacity_at", "label": "Last run", "type": "text"},
        {"id": "updated", "label": "Updated", "type": "date"},
    ],
    "views": [
        {"type": "table", "default": True},
        {"type": "kanban", "group_by": "installation"},
        {"type": "kanban", "group_by": "project"},
    ],
    "kanban_group_by": "installation",
}


def list_presets() -> list[dict]:
    """Return a summary list of all presets."""
    out = []
    for p in PRESETS.values():
        src = p.get("source") or {"type": "vault_tag", "tag": p.get("source_tag", "")}
        out.append(
            {
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "source_tag": p.get("source_tag", ""),
                "source": src,
                "columns": len(p["columns"]),
                "views": len(p["views"]),
            }
        )
    return out


def get_preset(preset_id: str) -> dict | None:
    """Get a full preset config by ID."""
    return PRESETS.get(preset_id)
