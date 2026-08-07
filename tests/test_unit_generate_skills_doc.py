"""Both-direction pins for the gitignore filter in generate_skills_doc.py.

`docs/SKILLS.md` is tracked, so a gitignored skill rendered into it publishes
that skill's name, description and trigger phrases. The module already applies
this reasoning to the user-global store; the project roots went uncovered until
2026-08-07, when four skills ignored at `.gitignore:74-77` were found in the
committed doc.

This needs a test rather than trusting the gate, because every way of getting
`git check-ignore` wrong fails *silently* — it answers "not ignored" (or, worse,
a plausible wrong set) and the private skill is quietly documented again.

SCOPE, stated honestly: these pin the *contract* — ignored is detected, tracked
and untracked are both kept, a missing git fails open, an empty list makes no
call. They do NOT pin the choice to query `SKILL.md` rather than the directory.
That defect (a trailing slash spuriously matching untracked dirs against a blank
`.gitignore` line) reproduces in the real tree but not in a minimal fixture —
mutation-checking the directory form against these tests passes, so the fixture
cannot see it. The evidence for that choice lives in the real repo, not here.
Don't read these four greens as covering it.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_skills_doc.py"


def _load():
    spec = importlib.util.spec_from_file_location("generate_skills_doc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "skills").mkdir()
    for name in ("public-one", "private-one", "untracked-one"):
        d = tmp_path / "skills" / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    # Directory-only pattern, exactly the shape the real .gitignore uses.
    (tmp_path / ".gitignore").write_text("skills/private-one/\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "skills/public-one")
    return tmp_path


def _dirs(repo: Path) -> list[Path]:
    return sorted((repo / "skills").iterdir())


class TestGitignoreFilter:
    def test_ignored_skill_is_detected(self, repo, monkeypatch):
        mod = _load()
        monkeypatch.setattr(mod, "ROOT", repo)
        assert {p.name for p in mod._gitignored(_dirs(repo))} == {"private-one"}

    def test_tracked_and_untracked_skills_are_kept(self, repo, monkeypatch):
        """Untracked is not private — a skill someone has not committed YET must
        stay in the doc, or the matrix flaps with every in-progress skill."""
        mod = _load()
        monkeypatch.setattr(mod, "ROOT", repo)
        kept = {p.name for p in mod._skill_dirs(repo / "skills")}
        assert kept == {"public-one", "untracked-one"}

    def test_no_git_available_renders_everything(self, repo, monkeypatch):
        """Fail open. A missing git must not silently empty the matrix."""
        mod = _load()
        monkeypatch.setattr(mod, "ROOT", repo)

        def boom(*a, **kw):
            raise OSError("git not found")

        monkeypatch.setattr(mod.subprocess, "run", boom)
        assert mod._gitignored(_dirs(repo)) == set()

    def test_empty_input_makes_no_subprocess_call(self, repo, monkeypatch):
        mod = _load()
        monkeypatch.setattr(mod, "ROOT", repo)
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda *a, **kw: pytest.fail("called git for an empty path list"),
        )
        assert mod._gitignored([]) == set()
