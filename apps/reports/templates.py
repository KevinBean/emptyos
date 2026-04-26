"""Report template registry.

A template defines:
  - name: human-readable label
  - description: one-liner for the picker UI
  - sections: ordered list of {slug, title, prompt_hint, render?, required?}
    - render="table:<name>" embeds a structured YAML table in the section body.
    - render="signoff" embeds the approval block.
  - tables: names of structured YAML tables to scaffold (requirements, risks, verification, stakeholders)
  - approvers: default approver roles

Templates are plain data — adding one means adding a dict entry. No code.
"""

from __future__ import annotations


TEMPLATES: dict[str, dict] = {
    "pdr": {
        "name": "Preliminary Design Review",
        "description": "Engineering design review at concept maturity — scope, requirements, architecture, risks, verification plan.",
        "sections": [
            {"slug": "executive-summary", "title": "Executive Summary", "prompt_hint": "One paragraph: what is being designed, why now, what this review covers."},
            {"slug": "scope", "title": "1. Scope & Objectives", "required": True,
             "prompt_hint": "Purpose of the design, audience, boundaries, assumptions, out-of-scope items."},
            {"slug": "requirements", "title": "2. Requirements", "required": True,
             "render": "table:requirements",
             "prompt_hint": "Functional and non-functional requirements. Each row: ID, description, priority, verification method."},
            {"slug": "architecture", "title": "3. System Architecture", "required": True,
             "prompt_hint": "High-level design. Include block diagrams via `![[diagram.png|caption]]`."},
            {"slug": "interfaces", "title": "4. Interfaces & Dependencies",
             "prompt_hint": "External systems, APIs, protocols, physical interfaces."},
            {"slug": "risks", "title": "5. Risk Register",
             "render": "table:risks",
             "prompt_hint": "Identified risks with likelihood, impact, mitigation."},
            {"slug": "verification", "title": "6. Verification Plan",
             "render": "table:verification",
             "prompt_hint": "How each requirement will be verified (analysis, test, inspection, demonstration)."},
            {"slug": "open-issues", "title": "7. Open Issues",
             "prompt_hint": "Unresolved questions for this review meeting."},
            {"slug": "signoff", "title": "8. Approval", "render": "signoff"},
        ],
        "tables": ["requirements", "risks", "verification"],
        "approvers": ["Lead Engineer", "Project Manager", "Client Representative"],
    },
    "cdr": {
        "name": "Critical Design Review",
        "description": "Detailed design review ahead of build — interfaces locked, test plans, trade studies, PDR action closure.",
        "sections": [
            {"slug": "executive-summary", "title": "Executive Summary",
             "prompt_hint": "Status vs PDR, key design decisions, readiness for build."},
            {"slug": "pdr-actions", "title": "1. PDR Action Item Closure",
             "prompt_hint": "Table of actions from PDR and how each was resolved."},
            {"slug": "detailed-design", "title": "2. Detailed Design", "required": True,
             "prompt_hint": "Subsystem-level design, schematics, calculations. Embed figures."},
            {"slug": "trade-studies", "title": "3. Trade Studies",
             "prompt_hint": "Alternatives considered and why the chosen option wins."},
            {"slug": "interfaces-final", "title": "4. Finalised Interfaces", "required": True,
             "prompt_hint": "ICDs, API contracts, mechanical fit. Locked at this review."},
            {"slug": "requirements-trace", "title": "5. Requirements Traceability", "required": True,
             "render": "table:verification",
             "prompt_hint": "REQ-IDs → design element → verification evidence."},
            {"slug": "risks", "title": "6. Updated Risk Register",
             "render": "table:risks"},
            {"slug": "test-plan", "title": "7. Test & Acceptance Plan",
             "prompt_hint": "How the build will be verified end-to-end."},
            {"slug": "signoff", "title": "8. Approval", "render": "signoff"},
        ],
        "tables": ["requirements", "risks", "verification"],
        "approvers": ["Lead Engineer", "Quality Assurance", "Project Manager", "Client Representative"],
    },
    "trr": {
        "name": "Test Readiness Review",
        "description": "Gate before formal testing begins — verify hardware, procedures, personnel, and environment are all ready.",
        "sections": [
            {"slug": "purpose", "title": "1. Purpose of Testing", "required": True},
            {"slug": "uut", "title": "2. Unit Under Test Configuration",
             "prompt_hint": "Exact build/config/serial numbers being tested."},
            {"slug": "procedures", "title": "3. Test Procedures",
             "prompt_hint": "Reference to procedure documents; summary of steps."},
            {"slug": "requirements-covered", "title": "4. Requirements Coverage",
             "render": "table:verification",
             "prompt_hint": "Which REQs this test campaign will close."},
            {"slug": "facility", "title": "5. Facility & Equipment Readiness"},
            {"slug": "personnel", "title": "6. Personnel & Safety"},
            {"slug": "risks", "title": "7. Residual Risks",
             "render": "table:risks"},
            {"slug": "signoff", "title": "8. Approval to Proceed", "render": "signoff"},
        ],
        "tables": ["requirements", "risks", "verification"],
        "approvers": ["Test Lead", "Safety Officer", "Project Manager"],
    },
    "proposal": {
        "name": "Technical Proposal",
        "description": "Bid or pitch for work — problem, proposed approach, deliverables, schedule, cost.",
        "sections": [
            {"slug": "executive-summary", "title": "Executive Summary", "required": True,
             "prompt_hint": "One page: the ask, our response, the value."},
            {"slug": "problem", "title": "1. Understanding of the Problem", "required": True},
            {"slug": "approach", "title": "2. Proposed Approach", "required": True,
             "prompt_hint": "Methodology, phases, deliverables."},
            {"slug": "scope", "title": "3. Scope of Work",
             "prompt_hint": "Inclusions and explicit exclusions."},
            {"slug": "schedule", "title": "4. Schedule",
             "prompt_hint": "Milestones table or Gantt."},
            {"slug": "team", "title": "5. Team & Qualifications"},
            {"slug": "pricing", "title": "6. Pricing",
             "prompt_hint": "Fee structure, assumptions, exclusions."},
            {"slug": "terms", "title": "7. Terms & Conditions"},
            {"slug": "signoff", "title": "8. Acceptance", "render": "signoff"},
        ],
        "tables": ["stakeholders"],
        "approvers": ["Client Signatory", "Proposal Lead"],
    },
    "spec": {
        "name": "Technical Specification",
        "description": "Authoritative spec document — what something must do and how it will be verified.",
        "sections": [
            {"slug": "overview", "title": "1. Overview", "required": True},
            {"slug": "definitions", "title": "2. Definitions & Abbreviations"},
            {"slug": "references", "title": "3. Reference Documents"},
            {"slug": "requirements", "title": "4. Requirements", "required": True,
             "render": "table:requirements"},
            {"slug": "interfaces", "title": "5. Interfaces"},
            {"slug": "verification", "title": "6. Verification",
             "render": "table:verification"},
            {"slug": "revision-history", "title": "7. Revision History"},
            {"slug": "signoff", "title": "8. Approval", "render": "signoff"},
        ],
        "tables": ["requirements", "verification"],
        "approvers": ["Author", "Technical Reviewer", "Approver"],
    },
    "report": {
        "name": "Generic Report",
        "description": "Open-ended professional report — executive summary, body, findings, conclusion.",
        "sections": [
            {"slug": "executive-summary", "title": "Executive Summary", "required": True},
            {"slug": "introduction", "title": "1. Introduction"},
            {"slug": "background", "title": "2. Background"},
            {"slug": "findings", "title": "3. Findings", "required": True},
            {"slug": "analysis", "title": "4. Analysis"},
            {"slug": "recommendations", "title": "5. Recommendations"},
            {"slug": "conclusion", "title": "6. Conclusion"},
            {"slug": "references", "title": "7. References"},
            {"slug": "signoff", "title": "8. Approval", "render": "signoff"},
        ],
        "tables": [],
        "approvers": ["Author", "Reviewer"],
    },
}


