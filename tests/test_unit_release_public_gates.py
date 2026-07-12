"""Unit tests for release-public.py's gate bookkeeping + temp-dir hygiene.

Two properties, both regressions that were live before this file existed:

  1. `commit_and_push` cleaned its `%TEMP%/release-work` clone only on the
     full-success path. A dry run, an identical snapshot, or any abort left a
     full repo clone behind in the system temp dir.
  2. The click-target and KB-alignment gates soft-skip when the daemon is down
     or the vault isn't mounted. That was silent, so a release could ship with
     gates that never ran and nothing said so.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release-public.py"
_spec = importlib.util.spec_from_file_location("release_public", _SCRIPT)
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_skipped_gates():
    rp.SKIPPED_GATES.clear()
    yield
    rp.SKIPPED_GATES.clear()


# ── 1. The work-tree clone is always cleaned up ────────────────────────────


def _snapshot(tmp_path: Path) -> Path:
    snap = tmp_path / "snapshot"
    snap.mkdir()
    return snap


def test_commit_and_push_cleans_work_dir_when_the_inner_step_raises(
    tmp_path: Path, monkeypatch
) -> None:
    snap = _snapshot(tmp_path)
    work = snap.parent / "release-work"

    def boom(work_dir: Path, *a, **kw):
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "clone.txt").write_text("a whole git clone", encoding="utf-8")
        raise SystemExit("push failed")

    monkeypatch.setattr(rp, "_commit_and_push", boom)

    with pytest.raises(SystemExit):
        rp.commit_and_push(snap, "v1.2.3", "msg", dry_run=False)

    assert not work.exists(), "aborted release left a repo clone in %TEMP%"


def test_commit_and_push_cleans_work_dir_on_an_early_return(tmp_path: Path, monkeypatch) -> None:
    # e.g. --dry-run, or "snapshot is identical to public HEAD".
    snap = _snapshot(tmp_path)
    work = snap.parent / "release-work"

    def early_return(work_dir: Path, *a, **kw):
        work_dir.mkdir(parents=True, exist_ok=True)
        return

    monkeypatch.setattr(rp, "_commit_and_push", early_return)
    rp.commit_and_push(snap, "v1.2.3", "msg", dry_run=True)

    assert not work.exists()


def test_commit_and_push_clears_a_stale_work_dir_before_starting(
    tmp_path: Path, monkeypatch
) -> None:
    snap = _snapshot(tmp_path)
    work = snap.parent / "release-work"
    work.mkdir(parents=True)
    (work / "leftover.txt").write_text("from a previous run", encoding="utf-8")

    seen: dict = {}
    monkeypatch.setattr(rp, "_commit_and_push", lambda w, *a, **kw: seen.update(existed=w.exists()))
    rp.commit_and_push(snap, "v1.2.3", "msg", dry_run=True)

    assert seen["existed"] is False, "stale work dir was not cleared before the release"
    assert not work.exists()


# ── 2. Skipped gates are recorded and reported ─────────────────────────────


def test_skip_gate_records_name_and_reason() -> None:
    rp.skip_gate("click-target audit", "daemon at :9000 unreachable")
    assert rp.SKIPPED_GATES == [("click-target audit", "daemon at :9000 unreachable")]


def test_report_skipped_gates_is_silent_when_nothing_skipped(capsys) -> None:
    rp.report_skipped_gates()
    assert capsys.readouterr().out == ""


def test_report_skipped_gates_names_every_skip(capsys) -> None:
    rp.skip_gate("click-target audit", "daemon at :9000 unreachable")
    rp.skip_gate("snapshot boot smoke", "--skip-boot-smoke passed")
    rp.report_skipped_gates()

    out = capsys.readouterr().out
    assert "SKIPPED GATES" in out
    assert "click-target audit" in out
    assert "snapshot boot smoke" in out
    assert "--strict-gates" in out  # tells the reader how to make it fatal
