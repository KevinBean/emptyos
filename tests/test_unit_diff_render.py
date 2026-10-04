"""Unit tests for the shared terminal diff renderer (P1.1).

Used by both eos rooms/code (pending cards) and eos chat (Edit/Write approvals).
Pure — no daemon.
"""

from __future__ import annotations

from emptyos.sdk.diff_render import render_diff_lines, unified_diff_lines


def test_unified_diff_marks_add_del_hunk():
    dl = unified_diff_lines("a\nb\nc\n", "a\nB\nc\n", path="x.py")
    kinds = {d["kind"] for d in dl}
    assert "add" in kinds
    assert "del" in kinds
    assert "hunk" in kinds
    # file-header lines (--- / +++) are dropped
    assert not any(d["text"].startswith(("---", "+++")) for d in dl)


def test_unified_diff_write_is_all_adds():
    dl = unified_diff_lines("", "new\nfile\n", path="n.py")
    assert dl  # non-empty
    assert all(d["kind"] in ("add", "hunk") for d in dl)


def test_unified_diff_identical_is_empty():
    assert unified_diff_lines("same\n", "same\n") == []


def test_render_colors_and_escapes():
    dl = [
        {"kind": "hunk", "text": "@@ -1 +1 @@"},
        {"kind": "del", "text": "-old [x]"},
        {"kind": "add", "text": "+new [y]"},
        {"kind": "ctx", "text": " ctx"},
    ]
    out = render_diff_lines(dl)
    assert "[green]" in out and "[red]" in out and "[cyan]" in out
    # literal brackets in diff text are escaped so Rich doesn't parse them as markup
    assert r"\[x]" in out and r"\[y]" in out


def test_render_truncates_long_diffs():
    dl = [{"kind": "add", "text": f"+line {i}"} for i in range(200)]
    out = render_diff_lines(dl, max_lines=60)
    assert "more diff line(s)" in out
