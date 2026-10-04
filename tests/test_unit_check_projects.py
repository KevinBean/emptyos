"""scripts/check_projects.py — the project-model guard (plan P8).

Daemon-free: every case builds a throwaway vault. Both directions are pinned —
each finding fires on the shape it names, and each noise class measured on the
real vault on 2026-09-28 (an app data dir under `10_Projects/`, an album's
`tracks: 8` song count, area homes, closed projects, inherited areas) stays
silent.
"""
from __future__ import annotations

import datetime
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_projects as cp  # noqa: E402

TODAY = datetime.date(2026, 9, 28)

AREAS = """# Project areas

### `emptyos`
x

### `creative`
y
"""


def _note(status="active", area="emptyos", start="2026-09-01", deadline="2026-10-31",
          goal="Ship the thing.", tags=("project",), extra="") -> str:
    fm = [f"status: {status}"] if status is not None else []
    if area is not None:
        fm.append(f"area: {area}")
    if start is not None:
        fm.append(f"start: {start}")
    if deadline is not None:
        fm.append(f"deadline: {deadline}")
    if tags:
        fm.append("tags:\n" + "\n".join(f"  - {t}" for t in tags))
    if extra:
        fm.append(extra.strip())
    body = f"\n## Goal\n\n{goal}\n" if goal is not None else "\n## Notes\n\nx\n"
    return "---\n" + "\n".join(fm) + "\n---\n\n# p\n" + body


INBOX = {"human_open": 2, "machine_open": 5, "orphans": 1, "orphan_days": 7}


@pytest.fixture
def vault(monkeypatch):
    monkeypatch.setattr(cp, "_inbox", lambda v, t: INBOX)
    with tempfile.TemporaryDirectory() as d:
        v = Path(d)
        (v / "30_Resources" / "EmptyOS" / "projects").mkdir(parents=True)
        (v / "30_Resources" / "EmptyOS" / "projects" / "areas.md").write_text(AREAS, encoding="utf-8")
        for sub in ("_plans/done", "_next"):
            (v / "10_Projects" / "emptyos" / "log" / sub).mkdir(parents=True)
        yield v


def put(v: Path, pid: str, text: str, name: str | None = None, root: str = "10_Projects") -> None:
    d = v / root / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / (name or f"{pid}.md")).write_text(text, encoding="utf-8")


def codes(v: Path, pid: str | None = None) -> list[str]:
    res = cp.scan(v, today=TODAY)
    return sorted(f["code"] for f in res["findings"] if pid is None or f["project"] == pid)


def home(v: Path) -> None:
    # The fixture's `emptyos` folder holds `log/`; make it the area home it is.
    put(v, "emptyos", _note(extra="kind: area-home", start=None, deadline=None, goal=None))


# --- silent -----------------------------------------------------------------

def test_healthy_project_reports_nothing(vault):
    home(vault)
    put(vault, "good", _note())
    assert codes(vault) == []


def test_area_home_is_not_asked_for_an_end(vault):
    home(vault)
    assert codes(vault, "emptyos") == []


@pytest.mark.parametrize("status", ["idea", "shelved", "completed", "archived"])
def test_closed_or_idea_project_skips_the_open_checks(vault, status):
    home(vault)
    put(vault, "old", _note(status=status, area=None, start=None, deadline=None, goal=None))
    assert codes(vault, "old") == []


def test_subproject_inherits_the_parents_area(vault):
    home(vault)
    put(vault, "parent", _note(deadline="2026-12-31"))
    put(vault, "child", _note(area=None, extra="parent: parent"))
    assert codes(vault) == []


def test_plan_in_done_resolves(vault):
    home(vault)
    (vault / "10_Projects/emptyos/log/_plans/done/old-plan.md").write_text("x", encoding="utf-8")
    (vault / "10_Projects/emptyos/log/_plans/live-plan.md").write_text("x", encoding="utf-8")
    (vault / "10_Projects/emptyos/log/_next/core.md").write_text("x", encoding="utf-8")
    put(vault, "p", _note(extra="plans:\n  - old-plan\n  - live-plan\ntracks:\n  - core"))
    assert codes(vault) == []


def test_album_song_count_is_not_a_track_reference(vault):
    # Measured on a real vault: an album note's `tracks: 8` is its song count.
    home(vault)
    put(vault, "album", _note(area="creative", extra="tracks: 8"))
    assert codes(vault) == []


def test_vault_map_app_data_dir_is_not_a_project(vault):
    # canvas on the real vault: `[canvas] boards_dir = "10_Projects/canvas"`.
    home(vault)
    (vault / "30_Resources/EmptyOS/_vault-map.toml").write_text(
        '[canvas]\nboards_dir = "10_Projects/canvas"\n', encoding="utf-8")
    (vault / "10_Projects/canvas").mkdir()
    (vault / "10_Projects/canvas/board.md").write_text("x", encoding="utf-8")
    assert codes(vault) == []


