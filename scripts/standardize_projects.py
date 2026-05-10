import os

"""Standardize 10_Projects/ to directory-based project structure.

Usage:
    python scripts/standardize_projects.py                  # dry-run (print plan)
    python scripts/standardize_projects.py --apply          # execute changes

Standard structure per project:
    {id}/{id}.md        main project note
    {id}/docs/          specs, meeting notes
    {id}/assets/        images, PDFs, attachments
    {id}/log/           activity logs, changelogs
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

VAULT = Path(os.environ.get("EOS_VAULT", "."))
PROJECTS = VAULT / "10_Projects"
STANDARD_SUBDIRS = ("docs", "assets", "log")
DRY_RUN = "--apply" not in sys.argv

# ── Merge rules: related flat files → single project directory ──────────

MERGES = [
    {
        "id": "visa-189",
        "main": "189 Visa.md",
        "extras": ["189-Visa-Tracker.md"],
        "status": "completed",
    },
    {
        "id": "isla-friends",
        "main": "Isla-Friends.md",
        "extras": ["Isla-Friends-PRD.md"],
        "status": "active",
    },
    {
        "id": "pte-plan-2023",
        "main": "2023-01 PTE plan - Achieve.md",
        "extras": ["2023-02 PTE plan - Achieve.md", "2023-02 PTE plan.canvas"],
        "status": "completed",
    },
    {
        "id": "house-sale-2023",
        "main": "房屋买卖 2023.md",
        "extras": ["房屋买卖 2023 - 户口迁移委托书.md"],
        "status": "completed",
    },
]

# ── Flat file → directory upgrades ──────────────────────────────────────

FLAT_UPGRADES = [
    # (original filename, new dir id, status)
    ("482 visa.md", "visa-482", "completed"),
    ("Australian 485 visa application.md", "visa-485", "completed"),
    ("Australia Visa.md", None, None),  # skip if doesn't exist
    ("Canada Visa.md", "visa-canada", "completed"),
    ("O1 Visa.md", "visa-o1", "completed"),
    ("EOI.md", "eoi", "completed"),
    ("CCL.md", "ccl", "completed"),
    ("Sydney Trip 2024-02.md", "sydney-trip-2024-02", "completed"),
    ("Sydney Trip 2024-06.md", "sydney-trip-2024-06", "completed"),
    ("relocate to Sydney.md", "relocate-to-sydney", "completed"),
    ("Vocation Program.md", "vocation-program", "completed"),
    ("Medicare.md", "medicare", "completed"),
    ("Prepare for performance review - elek.md", "performance-review-elek", "completed"),
    ("professional skills assessment.md", "skills-assessment", "completed"),
    ("本科学位认证申请.md", "degree-certification", "completed"),
    ("委托书公证（远程视频） 2023-02-17.md", "notarization-2023", "completed"),
    ("Project - Human systems - C3L.md", "human-systems-c3l", "completed"),
    (
        "Project - check chatGPT's performance in answering quizs.md",
        "chatgpt-quiz-test",
        "completed",
    ),
    ("54-Day-Safe-Projects.md", "54-day-safe-projects", "completed"),
    # Active
    ("EmptyOS.md", "emptyos", "active"),
    ("2026年2月搬家计划.md", "move-feb-2026", "active"),
    ("Australian-Citizenship.md", "australian-citizenship", "active"),
    ("Contents-Insurance.md", "contents-insurance", "active"),
    ("Home-Portal-App-Audit.md", "home-portal-audit", "completed"),
    ("IEEE-P3779-Standard.md", "ieee-p3779", "active"),
    ("Items-Manager-Photo-Feature.md", "items-photo-feature", "active"),
    ("Job-Search-Tracker.md", "job-search", "active"),
    ("Job-Transition-Plan.md", "job-transition", "active"),
    ("Joint-Paper-with-Zhang-Huan---EMI-on-Buried-Pipelines.md", "joint-paper-emi", "active"),
    ("MSI-Laptop-Server-Migration.md", "msi-server-migration", "active"),
    ("Nile-Close-Facilities-Guide.md", "nile-close-guide", "active"),
    ("Note Organization Plan.md", "note-organization", "active"),
    ("Places-Timeline-App-Idea.md", "places-timeline-idea", "active"),
    ("Rabbit-R1-Repurpose.md", "rabbit-r1", "active"),
    ("TalkBuddy-English-App.md", "talkbuddy", "active"),
    ("US-EB2-Visa-Bulletin.md", "visa-eb2", "active"),
    ("_Immigration-Timeline.md", "immigration-timeline", "active"),
    ("uConsole-Purchase-Plan.md", "uconsole", "active"),
    ("睡眠改善计划.md", "sleep-improvement", "active"),
]

# ── Directory renames (spaces/inconsistent names) ──────────────────────

DIR_RENAMES = {
    # "Job Monitor " already renamed manually (trailing space broke Python Path)
    "hv cable induced sheath voltage": "hv-cable-sheath-voltage",
}

# ── Existing directory merge (flat file + dir exist simultaneously) ────

DIR_MERGES = [
    # (flat file, existing dir, role: "main" means becomes {id}.md, "docs" means goes to docs/)
    ("AI-Phone-Agent.md", "AI-Phone-Agent", "main"),
    ("NIW.md", "NIW-Application", "docs"),
]

# ── Empty dirs to delete ───────────────────────────────────────────────

DELETE_EMPTY = ["scripts"]

# ── Existing dirs that need main note + standard subdirs ───────────────
# (dir_name, existing_main_filename_or_None, desired_status)

DIR_STANDARDIZE = [
    ("YouTube-AI-Engineering", "YouTube AI Engineering Plan.md", "active"),
    ("YouTube-Music-Channel", "YouTube Music Channel Plan.md", "active"),
    ("apartment-decoration", "Apartment-Decoration-Plan.md", "active"),
    ("cable-current-rating", None, "active"),
    ("cable-pulling-debug", None, "completed"),
    ("diamond-sutra-study", None, "active"),
    ("fiction-engine", None, "active"),
    ("job-monitor", None, "completed"),
    ("house-inspection-feb7", "README-house-inspection.md", "completed"),
    ("nem-market-dashboard", "README-nem-market-dashboard.md", "active"),
    ("test-novel", None, "shelved"),
    ("tomodachi-island", None, "shelved"),
    ("writing-engine", None, "active"),
    ("卞萱简历", None, "active"),
]

# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------

actions = []  # log of actions taken


def log(msg: str):
    prefix = "[DRY] " if DRY_RUN else "[OK]  "
    print(f"{prefix}{msg}")
    actions.append(msg)


def ensure_dir(p: Path):
    if not p.exists():
        log(f"mkdir {p.relative_to(VAULT)}")
        if not DRY_RUN:
            p.mkdir(parents=True, exist_ok=True)


def ensure_subdirs(proj_dir: Path):
    for sub in STANDARD_SUBDIRS:
        ensure_dir(proj_dir / sub)


def move_file(src: Path, dst: Path):
    if not src.exists():
        return
    log(f"move {src.relative_to(VAULT)} → {dst.relative_to(VAULT)}")
    if not DRY_RUN:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def inject_status(filepath: Path, status: str):
    """Add status to frontmatter if missing."""
    if not filepath.exists():
        return
    text = filepath.read_text(encoding="utf-8")
    if text.startswith("---"):
        fm_end = text.index("---", 3)
        fm = text[3:fm_end]
        if "status:" not in fm:
            new_fm = fm.rstrip() + f"\nstatus: {status}\n"
            text = "---" + new_fm + text[fm_end:]
            log(f"inject status: {status} → {filepath.relative_to(VAULT)}")
            if not DRY_RUN:
                filepath.write_text(text, encoding="utf-8")
    else:
        # No frontmatter at all — add one
        today = datetime.now().strftime("%Y-%m-%d")
        fm = f"---\nstatus: {status}\ncreated: {today}\ntags:\n  - project\n---\n\n"
        text = fm + text
        log(f"add frontmatter (status: {status}) → {filepath.relative_to(VAULT)}")
        if not DRY_RUN:
            filepath.write_text(text, encoding="utf-8")


def create_main_note(proj_dir: Path, project_id: str, status: str):
    """Create a minimal main note if none exists."""
    target = proj_dir / f"{project_id}.md"
    if target.exists():
        return target
    name = project_id.replace("-", " ").replace("_", " ").title()
    today = datetime.now().strftime("%Y-%m-%d")
    content = f"---\nstatus: {status}\ncreated: {today}\ntags:\n  - project\n---\n\n# {name}\n\n## Tasks\n\n## Notes\n"
    log(f"create main note {target.relative_to(VAULT)}")
    if not DRY_RUN:
        target.write_text(content, encoding="utf-8")
    return target


# ------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------


def run():
    print(f"{'DRY RUN' if DRY_RUN else 'APPLYING'} — standardizing {PROJECTS}\n")

    # 1. Merges (related flat files → single project)
    print("--- Group A: Merge related files ---")
    for m in MERGES:
        pid = m["id"]
        main_file = PROJECTS / m["main"]
        if not main_file.exists():
            print(f"  SKIP (not found): {m['main']}")
            continue
        proj_dir = PROJECTS / pid
        ensure_dir(proj_dir)
        ensure_subdirs(proj_dir)
        move_file(main_file, proj_dir / f"{pid}.md")
        for extra in m["extras"]:
            src = PROJECTS / extra
            if src.exists():
                move_file(src, proj_dir / "docs" / extra)
        inject_status(proj_dir / f"{pid}.md", m["status"])

    # 2. Flat file upgrades
    print("\n--- Group B+C: Flat file → directory ---")
    for filename, pid, status in FLAT_UPGRADES:
        if pid is None:
            continue
        src = PROJECTS / filename
        if not src.exists():
            continue
        proj_dir = PROJECTS / pid
        if proj_dir.exists():
            print(f"  SKIP (dir exists): {pid}/")
            continue
        ensure_dir(proj_dir)
        ensure_subdirs(proj_dir)
        move_file(src, proj_dir / f"{pid}.md")
        inject_status(proj_dir / f"{pid}.md", status)

    # 3. Directory renames
    print("\n--- Directory renames ---")
    for old_name, new_name in DIR_RENAMES.items():
        old = PROJECTS / old_name
        new = PROJECTS / new_name
        if old.exists() and not new.exists():
            log(f"rename dir {old_name} → {new_name}")
            if not DRY_RUN:
                old.rename(new)

    # 4. Dir merges (flat file → existing dir)
    print("\n--- Dir merges (flat → existing dir) ---")
    for flat_name, dir_name, role in DIR_MERGES:
        flat = PROJECTS / flat_name
        target_dir = PROJECTS / dir_name
        if not flat.exists():
            continue
        if not target_dir.exists():
            continue
        pid = dir_name.lower()
        if role == "main":
            move_file(flat, target_dir / f"{dir_name}.md")
        else:
            ensure_dir(target_dir / "docs")
            move_file(flat, target_dir / "docs" / flat_name)

    # 5. Delete empty dirs
    print("\n--- Delete empty dirs ---")
    for name in DELETE_EMPTY:
        d = PROJECTS / name
        if d.exists() and d.is_dir():
            contents = list(d.iterdir())
            if not contents:
                log(f"rmdir (empty) {name}/")
                if not DRY_RUN:
                    d.rmdir()
            else:
                print(f"  SKIP (not empty): {name}/ — {len(contents)} items")

    # 6. Standardize existing directories
    print("\n--- Group D: Standardize existing dirs ---")
    for dir_name, existing_main, status in DIR_STANDARDIZE:
        d = PROJECTS / dir_name
        if not d.exists():
            # Check after rename
            for old, new in DIR_RENAMES.items():
                if new == dir_name:
                    d = PROJECTS / new
            if not d.exists():
                print(f"  SKIP (not found): {dir_name}/")
                continue

        pid = d.name
        ensure_subdirs(d)

        # Rename existing main note to standard name
        main_target = d / f"{pid}.md"
        if existing_main and not main_target.exists():
            existing = d / existing_main
            if existing.exists():
                move_file(existing, main_target)

        # Create main note if still missing
        if not main_target.exists():
            create_main_note(d, pid, status)

        inject_status(main_target, status)

    # 7. Organize assets in specific dirs
    print("\n--- Organize assets ---")

    # NIW-Application: PDFs → assets/
    niw = PROJECTS / "NIW-Application"
    if niw.exists():
        ensure_subdirs(niw)
        for f in niw.glob("*.pdf"):
            move_file(f, niw / "assets" / f.name)
        for f in niw.glob("*.docx"):
            move_file(f, niw / "assets" / f.name)
        create_main_note(niw, "NIW-Application", "completed")

    # hv-cable-sheath-voltage: PDFs → assets/, paper dirs → docs/
    hv = PROJECTS / "hv-cable-sheath-voltage"
    if hv.exists():
        ensure_subdirs(hv)
        for f in hv.glob("*.pdf"):
            move_file(f, hv / "assets" / f.name)
        create_main_note(hv, "hv-cable-sheath-voltage", "active")

    # job-monitor: move internal file to docs/
    jm = PROJECTS / "job-monitor"
    if jm.exists():
        old_main = jm / "Job Monitor-Company list.md"
        if old_main.exists():
            ensure_dir(jm / "docs")
            move_file(old_main, jm / "docs" / "company-list.md")

    # 卞萱简历: images → assets/
    bx = PROJECTS / "卞萱简历"
    if bx.exists():
        ensure_subdirs(bx)
        for f in bx.glob("*.png"):
            move_file(f, bx / "assets" / f.name)
        for f in bx.glob("*.docx"):
            move_file(f, bx / "assets" / f.name)

    # tomodachi-island: remove node_modules if that's all there is
    ti = PROJECTS / "tomodachi-island"
    if ti.exists():
        nm = ti / "node_modules"
        if nm.exists() and nm.is_dir():
            log("rmtree tomodachi-island/node_modules/")
            if not DRY_RUN:
                shutil.rmtree(str(nm))
        ensure_subdirs(ti)
        create_main_note(ti, "tomodachi-island", "shelved")

    # Remove inspection-mobile.pdf from root (belongs in house-inspection-feb7)
    stray_pdf = PROJECTS / "inspection-mobile.pdf"
    hi = PROJECTS / "house-inspection-feb7"
    if stray_pdf.exists() and hi.exists():
        move_file(stray_pdf, hi / "assets" / "inspection-mobile.pdf")

    # Summary
    print(f"\n{'=' * 50}")
    print(f"Total actions: {len(actions)}")
    if DRY_RUN:
        print("Run with --apply to execute.")
    else:
        print("Done! Verify in Obsidian + EmptyOS.")


if __name__ == "__main__":
    run()
