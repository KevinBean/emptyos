"""scripts/release.py::snapshot_worktree copies files whatever their names.

It listed files by parsing `git ls-files` text output, where git C-quotes any
non-ASCII path and Windows decodes as cp1252. The quoted name did not exist, and
the copy loop's `is_file()` check skipped it without a word: measured 2026-09-25,
18 tracked files (demo-vault album notes and audio among them) were missing from
every dist built on this machine. Same defect as check_base (see
tests/test_unit_check_base_paths.py).

Driven through `snapshot_worktree` against a real throwaway repo, because the
defect is git's output format and the silent skip in the caller.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.py"
_spec = importlib.util.spec_from_file_location("release_snap_paths", SCRIPT)
rel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rel)

TRACKED = ["plain.md", "01 · 登入 (Log In).md", "ピクセルの体.md"]
UNTRACKED = ["Sans Récursion.md"]


def test_snapshot_copies_non_ascii_names(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for name in TRACKED + UNTRACKED:
        (root / name).write_text(name + "\n", encoding="utf-8")
    subprocess.run([*git, "add", "--", *TRACKED], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=root, check=True)

    monkeypatch.setattr(rel, "ROOT", root)
    monkeypatch.setattr(rel, "_PRUNE_PATHS", [])
    out = tmp_path / "snap"
    out.mkdir()
    rel.snapshot_worktree(out)

    copied = sorted(p.name for p in out.rglob("*") if p.is_file())
    assert copied == sorted(TRACKED + UNTRACKED)
    assert (out / TRACKED[1]).read_text(encoding="utf-8") == TRACKED[1] + "\n"


def test_failed_listing_aborts_instead_of_empty_snapshot(tmp_path, monkeypatch):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr(rel, "ROOT", not_a_repo)
    try:
        rel._git_lines("ls-files")
    except SystemExit as e:
        assert e.code != 0
    else:
        raise AssertionError("a failed git listing must abort, not return []")
