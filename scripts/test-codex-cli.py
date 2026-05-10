"""Run the same EmptyOS conversation-mode tests through the OpenAI Codex CLI.

Codex is OpenAI's analog to Claude Code — a terminal coding agent with its own
sandbox and tools. This tests the AGENT, not just the model. For the model-only
test (same gpt-4o-mini, but driven by my harness), see test-conversation-mode.py
with --provider openai.

Usage:
    python scripts/test-codex-cli.py                   # run all 5 tests
    python scripts/test-codex-cli.py --test 1          # one test
    python scripts/test-codex-cli.py --model gpt-4o    # different model

Outputs to: results/conv-test-codex-{timestamp}/{case_id}/
"""

from __future__ import annotations

import argparse
import json
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


def run_one_test(model: str, case: dict, results_dir: Path) -> dict:
    case_id = case["id"]
    print(f"\n=== Test {case_id}: {case['title']} ===")

    # Use a sandbox INSIDE the EmptyOS repo so Codex's cwd can be the repo root
    # (mirrors how Claude Code subagents worked — cwd=D:/emptyos, writes to results/)
    sandbox = results_dir / case_id / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    print(f"Sandbox: {sandbox}")

    raw_prompt = case["prompt"].replace("$SANDBOX", str(sandbox).replace("\\", "/"))

    # Codex-specific: write a single continuous instruction so it doesn't split
    # the prompt into "setup + ask". Inline the sandbox path into the task.
    sandbox_path = str(sandbox).replace("\\", "/")
    prompt = (
        f"Complete this task in the EmptyOS repo at your cwd. "
        f"Read source files as needed (CLAUDE.md, apps/, etc.) but write all output ONLY to {sandbox_path}/ — "
        f"do not modify any existing project files. "
        f"Execute now without asking for confirmation.\n\n"
        f"{raw_prompt}"
    )

    case_dir = results_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    last_msg_file = case_dir / "codex_last_message.txt"
    log_file = case_dir / "codex_stdout.log"

    # Codex exec command:
    # --dangerously-bypass-approvals-and-sandbox: full automation, we control via cwd
    # -C <sandbox>: working directory (writes happen here)
    # -m <model>: which model to use
    # --skip-git-repo-check: sandbox isn't a git repo
    # -o: write final agent message to file
    # Reads happen from anywhere (no sandbox restriction at file-read level here)
    codex_bin = shutil.which("codex")
    if not codex_bin:
        msg = "ERROR: codex CLI not found in PATH"
        log_file.write_text(msg, encoding="utf-8")
        print(f"  {msg}")
        return {"case_id": case_id, "error": msg}

    # Codex CLI uses ChatGPT subscription auth which restricts models.
    # If model is "default", omit -m and use Codex's configured default.
    # cwd = D:/emptyos so Codex sees the actual project; sandbox is a subdir.
    cmd = [
        codex_bin,
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        str(REPO_ROOT),
        "-o",
        str(last_msg_file),
    ]
    if model and model != "default":
        cmd.extend(["-m", model])
    cmd.append(prompt)

    started = datetime.now()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=900,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        log_file.write_text(stdout + "\n--- STDERR ---\n" + stderr, encoding="utf-8")
        return_code = result.returncode
    except subprocess.TimeoutExpired:
        log_file.write_text("ERROR: codex exec timed out (15min)", encoding="utf-8")
        return_code = -1
    except FileNotFoundError:
        log_file.write_text("ERROR: codex CLI not found in PATH", encoding="utf-8")
        return_code = -1
    elapsed = (datetime.now() - started).total_seconds()

    # Sandbox is already inside results_dir — no copy needed
    sandbox_files = []
    if sandbox.exists():
        sandbox_files = [str(p.relative_to(sandbox)) for p in sandbox.rglob("*") if p.is_file()]

    final_text = ""
    if last_msg_file.exists():
        final_text = last_msg_file.read_text(encoding="utf-8", errors="replace")

    summary = {
        "case_id": case_id,
        "title": case["title"],
        "provider": "codex-cli",
        "model": model,
        "elapsed_sec": round(elapsed, 1),
        "return_code": return_code,
        "final_text": final_text[:4000],
        "sandbox_files": sandbox_files,
        "rubric": case.get("rubric", []),
    }
    (case_dir / "codex_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"  return_code: {return_code}, elapsed: {elapsed:.1f}s")
    print(f"  files written: {sandbox_files}")
    print(f"  results: {case_dir}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--model",
        default="default",
        help="Model name. 'default' uses Codex's configured default (recommended for ChatGPT subscription auth)",
    )
    ap.add_argument("--test", help="Run a single test by id")
    args = ap.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir = RESULTS_ROOT / f"conv-test-codex-{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)
    print(f"Results dir: {results_dir}")
    print(f"Model: {args.model}")

    cases = load_cases()
    if args.test:
        cases = [c for c in cases if c["id"] == args.test or c["id"].startswith(args.test)]
        if not cases:
            print(f"No matching test for {args.test!r}")
            sys.exit(1)

    summaries = []
    for case in cases:
        try:
            summaries.append(run_one_test(args.model, case, results_dir))
        except Exception as e:
            print(f"  ERROR: {e}")
            summaries.append({"case_id": case["id"], "error": str(e)})

    (results_dir / "all_summaries.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    print(f"\nAll done. Results in {results_dir}")


if __name__ == "__main__":
    main()
