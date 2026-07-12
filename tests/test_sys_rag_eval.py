"""System tests: RAG Eval harness.

Two layers:
- Pure unit tests for the scoring/aggregation (no daemon, always CI-safe).
- API smoke against the live daemon, plus a self-cleaning grep-only end-to-end
  eval (non-LLM) that authors a TEST_PREFIX case pointing at a real vault note.
"""

import sys

import pytest

from helpers import TEST_PREFIX, assert_ok, app_path


# ── Pure unit tests (no daemon) ──────────────────────────────────────

def _load_app_module():
    """Import the app module without booting the kernel — register the
    package path so the relative `from emptyos.sdk import ...` resolves."""
    import importlib.util
    import types

    repo = app_path("rag-eval").resolve().parents[3]  # .../D:/emptyos
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    pkg = types.ModuleType("eos_rageval_pkg")
    pkg.__path__ = [str(app_path("rag-eval"))]
    sys.modules["eos_rageval_pkg"] = pkg
    spec = importlib.util.spec_from_file_location(
        "eos_rageval_pkg.app", str(app_path("rag-eval") / "app.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def app_mod():
    return _load_app_module()


class TestScoring:
    def test_perfect_hit_at_1(self, app_mod):
        m = app_mod.score_retrieval(["a.md", "b.md", "c.md"], ["a.md"])
        assert m["hit_1"] == 1.0
        assert m["hit_3"] == 1.0
        assert m["mrr"] == 1.0
        assert m["precision_5"] == pytest.approx(0.2)  # 1 of top-5
        assert m["recall_5"] == 1.0

    def test_miss(self, app_mod):
        m = app_mod.score_retrieval(["x.md", "y.md"], ["a.md"])
        assert m["hit_1"] == 0.0
        assert m["hit_5"] == 0.0
        assert m["mrr"] == 0.0
        assert m["recall_5"] == 0.0

    def test_mid_rank_mrr(self, app_mod):
        # expected at rank 3 → MRR 1/3, hit@1 miss, hit@3 hit
        m = app_mod.score_retrieval(["x.md", "y.md", "a.md", "z.md"], ["a.md"])
        assert m["hit_1"] == 0.0
        assert m["hit_3"] == 1.0
        assert m["mrr"] == pytest.approx(1 / 3)

    def test_case_insensitive(self, app_mod):
        m = app_mod.score_retrieval(["Folder/A.md"], ["folder/a.md"])
        assert m["hit_1"] == 1.0

    def test_multi_expected_recall(self, app_mod):
        m = app_mod.score_retrieval(["a.md", "b.md", "q.md"], ["a.md", "b.md"])
        assert m["recall_5"] == 1.0
        assert m["precision_5"] == pytest.approx(0.4)  # 2 of top-5

    def test_aggregate_mean(self, app_mod):
        agg = app_mod.aggregate_metrics([
            {"hit_1": 1.0, "mrr": 1.0},
            {"hit_1": 0.0, "mrr": 0.0},
        ])
        assert agg["hit_1"] == 0.5
        assert agg["mrr"] == 0.5

    def test_aggregate_empty(self, app_mod):
        agg = app_mod.aggregate_metrics([])
        assert agg["mrr"] == 0.0

    def test_slugify(self, app_mod):
        assert app_mod._slugify("How is X calculated?") == "how-is-x-calculated"


# ── API smoke (live daemon) ──────────────────────────────────────────

@pytest.mark.api
class TestRagEvalAPI:
    def test_cases_shape(self, http_client):
        data = assert_ok(http_client.get("/rag-eval/api/cases"))
        assert isinstance(data.get("cases"), list)
        assert "count" in data
        assert "embeddings_available" in data

    def test_runs_shape(self, http_client):
        data = assert_ok(http_client.get("/rag-eval/api/runs"))
        assert isinstance(data.get("runs"), list)

    def test_latest_handles_no_runs(self, http_client):
        resp = http_client.get("/rag-eval/api/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data  # ok:False when no runs, else ok:True + run

    def test_run_detail_not_found(self, http_client):
        data = assert_ok(http_client.get("/rag-eval/api/runs/does-not-exist"))
        assert data.get("ok") is False


@pytest.mark.api
class TestRagEvalEndToEnd:
    """Author a TEST_PREFIX case pointing at a real vault note, run a
    grep-only eval (no LLM), assert the harness scores it. Case note carries
    the test prefix so the vault-leak guard sweeps it."""

    def test_grep_eval_scores_real_note(self, http_client):
        # 1. Pick a query that grep actually returns content hits for, and the
        #    top result it returns. Anchoring the case on the exact
        #    (query, result) pair makes the assertion deterministic — grep
        #    searches content, so querying a note by its filename would NOT be.
        query = "emptyos"
        s = assert_ok(http_client.get(f"/search/api/search?q={query}&top=5&semantic=0"))
        results = s.get("results") or []
        paths = [r if isinstance(r, str) else r.get("path", "") for r in results]
        # exclude test fixtures + the eval case notes (the app filters these too)
        paths = [p for p in paths if p and not p.split("/")[-1].startswith(TEST_PREFIX)
                 and "/rag-eval/" not in p.replace("\\", "/")]
        if not paths:
            pytest.skip("vault has no grep hit to anchor the eval case")
        target = paths[0]

        # 2. Author a TEST_PREFIX case: same query, expecting that result.
        case_id = f"{TEST_PREFIX}rag-e2e"
        created = assert_ok(http_client.post("/rag-eval/api/cases", json={
            "query": query,
            "expected_sources": [target],
            "case_id": case_id,
            "eval_id": "manual",
        }))
        assert created.get("ok") is True

        # 3. Run grep-only eval (non-LLM), then inspect the run detail.
        run = assert_ok(http_client.post("/rag-eval/api/run", json={
            "methods": ["grep"], "top": 10,
        }))
        assert run.get("ok") is True
        assert "grep" in run.get("metrics", {})
        run_id = run["run_id"]

        detail = assert_ok(http_client.get(f"/rag-eval/api/runs/{run_id}"))
        cases = detail["run"]["cases"]
        ours = [c for c in cases if c["case_id"] == case_id]
        assert ours, "authored case not found in run"
        gm = ours[0]["per_method"]["grep"]
        # grep re-runs the same query → must retrieve the same result it just
        # returned, so the harness scores a hit somewhere in top-k.
        assert gm["mrr"] > 0, f"grep did not score the known result: {gm}"
