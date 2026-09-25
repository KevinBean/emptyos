# Conversation Stack

EmptyOS has **5 backends** that talk to LLMs and **8 frontends** that surface them, plus **1 observer surface** that watches agent sessions without talking (cockpit — see the last section). Knowing which to reach for matters — they're not interchangeable. This doc is the canonical layout.

Last verified: 2026-07-22 (added the cockpit observer category; `eos code` unified with the agent CLI; rooms code preset retained as `eos rooms --code`).

> **Path note (2026 reorg):** the five backends live under `apps/public/standard/{agent,rooms,assistant,voice-assistant}/` and `apps/personal/staff/`; `/code/` is `apps/extension/plekto/code/`. This doc uses the short ids (`apps/public/standard/agent`, `apps/public/standard/rooms`, …) below for readability — app **ids** are location-independent at runtime, but grep the id, not the shorthand path.

---

## The 5 backends — what each is

### `apps/public/standard/agent` — autonomous tool-loop

- **Shape**: multi-iteration loop driven by `emptyos/sdk/agent_loop.py`. Model emits native tool calls; runtime dispatches; tool results feed back; loop continues until model stops calling tools or max-iters trips (default 25).
- **Tool surface**: 18 bundled tools in `emptyos/sdk/agent_tools/` — Read, Edit, Write, Bash, Grep, Glob, Python, Fetch, Screenshot, CallApp, Skill, TaskList, SubAgent, RestartDaemon, etc.
- **Gate**: per-tool `ToolConsentManager` — each tool has an "ask / auto / deny" default; user policy + per-call summary surface as approval prompts.
- **State**: SQLite sessions with fork/revert/edit-stack/MCP support; full audit log.
- **When to reach for it**: code work, debugging, refactors. Anywhere you want "read → think → edit → run tests → re-read" to happen autonomously inside one turn.

### `apps/public/standard/rooms` — multi-participant chat with review gate

- **Shape**: single LLM call per user message; multi-participant rooms (1:1, group, CLI participants). `[DO:app.method(...)]` token grammar for state-changing verbs.
- **Tool surface**: per-agent `server_actions` allowlist (e.g. `{"task": ["add", "complete"], "repo": ["read", "grep", "edit", "write", "exec"]}`). Verbs the agent can emit at all.
- **Gate**: `[DO:]` token parsing with two paths — auto-execute via `call_app` for whitelisted methods OR force-gated via `ALWAYS_GATE_VERBS` (`repo.edit`, `repo.write`, `repo.exec`, `rooms.write_note`) which always route through the review queue regardless of allowlist. Pending actions render as Apply/Reject cards (sandboxed diff for writes, command preview for exec).
- **State**: per-room JSON history (`data/apps/rooms/history/`); pending actions on disk (`data/apps/rooms/pending/`); per-action sandbox dirs for write proposals (`data/apps/rooms/sandboxes/`); scheduled reminders.
- **When to reach for it**: daily personal verbs (add task, log journal, capture, search), persona conversations (finance-advisor, job-scout), chat-shape coding via `eos rooms --code` and the `cli-code` agent with its `repo.*` allowlist.

### `apps/public/standard/assistant` — multi-provider research + vault chat

- **Shape**: single-shot Q&A with vault context injection. WebSocket streaming. Multi-provider compare (`api_compare` sends the same prompt to every think provider, captures latency + diff).
- **Tool surface**: dynamic slash command discovery — reads `[[provides.assistant]]` from every app manifest, builds a cross-app verb palette (e.g. `/task`, `/capture`, `/kb`). Optional model-driven Read/Grep/Glob tool-use.
- **Gate**: none — single-shot, no destructive state changes by default. Slash verbs invoke their app methods directly when used.
- **State**: SQLite sessions with project pinning, auto-archiving, export-to-vault. Project-scoped sessions.
- **When to reach for it**: research questions over the vault, "compare how these providers answer X", quick Q&A with attached images (auto-pins vision provider), project-pinned multi-turn threads.

### `apps/public/standard/voice-assistant` — Aura, voice runtime

