# Multi-CLI Participants Rule — agent-runtime adapters

EmptyOS rooms (`apps/rooms/`) participants come in three flavours: `user`,
`agent`, and `cli`. CLI participants run an external coding-agent CLI per
@-mention via the `agent-runtime` plugin. This rule documents the adapter
contract so adding a new CLI (codex, gemini, cursor-agent, kimi, …) is
config-only when possible and a small plugin patch when not.

**Reference implementation:** `plugins/agent-runtime/plugin.py`,
`apps/rooms/participants.py:_dispatch_cli_turn` (bound onto `RoomsApp`
via the multi-module decomposition pattern in
`.claude/rules/multi-module-apps.md`). See also `docs/ROOMS-V3.md`.

## MCP is a CLI-level concern, not an EmptyOS-level one

External CLIs (claude-cli, codex, gemini) read their own MCP server config
from dotfiles. EmptyOS does not proxy, manage, or know about MCP servers
— configuring them is a config-file edit, never a plugin patch.

Two scopes:

- **Repo-local `D:/emptyos/.mcp.json`** — loaded by claude-cli when its
  `cwd` is inside the EmptyOS repo. Affects the user's interactive Claude
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
  servers that should follow the user everywhere.

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
| **`claude_cli_run`** (streaming + tool events) | `claude-cli` | stream-json events parsed into text chunks + `tool_use` / `tool_result` cards |
| **`text_cli_run` + `stream_json`** (streaming) | `codex` | `codex exec --json` events, same cards, same parser |
| **`text_cli_run`** (buffered text) | `gemini`, every other CLI | full stdout captured into one text chunk, no tool parsing |

The split is by **whether the CLI emits a structured event stream**, not by
which CLI it is. A CLI that prints plain text gets its whole reply captured
and surfaced as one chunk; inventing structure for it would mean
reverse-engineering a format that shifts between versions.

The dividing line is the adapter's `stream_json` flag, not a hardcoded
`cli_id` check. `_dispatch_cli_turn` branches on that flag, and everything
past the branch is dialect-agnostic because
`transform_stream_json_obj` (`emptyos/sdk/claude_run_stream.py`) normalizes
every dialect to one canonical `chunk` / `tool_use` / `tool_result`
vocabulary. Adding a streaming CLI is therefore a parser branch plus a flag —
not a second copy of the dispatch loop.

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

### Windows: a multi-line prompt cannot go on argv through a `.CMD` shim

The single nastiest failure mode in this file, because **nothing reports an
error**. `shutil.which` resolves npm-installed CLIs to a `.CMD` shim, which
runs through `cmd.exe` — and there a newline **terminates the command**. Room
prompts are multi-line by construction (`_build_cli_prompt` joins history and
the current turn with `\n`), so the CLI receives only the first line.

It exits 0. It replies fluently. It simply answers a question nobody asked.
Measured 2026-07-31: codex got a prompt of exactly `[System]` and replied
"What would you like me to work on in EmptyOS?" — which reads like a model
being unhelpful, not like a truncated argv.

Check the resolved binary before trusting argv:

| CLI | Resolves to | Multi-line argv |
|---|---|---|
| `claude` | `claude.EXE` | fine — a real exe, so `claude_cli_run` is unaffected |
| `codex` | `codex.CMD` | **truncated** — carries the prompt on stdin instead |
| `pi` | `pi.cmd` (configured) | **truncated** once a room has history |

The fix is `prompt_via_stdin = true` on the adapter plus whatever flag makes
the CLI read stdin (`-` for codex); `text_cli_run` then sends the prompt down
the pipe and closes it. Do **not** also leave `{prompt}` in `args_template` —
the model would receive it twice.

**The installer predicts the risk**, which saves measuring every adapter: npm
writes a `.CMD` shim (exposed), while pip's console-scripts and Go builds
produce real `.exe` files that take a multi-line argv fine. Measured on this
machine — codex and pi are npm/node shims and were both bitten; aider (pip
`.exe`) and goose (`goose.EXE`) are not. So treat any npm-distributed CLI as
suspect on Windows before it is ever run, and still confirm with
`shutil.which` rather than trusting the ecosystem alone.

`pi` had the same bug and is fixed the same way (2026-07-31). Measured against
qwen3.5-32k: prompt on argv → an instruction on line 2 was never seen
("Ignored. What would you like me to do next?"); the same prompt on stdin came
back answered.

It needed fixing in **two places**, which is worth knowing before you call one
of these adapters fixed: `DEFAULT_CLI_ADAPTERS` in the plugin *and* any
`[plugins.agent-runtime.clis.<id>]` override in `emptyos.toml`. User config
wins on every key, so patching only the machine's toml leaves the shipped
default broken for everyone else, and patching only the default leaves this
machine broken. The toml fix went in first and the code default sat broken for
an hour before an `@codex` room turn reading the actual file surfaced it.

Its fix carries one extra move worth copying. `--append-system-prompt` takes
the ~1100-char room system prompt, which is *also* multi-line and was *also*
being cut — so a native system-prompt flag is no escape from this bug. Setting
`supports_system = false` makes `text_cli_run` fold the system prompt into a
`[System] … [User] …` preamble, putting both halves on stdin. A folded system
prompt is weaker than a native one, and a whole one still beats a truncated
one.

**So the check when adding a CLI is per-argument, not per-adapter:** every
argv slot that can carry multi-line content is exposed, including the ones
that look like the "proper" channel.

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

