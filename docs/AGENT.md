# Agent — Coding Companion for EmptyOS

The `agent` app is a Claude-Code-like coding companion that lives inside EmptyOS. It runs a tool-use loop — plan, call tools, observe results, iterate — with a permission gate that keeps the user in the loop on anything non-read-only. It is exposed in two places:

- **Web** — `http://localhost:9000/agent/` — a streaming chat with collapsible tool-call panels and an approval modal.
- **Terminal** — `eos chat` — a Rich REPL with inline approvals.

Both surfaces drive the same loop, the same tool registry, and the same permission manager. Pick the one that fits your task.

## Principle

**With you, not for you.** Reads auto-approve. Writes, shell commands, and cloud calls always ask. There is no global "yolo" flag — the closest thing is an "approve for this session" checkbox that lasts only until the session ends or the daemon restarts.

## Tool Set

The tool registry lives in `emptyos/sdk/agent_tools/` — one file per tool, a shared `Tool` ABC + `ToolResult` in `base.py`, instantiated by `build_registry()` in `__init__.py` (the `V1_TOOLS` list). Each tool carries a `permission` (`auto | ask | deny`, default `ask`) read by `kernel.tool_consent`, plus a `readonly` flag that gates plan mode. The principle is unchanged: **read-only tools auto-approve; anything that mutates files, executes code, calls another app, or touches the network asks.** The tables below group by typical risk; each tool's `permission` + `readonly` attributes are the source of truth.

### Read-only (auto-approve)

| Tool | Notes |
|---|---|
| `Read` | Read a file. Returns `cat -n` formatted content. Truncates over 2000 lines. |
| `Grep` | Search file contents via ripgrep. Modes: `files_with_matches`, `content`. Supports glob/type filters. |
| `Glob` | Find files by glob pattern, sorted by mtime. |
| `VaultQuery` | Query the VaultIndex by tags + frontmatter properties (read-only). |
| `TaskList` | List tasks across the vault (read-only aggregation). |

### Mutating / executing / network (ask)

| Tool | Notes |
|---|---|
| `Write` | Create or overwrite a file. |
| `Edit` | Exact-string replacement with the uniqueness rule + diff preview. |
| `DeleteFunction` | Remove a named function/symbol from a file. |
| `Bash` | Run a shell command via `shlex.split` + `create_subprocess_exec` — no shell metacharacters. Read-only commands (git status/log/diff/show, ls, cat, head, tail, rg, find -type f, --version checks) auto-approve; anything else asks. |
| `Python` | Execute a Python snippet. |
| `CallApp` | Invoke another app's `@web_route` verb via `self.call_app` (kwargs-only). |
| `SubAgent` | Spawn a sub-agent run; recorded on the harness-runs board. |
| `Fetch` | HTTP fetch of a URL. |
| `WebSearch` | Web search. |
| `Screenshot` | Capture a UI screenshot via the playwright plugin (flagged `readonly` for plan mode). |
| `Skill` | Run a registered skill (flagged `readonly` for plan mode). |
| `RestartDaemon` | Privileged — restart a sandbox daemon (not `:9000`/`:9001`). |

The read-only v1 set (Read/Grep/Glob/Bash) plus the v1.5 mutating set (Write/Edit/CallApp/VaultQuery/SubAgent) have all shipped; the 17-tool `V1_TOOLS` registry above is the live set.

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
- `agent:compacted` — stale tool_result bodies summarized in place (history exceeded the char budget)
- `agent:cancelled` / `agent:max_iters` / `agent:error` — abnormal exits

Other apps can subscribe to any of these for telemetry, hooks, or side effects — the event bus is shared.

**Billing visibility.** `run_turn` calls the provider's `execute_tools()` directly (it does *not* go through `BaseApp.think()`), so the loop emits `think:executed` itself — one per model round-trip via `agent_loop._meter_think` on the kernel bus — so autonomous loops (`eos chat`, fix-agent, dogfood-agent) show up in billing + the trace-costs feature like any other think call. Each turn is wrapped in `@trace.trace_boundary`, so every `agent:*` event and every nested `self.think()` carries one `trace_id`. (The native `claude-cli` path runs on the Max subscription and reports no metered usage, so it is not billed.)

## File layout

```
apps/public/standard/agent/
├── manifest.toml
├── app.py             # AgentApp — spine; binds helper modules
├── routes.py          # WS + REST handlers
├── repl.py            # eos chat CLI
├── sessions.py        # session lifecycle
├── context.py         # context assembly
├── orient.py          # orientation / planning
├── prompts.py         # system prompts
├── skills.py          # skill surfacing
└── pages/
    ├── index.html     # web UI
    └── agent.js       # WS client + rendering

emptyos/sdk/agent_tools/   # the tool registry (shared SDK, not app-local)
├── base.py            # Tool ABC, ToolResult
├── __init__.py        # registry assembly
├── read.py  grep.py  glob.py  vault_query.py  task_list.py   # read-only
├── write.py  edit.py  delete_function.py  bash.py  python.py  # mutating/exec
└── call_app.py  subagent.py  fetch.py  web_search.py  screenshot.py  skill.py  restart_daemon.py

emptyos/capabilities/
├── providers/
│   ├── _tool_capable.py   # ToolCapableProvider ABC + AgentTurn
│   ├── anthropic_sdk.py   # native tool_use blocks
│   └── openai_compat.py   # extended with execute_tools()
└── tool_consent.py        # ToolConsentManager
```

> The agent app was decomposed per `.claude/rules/multi-module-apps.md` and the
> tool registry was lifted into `emptyos/sdk/agent_tools/` so other surfaces
> (rooms CLI participants, dogfood-agent, staff) can reuse the same tools.

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

