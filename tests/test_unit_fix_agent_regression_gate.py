"""Unit tests for the fix-agent regression merge gate.

The gate is the only step in the fix loop that produces evidence a bug is fixed:
py_compile proves the diff parses, and the dogfood persona that "verifies" a fix
was measured at 2% self-recurrence on an unchanged system (see the vault note
"2026-08-05 dogfood persona test-retest"). So the test must FAIL at the merge
base and PASS on the branch -- one-sided checks let a tautological test through.

The two-sided check is exercised against a REAL temporary git repo, because the
worktree/merge-base plumbing is where it would break, not the pure helpers.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/extension/dev/fix-agent"

pytestmark = pytest.mark.skipif(not APP.exists(), reason="fix-agent not installed")


def _mod():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "fa_t" not in sys.modules:
        pkg = types.ModuleType("fa_t")
        pkg.__path__ = [str(APP)]
        sys.modules["fa_t"] = pkg
    key = "fa_t.regression"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, APP / "regression.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[key] = m
        spec.loader.exec_module(m)
    return sys.modules[key]


# ------------------------------------------------------------ pure helpers


@pytest.mark.parametrize("path,expected", [
    ("tests/test_unit_thing.py", "daemon-free"),
    ("tests/test_sdk_thing.py", "daemon-free"),
    ("tests/test_sys_journal.py", "daemon-bound"),
    ("tests/test_dogfood_media.py", "daemon-bound"),
    ("tests/test_journeys.py", "daemon-bound"),
    ("tests/helpers.py", "not-a-test"),
    ("apps/public/core/task/app.py", "not-a-test"),
    ("emptyos/sdk/test_unit_nope.py", "not-a-test"),   # must live under tests/
    ("tests/test_unit_thing.txt", "not-a-test"),
])
def test_classify_test_path(path, expected):
    assert _mod().classify_test_path(path) == expected


def test_classify_handles_windows_separators():
    assert _mod().classify_test_path("tests\\test_unit_thing.py") == "daemon-free"


def test_select_gate_tests_buckets_a_diff():
    sel = _mod().select_gate_tests([
        "emptyos/sdk/base_app.py", "tests/test_unit_a.py",
        "tests/test_sys_b.py", "", "tests/test_sdk_c.py",
    ])
    assert sel["daemon_free"] == ["tests/test_unit_a.py", "tests/test_sdk_c.py"]
    assert sel["daemon_bound"] == ["tests/test_sys_b.py"]
    assert sel["other"] == ["emptyos/sdk/base_app.py"]


@pytest.mark.parametrize("rc,out,ok", [
    (0, "15 passed in 41.20s", True),
    (1, "2 failed, 3 passed in 1.02s", False),
    (0, "no tests ran in 0.01s", False),          # green rc but nothing ran
    (0, "collected 0 items", False),
    (1, "1 error in 0.30s", False),
    (0, "", False),                                # no summary at all
])
def test_summarise_pytest(rc, out, ok):
    assert _mod().summarise_pytest(rc, out)["ok"] is ok


def test_summarise_pytest_counts_and_never_raises():
    s = _mod().summarise_pytest(1, "3 failed, 7 passed, 2 skipped in 2s")
    assert (s["failed"], s["passed"], s["skipped"]) == (3, 7, 2)
    assert _mod().summarise_pytest(0, None)["ok"] is False


# ------------------------------------------------- two-sided gate, real git


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


class _Host:
    """Minimal stand-in for FixAgentApp — the gate only touches these."""

    def __init__(self, repo, data_dir, enabled=True):
        self.repo_root = repo
        self.data_dir = data_dir
        self._enabled = enabled
        m = _mod()
        self.run_regression_gate = m.run_regression_gate.__get__(self)
        self._regression_enabled = m._regression_enabled.__get__(self)

    def app_config(self, key, default=None):
        return self._enabled if key == "feature.regression-gate.enabled" else default


SOURCE_BUGGY = "def double(n):\n    return n + n if isinstance(n, int) else None\n"
SOURCE_FIXED = "def double(n):\n    return n * 2\n"

TEST_PINS = textwrap.dedent("""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mylib import double

    def test_double_handles_strings():
        assert double("ab") == "abab"
""")

TEST_TAUTOLOGY = textwrap.dedent("""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mylib import double

    def test_double_of_two():
        assert double(2) == 4
