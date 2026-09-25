"""run_model_matrix — the one-harness, many-models driver.

Pure: the HTTP call is patched, so no daemon and no model is involved. Result
dicts use the real AgentRunResult keys (subject_model, wall_ms, skipped,
cost_usd, notes, error).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("run_model_matrix", _SCRIPTS / "run_model_matrix.py")
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)

SANDBOX = "http://127.0.0.1:9002"


def _result(ok=True, cost=0.0, model="qwen3.5-32k:latest", skipped=False):
    return {"ok": ok, "skipped": skipped, "subject_model": model, "wall_ms": 1500,
            "tool_calls": 3, "cost_usd": cost, "notes": "", "error": None}


def test_main_daemons_are_refused_however_the_host_is_spelled():
    assert mm.is_main_daemon("http://127.0.0.1:9000")
    assert mm.is_main_daemon("http://127.0.0.1:9001")
    assert mm.is_main_daemon("127.0.0.1:9000")  # scheme-less
    assert mm.is_main_daemon("http://127.0.0.1:9100", configured_port=9100)  # non-default main port
    assert not mm.is_main_daemon(SANDBOX)


def test_a_billed_call_priced_at_zero_is_unknown_not_free():
    assert mm.row_from_result("eos+openrouter:x/y", "s", _result(cost=0.0))["cost_usd"] is None
    assert mm.row_from_result("eos+openai:new-model", "s", _result(cost=0.0))["cost_usd"] is None
    assert mm.row_from_result("eos+openai:gpt-5.4-mini", "s", _result(cost=0.01))["cost_usd"] == 0.01
    # A local model really is free.
    assert mm.row_from_result("eos+ollama", "s", _result(cost=0.0))["cost_usd"] == 0.0


def test_an_error_cell_weighs_one_row_per_requested_rep_with_the_full_schema():
    rows = mm.error_rows("eos+ollama", "s", "boom", reps=3)
    assert len(rows) == 3
    assert set(rows[0]) == set(mm.row_from_result("eos+ollama", "s", _result()))
    assert all(not r["ok"] and r["error"] == "boom" for r in rows)


def test_summary_excludes_skipped_rows_and_counts_errors():
    rows = [mm.row_from_result("eos+ollama", "s", _result(ok=True)),
            mm.row_from_result("eos+ollama", "s", _result(ok=False, skipped=True)),
            *mm.error_rows("eos+ollama", "s", "timeout", reps=1)]
    (s,) = mm.summarize(rows, ["eos+ollama"])
    assert (s["passed"], s["runs"], s["errors"]) == (1, 2, 1)


def test_summary_cost_is_unknown_when_any_row_is_unknown():
    rows = [mm.row_from_result("eos+openai:m", "s", _result(cost=0.25)),
            mm.row_from_result("eos+openai:m", "s", _result(cost=0.0))]
    (s,) = mm.summarize(rows, ["eos+openai:m"])
    assert s["cost_usd"] is None  # a partial sum would understate the spend
    (s,) = mm.summarize(rows[:1] * 2, ["eos+openai:m"])
    assert s["cost_usd"] == 0.5


def _fake_post(bad_subject="eos+ollama:bad"):
    calls = []

    def fake(host, tok, path, body, timeout):
        calls.append((tok, body))
        if body["subject_ids"] == [bad_subject]:
            return {"error": "unknown subject_ids"}
        return {"results": [_result()] * body["reps"]}

    return fake, calls


def test_run_posts_one_subject_per_request_and_streams_every_row(tmp_path, monkeypatch):
    fake, calls = _fake_post()
    monkeypatch.setattr(mm, "_post", fake)
    out = tmp_path / "rows.jsonl"
    rows, failed = mm.run(["eos+ollama", "eos+ollama:bad"], ["a", "b"], host="h", tok="", reps=2,
                          jsonl_path=out, timeout=1, log=lambda s: None)
    assert [b["subject_ids"] for _, b in calls] == [["eos+ollama"], ["eos+ollama:bad"]] * 2
    assert failed == 2
    assert len(rows) == 8  # 2 scenarios x 2 subjects x 2 reps — an error cell still fills its reps
    assert [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] == rows
    assert sum(r["ok"] for r in rows) == 4


def test_a_run_where_a_cell_failed_exits_nonzero(tmp_path, monkeypatch, capsys):
    fake, _ = _fake_post()
    monkeypatch.setattr(mm, "_post", fake)
    monkeypatch.setattr(mm, "REPORT_DIR", tmp_path)
    code = mm.main(["--host", SANDBOX, "--subjects", "eos+ollama", "eos+ollama:bad",
                    "--scenarios", "a", "--json"])
    env = json.loads(capsys.readouterr().out)
    assert code == 1 and env["ok"] is False and env["code"] == "error"


def test_a_clean_run_exits_zero(tmp_path, monkeypatch, capsys):
    fake, _ = _fake_post()
    monkeypatch.setattr(mm, "_post", fake)
    monkeypatch.setattr(mm, "REPORT_DIR", tmp_path)
    code = mm.main(["--host", SANDBOX, "--subjects", "eos+ollama", "--scenarios", "a", "--json"])
    env = json.loads(capsys.readouterr().out)
    assert code == 0 and env["ok"] is True
    assert env["data"]["summary"][0]["passed"] == 1


def test_runs_without_emptyos_toml_and_sends_no_token_to_a_sandbox(tmp_path, monkeypatch, capsys):
    fake, calls = _fake_post()
    monkeypatch.setattr(mm, "_post", fake)
    monkeypatch.setattr(mm, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(mm, "TOML", tmp_path / "missing.toml")
    code = mm.main(["--host", SANDBOX, "--subjects", "eos+ollama", "--scenarios", "a", "--json"])
    assert code == 0 and json.loads(capsys.readouterr().out)["ok"] is True
    assert calls[0][0] == ""


def test_main_refuses_the_main_daemon_without_allow_main(capsys):
    code = mm.main(["--host", "http://127.0.0.1:9000", "--subjects", "eos+ollama", "--json"])
    env = json.loads(capsys.readouterr().out)
    assert code == 1 and env["code"] == "invalid_args"


@pytest.mark.parametrize("reps", ["0", "21"])
def test_reps_outside_the_server_range_are_refused(reps, capsys):
    code = mm.main(["--host", SANDBOX, "--subjects", "eos+ollama", "--reps", reps, "--dry-run", "--json"])
    assert code == 1 and json.loads(capsys.readouterr().out)["code"] == "invalid_args"


def test_dry_run_plans_without_posting(monkeypatch, capsys):
    monkeypatch.setattr(mm, "_post", lambda *a, **k: pytest.fail("dry run must not post"))
    code = mm.main(["--host", SANDBOX, "--subjects", "eos+ollama", "eos+ollama:x",
                    "--scenarios", "a", "b", "c", "--reps", "2", "--dry-run", "--json"])
    env = json.loads(capsys.readouterr().out)
    assert code == 0 and env["data"]["runs"] == 12
