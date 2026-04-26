# Agent — Coding Companion for EmptyOS

The `agent` app is a Claude-Code-like coding companion that lives inside EmptyOS. It runs a tool-use loop — plan, call tools, observe results, iterate — with a permission gate that keeps the user in the loop on anything non-read-only. It is exposed in two places:

- **Web** — `http://localhost:9000/agent/` — a streaming chat with collapsible tool-call panels and an approval modal.
- **Terminal** — `eos chat` — a Rich REPL with inline approvals.

Both surfaces drive the same loop, the same tool registry, and the same permission manager. Pick the one that fits your task.

## Principle

**With you, not for you.** Reads auto-approve. Writes, shell commands, and cloud calls always ask. There is no global "yolo" flag — the closest thing is an "approve for this session" checkbox that lasts only until the session ends or the daemon restarts.

## v1 Tool Set

| Tool | Permission | Notes |
|---|---|---|
| `Read` | auto | Read a file. Returns `cat -n` formatted content. Truncates over 2000 lines. |
| `Grep` | auto | Search file contents via ripgrep. Modes: `files_with_matches`, `content`. Supports glob/type filters. |
| `Glob` | auto | Find files by glob pattern, sorted by mtime. |
| `Bash` | ask (allowlist → auto) | Run a shell command via `shlex.split` + `create_subprocess_exec` — no shell metacharacters. Read-only commands (git status/log/diff/show, ls, cat, head, tail, rg, find -type f, --version checks) auto-approve. Anything else asks. |

Write, Edit, CallApp, VaultQuery, Emit, and spawn_agent land in v1.5 after the loop + permission UX is proven.

## Providers

The agent supports **two provider shapes**:

### 1. Tool-capable (our loop drives it)

These providers accept tool schemas, return `tool_use` blocks, and expect us to inject `tool_result` back. `run_turn()` drives the tool-use loop with our Tool registry and our tool_consent gate.

| Provider | Wire format | When it's used |
|---|---|---|
| `anthropic_sdk` | native `tool_use` blocks | Configure with `ANTHROPIC_API_KEY`. Best tool-use fidelity, prompt caching. |
| `openai_compat` (extended) | OpenAI function calling | Covers OpenAI (`OPENAI_API_KEY`) **and** Ollama tool-capable models (`qwen3.5`, `llama3.2`, `qwen2.5-coder`, `mistral-nemo`, etc.) at `http://localhost:11434`. |

### 2. Natively agentic (runs its own loop)

These providers are themselves agentic tools — they run their own tool-use loop internally with their own built-in tools and permission model. We don't drive them; we delegate the whole turn and stream their narration back. `run_native_turn()` handles this path.

| Provider | Built-in tools | When it's used |
|---|---|---|
| `claude-cli` (`ClaudeCLIThinkProvider`) | Read, Grep, Glob, WebSearch, WebFetch | Free with Claude Max subscription. Tagged `NativelyAgenticProvider` — EmptyOS tool_consent does NOT apply (the CLI gates its own tool use). |

The web UI and `eos chat` both surface a banner when a native-agent session is running, so the user knows our custom tools + permission gate aren't in play.

### Provider selection

Per-session `provider` field → settings `agent.default_provider` → first agent-capable provider found (tool-capable preferred, then natively agentic). Resolved at turn start; switching mid-session isn't supported in v1.

**Default on this machine:** `ollama` with `qwen3.5:latest` (9.7B). For Claude-quality agent runs without paying API, pick `claude-cli`:

```bash
eos settings set agent.default_provider claude-cli
```

## Permission Manager

`kernel.tool_consent` is a `ToolConsentManager` parallel to `CloudConsentManager`. Per-tool policies come from the `Tool` class (`auto | ask | deny`). Per-session caching: approving a tool in scope=`session` skips the prompt for the remainder of that session.

Global kill switch: set `agent.tool_policy = deny` in settings. Overrides everything, including class-level auto tools.

## Wire events

Every turn narrates itself on the EventBus. WebSocket clients get the same stream on `/agent/ws/{session_id}`:

- `agent:turn_start` — user sent a message
- `agent:iter_start` — one model round-trip starting
- `agent:text` — assistant text delta
- `agent:tool_call` — model requested a tool
- `agent:tool_result` — tool completed (or errored, or was denied)
- `agent:permission_requested` — gate is waiting for a user decision
- `agent:permission_resolved` — gate received a decision
- `agent:done` — turn complete (stop_reason=end_turn)
- `agent:cancelled` / `agent:max_iters` / `agent:error` — abnormal exits

Other apps can subscribe to any of these for telemetry, hooks, or side effects — the event bus is shared.

## File layout

```
apps/agent/
├── manifest.toml
├── app.py             # AgentApp — WS + REST + eos chat CLI
├── loop.py            # run_turn() tool-use loop
├── tools/
│   ├── base.py        # Tool ABC, ToolResult
│   ├── read.py
│   ├── grep.py
│   ├── glob.py
│   ├── bash.py        # shlex + allowlist
│   └── __init__.py    # build_registry()
└── pages/
    ├── index.html     # web UI
    └── agent.js       # WS client + rendering

emptyos/capabilities/
├── providers/
│   ├── _tool_capable.py   # ToolCapableProvider ABC + AgentTurn
│   ├── anthropic_sdk.py   # native tool_use blocks
│   └── openai_compat.py   # extended with execute_tools()
└── tool_consent.py        # ToolConsentManager
```

## Testing

`tests/test_sys_agent.py` covers:
- Tool schema round-trip (Anthropic + OpenAI formats)
- Bash allowlist (read-only auto-approve, shell metacharacter rejection)
- Permission manager (auto/deny/ask + session caching + kill switch)
- Loop integration (text-only termination, tool dispatch + continue, denied-tool recovery, unknown-tool error path)
- OpenAI response → AgentTurn round-trip

All 26 tests are pure-python and run without a daemon:

```bash
pytest tests/test_sys_agent.py -v
```

## v1.5 / v2 roadmap

**v1.5** — Write + Edit (with diff preview and uniqueness rule), CallApp, VaultQuery, Emit, session compaction, JSON-fallback provider for small local models.

**v2** — spawn_agent sub-agents, `agent:tool_pre`/`agent:tool_post` hooks with veto, MCP server parity.