**Resolved 2026-07-31 — and the earlier warning here was pessimistic.** This
section used to say extending the streaming shape to a second CLI meant
duplicating the ~80-line claude branch, and to wait for a real consumer
before abstracting. The consumer arrived (codex), and the duplication turned
out to be unnecessary: the abstraction already existed. The claude branch was
never really claude-specific below its spawn call — it parses via
`transform_stream_json_obj`, which was already multi-dialect. Codex needed a
~35-line branch in that parser, a `stream_json` flag, and `--json` in its
args. No second dispatch loop, no new plugin method (`text_cli_run` already
forwarded stdout lines).

So the bar for a third streaming CLI is low: add a dialect branch to the
shared parser, declare `stream_json`, pin whatever flag makes the CLI emit
events. Do **not** add a bespoke `<cli>_cli_run` — if you find yourself
writing one, check first whether `text_cli_run` plus a flag covers it.

The one genuinely fragile part is the dialect itself: an event schema can
shift between CLI versions. Pin new dialect branches with tests built from
**real captured output** (`tests/test_unit_claude_run_stream_dialects.py`
uses verbatim events from an actual run), so a schema change fails a test
instead of silently emptying the transcript.

## Sub-agent depth limit (OpenClaw borrow, 2026-06-11)

Every agent-runtime spawn (`run`, `spawn_detached`, and the wrappers on top)
stamps `EOS_AGENT_DEPTH=<parent depth + 1>` into the child's env and refuses
to spawn at `depth >= max_agent_depth` (`[plugins.agent-runtime]
max_agent_depth`, default 3; `0` disables). The user's daemon runs at depth 0;
a CLI it spawns is 1; anything that CLI spawns through a local-kernel
agent-runtime is 2. Refusals use the existing no-spawn `{"error": ...}` shape
(same as "claude CLI not on PATH"), so every consumer's error path already
handles them.

Scope honesty: the guard caps **subprocess recursion via env inheritance**. A
child calling back into the daemon over HTTP starts a fresh chain (the daemon
is depth 0) — HTTP loop-back capping would need an `X-EOS-Agent-Depth` header
threaded through the rooms/staff dispatchers; build it only if a real loop
shows up. Tests: `tests/test_unit_agent_depth.py`.

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

## Don't name a room after one of its participants

A room's own agent is a responder candidate alongside its CLI participants,
and `_resolve_responder` matches an agent **by display name as well as by id**
— `name == mention`, or the name with spaces turned into dashes. Participants
are scanned in list order and the room agent comes first, so a room *named*
"Codex" makes `@codex` resolve to the room agent and never reach the CLI
participant whose id is literally `codex`.

That is worse than an id collision, because the name is the human-facing label
nobody thinks of as an identifier. Cost two failed room setups on 2026-07-31,
and the failure is quiet: the room agent answers, fluently, with no tools —
which reads as the CLI being unhelpful rather than as never having run. The
tell is on the final chunk: `actor_type` is `None` and `responder_id` is the
room's id, where a real CLI turn reports `actor_type: "cli"` and the
participant's id.

The other half of the same function: when no mention matches anything, it
falls through to `responders[0]` — again usually the room's own agent. So a
turn with a typo'd or absent mention is silently answered by the room rather
than refused.

Give a room a name and id unrelated to any participant id (`clilab` /
"CLI Lab" for a room hosting `@codex`), and check `actor_type` on the final
chunk when a CLI turn produces no tool cards.

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

**The text-CLI path does not inherit that posture — each adapter must pin
its own.** `--allowedTools` is threaded only on the claude-cli branch;
`text_cli_run` passes whatever `args_template` says and nothing more. So a
new CLI is write-capable by default, dispatched with
`cwd = notes_path` — the live vault (`_dispatch_cli_turn`). Both shipped
adapters now pin explicitly: `pi` via `--tools read,grep,find,ls`, `codex`
via `-s read-only`. **Adding a CLI means adding its read-only flag in the
same commit**, or the room review gate is decorative for that participant.

**A sandbox flag is not a containment boundary on Windows.** Measured
2026-07-31 against codex-cli 0.144.1: `codex exec -s read-only` logs
`sandbox: read-only`, then executes a PowerShell `Set-Content` and creates
the file — exit 0, no approval prompt.

The mechanism is worth stating correctly, because the obvious guess is wrong.
Codex *does* ship a Windows sandbox: a helper that applies deny-read ACLs.
It is simply not dependable, and **it fails open**. In one measured run the
helper errored — `windows sandbox: helper_unknown_error: apply deny-read
ACLs`, `exit_code: -1`, `status: failed` — and codex then re-ran the
identical command with no sandbox and succeeded. In the write run no sandbox
error surfaced at all; the write just landed. So the failure mode is not "the
flag is ignored", it is "the flag is attempted, and when the attempt fails
the command runs anyway". A clean-looking log is therefore not evidence of
containment.

Keep pinning the flag (CLAUDE.md rule 20 — the same code runs where it
bites), but on Windows the real controls are the participant's `cwd` and the
prompt contract. Do not read
"sandbox: read-only" in a log as proof that a write was prevented; the
2026-07-31 check confirmed it is not. Verify a containment claim by
attempting the write, on the platform you actually ship to.

That last control is now enforced rather than merely advised. An adapter
declares `writes_unsandboxed` when an escape has actually been *measured*
(codex sets it on win32 only), the plugin exposes it via `cli_adapter_info()`,
and `_dispatch_cli_turn` refuses to run such a CLI on the **implicit** vault
cwd — it yields an error telling the user to set an explicit `cwd`. An
explicit cwd is a deliberate choice and passes through. The key is a
known-bad list, not a safety certificate: absent means "never tested", not
"proven contained", so never read a missing flag as an assurance.

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

`tests/test_unit_rooms_logic.py::TestResolveResponder` covers participant
resolution including CLI ids. End-to-end CLI dispatch is exercised
through `apps/dogfood-agent/` (the only other consumer of the
`agent-runtime` plugin) via its existing test suite.