def test_area_inherits_through_two_levels(vault):
    # The app's resolve_inherited_areas walks to the nearest ancestor with an area.
    home(vault)
    # Leaf sorts FIRST, so a one-level lookup filling rows in order cannot
    # borrow an area its parent has only just inherited.
    put(vault, "z-root", _note(deadline="2026-12-31"))
    put(vault, "m-mid", _note(area=None, deadline="2026-12-31", extra="parent: z-root"))
    put(vault, "a-leaf", _note(area=None, extra="parent: m-mid"))
    assert codes(vault) == []


def test_archived_parent_resolves_and_lends_its_area(vault):
    home(vault)
    put(vault, "old", _note(status="completed", deadline="2026-12-31"), root="40_Archive/10_Projects")
    put(vault, "child", _note(area=None, extra="parent: old"))
    assert codes(vault) == []


def test_status_is_read_case_folded_like_the_app(vault):
    home(vault)
    put(vault, "p", _note(status="Active", goal=None))
    assert codes(vault, "p") == ["no_goal"]


def test_equal_dates_are_not_findings(vault):
    # start == deadline, parent ends the same day as its child, deadline today.
    home(vault)
    put(vault, "parent", _note(start="2026-09-01", deadline="2026-09-28"))
    put(vault, "child", _note(start="2026-09-28", deadline="2026-09-28", extra="parent: parent"))
    res = cp.scan(vault, today=TODAY)
    assert res["findings"] == [] and res["report"]["overdue"] == []


def test_closed_child_does_not_hold_the_parent_to_its_deadline(vault):
    home(vault)
    put(vault, "parent", _note(deadline="2026-10-01"))
    put(vault, "child", _note(status="completed", deadline="2026-12-31", extra="parent: parent"))
    assert codes(vault) == []


# --- fires ------------------------------------------------------------------

def test_folder_without_any_note(vault):
    home(vault)
    (vault / "10_Projects/stray").mkdir()
    assert codes(vault) == ["no_note"]


def test_flat_project_file(vault):
    home(vault)
    (vault / "10_Projects/loose.md").write_text(_note(), encoding="utf-8")
    assert codes(vault) == ["flat_note"]


def test_reads_the_note_the_app_reads(vault):
    # Measured on a real vault: a folder with both notes; the app reads README.md.
    home(vault)
    put(vault, "p", _note(status="shelved"))
    put(vault, "p", _note(status="drafting"), name="README.md")
    assert codes(vault, "p") == ["bad_status"]


def test_any_markdown_note_is_read_when_no_named_one_exists(vault):
    home(vault)
    put(vault, "p", _note(goal=None), name="notes.md")
    assert codes(vault, "p") == ["no_goal"]


def test_untagged_note(vault):
    home(vault)
    put(vault, "p", _note(tags=("music",)))
    assert codes(vault, "p") == ["untagged"]


@pytest.mark.parametrize("status", [None, "in-progress", "evaluation", "granted"])
def test_status_outside_the_vocabulary(vault, status):
    home(vault)
    put(vault, "p", _note(status=status))
    assert codes(vault, "p") == ["bad_status"]


def test_area_outside_the_vocabulary(vault):
    # inbox on the real vault carries `area: none`.
    home(vault)
    put(vault, "p", _note(area="none"))
    assert codes(vault, "p") == ["bad_area"]


def test_area_home_is_still_held_to_the_vocabulary(vault):
    home(vault)
    put(vault, "inbox", _note(area="none", extra="kind: area-home", start=None, deadline=None))
    assert codes(vault, "inbox") == ["bad_area"]


def test_unreal_dates(vault):
    home(vault)
    put(vault, "p", _note(start="2026-02-30", deadline="soon"))
    assert codes(vault, "p") == ["bad_date", "bad_date"]


def test_start_after_deadline(vault):
    home(vault)
    put(vault, "p", _note(start="2026-11-01", deadline="2026-10-01"))
    assert codes(vault, "p") == ["start_after_deadline"]


def test_parent_that_does_not_exist(vault):
    home(vault)
    put(vault, "p", _note(extra="parent: ghost"))
    assert codes(vault, "p") == ["parent_missing"]


def test_parent_that_ends_before_its_open_child(vault):
    home(vault)
    put(vault, "parent", _note(deadline="2026-10-01"))
    put(vault, "child", _note(deadline="2026-10-31", extra="parent: parent"))
    res = cp.scan(vault, today=TODAY)
    assert [(f["project"], f["code"]) for f in res["findings"]] == [("parent", "parent_ends_first")]


def test_plan_and_track_that_resolve_nowhere(vault):
    home(vault)
    put(vault, "p", _note(extra="plans:\n  - nope\ntracks:\n  - englishos-cloud"))
    assert codes(vault, "p") == ["plan_missing", "track_missing"]


def test_single_scalar_track_is_still_resolved(vault):
    # Only a bare INTEGER is a song count; a scalar slug is a track reference.
    home(vault)
    put(vault, "p", _note(extra="tracks: englishos-cloud"))
    assert codes(vault, "p") == ["track_missing"]


