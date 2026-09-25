"""Unit tests for scripts/guard_adversarial_review.py — the commit review gate.

Pins BOTH directions (per `.claude/rules/audits.md`): the gate must fire on a
real unreviewed commit, and stay SILENT on every command that commits nothing.
The guard had no test file at all until 2026-09-02 — its siblings
`guard_git_safety` and `guard_daemon_safety` both had one — and two defects had
shipped behind that gap:

  1. `\\bcommit\\b` matched `commit-tree` and `commit-graph`, because `-` is a
     non-word character. Both update no ref; both were denied, and the denial
     told the operator to write a receipt attesting a diff they never touch.

  2. `cd <dir> && git commit` was not modelled at all, so the gate keyed on the
     PARENT index. That is the same defect the `git -C` handling was written to
     fix, still open on the more common spelling — and it failed OPEN, since the
     parent index is usually empty and `_receipt_state` reads an empty index as
     "nothing to attest to". `apps/personal` is a nested repo, so the hole sat
     on exactly the repo the `-C` fix existed for.

Layer 1 (`_is_commit`, `_cd_repo`) is pure and needs no git. Layer 2 drives
`main()` over a real throwaway repo, because "does the gate actually deny an
unreviewed staged diff" cannot be answered by a regex test — the first version
of defect 2 above was invisible to every pure check.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
# The hook imports `hook_common`, which resolves at runtime because Python puts a
# script's own dir on sys.path[0]. Under importlib that doesn't happen, so add it.
sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "guard_adversarial_review", _SCRIPTS / "guard_adversarial_review.py"
)
guard = importlib.util.module_from_spec(_SPEC)
sys.modules["guard_adversarial_review"] = guard
_SPEC.loader.exec_module(guard)


# ── Layer 1: which commands are a commit at all ─────────────────────────────

IS_COMMIT = [
    ("git commit -m x", True),
    ("git commit --amend --no-edit", True),
    ('git commit -F- <<MSG', True),
    ("git -c user.email=t@t commit -m x", True),
    ("git -C apps/personal commit -m x", True),
    ("cd apps/personal && git commit -m x", True),
    # Looks like a commit, changes nothing.
    ("git commit --dry-run", False),
    # Defect 1 — plumbing and maintenance, not a commit.
    ("git commit-tree $T -m base", False),
    ("git commit-graph write --reachable", False),
    # Never a commit.
    ("echo commit", False),
    ("git log --oneline -3", False),
    ("git commit_foo", False),
]


@pytest.mark.parametrize("cmd,expected", IS_COMMIT, ids=[c for c, _ in IS_COMMIT])
def test_is_commit(cmd, expected):
    assert guard._is_commit(cmd) is expected


# ── Layer 1: where does the commit land ─────────────────────────────────────

def test_cd_resolves_relative_to_the_project_root():
    assert guard._cd_repo("cd apps/personal && git commit -m x", "/repo") == os.path.join(
        "/repo", "apps/personal"
    )


def test_cd_absolute_is_used_as_is():
    assert guard._cd_repo("cd /other/repo && git commit -m x", "/repo") == "/other/repo"


def test_no_cd_is_none_not_unresolved():
    """None and UNRESOLVED_CD want opposite defaults — they must not collapse."""
    assert guard._cd_repo("git commit -m x", "/repo") is None


def test_last_cd_before_the_commit_wins():
    cmd = "cd a && cd b && git commit -m x"
    assert guard._cd_repo(cmd, "/repo") == os.path.join("/repo", "b")


def test_a_cd_after_the_commit_is_ignored():
    """Only the directory in force when git runs can affect the commit."""
    cmd = "git commit -m x && cd elsewhere"
    assert guard._cd_repo(cmd, "/repo") is None


@pytest.mark.parametrize(
    "raw",
    ['cd "$SP" && git commit -m x',
     "cd $HOME/w && git commit -m x",
     "cd `pwd`/x && git commit -m x",
     "cd build_*/ && git commit -m x",
     "cd ~/repo && git commit -m x"],
)
def test_unresolvable_cd_is_flagged_not_guessed(raw):
    assert guard._cd_repo(raw, "/repo") is guard.UNRESOLVED_CD


def test_cd_does_not_match_inside_a_commit_message():
    """`cd` is a common word; only a statement-boundary `cd` changes directory."""
    assert guard._cd_repo('git commit -m "cd into the dir first"', "/repo") is None


# ── Layer 2: end-to-end over a real repo ────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def staged_repo(tmp_path: Path) -> str:
    """A repo with one STAGED, unreviewed change — what the gate exists to catch."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", ".")
    (r / "a.txt").write_text("base\n", encoding="utf-8")
    _git(r, "add", "a.txt")
    tree = _git(r, "write-tree")
    # commit-tree, not commit: this suite must not depend on a committer identity
    # being configured, and plumbing keeps the fixture independent of user config.
    sha = _git(r, "commit-tree", tree, "-m", "base")
    _git(r, "update-ref", "refs/heads/main", sha)
    _git(r, "symbolic-ref", "HEAD", "refs/heads/main")
    (r / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    _git(r, "add", "a.txt")
    # Forward slashes on purpose. The hook splits with `shlex.split(posix=True)`,
    # which eats backslashes — so a native `C:\...` path arrives as `C:...` and
    # the repo does not resolve. That is the real behaviour of the Bash tool this
    # hook guards (Git Bash treats `\` as an escape too), and the unreadable-repo
    # case is pinned separately below rather than papered over here.
    # Returned as a str: `Path(...)` would re-normalise straight back to
    # backslashes on Windows, which is the bug this is avoiding.
    return r.as_posix()


def _decide(cmd: str, monkeypatch) -> str | None:
    """Run the hook's main() over `cmd`; return the deny reason, or None to allow."""
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})),
    )
    monkeypatch.delenv("EOS_SKIP_REVIEW_GATE", raising=False)
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    assert guard.main() == 0, "the hook must always exit 0 — it denies via stdout"
    out = buf.getvalue().strip()
    if not out:
        return None
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_unreviewed_staged_diff_is_denied(staged_repo, monkeypatch):
    reason = _decide(f"git -C {staged_repo} commit -m x", monkeypatch)
    assert reason is not None, "an unreviewed staged diff must not pass the gate"
    assert "1 staged path" in reason


