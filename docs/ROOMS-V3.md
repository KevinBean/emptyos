# Rooms v3

> The conversation substrate. Where you, named agents, and CLIs meet.

`apps/rooms/` started life as `apps/gpts/` — a single-agent persistent-chat
app. Phases 0-27 turned it into a multi-participant workspace with vault
integration, scheduled check-ins, review-gated CLI participants, and a
full lifecycle (archive / export / distill / pin / remember / reply).

This doc is the operator's reference for what's there and where it lives.

## What changed for users

- **`gpts` → `rooms`.** Old URLs 308-redirect; per-machine state migrates
  from `data/apps/gpts/` to `data/apps/rooms/` on first boot. Every
  pre-existing single-agent thread becomes a 1:1 room.
- **Multi-participant.** Group rooms hold 2+ agents (or agents + CLIs).
  `@mention` directs a turn; first-listed agent answers when no mention.
- **CLI participants.** `claude-cli` streams with tool events + review gate;
  `codex` and `gemini` run in buffered text mode. Configure via
  `[plugins.agent-runtime.clis.<id>]` in `emptyos.toml`.
- **Vault is first-class.** `[[wikilinks]]` in any message resolve to file
  contents, prepended to the LLM prompt. Persistent room knowledge lives
  in the Knowledge tab. Distill writes a KB note tagged `room-distill`.
- **Tasks ripple.** `/task <text>` attaches a task to the room AND inserts
  it into `apps/projects/`'s universal pool with a `🗨️` marker. The
  Activity drawer's Tasks tab can also browse all vault tasks (across
  every project) and attach existing ones to the current room.
- **Lifecycle.** Archive (reversible) / Export to vault (markdown) /
  Distill into KB (LLM-summarised). All emit reactor events that ripple
  to today's journal as breadcrumbs.
- **Time.** Visit tracking + unread badges + catch-me-up banner on return.
  `/remind 2h` sets a one-shot. `/schedule` sets a recurring agent
  check-in (cron). The inbound dashboard surfaces fired reminders +
  pending actions + unread rooms when you open `/rooms/` cold.
- **Power-user.** 21 slash commands, drafts (auto-saved per room), pinned
  messages, voice playback, snippet library, context inspector, reply
  threading, agent memory.

## Quick start

| Action | How |
|---|---|
| Open a room | Click in the sidebar |
| @mention an agent | Type `@`, pick from popup |
| Reference a vault note | Type `[[`, search, pick — file content lands in next prompt |
| Run a slash command | Type `/`, pick from popup, fill args, Enter |
| Pin a message | Hover the message → 📌 |
| Reply to a message | Hover the message → ↩ |
| Hear a reply aloud | Hover the message → 🔊 |
| See what the LLM sees | `/context` |
| Save a frequent prompt | `/save <name>`, recall with `/snip <name>` |
| Defer attention | `/remind 2h finish review` |
| Recurring check-in | `/schedule` |
| Pin a fact agent should always remember | `/remember Kevin prefers tabs over spaces` |

## Slash commands (21)

| Command | What |
|---|---|
| `/distill` | Summarise this room into a KB note in the vault |
| `/export` | Export the thread as a markdown vault note |
| `/archive` | Archive (or unarchive) the current room |
| `/task <text>` | Attach a task to this room and the inbox project |
| `/find <query>` | Sidebar cross-room message search |
| `/add <id>` | Add an agent or CLI to this room |
| `/remove <id>` | Remove a participant |
| `/clear` | Clear chat history |
| `/help` | List all commands |
| `/remind <when> [note]` | Schedule a reminder (e.g. `2h`, `tomorrow`, `14:30`) |
| `/context` | Inspect what the LLM sees on the next turn |
| `/schedule` | Set / view the cron-driven check-in for this room |
| `/snip <name>` | Insert a saved snippet into the input |
| `/save <name>` | Save the current input as a snippet |
| `/snippets` | Open the snippet library |
| `/remember <fact>` | Add a persistent memory the agent always sees |
| `/speak` | Toggle auto-speak (read agent replies aloud) |
| `/mute` | Stop speech and disable auto-speak |
| `/pin` | Pin the most recent assistant message |
| `/discard` | Discard the current draft |

