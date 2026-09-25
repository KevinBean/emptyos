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
| `CreateArtifact` | Save a standalone HTML page the model wrote as an artifact, through viz (`viz.save_artifact` → the same record note + version ring a generated artifact gets). Auto-approved: it hands HTML to viz and chooses no path of its own, so the only reachable write is an artifact under viz's outputs, and a revision keeps the render it replaced. Dark-flagged `[apps.agent] feature.artifacts.enabled`; with the flag off it is removed from the registry, so no surface is offered it. |

The read-only v1 set (Read/Grep/Glob/Bash) plus the v1.5 mutating set (Write/Edit/CallApp/VaultQuery/SubAgent) have all shipped; the `V1_TOOLS` registry above is the live set (`CreateArtifact` is in it only while its flag is on).

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

A new session takes its `provider` from settings `think.app.agent` — the single knob that the `/agent/` model pill, the settings-panel "Default Provider" field and `eos chat`'s `/model … save` all write — then the legacy `agent.default_provider`, then `openai`. A stored value counts only if it names a provider the agent loop can drive (a pill can list others, e.g. `codex`). An existing session keeps its own `provider`; at turn start an unavailable one falls back to the first agent-capable provider.

**Default on this machine:** `ollama` with `qwen3.5:latest` (9.7B). For Claude-quality agent runs without paying API, pick `claude-cli`:

```bash
eos settings set think.app.agent claude-cli
```

`GET /agent/api/providers` lists the agent-capable providers for a picker — `{name, kind, model, available, chat_ok, is_cloud}` plus `default` and `chat_default`. `kind` is the wire family (`anthropic` / `openai` / `json`) or `native`. `GET /agent/api/sessions/{sid}` adds `history_kind`, read from each stored message's `provider_kind`: OpenAI tool-call history and Anthropic block history fail on the next turn if replayed to the other family, so `PATCH … {provider}` refuses a switch out of it. Native and handoff messages are plain strings any provider replays, and a native provider flattens whatever history it gets, so neither locks a session.

### Session profiles

A session's `profile` column says what kind of conversation it is (`profiles.py`). Blank — every session `/agent/` and `eos chat` create — is **coding**: the default prompt, the whole tool registry, the orient pass, the app-scaffold and skill auto-trigger blocks, episodic recall, the plan-mode banner, CLAUDE.md and the repo map. An unknown stored value is refused, never read as coding. **chat** (the portal's chat-first home, `POST /agent/api/sessions {"profile": "chat"}`) swaps in `CHAT_SYSTEM_PROMPT` and skips all of those passes; it keeps the app catalog, the skills catalog and a `/mode` persona if one is set. Its tools are VaultQuery, Locate, WebSearch, Skill, ContextRef, the flagged CreateArtifact (below) and three **narrowed** ones (`emptyos/sdk/agent_tools/restricted.py`) — hiding a schema does not bound what an offered tool can reach: **Fetch** is public-web GET only with every redirect re-checked; **CallApp** runs only declared `[[provides.verbs]]` on the agent / assistant / mcp surfaces and never an eligibility-`never` verb; **Read** is confined to the vault and the configured file roots, never the repo (whose `emptyos.toml` holds the credentials). A `connectors` column is reserved for per-chat MCP tools (B5); nothing sets it yet; `project_id` is covered below. A chat is refused on a natively-agentic provider (it runs its own tools past this registry and the consent gate); one created without a provider takes `chat_default` — the usual default if the loop can drive it, else the first local tool-capable provider. A chat turn on a cloud provider passes the cloud-consent gate first (`AgentApp._cloud_turn_allowed`); `eos chat` refuses to resume a chat session.

### Chat projects and search