- **Shape**: voice-in (`listen`) → think → voice-out (`speak`). Companions (persona variants) switchable mid-conversation. Multi-intent planning (`api_plan` + `api_execute_plan`) sequences multiple intents before executing.
- **Tool surface**: voice intents declared by apps via `[[contributes.voice-assistant.intent]]`. Embedding-aware ranking + recency fallback narrows the in-scope intent set per turn. Post-intent narration (`[[contributes.voice-assistant.narration]]`) appends consequence sentences ("that's 3 open tasks total").
- **Gate**: intents are pre-allowlisted via manifest contributions; multi-turn confirmation flow for risky intents (`api_confirm_intent`).
- **State**: per-conversation message history; active companion; embedding cache for intent ranking.
- **When to reach for it**: hands-busy quick-fire intents ("add a task to call mom"), spoken summaries, phone PWA voice mode. TTS-aware prompting prevents markdown/URLs that mangle in audio.

### `apps/personal/staff` — scheduled + on-demand agents

- **Shape**: each agent follows OBSERVE → DECIDE → ACT phases on a cron schedule. Workflow agents fire on-demand instead of cron. HITL gate queues risky actions for human approval.
- **Tool surface**: each agent is a Python recipe with full SDK access (call_app to any other app). Not LLM-driven verb selection — the recipe defines what to do; the LLM is consulted inside the recipe.
- **Gate**: actions marked `is_risky=true` queue to approvals (`data/apps/personal/staff/approvals.json`); user approves/denies via web dashboard or `eos staff approve <id>`.
- **State**: per-agent shift counts + last-run timestamps; trace history (`data/apps/personal/staff/traces/`); pending approvals queue.
- **When to reach for it**: scheduled autonomous work (nightly job-scouting, daily briefings, weekly reviews). Not interactive.

---

## The 8 frontends + which backend each talks to

| Frontend | Type | Backend | Entry |
|---|---|---|---|
| `/agent/` | Web page | `apps/public/standard/agent` | Browser |
| `/rooms/` | Web page | `apps/public/standard/rooms` | Browser |
| `/assistant/` | Web page | `apps/public/standard/assistant` | Browser |
| `/voice-assistant/` | Web page | `apps/public/standard/voice-assistant` | Browser, phone PWA |
| `/staff/` | Web page | `apps/personal/staff` | Browser |
| page-assistant (✨ FAB sidebar / pinnable rail) | Per-page sidebar | `apps/public/standard/rooms` (primary), `apps/public/standard/assistant` (fallback); **`apps/public/standard/voice-assistant` brain when `feature.companion-frame.enabled`** | Every app page's bottom-right FAB (or a pinned docked rail); loaded by `emptyos/web/static/eos.js` |
| `eos chat` | Terminal REPL | `apps/public/standard/agent` | `@cli_command("chat")` in `apps/public/standard/agent/repl.py` |
| `eos code` | Terminal REPL | `apps/public/standard/agent` | Alias registered by `@cli_command("code")`; seeds the session-scoped Code persona when agent modes are enabled |
| `eos rooms` | Terminal REPL | `apps/public/standard/rooms` | `app.command("rooms")` in `emptyos/cli/main.py` → `emptyos/cli/chat.py`; `--code` bootstraps the review-gated `cli-code` preset |
| `/code/` | Responsive web page | `apps/public/standard/agent` (via WebSocket + sessions) | `apps/extension/plekto/code/` — desktop code workspace (tree + preview/diff + terminal + chat) that reflows into a phone-friendly remote (Agent + Review + Files + Output). Same agent loop and consent manager; mobile approvals are one-action only. Mobile guardrails (one-action approval, output-only terminal, secret files kept out of the workspace) are UX/exposure-reduction, **not** a sandbox — single-user token has full access via `/repo`, `/settings`, or the agent (docs/AUTH.md). |
| `eos staff` | Terminal CLI (not REPL) | `apps/personal/staff` | `@cli_command("staff")` in `apps/personal/staff/app.py` |

