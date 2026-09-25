#!/usr/bin/env python3
"""Model matrix — one harness, many models, on model-bench's agent scenarios.

The sibling of `run_harness_matrix.py`, on the other axis: that script pins ONE
model and compares agent harnesses; this one pins the EmptyOS harness and
compares MODELS. Use it for "is a bigger/other model better at our tasks?" —
e.g. whether an open model that needs 48GB beats the local default.

    python scripts/run_model_matrix.py --host http://127.0.0.1:9002 \\
        --subjects eos+ollama eos+ollama:gemma4:31b-cloud --reps 2
    python scripts/run_model_matrix.py --host ... --dry-run     # print the plan
    python scripts/run_model_matrix.py --host ... --json        # agent-cli envelope

Subjects are model-bench subject ids: `eos+ollama` (configured local model) or
`eos+<provider>:<model>` (claude / openai / ollama / openrouter). Local models
are free and Ollama cloud tags spend a flat quota; openai, claude and openrouter
are billed per token.

Point `--host` at a LEASED SANDBOX MEMBER (`.claude/rules/sandbox-usage.md`).
The in-daemon agent loop wedges `:9000` under a batch, so the main daemon is
refused unless `--allow-main` is passed.

Rows stream to a JSONL as each (subject, scenario) finishes, so a long run keeps
its progress. A run in which any cell failed at the HTTP level — daemon down,
unknown subject, timeout — exits non-zero: it did not measure what it was asked to.

Two limits of the data, stated so nobody reads more into it:
- `model` is what model-bench recorded, which is the model REQUESTED. The
  provider stamps its configured name on the usage record and never reads the
  name the server answered with, so this cannot prove which model responded.
  Cross-check a surprising result by behaviour (timings, tool counts).
- `cost_usd` comes from the provider's exact-match OpenAI price table. A billed
  subject that reads $0 is a missing price, so it is recorded as None (unknown).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_harness_matrix import _post  # noqa: E402
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TOML = REPO / "emptyos.toml"
REPORT_DIR = REPO / "data" / "apps" / "model-bench" / "reports"

# Round-3b's five plus the ten-scenario core — the set every recorded model
# result in this repo was scored on, so new rows line up with old ones.
DEFAULT_SCENARIOS = [
    "write-new-util", "add-temperature", "grep-replace", "multi-file-refactor",
    "debug-and-fix", "long-context-needle", "cross-format-spec", "delete-with-callers",
    "error-recovery", "false-premise", "big-refactor", "engineer-calc",
]
MAIN_PORTS = {9000, 9001}  # user-owned daemons (.claude/rules/daemon-handling.md)
BILLED_BASES = ("eos+openai", "eos+claude", "eos+openrouter")
MAX_REPS = 20  # agent_api.api_agent_run clamps reps to 1..20
PER_REQUEST_TIMEOUT_S = 3600  # one POST runs all reps of a cell sequentially


def _load_network() -> tuple[int, str]:
    """(main daemon port, auth token) from emptyos.toml; defaults when absent,
    so a fresh clone can still drive a sandbox member."""
    try:
        net = tomllib.loads(TOML.read_text(encoding="utf-8")).get("network", {})
    except (OSError, tomllib.TOMLDecodeError):
        return 9000, ""
    return int(net.get("port", 9000)), net.get("auth_token", "")


def _port(host: str) -> int | None:
    parsed = urlparse(host if "://" in host else "http://" + host)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def is_main_daemon(host: str, configured_port: int = 9000) -> bool:
    return _port(host) in MAIN_PORTS | {configured_port}


def row_from_result(subject: str, scenario: str, r: dict) -> dict:
    cost = r.get("cost_usd")
    if subject.startswith(BILLED_BASES) and not cost:
        cost = None  # a billed call priced at $0 is a missing price, not a free call
    return {
        "subject": subject,
        "scenario": scenario,
        "ok": bool(r.get("ok")),
        "skipped": bool(r.get("skipped")),
        "model": r.get("subject_model", ""),
        "tool_calls": r.get("tool_calls"),
        "wall_s": round((r.get("wall_ms") or 0) / 1000, 1),
        "cost_usd": cost,
        "error": r.get("error"),
        "notes": (r.get("notes") or "")[:300],
    }


def error_rows(subject: str, scenario: str, error: str, reps: int) -> list[dict]:
    # One row per requested rep, so a failed cell weighs the same as a passed
    # one in the pass-rate denominator.
    row = row_from_result(subject, scenario, {"ok": False, "error": error})
    return [dict(row) for _ in range(reps)]


def summarize(rows: list[dict], subjects: list[str]) -> list[dict]:
    """Per-subject totals. Skipped rows are excluded from the pass rate."""
    out = []
    for sid in subjects:
        mine = [r for r in rows if r["subject"] == sid and not r.get("skipped")]
        costs = [r["cost_usd"] for r in mine]
        out.append({
            "subject": sid,
            "passed": sum(1 for r in mine if r["ok"]),
            "runs": len(mine),
            "errors": sum(1 for r in mine if r.get("error") and not r["ok"]),
            "models": sorted({r["model"] for r in mine if r.get("model")}),
            # Unknown if any row's cost is unknown — a partial sum would understate.
            "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 4),
        })
    return out


def run(subjects, scenarios, *, host, tok, reps, jsonl_path, timeout, log=print) -> tuple[list[dict], int]:
    """Run the matrix; return (rows, number of cells that failed at the HTTP level)."""
    rows, failed_cells = [], 0
    for scenario in scenarios:
        for sid in subjects:  # one subject per POST: a failure costs a cell, not a row
            t0 = time.monotonic()
            try:
                res = _post(host, tok, "/model-bench/api/agent-run",
                            {"scenario_id": scenario, "subject_ids": [sid], "reps": reps}, timeout)
            except Exception as e:  # noqa: BLE001 — record and continue; runs are long
                res = {"error": f"{type(e).__name__}: {e}"}
            results = res.get("results")
            if "error" in res or not results:
                failed_cells += 1
                cell = error_rows(sid, scenario, res.get("error", "no results"), reps)
            else:
                cell = [row_from_result(sid, scenario, r) for r in results]
            with jsonl_path.open("a", encoding="utf-8") as f:
                for row in cell:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.extend(cell)
            npass = sum(1 for r in cell if r["ok"])
            err = f"  {res['error']}" if "error" in res else ""
            log(f"  {scenario:22} {sid:48} {npass}/{len(cell)}  ({time.monotonic() - t0:.0f}s){err}")
    return rows, failed_cells


def _fail(as_json: bool, code: str, msg: str) -> int:
    if as_json:
        return emit_json(False, code, msg)
    print(msg, file=sys.stderr)
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="daemon base URL — a leased sandbox member")
    ap.add_argument("--subjects", nargs="+", required=True, help="model-bench subject ids")
    ap.add_argument("--scenarios", nargs="+", default=DEFAULT_SCENARIOS)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=PER_REQUEST_TIMEOUT_S)
    ap.add_argument("--allow-main", action="store_true", help="permit the main daemon (can wedge it)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    args = ap.parse_args(argv)

    if not 1 <= args.reps <= MAX_REPS:
        return _fail(args.json, "invalid_args", f"--reps must be 1..{MAX_REPS} (the server clamps to that range)")
    main_port, main_tok = _load_network()
    on_main = is_main_daemon(args.host, main_port)
    if on_main and not args.allow_main:
        return _fail(args.json, "invalid_args",
                     f"{args.host} is a user-owned daemon; lease a sandbox member or pass --allow-main")

    plan = {"subjects": args.subjects, "scenarios": args.scenarios, "reps": args.reps,
            "runs": len(args.subjects) * len(args.scenarios) * args.reps}
    if args.dry_run:
        if args.json:
            return emit_json(True, "ok", f"{plan['runs']} runs planned", plan)
        print(f"{plan['runs']} runs: {len(args.subjects)} subjects x {len(args.scenarios)} scenarios x {args.reps} reps")
        for sid in args.subjects:
            print(f"  {sid}" + ("  (billed; $0 rows are recorded as unknown)" if sid.startswith(BILLED_BASES) else ""))
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = REPORT_DIR / f"model-matrix-{stamp}.jsonl"
    tok = main_tok if on_main else ""  # sandbox members need no token
    log = (lambda s: print(s, file=sys.stderr, flush=True)) if args.json else (lambda s: print(s, flush=True))
    rows, failed_cells = run(args.subjects, args.scenarios, host=args.host, tok=tok, reps=args.reps,
                             jsonl_path=jsonl_path, timeout=args.timeout, log=log)
    summary = summarize(rows, args.subjects)
    ok = failed_cells == 0

    if args.json:
        msg = f"{len(rows)} runs" + ("" if ok else f"; {failed_cells} cell(s) failed to run")
        return emit_json(ok, "ok" if ok else "error", msg, {"summary": summary, "jsonl": str(jsonl_path)})
    print(f"\nsubject | passed | cost | recorded model   ({jsonl_path.name})")
    for s in summary:
        cost = "unknown" if s["cost_usd"] is None else f"${s['cost_usd']:.4f}"
        errs = f" ({s['errors']} errored)" if s["errors"] else ""
        print(f"{s['subject']} | {s['passed']}/{s['runs']}{errs} | {cost} | {s['models']}")
    if not ok:
        print(f"\n{failed_cells} cell(s) failed to run - see the error rows in the JSONL", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
