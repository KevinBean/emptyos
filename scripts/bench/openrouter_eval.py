"""Benchmark free think providers on the task shapes EmptyOS uses most.

Compares three $0-marginal-cost providers head-to-head:
  - DeepSeek V4 Flash via OpenRouter (free tier)
  - Claude CLI (free under Anthropic Max subscription)
  - Local Ollama (qwen3.5)

Each provider is called via its native channel — same routes EmptyOS uses
in production. Writes a markdown report to the vault at
`10_Projects/emptyos/bench/<date>-free-providers-eval.md`.

Usage (PowerShell):
    $env:OPENROUTER_API_KEY = "sk-or-v1-..."   # only required for the OpenRouter row
    python scripts/bench/openrouter_eval.py

The script is intentionally self-contained — stdlib only, no SDK imports,
no EmptyOS kernel boot. Re-run when provider availability or pricing shifts.

Two tasks, picked because they bracket the cheap/quality axis for daily
EmptyOS think() calls:

  1. note-distillation  — paste a conversation, extract atomic KB notes.
     High-stakes quality path. Does free DeepSeek match Claude's quality?

  2. breadcrumb-gen     — git commit -> one-sentence journal entry.
     High-frequency cheap path. Free latency-and-cost winner if quality clears the bar.

Inputs are synthetic so the harness contains no personal vault content.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ─── Config ─────────────────────────────────────────────────────────────
#
# FREE PROVIDERS ONLY — Kevin's constraint 2026-05-16: bench must not bill.
# Compares DeepSeek V4 Flash (via OpenRouter, free tier) head-to-head with
# Claude CLI (free via Max sub) and local Ollama (free local). Each provider
# is called via its native channel — same routes EmptyOS uses in production.


def _resolve_vault() -> Path:
    """Read notes.path from emptyos.toml; honour EOS_VAULT env override."""
    env = os.environ.get("EOS_VAULT")
    if env:
        return Path(env)
    repo_root = Path(__file__).resolve().parents[2]
    toml_path = repo_root / "emptyos.toml"
    if toml_path.exists():
        with toml_path.open("rb") as f:
            cfg = tomllib.load(f)
        p = cfg.get("notes", {}).get("path")
        if p:
            return Path(p)
    raise SystemExit(
        "Set EOS_VAULT=<vault-path> or configure [notes] path in emptyos.toml."
    )


VAULT = _resolve_vault()
REPORT_DIR = VAULT / "10_Projects" / "emptyos" / "bench"

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "deepseek/deepseek-v4-flash:free"

OLLAMA_ENDPOINT = "http://localhost:11434/api/chat"  # native — supports think:false
OLLAMA_MODEL = "qwen3.5:latest"

CLAUDE_BIN = shutil.which("claude") or "claude"
CLAUDE_MODEL = ""  # empty = use CLI default (whatever your `claude` resolves to)

TIMEOUT_S = 180

# ─── Task definitions ───────────────────────────────────────────────────

TASK_DISTILL_SYSTEM = """You are extracting durable knowledge from a conversation transcript.