**Observations from the matrix:**
- `apps/public/standard/rooms` has web `/rooms/`, the page-sidebar, and CLI `eos rooms` (including its `--code` preset).
- `apps/public/standard/agent` has web `/agent/`, web `/code/`, and one CLI surface with two entry names (`eos chat` / `eos code`). `/code/` is a pure frontend over apps/agent — different layout, same backend.
- `apps/personal/staff` CLI is operational, not conversational (different shape).
- Aura and assistant have no CLI — voice is voice-shaped; assistant is web-shaped.

**`/portal/` — the unified conversation door (the one front door).** `apps/public/standard/portal/`
is a *frontend over multiple backends*, not a backend of its own. Its composer carries a
backend **mode switch** — **Rooms** (`/rooms/api/chat/stream`), **Assistant**
(`/assistant/api/chat`), **Agent** (native WS `/agent/ws/{sid}`, a deliberate subset of `/agent/`:
streaming text + tool cards + permission modal + turn footer + cancel), and **Code** (the `/code/`
IDE workspace hosted in portal's iframe pane via `?embed=1`). Each thread remembers its backend
(`asst:`/`agent:` id prefixes; rooms = bare id). Backends are untouched; the dedicated pages and
CLIs all still work. `portal` is the `companion` suite `surface` (`suites.toml`). This exists to
collapse the "which chat app do I open" choice into one door — it does **not** merge any backend
(see "Coexist, not merge" below). Portal drives its **own** agent sessions. The agent backend
allows one running turn per session across every socket (`AgentApp._claim_turn`, 2026-09-12), so
a second window on the same session is refused mid-turn rather than interleaving into its history.

**Chat-first home (dark: `[apps.portal] feature.chat-first.enabled`).** With the flag on, a new
conversation is an agent session with `profile="chat"` (`apps/public/standard/agent/profiles.py`):
the chat persona (`CHAT_SYSTEM_PROMPT`); VaultQuery / Locate / WebSearch / Skill / ContextRef plus
narrowed Fetch (public-web GET), CallApp (declared app verbs, never an eligibility-`never` one) and
Read (vault + allowed folders, never the repo) — no shell, code execution or file writes; and none
of the coding passes (orient, app scaffold, skill auto-trigger, episodic recall, plan-mode banner,
CLAUDE.md, repo map). Rooms / Assistant / Agent (coding) / Code move behind a **More** menu; a
model picker sets the session's provider from `GET /agent/api/providers`. A chat never runs on a
natively-agentic provider (claude-cli brings its own tools past the registry and consent gate), a
chat turn on a cloud model passes the cloud-consent gate, and once stored history has a wire kind
it only switches between providers of that kind. The sidebar gains **Projects** (a name + standing
instructions every chat in it receives), Recent chats and a chat search; "Copy rooms into projects"
turns portal folders into projects (their agent threads move; Rooms threads stay in the folder). A
new thread inside a portal folder still starts in Rooms. The composer attaches vault notes, uploads
and pasted screenshots, and a **Vault** toggle grounds the answer in the user's notes — neither
reaches a cloud model without an explicit yes for that message (`docs/AGENT.md` § Attachments).
When an answer is a **thing** rather than prose, the model writes a standalone page through the
flagged `CreateArtifact` tool and it opens in a side panel beside the conversation
(`[apps.agent] feature.artifacts.enabled`). viz stores it, so an artifact gets the record note and
the version ring a generated one gets — a revision keeps the render it replaced, and the panel's
picker can show it. The preview frame has an opaque origin (`sandbox="allow-scripts"`, plus a CSP
on the response when `[apps.viz] feature.html-csp-sandbox.enabled` is on), so model-written script
never runs with the daemon's credentials.
**Connectors** (`[apps.agent] feature.mcp-inbound.enabled`) let a chat reach an
external MCP server's tools: the machine's servers are added, connected and
disconnected without a restart, and each chat chooses which of them it may use
— a connected server's tools existing is not the same as every conversation
being offered them. Adding one that spawns a process shows the exact command
line first. Same engine, same WS, same persistence — a
profile is data, not a fifth backend. Details: `docs/AGENT.md` § Session profiles. Plan:
`_plans/eos-desktop-gui.md` (B1).

---

## Mental model — four families

Don't try to keep 8 frontends in your head. Group them:

| Family | Backends | Frontends | One-line semantic |
|---|---|---|---|
| **TOOL LOOP** | agent | `/agent/`, `eos chat`, `eos code` | "Do this multi-step thing autonomously, ask before each tool" |
| **CONVERSATION** | rooms, assistant | `/rooms/`, `/assistant/`, page-sidebar, `eos rooms` (`--code` preset) | "Talk to me; you propose, I review, you act" |
| **VOICE** | voice-assistant | `/voice-assistant/`, phone PWA | "Hands-busy intents, narrated back" |
| **CRON** | staff | `/staff/`, `eos staff` | "Run on schedule, wake me only for approvals" |

---

## Decision tree: which one should I use?

Walk these in order; first match wins.

1. **Is it a scheduled background task** (nightly research, daily summaries)? → `apps/personal/staff`. Use `eos staff dispatch` for on-demand workflow agents.
2. **Are you driving**, **hands-busy** (cooking, driving, walking)? → `/voice-assistant/` (web) or phone PWA. Speak intent, hear narration.
3. **Are you adding a quick personal verb** (task, journal, capture, expense, kb tag)? → `eos rooms` if at terminal, page-sidebar if in browser, `/voice-assistant/` if voice. Chat-shape, single-turn, [DO:]-gated.
4. **Do you want the assistant to read your vault and answer over it** (with citations / multi-provider compare)? → `/assistant/` (web). Vault-aware, multi-provider, project-pinned sessions.
5. **Are you doing code work that fits in one reviewed turn** (one edit, one test, one explanation)? → `eos rooms --code` (CLI) or `/rooms/` (web) with the `cli-code` agent. Diff cards before any write; command cards before any exec.
6. **Are you doing code work that needs an autonomous tool loop** (refactor across files, debug a failing test with multiple hypotheses)? → `eos chat` or its Code-persona alias `eos code` (CLI), or `/agent/` (web). Per-tool consent still applies.

If you're not sure — start with **conversation** family. It's the cheapest model fit, the most-reviewed gate, and the easiest to abandon mid-flow.

---

## CLI/web overlap principle

CLI and web are different frontends over the same backend, not different products:

| CLI | Web equivalent | Shared backend |
|---|---|---|
| `eos chat`, `eos code` | `/agent/` | apps/agent — same sessions, tools, consent manager; `eos code` only seeds the Code persona when enabled |
| `eos rooms` (including `--code`) | `/rooms/`, page-sidebar | apps/rooms — same agents, history, [DO:] gate |
| `eos staff` | `/staff/` | apps/personal/staff — same agents, approvals, traces |

If you fix a bug in the backend, every frontend over that backend picks it up. The CLIs add nothing to the backend — they just render the same NDJSON streams as terminal output and proxy clicks/inputs.

---

## "Coexist, not merge" — explicit decisions

The reflexive instinct on first inventory was "retire assistant, fold Aura into rooms, merge skill registries." An audit in 2026-05-16 showed each has unique value the others don't replicate. **None of these merges should happen.** Documenting the decisions so they don't get re-questioned.

### Decision A — `apps/public/standard/assistant` stays as its own backend

**Unique features rooms doesn't have:**
- `think_compare()` — send the same prompt to ALL providers, capture latency + responses. No rooms analogue.
- Session store with **project pinning**, auto-archiving, export-to-vault. Rooms history is per-agent; assistant tags sessions to projects.
- **Vault context injection** — keyword search + read top-N results, optional model-driven Read/Grep/Glob tool-use.
- Dynamic **slash command discovery** — reads `[[provides.assistant]]` from every app manifest. Rooms requires per-agent allowlist.
- **Vision provider routing** — auto-pins to openai-mini when images attached.
- 15 web routes including TTS/STT endpoints that other apps consume.

**Verdict**: keep. If a feature here ever needs to be in rooms too (e.g. project pinning), port it — don't fold the whole app.

### Decision B — `apps/public/standard/voice-assistant` stays as its own backend

**Unique features rooms doesn't have:**
- **Companion switching** mid-conversation via `[[contributes.voice-assistant.companion]]` — voice-scoped persona variants.
- **Voice intent dispatcher** — `[INTENT:verb(args)]` parsing with embedding-aware ranking + recency fallback. Different shape than rooms' `[DO:]`.
- **Post-intent narration** — apps append consequence sentences after intents fire.
- **Multi-intent planning** — `api_plan` + `api_execute_plan` reason over available intents before executing the sequence.
- **TTS-aware prompting** — strict framing prevents markdown/URLs that mangle in audio.
- **Card renderers** — Aura's glassy full-screen visual island with structured response cards (`stat-tile`, `entity-card`, `task-list`).

**Verdict**: keep. Voice is its own runtime; rooms is text-primary. They share the underlying capability layer (`think`, `listen`, `speak`); the dispatcher / narration / planning layer above that is voice-specific.

**Revision (2026-06-29) — one brain, two frontends.** Re-examined when building the top-level companion rail: almost everything called "voice-specific" above (companion switching, intent ranking, narration, planning, card *data* shapes) is not actually modality-bound — only the I/O transport (mic/speaker vs textarea) and TTS-aware prompting are. `turn_events` (`voice-assistant/chat_pipeline.py`) was already the one brain, serving both `/api/chat_stream` (voice) and `/api/chat_text` (text). So the page-assistant rail becomes the **text face of that same brain**: behind `[apps.voice-assistant] feature.companion-frame.enabled` (default dark), it posts to `/voice-assistant/api/companion_turn` (= `turn_events(text_only=True)` collapsed to one JSON, mirroring `/api/device_turn`) instead of rooms — gaining Aura's verb dispatch, cards, pending Apply/Reject, and the ⚡ session grant, differing only by modality. No sdk extraction was needed (both consumers live in voice-assistant → Rule 9 keeps the brain in-app). **Rooms is NOT merged** — it keeps its distinct multi-participant + CLI identity; only the single personal-assistant companion (page-assistant's degenerate one-agent room) moved to the shared brain. Flag off ⇒ Aura and the rail are byte-identical to before.