A **project** (`projects.py`, stored in `ChatSessionStore`'s opt-in projects table) is a name plus standing instructions. A session's `project_id` puts it in one; every turn of that session gets the project's instructions in its system prompt (built once per turn, so the prompt prefix stays stable). Routes: `GET/POST /agent/api/projects`, `PATCH/DELETE /agent/api/projects/{pid}` (deleting a project keeps its chats, detached). `POST /agent/api/sessions` and `PATCH …/sessions/{sid}` refuse a `project_id` that names no project. Two plain verbs for other apps: `create_chat_project(name, instructions)` and `assign_chat_project(session_id, project_id)` — portal's `POST /portal/api/folders/migrate` uses them to copy its folders into projects (re-runnable, backs up `folders.json` first, moves only `agent:` threads).

`GET /agent/api/search?q=…[&project=…][&profile=chat]` searches what was said — the user's typed text and the assistant's replies, never tool output — plus session names, one row per session (the filter and grouping run in SQL, so a busy session cannot crowd a match out), ordered by latest activity. Injected orient/memory context and loop nudges are left out for messages stored since 2026-09-12, when turns began recording `display_text` / `origin`; older sessions are indexed as they were stored, injected blocks included. The index is SQLite FTS5 with the trigram tokenizer, so CJK substrings match (a `LIKE` over `content_json` cannot: `json.dumps` escapes them to `\uXXXX`). Queries under three characters, and a SQLite without FTS5, fall back to a substring scan. The first boot with search on indexes every stored message, inline in `setup()` (41 ms for 1,580 on the author's machine, about 26 µs a message); bumping `SEARCH_INDEX_VERSION` rebuilds it. The projects table, the index and `delete_session` removing a session's messages are part of the agent's store whatever the portal flag says — only the portal UI for them is dark.

### Attachments and vault context

A WS `message` may carry `attachments` (vault-relative paths, capped at `MAX_ATTACHMENTS`), `vault_context: true` ("ground this in my notes"), and `cloud_ok: true`. `turn_inputs.py` turns them into what the model reads: images become provider-native parts (`emptyos/sdk/attachments.py` — OpenAI `image_url`, Anthropic `image` blocks; the OpenAI-compat normalizer used to flatten a part list to its text, so an attached screenshot never arrived), documents (pdf / docx / txt / md) become fenced extracted text after the user's words, and the vault block goes first. Every path is confined to the vault, and anything unusable is reported as `agent:attachment_problems` rather than dropped.

Two refusals come first. An image for a model that cannot read it is an error naming the model, never a silent reroute (`provider_reads_images`). And vault-derived content — attachments or the vault block — is never sent to a **cloud** provider without `cloud_ok` for that message: the turn is refused with `agent:needs_confirmation` (CLAUDE.md rule 19). That holds on later turns too, which is where it is easy to lose: **every** message the turn appends is stored marked (the reply quotes the file; the tool results carry it), and a replay to a cloud model that was not approved for it shows the user's own typed words plus a withheld note instead. An approval is recorded against the provider it was given for, so the same model replays that content without asking again and a different one does not.

History stores an image as `{"type": "eos_image", "path": …}`, not base64; `hydrate_messages` turns it back into the current provider's part on each turn and strips every `eos_*` key before the provider sees the message. A file that has since moved, a model that cannot read images, and anything past the per-turn image budget (`MAX_REPLAY_IMAGE_BYTES`) each replay as a short note rather than a failed request. A send that is only attachments is recorded as `📎 <names>`, so the transcript and the chat search show that rather than the extracted text.

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

**Inbound MCP (shipped, behind a flag)** — the EOS agent loop can *consume* an external MCP server's tools (the foundry above is outbound; this is the other direction). `emptyos/sdk/mcp_client.py` connects over **either** transport, and `emptyos/sdk/agent_tools/mcp_proxy.py` wraps each remote tool as an `MCPProxyTool` (namespaced `mcp__<server>__<tool>`, `permission="ask"` — gated by the same `ToolConsentManager`; `readonly=False` so plan mode blocks it). Each row carries **either** a `command` (stdio — we spawn it) or a `url` (HTTP — it is already running); a row with both prefers stdio, since a spawned server is the one we control. Enable + configure in `emptyos.toml`, then restart:

```toml
[apps.agent]
feature.mcp-inbound.enabled = true

[[apps.agent.mcp_servers]]
id = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/some/dir"]
# env = { KEY = "value" }   # optional

[[apps.agent.mcp_servers]]
id = "velorn"                          # a desktop app that exposes MCP over HTTP
url = "http://127.0.0.1:19790/mcp"
# headers = { Authorization = "Bearer …" }   # optional
```

Dark default: with the flag off (or no `mcp_servers` rows) the registry is byte-identical. A server that fails to start is dropped (boot never breaks). Servers stop on app teardown.

**Connectors — the same thing, managed at runtime (B5).** `agent/connectors.py`
adds what a chat home needs on top of those config rows: a **store** the user
writes from the UI (`data/apps/agent/mcp_servers.json`, listed alongside the
toml rows, which stay read-only because the file is the user's), **connect and
disconnect without a restart**, and a **per-chat** enabled set (the session's
`connectors` column, read by `profiles.select_session_tools` — a connected
server's tools exist, which is not the same as every conversation being offered
them).

Three properties worth keeping:

- **Adding a stdio server is propose → confirm.** `POST /agent/api/connectors`
  answers `needs_confirmation` with the exact command line and spawns nothing;
  `confirm: true` is what writes it. A `url` row is added directly — a URL we
  call is not a process we spawn.
- **Secrets are referenced, never stored.** A header or env value written
  `${NAME}` is expanded from the daemon's environment at connect time, so the
  store keeps the *name*. A missing name refuses the connection and says which,
  rather than connecting without the credential and reporting "unauthorized".
  A literal value a user pasted is masked before the page ever sees it.
- **Refused in public mode**, on every path including boot: connecting an MCP
  server is local execution or an outbound call made on the daemon's behalf.

MCP output is fenced as untrusted data (`.claude/rules/untrusted-content.md`)
behind `feature.untrusted-wrap.enabled` — a server's text lands verbatim in the
model's context and nobody here audited that server.

Routes: `GET /agent/api/connectors`, `POST /agent/api/connectors`,
`POST /agent/api/connectors/{id}/connect` (`{"connect": false}` disconnects),
`POST /agent/api/connectors/{id}/remove`,
`POST /agent/api/sessions/{sid}/connectors`.

An HTTP server is one we reach but never spawn, so it is simply **absent** when it is not running — the normal state for a desktop app, and deliberately silent rather than an error on every boot. Reaching the host is bounded separately from the call (`sock_connect`), because a URL, unlike a local `command`, can name a machine that swallows the connection rather than refusing it — and this runs inside the agent app's `setup()`.

**Not yet built** — `agent:tool_pre`/`agent:tool_post` hooks with veto, JSON-fallback provider for small local models, A2A interoperability.
