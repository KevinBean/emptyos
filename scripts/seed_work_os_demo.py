"""Seed a realistic Work OS demo — 5 engineers, 7 EPC projects, ~20 deliverables
with designer/checker/approver assignments, skills, due dates, and dependencies.

Produces a dataset the team lead can actually steer: filter the board by
project, open the timeline view for the Gantt chart, try to move a drawing
from IFR→IFA without a checker sign-off (guard blocks it), reassign an
overloaded engineer, watch dependency arrows redraw.

Usage:
    python scripts/seed_work_os_demo.py                # seed everything
    python scripts/seed_work_os_demo.py --clear        # wipe demo data first
    python scripts/seed_work_os_demo.py --base URL     # custom daemon URL

Assumes the daemon is running on http://127.0.0.1:9000.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

import httpx


# ────────────────────────────────────────────────────────────
# Scenario data
# ────────────────────────────────────────────────────────────

PEOPLE = [
    {"id": "alice", "name": "Alice Chen", "role": "Senior Design Engineer",
     "type": "internal", "capacity_hours_per_week": 40,
     "skills": ["HV_design", "protection", "cable_sizing", "earthing"]},
    {"id": "bob", "name": "Bob Rivera", "role": "Design Engineer",
     "type": "internal", "capacity_hours_per_week": 40,
     "skills": ["LV_design", "protection", "earthing", "MCC_design"]},
    {"id": "carol", "name": "Carol Singh", "role": "Checking Engineer",
     "type": "internal", "capacity_hours_per_week": 30,
     "skills": ["protection", "cable_sizing", "arc_flash", "HV_design"]},
    {"id": "david", "name": "David Park", "role": "Junior Engineer",
     "type": "internal", "capacity_hours_per_week": 40,
     "skills": ["LV_design", "cable_sizing", "drafting"]},
    {"id": "eva", "name": "Eva Mueller", "role": "Team Lead (Approver)",
     "type": "internal", "capacity_hours_per_week": 20,
     "skills": ["HV_design", "project_management", "arc_flash"]},
]

PROJECTS = [
    {"id": "kingsford-33kv", "name": "Kingsford Substation 33/11kV",
     "status": "active", "type": "engineering",
     "description": "Greenfield substation for the Kingsford depot — 33/11kV, 2×10 MVA transformers.",
     "deadline_weeks": 10, "assignees": ["alice", "bob", "eva"]},
    {"id": "riverside-solar", "name": "Riverside Solar Farm 50MW Connection",
     "status": "active", "type": "engineering",
     "description": "Grid-connection package for a 50MW solar farm — inverter yard + 33kV collection.",
     "deadline_weeks": 14, "assignees": ["alice", "david", "eva"]},
    {"id": "harbour-mcc-upgrade", "name": "Harbour Terminal MCC Upgrade",
     "status": "active", "type": "engineering",
     "description": "Replace legacy MCC with modern IEC-61439 assembly; minimal shutdown window.",
     "deadline_weeks": 5, "assignees": ["bob", "carol", "eva"]},
    {"id": "westfield-dc", "name": "Westfield Distribution Centre Switchgear",
     "status": "active", "type": "engineering",
     "description": "LV main switchboard + MCC for a 40,000 m² distribution centre.",
     "deadline_weeks": 7, "assignees": ["bob", "david", "eva"]},
    {"id": "northgate-genset", "name": "Northgate Campus Backup Genset",
     "status": "blocked", "type": "engineering",
     "description": "2×1MW diesel backup with automatic transfer — waiting on vendor data.",
     "deadline_weeks": 9, "assignees": ["david", "carol"]},
    {"id": "coastal-wind-230kv", "name": "Coastal Wind Farm 230kV Connection",
     "status": "active", "type": "engineering",
     "description": "Grid connection for a 200MW offshore wind farm. Client-facing, high scrutiny.",
     "deadline_weeks": 18, "assignees": ["alice", "carol", "eva"]},
    {"id": "central-hospital", "name": "Central Hospital Emergency System",
     "status": "active", "type": "engineering",
     "description": "Essential-supply switchgear for critical-care wing. Compliance-heavy.",
     "deadline_weeks": 4, "assignees": ["bob", "carol", "eva"]},
]

# Deliverables board. Each row is a drawing/document/calculation with a real
# engineering task flow. Dependencies within a project: SLD → Cable Schedule →
# Protection Settings → Arc-Flash Study; Layout → GA Drawings → Earthing.
# Dependencies are expressed as `blocks: [item-id]` — the arrow goes from the
# prerequisite to the thing it unlocks.
#
# Everything is keyed by the slug generated from `name` (lowercase, hyphens).
def _slug(name: str) -> str:
    s = name.lower().replace(" ", "-")
    return "".join(c for c in s if c.isalnum() or c == "-") + ".md"


# Helper: build deliverables for one project — 3 canonical drawings per project,
# with the standard SLD → Cable Schedule → Protection flow, plus one extra
# document per project so we get interesting arrow patterns.
def _deliverables_for_project(project_id: str, project_name: str,
                              designer: str, checker: str, approver: str,
                              start_weeks: int, duration_weeks: int) -> list[dict]:
    """Three deliverables per project with real start→end spans and dependencies.

    Each deliverable is scheduled as a work block:
      SLD              starts at kickoff, lasts ~30% of project
      Cable Schedule   starts when SLD finishes, lasts ~30% of project
      Protection Set.  starts when Cable Schedule finishes, lasts ~30% of project
    That produces visibly-sized Gantt bars whose ends feed naturally into the
    next item's start — what a real project timeline looks like.
    """
    short = project_name.split()[0]
    today = date.today()
    w = 7
    kickoff_day   = start_weeks * w
    sld_start     = today + timedelta(days=kickoff_day)
    sld_end       = today + timedelta(days=kickoff_day + int(duration_weeks * 0.3 * w))
    cable_start   = sld_end
    cable_end     = today + timedelta(days=kickoff_day + int(duration_weeks * 0.6 * w))
    prot_start    = cable_end
    prot_end      = today + timedelta(days=kickoff_day + int(duration_weeks * 0.9 * w))

    sld_name = f"{short} SLD"
    cable_name = f"{short} Cable Schedule"
    prot_name = f"{short} Protection Settings"
    sld_slug = _slug(sld_name)
    cable_slug = _slug(cable_name)
    prot_slug = _slug(prot_name)
    return [
        {
            "name": sld_name, "project": project_id, "drawing_no": f"{short[:3].upper()}-E-001",
            "rev": "A", "status": "IFR",
            "designer": designer, "checker": checker, "approver": approver,
            "checker_signoff": False, "approver_signoff": False,
            "skills_required": ["HV_design"] if "kv" in project_id or "wind" in project_id else ["LV_design"],
            "start_date": sld_start.isoformat(),
            "due": sld_end.isoformat(),
            "blocks": [cable_slug, prot_slug],
        },
        {
            "name": cable_name, "project": project_id, "drawing_no": f"{short[:3].upper()}-E-002",
            "rev": "A", "status": "draft",
            "designer": designer, "checker": checker, "approver": approver,
            "checker_signoff": False, "approver_signoff": False,
            "skills_required": ["cable_sizing"],
            "start_date": cable_start.isoformat(),
            "due": cable_end.isoformat(),
            "blocks": [prot_slug],
            "blocked_by": [sld_slug],
        },
        {
            "name": prot_name, "project": project_id, "drawing_no": f"{short[:3].upper()}-E-003",
            "rev": "A", "status": "draft",
            "designer": designer, "checker": checker, "approver": approver,
            "checker_signoff": False, "approver_signoff": False,
            "skills_required": ["protection"],
            "start_date": prot_start.isoformat(),
            "due": prot_end.isoformat(),
            "blocks": [],
            "blocked_by": [cable_slug],
        },
    ]


def _build_board_config() -> dict:
    """Team Deliverables board — one board, all projects visible; filter via
    the group-by selector or the project column.

    Includes the designer/checker/approver role columns (capacity-dot chips)
    plus the status workflow with guards on IFR→IFA and IFA→IFC.
    """
    project_options = [p["id"] for p in PROJECTS]
    project_colors = {p["id"]: c for p, c in zip(PROJECTS, [
        "blue", "green", "amber", "purple", "red", "emerald", "orange"
    ])}
    return {
        "id": "team-deliverables",
        "name": "Team Deliverables",
        "description": "Every deliverable across every project. Use timeline for the Gantt.",
        "source_tag": "team-deliverable",
        "tags": ["board-config"],
        "columns": [
            {"id": "name", "label": "Title", "type": "text"},
            {"id": "project", "label": "Project", "type": "select",
             "options": project_options, "color_map": project_colors},
            {"id": "drawing_no", "label": "Drawing #", "type": "text"},
            {"id": "rev", "label": "Rev", "type": "text"},
            {"id": "status", "label": "Status", "type": "select",
             "options": ["draft", "IFR", "IFA", "IFC", "superseded"],
             "color_map": {"draft": "gray", "IFR": "amber", "IFA": "blue",
                           "IFC": "green", "superseded": "red"}},
            {"id": "designer", "label": "Designer", "type": "designer", "weight_hours": 20},
            {"id": "checker", "label": "Checker", "type": "checker", "weight_hours": 4},
            {"id": "approver", "label": "Approver", "type": "approver", "weight_hours": 1},
            {"id": "checker_signoff", "label": "Chk✓", "type": "checkbox"},
            {"id": "approver_signoff", "label": "Apr✓", "type": "checkbox"},
            {"id": "skills_required", "label": "Skills", "type": "skills"},
            {"id": "start_date", "label": "Start", "type": "date"},
            {"id": "due", "label": "Due", "type": "date"},
            {"id": "blocks", "label": "Blocks", "type": "dependencies"},
        ],
        "views": [
            {"type": "timeline", "start_field": "start_date", "end_field": "due", "default": True},
            {"type": "kanban", "group_by": "status"},
            {"type": "table"},
        ],
        "kanban_group_by": "status",
        "rules": [
            {"kind": "guard", "trigger": "field_change", "field": "status",
             "from": "IFR", "to": "IFA",
             "guard": "checker_signoff == true",
             "on_block": {"toast": "Cannot issue for approval — checker sign-off required.",
                          "emit": "deliverable:blocked"}},
            {"kind": "guard", "trigger": "field_change", "field": "status",
             "from": "IFA", "to": "IFC",
             "guard": "approver_signoff == true",
             "on_block": {"toast": "Cannot issue for construction — approver sign-off required.",
                          "emit": "deliverable:blocked"}},
            {"trigger": {"event": "field_changed", "field": "due"},
             "actions": [{"type": "propagate_slip", "field": "due", "auto_slip_limit_days": 14}]},
        ],
    }


# ────────────────────────────────────────────────────────────
# Seed execution
# ────────────────────────────────────────────────────────────

def vault_path() -> Path:
    """Resolve the vault root from emptyos.toml so we can write project .md files."""
    cfg = Path(__file__).resolve().parent.parent / "emptyos.toml"
    if not cfg.exists():
        raise SystemExit(f"emptyos.toml not found at {cfg}")
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    p = (data.get("notes") or {}).get("path") or ""
    if not p:
        raise SystemExit("notes.path not set in emptyos.toml")
    return Path(p)


def seed_people(client: httpx.Client, base: str):
    print("\n▸ Seeding 5 people")
    for p in PEOPLE:
        r = client.post(f"{base}/people/api/people", json=p)
        try:
            res = r.json()
        except Exception:
            res = {"error": r.text[:80]}
        if res.get("ok"):
            print(f"  ✓ {p['name']:<22} ({p['role']})")
        elif "already exists" in (res.get("error") or ""):
            # Update instead.
            client.patch(f"{base}/people/api/people/{p['id']}", json={
                "name": p["name"], "role": p["role"], "type": p["type"],
                "capacity_hours_per_week": p["capacity_hours_per_week"],
                "skills": p["skills"], "active": True,
            })
            print(f"  ↻ {p['name']:<22} (updated existing)")
        else:
            print(f"  ✗ {p['name']}: {res}")


def seed_projects(vault: Path):
    print("\n▸ Seeding 7 projects as vault notes")
    proj_root = vault / "10_Projects"
    proj_root.mkdir(parents=True, exist_ok=True)
    today = date.today()
    for p in PROJECTS:
        dl = (today + timedelta(weeks=p["deadline_weeks"])).isoformat()
        pdir = proj_root / p["id"]
        pdir.mkdir(exist_ok=True)
        main = pdir / f"{p['id']}.md"
        fm_lines = [
            "---",
            f"status: {p['status']}",
            f"type: {p['type']}",
            f"deadline: {dl}",
            f"created: {today.isoformat()}",
            f"progress: 0",
            "tags: [project]",
            "assignees: [" + ", ".join(p["assignees"]) + "]",
            "---",
            "",
            f"# {p['name']}",
            "",
            f"{p['description']}",
            "",
            "## Goal",
            "Deliver engineering design package ready for procurement.",
            "",
            "## Tasks",
            "- [ ] Kickoff with client",
            "- [ ] Complete deliverables (see Team Deliverables board)",
            "- [ ] IFC sign-off",
        ]
        main.write_text("\n".join(fm_lines), encoding="utf-8")
        print(f"  ✓ {p['name']:<40} (due {dl})")


def seed_board(client: httpx.Client, base: str) -> str:
    print("\n▸ Creating Team Deliverables board")
    cfg = _build_board_config()
    # Boards' POST /api/boards handles custom configs (non-preset).
    r = client.post(f"{base}/boards/api/boards", json=cfg)
    res = r.json() if r.is_success else {"error": r.text}
    if res.get("ok"):
        print(f"  ✓ {cfg['name']}")
    else:
        # Attempt PATCH if it already exists.
        client.patch(f"{base}/boards/api/boards/{cfg['id']}", json=cfg)
        print(f"  ↻ {cfg['name']} (updated existing)")
    return cfg["id"]


def seed_deliverables(client: httpx.Client, base: str, board_id: str):
    """20 deliverables across 7 projects, designer/checker/approver assigned."""
    print("\n▸ Seeding deliverables with dependencies")

    # Project-to-team map: designer, checker, approver.
    assign = {
        "kingsford-33kv":      ("alice", "carol", "eva"),
        "riverside-solar":     ("alice", "carol", "eva"),
        "harbour-mcc-upgrade": ("bob", "carol", "eva"),
        "westfield-dc":        ("bob", "carol", "eva"),
        "northgate-genset":    ("david", "carol", "eva"),
        "coastal-wind-230kv":  ("alice", "carol", "eva"),
        "central-hospital":    ("bob", "carol", "eva"),
    }

    # Stagger project starts so the Gantt looks varied.
    schedule = {
        "kingsford-33kv":      (0, 10),
        "riverside-solar":     (2, 12),
        "harbour-mcc-upgrade": (0, 5),
        "westfield-dc":        (1, 6),
        "northgate-genset":    (3, 6),
        "coastal-wind-230kv":  (4, 14),
        "central-hospital":    (0, 4),
    }

    created = 0
    for proj in PROJECTS:
        d, c, a = assign[proj["id"]]
        start, dur = schedule[proj["id"]]
        dvs = _deliverables_for_project(proj["id"], proj["name"], d, c, a, start, dur)
        for dv in dvs:
            r = client.post(f"{base}/boards/api/boards/{board_id}/items", json=dv)
            if r.is_success and r.json().get("ok"):
                created += 1
            else:
                print(f"  ✗ {dv['name']}: {r.text[:100]}")
    print(f"  ✓ {created} deliverables created across {len(PROJECTS)} projects")

    # Flip a handful of deliverables forward in the workflow so the demo has
    # progress to show: 2 items at IFA (checker signed off), 1 at IFC.
    print("\n▸ Advancing a few items through the workflow")
    advanced = 0
    samples = [
        # (file, updates) — tick checker signoff then move to IFA.
        ("kingsford-sld.md", {"checker_signoff": True}),
        ("harbour-sld.md", {"checker_signoff": True}),
        ("central-sld.md", {"checker_signoff": True, "approver_signoff": True}),
    ]
    for fname, updates in samples:
        r = client.patch(f"{base}/boards/api/boards/{board_id}/items/{fname}",
                         json={"updates": updates})
        if r.is_success:
            advanced += 1
    # Now transition them.
    transitions = [
        ("kingsford-sld.md", "IFA"),
        ("harbour-sld.md", "IFA"),
        ("central-sld.md", "IFC"),
    ]
    for fname, new_status in transitions:
        r = client.patch(f"{base}/boards/api/boards/{board_id}/items/{fname}",
                         json={"updates": {"status": new_status}})
        if r.is_success and not r.json().get("error"):
            advanced += 1
    print(f"  ✓ {advanced} workflow steps applied")


def clear_demo(client: httpx.Client, base: str, vault: Path):
    print("\n▸ Clearing demo data")
    # People
    for p in PEOPLE:
        client.delete(f"{base}/people/api/people/{p['id']}")
    # Projects — remove the directories we created.
    for p in PROJECTS:
        pdir = vault / "10_Projects" / p["id"]
        if pdir.exists():
            import shutil
            shutil.rmtree(pdir)
    # Board config (items remain in vault; boards.source_tag filter just stops showing them).
    client.delete(f"{base}/boards/api/boards/team-deliverables")
    print("  ✓ demo data cleared (deliverable vault notes preserved by design)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:9000",
                        help="Daemon base URL")
    parser.add_argument("--clear", action="store_true",
                        help="Wipe demo data before seeding")
    args = parser.parse_args()

    vault = vault_path()
    print(f"Vault: {vault}")
    print(f"Daemon: {args.base}")

    with httpx.Client(timeout=15) as client:
        # Health probe.
        try:
            client.get(f"{args.base}/api/health", timeout=3)
        except Exception as e:
            print(f"❌ daemon not reachable at {args.base}: {e}")
            sys.exit(1)

        if args.clear:
            clear_demo(client, args.base, vault)

        seed_people(client, args.base)
        seed_projects(vault)
        # Give projects a beat to appear in the index.
        client.post(f"{args.base}/projects/api/refresh")
        board_id = seed_board(client, args.base)
        seed_deliverables(client, args.base, board_id)
        # Rebuild the people workload index so the roster reflects every
        # assignment declared by projects.list_assignments() + board items.
        r = client.post(f"{args.base}/people/api/rebuild")
        if r.is_success:
            print(f"\n▸ People workload index rebuilt ({r.json().get('assignments')} assignments indexed)")

    print("\n" + "═" * 60)
    print("✓ Demo seeded. Entry points:")
    print("  • http://localhost:9000/people/                      — roster + capacity")
    print("  • http://localhost:9000/boards/#team-deliverables    — Gantt timeline (default view)")
    print("  • http://localhost:9000/boards/#team-deliverables    — switch to Kanban/Table via tabs")
    print("  • http://localhost:9000/projects/                    — project list with assignees")
    print("  • http://localhost:9000/                             — hub (overload panel fires if anyone > 100%)")
    print()
    print("Try it:")
    print("  1. Open the timeline view → dependency arrows appear between drawings.")
    print("  2. Click a drawing → slide-out panel. Try to flip status IFR→IFA without")
    print("     ticking Chk✓ → guard blocks the write.")
    print("  3. Open /people/ → see who's overloaded, click through to their assignments.")
    print("═" * 60)


if __name__ == "__main__":
    main()
