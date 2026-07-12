"""Pure-logic tests for the dogfood-agent drain v2 helpers.

Covers the 2026-05-20 bundle (commit 1846d37):
- Per-prompt attempt budget (_bump/_clear/_attempt_count, _move_to_blocked)
- Pre-flight frontmatter-key dedup (_prompt_frontmatter_key, _dedupe_pending_by_key)
- Auto-stash push/pop wrappers (_git_repo, _auto_stash_push, _auto_stash_pop)

Doesn't require a running daemon. Loads the module directly and uses a
SimpleNamespace as a fake `self` with just the attributes the functions
read. For git-touching helpers, the test sets self.repo_root to a tmp_path
with `git init`.

Run: python -m pytest tests/test_sys_dogfood_agent_drain_v2.py -v
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def drain_module():
    """Load apps/dogfood-agent/drain.py as a standalone module.

    We need apps + apps.dogfood-agent registered as parent packages so the
    `from .app import DogfoodAgentApp` import inside the TYPE_CHECKING block
    doesn't matter (it's stringified by __future__ annotations), but the
    module-level `from emptyos.sdk import web_route` does need emptyos
    importable — pytest's rootdir adds it.
    """
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    from helpers import app_path
    dogfood_dir = app_path("dogfood-agent")

    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(repo_root / "apps")]
        sys.modules["apps"] = apps_pkg
    if "apps.dogfood-agent" not in sys.modules:
        df_pkg = types.ModuleType("apps.dogfood-agent")
        df_pkg.__path__ = [str(dogfood_dir)]
        sys.modules["apps.dogfood-agent"] = df_pkg

    spec = importlib.util.spec_from_file_location(
        "apps.dogfood-agent.drain", dogfood_dir / "drain.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apps.dogfood-agent.drain"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def FakeAppClass(drain_module):
    """A class that has every drain helper bound as a method so the
    cross-method `self.X()` calls inside the helpers resolve correctly.
    Same shape as the multi-module-apps rebinding pattern, just minus
    the rest of the DogfoodAgentApp class (we don't need scenarios,
    personas, friction etc. for these tests)."""
    bound = (
        "_attempts_path", "_load_attempts", "_save_attempts",
        "_bump_attempt", "_clear_attempt", "_attempt_count",
        "_blocked_dir", "_move_to_blocked",
        "_prompt_frontmatter_key", "_dedupe_pending_by_key",
        "_git_repo", "_auto_stash_push", "_auto_stash_pop",
        "_list_pending_fix_prompts",
    )
    cls = type("FakeDogfoodApp", (), {})
    for name in bound:
        setattr(cls, name, getattr(drain_module, name))
    return cls


@pytest.fixture
def fake_app(FakeAppClass, tmp_path):
    """Instance of FakeAppClass with `data_dir` (tmp) + `repo_root` (git-init'd).

    `_fix_prompts_dir` is the only method the drain helpers expect that
    isn't part of the v2 bundle itself — overridden as an instance
    attribute so callers don't drag in the rest of the friction module.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    prompts_dir = data_dir / "fix-prompts"
    prompts_dir.mkdir()

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # `git init` so stash commands resolve. -q to keep test output clean.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_root, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_root, check=True,
    )
    # Need at least one commit before we can stash anything.
    (repo_root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo_root, check=True)

    obj = FakeAppClass()
    obj.data_dir = data_dir
    obj.repo_root = repo_root
    obj._fix_prompts_dir = lambda: prompts_dir
    return obj


# ─── Attempt budget ──────────────────────────────────────────────────