# --- Structured table column schemas (used for scaffolding + UI rendering) ---

TABLE_SCHEMAS: dict[str, dict] = {
    "requirements": {
        "columns": [
            {"key": "id",           "label": "ID",           "width": "short"},
            {"key": "text",         "label": "Requirement",  "width": "wide"},
            {"key": "priority",     "label": "Priority",     "width": "short", "options": ["High", "Medium", "Low"]},
            {"key": "verification", "label": "Verification", "width": "short", "options": ["Analysis", "Test", "Inspection", "Demonstration"]},
            {"key": "status",       "label": "Status",       "width": "short", "options": ["Proposed", "Approved", "Verified", "Closed"]},
        ],
        "id_prefix": "REQ",
    },
    "risks": {
        "columns": [
            {"key": "id",          "label": "ID",         "width": "short"},
            {"key": "description", "label": "Description", "width": "wide"},
            {"key": "likelihood",  "label": "Likelihood",  "width": "short", "options": ["Low", "Medium", "High"]},
            {"key": "impact",      "label": "Impact",      "width": "short", "options": ["Low", "Medium", "High"]},
            {"key": "mitigation",  "label": "Mitigation",  "width": "wide"},
            {"key": "owner",       "label": "Owner",       "width": "short"},
        ],
        "id_prefix": "RISK",
    },
    "verification": {
        "columns": [
            {"key": "req_id",  "label": "Requirement",  "width": "short"},
            {"key": "method",  "label": "Method",       "width": "short", "options": ["Analysis", "Test", "Inspection", "Demonstration"]},
            {"key": "evidence", "label": "Evidence / Ref", "width": "wide"},
            {"key": "status",  "label": "Status",       "width": "short", "options": ["Planned", "In Progress", "Passed", "Failed", "Waived"]},
        ],
        "id_prefix": "",
    },
    "stakeholders": {
        "columns": [
            {"key": "name",   "label": "Name",   "width": "short"},
            {"key": "role",   "label": "Role",   "width": "short"},
            {"key": "org",    "label": "Organisation", "width": "short"},
            {"key": "concern", "label": "Primary Concern", "width": "wide"},
        ],
        "id_prefix": "",
    },
}


def list_templates() -> list[dict]:
    """Return template metadata for the picker UI (no full section bodies)."""
    return [
        {
            "id": tid,
            "name": t["name"],
            "description": t["description"],
            "section_count": len(t["sections"]),
            "table_count": len(t.get("tables", [])),
            "approver_count": len(t.get("approvers", [])),
        }
        for tid, t in TEMPLATES.items()
    ]


def get_template(template_id: str) -> dict | None:
    """Get a full template dict by id, or None if unknown."""
    return TEMPLATES.get(template_id)


def table_schema(table_name: str) -> dict | None:
    """Get a table schema by name, or None if unknown."""
    return TABLE_SCHEMAS.get(table_name)
