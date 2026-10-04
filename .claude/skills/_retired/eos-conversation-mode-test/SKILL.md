# EmptyOS Conversation-Mode Test

Validate the **tool-independence claim** in `docs/DESIGN.md` "Three Runtime Modes" by running a local Ollama model through the same EmptyOS architecture tasks as Claude Code, then scoring the gap.

## When to Use

- User asks "can a local model run conversation mode?", "test ollama on the codebase", "is EmptyOS tool-independent?"
- After installing a new candidate Ollama model worth benchmarking
- Before making claims in docs/marketing about model portability
- After significant CLAUDE.md changes — does the new doc still scaffold a working scaffold via local model?

## What This Tests

The DESIGN.md "Three Runtime Modes" section claims any AI tool with file access + large context can do conversation mode (the system's growth runtime). This skill measures the gap between a local Ollama model and Claude Code on identical EmptyOS tasks.

It's **not a benchmark of model quality in general** — it's specifically: *can the model use CLAUDE.md to do EmptyOS work?*

## Required State

- Ollama running on `localhost:11434` (`curl -s localhost:11434/api/version` returns version)
- At least one model installed with **native tool-calling** support
- `scripts/test-conversation-mode.py` and `scripts/conv-test-cases.toml` exist (reference impl)
- Python with `tomllib` (3.11+) — already a project dep

---

## Procedure

### Step 1: Pick a Model with Working Tool Calling

**Critical gotcha:** Many Ollama models claim `tools` capability in `/api/show` but emit tool calls as JSON inside `content` instead of structured `tool_calls`. The harness needs structured calls.

Test each candidate before committing to a full run:

```bash
cat <<'EOF' > /tmp/tool-check.py
import json, urllib.request
def test(model):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "What is 5 + 7? Use the calc tool."}],
        "tools": [{"type":"function","function":{"name":"calc","description":"Calculate","parameters":{"type":"object","properties":{"expr":{"type":"string"}},"required":["expr"]}}}],
        "stream": False,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=data, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    msg = resp.get("message", {})
    return f"{model}: tool_calls={'YES' if msg.get('tool_calls') else 'NO (text only)'}, content={(msg.get('content') or '')[:80]!r}"

# Add candidate models here
for m in ["qwen3.5:latest", "gemma4:latest", "glm-4.7-flash:latest", "qwen2.5-coder:14b"]:
    print(test(m))
EOF
python /tmp/tool-check.py
```

| Confirmed working (as of 2026-04) | Confirmed broken |
|---|---|
| qwen3.5:latest (9.7B) — fast, balanced | qwen2.5-coder:14b (emits as text) |
| gemma4:latest (8B) — small, fast | |
| glm-4.7-flash:latest (29.9B MoE) — slowest, most capable | |
| llama3.2:latest (3B) — partial (mangled args) | |

### Step 2: Verify GPU + Context Window

Ollama defaults to `num_ctx=4096` which is too small for CLAUDE.md (~10K tokens). The harness sets `num_ctx=32768` in `options`. Confirm the chosen model fits:

```bash
curl -s http://localhost:11434/api/ps | python -m json.tool
```

Look for: `size_vram` (how much is on GPU), `context_length` (the negotiated context — should be 32768 after harness call).

If model is too big for GPU, Ollama spills to system RAM. That's fine but ~5-10x slower. Smaller models stay fully on GPU.

### Step 3: Run Smoke Test

Verify tool-call loop + sandbox isolation BEFORE running the full suite:

```bash
cd D:/emptyos
python scripts/test-conversation-mode.py --smoke --model <model_name>
```

Pass criteria:
- "PASS: sandbox isolation held" appears
- `summary.txt` written to sandbox
- Iterations between 2-8 (more = wasted, fewer = didn't actually use tools)

### Step 4: Run the Full 5-Test Suite

```bash
cd D:/emptyos
python scripts/test-conversation-mode.py --model <model_name>
```

**Run in background** (`run_in_background=true`) — full suite takes 4-30 min depending on model size. The script writes per-test results incrementally to `results/conv-test-{timestamp}/`.

Output structure:
```
results/conv-test-{timestamp}/
├── all_summaries.json              # aggregated metadata
├── 1-pattern-match/
│   ├── ollama_summary.json         # per-test metadata
│   ├── ollama_trace.json           # full tool-call trace
│   └── ollama_output/              # files the model wrote
├── 2-storage-decision/
└── ...
```

### Step 5: Run Claude Code Baseline (Parallel)

While Ollama runs, launch one general-purpose subagent per test in parallel. Use the prompts from `scripts/conv-test-cases.toml` but replace `$SANDBOX` with `D:/emptyos/results/claude-code-baseline/{test-id}/`.

Five parallel subagents — they share Claude's API but should complete in ~30s each in parallel.

### Step 6: Score Side-by-Side

Read each pair (Ollama output vs Claude Code output) and score against the rubric in `conv-test-cases.toml` AND quality dimensions:

| Dimension | What to look for |
|---|---|
| **Rubric pass rate** | Each rubric item: pass / fail. Count out of 25. |
| **Hallucinated APIs** | Functions called on `self.` that don't exist in `BaseApp` (grep `emptyos/sdk/base_app.py` to verify) |
| **Async correctness** | `await` only inside `async def`; sync calls don't have `await` |
| **Scope discipline** | Did the model add features beyond what was asked? Count extra methods/endpoints |
| **Architecture coherence** | Does it use real EmptyOS patterns (mixin decomposition, vault_config, reactor) or generic ones? |
| **Edge cases** | Case sensitivity, empty input, error handling — present or not? |

### Step 7: Write `scores.md`

Save the comparison to `results/conv-test-{timestamp}/scores.md` using the template from the **2026-04-12 baseline run** (`results/conv-test-20260412-122208/scores.md`). Required sections:

1. **Header** — date, models compared, total runtime
2. **Rubric Pass Rate** — table with per-test scores, total
3. **Quality Comparison** — per-test side-by-side table (rubric + quality dimensions)
4. **What Each Test Measured** — single-row summary per test, where the gap appeared
5. **Conclusion** — what the local model can/cannot do, tool-independence verdict
6. **Next experiments** — which models or prompts to try next

### Step 8: Update Memory

Append findings to `~/.claude/projects/D--emptyos/memory/project_ollama_vault_test.md` — date, model used, key numbers, link to scores.md.

If the gap is small enough (<3 rubric points + clean code), consider updating `AGENTS.md` with "tested local model" note and recommending the model.

---

## Key Gotchas

| Issue | Symptom | Fix |
|---|---|---|
| Tool calls as JSON text | `tool_calls=None` but `content` has `{"name":...}` | Switch model — qwen2.5-coder is unusable for this |
| Hung first iteration | Output file 0 bytes after 5+ min, no test files created | `num_ctx` too small — model can't fit input. Default 4K is broken. Set `num_ctx=32768` minimum |
| `await` on sync API | Generated code has `await self.vault_create_note(...)` | Both Ollama and Claude make this mistake — the API is sync. Note in scoring, don't fail rubric |
| Model emits 200+ lines for small task | Over-engineered scaffolds with 3 method variants | Smaller models lack scope discipline. Note in scoring under "scope discipline" |
| Claude Code parallel agents timeout | One of 5 subagents stuck | Re-launch just that one with same prompt; results should be deterministic enough |

---

## Files

| Path | Purpose |
|---|---|
| `scripts/test-conversation-mode.py` | The harness — Ollama agent loop with 4 tools |
| `scripts/conv-test-cases.toml` | 5 test definitions with rubrics |
| `results/conv-test-{ts}/` | Per-run outputs |
| `results/.gitignore` | Excludes test outputs from git |
| `~/.claude/projects/D--emptyos/memory/project_ollama_vault_test.md` | Cross-session memory of test history |

## Reference Run

**2026-04-12 4-way comparison:**

| Config | Rubric | Runtime | Verdict |
|---|:-:|---|---|
| Ollama qwen3.5:latest (9.7B local) | 24/25 | 3m 45s | Good at scaffolding, hallucinates APIs |
| Claude Code (Sonnet 4.6) | 25/25 | ~30s parallel | Best at architectural diagnosis |
| OpenAI gpt-4o-mini | 17/25 | 1m 21s | Shipped broken code (quote-in-f-string), hallucinated line numbers |
| OpenAI Codex CLI (gpt-5.3-codex) | 19/25 | 9m 52s | High quality but missed `apps/personal/` convention on test 4 |

Full report: `results/conv-test-4way-comparison.md`

**Headline finding:** Model parameter count doesn't predict conversation-mode quality. The 9.7B local model outscored gpt-5.3-codex (a much larger model) because the agent framework in Codex explores cwd cautiously, while a tight tool loop with explicit absolute paths just works.

## Extending to Other Providers

The harness supports `--provider {ollama, openai}`. To add another provider (e.g., Anthropic, Gemini):

1. Add a `call_<provider>()` function in `test-conversation-mode.py` that returns the normalized dict:
   ```python
   {"content": str, "tool_calls": [{"id", "name", "args"}], "raw_message": dict}
   ```
2. Add to `call_provider()` dispatch
3. Add to `--provider` choices in argparse

For Codex-CLI-style agent tools (terminal coding agents with their own sandbox):
- Use `scripts/test-codex-cli.py` as template
- Agents are fully agentic — need sandbox-aware prefix in the prompt (see existing template), otherwise the agent writes to the real project dir
- Use `cwd = D:/emptyos` so the agent sees the actual project; sandbox is a subdirectory inside `results/`

## Key Additional Gotchas (from the 4-way run)

| Tool | Gotcha |
|---|---|
| Codex CLI (Windows) | `shutil.which("codex")` returns `.CMD` path — must resolve it before `subprocess.run`, can't just pass `"codex"` as argv[0] |
| Codex CLI prompts | If the prompt has a separate "setup + task" structure, Codex may stop at the setup and ask for the task. Write ONE cohesive instruction instead |
| Codex CLI + ChatGPT subscription | gpt-4o-mini is not available via subscription auth — use `default` model (currently gpt-5.3-codex) |
| Ripgrep output | `subprocess.run(..., text=True)` on Windows uses cp1252 → UnicodeDecodeError on UTF-8 files. Use `capture_output=True` (bytes) and decode with `errors="replace"` |
| Ollama num_ctx | Default is 4096 — too small for CLAUDE.md. Set `num_ctx=32768` in `options`. Use native `/api/chat` endpoint (the OpenAI-compat endpoint ignores Ollama options) |

## Anti-Patterns

- Don't run the full suite without smoke-testing tool calling first — wasted 5+ min on stuck runs
- Don't compare against Claude Code in the SAME session that built the harness — context bleed makes Claude unfairly good. Use fresh subagents
- Don't score on rubric alone — quality differences are larger than checkbox differences
- Don't conclude "tool independent" or "not" from one model — try at least 2 models in different size tiers
- Don't blame the model for sync/async confusion — that's a CLAUDE.md ambiguity (the API is sync but examples sometimes show `await`); fix the docs, not the test