class TestAttemptBudget:
    def test_initial_count_zero(self, drain_module, fake_app):
        assert drain_module._attempt_count(fake_app, "anything.md") == 0

    def test_bump_persists_and_returns_count(self, drain_module, fake_app):
        n = drain_module._bump_attempt(fake_app, "foo.md", "queue: boom")
        assert n == 1
        # Re-read from disk via _attempt_count.
        assert drain_module._attempt_count(fake_app, "foo.md") == 1
        # Second bump increments.
        assert drain_module._bump_attempt(fake_app, "foo.md", "merge: oops") == 2

    def test_bump_records_last_error_truncated(self, drain_module, fake_app):
        long_err = "x" * 500
        drain_module._bump_attempt(fake_app, "foo.md", long_err)
        record = drain_module._load_attempts(fake_app)["foo.md"]
        assert len(record["last_error"]) <= 200

    def test_clear_attempt_removes_record(self, drain_module, fake_app):
        drain_module._bump_attempt(fake_app, "foo.md", "")
        assert drain_module._attempt_count(fake_app, "foo.md") == 1
        drain_module._clear_attempt(fake_app, "foo.md")
        assert drain_module._attempt_count(fake_app, "foo.md") == 0

    def test_clear_attempt_idempotent_for_missing(self, drain_module, fake_app):
        # Should not raise.
        drain_module._clear_attempt(fake_app, "never-existed.md")

    def test_load_attempts_corrupt_returns_empty(self, drain_module, fake_app):
        p = drain_module._attempts_path(fake_app)
        p.write_text("not json", encoding="utf-8")
        assert drain_module._load_attempts(fake_app) == {}


class TestMoveToBlocked:
    def test_move_creates_blocked_dir_and_reason(self, drain_module, fake_app):
        prompt = fake_app._fix_prompts_dir() / "bug-foo.md"
        prompt.write_text("---\nkind: bug\n---\nbody", encoding="utf-8")

        drain_module._move_to_blocked(fake_app, prompt, "exceeded 2 attempts")

        blocked = drain_module._blocked_dir(fake_app)
        assert (blocked / "bug-foo.md").exists()
        reason = blocked / "bug-foo.reason.md"
        assert reason.exists()
        text = reason.read_text(encoding="utf-8")
        assert "exceeded 2 attempts" in text
        assert "bug-foo.md" in text  # filename mentioned in re-queue instructions

    def test_move_missing_prompt_no_raise(self, drain_module, fake_app):
        ghost = fake_app._fix_prompts_dir() / "ghost.md"
        # Don't create — function should swallow the rename failure.
        drain_module._move_to_blocked(fake_app, ghost, "test")


# ─── Frontmatter-key dedup ───────────────────────────────────────────


def _write_prompt(d: Path, name: str, key: str | None = None, body: str = "body\n"):
    fm = ["---", "kind: bug"]
    if key is not None:
        fm.append(f"key: {key}")
    fm.append("---")
    p = d / name
    p.write_text("\n".join(fm) + "\n" + body, encoding="utf-8")
    return p


class TestFrontmatterKey:
    def test_extracts_key(self, drain_module, fake_app, tmp_path):
        p = _write_prompt(tmp_path, "a.md", key="bug::same shape friction signature")
        assert drain_module._prompt_frontmatter_key(fake_app, p) == (
            "bug::same shape friction signature"
        )

    def test_missing_key_returns_none(self, drain_module, fake_app, tmp_path):
        p = _write_prompt(tmp_path, "b.md", key=None)
        assert drain_module._prompt_frontmatter_key(fake_app, p) is None

    def test_no_frontmatter_returns_none(self, drain_module, fake_app, tmp_path):
        p = tmp_path / "c.md"
        p.write_text("plain body, no frontmatter", encoding="utf-8")
        assert drain_module._prompt_frontmatter_key(fake_app, p) is None

    def test_quoted_key_stripped(self, drain_module, fake_app, tmp_path):
        p = _write_prompt(tmp_path, "d.md", key="'bug::with quotes'")
        assert drain_module._prompt_frontmatter_key(fake_app, p) == (
            "bug::with quotes"
        )


