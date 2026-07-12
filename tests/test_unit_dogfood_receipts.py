"""Pure-logic tests for dogfood-agent loop receipts.

Receipts are read-side projections over existing fix-agent and dogfood state,
so most behavior can be tested without a running daemon.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def receipts_module():
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    from helpers import app_path

    dogfood_dir = app_path("dogfood-agent")
    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(repo_root / "apps")]
        sys.modules["apps"] = apps_pkg
    if "apps.dogfood-agent" not in sys.modules:
        pkg = types.ModuleType("apps.dogfood-agent")
        pkg.__path__ = [str(dogfood_dir)]
        sys.modules["apps.dogfood-agent"] = pkg
    return _load_module("apps.dogfood-agent.receipts", dogfood_dir / "receipts.py")


@pytest.fixture(scope="module")
def fix_runs_module():
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    from helpers import app_path

    fix_dir = app_path("fix-agent")
    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(repo_root / "apps")]
        sys.modules["apps"] = apps_pkg
    if "apps.fix-agent" not in sys.modules:
        pkg = types.ModuleType("apps.fix-agent")
        pkg.__path__ = [str(fix_dir)]
        sys.modules["apps.fix-agent"] = pkg
    # shared.py is needed by runs.py relative imports.
    _load_module("apps.fix-agent.shared", fix_dir / "shared.py")
    return _load_module("apps.fix-agent.runs", fix_dir / "runs.py")


def _run(status: str, run_id: str = "20260708T010000-aaaaaa", **extra):
    data = {
        "run_id": run_id,
        "filename": "bug-sample.md",
        "attempt": 1,
        "status": status,
        "started_at": "2026-07-08T01:00:00+00:00",
    }
    data.update(extra)
    return data


@pytest.mark.parametrize(
    ("statuses", "queue_location", "expected"),
    [
        (["verified"], "done", "verified"),
        (["reverted"], "pending", "reverted"),
        ([], "blocked", "blocked"),
        (["merged"], "done", "merged"),
        (["ready"], "pending", "in-progress"),
        (["verify-failed"], "done", "failed"),
    ],
)
def test_final_state_matrix(receipts_module, statuses, queue_location, expected):
    rows = [{"status": s} for s in statuses]
    assert receipts_module._derive_final_state(rows, queue_location) == expected


def test_assemble_receipt_extracts_gates_changed_files_and_source(receipts_module):
    prompt = """---
