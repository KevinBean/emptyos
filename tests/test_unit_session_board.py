"""scripts/session_board.py — lanes, live-session mapping, and the exact-slug mutations.

Daemon-free: every case builds a throwaway `_next/` + `_plans/` log dir and a fake
home directory for transcripts, so nothing touches the real vault.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import session_board as sb  # noqa: E402

TODAY = datetime.date(2026, 9, 23)


def brief(slug: str, last: str, threads: list[str], extra_fm: str = "") -> str:
    body = "\n".join(f"- {t}" for t in threads)
    return (f"---\ntype: next-session-brief\ntrack: {slug}\nlast_session: {last}\n"
            f"last_session_title: {slug} title\n{extra_fm}---\n\n# Next\n\n"
            f"## Open threads\n{body}\n\n## Recommended starting move\nGo.\n")


@pytest.fixture
def log():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "log"
        (root / "_next").mkdir(parents=True)
        (root / "_plans").mkdir()
        (root / "home").mkdir()
        yield root


def write(log: Path, slug: str, text: str) -> None:
    (log / "_next" / f"{slug}.md").write_text(text, encoding="utf-8")


def board(log: Path) -> dict:
    return sb.scan(log, 120, today=TODAY, home=log / "home")


def lane(b: dict, slug: str) -> str:
    return next(t["lane"] for t in b["tracks"] if t["slug"] == slug)


def ns(**kw) -> argparse.Namespace:
    base = {"apply": False, "reason": "", "disposition": ""}
    base.update(kw)
    return argparse.Namespace(**base)


# ── classification ───────────────────────────────────────────────────────────

def test_decision_kevin_counts_as_waiting_on_kevin(log):
    # dev_tracks.blocked_human matches only [blocked-human]; the board must be wider.
    write(log, "a", brief("a", "2026-09-20", ["**[decision-Kevin]** pick the slug"]))
    assert lane(board(log), "a") == "kevin"


@pytest.mark.parametrize("threads,extra,last,want", [
    (["**[blocked-human]** x", "**[open-code]** y"], "", "2026-09-20", "kevin"),
    (["**[open-code]** y"], "", "2026-09-20", "ready"),
    (["plain thread"], "parked: 2026-09-01\n", "2026-09-20", "parked"),
    (["plain thread"], "", "2026-07-01", "dormant"),
    (["plain thread"], "", "2026-09-20", "untagged"),
])
def test_lane_precedence(log, threads, extra, last, want):
    write(log, "t", brief("t", last, threads, extra))
    assert lane(board(log), "t") == want


def test_struck_out_thread_is_resolved_even_with_its_tag_inside(log):
    # Real shape (core-infra brief): the tag sits inside the strike-through.
    write(log, "t", brief("t", "2026-09-20", [
        "~~**`atomic_io.py` mode on POSIX [decision-Kevin].**~~ fixed in abc1234",
        "**[open-code]** still to do"]))
    b = board(log)
    assert lane(b, "t") == "ready"
    assert [th["kind"] for th in b["tracks"][0]["threads"]] == ["ready"]


def test_strike_in_the_middle_of_a_thread_keeps_it_open(log):
    write(log, "t", brief("t", "2026-09-20", ["**[decision-Kevin]** ~~old wording~~ still open"]))
    assert lane(board(log), "t") == "kevin"


def test_claimed_plan_makes_track_live_over_kevin(log):
    write(log, "t", brief("t", "2026-09-20", ["**[decision-Kevin]** q"]))
    (log / "_plans" / "p.md").write_text(
        "---\nplan: p\ntrack: t\nactive_task: T2 2026-09-23\n---\n\n## Tasks\n\n"
        "| id | task | depends_on | status |\n|---|---|---|---|\n"
        "| T1 | a | — | done |\n| T2 | b | T1 | active |\n| T3 | c | — | blocked [decision-kevin] |\n",
        encoding="utf-8")
    b = board(log)
    assert lane(b, "t") == "live"
    assert b["plans"][0]["tasks"] == {"done": 1, "active": 1, "blocked": 1}


def test_plan_rows_are_read_whatever_the_id_prefix(log):
    # The board used to count only `T<n>` ids by column index, so a plan
    # numbered P1..Pn showed no tasks at all. It now shares check_plan_staleness'
    # row parser, which anchors on the status cell.
    (log / "_plans" / "p.md").write_text(
        "---\nplan: p\ntrack: t\nactive_task: \"\"\n---\n\n## Tasks\n\n"
        "| id | task | depends_on | status | session | disposition |\n|---|---|---|---|---|---|\n"
        "| P1 | a `x | y` pipe | — | done | s | shipped |\n| P2 | b | P1 | queued | | |\n",
        encoding="utf-8")
    assert board(log)["plans"][0]["tasks"] == {"done": 1, "queued": 1}


def test_unreadable_and_cut_off_rows_are_counted_not_guessed(log):
    # A row with no status word is "?", not whatever sits in column 3; rows
    # below a wrapped row are "unseen" rather than silently dropped.
    (log / "_plans" / "p.md").write_text(
        "---\nplan: p\ntrack: t\n---\n\n## Tasks\n\n"
        "| id | task | depends_on | status |\n|---|---|---|---|\n"
        "| T1 | a | — | done |\n| T2 | b | — | shipped-ish |\n"
        "| T3 | c starts\nand wraps here | — | queued |\n| T4 | d | — | queued |\n| T5 | e | — | queued |\n",
        encoding="utf-8")
    # T2 has no status word; T3's status wrapped onto the next line. Both are "?".
    assert board(log)["plans"][0]["tasks"] == {"done": 1, "?": 2, "unseen": 2}


def test_live_session_names_track_and_stale_transcript_is_ignored(log):
    write(log, "cable", brief("cable", "2026-09-20", ["**[open-code]** T7"]))
    write(log, "cable-pulling", brief("cable-pulling", "2026-09-20", ["**[open-code]** T7"]))
    proj = log / "home" / ".claude" / "projects" / "D--x"
    proj.mkdir(parents=True)
    lines = [
        # typed by the user — `cable` is a prefix of `cable-pulling` and must not match
        {"type": "user", "message": {"content": "/eos-session-resume cable-pulling"}},
        # merely READ: a tool result listing a brief is not work on that track
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "D:/v/log/_next/cable.md\n"}]}},
        {"type": "ai-title", "aiTitle": "Cable work"},
    ]
    fresh = proj / "s1.jsonl"
    fresh.write_text("".join(json.dumps(o) + "\n" for o in lines), encoding="utf-8")
    old = proj / "s2.jsonl"
    old.write_text(json.dumps({"type": "user", "message": {"content": "session-resume cable"}}) + "\n",
                   encoding="utf-8")
    hours_ago = time.time() - 5 * 3600
    os.utime(old, (hours_ago, hours_ago))
    b = board(log)
    assert [s["title"] for s in b["live"]] == ["Cable work"]
    assert b["live"][0]["tracks"] == ["cable-pulling"]
    assert lane(b, "cable-pulling") == "live" and lane(b, "cable") == "ready"


def test_codex_subthread_is_not_a_session(log):
    day = log / "home" / ".codex" / "sessions" / "2026" / "09" / "23"
    day.mkdir(parents=True)
    main = [{"type": "session_meta", "payload": {"id": "m"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "fix the  board"}}]
    sub = [{"type": "session_meta", "payload": {"id": "s", "parent_thread_id": "m"}},
           {"type": "event_msg", "payload": {"type": "user_message", "message": "review this"}}]
    (day / "rollout-main.jsonl").write_text("".join(json.dumps(o) + "\n" for o in main), encoding="utf-8")
    (day / "rollout-sub.jsonl").write_text("".join(json.dumps(o) + "\n" for o in sub), encoding="utf-8")
    assert [s["title"] for s in board(log)["live"]] == ["fix the board"]


def test_edit_of_a_brief_counts_as_working_on_it(log):
    write(log, "career", brief("career", "2026-09-20", ["**[open-code]** x"]))
    proj = log / "home" / ".claude" / "projects" / "D--x"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit",
         "input": {"file_path": "D:\\Vault\\log\\_next\\career.md"}}]}}) + "\n", encoding="utf-8")
    assert board(log)["live"][0]["tracks"] == ["career"]


def test_render_lists_decision_queue(log):
    write(log, "a", brief("a", "2026-09-20", ["**[decision-Kevin]** pick the slug"]))
    out = sb.render(board(log), "2026-09-23 10:00")
    assert "## Decision queue (1)" in out and "pick the slug" in out
    assert "author: ai" in out


# ── mutations ────────────────────────────────────────────────────────────────

def test_park_is_dry_run_until_apply_then_unpark(log):
    write(log, "t", brief("t", "2026-07-01", ["plain"]))
    before = (log / "_next" / "t.md").read_text(encoding="utf-8")
    mode, _ = sb.mutate(ns(cmd="park", slug="t", reason="waits for GPU"), log)
    assert mode == "dry-run"
    assert (log / "_next" / "t.md").read_text(encoding="utf-8") == before
    sb.mutate(ns(cmd="park", slug="t", reason="waits for GPU", apply=True), log)
    assert lane(board(log), "t") == "parked"
    sb.mutate(ns(cmd="unpark", slug="t", apply=True), log)
    assert lane(board(log), "t") == "dormant"


def test_park_requires_reason(log):
    write(log, "t", brief("t", "2026-07-01", ["plain"]))
    with pytest.raises(SystemExit):
        sb.mutate(ns(cmd="park", slug="t", apply=True), log)


def test_close_archives_and_drops_only_the_exact_index_row(log):
    write(log, "cable", brief("cable", "2026-09-20", []))
    write(log, "cable-pulling", brief("cable-pulling", "2026-09-20", []))
    (log / "_next" / "_index.md").write_text(
        "| Track | Last |\n|---|---|\n| [cable](cable.md) | x |\n| [cable-pulling](cable-pulling.md) | y |\n",
        encoding="utf-8")
    sb.mutate(ns(cmd="close", slug="cable", disposition="shipped", apply=True), log)
    assert not (log / "_next" / "cable.md").exists()
    arch = list((log / "_next" / "archive").glob("cable-*.md"))
    assert len(arch) == 1 and "closed_disposition" in arch[0].read_text(encoding="utf-8")
    idx = (log / "_next" / "_index.md").read_text(encoding="utf-8")
    assert "(cable.md)" not in idx and "(cable-pulling.md)" in idx


def test_unknown_or_substring_slug_is_refused(log):
    write(log, "cable-pulling", brief("cable-pulling", "2026-09-20", []))
    with pytest.raises(SystemExit):
        sb.mutate(ns(cmd="close", slug="cable", disposition="x", apply=True), log)
    assert (log / "_next" / "cable-pulling.md").exists()


def test_close_keeps_a_row_fused_onto_the_same_line(log):
    # Real shape (_index.md line 54, 2026-09-23): two rows on one line, joined by `||`.
    write(log, "usecase-audit", brief("usecase-audit", "2026-09-20", []))
    fused = ("| [android-tool](android-tool.md) | 2026-07-22 | Gemini vision. "
             "|| [usecase-audit](usecase-audit.md) | 2026-07-20 | see [[android-tool]] | [[usecase-audit]] |")
    other = "| [career](career.md) | 2026-09-01 | links [[usecase-audit]] in prose | [[career]] |"
    (log / "_next" / "_index.md").write_text(f"| Track |\n|---|\n{fused}\n{other}\n", encoding="utf-8")
    sb.mutate(ns(cmd="close", slug="usecase-audit", disposition="shipped", apply=True), log)
    idx = (log / "_next" / "_index.md").read_text(encoding="utf-8")
    assert "(android-tool.md)" in idx and "(career.md)" in idx
    assert "(usecase-audit.md)" not in idx


def test_second_close_same_day_never_overwrites_the_archive(log):
    write(log, "t", brief("t", "2026-09-20", ["first"]))
    sb.mutate(ns(cmd="close", slug="t", disposition="a", apply=True), log)
    write(log, "t", brief("t", "2026-09-20", ["second"]))
    sb.mutate(ns(cmd="close", slug="t", disposition="b", apply=True), log)
    bodies = sorted(p.read_text(encoding="utf-8") for p in (log / "_next" / "archive").glob("t-*.md"))
    assert len(bodies) == 2 and any("- first" in b for b in bodies) and any("- second" in b for b in bodies)


def test_park_reason_with_quotes_round_trips(log):
    write(log, "t", brief("t", "2026-09-20", ["plain"]))
    sb.mutate(ns(cmd="park", slug="t", reason='waits "GPU" 等 C:\\x', apply=True), log)
    t = next(t for t in board(log)["tracks"] if t["slug"] == "t")
    assert t["parked_reason"] == "waits 'GPU' 等 C:/x"


def test_parking_a_tagged_track_hides_it_and_its_decisions(log):
    write(log, "t", brief("t", "2026-09-20", ["**[decision-Kevin]** q", "**[open-code]** y"]))
    sb.mutate(ns(cmd="park", slug="t", reason="later", apply=True), log)
    b = board(log)
    assert lane(b, "t") == "parked"
    assert "## Decision queue (0)" in sb.render(b, "now")


def test_harness_injected_skill_text_is_not_an_action(log):
    write(log, "career", brief("career", "2026-09-20", ["**[open-code]** x"]))
    proj = log / "home" / ".claude" / "projects" / "D--x"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(json.dumps(
        {"type": "user", "isMeta": True,
         "message": {"content": "Example: `/eos-session-resume career` opens the career brief"}}) + "\n",
        encoding="utf-8")
    assert board(log)["live"][0]["tracks"] == []


def test_merge_keeps_sub_bullets_and_prose(log):
    write(log, "a", brief("a", "2026-09-20", ["keep A"]))
    (log / "_next" / "b.md").write_text(
        "---\ntrack: b\nlast_session: 2026-09-20\n---\n\n## Open threads\n"
        "- **[open-code]** parent\n  - child detail\n  continued prose\n", encoding="utf-8")
    sb.mutate(ns(cmd="merge", into="a", src="b", apply=True), log)
    text = (log / "_next" / "a.md").read_text(encoding="utf-8")
    assert "- (from b) **[open-code]** parent\n  - child detail\n  continued prose" in text


def test_merge_into_brief_ending_at_the_heading_adds_no_second_heading(log):
    (log / "_next" / "a.md").write_text("---\ntrack: a\n---\n\n## Open threads", encoding="utf-8")
    write(log, "b", brief("b", "2026-09-20", ["from B"]))
    sb.mutate(ns(cmd="merge", into="a", src="b", apply=True), log)
    text = (log / "_next" / "a.md").read_text(encoding="utf-8")
    assert text.count("## Open threads") == 1 and "- (from b) from B" in text


def test_merge_moves_threads_and_archives_source(log):
    write(log, "a", brief("a", "2026-09-20", ["keep A"]))
    write(log, "b", brief("b", "2026-09-20", ["**[open-code]** from B"]))
    sb.mutate(ns(cmd="merge", into="a", src="b", apply=True), log)
    text = (log / "_next" / "a.md").read_text(encoding="utf-8")
    assert "- keep A" in text and "- (from b) **[open-code]** from B" in text
    assert text.index("(from b)") < text.index("## Recommended starting move")
    assert not (log / "_next" / "b.md").exists()
    assert lane(board(log), "a") == "ready"


# ── per-row claims (plan project-session-integration P5) ─────────────────────

def _claimed_plan(log: Path) -> None:
    (log / "_plans" / "p.md").write_text(
        "---\nplan: p\ntrack: t\n---\n\n## Tasks\n\n"
        "| id | task | depends_on | status | session | disposition |\n|---|---|---|---|---|---|\n"
        "| P1 | a | — | active | 2026-09-23 #70b339b6 | |\n"
        "| P2 | b | — | active | 2026-09-23 #efbad179 | |\n"
        "| P3 | c | P1 | queued | | |\n",
        encoding="utf-8")


def test_a_plan_can_hold_two_claims_and_its_track_is_live(log):
    write(log, "t", brief("t", "2026-09-20", ["**[open-code]** x"]))
    _claimed_plan(log)
    b = board(log)
    assert [(c["id"], c["sid"]) for c in b["plans"][0]["claims"]] == [
        ("P1", "70b339b6"), ("P2", "efbad179")]
    assert lane(b, "t") == "live"
    assert "| P1 #70b339b6 (no live session), P2 #efbad179 (no live session) |" in sb.render(b, "now")


def test_live_transcript_is_matched_to_its_claim_by_exact_sid(log):
    write(log, "t", brief("t", "2026-09-20", ["**[open-code]** x"]))
    _claimed_plan(log)
    proj = log / "home" / ".claude" / "projects" / "D--x"
    proj.mkdir(parents=True)
    title = json.dumps({"type": "ai-title", "aiTitle": "Working"}) + "\n"
    # The session never typed the track slug: only the sid ties it to the plan.
    (proj / "70b339b6-4caa-4074-99ca-43760fe62115.jsonl").write_text(title, encoding="utf-8")
    (proj / "70b339b7-0000-0000-0000-000000000000.jsonl").write_text(title, encoding="utf-8")
    live = {s["session_id"][:8]: s for s in board(log)["live"]}
    assert live["70b339b6"]["claims"] == ["p · P1"]
    assert live["70b339b6"]["tracks"] == ["t"]
    assert live["70b339b7"]["claims"] == [] and live["70b339b7"]["tracks"] == []
    held = {c["id"]: c["holder_live"] for c in board(log)["plans"][0]["claims"]}
    assert held == {"P1": True, "P2": False}


def test_an_unstamped_active_row_is_not_a_claim(log):
    # A half-written claim: check_plan_staleness reports it; it must not pin the lane.
    write(log, "t", brief("t", "2026-09-20", ["**[open-code]** x"]))
    (log / "_plans" / "p.md").write_text(
        "---\nplan: p\ntrack: t\n---\n\n## Tasks\n\n"
        "| id | task | depends_on | status | session | disposition |\n|---|---|---|---|---|---|\n"
        "| P1 | a | — | active | | |\n",
        encoding="utf-8")
    b = board(log)
    assert b["plans"][0]["claims"] == [] and lane(b, "t") == "ready"


# ── Projects section (plan project-session-integration P3) ─────────────────

import subprocess  # noqa: E402


@pytest.fixture
def vlog():
    """A log dir with the real vault shape, so `vault_of` finds the projects."""
    with tempfile.TemporaryDirectory() as d:
        vault = Path(d) / "vault"
        root = vault / "10_Projects" / "emptyos" / "log"
        (root / "_next").mkdir(parents=True)
        (root / "_plans").mkdir()
        (root / "home").mkdir()
        yield root


def project(log: Path, pid: str, fm: str, body: str = "") -> None:
    d = log.parents[1] / pid
    d.mkdir(exist_ok=True)
    (d / f"{pid}.md").write_text(f"---\n{fm.strip()}\ntags:\n  - project\n---\n\n{body}",
                                 encoding="utf-8")


PLAN = ("---\nplan: {name}\ntrack: {track}\n---\n\n## Tasks\n\n"
        "| id | task | depends_on | status | session | disposition |\n|---|---|---|---|---|---|\n{rows}")


def plan(log: Path, name: str, rows: str, track: str = "t") -> None:
    (log / "_plans" / f"{name}.md").write_text(PLAN.format(name=name, track=track, rows=rows),
                                               encoding="utf-8")


def vboard(log: Path) -> dict:
    return sb.scan(log, 120, today=TODAY, home=log / "home")


def rows(b: dict) -> dict:
    return {r["id"]: r for r in b["projects"]["rows"]}


def test_vault_is_found_only_for_the_real_log_shape(log, vlog):
    assert sb.vault_of(log) is None
    assert sb.vault_of(vlog) == vlog.parents[2]


def test_projects_sort_by_deadline_and_leave_out_homes_and_finished(vlog):
    project(vlog, "late", "status: active\ndeadline: 2026-10-30")
    project(vlog, "soon", "status: active\ndeadline: 2026-09-25")
    project(vlog, "gone", "status: active\ndeadline: 2026-09-20")
    project(vlog, "undated", "status: active")
    project(vlog, "home", "status: active\nkind: area-home\ndeadline: 2026-09-01")
    project(vlog, "done", "status: completed\ndeadline: 2026-09-01")
    b = vboard(vlog)
    assert [r["id"] for r in b["projects"]["rows"]] == ["gone", "soon", "late", "undated"]
    assert rows(b)["soon"]["days_left"] == 2 and rows(b)["gone"]["days_left"] == -3
    assert b["projects"]["hidden"] == {"completed": 1} and b["projects"]["area_homes"] == 1
    out = sb.render(b, "now")
    assert "⚠ 3d overdue" in out and "Not listed: 1 completed, 1 area homes." in out


def test_progress_counts_plans_boxes_milestone_and_subprojects(vlog):
    body = ("## Milestones\n\n### M1 — first\n- status: closed\n\n### M2 — second\n- status: open\n\n"
            "## Tasks\n- [x] a\n  - milestone: M2\n- [ ] b\n  - milestone: M2\n- [ ] c\n")
    project(vlog, "big", "status: active\nplans:\n  - p", body)
    project(vlog, "kid1", "status: completed\nparent: big")
    project(vlog, "kid2", "status: active\nparent: big")
    plan(vlog, "p", "| P1 | a | — | done | 2026-09-20 | shipped |\n| P2 | b | P1 | queued | | |\n"
                    "| P3 | c | — | queued | | |\n")
    r = rows(vboard(vlog))["big"]
    assert r["sessions"] == (1, 3) and r["tasks"] == (1, 3)
    assert r["milestone"] == "M2 1/2" and r["subprojects"] == (1, 2)
    assert sb._progress(r) == "sessions 1/3 · tasks 1/3 · M2 1/2 · sub 1/2"


def test_next_session_task_waits_for_its_dependencies(vlog):
    project(vlog, "p", "status: active\nplans:\n  - a\n  - b")
    plan(vlog, "a", "| A1 | first | — | active | 2026-09-23 #deadbeef | |\n"
                    "| A2 | needs A1 | A1 | queued | | |\n")
    plan(vlog, "b", "| B1 | free | — | queued | | |\n")
    r = rows(vboard(vlog))["p"]
    assert r["next_session_task"] == "b · B1: free"
    assert r["holders"] == ["a · A1 #deadbeef (no live session)"]


def test_next_session_task_follows_a_satisfied_dependency(vlog):
    project(vlog, "p", "status: active\nplans:\n  - a")
    plan(vlog, "a", "| A1 | first | — | done | 2026-09-20 | shipped |\n"
                    "| A2 | needs A1 | A1 | queued | | |\n| A3 | free | — | queued | | |\n")
    assert rows(vboard(vlog))["p"]["next_session_task"] == "a · A2: needs A1"


def test_flow_style_tags_and_normalised_kind_and_status(vlog):
    # The one frontmatter parser reads `tags: [project]`; the scripts' parser did not.
    d = vlog.parents[1] / "flow"
    d.mkdir()
    (d / "flow.md").write_text("---\nstatus: active\ntags: [project]\n---\n", encoding="utf-8")
    project(vlog, "home2", "status: active\nkind: Area_Home")
    project(vlog, "arch", "status: Archived")
    b = vboard(vlog)
    assert [r["id"] for r in b["projects"]["rows"]] == ["flow"]
    assert b["projects"]["hidden"] == {"archived": 1} and b["projects"]["area_homes"] == 1


def test_closed_and_missing_plans_are_counted_not_dropped(vlog):
    project(vlog, "p", "status: active\nplans:\n  - shipped\n  - typo")
    (vlog / "_plans" / "done").mkdir()
    (vlog / "_plans" / "done" / "shipped.md").write_text(
        PLAN.format(name="shipped", track="t", rows="| S1 | a | — | done | 2026-09-01 | shipped |\n"),
        encoding="utf-8")
    r = rows(vboard(vlog))["p"]
    assert r["sessions"] == (1, 1) and r["missing_plans"] == ["typo"]
    assert sb._progress(r) == "sessions 1/1 · no such plan: typo"


def test_a_milestone_with_no_linked_boxes_is_named_not_zero(vlog):
    project(vlog, "p", "status: active", "## Milestones\n\n### M1 — first\n- status: open\n")
    assert rows(vboard(vlog))["p"]["milestone"] == "M1"


def test_a_live_holder_is_shown_without_the_dead_marker(vlog):
    project(vlog, "p", "status: active\nplans:\n  - a")
    plan(vlog, "a", "| A1 | x | — | active | 2026-09-23 #cafe1234 | |\n")
    _transcript(vlog, "cafe1234-0000", "Working")
    assert rows(vboard(vlog))["p"]["holders"] == ["a · A1 #cafe1234"]


def test_next_personal_task_is_the_first_open_box(vlog):
    project(vlog, "p", "status: active", "## Tasks\n- [x] done one\n- [ ] do this\n- [ ] then that\n")
    assert rows(vboard(vlog))["p"]["next_personal_task"] == "do this"


def test_tracks_map_to_projects_even_when_the_owner_is_not_listed(vlog):
    for slug in ("owned", "retired", "loose", "old"):
        write(vlog, slug, brief(slug, "2026-09-20" if slug != "old" else "2026-01-01", ["x"]))
    project(vlog, "live-one", "status: active\ntracks:\n  - owned")
    project(vlog, "finished", "status: completed\ntracks:\n  - retired")
    b = vboard(vlog)
    by = {t["slug"]: t["projects"] for t in b["tracks"]}
    assert by["owned"] == ["live-one"] and by["retired"] == ["finished"]
    assert sorted(b["unowned"]) == ["loose", "old"]
    out = sb.render(b, "now")
    assert "| [[owned]] | [[live-one]] |" in out
    # Only the non-dormant unowned track is listed by name; the dormant one is counted.
    assert "## Tracks no project owns (2)" in out and "[[loose]]" in out
    assert "Listed: the 1 not dormant or parked; the other 1 are only counted." in out
    unowned_section = out.split("## Tracks no project owns")[1]
    assert unowned_section.rstrip().splitlines()[-1] == "[[loose]]"


def _transcript(log: Path, name: str, title: str, text: str = "hi") -> None:
    proj = log / "home" / ".claude" / "projects" / "D--x"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{name}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": text}}) + "\n"
        + json.dumps({"type": "ai-title", "aiTitle": title}) + "\n", encoding="utf-8")


REVIEW = "Review this change for security vulnerabilities.\n\nChanged files: x.py\n/eos-session-resume t"


def test_reviewers_are_not_work_in_progress_and_cannot_make_a_track_live(log):
    write(log, "t", brief("t", "2026-09-20", ["**[open-code]** x"]))
    # A real session whose TITLE says security review is still work.
    _transcript(log, "a1", "Fix security review findings")
    _transcript(log, "a2", "Logout review", REVIEW)
    b = board(log)
    assert [s["title"] for s in b["live"]] == ["Fix security review findings"] and b["wip"] == 1
    assert [s["title"] for s in b["reviewers"]] == ["Logout review"]
    assert lane(b, "t") == "ready"
    assert "Reviewers (1, not counted as work in progress)" in sb.render(b, "now")


def test_a_claim_held_by_any_session_in_the_window_is_live(log):
    # Holder matched against every transcript, reviewers included.
    plan(log, "p", "| P1 | a | — | active | 2026-09-23 #aaaa1111 | |\n"
                   "| P2 | b | — | active | 2026-09-23 #bbbb2222 | |\n")
    _transcript(log, "aaaa1111-0000", "Plain work")
    _transcript(log, "bbbb2222-0000", "Auto", REVIEW)
    held = {c["id"]: c["holder_live"] for c in board(log)["plans"][0]["claims"]}
    assert held == {"P1": True, "P2": True}


def test_codex_subthread_is_a_reviewer(log):
    day = log / "home" / ".codex" / "sessions" / "2026" / "09" / "23"
    day.mkdir(parents=True)
    sub = [{"type": "session_meta", "payload": {"id": "s", "parent_thread_id": "m"}}]
    (day / "rollout-sub.jsonl").write_text("".join(json.dumps(o) + "\n" for o in sub), encoding="utf-8")
    b = board(log)
    assert b["live"] == [] and len(b["reviewers"]) == 1 and b["wip"] == 0


INBOX = ("---\ntags:\n  - project\nkind: area-home\n---\n\n## Tasks\n"
         "- [ ] Buy milk\n- [ ] [JOB] Engineer at X\n- [ ] [missing] thing #dogfood\n"
         "- [ ] 🌱 Growth: fix a loop\n- [x] done human\n")


@pytest.mark.parametrize("line,machine", [
    ("[JOB SCOUT] nothing new tonight", True),
    ("[missing] a thing #dogfood-bug", True),
    ("🌱 Growth (ESCALATION): aged item", True),
    ("🕸️ Connect: wire the orphan", True),
    ("🌿 Root: fix the loop", True),
    ("Write up the dogfooding notes", False),
    ("Ask about the [job] offer letter", False),
    ("Plant a tree — Root: check drainage first", False),
])
def test_machine_markers_are_exact(line, machine):
    assert bool(sb.MACHINE_LINE.search(line)) is machine


def _git(vault: Path, *args: str, date: str | None = None) -> None:
    env = dict(os.environ)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@x")
    subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, env=env)


def test_inbox_splits_machine_lines_and_ages_human_ones_by_blame(vlog):
    vault = vlog.parents[2]
    ib = vault / "10_Projects" / "inbox"
    ib.mkdir()
    (ib / "inbox.md").write_text(INBOX, encoding="utf-8")
    _git(vault, "init", "-q")
    _git(vault, "add", "10_Projects/inbox/inbox.md")
    # 8 days before TODAY (2026-09-23): past the 7-day line.
    _git(vault, "commit", "-q", "-m", "old", date="2026-09-15T12:00:00")
    # 5 days before TODAY, and INSERTED ABOVE the old line, so its final line
    # number differs from the original — blame must be read by the final one.
    (ib / "inbox.md").write_text(INBOX.replace("- [ ] Buy milk\n", "- [ ] Pay rent\n- [ ] Buy milk\n"),
                                 encoding="utf-8")
    _git(vault, "commit", "-q", "-am", "newer", date="2026-09-18T12:00:00")
    # A human task added since the last commit is new, not an orphan.
    text = (ib / "inbox.md").read_text(encoding="utf-8")
    (ib / "inbox.md").write_text(text + "- [ ] Call the plumber\n", encoding="utf-8")
    got = vboard(vlog)["inbox"]
    assert got == {"human_open": 3, "machine_open": 3, "orphans": 1}


def test_inbox_age_is_unknown_without_git(vlog):
    ib = vlog.parents[1] / "inbox"
    ib.mkdir()
    (ib / "inbox.md").write_text(INBOX, encoding="utf-8")
    got = vboard(vlog)["inbox"]
    assert got["orphans"] is None and got["human_open"] == 1
    assert "age unknown" in sb.render(vboard(vlog), "now")


# ── Dispatch (plan project-session-integration P4) ──────────────────────────


def _rows(spec: str) -> list[dict]:
    """`A1:done:, A2:queued:A1` → plan_table rows."""
    table = "\n".join(
        f"| {i} | task {i} | {d or '—'} | {st} | | |"
        for i, st, d in (x.split(":") for x in spec.split(", ")))
    body = ("## Tasks\n\n| id | task | depends_on | status | session | disposition |\n"
            "|---|---|---|---|---|---|\n" + table + "\n")
    return sb.parse_task_table(body, 0)[0]


def test_waves_level_rows_by_their_dependencies():
    ws, stuck = sb.waves(_rows("A1:done:, A2:queued:A1, A3:queued:A2, A4:queued:, A5:queued:A2,A4"))
    assert [[r["id"] for r in w] for w in ws] == [["A2", "A4"], ["A3", "A5"]]
    assert stuck == []


def test_stuck_rows_say_why():
    ws, stuck = sb.waves(_rows("B1:queued:ZZ, B2:queued:B1, C1:queued:C2, C2:queued:C1"))
    why = {r["id"]: r["why"] for r in stuck}
    assert ws == []
    assert why["B1"] == "depends on an unknown id: ZZ"
    assert why["B2"] == "waits on a stuck or blocked row: B1"
    assert why["C1"] == "in a dependency cycle: C2"


def _dispatch_vault(vlog, sessions=()):
    """Two projects, two plans, three tracks; `sessions` = (name, idle_min, first_prompt)."""
    for slug in ("ta", "tb", "tc", "td"):
        write(vlog, slug, brief(slug, "2026-09-20", ["**[open-code]** x"]))
    project(vlog, "later", "status: active\ndeadline: 2026-11-30\nplans:\n  - pa")
    project(vlog, "sooner", "status: active\ndeadline: 2026-10-01\nplans:\n  - pb\ntracks:\n  - tc")
    project(vlog, "loose", "status: active\ndeadline: 2026-12-31", "## Tasks\n- [ ] buy tape\n")
    plan(vlog, "pa", "| A1 | plain work | — | queued | | |\n| A2 | then this | A1 | queued | | |\n", track="ta")
    plan(vlog, "pb", "| B1 | ask [decision-Kevin] first | — | queued | | |\n"
                     "| B2 | deploy it | — | queued | | |\n| B3 | ordinary | — | queued | | |\n", track="tb")
    for name, idle, prompt in sessions:
        _transcript(vlog, name, name, prompt)
        p = vlog / "home" / ".claude" / "projects" / "D--x" / f"{name}.jsonl"
        t = time.time() - idle * 60
        os.utime(p, (t, t))
    return vboard(vlog), sb.open_plan_rows(vlog / "_plans")


def test_roadmap_tags_kevin_steps_and_orders_by_deadline(vlog):
    b, pr = _dispatch_vault(vlog)
    d = sb.dispatch(b, pr, commit_gb=10.0)
    assert [e["project"] for e in d["roadmap"]] == ["sooner", "later", "loose"]
    sooner = {st["id"]: st["kevin"] for st in d["roadmap"][0]["waves"][0]}
    assert sooner == {"B1": True, "B2": True, "B3": False}
    assert d["roadmap"][1]["waves"][1][0]["id"] == "A2"
    assert d["roadmap"][2]["note"] == "no session work; your next task: buy tape"
    # Earliest deadline first, gated steps skipped, one session per track; the
    # project's plan-less track `tc` is offered because its lane is ready.
    assert [(c["track"], c["id"]) for c in d["open_now"]] == [("tb", "B3"), ("tc", ""), ("ta", "A1")]
    # The command names the row, so resume claims exactly this step (its Step 1).
    assert "  /eos-session-resume tb pb:B3   (sooner" in sb.render_dispatch(d)
    assert d["open_now"][0]["command"] == "/eos-session-resume tb pb:B3"


def test_free_slots_count_only_working_claude_sessions(vlog):
    b, pr = _dispatch_vault(vlog, sessions=[
        ("s1aaaaaa-1", 5, "hi"),
        ("s3cccccc-1", 90, "hi"),                        # idle but open: still holds a slot
        ("s4dddddd-1", 5, sb.REVIEWER_PROMPT + " x"),    # auto review
        ("s5eeeeee-1", 5, "hi"),                         # the supervisor, passed below
    ])
    day = vlog / "home" / ".codex" / "sessions" / "2026" / "09" / "23"
    day.mkdir(parents=True)
    (day / "rollout-main.jsonl").write_text(json.dumps(
        {"type": "event_msg", "payload": {"type": "user_message", "message": "codex work"}}) + "\n",
        encoding="utf-8")
    b = vboard(vlog)
    assert any(s["source"] == "codex" for s in b["live"])  # Codex is live, just not counted
    # Full id, upper case, with '#': still recognised as the supervisor.
    d = sb.dispatch(b, pr, commit_gb=10.0, not_counted=("#S5EEEEEE-1",))
    assert sorted(d["working"]) == ["s1aaaaaa", "s3cccccc"] and d["free"] == 2
    assert len(d["open_now"]) == 2


def test_blocked_row_unblocks_nothing_and_active_is_not_done():
    ws, stuck = sb.waves(_rows("A1:blocked:, A2:queued:A1, B1:active:, B2:queued:B1"))
    assert [[r["id"] for r in w] for w in ws] == [["A1", "B1"], ["B2"]]
    assert {r["id"]: r["why"] for r in stuck} == {"A2": "waits on a stuck or blocked row: A1"}


def test_a_cycle_is_named_and_healthy_rows_are_not_blamed():
    ws, stuck = sb.waves(_rows("C1:queued:C2, C2:queued:C1, D1:queued:, D2:queued:D1,C1"))
    why = {r["id"]: r["why"] for r in stuck}
    assert why["C1"] == "in a dependency cycle: C2"
    assert why["D2"] == "waits on a stuck or blocked row: C1"  # D1 is placed; not named
    assert [[r["id"] for r in w] for w in ws] == [["D1"]]


@pytest.mark.parametrize("task,gated", [
    ("ask [decision-Kevin] first", True), ("wait [blocked-human] on login", True),
    ("republish the page", True), ("purchase a licence", True), ("send the email to X", True),
    ("部署到 VPS", True), ("on Kevin's approval", True),
    ("refactor the parser", False), ("add a test for the resender", False),
])
def test_kevin_gate(task, gated):
    rows = _rows("X1:queued:")
    rows[0]["task"] = task
    assert sb._step("p", "t", rows[0], 1, set())["kevin"] is gated


def test_blocked_open_code_row_is_not_a_kevin_step():
    rows = _rows("X1:blocked:")
    rows[0]["task"] = "[open-code] needs the parser first"
    assert sb._step("p", "t", rows[0], 1, set())["kevin"] is False
    rows[0]["task"] = "needs a login"
    assert sb._step("p", "t", rows[0], 1, set())["kevin"] is True


def test_a_dead_claim_is_not_shown_as_running(vlog):
    b, pr = _dispatch_vault(vlog)
    plan(vlog, "pa", "| A1 | x | — | active | 2026-09-20 #dead0000 | |\n", track="ta")
    d = sb.dispatch(vboard(vlog), sb.open_plan_rows(vlog / "_plans"), commit_gb=10.0)
    out = sb.render_dispatch(d)
    assert "[claimed #dead0000, no live session]" in out and "[running" not in out


def test_broken_links_are_reported_not_turned_into_commands(vlog):
    b, pr = _dispatch_vault(vlog)
    project(vlog, "odd", "status: active\ntracks:\n  - '8'\nplans:\n  - nope")
    d = sb.dispatch(vboard(vlog), sb.open_plan_rows(vlog / "_plans"), commit_gb=10.0)
    out = sb.render_dispatch(d)
    assert "tracks: names '8', which has no brief in _next/" in out
    assert "plans: names 'nope', which is neither open nor in _plans/done/" in out
    assert "/eos-session-resume 8" not in out


def test_cli_takes_json_after_the_subcommand_and_refuses_an_unknown_project(vlog, monkeypatch, capsys):
    _dispatch_vault(vlog)
    monkeypatch.setattr(sb, "commit_charge_gb", lambda: 10.0)
    monkeypatch.setattr(sb.Path, "home", staticmethod(lambda: vlog / "home"))
    monkeypatch.setattr(sys, "argv", ["sb", "--log-dir", str(vlog), "dispatch", "--json"])
    assert sb.main() == 0
    assert json.loads(capsys.readouterr().out)["data"]["free"] == 4
    monkeypatch.setattr(sys, "argv", ["sb", "--log-dir", str(vlog), "dispatch", "--project", "nope"])
    with pytest.raises(SystemExit, match="no listed project named exactly 'nope'"):
        sb.main()


@pytest.mark.skipif(sys.platform != "win32", reason="GetPerformanceInfo is Windows-only")
def test_commit_charge_reads_a_plausible_number_on_windows():
    gb = sb.commit_charge_gb()
    assert gb is not None and 0.5 < gb < 4096


def test_a_live_track_is_never_offered_again(vlog):
    b, pr = _dispatch_vault(vlog, sessions=[("s1aaaaaa-1", 5, "/eos-session-resume tb")])
    d = sb.dispatch(b, pr, commit_gb=10.0)
    assert "tb" not in [c["track"] for c in d["open_now"]]


@pytest.mark.parametrize("gb,hold,free", [(95.0, True, 0), (90.0, True, 0), (89.9, False, 4), (None, False, 4)])
def test_memory_gate_holds_new_sessions(vlog, gb, hold, free):
    b, pr = _dispatch_vault(vlog)
    d = sb.dispatch(b, pr, commit_gb=gb)
    assert d["memory_hold"] is hold and d["free"] == free
    if gb is None:
        assert "Commit charge: unknown." in sb.render_dispatch(d)


def test_slots_only_ever_lower_the_free_count(vlog):
    b, pr = _dispatch_vault(vlog)
    assert sb.dispatch(b, pr, commit_gb=10.0, slots=1)["free"] == 1
    assert sb.dispatch(b, pr, commit_gb=10.0, slots=9)["free"] == 4


def test_project_filter_keeps_one_roadmap(vlog):
    b, pr = _dispatch_vault(vlog)
    d = sb.dispatch(b, pr, project="later", commit_gb=10.0)
    assert [e["project"] for e in d["roadmap"]] == ["later"]
    assert [c["id"] for c in d["open_now"]] == ["A1"]


def test_commit_charge_is_unknown_off_windows(monkeypatch):
    monkeypatch.setattr(sb.sys, "platform", "linux")
    assert sb.commit_charge_gb() is None


# ── P9: open sessions, first prompt past a big head ─────────────────────────


def test_first_prompt_found_past_a_large_queue_operation_line():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "s.jsonl"
        big = {"type": "queue-operation", "operation": "dequeue", "content": "x" * 400_000}
        user = {"type": "user", "message": {"content": sb.REVIEWER_PROMPT + "\n\nChanged files"}}
        f.write_text(json.dumps(big) + "\n" + json.dumps(user) + "\n", encoding="utf-8")
        assert sb._first_prompt(f).startswith(sb.REVIEWER_PROMPT)
        enq = {"type": "queue-operation", "operation": "enqueue", "content": sb.REVIEWER_PROMPT + " " + "y" * 300_000}
        f.write_text(json.dumps(enq) + "\n", encoding="utf-8")
        assert sb._first_prompt(f).startswith(sb.REVIEWER_PROMPT)


def _registry(home: Path, entries: list[dict]) -> None:
    reg = home / ".claude" / "sessions"
    reg.mkdir(parents=True, exist_ok=True)
    for i, e in enumerate(entries):
        (reg / f"{i}.json").write_text(json.dumps(e), encoding="utf-8")


def test_open_sessions_need_a_running_process_and_doubt_counts_as_open(log):
    me = os.getpid()
    started = sb._process_started(me)
    assert started is not None
    home = log / "home"
    # pid as an int, the way the real registry writes it.
    _registry(home, [
        {"pid": me, "sessionId": "aaaa1111-x", "status": "busy", "procStart": str(started),
         "entrypoint": "cli"},
        {"pid": 4000000000, "sessionId": "cccc3333-x", "status": "idle", "procStart": "1"},
        {"pid": me, "sessionId": "eeee5555-x", "status": "idle"},   # no procStart: live pid counts
        {"sessionId": "dddd4444-x"},                                 # no pid: unreadable -> open
    ])
    reg = home / ".claude" / "sessions"
    (reg / "junk.json").write_text("{not json", encoding="utf-8")   # drifted format -> open
    (reg / "bom.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"pid": me, "sessionId": "ffff6666-x", "status": "busy"}).encode())
    got = sb.open_claude_sessions(home)
    assert got["aaaa1111"]["status"] == "busy" and "cccc3333" not in got
    assert got["eeee5555"]["status"] == "idle" and got["ffff6666"]["status"] == "busy"
    assert sorted(v["status"] for k, v in got.items() if k.startswith("?")) == ["unreadable", "unreadable"]
    assert sb.open_claude_sessions(log / "nowhere") is None


@pytest.mark.skipif(sys.platform != "win32", reason="start-time and exit-code checks are Windows-only")
def test_a_reused_pid_and_an_exited_process_are_not_open(log):
    me = os.getpid()
    started = sb._process_started(me)
    assert started > 0  # the creation time must actually be read
    import subprocess as sp
    child = sp.Popen([sys.executable, "-c", "pass"])
    child.wait()  # exited, but this process still holds its handle
    assert sb._process_started(child.pid) is None
    home = log / "home"
    _registry(home, [{"pid": me, "sessionId": "bbbb2222-x", "status": "idle", "procStart": str(started + 1)}])
    assert sb.open_claude_sessions(home) == {}


def test_an_open_review_session_is_a_reviewer_whatever_its_age(log):
    home = log / "home"
    _registry(home, [{"pid": os.getpid(), "sessionId": "r1r1r1r1-full-id", "status": "busy"}])
    proj = home / ".claude" / "projects" / "D--x"
    proj.mkdir(parents=True)
    t = proj / "r1r1r1r1-full-id.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": sb.REVIEWER_PROMPT}}) + "\n",
                 encoding="utf-8")
    os.utime(t, (1, 1))  # far outside any live window
    assert sb.open_claude_sessions(home)["r1r1r1r1"]["reviewer"] is True


def test_first_prompt_stops_at_its_line_bound():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "s.jsonl"
        noise = json.dumps({"type": "summary"}) + "\n"
        user = json.dumps({"type": "user", "message": {"content": "late"}}) + "\n"
        f.write_text(noise * sb.FIRST_PROMPT_LINES + user, encoding="utf-8")
        assert sb._first_prompt(f) == ""
        f.write_text(noise * (sb.FIRST_PROMPT_LINES - 1) + user, encoding="utf-8")
        assert sb._first_prompt(f) == "late"


def test_wip_counts_open_sessions_not_touched_transcripts(vlog):
    b, pr = _dispatch_vault(vlog, sessions=[("s1aaaaaa-1", 5, "hi"), ("s2bbbbbb-1", 5, "hi")])
    reviewer = {"source": "claude", "session_id": "s4dddddd-1", "title": "r", "tracks": []}
    b["reviewers"].append(reviewer)
    opened = {"s1aaaaaa": {"status": "busy"}, "s9zzzzzz": {"status": "idle", "entrypoint": "claude-desktop"},
              "s4dddddd": {"status": "busy"}, "s5eeeeee": {"status": "idle"},
              "s6ffffff": {"status": "busy", "reviewer": True}}   # a review older than the window
    # s2 touched its transcript but has exited; s9 is open but idle past the window.
    d = sb.dispatch(b, pr, commit_gb=10.0, open_sessions=opened, not_counted=("s5eeeeee",))
    assert d["working"] == ["s1aaaaaa", "s9zzzzzz"] and d["count_basis"] == "open"
    assert ("Working Claude sessions (open): 2 of 4 (#s1aaaaaa busy, #s9zzzzzz idle desktop)"
            in sb.render_dispatch(d))
    # A claim held by an open session past the window is still held.
    plan(vlog, "pa", "| A1 | x | — | active | 2026-09-20 #a9a9a9a9 | |\n", track="ta")
    d = sb.dispatch(vboard(vlog), sb.open_plan_rows(vlog / "_plans"), commit_gb=10.0,
                    open_sessions={"a9a9a9a9": {"status": "idle"}})
    assert "[running #a9a9a9a9]" in sb.render_dispatch(d)
    d = sb.dispatch(b, pr, commit_gb=10.0, open_sessions=None)
    assert d["count_basis"] == "touched in window"
    assert "no session registry, so closed windows count too" in sb.render_dispatch(d)
