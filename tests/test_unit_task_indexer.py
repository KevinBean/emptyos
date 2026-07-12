"""Unit tests for task indexer recency stamping (daemon-free).

The undated "No Due Date" group on the task page sorts by source-file mtime so
a freshly captured task surfaces at the top instead of being buried below the
row cap. These tests pin the wire contract (Task.to_dict carries mtime) and the
pure mtime-attach helper that feeds it.
"""

import os
import time

from apps.task.indexer import Task, attach_mtimes


def test_task_to_dict_includes_mtime():
    t = Task(text="capture me", done=False, file="10_Projects/inbox/inbox.md", line=42, mtime=123.5)
    d = t.to_dict()
    assert d["mtime"] == 123.5
    # Full wire shape the frontend consumes.
    assert set(d) >= {"text", "done", "file", "line", "due", "tier", "focus_score", "mtime"}


def test_task_mtime_defaults_to_zero():
    assert Task(text="x", done=False, file="f.md", line=1).to_dict()["mtime"] == 0.0


def test_attach_mtimes_stamps_real_file_mtime(tmp_path):
    notes = tmp_path
    (notes / "10_Projects" / "inbox").mkdir(parents=True)
    f = notes / "10_Projects" / "inbox" / "inbox.md"
    f.write_text("- [ ] new task", encoding="utf-8")
    known = time.time() - 100
    os.utime(f, (known, known))

    tasks = [{"file": "10_Projects/inbox/inbox.md", "line": 1, "due": ""}]
    attach_mtimes(tasks, notes)
    assert abs(tasks[0]["mtime"] - known) < 1.0


def test_attach_mtimes_missing_file_is_zero(tmp_path):
    tasks = [{"file": "nope/gone.md", "line": 1, "due": ""}]
    attach_mtimes(tasks, tmp_path)
    assert tasks[0]["mtime"] == 0.0


def test_attach_mtimes_no_notes_dir_defaults_zero():
    tasks = [{"file": "a.md", "line": 1}, {"file": "b.md", "line": 2}]
    attach_mtimes(tasks, None)
    assert all(t["mtime"] == 0.0 for t in tasks)


def test_attach_mtimes_stats_each_unique_file_once(tmp_path):
    f = tmp_path / "shared.md"
    f.write_text("- [ ] a\n- [ ] b", encoding="utf-8")
    tasks = [
        {"file": "shared.md", "line": 1, "due": ""},
        {"file": "shared.md", "line": 2, "due": ""},
    ]
    attach_mtimes(tasks, tmp_path)
    # Same file -> identical mtime on both tasks.
    assert tasks[0]["mtime"] == tasks[1]["mtime"] > 0


def test_undated_recency_sort_order():
    """Replicates the frontend undated sort: newest file mtime first, then
    later line first. A fresh capture (high mtime) must lead."""
    undated = [
        {"text": "old in archive", "file": "20_Areas/x.md", "line": 5, "mtime": 100.0},
        {"text": "fresh capture", "file": "10_Projects/inbox/inbox.md", "line": 999, "mtime": 5000.0},
        {"text": "same file earlier", "file": "10_Projects/inbox/inbox.md", "line": 3, "mtime": 5000.0},
    ]
    undated.sort(key=lambda t: (-t.get("mtime", 0), -t.get("line", 0)))
    assert undated[0]["text"] == "fresh capture"
    assert undated[1]["text"] == "same file earlier"
    assert undated[2]["text"] == "old in archive"