kind: bug
app: journal
key: bug::journal save
---
## Friction
Save button did nothing.
"""
    runs = [
        _run(
            "verified",
            diff_stat=" apps/public/standard/journal/app.py | 4 ++--\n 1 file changed, 2 insertions(+), 2 deletions(-)",
            compile_check={"ok": True, "files": ["apps/public/standard/journal/app.py"], "output": ""},
            sandbox_restart={"ok": True, "member": "dogfood-demo"},
            verify_result={"target_fixed": True, "summary": "target cleared", "regressed_count": 0, "new_count": 0},
            verify_context={"persona": "kevin-weekday", "scenario": "tuesday-evening", "friction_text": "Save button did nothing."},
            verify_run_id="dogfood-1",
        )
    ]
    receipt = receipts_module._assemble_receipt(
        runs,
        {"count": 1, "last_error": ""},
        "done",
        "",
        2,
        filename="bug-sample.md",
        prompt_content=prompt,
    )
    assert receipt["final_state"] == "verified"
    assert receipt["app"] == "journal"
    assert receipt["gates"]["py_compile"]["ok"] is True
    assert receipt["gates"]["sandbox_restart"]["ok"] is True
    assert receipt["gates"]["verify"]["target_fixed"] is True
    assert receipt["changed_files"] == ["apps/public/standard/journal/app.py"]


def test_markdown_render_contains_receipt_sections(receipts_module):
    receipt = receipts_module._assemble_receipt(
        [_run("verified", verify_result={"target_fixed": True})],
        {},
        "done",
        "",
        2,
        filename="bug-sample.md",
        prompt_content="---\nkind: bug\napp: journal\n---\nBody",
    )
    md = receipts_module._receipt_markdown(receipt)
    assert "# Loop receipt: bug-sample" in md
    assert "## Attempts" in md
    assert "## Gates" in md
    assert "## Learning note" in md


def test_source_extracts_persona_reported_heading(receipts_module):
    # The real dogfood fix-prompt contract heading — must be matched by the
    # body fallback when a run-less prompt has no verify_context.
    prompt = (
        "---\nkind: bug\napp: task\n---\n"
        "## What the persona reported\n> tasks show overdue_days=0\n"
    )
    src = receipts_module._source_from_prompt(prompt, [])
    assert "overdue_days" in src["friction_text"]


def test_changed_files_normalizes_renames(receipts_module):
    stat = (
        " apps/a.py => apps/b.py | 2 +-\n"
        " emptyos/{old => new}/x.py | 4 ++--\n"
        " apps/c.py | 1 +\n"
        " 3 files changed, 7 insertions(+)"
    )
    assert receipts_module._changed_files_from_diff_stat(stat) == [
        "apps/b.py",
        "apps/c.py",
        "emptyos/new/x.py",
    ]


def test_gates_come_from_verified_run_not_last(receipts_module):
    # A verified attempt followed by a later still-running attempt: the receipt
    # is "verified", so its gates must reflect the verified run, not the last.
    runs = [
        _run(
            "verified",
            "20260708T010000-aaaaaa",
            compile_check={"ok": True, "files": ["a.py"]},
            sandbox_restart={"ok": True},
            verify_result={"target_fixed": True, "summary": "cleared"},
        ),
        _run("running", "20260708T020000-bbbbbb", started_at="2026-07-08T02:00:00+00:00"),
    ]
    receipt = receipts_module._assemble_receipt(
        runs, {}, "done", "", 2, filename="bug-sample.md", prompt_content=""
    )
    assert receipt["final_state"] == "verified"
    assert receipt["gates"]["py_compile"]["ok"] is True
    assert receipt["gates"]["verify"]["target_fixed"] is True


def test_summary_row_from_runs(receipts_module):
    runs = [
        _run("reverted", "20260708T010000-aaaaaa"),
        _run(
            "verified",
            "20260708T020000-bbbbbb",
            verify_context={"app": "task", "friction_kind": "bug", "source": "dogfood-agent"},
            finished_at="2026-07-08T02:05:00+00:00",
        ),
    ]
    row = receipts_module._summary_row(runs, {"count": 2}, "done", "bug-sample.md")
    assert row["final_state"] == "verified"
    assert row["app"] == "task"
    assert row["kind"] == "bug"
    assert row["attempts"] == 2
    assert row["last_activity"] == "2026-07-08T02:05:00+00:00"


def test_summary_row_no_runs_uses_queue_location(receipts_module):
    row = receipts_module._summary_row([], {"count": 0}, "pending", "bug-x.md")
    assert row["final_state"] == "in-progress"
    assert row["app"] == ""
    assert row["attempts"] == 0


@pytest.mark.asyncio
async def test_runs_grouped_by_filename(fix_runs_module, tmp_path):
    from emptyos.sdk.run_registry import RunRegistry

    class FakeFixAgent:
        def __init__(self, root: Path):
            self._registry = RunRegistry(root / "runs")

        def runs(self, kind: str):
            return self._registry

    app = FakeFixAgent(tmp_path)
    app.runs("runs").new("20260708T020000-bbbbbb").write_state(
        _run("verified", "20260708T020000-bbbbbb", started_at="2026-07-08T02:00:00+00:00")
    )
    app.runs("runs").new("20260708T010000-aaaaaa").write_state(
        _run("reverted", "20260708T010000-aaaaaa", started_at="2026-07-08T01:00:00+00:00")
    )
    app.runs("runs").new("20260708T030000-cccccc").write_state(
        _run("verified", "20260708T030000-cccccc", filename="other.md")
    )
    groups = await fix_runs_module.runs_grouped_by_filename(app)
    assert set(groups) == {"bug-sample.md", "other.md"}
    assert [r["run_id"] for r in groups["bug-sample.md"]] == [
        "20260708T010000-aaaaaa",
        "20260708T020000-bbbbbb",
    ]


@pytest.mark.asyncio
async def test_build_receipt_rejects_traversal_before_cross_app_call(receipts_module):
    class FakeApp:
        async def call_app(self, *args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("call_app should not run for invalid filename")

    res = await receipts_module._build_receipt(FakeApp(), "../bad.md")
    assert res == {"error": "invalid filename"}


@pytest.mark.asyncio
async def test_runs_for_filename_filters_and_sorts(fix_runs_module, tmp_path):
    from emptyos.sdk.run_registry import RunRegistry

    class FakeFixAgent:
        def __init__(self, root: Path):
            self._registry = RunRegistry(root / "runs")

        def runs(self, kind: str):
            assert kind == "runs"
            return self._registry

    app = FakeFixAgent(tmp_path)
    app.runs("runs").new("20260708T020000-bbbbbb").write_state(
        _run("verified", "20260708T020000-bbbbbb", started_at="2026-07-08T02:00:00+00:00")
    )
    app.runs("runs").new("20260708T010000-aaaaaa").write_state(
        _run("reverted", "20260708T010000-aaaaaa", started_at="2026-07-08T01:00:00+00:00")
    )
    app.runs("runs").new("20260708T030000-cccccc").write_state(
        _run("verified", "20260708T030000-cccccc", filename="other.md")
    )

    rows = await fix_runs_module.runs_for_filename(app, "bug-sample.md")

    assert [r["run_id"] for r in rows] == [
        "20260708T010000-aaaaaa",
        "20260708T020000-bbbbbb",
    ]
    assert await fix_runs_module.runs_for_filename(app, "../bad.md") == []