## Roadmap

**Shipped** — Write + Edit (diff preview + uniqueness rule), CallApp, VaultQuery, SubAgent (sub-agents, recorded on the harness-runs board), Python, Fetch, WebSearch, Screenshot, Skill, DeleteFunction, RestartDaemon. The tool registry moved to `emptyos/sdk/agent_tools/` and the app was decomposed into helper modules. **Session compaction** (`agent_loop._compact_history` — summarizes stale tool_result bodies past a char budget, emits `agent:compacted`) and **per-tool-result context packing** (`_maybe_pack_tool_output` → recoverable via the `ContextRef` tool) are both live. **Billing metering** of the autonomous loop (`_meter_think` → `think:executed`) + per-turn `trace_id` (`@trace.trace_boundary`) ship as of 2026-06-28.

**Outbound MCP foundry (shipping, behind a flag)** — `emptyos/mcp_outbound_server.py` lets an external MCP client (Codex, Cursor, Claude Desktop) connect to EmptyOS over stdio, **read** the work graph + allowlisted KB notes as MCP resources, **pull** KB `kind: pattern` notes as MCP prompts, and **call** a grant-gated subset of app verbs. The 5-step gate on writes (flag → allowlist → eligibility hard-floor → autopilot grant match → dispatch) fails closed; resources are read-only graph metadata gated on flag-on only. The advertised verb set is per-app manifest opt-in (`[provides.mcp_foundry]`), aggregated by `GET /agent/api/mcp/foundry/verbs` and floored by autopilot eligibility — a manifest can never expose a non-eligible or another app's verb. Grants for `actor_type="mcp-client"` are issued via `eos autopilot grant` or the `/agent/pages/foundry.html` panel. Dark default: with the flag off (or no grant) nothing is callable. Tests: `tests/test_unit_mcp_foundry.py` (offline gate + resource allowlist) + `tests/test_sys_mcp_foundry.py` (endpoints). See `.claude/rules/autopilot-grants.md` for the grant model and **Connect an external MCP client** below.

### Connect an external MCP client (Codex / Cursor / Claude Desktop)

1. **Enable the foundry** (deliberate edit — keeps the dark default honest) in `emptyos.toml`, then restart:
   ```toml
   [apps.agent]
   feature.mcp-foundry.enabled = true
   # Optional: which vault tags an agent may read via eos://vault/query (frontmatter only).
   # Empty/omitted = the vault resource returns nothing sensitive only if you also leave it unset;
   # set an explicit allowlist to expose just these tags:
   feature.mcp-foundry.vault_tags_allow = ["kb", "project", "runbook"]
   # Optional: which KB note KINDS the client may read with full bodies, via
   # eos://kb/notes?kind={kind}&domain={domain} (metadata list) and
   # eos://kb/note/{slug} (full note). Fail-closed: empty/omitted = no KB
   # exposure. Including "pattern" also enables the MCP prompts primitive —
   # prompts/list + prompts/get serve KB `kind: pattern` notes (full body) as
   # reusable prompt scaffolding:
   feature.mcp-foundry.kb_kinds_allow = ["pattern", "concept", "lesson"]
   ```
2. **Opt verbs in** per app via `[provides.mcp_foundry] verbs = [...]` (must be autopilot-eligible). Shipped: `task.add`, `capture.add`. Check the live set: `curl localhost:9000/agent/api/mcp/foundry/verbs`.
3. **Issue a grant** for the client (eligibility floor still applies):
   ```bash
   eos autopilot grant codex task.add --scope mcp:codex
   ```
   or use the panel at `/agent/pages/foundry.html` (also shows flag state + active grants).
4. **Point the client at the server** via its `mcpServers` dotfile:
   ```json
   {
     "mcpServers": {
       "emptyos-foundry": {
         "command": "python",
         "args": ["-m", "emptyos.mcp_outbound_server"],
         "env": {"EMPTYOS_PORT": "9000", "EMPTYOS_MCP_CLIENT_ID": "codex",
                 "EMPTYOS_AUTH_TOKEN": "<token if network.mode is private/public>"}
       }
     }
   }
   ```
5. **What the client sees** — `resources/list` (the work graph, plus the two `eos://kb/...` templates when `kb_kinds_allow` is non-empty); `prompts/list` / `prompts/get` (KB pattern notes, when `"pattern"` is allowlisted); `tools/list` = the verbs that are advertised ∩ eligible ∩ granted-for-this-client. Resources + prompts are read-only and need no grant; every `tools/call` is recorded in the tamper-evident audit chain — inspect with `eos autopilot log` / verify with `eos autopilot verify`.

**Inbound MCP (shipped, behind a flag)** — the EOS agent loop can *consume* an external MCP server's tools (the foundry above is outbound; this is the other direction). `emptyos/sdk/mcp_client.py` spawns a stdio MCP server, and `emptyos/sdk/agent_tools/mcp_proxy.py` wraps each remote tool as an `MCPProxyTool` (namespaced `mcp__<server>__<tool>`, `permission="ask"` — gated by the same `ToolConsentManager`; `readonly=False` so plan mode blocks it). Enable + configure in `emptyos.toml`, then restart:

```toml
[apps.agent]
feature.mcp-inbound.enabled = true

[[apps.agent.mcp_servers]]
id = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/some/dir"]
# env = { KEY = "value" }   # optional
```

Dark default: with the flag off (or no `mcp_servers` rows) the registry is byte-identical. A server that fails to start is dropped (boot never breaks). Servers stop on app teardown.

**Not yet built** — `agent:tool_pre`/`agent:tool_post` hooks with veto, JSON-fallback provider for small local models, A2A interoperability.