class TestDedupePendingByKey:
    def test_no_duplicates_returns_zero_deduped(self, drain_module, fake_app):
        d = fake_app._fix_prompts_dir()
        _write_prompt(d, "a-one.md", key="bug::alpha")
        _write_prompt(d, "b-two.md", key="bug::beta")
        res = drain_module._dedupe_pending_by_key(fake_app)
        assert res["deduped"] == 0
        # Both files still in queue.
        assert (d / "a-one.md").exists()
        assert (d / "b-two.md").exists()

    def test_duplicates_folded_oldest_kept(self, drain_module, fake_app):
        d = fake_app._fix_prompts_dir()
        # Sorted lexically — a comes before b, c.
        _write_prompt(d, "a-keep.md", key="bug::same friction")
        _write_prompt(d, "b-fold.md", key="bug::same friction")
        _write_prompt(d, "c-fold.md", key="bug::same friction")

        res = drain_module._dedupe_pending_by_key(fake_app)

        assert res["deduped"] == 2
        assert (d / "a-keep.md").exists()  # earliest filename kept
        assert not (d / "b-fold.md").exists()
        assert not (d / "c-fold.md").exists()
        done = d / "done"
        assert (done / "b-fold.md").exists()
        assert (done / "c-fold.md").exists()
        # An audit note was written.
        notes = list(done.glob("_dedupe-*.md"))
        assert len(notes) == 1
        assert "b-fold.md" in notes[0].read_text(encoding="utf-8")

    def test_no_key_prompts_pass_through(self, drain_module, fake_app):
        d = fake_app._fix_prompts_dir()
        _write_prompt(d, "no-key.md", key=None)
        _write_prompt(d, "also-no.md", key=None)
        res = drain_module._dedupe_pending_by_key(fake_app)
        assert res["deduped"] == 0
        assert res["no_key"] == 2

    def test_details_capped_at_ten(self, drain_module, fake_app):
        d = fake_app._fix_prompts_dir()
        _write_prompt(d, "a-keep.md", key="bug::flood")
        for i in range(15):
            _write_prompt(d, f"b-{i:02d}.md", key="bug::flood")
        res = drain_module._dedupe_pending_by_key(fake_app)
        assert res["deduped"] == 15
        assert len(res["details"]) == 10


# ─── Auto-stash roundtrip ────────────────────────────────────────────


