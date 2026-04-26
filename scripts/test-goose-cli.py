"""Run the same EmptyOS conversation-mode tests through the Goose AI agent.

Goose (github.com/block/goose) is an open-source agentic shell similar to
Claude Code. This tests the AGENT (Goose) with a configurable MODEL (OpenAI,
Ollama, etc.) — complementing test-conversation-mode.py (my harness) and
test-codex-cli.py (OpenAI Codex CLI).

Usage:
    python scripts/test-goose-cli.py --provider openai --model gpt-4o
    python scripts/test-goose-cli.py --provider ollama --model qwen3.5:latest
    python scripts/test-goose-cli.py --test 1                  # one test
    python scripts/test-goose-cli.py --smoke                   # trivial test only

Outputs to: results/conv-test-goose-{provider}-{timestamp}/
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = REPO_ROOT / "scripts" / "conv-test-cases.toml"
RESULTS_ROOT = REPO_ROOT / "results"


def load_cases() -> list[dict]:
    with CASES_FILE.open("rb") as f:
        data = tomllib.load(f)
    return data.get("test", [])


def git_pollution_check(ignore_prefixes: tuple[str, ...] = ("results/",)) -> list[str]:
    """Return list of paths modified outside the allowed prefixes (empty = clean)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            return [f"(git status failed: exit {result.returncode})"]
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        return [f"(git status exception: {e})"]

    polluted = []
    for line in lines:
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].strip()
        # Ignore untracked dirs under ignored prefixes
        if any(path.startswith(p) for p in ignore_prefixes):
            continue
        # These states indicate modification to tracked files or new tracked files
        if status.strip() in ("M", "A", "D", "R", "C", "AM", "MM", "??"):
            # Only flag actual changes — preexisting untracked files are fine if we snapshot them
            polluted.append(f"{status} {path}")
    return polluted


def parse_goose_json(stdout: str) -> dict:
    """Goose emits some preamble then a JSON blob. Find the first { and parse."""
    start = stdout.find("{")
    if start < 0:
        return {}
    try:
        return json.loads(stdout[start:])
    except json.JSONDecodeError:
        # Try to find the last balanced JSON object
        # Fall back: give up
        return {}


def extract_final_text(goose_json: dict) -> str:
    """Get the last assistant message's text content."""
    msgs = goose_json.get("messages", []) or []
    for msg in reversed(msgs):
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("content", []) or []:
            if block.get("type") == "text" and block.get("text"):
                return block["text"]
    return ""


def extract_tool_calls(goose_json: dict) -> list[dict]:
    """Return a brief trace of tool calls for observability."""
    trace = []
    for msg in goose_json.get("messages", []) or []:
        for block in msg.get("content", []) or []:
            if block.get("type") == "toolRequest":
                tr = block.get("toolRequest", {})
                call = tr.get("tool_call", {})
                val = call.get("value", {}) if isinstance(call, dict) else {}
                trace.append({
                    "id": tr.get("id"),
                    "tool": val.get("name"),
                    "args_preview": str(val.get("arguments", ""))[:200],
                })
            elif block.get("type") == "toolResponse":
                tr = block.get("toolResponse", {})
                result = tr.get("toolResult", {})
                content = result.get("value", {}).get("content", []) if isinstance(result, dict) else []
                text = content[0].get("text", "") if content else ""
                trace.append({
                    "id": tr.get("id"),
                    "result_preview": text[:200],
                })
    return trace


