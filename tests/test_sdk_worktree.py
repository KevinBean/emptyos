"""Unit tests for emptyos.sdk.worktree.

Cover the three branches each helper actually has — happy path, git/process
failure, and the idempotent case for ensure_worktree. We don't try to
exercise the real subprocess invocation beyond what a temp git repo needs;
deeper integration is the responsibility of the calling apps' own tests.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from emptyos.sdk.worktree import ensure_worktree, git_run, py_compile_files


def _init_repo(repo: Path) -> None:
    """Make a minimal git repo with one commit so HEAD exists."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, cwd=str(repo.parent))
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)


def test_git_run_returns_status_and_streams(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    rc, out, err = git_run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    assert rc == 0
    assert out.strip() == "main"
    assert err == ""


def test_git_run_on_non_repo_returns_nonzero(tmp_path: Path) -> None:
    rc, _, err = git_run(["rev-parse", "HEAD"], cwd=tmp_path)
    assert rc != 0
    assert err  # git complains about "not a git repository"


def test_ensure_worktree_creates_then_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    wt = tmp_path / "wt" / "child"

    p1, err1 = ensure_worktree(repo, wt)
    assert err1 is None
    assert p1 == wt
    assert (wt / ".git").exists()
    assert (wt / "README.md").exists()

    # Second call must be a no-op (no error, same path, no double-add).
    p2, err2 = ensure_worktree(repo, wt)
    assert err2 is None
    assert p2 == wt


def test_ensure_worktree_failure_returns_error(tmp_path: Path) -> None:
    # `repo` is not a git repository → `git worktree add` fails.
    not_repo = tmp_path / "not_a_repo"
    not_repo.mkdir()
    wt = tmp_path / "wt"
    _, err = ensure_worktree(not_repo, wt)
    assert err is not None
    assert "git worktree add failed" in err


def test_py_compile_files_ok(tmp_path: Path) -> None:
    f = tmp_path / "good.py"
    f.write_text("x = 1\n", encoding="utf-8")
    ok, msg = py_compile_files([f.name], cwd=tmp_path)
    assert ok, msg
    assert msg == ""


def test_py_compile_files_syntax_error(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    ok, msg = py_compile_files([f.name], cwd=tmp_path)
    assert not ok
    assert "bad.py" in msg


def test_py_compile_files_missing_file(tmp_path: Path) -> None:
    ok, msg = py_compile_files(["does_not_exist.py"], cwd=tmp_path)
    assert not ok
    assert msg  # py_compile reports the missing file