def test_cd_form_keys_on_the_same_repo_as_the_dash_C_form(staged_repo, monkeypatch):
    """The regression: `cd <repo> && git commit` used to key on the PARENT index.

    Asserting only that it denies is not enough — it denied before too, whenever
    a parallel session happened to hold staged files, while attesting the wrong
    bytes. The KEY is what proves the repo was resolved, so both spellings must
    produce the identical key.
    """
    dash_c = _decide(f"git -C {staged_repo} commit -m x", monkeypatch)
    cd_form = _decide(f"cd {staged_repo} && git commit -m x", monkeypatch)
    assert dash_c is not None and cd_form is not None
    key = dash_c.split("key ")[1].split(",")[0]
    assert f"key {key}" in cd_form, (
        "the cd form resolved to a different repo than the -C form — it is keying "
        "on the parent index again"
    )


def test_dry_run_passes_even_with_a_staged_diff(staged_repo, monkeypatch):
    assert _decide(f"git -C {staged_repo} commit --dry-run -m x", monkeypatch) is None


@pytest.mark.parametrize("sub", ["commit-tree", "commit-graph"])
def test_commit_lookalikes_pass_with_a_staged_diff(staged_repo, monkeypatch, sub):
    """Defect 1, end-to-end: neither updates a ref, so neither may be gated."""
    assert _decide(f"git -C {staged_repo} {sub} write", monkeypatch) is None


def test_unresolvable_cd_denies_rather_than_keying_the_wrong_repo(monkeypatch):
    """The one place this hook is deliberately NOT fail-open.

    Failing open here does not mean "miss a receipt", it means attest a
    different repo's bytes — so an unreviewed commit rides in on somebody
    else's receipt.
    """
    reason = _decide('cd "$SOMEWHERE" && git commit -m x', monkeypatch)
    assert reason is not None
    assert "cannot tell which repo" in reason
    assert "git -C" in reason, "the denial must name the way out"


def test_a_named_repo_that_cannot_be_read_denies(monkeypatch):
    """Third defect, found by this suite on 2026-09-02.

    `_receipt_state` catches every exception and returns ok, so a `-C` path that
    did not resolve ALLOWED the commit — the gate's worst direction. Reachable
    without contrivance on Windows, where posix shlex strips the backslashes out
    of `C:\\path\\to\\repo`.
    """
    reason = _decide("git -C /no/such/repo/anywhere commit -m x", monkeypatch)
    assert reason is not None, "an unreadable named repo must not pass the gate"
    assert "cannot tell which repo" in reason


def test_skip_env_still_wins(staged_repo, monkeypatch):
    """The documented emergency escape must survive both fixes."""
    monkeypatch.setenv("EOS_SKIP_REVIEW_GATE", "1")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {staged_repo} commit -m x"},
        })),
    )
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    assert guard.main() == 0
    assert buf.getvalue().strip() == ""


def test_a_non_shell_tool_is_never_gated(monkeypatch):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({
            "tool_name": "Read", "tool_input": {"command": "git commit -m x"},
        })),
    )
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    assert guard.main() == 0
    assert buf.getvalue().strip() == ""
