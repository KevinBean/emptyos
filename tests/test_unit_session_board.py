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