""")


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    _git(["init", "-b", "main"], r.parent) if False else None
    subprocess.run(["git", "init"], cwd=str(r), check=True, capture_output=True)
    _git(["symbolic-ref", "HEAD", "refs/heads/main"], r)
    _git(["config", "user.email", "t@t"], r)
    _git(["config", "user.name", "t"], r)
    (r / "mylib.py").write_text(SOURCE_BUGGY, encoding="utf-8")
    (r / "tests" / "__init__.py").write_text("", encoding="utf-8")
    _git(["add", "-A"], r)
    _git(["commit", "-m", "base"], r)
    return r


def _branch_with(repo, tmp_path, test_body, source):
    """Create a fix branch in a worktree: new source + a test file."""
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "fix/x", str(wt), "main"], repo)
    (wt / "mylib.py").write_text(source, encoding="utf-8")
    (wt / "tests" / "test_unit_double.py").write_text(test_body, encoding="utf-8")
    _git(["add", "-A"], wt)
    _git(["commit", "-m", "fix"], wt)
    return wt


def test_gate_passes_when_test_pins_the_bug(repo, tmp_path):
    wt = _branch_with(repo, tmp_path, TEST_PINS, SOURCE_FIXED)
    host = _Host(repo, tmp_path / "data")
    res = asyncio.run(host.run_regression_gate(wt))
    assert res["ok"] is True, res.get("reason")
    assert res["after"]["ok"] is True and res["before"]["ok"] is False


def test_gate_rejects_a_test_that_passes_without_the_fix(repo, tmp_path):
    """The tautology case — green at base, so it pins nothing."""
    wt = _branch_with(repo, tmp_path, TEST_TAUTOLOGY, SOURCE_FIXED)
    host = _Host(repo, tmp_path / "data")
    res = asyncio.run(host.run_regression_gate(wt))
    assert res["ok"] is False
    assert "WITHOUT the fix" in res["reason"]


def test_gate_rejects_when_the_test_fails_on_the_branch(repo, tmp_path):
    """Test pins the bug but the fix doesn't work."""
    wt = _branch_with(repo, tmp_path, TEST_PINS, SOURCE_BUGGY)
    host = _Host(repo, tmp_path / "data")
    res = asyncio.run(host.run_regression_gate(wt))
    assert res["ok"] is False
    assert "does not pass on the fix branch" in res["reason"]


def test_gate_rejects_a_diff_with_no_test(repo, tmp_path):
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "fix/y", str(wt), "main"], repo)
    (wt / "mylib.py").write_text(SOURCE_FIXED, encoding="utf-8")
    _git(["add", "-A"], wt)
    _git(["commit", "-m", "fix, no test"], wt)
    res = asyncio.run(_Host(repo, tmp_path / "data").run_regression_gate(wt))
    assert res["ok"] is False
    assert "no daemon-free regression test" in res["reason"]


def test_gate_names_daemon_bound_tests_as_not_counting(repo, tmp_path):
    """A test_sys_* file is not evidence — it would test main, not the worktree."""
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "fix/z", str(wt), "main"], repo)
    (wt / "mylib.py").write_text(SOURCE_FIXED, encoding="utf-8")
    (wt / "tests" / "test_sys_thing.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")
    _git(["add", "-A"], wt)
    _git(["commit", "-m", "fix with sys test"], wt)
    res = asyncio.run(_Host(repo, tmp_path / "data").run_regression_gate(wt))
    assert res["ok"] is False
    assert "daemon-backed test file" in res["reason"]


def test_base_worktree_is_cleaned_up(repo, tmp_path):
    wt = _branch_with(repo, tmp_path, TEST_PINS, SOURCE_FIXED)
    data = tmp_path / "data"
    asyncio.run(_Host(repo, data).run_regression_gate(wt))
    leftover = list((data / "regression-base").glob("*")) if (data / "regression-base").exists() else []
    assert leftover == [], f"base worktree not removed: {leftover}"


# ------------------------------------------------------------- the dark flag


def test_flag_defaults_off(tmp_path):
    class Bare:
        data_dir = tmp_path
        repo_root = tmp_path

        def setting_or_config(self, key, default=None, *, config_key=None):
            return default

    assert _mod()._regression_enabled(Bare()) is False


def test_flag_is_read_restart_free_with_the_toml_key_preserved(tmp_path):
    """Settings service (restart-free) is the read path; the TOML key must stay
    `feature.regression-gate.enabled` so scripts/check_dark_flags.py still sees it."""
    seen = {}

    class Bare:
        data_dir = tmp_path
        repo_root = tmp_path

        def setting_or_config(self, key, default=None, *, config_key=None):
            seen["key"], seen["config_key"] = key, config_key
            return True

    assert _mod()._regression_enabled(Bare()) is True
    assert seen["config_key"] == "feature.regression-gate.enabled"
    assert seen["key"] == "fix-agent.feature.regression-gate.enabled"


def test_prompt_section_only_appears_when_enabled():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "fa_t" not in sys.modules:
        _mod()
    spec = importlib.util.spec_from_file_location("fa_t.shared", APP / "shared.py")
    sh = importlib.util.module_from_spec(spec)
    sys.modules["fa_t.shared"] = sh
    spec.loader.exec_module(sh)

    off = sh._build_fix_system_prompt(1)
    on = sh._build_fix_system_prompt(1, regression=True)
    assert off == sh._FIX_SYSTEM_PROMPT, "flag off must be byte-identical to before"
    assert "REGRESSION TEST REQUIRED" in on
    # The base prompt bans pytest; the section must say it supersedes that.
    assert "SUPERSEDES" in on
    assert on.startswith(sh._FIX_SYSTEM_PROMPT)