### Decision C — page-sidebar keeps the assistant fallback

When a page sets `pageGpt: "<rooms-agent-id>"`, the sidebar POSTs to `/rooms/api/chat`. Else it falls back to `/assistant/api/chat`. The fallback path uses assistant's session store + vault context injection — features rooms doesn't replicate.

**Verdict**: keep both fallback paths.

### Decision D — `apps/extension/dev/skill` and `apps/public/standard/agent/skills.py` both exist

Same SKILL.md format, two registries — one for rooms slash-overlay (`apps/extension/dev/skill`), one for the agent loop's `Skill` tool (`apps/public/standard/agent/skills.py`). Merging means one consumer dictates the other's loading semantics.

**Verdict**: keep both. A skill written once works in both via the shared file format.

### Decision E — `apps/extension/dev/repo` and `emptyos/sdk/agent_tools/*` both exist

`apps/extension/dev/repo` exposes read/grep/edit/write/exec as **rooms-callable verbs** (chat-shape, [DO:]-gated, sandboxed diff for writes). `emptyos/sdk/agent_tools/{read,edit,write,bash,grep,glob}` are **loop-internal tools** the agent loop dispatches with per-call consent.

Same operations, two consumers, two gate philosophies. Not duplication — parallel infrastructure for different UX shapes.