class TestAutoStash:
    def test_push_empty_paths_noop(self, drain_module, fake_app):
        res = drain_module._auto_stash_push(fake_app, [], "label")
        assert res == {"ok": True, "msg": "label", "count": 0}

    def test_push_and_pop_roundtrip(self, drain_module, fake_app):
        # Modify the seed file so there's something to stash.
        readme = fake_app.repo_root / "README.md"
        readme.write_text("modified content\n", encoding="utf-8")

        push = drain_module._auto_stash_push(fake_app, ["README.md"], "round-1")
        assert push["ok"] is True
        assert push["count"] == 1
        msg = push["msg"]
        # File reverts to seed content while stashed.
        assert readme.read_text(encoding="utf-8") == "seed\n"

        pop = drain_module._auto_stash_pop(fake_app, msg)
        assert pop["ok"] is True
        assert readme.read_text(encoding="utf-8") == "modified content\n"

    def test_pop_missing_msg_returns_error(self, drain_module, fake_app):
        res = drain_module._auto_stash_pop(fake_app, "no-such-stash-msg")
        assert res["ok"] is False
        assert "no stash found" in res["error"]

    def test_pop_empty_msg_is_noop(self, drain_module, fake_app):
        res = drain_module._auto_stash_pop(fake_app, "")
        assert res == {"ok": True, "ref": None}

    def test_parse_overwrite_paths_extracts_tab_indented(self, drain_module):
        err = (
            "error: Your local changes to the following files would be "
            "overwritten by merge:\n"
            "\tapps/video-digest/app.py\n"
            "\tapps/video-digest/pages/index.html\n"
            "Please commit your changes or stash them before you merge.\n"
            "Aborting"
        )
        paths = drain_module._parse_overwrite_paths(err)
        assert paths == [
            "apps/video-digest/app.py",
            "apps/video-digest/pages/index.html",
        ]

    def test_parse_overwrite_paths_unrelated_error_returns_empty(self, drain_module):
        # Merge-conflict style: no "would be overwritten", no recovery attempt.
        err = "CONFLICT (content): Merge conflict in foo.py\nAborting"
        assert drain_module._parse_overwrite_paths(err) == []

    def test_pop_recovers_from_drain_time_clobber(self, drain_module, fake_app):
        """The bug from session 2026-05-20: user/parallel session writes to
        stashed paths during the drain. Auto-stash pop must park those edits
        in a recovery stash, then succeed."""
        readme = fake_app.repo_root / "README.md"
        # 1. Pre-drain dirty state.
        readme.write_text("pre-drain WIP\n", encoding="utf-8")
        push = drain_module._auto_stash_push(fake_app, ["README.md"], "drain-clobber")
        assert push["ok"] is True
        msg = push["msg"]
        # Tree is restored to seed after stash.
        assert readme.read_text(encoding="utf-8") == "seed\n"

        # 2. Drain runs; meanwhile something writes the file again.
        readme.write_text("drain-time edits from elsewhere\n", encoding="utf-8")

        # 3. Drain's finally clause pops — must succeed via recovery.
        pop = drain_module._auto_stash_pop(fake_app, msg)
        assert pop["ok"] is True, pop
        assert pop["ref"].startswith("stash@{")
        assert "recovery_stash_msg" in pop
        assert pop["recovery_stash_msg"].startswith("drain-recover-")
        assert pop["conflicting_paths"] == ["README.md"]
        # Pre-drain WIP is restored.
        assert readme.read_text(encoding="utf-8") == "pre-drain WIP\n"
        # Drain-time edits parked as the recovery stash.
        rc, listing, _ = drain_module._git_repo(fake_app, ["stash", "list"])
        assert rc == 0
        assert pop["recovery_stash_msg"] in listing


# ─── Integration shape: end-to-end of v2 features ────────────────────


class TestV2EndToEnd:
    def test_budget_exhaustion_into_blocked(self, drain_module, fake_app):
        """Simulate two failed attempts → third attempt would be over budget."""
        # File-state setup
        prompt = fake_app._fix_prompts_dir() / "stuck-prompt.md"
        prompt.write_text("---\nkind: bug\nkey: bug::stuck\n---\nbody", encoding="utf-8")

        # Two failed attempts
        drain_module._bump_attempt(fake_app, prompt.name, "merge: fail 1")
        drain_module._bump_attempt(fake_app, prompt.name, "merge: fail 2")
        assert drain_module._attempt_count(fake_app, prompt.name) == 2

        # Drain loop would call _move_to_blocked at >= max_attempts.
        drain_module._move_to_blocked(
            fake_app, prompt, "exceeded 2 drain attempts",
        )
        # Verify the prompt is gone from queue and reason exists.
        assert not prompt.exists()
        blocked = drain_module._blocked_dir(fake_app)
        assert (blocked / "stuck-prompt.md").exists()
        assert (blocked / "stuck-prompt.reason.md").exists()

    def test_dedup_clears_budget_for_kept_prompt(self, drain_module, fake_app):
        """When dedup folds siblings, the kept prompt's attempt count is unaffected."""
        d = fake_app._fix_prompts_dir()
        keep = _write_prompt(d, "a-keep.md", key="bug::dup-test")
        _write_prompt(d, "b-fold.md", key="bug::dup-test")

        # Imagine keep has 1 prior attempt.
        drain_module._bump_attempt(fake_app, keep.name, "")
        assert drain_module._attempt_count(fake_app, keep.name) == 1

        drain_module._dedupe_pending_by_key(fake_app)

        # keep stays; its counter is unchanged.
        assert keep.exists()
        assert drain_module._attempt_count(fake_app, keep.name) == 1