Output 3-5 atomic markdown notes. Each note has:
- A short slug-style title (kebab-case)
- YAML frontmatter with: tag (single word), tags (list), created (today's date)
- A short body (2-4 sentences) capturing one self-contained idea
- Wiki-style links [[other-slug]] to sibling notes where the idea relates

Notes must be atomic — one idea per note. Do not duplicate content across notes.
Do not invent facts not present in the transcript.

Format your output as N markdown blocks, separated by `---` on its own line."""

TASK_DISTILL_INPUTS = [
    {
        "id": "transcript-1",
        "user": """Here is the transcript:

A: I've been thinking about how to position our product. The default framing is "AI assistant" but that puts us in the same category as Siri and ChatGPT.

B: Right, and that category is saturated. What's the actual differentiator?

A: Local-first. Data stays on your machine. The model reads your notes, not the public internet.

B: That's a stance, not a benefit. A normal user doesn't know why local matters.

A: Fair. The benefit is: AI that actually knows your stuff. Because you control the data, the model can read your real life, not generic web content.

B: That's much stronger. "Personal AI built on your own data."

A: Yes. And it's honest — we don't own the model, the user owns the data. The slogan should reflect what's actually owned.

B: Agreed. Avoid claiming the AI is "yours" — that's untrue. The data is yours, the system is yours, the model is rented.""",
    },
    {
        "id": "transcript-2",
        "user": """Here is the transcript:

A: Why are we benchmarking models now? We've been using Claude CLI for months.

B: Two reasons. First, OpenRouter just landed in the provider chain as the last fallback, and we have no data on whether DeepSeek V4 Flash free tier is actually usable. Second, the high-frequency low-stakes paths — reactor breadcrumbs, capture summaries — are paying for Claude when they probably don't need to.

A: So the question is "can free DeepSeek replace paid Claude for the bulk path?"

B: Exactly. And independently — "is paid DeepSeek competitive with paid Claude for the quality path?" Different tradeoff, different answer probably.

A: What's the test shape?

B: Two tasks. Note distillation (paste a chat, get atomic KB notes). Breadcrumb gen (commit message, get one-sentence journal entry). They bracket the spectrum — one needs quality, the other needs latency-and-cost.

A: Not code generation?

B: That's already covered in the existing bench. We don't need to retest something we already have data on.""",
    },
]

TASK_BREADCRUMB_SYSTEM = """You write one-sentence journal entries summarizing git commits.

Style: past tense, active voice, names the change in plain language, no jargon.
Length: 12-25 words. One sentence. No emoji, no markdown, no quotation marks."""

TASK_BREADCRUMB_INPUTS = [
    {
        "id": "commit-1",
        "user": "Commit: feat(rooms): sandboxed [DO:rooms.write_note] + toolless agent guard\n\nFiles changed: 8\nInsertions: 320\nDeletions: 45",
    },
    {
        "id": "commit-2",
        "user": "Commit: fix(release-public): exclude gitignored apps from private-app check\n\nFiles changed: 2\nInsertions: 18\nDeletions: 4",
    },
    {
        "id": "commit-3",
        "user": "Commit: refactor(sdk): extract assert_single_line guard from journal to emptyos.sdk.text_guards\n\nFiles changed: 4\nInsertions: 67\nDeletions: 22",
    },
]

TASKS = [
    {
        "id": "note-distillation",
        "system": TASK_DISTILL_SYSTEM,
        "inputs": TASK_DISTILL_INPUTS,
        "max_tokens": 1500,
    },
    {
        "id": "breadcrumb-gen",
        "system": TASK_BREADCRUMB_SYSTEM,
        "inputs": TASK_BREADCRUMB_INPUTS,
        "max_tokens": 80,
    },
]

# ─── Wire — one caller per provider ─────────────────────────────────────