## Architecture

### Storage map

```
data/apps/rooms/
├── agents/<id>.json           # room records (name, system_prompt,
│                              #   participants, knowledge_files,
│                              #   pinned_ts, schedule, memory, …)
├── history/<id>.json          # message arrays (capped 200 per room)
├── pending/<action_id>.json   # review-gate actions awaiting Apply/Reject
├── reminders.json             # one-shot reminders (status + due_ts)
├── snippets.json              # named prompt fragments
├── visits.json                # last-visited timestamp per room
└── actions.jsonl              # [DO:] action audit log

vault/30_Resources/EmptyOS/rooms/
├── exports/<date>-<slug>.md   # /export targets
└── distills/<date>-<slug>.md  # /distill targets (tagged kb,room-distill)
```

### Participants

Each room has `participants: [{type, id, ...}]`:
- `{type: "user", id: "me"}` — always first
- `{type: "agent", id: "<agent_id>"}` — references an agent record
- `{type: "cli", id: "claude-cli"|"codex"|"gemini", model?, effort?, allowed_tools?, cwd?, timeout_s?}`

Single-agent rooms are stored as the agent record itself; the participants
list is synthesised on read. Group rooms have a generated `group-<hex>`
id and an explicit participant list.

### Dispatch

```
api_chat_stream
├── _resolve_responder(text, parts)        # @mention or first responder
├── if cli → _dispatch_cli_turn
│   ├── claude-cli: streaming + tool events + [DO:] gate
│   └── other: text_cli_run (buffered)
└── if agent → think_stream + _execute_server_actions
```

Memory + wikilinks + knowledge files are merged into context before
`_build_prompt_async` runs.

### Cron jobs

- `rooms:reminders-tick` — every 60s, fires due reminders
- `rooms:schedule:<room_id>` — per-room cron from `room.schedule.cron`,
  fires `_fire_room_schedule` which generates a self-initiated message

### Events emitted

`rooms:chat`, `rooms:undo`, `rooms:created`, `rooms:participant_added`,
`rooms:action_applied`, `rooms:action_rejected`, `rooms:archived`,
`rooms:unarchived`, `rooms:exported`, `rooms:distilled`, `rooms:pinned`,
`rooms:unpinned`, `rooms:reminder_fired`, `rooms:scheduled_fired`.

The reactor (`apps/reactor/reactions_work.py`) listens to the lifecycle
events and writes journal breadcrumbs:

| Event | Journal line |
|---|---|
| `rooms:created` | 🏠 New room: `<title>` |
| `rooms:archived` | 🗄️ Archived room: `<id>` |
| `rooms:exported` | 📄 Exported room thread to `<path>` |
| `rooms:distilled` | ✨ Distilled `<n>` messages into `<path>` |
| `rooms:action_applied` | ✓ Applied `<app>.<method>` from room `<id>` |

### Hub panels

Two panels contributed by the rooms manifest:
- `rooms-pending` — stat-tile of total pending actions (drops silently when 0)
- `rooms-recent` — plain-list of 5 most-recent rooms

### Voice intents (Aura)

- `rooms.list` — speak top 5 recent rooms + plain-list card
- `rooms.open <name>` — substring-match a room name and produce a card
  with the URL; disambiguates politely on multiple matches

## Per-CLI configuration

Each CLI participant accepts these fields on the participant record:
- `model` — passed as `--model <id>` (claude-cli only)
- `effort` — passed as `--effort <level>` (claude-cli only)
- `allowed_tools` — comma-separated tool list (claude-cli only)
- `cwd` — working directory; defaults to `notes_path` (vault root)
- `timeout_s` — wall-clock kill-switch
- `extra_args` — arbitrary list appended to the command

For non-claude CLIs, `[plugins.agent-runtime.clis.<id>]` in `emptyos.toml`
declares:
```toml
[plugins.agent-runtime.clis.codex]
binary = "codex"                  # path or PATH name
args_template = ["exec", "{prompt}"]
supports_system = false           # true if the CLI has a system-prompt flag
env_drop = []                     # env vars to strip
```

## Mobile

