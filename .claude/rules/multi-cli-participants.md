# Multi-CLI Participants Rule — agent-runtime adapters

EmptyOS rooms (`apps/rooms/`) participants come in three flavours: `user`,
`agent`, and `cli`. CLI participants run an external coding-agent CLI per
@-mention via the `agent-runtime` plugin. This rule documents the adapter
contract so adding a new CLI (codex, gemini, cursor-agent, kimi, …) is
config-only when possible and a small plugin patch when not.

**Reference implementation:** `plugins/agent-runtime/plugin.py`,
`apps/rooms/app.py:_dispatch_cli_turn`. See also `docs/ROOMS-V3.md`.

## MCP is a CLI-level concern, not an EmptyOS-level one

External CLIs (claude-cli, codex, gemini) read their own MCP server config
from dotfiles. EmptyOS does not proxy, manage, or know about MCP servers
— configuring them is a config-file edit, never a plugin patch.

Two scopes:

- **Repo-local `D:/emptyos/.mcp.json`** — loaded by claude-cli when its
  `cwd` is inside the EmptyOS repo. Affects Kevin's interactive Claude
  Code sessions (this CLAUDE.md conversation), `/eos-*` skills, and any
  agent-runtime invocation whose `cwd` is `D:/emptyos`. Today this file
  ships with **no servers** — `mcpServers: {}`.

  Two servers were previously loaded here and removed to reclaim
  per-turn token budget (MCP tool defs load into every conversation
  regardless of need):
  - Context7 (removed 2026-05-13) — library-docs lookup. `WebFetch`
    to docs sites covers the rare interactive need; build
    `plugins/context7/` around context7.com's HTTP API only when an
    EmptyOS app actually wants structured docs lookup.
  - Serena (removed 2026-05-14) — symbol-level LSP navigation. The
    codebase is dynamic-dispatch heavy (string-keyed `call_app`,
    `emit`, manifest contributions), so the interesting cross-references
    are Grep-shaped, not LSP-shaped. Re-add if a session is heavy on
    within-app Python refactoring touching many call sites of a real
    Python symbol.

- **User-global `~/.claude/.mcp.json`** — affects every claude-cli
  invocation on the machine, regardless of `cwd`. Use this only for
  servers that should follow Kevin everywhere.

### Room `@claude` participants and MCP

Room CLI participants spawn via `agent-runtime.claude_cli_run` with `cwd`
defaulting to the vault root, so they **do not** inherit the repo-local
`D:/emptyos/.mcp.json`. Two opt-in paths if a future room needs MCP:

1. Move the relevant servers (Context7 in particular — it's
   `cwd`-agnostic) to `~/.claude/.mcp.json`.
2. Add `extra_args: ["--mcp-config", "D:/emptyos/.mcp.json"]` to the
   participant record in the room — only useful once the repo-local
   file actually has servers in it; today it's empty.

Neither is enabled by default — room participants stay focused on
chat-shape tasks, not codebase navigation.

## Two adapter shapes

| Shape | Used by | What it streams |
|---|---|---|
| **`claude_cli_run`** (streaming + tool events) | `claude-cli` only | stream-json events parsed into text chunks + `tool_use` / `tool_result` cards |
| **`text_cli_run`** (buffered text) | `codex`, `gemini`, every other CLI | full stdout captured into one text chunk, no tool parsing |

The split is deliberate. Claude Code CLI emits structured `stream-json`
that powers the review-gate UI — full tool parity. Other CLIs print plain
text to stdout; we capture the whole reply and surface it without trying
to fake structured events. Trying to reverse-engineer each CLI's stream
format is fragile across versions.

## Adding a new CLI

### Path 1: pure config (preferred)

If the CLI prints its reply as plain text, add a `[plugins.agent-runtime.clis.<id>]`
block to `emptyos.toml`:

```toml
[plugins.agent-runtime.clis.kimi]
binary = "kimi"                       # path or PATH name
args_template = ["-p", "{prompt}"]    # str.format substitution
supports_system = false               # true if the CLI has --system / --append-system-prompt
env_drop = []                         # env vars to strip
```

