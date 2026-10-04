"""scripts/triage_inbox.py — producer groups, human hints, journal routines, the
dry run writing only its report, and `--apply` moving machine lines to the
archive without losing or duplicating a line.

Daemon-free: each case builds its own inbox text or a throwaway vault.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import session_board as sb  # noqa: E402
import triage_inbox as ti  # noqa: E402

TODAY = datetime.date(2026, 9, 28)

INBOX = [
    "---", "kind: area-home", "---", "# Inbox", "## Tasks",
    "- [ ] [missing] no preset (run 20260508T134934-97f497) #dogfood-bug",
    "- [ ] [JOB SCOUT] No fresh fits tonight; sources checked: Seek",
    "- [ ] [JOB] Senior Engineer at Grid Co (Sydney) — $150k — Builds tools — https://example.com/job/1",
    "- [ ] [JOB] Data Lead at Acme (Sydney) — $160k — Pipelines — https://example.com/job/...",
    "- [ ] 🌱 Growth (ESCALATION): aged item",
    "- [ ] 🕸️ Connect: wire the orphan",
    "- [ ] Buy milk",
    "- [ ] Buy  milk",
    "- [ ] Book the GP 📅 2026-08-08",
    "- [ ] Renew licence 📅 2026-10-30",
    "- [ ] phase1 dispatcher smoke test",
    "- [ ] Write up the dogfooding notes",
    "- [x] Call mom ✅ 2026-06-21",
    "- [-] dropped thing",
    "plain prose, not a task",
    "- [ ] Pay rent 📅 2026-09-28",                                   # 21: due today
    "- [ ] Stand up 🔁 every day 📅 2026-06-17",                      # 22: recurring
    "- [ ] test the narration slot",                                  # 23
    "- [ ] verify the hold gates this action",                        # 24
    "- [/] half-done errand",                                         # 25: open, not closed
    "- [ ] [JOB] Ops Lead at Beta (Sydney) — $1 — x — https://example.com/j/…",  # 26
    "  - [ ] 🌱 Growth: nested proposal",                              # 27: indented
]


def by_line(groups: dict) -> dict[int, tuple[str, dict]]:
    return {r["line"]: (g, r) for g, rows in groups.items() for r in rows}


@pytest.fixture
def rows():
    return by_line(ti.triage(INBOX, TODAY))


def test_every_task_line_lands_in_exactly_one_group(rows):
    task_lines = [n for n, ln in enumerate(INBOX, 1) if ti.TASK.match(ln)]
    assert sorted(rows) == task_lines


@pytest.mark.parametrize("line, group", [
    (6, "dogfood"), (7, "job-scout-empty"), (8, "job-lead"), (26, "job-lead"),
    (10, "staff"), (11, "staff"), (18, "closed"), (19, "closed"),
])
def test_producer_groups(rows, line, group):
    assert rows[line][0] == group


def test_a_human_line_mentioning_dogfood_stays_human(rows):
    assert rows[17][0] == "human"


def test_human_hints(rows):
    hints = {n: r["hints"] for n, (g, r) in rows.items() if g == "human"}
    assert hints[12] == []
    assert hints[13] == ["duplicate of line 12"]      # whitespace-insensitive
    assert hints[14] == ["past-due"]
    assert hints[15] == []                            # due in the future
    assert hints[16] == ["test-capture"]
    assert hints[21] == []                            # due today is not past
    assert hints[22] == []                            # 📅 on a 🔁 task is the next occurrence
    assert hints[23] == ["test-capture"]
    assert hints[24] == ["test-capture"]
    assert hints[25] == []


def test_an_open_mark_other_than_space_is_not_closed(rows):
    assert rows[25][0] == "human"


@pytest.mark.parametrize("text", [
    "Plant seeds — 🌿 Root: check drainage",       # staff marker only at the start
    "Tag this #dogfooding for later",               # #dogfood must end at a word boundary
    "Ask about the [job] offer letter",
])
def test_human_text_carrying_a_marker_mid_line_stays_human(text):
    assert ti.classify(text) is None
    assert not ti.MACHINE_LINE.search(text)


def test_session_board_reads_the_same_machine_markers():
    assert sb.MACHINE_LINE is ti.MACHINE_LINE
    for line in INBOX:
        m = ti.TASK.match(line)
        if m and m.group(1) == " ":
            assert bool(ti.MACHINE_LINE.search(m.group(2))) is (ti.classify(m.group(2)) is not None)


def _vault(tmp: Path) -> Path:
    (tmp / "10_Projects/inbox").mkdir(parents=True)
    (tmp / ti.INBOX).write_text("\n".join(INBOX), encoding="utf-8")
    j = tmp / "50_Journal/2026"
    j.mkdir(parents=True)
    (j / "2026-04.md").write_text(
        "- [ ] Check immiaccount weekly 🔁 every week 📅 2026-04-03\n"
        "- [x] Check immiaccount weekly 🔁 every week ✅ 2026-04-01\n"
        "- [ ] Buy bread\n", encoding="utf-8")
    (j / "2026-05.md").write_text(
        "- [ ] Check immiaccount weekly 🔁 every week 📅 2026-05-01\n", encoding="utf-8")
    return tmp


def test_journal_routines_group_open_recurring_tasks_across_files():
    with tempfile.TemporaryDirectory() as d:
        routines = ti.journal_routines(_vault(Path(d)))
    assert routines == [{
        "text": "Check immiaccount weekly 🔁 every week", "count": 2,
        "files": ["50_Journal/2026/2026-04.md", "50_Journal/2026/2026-05.md"],
    }]


def test_dry_run_writes_only_the_report(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        report = vault / "out/report.md"
        monkeypatch.setenv("EOS_VAULT", str(vault))
        assert ti.main(["--report", str(report)]) == 0
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file() and p != report}
        text = report.read_text(encoding="utf-8")
    assert after == before
    assert "| human | 11 | 1 | stays — triage with Kevin |" in text
    assert "| 16 | phase1 dispatcher smoke test | test-capture |" in text
    assert "dogfood" in capsys.readouterr().out


def test_missing_inbox_is_an_error_not_an_empty_triage(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("EOS_VAULT", d)
        assert ti.main([]) == 1


MACHINE_LINES = [6, 7, 8, 9, 10, 11, 18, 19, 26, 27]


def _apply(vault: Path, groups: list[str]) -> dict:
    return ti.apply(vault, groups, TODAY)


def test_apply_moves_machine_lines_verbatim_and_keeps_every_other_line():
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        moved = _apply(vault, list(ti.MACHINE_GROUPS))
        inbox = (vault / ti.INBOX).read_text(encoding="utf-8").splitlines()
        archive = (vault / ti.ARCHIVE).read_text(encoding="utf-8")
    assert moved == {"dogfood": 1, "job-scout-empty": 1, "job-lead": 3, "staff": 3, "closed": 2}
    assert inbox == [ln for n, ln in enumerate(INBOX, 1) if n not in MACHINE_LINES]
    body = archive[archive.index("\n## "):]
    archived = [ln.replace("- \\[ \\] ", "- [ ] ")
                for ln in body.splitlines() if ln.lstrip().startswith("- ")]
    assert sorted(archived) == sorted(INBOX[n - 1] for n in MACHINE_LINES)


def test_archive_line_escapes_open_checkboxes_exactly_and_keeps_closed_ones():
    assert ti.archive_line("- [ ] a #dogfood") == "- \\[ \\] a #dogfood"
    assert ti.archive_line("  - [ ] 🌱 Growth: b") == "  - \\[ \\] 🌱 Growth: b"
    assert ti.archive_line("- [x] Call mom ✅ 2026-06-21") == "- [x] Call mom ✅ 2026-06-21"
    assert ti.archive_line("- [-] dropped") == "- [-] dropped"


def test_split_lines_breaks_on_newline_only():
    assert ti.split_lines("a\x0cb\r\nc\u2028d\n") == ["a\x0cb\r\n", "c\u2028d\n"]


def test_archived_lines_are_no_longer_tasks():
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        _apply(vault, ["dogfood", "closed"])
        archive = (vault / ti.ARCHIVE).read_text(encoding="utf-8")
    assert "#dogfood-bug" in archive and "Call mom" in archive
    tasks = [ti.TASK.match(ln) for ln in archive.splitlines()]
    assert sorted(m.group(1) for m in tasks if m) == ["-", "x"]   # no open task left


def test_apply_only_moves_the_named_groups():
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        assert _apply(vault, ["staff"]) == {"staff": 3}
        inbox = (vault / ti.INBOX).read_text(encoding="utf-8")
    assert "Growth" not in inbox and "Connect" not in inbox
    assert "#dogfood-bug" in inbox and "[JOB]" in inbox


def test_apply_appends_a_second_run_below_the_first():
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        _apply(vault, ["staff"])
        _apply(vault, ["dogfood"])
        archive = (vault / ti.ARCHIVE).read_text(encoding="utf-8")
    assert archive.count("# Inbox triage archive") == 1
    assert archive.index("### staff (3)") < archive.index("### dogfood (1)")


@pytest.mark.parametrize("groups", [["human"], ["dogfood", "nonsense"]])
def test_apply_refuses_human_and_unknown_groups_and_writes_nothing(groups):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        before = (vault / ti.INBOX).read_bytes()
        with pytest.raises(ValueError):
            _apply(vault, groups)
        assert (vault / ti.INBOX).read_bytes() == before
        assert not (vault / ti.ARCHIVE).exists()


def test_apply_preserves_crlf_line_endings():
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        (vault / ti.INBOX).write_bytes("\r\n".join(INBOX).encode("utf-8") + b"\r\n")
        _apply(vault, ["dogfood"])
        raw = (vault / ti.INBOX).read_bytes()
    assert b"#dogfood" not in raw
    assert raw.count(b"\r\n") == len(INBOX) - 1 and b"\n" not in raw.replace(b"\r\n", b"")


def test_apply_aborts_without_touching_the_inbox_if_it_changed(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        inbox = vault / ti.INBOX
        real_write = ti.atomic_write_text

        def write_then_capture(path, content):   # a capture lands mid-apply
            real_write(path, content)
            if Path(path) == vault / ti.ARCHIVE:
                inbox.write_bytes(inbox.read_bytes() + b"\n- [ ] new capture")
        monkeypatch.setattr(ti, "atomic_write_text", write_then_capture)
        with pytest.raises(ti.InboxChanged):
            _apply(vault, ["dogfood"])
        text = inbox.read_text(encoding="utf-8")
    assert "#dogfood-bug" in text and "new capture" in text


def test_main_apply_exit_codes(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        monkeypatch.setenv("EOS_VAULT", str(vault))
        assert ti.main(["--apply", "human"]) == 1
        assert ti.main(["--apply", ","]) == 1          # no group named is a refusal
        assert ti.main(["--apply", ""]) == 1           # not a silent dry run
        assert ti.main(["--apply", "dogfood,staff"]) == 0
        assert "#dogfood" not in (vault / ti.INBOX).read_text(encoding="utf-8")


def test_main_apply_json_envelopes(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        monkeypatch.setenv("EOS_VAULT", str(vault))
        assert ti.main(["--apply", "human", "--json"]) == 1
        refused = json.loads(capsys.readouterr().out)
        assert ti.main(["--apply", "staff", "--json"]) == 0
        done = json.loads(capsys.readouterr().out)
    assert (refused["ok"], refused["code"]) == (False, "apply_refused")
    assert (done["ok"], done["data"]) == (True, {"staff": 3})


def _race(monkeypatch, vault: Path, when: Path, change) -> None:
    """Run `change()` right after atomic_write_text writes `when`."""
    real_write = ti.atomic_write_text

    def write_then(path, content):
        real_write(path, content)
        if Path(path) == when:
            change()
    monkeypatch.setattr(ti, "atomic_write_text", write_then)


def test_inbox_changed_before_the_move_writes_nothing(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        inbox = vault / ti.INBOX
        real_read = Path.read_bytes
        calls = {"n": 0}

        def read_then_capture(self):             # capture lands after the first read
            data = real_read(self)
            if self == inbox:
                calls["n"] += 1
                if calls["n"] == 1:
                    inbox.write_bytes(data + b"\n- [ ] new capture")
            return data
        monkeypatch.setattr(Path, "read_bytes", read_then_capture)
        with pytest.raises(ti.InboxChanged):
            _apply(vault, ["dogfood"])
        monkeypatch.undo()
        assert not (vault / ti.ARCHIVE).exists()
        assert "#dogfood-bug" in inbox.read_text(encoding="utf-8")


def test_an_archive_edit_during_apply_is_not_overwritten(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        _apply(vault, ["staff"])
        archive = vault / ti.ARCHIVE
        real_read = Path.read_bytes
        calls = {"n": 0}

        def read_then_edit(self):
            data = real_read(self)
            if self == archive:
                calls["n"] += 1
                if calls["n"] == 1:
                    archive.write_bytes(data + b"\nKevin's note")
            return data
        monkeypatch.setattr(Path, "read_bytes", read_then_edit)
        with pytest.raises(ti.InboxChanged):
            _apply(vault, ["dogfood"])
        monkeypatch.undo()
        assert archive.read_text(encoding="utf-8").endswith("Kevin's note")


def test_a_stale_copy_written_back_after_the_move_is_reported(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        inbox = vault / ti.INBOX
        original = inbox.read_bytes()
        _race(monkeypatch, vault, inbox, lambda: inbox.write_bytes(original + b"\n- [ ] x"))
        with pytest.raises(ti.InboxChanged):
            _apply(vault, ["dogfood"])


def test_an_os_error_mid_apply_exits_1_with_a_recovery_hint(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        monkeypatch.setenv("EOS_VAULT", str(vault))
        inbox = vault / ti.INBOX

        def locked():
            raise PermissionError("file in use")
        _race(monkeypatch, vault, vault / ti.ARCHIVE, locked)
        assert ti.main(["--apply", "dogfood"]) == 1
        assert "remove that section" in capsys.readouterr().err
        assert "#dogfood-bug" in inbox.read_text(encoding="utf-8")


def test_invalid_utf8_is_refused_not_a_traceback(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        (vault / ti.INBOX).write_bytes(b"- [ ] bad \xff byte #dogfood\n")
        monkeypatch.setenv("EOS_VAULT", str(vault))
        assert ti.main(["--apply", "dogfood"]) == 1
        assert not (vault / ti.ARCHIVE).exists()
    assert "inbox is not valid UTF-8" in capsys.readouterr().err


def test_a_form_feed_inside_a_line_moves_the_whole_line():
    with tempfile.TemporaryDirectory() as d:
        vault = _vault(Path(d))
        (vault / ti.INBOX).write_text("- [ ] keep\n- [ ] a\x0c- [ ] tail #dogfood\n", encoding="utf-8")
        _apply(vault, ["dogfood"])
        assert (vault / ti.INBOX).read_text(encoding="utf-8") == "- [ ] keep\n"