`@media (max-width: 700px)` block adapts:
- Sidebar fills the viewport (existing isMobile JS toggles which panel shows)
- Activity drawer goes full-width
- Tier filter + member chip rows scroll horizontally instead of wrapping
- Roster picker collapses to single column
- A `body.rooms-overlay-open` class hides global FABs (`#eos-fab-dock`,
  `.pa-fab`, `.cap-fab`) while drawers / modals are open

## Test harness

`tests/personal/test_rooms_design_shots.py` is a screenshot-driven visual
regression check. 24 captures cover:

```
01-landing             09-group-empty-state    17-mobile-drawer
02-one-on-one          10-pending-dashboard    18-mobile-group-modal
03-group-modal         11-overflow-phase8      19-bulk-select
04-group-chat          12-sidebar-search-hits  20-schedule-modal
05-agent-edit-modal    13-archived-tab         21-memory-tab
06-activity-drawer     14-hub-rooms-panels     22-group-suggestions
07-activity-tasks      15-mobile-landing       23-context-inspector
08-chat-overflow       16-mobile-chat          24-snippets-library
```

Run after a UI change to spot drift:
```bash
python -m pytest tests/personal/test_rooms_design_shots.py -v --timeout=30
```

`tests/personal/test_rooms.py` covers API+UI smoke (11 cases).
`tests/test_page_assistant_*.py` cover cross-app contracts.

## What's not in v3

- Smart agent suggestions are keyword-based; no embeddings
- CLI write-tools route through review gate as `[DO:]` tokens, not actual
  filesystem operations — claude-cli still runs read-only
- No multi-user rooms; single-user system
- No reply quote rendering for parents that have scrolled out of the 200-msg
  cap (graceful fallback message)
- Codex / Gemini adapters use best-guess flag templates; users with
  different installed versions override via emptyos.toml

## Files modified

| Surface | File |
|---|---|
| App backend | `apps/rooms/app.py` |
| App frontend | `apps/rooms/pages/index.html` (~188k) |
| Manifest | `apps/rooms/manifest.toml` |
| Cross-app: tasks | `apps/projects/app.py` (`add_task_to_project room_id`, `tasks_for_room`, `ROOM_PATTERN`) |
| Cross-app: tasks | `apps/task/app.py` (`api_attach_room`) |
| Cross-app: scroll | `apps/scroll/app.py` (call_app calls renamed) |
| Reactor | `apps/reactor/reactions_work.py`, `apps/reactor/manifest.toml` |
| SDK | `emptyos/sdk/intents.py` (extracted from voice-assistant), `emptyos/sdk/utils.py` (`ROOM_PATTERN`) |
| Web | `emptyos/web/server.py`, `emptyos/web/clustering.py`, `emptyos/web/static/page-assistant.js` |
| Plugin | `plugins/agent-runtime/{plugin.py, manifest.toml}` (extracted from dogfood-agent) |
| Dogfood | `apps/dogfood-agent/app.py` (uses agent-runtime plugin) |
| Voice | `apps/voice-assistant/intents.py` (re-exports from SDK) |
| Tests | `tests/personal/test_rooms_design_shots.py` (24 shots), `tests/personal/test_rooms.py`, `tests/helpers.py` |

## Phase index

```
 0  Foundation extraction (intents, agent-runtime)
 1  Rename gpts → rooms
 2  Multi-participant
 3  CLI participants (claude-cli)
 4  Room↔task pointer
 5  Review gate (pending [DO:] actions)
 6  UI polish + global pending dashboard
 7  Hub panels + cross-room search + voice intents
 8  Archive + export + distill
 9  Visit tracking + unread + catch-me-up
10  Slash commands
11  Vault file references ([[wikilinks]])
12  Inbound activity dashboard
13  Reactor breadcrumbs
14  Mobile responsive
15  Pin messages
16  Drafts
17  Reminders
18  Per-room knowledge UI
19  Context inspector
20  Voice playback + sidebar sort fix
21  Snippet library
22  Scheduled check-ins
23  Codex / Gemini CLI adapters
24  Bulk operations
25  Reply threading
26  Agent memory
27  Smart agent suggestions
```