def _http_chat(endpoint: str, headers: dict, model: str, system: str, user: str, max_tokens: int) -> dict:
    """Shared OpenAI-compatible chat completion POST."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read())
        elapsed = time.monotonic() - t0
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        return {"ok": True, "elapsed_s": round(elapsed, 2),
                "content": msg.get("content") or "",
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "finish_reason": choice.get("finish_reason")}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        return {"ok": False, "elapsed_s": round(time.monotonic() - t0, 2),
                "error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"ok": False, "elapsed_s": round(time.monotonic() - t0, 2),
                "error": f"{type(e).__name__}: {e}"}


def call_openrouter(system: str, user: str, max_tokens: int) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {"ok": False, "error": "OPENROUTER_API_KEY not set"}
    return _http_chat(
        OPENROUTER_ENDPOINT,
        headers={"Authorization": f"Bearer {key}",
                 "HTTP-Referer": "https://github.com/emptyos",
                 "X-Title": "EmptyOS bench"},
        model=OPENROUTER_MODEL, system=system, user=user, max_tokens=max_tokens,
    )


def call_ollama(system: str, user: str, max_tokens: int) -> dict:
    """Ollama native /api/chat — supports think:false to disable qwen3 reasoning
    tokens that the OpenAI-compat /v1/ endpoint silently strips, leaving empty
    content. See memory: feedback_qwen3_streaming_think_false.
    """
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "think": False,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_ENDPOINT, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read())
        elapsed = time.monotonic() - t0
        msg = data.get("message") or {}
        return {"ok": True, "elapsed_s": round(elapsed, 2),
                "content": msg.get("content") or "",
                "prompt_tokens": data.get("prompt_eval_count"),
                "completion_tokens": data.get("eval_count"),
                "finish_reason": "stop" if data.get("done") else "length"}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        return {"ok": False, "elapsed_s": round(time.monotonic() - t0, 2),
                "error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"ok": False, "elapsed_s": round(time.monotonic() - t0, 2),
                "error": f"{type(e).__name__}: {e}"}


def call_claude_cli(system: str, user: str, max_tokens: int) -> dict:
    """Spawn `claude -p` with system + user. Free under Max subscription.

    max_tokens is advisory — claude CLI doesn't accept it directly; we rely on
    the prompt itself to bound output (system prompts already do this).
    """
    cmd = [CLAUDE_BIN, "-p", "--output-format", "text",
           "--no-session-persistence",
           "--dangerously-skip-permissions",
           "--allowedTools", "",  # no tools — pure text completion
           "--append-system-prompt", system,
           user]
    if CLAUDE_MODEL:
        cmd[1:1] = ["--model", CLAUDE_MODEL]

    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=TIMEOUT_S, encoding="utf-8", errors="replace")
        elapsed = time.monotonic() - t0
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "")[:500]
            return {"ok": False, "elapsed_s": round(elapsed, 2),
                    "error": f"exit {proc.returncode}: {err.strip()}"}
        return {"ok": True, "elapsed_s": round(elapsed, 2),
                "content": (proc.stdout or "").strip(),
                "prompt_tokens": None, "completion_tokens": None,
                "finish_reason": "stop"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "elapsed_s": TIMEOUT_S,
                "error": f"timed out after {TIMEOUT_S}s"}
    except FileNotFoundError:
        return {"ok": False, "elapsed_s": 0,
                "error": f"`claude` not found on PATH (looked for {CLAUDE_BIN!r})"}
    except Exception as e:
        return {"ok": False, "elapsed_s": round(time.monotonic() - t0, 2),
                "error": f"{type(e).__name__}: {e}"}


PROVIDERS = [
    {"label": "deepseek-v4-flash:free (openrouter)", "fn": call_openrouter},
    {"label": "claude (cli, Max sub)",               "fn": call_claude_cli},
    {"label": f"ollama ({OLLAMA_MODEL}, local)",     "fn": call_ollama},
]


def fmt_md_row(label: str, input_id: str, r: dict) -> str:
    if not r.get("ok"):
        return f"| `{label}` | `{input_id}` | ❌ | {r.get('elapsed_s', '?')}s | — | — | {r.get('error', '?')[:80]} |"
    p = r.get("prompt_tokens")
    c = r.get("completion_tokens")
    p_s = p if p is not None else "—"
    c_s = c if c is not None else "—"
    return f"| `{label}` | `{input_id}` | ✅ | {r['elapsed_s']}s | {p_s} | {c_s} | {r.get('finish_reason') or '-'} |"


def run() -> None:
    if not VAULT.exists():
        print(f"ERROR: vault path not found at {VAULT}", file=sys.stderr)
        sys.exit(1)

    # Pre-flight: tell user which providers will run vs. be skipped.
    print("Providers:")
    if os.environ.get("OPENROUTER_API_KEY"):
        print(f"  ✓ openrouter ({OPENROUTER_MODEL})")
    else:
        print("  ✗ openrouter — OPENROUTER_API_KEY not set; will report as error per row")
    if shutil.which("claude"):
        print(f"  ✓ claude-cli ({CLAUDE_BIN})")
    else:
        print("  ✗ claude-cli — `claude` not on PATH; will report as error per row")
    print(f"  ? ollama ({OLLAMA_MODEL}) — will be probed on first call")
    print()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORT_DIR / f"{date_str}-free-providers-eval.md"

    results: list[dict] = []
    total_calls = sum(len(t["inputs"]) for t in TASKS) * len(PROVIDERS)
    n = 0

    for task in TASKS:
        for inp in task["inputs"]:
            for prov in PROVIDERS:
                n += 1
                print(f"[{n}/{total_calls}] {prov['label']}  task={task['id']}  input={inp['id']}", flush=True)
                r = prov["fn"](task["system"], inp["user"], task["max_tokens"])
                r["provider"] = prov["label"]
                r["task"] = task["id"]
                r["input_id"] = inp["id"]
                results.append(r)
                time.sleep(0.3)

    # ── Render report ──────────────────────────────────────────────────
    lines: list[str] = []
    lines.append("---")
    lines.append("tag: bench")
    lines.append("tags:")
    lines.append("  - bench")
    lines.append("  - free-providers")
    lines.append("  - deepseek")
    lines.append("  - claude")
    lines.append("  - ollama")
    lines.append(f"created: {date_str}")
    lines.append("---")
    lines.append("")
    lines.append("# Free-Provider Evaluation — DeepSeek vs Claude vs Ollama")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("Three $0-marginal-cost providers compared head-to-head on the "
                 "two task shapes that dominate daily EmptyOS think() calls. "
                 "Quality is not auto-scored — read the outputs and decide which "
                 "provider clears your bar for which path.")
    lines.append("")
    lines.append("**Providers tested:**")
    lines.append("")
    lines.append(f"- `{OPENROUTER_MODEL}` via OpenRouter free tier")
    lines.append(f"- `claude` CLI (free with Anthropic Max subscription)")
    lines.append(f"- `{OLLAMA_MODEL}` local via Ollama")
    lines.append("")
    lines.append("## Summary table")
    lines.append("")
    lines.append("| Provider | Input | OK | Latency | In tok | Out tok | Finish |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(fmt_md_row(r["provider"], f"{r['task']}::{r['input_id']}", r))
    lines.append("")

    for task in TASKS:
        lines.append(f"## Task: `{task['id']}`")
        lines.append("")
        lines.append("**System prompt:**")
        lines.append("")
        lines.append("```")
        lines.append(task["system"])
        lines.append("```")
        lines.append("")
        for inp in task["inputs"]:
            lines.append(f"### Input `{inp['id']}`")
            lines.append("")
            lines.append("**User message:**")
            lines.append("")
            lines.append("```")
            lines.append(inp["user"])
            lines.append("```")
            lines.append("")
            for r in results:
                if r["task"] != task["id"] or r["input_id"] != inp["id"]:
                    continue
                lines.append(f"#### `{r['provider']}`")
                lines.append("")
                if not r.get("ok"):
                    lines.append(f"**Error:** {r.get('error')}")
                    lines.append("")
                    continue
                lines.append(f"_{r['elapsed_s']}s · "
                             f"in={r.get('prompt_tokens')} · "
                             f"out={r.get('completion_tokens')} · "
                             f"finish={r.get('finish_reason')}_")
                lines.append("")
                lines.append("```")
                lines.append(r["content"])
                lines.append("```")
                lines.append("")

    # Raw JSON appendix for re-analysis
    lines.append("## Raw results (JSON)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(results, indent=2, ensure_ascii=False))
    lines.append("```")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to {report_path}", flush=True)
    print(f"Calls: {len(results)}  · "
          f"ok: {sum(1 for r in results if r.get('ok'))}  · "
          f"failed: {sum(1 for r in results if not r.get('ok'))}", flush=True)


if __name__ == "__main__":
    run()
