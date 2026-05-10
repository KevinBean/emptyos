"""System app tests: Tests app — pytest runner + run-artifact viewer.

Covers the read-side of the runs API by synthesizing a fake run directory
under data/apps/tests/runs/ and asserting the daemon surfaces it. We
deliberately don't drive /api/run-* endpoints (that would launch pytest
recursively).
"""

import json
import shutil
import time
import uuid
from pathlib import Path

import pytest

from helpers import assert_dict_response, assert_list_response, assert_ok

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = REPO_ROOT / "data" / "apps" / "tests" / "runs"


def _make_fake_run(passed: int = 1, failed: int = 0) -> tuple[str, Path]:
    """Synthesize a run dir with one fake test's artifacts. Returns (run_id, dir)."""
    run_id = "PLAYWRIGHT-TEST-" + time.strftime("%Y-%m-%dT%H-%M-%S") + "_" + uuid.uuid4().hex[:6]
    rd = RUNS_ROOT / run_id
    test_dir = rd / "fake_test_node"
    test_dir.mkdir(parents=True, exist_ok=True)
    # summary.json (the run-level one apps/tests writes after pytest exit)
    (rd / "summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "label": "synthetic",
                "summary": {
                    "passed": passed,
                    "failed": failed,
                    "errors": 0,
                    "wall_time": 0.5,
                    "exit_code": 0 if failed == 0 else 1,
                    "timestamp": "2026-05-09T00:00:00",
                },
            }
        ),
        encoding="utf-8",
    )
    # Per-test artifacts (mirrors what conftest produces)
    (test_dir / "result.json").write_text(
        json.dumps(
            {
                "nodeid": "tests/fake.py::test_thing",
                "status": "passed" if failed == 0 else "failed",
                "duration_ms": 123,
                "error": None if failed == 0 else "boom",
            }
        ),
        encoding="utf-8",
    )
    (test_dir / "console.log").write_text("[log] hello\n", encoding="utf-8")
    (test_dir / "screenshot-final.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG header
    (test_dir / "trace.zip").write_bytes(b"PK\x03\x04")  # ZIP header
    return run_id, rd


@pytest.fixture
def fake_run():
    run_id, rd = _make_fake_run()
    yield run_id
    shutil.rmtree(rd, ignore_errors=True)


@pytest.mark.api
class TestTestsAppAPI:
    def test_list_returns_test_files(self, http_client):
        data = assert_dict_response(http_client.get("/tests/api/list"))
        files = data.get("tests", [])
        assert isinstance(files, list) and files, "expected test files discovered"
        # Every file should have path + name + size
        sample = files[0]
        for k in ("path", "name", "size"):
            assert k in sample, f"missing {k} in /api/list row"

    def test_history_is_dict(self, http_client):
        # /api/history returns the raw history dict (path → summary)
        resp = http_client.get("/tests/api/history")
        data = assert_ok(resp)
        assert isinstance(data, dict)

    def test_runs_returns_shape(self, http_client):
        data = assert_dict_response(http_client.get("/tests/api/runs"))
        assert isinstance(data.get("runs"), list)

    def test_runs_includes_synthesized(self, http_client, fake_run):
        data = http_client.get("/tests/api/runs").json()
        ids = [r.get("run_id") for r in data.get("runs", [])]
        assert fake_run in ids, f"{fake_run} missing from /api/runs"

    def test_run_detail_lists_artifacts(self, http_client, fake_run):
        data = http_client.get(f"/tests/api/run/{fake_run}").json()
        assert data.get("run_id") == fake_run
        tests = data.get("tests") or []
        assert tests, "expected at least one test row"
        arts = set(tests[0].get("artifacts") or [])
        # All four artifact types should be detected
        assert {"trace.zip", "console.log", "screenshot-final.png", "result.json"} <= arts

    def test_run_detail_404_for_missing(self, http_client):
        data = http_client.get("/tests/api/run/does-not-exist-xyz").json()
        assert data.get("error") == "not found"

    def test_artifact_serves_console_as_text(self, http_client, fake_run):
        resp = http_client.get(f"/tests/api/run/{fake_run}/fake_test_node/console.log")
        assert resp.status_code == 200
        assert "hello" in resp.text

    def test_artifact_serves_result_json(self, http_client, fake_run):
        resp = http_client.get(f"/tests/api/run/{fake_run}/fake_test_node/result.json")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "passed"
        assert body.get("duration_ms") == 123

    def test_artifact_serves_trace_zip(self, http_client, fake_run):
        resp = http_client.get(f"/tests/api/run/{fake_run}/fake_test_node/trace.zip")
        assert resp.status_code == 200
        assert resp.content[:2] == b"PK", "expected ZIP magic"

    def test_artifact_rejects_unwhitelisted_name(self, http_client, fake_run):
        # Even if the file exists on disk, only the whitelist is served.
        (RUNS_ROOT / fake_run / "fake_test_node" / "secret.txt").write_text("nope")
        resp = http_client.get(f"/tests/api/run/{fake_run}/fake_test_node/secret.txt")
        assert resp.status_code == 400

    def test_artifact_404_for_missing_file(self, http_client, fake_run):
        # Whitelisted name but file doesn't exist on disk.
        td = RUNS_ROOT / fake_run / "fake_test_node"
        (td / "screenshot-final.png").unlink()
        resp = http_client.get(f"/tests/api/run/{fake_run}/fake_test_node/screenshot-final.png")
        assert resp.status_code == 404

    def test_delete_run_removes_dir(self, http_client):
        run_id, rd = _make_fake_run()
        assert rd.exists()
        resp = http_client.request("DELETE", f"/tests/api/run/{run_id}")
        assert_ok(resp)
        assert not rd.exists(), "run dir should be removed after DELETE"
