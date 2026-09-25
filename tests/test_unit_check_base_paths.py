"""check_base's git path helpers must return paths that exist, whatever the name.

Until 2026-09-25 they parsed `git ls-files` line by line with `text=True`. Git
C-quotes any path holding a non-ASCII byte, and Windows decodes stdout as cp1252,
so 26 tracked files — nine demo-vault album notes named with `·`, CJK and kana
among them — came back as paths that do not exist. Every scanner built on these
helpers (check-personal, check-branding, the UI scanners) opened nothing and
reported clean; the album notes carried 28 branding findings nobody saw.

Driven against a real throwaway repo rather than a mocked subprocess, because the
defect lives in git's output format, which a mock would have to restate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_base  # noqa: E402

NAMES = ["plain.md", "01 · 登入 (Log In).md", "ピクセルの体.md", "Sans Récursion.md"]


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for name in NAMES:
        (root / name).write_text("x\n", encoding="utf-8")
    subprocess.run([*git, "add", "--", *NAMES[:2]], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=root, check=True)
    subprocess.run([*git, "add", "--", NAMES[2]], cwd=root, check=True)  # staged
    return root  # NAMES[3] stays untracked


def test_tracked_paths_exist_for_non_ascii_names(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setattr(check_base, "REPO_ROOT", root)
    got = check_base.git_tracked()
    assert sorted(p.name for p in got) == sorted(NAMES[:3])
    assert all(p.exists() for p in got)


def test_staged_paths_exist_for_non_ascii_names(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setattr(check_base, "REPO_ROOT", root)
    got = check_base.git_staged()
    assert [p.name for p in got] == [NAMES[2]]
    assert got[0].exists()


def test_untracked_paths_exist_for_non_ascii_names(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setattr(check_base, "REPO_ROOT", root)
    got = check_base.git_untracked()
    assert [p.name for p in got] == [NAMES[3]]
    assert got[0].exists()
