"""Unit tests — task index hygiene (gap 4/5 fixes).

Covers: the local-folder scanner's template/skill exclusion (the "312
overdue, mostly boilerplate" fix), the projects get_all_tasks archived-dir
default, and the voice add double-submit guard. No daemon — modules loaded
with a registered parent package (indexer/voice use relative imports).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_TASK_DIR = _ROOT / "apps/public/core/task"
_AGG_PATH = _ROOT / "apps/public/standard/projects/aggregations.py"


@pytest.fixture(scope="module")
def task_pkg():
    """Load task queries/indexer/voice under a registered parent package so
    their `from . import queries`-style imports resolve."""
    pkg = types.ModuleType("taskpkg_under_test")
    pkg.__path__ = [str(_TASK_DIR)]
    sys.modules["taskpkg_under_test"] = pkg
    mods = {}
    for sub in ("queries", "indexer", "voice"):
        spec = importlib.util.spec_from_file_location(
            f"taskpkg_under_test.{sub}", _TASK_DIR / f"{sub}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"taskpkg_under_test.{sub}"] = mod
        spec.loader.exec_module(mod)
        mods[sub] = mod
    return mods


class StubTaskApp:
    def __init__(self, notes: Path, cfg: dict | None = None):
        self.kernel = types.SimpleNamespace(
            config=types.SimpleNamespace(notes_path=notes)
        )
        self.data_dir = notes / "_data"
        self._cfg = cfg or {}
        self._recent_adds = []

    def vault_config(self, key, default=""):
        return self._cfg.get(key, default)


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestScanExclude:
    def test_template_and_skill_dirs_skipped(self, task_pkg, tmp_path, ):
        notes = tmp_path / "vault"
        _write(notes / "00_Inbox/real.md", "- [ ] real task\n")
        _write(notes / "20_Areas/Career/notes.md", "- [ ] another real one\n")
        # Boilerplate that used to pollute the index:
        _write(notes / "20_Areas/templates/weekly.md", "- [ ] example item\n- [ ] second example\n")
        _write(notes / "00_Inbox/.claude/skills/foo/SKILL.md", "- [ ] template task\n")
        _write(notes / "20_Areas/skills/pack/guide.md", "- [ ] drill example\n")

        idx = task_pkg["indexer"].TaskIndexer(StubTaskApp(notes))
        tasks = idx._scan_local_folders()
        texts = {t["text"] for t in tasks}
        assert texts == {"real task", "another real one"}

    def test_exclusion_user_tunable(self, task_pkg, tmp_path):
        notes = tmp_path / "vault"
        _write(notes / "00_Inbox/sub/keep.md", "- [ ] keep me\n")
        _write(notes / "00_Inbox/noise/skip.md", "- [ ] skip me\n")
        idx = task_pkg["indexer"].TaskIndexer(
            StubTaskApp(notes, {"scan_exclude": "noise"})
        )
        texts = {t["text"] for t in idx._scan_local_folders()}
        assert texts == {"keep me"}


class TestArchivedDefault:
    @pytest.fixture(scope="class")
    def agg(self):
        spec = importlib.util.spec_from_file_location("projects_agg_under_test", _AGG_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    class StubProjects:
        def __init__(self, proj: Path, arch: Path):
            self.vault_root = proj.parent
            self._p, self._a = proj, arch
            self.visited: list[Path] = []

        def _projects_dir(self):
            return self._p

        def _archive_dir(self):
            return self._a

        def _iter_project_files(self, directory):
            self.visited.append(directory)
            return iter(())

    def test_archive_excluded_by_default(self, agg, tmp_path):
        proj, arch = tmp_path / "10_Projects", tmp_path / "40_Archive"
        proj.mkdir(), arch.mkdir()
        stub = self.StubProjects(proj, arch)
        asyncio.run(agg.get_all_tasks(stub))
        assert stub.visited == [proj]

    def test_archive_included_on_request(self, agg, tmp_path):
        proj, arch = tmp_path / "10_Projects", tmp_path / "40_Archive"
        proj.mkdir(), arch.mkdir()
        stub = self.StubProjects(proj, arch)
        asyncio.run(agg.get_all_tasks(stub, include_archived=True))
        assert stub.visited == [proj, arch]


class TestVoiceAddDedupe:
    def test_recent_identical_add_detected(self, task_pkg):
        voice = task_pkg["voice"]
        app = StubTaskApp(Path("."))
        app._recent_adds = [
            {"text": "Call mom", "ts": datetime.now().isoformat(timespec="seconds")}
        ]
        dup = voice._recent_duplicate_add(app, "call mom")   # normalizer casefolds
        assert dup and dup["text"] == "Call mom"

    def test_old_add_not_deduped(self, task_pkg):
        voice = task_pkg["voice"]
        app = StubTaskApp(Path("."))
        old = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        app._recent_adds = [{"text": "Call mom", "ts": old}]
        assert voice._recent_duplicate_add(app, "Call mom") is None

    def test_different_text_not_deduped(self, task_pkg):
        voice = task_pkg["voice"]
        app = StubTaskApp(Path("."))
        app._recent_adds = [
            {"text": "Call mom", "ts": datetime.now().isoformat(timespec="seconds")}
        ]
        assert voice._recent_duplicate_add(app, "Call dad") is None