`text_cli_run` reads this and runs the CLI. No code change; rooms picks it
up the next time `_dispatch_cli_turn` is called with `cli_id="kimi"`.

To make it appear in the group-create modal CLI section, add a
`simpleCliRow(...)` line in `apps/rooms/pages/index.html` (search for
`simpleCliRow`) and a name resolver in `agentNameById`.

### Path 2: built-in defaults

If you want the CLI to work for users without `emptyos.toml` config, add a
default to `DEFAULT_CLI_ADAPTERS` in `plugins/agent-runtime/plugin.py`:

```python
DEFAULT_CLI_ADAPTERS: dict[str, dict] = {
    "codex": {
        "binary": "codex",
        "args_template": ["exec", "{prompt}"],
        "supports_system": False,
    },
    # add yours here
}
```

User config still overrides on every key — defaults are a starting point,
not a contract.

### Path 3: streaming + tool events

Required only when:
- The CLI emits stream-json or another structured format you want parsed
- Users need per-tool review-gate cards live in the chat
- A non-streaming text adapter loses too much

Don't take this path lightly. Today only `claude-cli` justifies it —
`claude_cli_run` is ~80 lines of stream-json event parsing wired into the
rooms `_dispatch_cli_turn` claude-specific branch. Extending the same
shape to a second CLI means duplicating that branch. Wait for a real
second consumer before extracting a streaming-adapter abstraction.

## Per-participant config knobs

Every CLI participant on a room (`{type: "cli", id: "...", ...}`) accepts:

| Field | Meaning | Adapters that use it |
|---|---|---|
| `model` | Passes as `--model <id>` | `claude-cli` only |
| `effort` | Passes as `--effort <level>` | `claude-cli` only |
| `allowed_tools` | Passes as `--allowedTools <csv>` | `claude-cli` only (read-only set by default — review gate pattern) |
| `cwd` | Working directory; defaults to vault root | all |
| `timeout_s` | Wall-clock kill switch | all |
| `extra_args` | List appended to the command line | all |

Persistent across save/load via the room record's `participants` list.
The group-create modal carries `model` / `effort` for `claude-cli`; for
other CLIs the user edits the room record directly today (add a UI hook
in the group modal if you wire it).

## Dispatch flow

```
api_chat_stream
└── _resolve_responder(text, parts)
    └── if responder.type == "cli":
        ├── if cli_id == "claude-cli":
        │   └── _dispatch_cli_turn → runtime.claude_cli_run
        │       (stream-json events → text + tool_use + tool_result chunks
        │        → after stream: _gate_server_actions parses [DO:] tags)
        └── else:
            └── _dispatch_cli_turn → runtime.text_cli_run
                (one buffered text chunk, no tool parsing, no review gate)
```

## Why CLI participants stay read-only

Claude Code CLI's `--allowedTools` defaults to `Read,Grep,Glob,WebFetch`
for room participants. The CLI is instructed via system prompt to emit
`[DO:app.method({...})]` tokens for any state-changing action — those
tokens land in the review-gate (Phase 5: `_gate_server_actions`) as
pending action cards the user reviews and applies.

This is a deliberate "with you, not for you" design: no autonomous
filesystem writes from a CLI participant. Direct write tools could be
unlocked per-CLI-per-room if a future user opts in, but that needs a
real review-gate UX for tool_use events, not just for `[DO:]` tokens.

## When NOT to add a CLI

- The "agent" already does what you want — agent participants are
  cheaper (no subprocess), faster (no spawn), and have full review-gate
  semantics today via `[DO:]`. CLIs are for cases where the external
  binary brings something irreplaceable: claude-cli's agentic tool-use,
  codex's coding-tuned chain, etc.
- The CLI requires interactive auth or a TTY — these don't work under
  `asyncio.create_subprocess_exec` cleanly. Either wrap with a
  non-interactive auth path first or skip.
- The CLI streams binary output (audio, video) — text adapter can't
  represent it. Different shape of plugin entirely.

## Tests

`tests/test_sys_rooms_logic.py::TestResolveResponder` covers participant
resolution including CLI ids. End-to-end CLI dispatch is exercised
through `apps/dogfood-agent/` (the only other consumer of the
`agent-runtime` plugin) via its existing test suite.