def run_one_test(provider: str, model: str, case: dict, results_dir: Path,
                 snapshot_baseline: set[str]) -> dict:
    case_id = case["id"]
    print(f"\n=== Test {case_id}: {case['title']} ===")

    sandbox = results_dir / case_id / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    print(f"Sandbox: {sandbox}")

    raw_prompt = case["prompt"].replace("$SANDBOX", str(sandbox).replace("\\", "/"))
    sandbox_path = str(sandbox).replace("\\", "/")
    prompt = (
        f"Complete this task in the EmptyOS repo at your cwd ({REPO_ROOT}). "
        f"Read source files as needed (CLAUDE.md, apps/, docs/, etc.) but write all output ONLY to {sandbox_path}/ — "
        f"do NOT modify, create, or delete any files outside {sandbox_path}/. "
        f"Execute now without asking for confirmation.\n\n"
        f"{raw_prompt}"
    )

    case_dir = results_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    log_file = case_dir / "goose_stdout.log"
    json_file = case_dir / "goose_raw.json"

    goose_bin = shutil.which("goose")
    if not goose_bin:
        msg = "ERROR: goose CLI not found in PATH"
        log_file.write_text(msg, encoding="utf-8")
        return {"case_id": case_id, "error": msg}

    cmd = [
        goose_bin, "run",
        "--no-session",
        "-t", prompt,
        "--provider", provider,
        "--model", model,
        "--output-format", "json",
        "--quiet",
        "--max-turns", "25",
        "--max-tool-repetitions", "5",
    ]

    env = os.environ.copy()
    # Defensive: ensure auto mode is explicit (default) but approvals stay off for automation.
    # If GOOSE_MODE was set to something interactive in config, override to auto.
    env["GOOSE_MODE"] = "auto"

    started = datetime.now()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, timeout=900,
            cwd=str(REPO_ROOT),
            env=env,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        log_file.write_text(stdout + "\n--- STDERR ---\n" + stderr, encoding="utf-8")
        return_code = result.returncode
    except subprocess.TimeoutExpired:
        log_file.write_text("ERROR: goose run timed out (15min)", encoding="utf-8")
        return_code = -1
        stdout = ""
    elapsed = (datetime.now() - started).total_seconds()

    goose_json = parse_goose_json(stdout)
    if goose_json:
        json_file.write_text(json.dumps(goose_json, indent=2), encoding="utf-8")

    final_text = extract_final_text(goose_json)
    tool_trace = extract_tool_calls(goose_json)
    (case_dir / "goose_tool_trace.json").write_text(
        json.dumps(tool_trace, indent=2), encoding="utf-8"
    )

    sandbox_files = []
    if sandbox.exists():
        sandbox_files = [str(p.relative_to(sandbox)) for p in sandbox.rglob("*") if p.is_file()]

    # Pollution check: compare to baseline snapshot
    current_snapshot = _git_snapshot()
    new_changes = current_snapshot - snapshot_baseline
    outside_results = [
        c for c in new_changes
        if "results/" not in c  # any path under results/ is fine (including our sandbox)
    ]
    if outside_results:
        print(f"  ! POLLUTION DETECTED: {len(outside_results)} paths changed outside results/")
        for p in outside_results[:10]:
            print(f"    {p}")

    summary = {
        "case_id": case_id,
        "title": case["title"],
        "provider": "goose",
        "goose_provider": provider,
        "goose_model": model,
        "elapsed_sec": round(elapsed, 1),
        "return_code": return_code,
        "final_text": final_text[:4000],
        "tool_call_count": sum(1 for t in tool_trace if t.get("tool")),
        "sandbox_files": sandbox_files,
        "pollution_outside_results": outside_results,
        "rubric": case.get("rubric", []),
    }
    (case_dir / "goose_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print(f"  return_code: {return_code}, elapsed: {elapsed:.1f}s")
    print(f"  tool calls: {summary['tool_call_count']}, files written: {sandbox_files}")
    print(f"  results: {case_dir}")
    return summary


def _git_snapshot() -> set[str]:
    """Capture git status as a set of lines — for before/after diffing."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            return set(result.stdout.decode("utf-8", errors="replace").splitlines())
    except Exception:
        pass
    return set()


def smoke_test(provider: str, model: str) -> None:
    print(f"=== SMOKE TEST: goose / {provider} / {model} ===")
    sandbox = RESULTS_ROOT / "_goose_smoke"
    sandbox.mkdir(parents=True, exist_ok=True)

    sandbox_path = str(sandbox).replace("\\", "/")
    prompt = (
        f"Read D:/emptyos/README.md. Write a one-sentence summary to {sandbox_path}/summary.txt. "
        f"Do not modify any other files in the repo. Do not ask for confirmation."
    )

    goose_bin = shutil.which("goose")
    if not goose_bin:
        print("FAIL: goose not in PATH")
        return

    baseline = _git_snapshot()
    print(f"Sandbox: {sandbox}")

    cmd = [
        goose_bin, "run", "--no-session",
        "-t", prompt,
        "--provider", provider, "--model", model,
        "--output-format", "json", "--quiet",
        "--max-turns", "10",
    ]
    env = os.environ.copy()
    env["GOOSE_MODE"] = "auto"
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=300,
            cwd=str(REPO_ROOT), env=env,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: timeout")
        return

    stdout = result.stdout.decode("utf-8", errors="replace")
    goose_json = parse_goose_json(stdout)
    final = extract_final_text(goose_json)
    tool_count = sum(1 for t in extract_tool_calls(goose_json) if t.get("tool"))
    print(f"Return code: {result.returncode}")
    print(f"Tool calls: {tool_count}")
    print(f"Final text: {final[:300]}")

    summary_file = sandbox / "summary.txt"
    if summary_file.exists():
        print(f"PASS: summary written ({summary_file.stat().st_size} bytes)")
    else:
        print("FAIL: summary not written")

    # Pollution check
    after = _git_snapshot()
    new_changes = after - baseline
    outside = [c for c in new_changes if not c.startswith("results/") and not c.startswith("?? results/")]
    if outside:
        print(f"FAIL: pollution outside results/: {outside}")
    else:
        print("PASS: no pollution outside results/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai",
                    choices=["openai", "anthropic", "ollama", "gemini-cli", "claude-code", "databricks"])
    ap.add_argument("--model", default=None,
                    help="Model name. Defaults: openai=gpt-4o, ollama=qwen3.5:latest")
    ap.add_argument("--test", help="Run a single test by id")
    ap.add_argument("--smoke", action="store_true", help="Run smoke test only")
    args = ap.parse_args()

    defaults = {
        "openai": "gpt-4o",
        "ollama": "qwen3.5:latest",
        "anthropic": "claude-sonnet-4-5-20250929",
    }
    model = args.model or defaults.get(args.provider, "gpt-4o")

    if args.smoke:
        smoke_test(args.provider, model)
        return

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir = RESULTS_ROOT / f"conv-test-goose-{args.provider}-{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)
    print(f"Results dir: {results_dir}")
    print(f"Provider: {args.provider}, Model: {model}")

    cases = load_cases()
    if args.test:
        cases = [c for c in cases if c["id"] == args.test or c["id"].startswith(args.test)]
        if not cases:
            print(f"No matching test for {args.test!r}")
            sys.exit(1)

    # Capture baseline BEFORE any test runs
    baseline_snapshot = _git_snapshot()
    print(f"Baseline: {len(baseline_snapshot)} lines in git status")

    summaries = []
    for case in cases:
        try:
            summaries.append(run_one_test(args.provider, model, case, results_dir, baseline_snapshot))
        except Exception as e:
            print(f"  ERROR: {e}")
            summaries.append({"case_id": case["id"], "error": str(e)})

    (results_dir / "all_summaries.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )

    # Final pollution check
    final_snapshot = _git_snapshot()
    total_new = final_snapshot - baseline_snapshot
    outside = [c for c in total_new if not c.startswith("results/") and not c.startswith("?? results/")]
    if outside:
        print(f"\n! OVERALL POLLUTION: {len(outside)} paths outside results/")
        for p in outside[:20]:
            print(f"   {p}")
    else:
        print("\nOK: no pollution outside results/ - run is clean")

    print(f"\nAll done. Results in {results_dir}")


if __name__ == "__main__":
    main()