**Verdict**: keep both.

---

## The observer — watches, never talks

**cockpit** (`apps/extension/dev/cockpit/`, shipped 2026-07-21) is a *sixth category* the five-backend model has no slot for: a **read-only observer** of agent sessions, not a participant. It is deliberately NOT one of the five backends and must not be merged into any of them.

- **No session store, no LLM backend of its own.** It tails the transcript JSONL that Claude Code (`~/.claude/projects/`) and Codex (`~/.codex/sessions/`) write in *your own terminal*, subscribes to EmptyOS's `agent:*` bus (the `eos-agent` source), and — behind `feature.autorun-view.enabled` — tails fix-agent/dogfood `stream.jsonl` run streams. It renders the conversation, artifacts, and browser screenshots in a 3-pane UI + a mission-control wall.
- **No gate — a hard read-only invariant.** Cockpit *cannot spawn or steer a CLI*; a terminal session owns its own stdin (an OS boundary, not a missing feature). Its own WS (`/cockpit/ws/{channel}`) is receive-only (`ping` from the client is the only inbound message), and the high-frequency stream never touches the persisted kernel bus — only two lifecycle events do (`cockpit:session_discovered/_ended`).
- **The `active_sessions()` seam** (state/branch/deep-link per session) is consumed by devboard's "Live agent sessions" lane — a clean read-only layering, not a duplicated session list.
- **Attention router, not chat.** Its value is *which agent needs me now* (state chips: working/waiting/stuck), *what did the others do while I looked away* (catch-up digest), and *what's it costing* (token rollup) — the scarce-resource-is-attention view when many sessions run in parallel. The idle→waiting nudge is the `cockpit-attention-push` loop (`emptyos/sdk/loops.py`, dark).
- **Not in `suites.toml`.** Cockpit is `apps/extension/`, `store_category="dev"` — dev chrome, ineligible for the Companion suite (which groups only `apps/public/standard/` conversation apps). Recorded here so a future suite pass doesn't re-flag it.

