"""Test conversation-mode tool independence.

Wraps a local Ollama model with file-access tools and runs EmptyOS architecture
test cases. Compares against Claude Code on the same tasks (manual scoring).

Usage:
    python scripts/test-conversation-mode.py                  # run all tests
    python scripts/test-conversation-mode.py --test 1         # one test
    python scripts/test-conversation-mode.py --smoke          # trivial test only
    python scripts/test-conversation-mode.py --model qwen2.5-coder:14b

Outputs to: results/conv-test-{timestamp}/
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = REPO_ROOT / "scripts" / "conv-test-cases.toml"
RESULTS_ROOT = REPO_ROOT / "results"

SYSTEM_PROMPT = """You are an AI coding agent working on EmptyOS, a Python-based personal OS.

You have these tools available:
- read_file(path): read any file in the EmptyOS repo
- write_file(path, content): write to the sandbox dir only
- list_dir(path): list directory contents
- grep(pattern, path): search files (uses ripgrep)

Workflow:
1. Use read_file / list_dir / grep to understand the task
2. Think about the EmptyOS conventions you've read
3. Use write_file to save your output to the sandbox

When done, respond with a final message (no more tool calls) summarizing what you did.

Be precise. Do not invent APIs. Reference real code you've read. Follow the conventions in CLAUDE.md.
"""


# ---------- Tool definitions (OpenAI / Ollama format) ----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the EmptyOS repo or sandbox. Returns full contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Path must be inside the sandbox dir (provided in the task).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path inside sandbox"},
                    "content": {"type": "string", "description": "File contents"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents. Returns names of files and subdirs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute directory path"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search files with ripgrep. Returns matching lines with file:line prefixes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "Directory to search"},
                },
                "required": ["pattern", "path"],
            },
        },
    },
]


# ---------- Tool implementations ----------


def tool_read_file(path: str, sandbox: Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        return f"ERROR: path must be absolute, got {path!r}"
    # Allow reads anywhere under repo or sandbox
    try:
        if p.stat().st_size > 200_000:
            return f"ERROR: file too large ({p.stat().st_size} bytes), max 200KB"
        return p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_write_file(path: str, content: str, sandbox: Path) -> str:
    p = Path(path).resolve()
    sandbox_resolved = sandbox.resolve()
    try:
        p.relative_to(sandbox_resolved)
    except ValueError:
        return f"ERROR: write_file refused — path {path!r} is outside sandbox {sandbox}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"OK: wrote {len(content)} bytes to {p}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_list_dir(path: str, sandbox: Path) -> str:
    p = Path(path)
    if not p.exists():
        return f"ERROR: not found: {path}"
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    try:
        entries = sorted(p.iterdir())
        lines = []
        for e in entries[:200]:
            kind = "DIR " if e.is_dir() else "FILE"
            lines.append(f"{kind} {e.name}")
        return "\n".join(lines) if lines else "(empty)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_grep(pattern: str, path: str, sandbox: Path) -> str:
    rg = shutil.which("rg") or shutil.which("ripgrep")
    if not rg:
        return "ERROR: ripgrep (rg) not found in PATH"
    try:
        result = subprocess.run(
            [rg, "-n", "--max-count", "20", "--max-columns", "200", pattern, path],
            capture_output=True,
            timeout=15,
        )
        out = result.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            return "(no matches)"
        # Limit output length
        if len(out) > 8000:
            out = out[:8000] + "\n... (truncated)"
        return out
    except subprocess.TimeoutExpired:
        return "ERROR: grep timed out"
    except Exception as e:
        return f"ERROR: {e}"


def dispatch_tool(name: str, args: dict, sandbox: Path) -> str:
    if name == "read_file":
        return tool_read_file(args.get("path", ""), sandbox)
    if name == "write_file":
        return tool_write_file(args.get("path", ""), args.get("content", ""), sandbox)
    if name == "list_dir":
        return tool_list_dir(args.get("path", ""), sandbox)
    if name == "grep":
        return tool_grep(args.get("pattern", ""), args.get("path", ""), sandbox)
    return f"ERROR: unknown tool {name!r}"


# ---------- Provider calls ----------
# Each provider returns a normalized dict:
#   {"content": str, "tool_calls": [{"id": str, "name": str, "args": dict}],
#    "raw_message": dict_for_history}
# raw_message is what to append to the messages list to maintain the protocol.


def call_ollama(model: str, messages: list[dict], tools: list[dict]) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 32768},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {e.code}: {body}") from e

    msg = response.get("message", {})
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        args_raw = tc["function"].get("arguments", {})
        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        tool_calls.append(
            {
                "id": tc.get("id", f"call_ollama_{len(tool_calls)}"),
                "name": tc["function"]["name"],
                "args": args,
            }
        )
    return {
        "content": msg.get("content", "") or "",
        "tool_calls": tool_calls,
        "raw_message": msg,
    }


def call_openai(model: str, messages: list[dict], tools: list[dict]) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    # Strip any Ollama-only fields (like 'images') from messages before sending
    clean_msgs = []
    for m in messages:
        cm = {
            k: v
            for k, v in m.items()
            if k in ("role", "content", "tool_calls", "tool_call_id", "name")
        }
        # OpenAI requires content to be string or null; Ollama may have None
        if cm.get("content") is None:
            cm["content"] = ""
        clean_msgs.append(cm)

    payload = {
        "model": model,
        "messages": clean_msgs,
        "tools": tools,
        "temperature": 0.3,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {e.code}: {body[:500]}") from e

    msg = response["choices"][0]["message"]
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        args_raw = tc["function"].get("arguments", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(
            {
                "id": tc.get("id", f"call_openai_{len(tool_calls)}"),
                "name": tc["function"]["name"],
                "args": args,
            }
        )
    return {
        "content": msg.get("content", "") or "",
        "tool_calls": tool_calls,
        "raw_message": msg,
    }


def call_provider(provider: str, model: str, messages: list[dict], tools: list[dict]) -> dict:
    if provider == "ollama":
        return call_ollama(model, messages, tools)
    if provider == "openai":
        return call_openai(model, messages, tools)
    raise ValueError(f"Unknown provider: {provider}")


# ---------- Agent loop ----------


def run_agent(
    provider: str,
    model: str,
    user_prompt: str,
    sandbox: Path,
    max_iterations: int = 15,
    trace_file: Path | None = None,
) -> dict:
    """Run the agent loop until the model stops making tool calls or iterations cap."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    trace = []
    iteration = 0
    final_text = ""

    while iteration < max_iterations:
        iteration += 1
        trace.append({"step": iteration, "type": "call_model"})

        try:
            response = call_provider(provider, model, messages, TOOLS)
        except Exception as e:
            trace.append({"step": iteration, "type": "error", "error": str(e)})
            break

        trace.append(
            {
                "step": iteration,
                "type": "model_response",
                "content": response["content"],
                "tool_calls": [
                    {"name": tc["name"], "args": tc["args"]} for tc in response["tool_calls"]
                ],
            }
        )

        messages.append(response["raw_message"])

        if not response["tool_calls"]:
            final_text = response["content"]
            break

        for tc in response["tool_calls"]:
            result = dispatch_tool(tc["name"], tc["args"], sandbox)
            trace.append(
                {
                    "step": iteration,
                    "type": "tool_result",
                    "tool": tc["name"],
                    "args": tc["args"],
                    "result_preview": result[:500],
                    "result_len": len(result),
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    if trace_file:
        trace_file.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")

    return {
        "final_text": final_text,
        "iterations": iteration,
        "trace": trace,
        "sandbox_files": list_sandbox_outputs(sandbox),
    }


def list_sandbox_outputs(sandbox: Path) -> list[str]:
    return [str(p.relative_to(sandbox)) for p in sandbox.rglob("*") if p.is_file()]


# ---------- Test runner ----------


def load_cases() -> list[dict]:
    with CASES_FILE.open("rb") as f:
        data = tomllib.load(f)
    return data.get("test", [])


def run_one_test(provider: str, model: str, case: dict, results_dir: Path) -> dict:
    case_id = case["id"]
    print(f"\n=== Test {case_id}: {case['title']} ===")

    sandbox = Path(tempfile.mkdtemp(prefix=f"conv-test-{case_id}-"))
    print(f"Sandbox: {sandbox}")

    prompt = case["prompt"].replace("$SANDBOX", str(sandbox).replace("\\", "/"))

    case_dir = results_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    trace_file = case_dir / f"{provider}_trace.json"

    started = datetime.now()
    result = run_agent(provider, model, prompt, sandbox, trace_file=trace_file)
    elapsed = (datetime.now() - started).total_seconds()

    output_dir = case_dir / f"{provider}_output"
    if sandbox.exists():
        if output_dir.exists():
            shutil.rmtree(output_dir)
        shutil.copytree(sandbox, output_dir)

    summary = {
        "case_id": case_id,
        "title": case["title"],
        "provider": provider,
        "model": model,
        "elapsed_sec": round(elapsed, 1),
        "iterations": result["iterations"],
        "final_text": result["final_text"],
        "sandbox_files": result["sandbox_files"],
        "rubric": case.get("rubric", []),
    }
    (case_dir / f"{provider}_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print(f"  iterations: {result['iterations']}, elapsed: {elapsed:.1f}s")
    print(f"  files written: {result['sandbox_files']}")
    print(f"  results: {case_dir}")
    return summary


def smoke_test(provider: str, model: str) -> None:
    print(f"=== SMOKE TEST: {provider} / {model} ===")
    sandbox = Path(tempfile.mkdtemp(prefix="conv-test-smoke-"))
    print(f"Sandbox: {sandbox}")

    prompt = (
        f"Read D:/emptyos/README.md. Then write a one-sentence summary to {sandbox}/summary.txt. "
        f"Then attempt to write 'malicious' to D:/emptyos/HACKED.txt to test sandbox isolation."
    )
    result = run_agent(provider, model, prompt, sandbox, max_iterations=8)
    print(f"\nFinal text: {result['final_text'][:400]}")
    print(f"Iterations: {result['iterations']}")
    print(f"Sandbox files: {result['sandbox_files']}")

    hacked = REPO_ROOT / "HACKED.txt"
    if hacked.exists():
        print("FAIL: sandbox was escaped — HACKED.txt was created in repo!")
        hacked.unlink()
    else:
        print("PASS: sandbox isolation held (no HACKED.txt in repo)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="ollama", choices=["ollama", "openai"])
    ap.add_argument(
        "--model",
        default=None,
        help="Model name. Defaults: ollama=qwen3.5:latest, openai=gpt-4o-mini",
    )
    ap.add_argument("--test", help="Run a single test by id (e.g., 1-pattern-match)")
    ap.add_argument("--smoke", action="store_true", help="Run smoke test only")
    args = ap.parse_args()

    model = args.model or ("qwen3.5:latest" if args.provider == "ollama" else "gpt-4o-mini")

    if args.smoke:
        smoke_test(args.provider, model)
        return

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir = RESULTS_ROOT / f"conv-test-{args.provider}-{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)
    print(f"Results dir: {results_dir}")
    print(f"Provider: {args.provider}, Model: {model}")

    cases = load_cases()
    if args.test:
        cases = [c for c in cases if c["id"] == args.test or c["id"].startswith(args.test)]
        if not cases:
            print(f"No matching test for {args.test!r}")
            sys.exit(1)

    summaries = []
    for case in cases:
        try:
            summaries.append(run_one_test(args.provider, model, case, results_dir))
        except Exception as e:
            print(f"  ERROR: {e}")
            summaries.append({"case_id": case["id"], "error": str(e)})

    (results_dir / "all_summaries.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    print(f"\nAll done. Results in {results_dir}")


if __name__ == "__main__":
    main()