@pytest.mark.parametrize("field,code", [
    ("area", "no_area"), ("start", "no_start"), ("deadline", "no_deadline"), ("goal", "no_goal"),
])
def test_open_project_missing_one_field(vault, field, code):
    home(vault)
    put(vault, "p", _note(**{field: None}))
    assert codes(vault, "p") == [code]


@pytest.mark.parametrize("status", ["spec-ready", "blocked"])
def test_every_open_status_is_held_to_the_fields(vault, status):
    home(vault)
    put(vault, "p", _note(status=status, goal=None))
    assert codes(vault, "p") == ["no_goal"]


def test_placeholder_goal_is_no_goal(vault):
    home(vault)
    put(vault, "p", _note(goal="TBD"))
    assert codes(vault, "p") == ["no_goal"]


def test_started_alias_is_named_in_the_hint(vault):
    # Measured on a real vault: `started:` where the model reads `start:`.
    home(vault)
    put(vault, "p", _note(start=None, extra="started: 2026-04-14"))
    res = cp.scan(vault, today=TODAY)
    [f] = [f for f in res["findings"] if f["project"] == "p"]
    assert f["code"] == "no_start" and "started: 2026-04-14" in f["detail"]


def test_missing_vocabulary_is_a_finding_not_a_pass(vault):
    home(vault)
    (vault / "30_Resources/EmptyOS/projects/areas.md").unlink()
    put(vault, "p", _note(area="anything"))
    assert codes(vault) == ["no_vocabulary"]


# --- report -----------------------------------------------------------------

def test_overdue_is_reported_but_is_not_a_finding(vault):
    home(vault)
    put(vault, "late", _note(start="2026-08-01", deadline="2026-09-20"))
    res = cp.scan(vault, today=TODAY)
    assert res["findings"] == []
    assert res["report"]["overdue"] == [{"project": "late", "deadline": "2026-09-20", "days": 8}]


def test_closed_project_past_its_deadline_is_not_overdue(vault):
    home(vault)
    put(vault, "done", _note(status="completed", deadline="2026-01-01"))
    assert cp.scan(vault, today=TODAY)["report"]["overdue"] == []


def test_inbox_report_is_unavailable_without_the_board_reader(monkeypatch):
    monkeypatch.setitem(sys.modules, "session_board", types.ModuleType("session_board"))
    assert cp._inbox(Path("."), TODAY) is None


def test_inbox_report_reads_the_board_and_its_day_limit(monkeypatch):
    fake = types.ModuleType("session_board")
    fake.inbox_ages = lambda v, t: {"human_open": 3, "machine_open": 9, "orphans": 1}
    fake.ORPHAN_DAYS = 10  # not 7, so a hardcoded limit in the guard shows
    monkeypatch.setitem(sys.modules, "session_board", fake)
    out = cp.render({"projects": 0, "findings": [],
                     "report": {"overdue": [], "inbox": cp._inbox(Path("."), TODAY)}})
    assert "3 open human tasks, 1 older than 10 days" in out


def test_a_failing_board_reader_reads_as_failed_not_unavailable(monkeypatch):
    fake = types.ModuleType("session_board")

    def boom(v, t):
        raise RuntimeError("git missing")
    fake.inbox_ages = boom
    monkeypatch.setitem(sys.modules, "session_board", fake)
    out = cp.render({"projects": 0, "findings": [],
                     "report": {"overdue": [], "inbox": cp._inbox(Path("."), TODAY)}})
    assert "inbox: failed: inbox_ages: RuntimeError: git missing" in out


def test_scan_carries_the_inbox_report(vault):
    home(vault)
    assert cp.scan(vault, today=TODAY)["report"]["inbox"] is INBOX


# --- entry point ------------------------------------------------------------

def test_no_vault_is_healthy(monkeypatch, capsys):
    monkeypatch.setattr(cp, "vault_root", lambda: None)
    monkeypatch.setattr(sys, "argv", ["check_projects.py", "--json"])
    assert cp.main() == 0
    assert '"ok": true' in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["human", "json"])
def test_findings_exit_exactly_one(vault, monkeypatch, capsys, mode):
    # Two findings, so `return len(findings)` cannot pass for `return 1`.
    home(vault)
    put(vault, "p", _note(goal=None, deadline=None))
    monkeypatch.setattr(cp, "vault_root", lambda: vault)
    monkeypatch.setattr(sys, "argv", ["check_projects.py"] + (["--json"] if mode == "json" else []))
    assert cp.main() == 1
    out = capsys.readouterr().out
    if mode == "json":
        env = json.loads(out)
        assert env["ok"] is False and len(env["data"]["findings"]) == 2
    else:
        assert "no_goal (1)" in out and out.rstrip().endswith("2 projects · 2 findings")


def test_healthy_vault_exits_zero(vault, monkeypatch):
    home(vault)
    put(vault, "p", _note())
    monkeypatch.setattr(cp, "vault_root", lambda: vault)
    monkeypatch.setattr(sys, "argv", ["check_projects.py"])
    assert cp.main() == 0