The four chat renderers (`/agent/`, `/rooms/`, cockpit, `/code/`) all render through the shared `EOS_UI.paintMarkdown` / `EOS_CONVERSATION` stack. `page-assistant.js`'s companion + nudge path (`_mdRich`) also delegates to the shared `EOS_UI.renderMarkdown`; its rooms path keeps a **deliberately minimal** renderer (`_renderMd`, bold/code/br only) because that path must leave `[BUTTON:]`/`[DO:]`/`[ACTION:]` action tokens intact for the regex passes that turn them into click-to-execute buttons — a rich renderer would escape the brackets and break the buttons. Not a consolidation target (token-safety constraint, verified 2026-07-22).

## Cross-references

- `services/chatbot/README.md` § Architecture — the **external-site sibling** of this stack. Deliberately *not* one of the 5 backends above: it's a Lane 1 service (no vault, no kernel), so it can't reach data or verbs directly. Same shape with a **digest layer** substituted in the middle (`external site → emptyos digest → chat`): crawled corpus + catalogue feed stand in for `vault_query`, `sites.toml` `allowed_actions` for the verb registry, `commerce_reply()` for `[INTENT:]` routing, `secure_action_form` for the review gate, and the embeddable widget for the companion rail. Read it before adding a chat surface for a site EmptyOS doesn't own; patterns port there, code never does.
- `docs/AGENT-FRAMEWORK.md` — the auto-agent **assembly manual**: this doc maps the conversational *surfaces*; that one maps the *primitives* (schedule/persona/verbs/gates/budgets/memory/comms/UI) you compose into an autonomous agent. Config, not code.
- `apps/public/standard/agent/manifest.toml` — the autonomous tool-loop backend.
- `apps/public/standard/rooms/manifest.toml` — the chat-shape multi-participant backend.
- `.claude/rules/room-review-gate.md` — `[DO:]` token grammar + Apply/Reject mechanics.
- `.claude/rules/voice-intents.md` — `[[contributes.voice-assistant.intent]]` contract.
- `.claude/rules/autopilot-grants.md` — explicit-grant model (per-actor + per-verb + per-scope) layered on top of all gates.
- `apps/personal/staff/agents.py` — the staff agent definitions.
- `emptyos/sdk/agent_loop.py` — the 950-line tool-use loop driver.
- `emptyos/cli/chat.py` — `eos rooms` REPL and `--code` dispatch.
- `emptyos/cli/code.py` — rooms code-preset bootstrap + cli-code agent seed.
- `apps/public/standard/agent/repl.py` — shared `eos chat` / `eos code` agent REPL (prompt_toolkit, slash commands, skill catalog).

---

## When you're adding a new backend

Before you add one, ask:
1. Can an existing backend take this with a new agent record (rooms), a new contribution slot (voice), a new tool (agent), or a new staff agent (staff)?
2. If yes — extend an existing backend; you get all that backend's frontends for free.
3. If no — what's the new shape? Where does it sit in the four families? Does it deserve its own CLI / web / sidebar frontend?

Most additions extend an existing backend. New backends should be rare and well-justified.
