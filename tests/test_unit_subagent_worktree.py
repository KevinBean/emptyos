"""SubAgent worktree isolation — the flag-gated path.

Daemon-free. Covers the three things that run ONLY when
``[apps.agent] feature.subagent-worktree.enabled`` is true, and that had **zero**
coverage before 2026-08-28 (there was no subagent test file at all — which is why
the flag sat dark for 80 days with no cheap way to satisfy the "clean sandbox run"
condition ``docs/AGENT-HARNESS-COMPETITIVE-NOTES.md`` names as its gate):

  * the gate itself — ``isolate="worktree"`` must refuse while the flag is dark.
    That is the off-state guarantee the whole dark-flag convention rests on.
  * ``_setup_worktree`` — the reset -> clean -> ``checkout -B <branch> main``
    sequence mirrored from ``apps/extension/dev/fix-agent/runs.py``.
  * ``_capture_worktree_result`` — stage, **py_compile gate**, commit, diff
    summary. The py_compile branch is the one that matters: it is what stops a
    syntactically broken worktree being handed back as a reviewable branch.

``WorktreeApp`` (the path-redirection wrapper these hand off to) is pinned
separately in ``test_unit_agent_harness.py``; this file covers the tool side.

Every test builds its own throwaway git repo under ``tmp_path`` — nothing here
touches the live EmptyOS repo, spawns an agent, or spends a token.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from emptyos.sdk.agent_tools.subagent import SubAgentTool


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit on ``main``."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init"],
                   cwd=str(r), check=True, capture_output=True, text=True)
    _git(["config", "user.email", "t@example.com"], r)
    _git(["config", "user.name", "T"], r)
    (r / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(["add", "-A"], r)
    _git(["commit", "-m", "seed", "--no-verify"], r)
    return r


class _StubApp:
    """Minimal app surface the worktree path touches: repo_root + the flag."""

    def __init__(self, repo_root: Path, flag: bool = False):
        self.repo_root = repo_root
        self.kernel = _StubKernel(flag)


class _StubKernel:
    def __init__(self, flag: bool):
        self.config = _StubConfig(flag)


class _StubConfig:
    def __init__(self, flag: bool):
        self._flag = flag

    def get(self, key: str, default=None):
        if key == "apps.agent.feature.subagent-worktree.enabled":
            return self._flag
        return default


# --- the gate ---------------------------------------------------------------

def test_worktree_isolation_refused_while_flag_is_dark(repo: Path):
    """Off-state guarantee: the flag is what makes isolate='worktree' legal."""
    res = asyncio.run(SubAgentTool().run(
        _StubApp(repo, flag=False), task="anything", isolate="worktree"))
    assert res.ok is False
    assert "worktree isolation is disabled" in res.content
    # No worktree may be created by a refused call.
    assert not (repo / ".claude" / "worktrees").exists()


# --- setup ------------------------------------------------------------------

def test_setup_worktree_creates_branch_off_main(repo: Path):
    wrapped, wt, branch, err = SubAgentTool()._setup_worktree(_StubApp(repo), "r1")
    assert err is None, err
    assert branch == "subagent/r1"
    assert wt.exists() and (wt / "seed.txt").exists()
    # The wrapper redirects file tools into the worktree — that is the seam.
    assert Path(wrapped.repo_root) == wt
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=str(wt), capture_output=True, text=True).stdout.strip()
    assert head == "subagent/r1"
    # The main working tree is untouched by an isolated run.
    main_head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                               cwd=str(repo), capture_output=True, text=True).stdout.strip()
    assert main_head == "main"


def test_two_runs_get_independent_worktrees(repo: Path):
    """Fan-out precondition: git refuses one branch in two worktrees, so each
    run must get both its own dir and its own branch."""
    t = SubAgentTool()
    _a, wt1, b1, e1 = t._setup_worktree(_StubApp(repo), "r1")
    _b, wt2, b2, e2 = t._setup_worktree(_StubApp(repo), "r2")
    assert e1 is None and e2 is None, (e1, e2)
    assert wt1 != wt2 and b1 != b2


# --- capture ----------------------------------------------------------------

def test_capture_reports_no_changes_when_subagent_edited_nothing(repo: Path):
    t = SubAgentTool()
    _w, wt, branch, err = t._setup_worktree(_StubApp(repo), "r1")
    assert err is None
    meta = t._capture_worktree_result(wt, branch)
    assert meta["changed"] is False
    assert meta["commits"] == []
    assert meta["compile_ok"] is True


def test_capture_commits_edits_and_summarises_diff(repo: Path):
    t = SubAgentTool()
    _w, wt, branch, err = t._setup_worktree(_StubApp(repo), "r1")
    assert err is None
    (wt / "added.py").write_text("VALUE = 1\n", encoding="utf-8")
    meta = t._capture_worktree_result(wt, branch)
    assert meta["changed"] is True
    assert meta["compile_ok"] is True
    assert meta["commits"], "an isolated run must leave a reviewable commit"
    assert "added.py" in meta["diff_stat"]
    # ...and the commit landed on the branch, not on main.
    assert subprocess.run(["git", "rev-parse", "main"], cwd=str(repo),
                          capture_output=True, text=True).stdout.strip() != \
           subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(wt),
                          capture_output=True, text=True).stdout.strip()


def test_capture_py_compile_gate_flags_a_broken_file(repo: Path):
    """The gate that stops a syntactically broken branch being handed back."""
    t = SubAgentTool()
    _w, wt, branch, err = t._setup_worktree(_StubApp(repo), "r1")
    assert err is None
    (wt / "broken.py").write_text("def oops(\n", encoding="utf-8")
    meta = t._capture_worktree_result(wt, branch)
    assert meta["changed"] is True
    assert meta["compile_ok"] is False
    assert "compile_error" in meta and meta["compile_error"]


def test_capture_ignores_non_python_for_the_compile_gate(repo: Path):
    """A markdown-only change is a real change but nothing to compile."""
    t = SubAgentTool()
    _w, wt, branch, err = t._setup_worktree(_StubApp(repo), "r1")
    assert err is None
    (wt / "notes.md").write_text("# hi\n", encoding="utf-8")
    meta = t._capture_worktree_result(wt, branch)
    assert meta["changed"] is True
    assert meta["compile_ok"] is True


# --- the hand-off note the model actually sees ------------------------------

def test_worktree_note_surfaces_a_failed_compile():
    note = SubAgentTool._worktree_note(
        {"branch": "subagent/r1", "changed": True, "compile_ok": False,
         "diff_stat": " broken.py | 1 +"})
    assert "subagent/r1" in note
    assert "py_compile FAILED" in note


def test_worktree_note_states_when_nothing_changed():
    note = SubAgentTool._worktree_note({"branch": "subagent/r1", "changed": False})
    assert "no file changes" in note
