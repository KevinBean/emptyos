# EmptyOS Design Document

> **EmptyOS — a mind companion. Think and create with you, not for you.**

## Core Thesis

**An operating system is just a human doing things.** Reading files, writing notes, thinking about problems, searching for information, scheduling tasks. These are human capabilities first. Tools are optional accelerators.

EmptyOS works at every level:

| Level | What | Examples |
|---|---|---|
| **Autonomous** | Scheduled agents, event-driven automation | Cron jobs, file watchers, auto-review |
| **Smart Tools** | LLM for thinking, semantic search | Any OpenAI-compatible API |
| **Basic Tools** | File I/O, grep, cron | Filesystem, ripgrep, shell |
| **Human** | You read, you write, you think | Pen and paper works too |

The OS never **requires** any specific tool. It **benefits** from them. With zero tools configured, it asks the human.

---

## Three Runtime Modes

EmptyOS has three distinct modes of execution. The first two are obvious. The third is the most powerful and the least documented.

| Mode | What | How | Verb |
|---|---|---|---|
| **Daemon** | The OS runs on port 9000, apps serve UIs and APIs, events ripple, agents wake on cron | `eos start` | Serve |
| **CLI** | One-shot commands, no daemon needed, local kernel boots per invocation | `eos capture "idea"` | Act |
| **Conversation** | An AI coding tool loads the codebase as context, understands the architecture, and builds/evolves the system | Any AI IDE session | Grow |

### Conversation Is Runtime

In daemon mode, the system serves what exists. In conversation mode, the system **evolves**. Every session where a new app is created, a pattern is extracted into the SDK, or an event chain is wired — that is the Growth verb happening in real time.

The conversation is not "development" in the traditional sense. It is the system's primary growth mechanism:

- `CLAUDE.md` is a **boot prompt** — it loads the full architecture into an AI's context window
- The AI reads vault notes, understands the user's life data, makes architectural decisions informed by the consciousness model
- New apps, refactored patterns, wired events — these are the output of the system running in conversation mode
- The reflect app tries to automate this loop, but the real reflection happens here

```
CLAUDE.md + Codebase + Vault
    ↓ (loaded into AI context)
Conversation Mode
    ↓ (AI understands architecture + philosophy + user intent)
New apps, extracted patterns, wired events, updated docs
    ↓ (committed back)
Better CLAUDE.md → Better next conversation → Compounding growth
```

### Tool Independence

Conversation mode does not depend on any specific AI tool. The requirements are:

| Requirement | Why | Who provides it |
|---|---|---|
| Large context window | CLAUDE.md + codebase must fit | Claude, Gemini, GPT-4.1 (all 1M+) |
| File read/write/search | Navigate 73 apps, edit code | All AI coding tools |
| Session memory | Remember cross-session context | Claude Code, Cursor, Windsurf |
| Philosophy alignment | Make judgment calls coherent with the vision | **The hard part** — this is contextual, not technical |

The capability abstraction that makes daemon mode tool-agnostic also applies here. At runtime, `self.think()` routes to any LLM provider. At development time, `CLAUDE.md` routes to any AI coding tool. The pattern is the same: **declare what you need, let the environment provide it.**

The only real lock-in is the quality of `CLAUDE.md`. A well-documented system can be grown by any sufficiently capable AI. The documentation IS the portability layer.

### The Daemon Also Grows (Autonomous Mode)

The Growth Agent (daily cron) and Root Agent (weekly cron) are attempts to bring conversation-mode evolution into daemon mode — automated self-improvement without a human in the loop. The Reflect app closes the loop by reading its own prior output.

But these agents operate within the existing architecture. They cannot create new apps, extract new SDK patterns, or make architectural leaps. True evolution still requires the conversation mode — a context window large enough to hold the whole system and a reasoning engine capable of coherent architectural decisions.

The aspiration: as AI agents mature, the boundary between daemon mode and conversation mode dissolves. The system grows itself.

---

## Architecture

Two layers. Apps on top, platform underneath. Apps declare what they need, the platform provides it.

```
┌──────────────────────────────────────────────────────────┐
│                         Apps                              │
│                                                          │
│  All first-class. Complexity varies by what they need.   │
│                                                          │
│  capture  note  task  briefing  dashboard  studio        │
│  assistant  cable-rating  fiction-engine  talkbuddy       │
│  digital-twin  compose  podcast  applio  isla-friends    │
├──────────────────────────────────────────────────────────┤
│                    Platform Runtime                       │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Services                                        │    │
│  │  real-time (WebSocket/SSE), compute workers,     │    │
│  │  scheduler, vault watcher, notifications         │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Libraries                                       │    │
│  │  media pipeline (audio/video/image),             │    │
│  │  frontend runtime (themes/components/nav)        │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Engines                                         │    │
│  │  engineering (IEC 60287, EMT), physics (catenary, │    │
│  │  thermal), geospatial (GIS), game (state/NPC)   │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Connectors (plugins)                            │    │
│  │  ollama, comfyui, voice-api, telegram,           │    │
│  │  google-maps, acestep, weather                   │    │
│  └──────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────┤
│                        Kernel                            │
│                                                          │
│  Config, Capabilities (think/read/write/search),         │
│  EventBus (SQLite), ServiceRegistry,                     │
│  AppLoader, PluginLoader, Providers                      │
└──────────────────────────────────────────────────────────┘
```

### The Three Layers Defined

**Kernel** — the absolute minimum. Config, 4 capabilities with provider chains, event bus, service registry, loaders. Human is always the final fallback for any capability. This never changes.

**Platform Runtime** — shared infrastructure that apps build on. Four distinct categories:

| Category | What It Is | How Apps Use It | Analogy |
|---|---|---|---|
| **Services** | Always-running system processes | Background, invisible | systemd units |
| **Libraries** | Shared code with no lifecycle | Imported/called | libc, libpng |
| **Engines** | Stateful computation domains | Invoked for heavy work | Physics engine, game engine |
| **Connectors** | Adapters to external hardware/APIs | `self.require("comfyui")` | Device drivers |

**Apps** — everything user-facing. ALL apps are first-class citizens with the same manifest format, lifecycle, and loader. A capture app and a fiction engine are both apps. The difference is what platform runtime they declare in their manifest.

### No Tiers, No Hierarchy

Apps are NOT tiered. There's no L2/L3/L4. There are just apps that need different things:

```toml
# Simple app
[requires]
capabilities = ["read", "write"]

# Complex app
[requires]
capabilities = ["read", "write", "think"]
services = ["compute", "realtime"]
connectors = ["comfyui", "voice_api"]
engines = ["engineering"]
libraries = ["media", "frontend"]
```

The platform either has what the app needs, or it doesn't. No privilege levels. No special treatment.

### Apps Can Ask for New Soil

When an app declares a dependency that doesn't exist, that's not an error — it's a **growth signal**. The manifest is a wish list. Unmet dependencies tell the system what infrastructure to grow next.

```toml
# This app needs a connector that doesn't exist yet
[requires]
connectors = ["telegram"]  # ← not built yet → growth signal
```

The system tracks unmet dependencies as **soil requests**:

```
app_loader: WARNING: 'podcast' unmet deps: connector:telegram
  → This is not a failure. This is the system telling itself what to grow next.
```

**Growth feedback loop:**
```
App manifest declares need → Unmet dependency logged →
grow-os skill sees the gap → Builds the missing piece →
App now works at full capacity → New apps can also use it
```

Apps don't just consume infrastructure — they **pull it into existence** by needing it. The island app that declares `engines = ["game"]` is the reason the game engine gets built. The podcast app that declares `connectors = ["telegram"]` is the reason the Telegram plugin gets built.

**Unmet deps are the system's to-do list.**

---

## The Vault as Hard Drive

### Two Storage Domains

| Domain | What Lives There | Format |
|---|---|---|
| **Vault** | User state, durable knowledge | Markdown files |
| **Kernel storage** | Operational state, caches, indexes | SQLite, JSON in `data/` |

The vault is the user-visible filesystem. Everything important is human-readable markdown. The kernel storage is system internals — event history, app state, job queues, indexes.

### Vault Mapping

```
vault/                  <- the "hard drive"
├── 00_Inbox/           <- scratch, incoming
├── 10_Projects/        <- active work
├── 20_Areas/           <- ongoing responsibilities
├── 30_Resources/       <- reference library
├── 40_Archive/         <- cold storage
├── 50_Journal/         <- daily logs
└── 99_Attachments/     <- non-text files

emptyos.toml            <- system config
data/events.db          <- event history
data/state/             <- app persistent state
data/apps/              <- app-local data (JSON, SQLite)
```

### The Vault Is External

The vault is an **external drive**, not built-in storage. EmptyOS mounts it via config:

```toml
[notes]
path = "/path/to/your/notes"    # mount point — can be any folder
```

Like a real OS mounting an external disk:
- The OS lives in one place, the data lives in another
- Swap the path, swap the data. Same OS, different person's vault.
- Multiple OS instances can mount the same vault.
- EmptyOS does NOT own the vault. Remove EmptyOS and everything is still readable plain text.

The vault works in Obsidian, Logseq, any text editor — with or without EmptyOS running.

### Vault Conventions

| Convention | Rule | Example |
|---|---|---|
| **Frontmatter** | YAML at file top | `status: active`, `tags: [project]` |
| **Links** | Wikilinks for relationships | `[[Related Note]]` |
| **Tasks** | Obsidian Tasks format | `- [ ] Do thing 📅 2026-04-05` |
| **Naming** | Kebab-case | `my-project-plan.md` |
| **Prefixes** | `_` MOC, `@` person, `$` finance, `%` config | `@John.md` |
| **Dates** | ISO 8601 | `2026-04-05` |

### Data Architecture: Two Domains

User knowledge and machine telemetry live in different places, by design.

**User knowledge → Vault notes (markdown + frontmatter)**
- Job applications, contacts, journal entries, reading notes, expense logs
- Human-readable, Obsidian-queryable, survives EmptyOS removal
- VaultIndex (in-memory) provides fast queries without scanning files
- Apps read/write via `vault_query()`, `vault_update()`, `vault_read_section()`

**Machine telemetry → SQLite/JSON in `data/`**
- Event history, system logs, LLM billing, chat sessions, activity logs
- High-frequency, append-only, session-scoped, or counter-based
- Not human knowledge — operational infrastructure

| Data | Where | Why |
|---|---|---|
| Job applications | Vault: `tags: [job-application]` frontmatter | User knowledge, Obsidian-editable |
| Contact profiles | Vault: `@Person.md` frontmatter | User knowledge, wikilinks |
| Journal entries | Vault: `50_Journal/{date}.md` | User knowledge, daily notes |
| Expense records | Vault: markdown tables | User knowledge, auditable |
| Task checkboxes | Vault: `- [ ] text 📅 date` | User knowledge, Obsidian native |
| Event history | SQLite: `data/events.db` | Machine telemetry, high-volume |
| System logs | SQLite: `data/syslog.db` | Machine telemetry, debugging |
| LLM billing | SQLite: `data/apps/billing/app.db` | Machine telemetry, counters |
| Chat messages | SQLite: `data/apps/assistant/app.db` | Session-scoped, ephemeral |
| Activity logs | SQLite: per-app `app.db` | Machine telemetry, analytics |

**Rule of thumb:** If a human wrote it, would edit it, or needs it to survive a system reset → vault. If the system generates it at high frequency for internal bookkeeping → SQLite.

### Three Data Layers in a Vault Note

A single vault note can contain all three layers:

```markdown
---                              ← Layer 1: Structured (frontmatter)
company: Acme Corp                   Indexed in memory. Queryable.
status: interview                    vault_query(tags=["job-application"])
tags: [job-application]              vault_update(path, {"status": "offer"})
---

## Timeline                      ← Layer 2: Semi-structured (sections)
- 2026-04-02 — interview            Section names indexed. Content on disk.
- 2026-03-24 — phone_screen          vault_read_section(path, "Timeline")
                                     vault_append_section(path, "Timeline", text)

## Notes                         ← Layer 3: Unstructured (prose)
Panel was friendly. Shanika          Not indexed. Read on demand.
asked about Python...                vault_read_body(path) → pass to LLM
```

### VaultIndex

Platform service (`emptyos/runtime/vault_index.py`). Boots with the kernel.

- Full scan on startup: ~800ms for 3000+ files, ~1MB memory
- Incremental updates: `vault:changed` events from VaultWatcher
- In-memory Python dicts — no SQLite, no persistent cache
- Restart = rescan (like Obsidian)

Apps never scan vault files directly. They use BaseApp convenience methods:

```python
self.vault_query(tags=["job-application"], status="interview")
self.vault_update(path, {"status": "offer"})
self.vault_read_section(path, "Notes")
self.vault_append_section(path, "Timeline", "- 2026-04-09 — offer")
self.vault_create_note(path, frontmatter, body)
self.vault_sections(path)       # from index, instant
self.vault_get_properties(path) # from index, instant
self.vault_read_body(path)      # from disk
```

---

## Apps

### All Apps Are Equal

Every app has:
- `manifest.toml` — declares identity, dependencies, what it provides
- `app.py` — implementation (BaseApp subclass)
- Optional: `pages/` — web frontend

The app loader treats all apps identically. A 10-line capture app and a 5000-line fiction engine go through the same discovery, loading, and lifecycle.

### App Manifest

```toml
[app]
id = "cable-rating"
name = "Cable Rating"
version = "2.0.0"
description = "IEC 60287 current rating calculator"

[app.entry]
module = "app"
class = "CableRatingApp"

[requires]
capabilities = ["read", "write", "think"]
services = ["compute"]
connectors = []
engines = ["engineering"]
libraries = ["frontend"]

[provides.cli]
commands = ["cable-rating"]

[provides.web]
prefix = "/cable-rating"

[provides.events]
emits = ["cable-rating:calculated"]

[provides.settings]
schema = [
    {key = "cable.default_friction", label = "Default Friction", type = "number", default = 0.5},
]
```

### Decentralized Settings

Apps own their settings. Each app declares what it needs in `[provides.settings].schema`:

```toml
[provides.settings]
schema = [
    {key = "expense.budget", label = "Monthly Budget ($)", type = "number", default = 3000},
    {key = "expense.alert_threshold", label = "Alert at %", type = "number", default = 80},
]
```

The settings app collects all schemas at runtime from every manifest. When a new app is added, its settings appear on the settings page automatically — no central registry to update.

Available types: `select` (with options), `number`, `text`, `toggle` (boolean).

### App Addons

Settings configure an app; **addons extend it**. An addon slot is a place where an app renders user-supplied entries — typically URL templates for external sites — without hardcoding them. The app owns the slot; the entries are data in `emptyos.toml`.

```toml
[[apps.dictionary.word_addons]]
id = "speech-examples"
label = "Hear in real speech"
icon = "🎧"
url_template = "https://example.com/pronounce/{word}/english"
```

Dictionary defines the slot (how/where `word_addons` render); it knows nothing about which external sites the user has plugged in. `apps/` stays generic; per-machine integrations live in user config. Graduation paths (SDK helper, manifest `[contributes]` for addons that need logic) are described in `.claude/rules/addons.md`. Reference implementation: `apps/dictionary/` `word_addons` slot.

### App Capabilities

Apps call capabilities through `self.*` — never through direct imports:

```python
class BriefingApp(BaseApp):
    async def generate(self):
        tasks = await self.read("tasks.md")
        summary = await self.think(f"Prioritize:\n{tasks}")
        await self.write("briefing.md", summary)
        await self.emit("briefing:generated", {"date": today})
```

For streaming:
```python
async for chunk in self.think_stream("Summarize my day"):
    yield chunk  # {"text": "...", "done": false}
```

### App Communication

Apps communicate through the event bus, never through direct imports:

```python
# App A emits
await self.emit("task:completed", {"text": "Fix bug"})

# App B listens
@on_event("task:completed")
async def handle(self, event):
    await self.write("log.md", f"Done: {event.data['text']}")
```

### App Lifecycle

```
discovered -> loaded -> started -> stopped
                |
              error
```

### App Data

| Need | Use |
|---|---|
| Vault files | `self.read()`, `self.write()` |
| App-local JSON/SQLite | `self.data_dir` (in `data/apps/<id>/`) |
| Persistent key-value | `self.load_state()`, `self.save_state()` |

---

## Platform Runtime

### Services

Always-running system processes. Apps don't call them directly — they're invisible infrastructure.

| Service | What | Status |
|---|---|---|
| **Vault watcher** | File changes -> EventBus events | TODO |
| **Scheduler** | Cron jobs via APScheduler | TODO |
| **Real-time** | WebSocket server, EventBus -> browser push | TODO |
| **Compute workers** | Job queue, GPU coordination, background tasks | TODO |
| **Notifications** | Push via telegram connector | TODO |
| **Analytics** | Usage tracking, LLM billing | TODO |

### Libraries

Shared code with no lifecycle. Apps import/use them.

| Library | What | Status |
|---|---|---|
| **Frontend runtime** | Themes, components, nav, page shell, app launcher | TODO |
| **Media pipeline** | Audio record/transcribe/TTS, image generate/convert, video assemble | TODO |

### Engines

Stateful computation domains. Apps invoke them for heavy/specialized work.

| Engine | Domain | Apps That Use It |
|---|---|---|
| **engineering** | IEC 60287 cable rating, sheath voltage, EMT | cable-rating, sheath-voltage |
| **physics** | Catenary sag, thermal expansion, mechanical loading | digital-twin |
| **geospatial** | Coordinate transforms, spatial indexing, GIS | geodemo, places |
| **game** | State machines, NPC AI, turn-based logic | isla-friends |

Engines live in `engines/` folder. Auto-discovered like apps and plugins.

### Connectors (Plugins)

Adapters to external hardware and APIs. Apps access them via `self.require("name")`.

| Connector | Connects To | Type | Tags |
|---|---|---|---|
| `ollama` | Local LLM (localhost:11434) | enhancer (think) | llm, local |
| `comfyui` | GPU image generation (localhost:8188) | enhancer (draw) | gpu, image |
| `voice-api` | TTS/STT service (localhost:8601) | enhancer (speak+listen) | audio |
| `health` | System watchdog | service | system, monitoring |
| `notifications` | Vault file + Telegram push | service | system, messaging |
| `telegram` | Telegram Bot API (two-way) | service | messaging, mobile |
| `applio` | AI voice conversion (localhost:6969) | service | audio, voice |

Connectors live in `plugins/` folder. Auto-discovered, loaded before apps.

### Plugin Types

Plugins serve two distinct roles:

| Type | Purpose | Example | Access Pattern |
|---|---|---|---|
| **Service Plugin** | Provides a named service | `notifications`, `telegram` | `self.require("notifications")` |
| **Enhancer Plugin** | Augments an existing capability | `comfyui`, `voice-api`, `ollama` | Transparent — apps call the capability, unaware of the provider |

Both types are optional and degrade gracefully. The difference is how apps interact with them.

### Graceful Enhancement Pattern

Enhancer plugins dynamically inject providers into capabilities at startup. If the plugin is absent or its service is offline, the system uses the next provider in the chain. **No app code changes are needed.**

```
┌─────────────────────────────────────────────────────────────┐
│  1. Plugin.connect() checks available()                      │
│  2. If available → inject Provider at priority=0 (try first) │
│  3. If not → silently skip, system works without it          │
│  4. Provider.available() re-checks at every call             │
│  5. If provider fails → capability tries next in chain       │
│  6. Health plugin monitors all providers automatically        │
└─────────────────────────────────────────────────────────────┘
```

**Implementation pattern** (from `plugins/comfyui/plugin.py`):

```python
async def connect(self):
    if await self.available():
        from emptyos.capabilities import Provider
        plugin = self

        class MyProvider(Provider):
            name = "my-enhancer"
            async def available(self) -> bool:
                return await plugin.available()
            async def execute(self, **kwargs) -> Any:
                return await plugin.do_work(**kwargs)

        cap = self.kernel.capabilities.get("target_capability")
        cap.add_provider(MyProvider(), priority=0)  # priority=0 = tried first
```

**Current enhancer plugins:**

| Plugin | Enhances | What It Adds |
|---|---|---|
| `comfyui` | `draw` | GPU image generation (FLUX, SDXL) |
| `voice-api` | `speak` + `listen` | TTS (F5-TTS) + STT (Whisper) |
| `ollama` | `think` | Local LLM inference |

**Dual-role plugins** can be both service AND enhancer — a plugin may inject a capability provider *and* expose named service methods. Add one when a vendor-specific feature is worth explicit opt-in; see `plugins/voice-api/` for the canonical shape.

---

## Capability System

### The 4 Core Capabilities

| Capability | Human mode | Tool mode |
|---|---|---|
| **think** | Human types answer | LLM generates |
| **read** | Human pastes content | Filesystem reads |
| **write** | Human saves to editor | Filesystem writes |
| **search** | Human browses and remembers | grep/semantic search |

### Provider Chain

Each capability tries providers in order. Human is always last:

```
think:  ollama -> openai -> claude-cli -> human
read:   filesystem -> human
write:  filesystem -> human
search: grep -> human
```

### Streaming

```python
# Non-streaming (returns string)
result = await self.think("What should I do today?")

# Streaming (yields chunks)
async for chunk in self.think_stream("Summarize my week"):
    # {"text": "First,", "done": false}
    # {"text": " you completed", "done": false}
    # {"text": "", "done": true}
```

---

## Project Structure

```
emptyos/
├── emptyos/
│   ├── kernel/          # Config, EventBus, ServiceRegistry, loaders
│   ├── capabilities/    # think, read, write, search
│   │   └── providers/   # human, filesystem, grep, openai-compat
│   ├── sdk/             # BaseApp, BasePlugin, decorators
│   ├── cli/             # eos command (Typer)
│   ├── web/             # FastAPI server + dashboard
│   └── runtime/         # Platform services (vault watcher, scheduler, etc.)
├── apps/                # Core apps (git-tracked, shipped with OS)
│   ├── capture/
│   ├── note/
│   ├── task/
│   ├── search/
│   ├── assistant/
│   └── ...              # ~25 core apps
├── apps/personal/       # User apps (gitignored, local only)
│   ├── cable/
│   ├── jobs/
│   ├── healing/
│   └── ...              # Your domain-specific apps
├── plugins/             # All connectors (auto-discovered)
├── engines/             # Core engines (git-tracked)
├── engines/personal/    # User engines (gitignored)
├── data/                # Runtime state (events.db, app state, caches)
├── emptyos.toml         # Machine-specific config
└── pyproject.toml       # Package config
```

### Three-Tier Distribution Model

EmptyOS has three distribution tiers. All are equal at runtime — the tiers only affect what ships in the git repo.

```
┌──────────────────────────────────────────────────────────────┐
│  Core (git-tracked, shipped with every install)              │
│                                                              │
│  emptyos/           Kernel, SDK, Web, Runtime, CLI           │
│  apps/              ~25 generic apps (capture, task, search) │
│  plugins/           9 connectors (ollama, comfyui, etc.)     │
│  docs/              DESIGN.md, system documentation          │
│  .claude/skills/    System skills (grow, connect, devlog)    │
│  .claude/rules/     Development rules                        │
│  tests/             E2E test suite (202 tests)               │
├──────────────────────────────────────────────────────────────┤
│  Community (git-tracked, domain-specific but shareable)      │
│                                                              │
│  apps/              Domain apps that could be useful to      │
│                     others with similar needs — finance,     │
│                     engineering, learning, creative tools.   │
│                     Currently merged with core; future:      │
│                     separate repos or app marketplace.       │
│  engines/           Domain engines (engineering, finance)     │
├──────────────────────────────────────────────────────────────┤
│  Personal (gitignored, local only)                           │
│                                                              │
│  apps/personal/     Your domain apps (41 apps)               │
│  engines/personal/  Your domain engines                      │
│  emptyos.toml       Your machine config                      │
│  data/              Runtime state (SQLite, JSON, traces)     │
│  vault (external)   Your markdown files                      │
└──────────────────────────────────────────────────────────────┘
```

**How the tiers work:**

| Tier | Location | gitignored? | Who benefits | Examples |
|------|----------|-------------|-------------|----------|
| **Core** | `emptyos/`, `apps/` (infra/system) | No | Everyone | kernel, capture, task, search, reactor, settings |
| **Community** | `apps/` (growth/creative) | No | Anyone with a vault | journal, expense, dashboard, music-studio, reader |
| **Personal** | `apps/personal/`, `engines/personal/` | Yes | Only you | healing, jobs, cable, staff, integrity |

- Loaders scan `apps/` + `apps/personal/` — all discovered apps are equal at runtime
- A fresh `git clone` gives Core + Community (~25 apps). Personal apps are local only.
- ID collision: personal overrides core/community (loader logs warning)
- The vault is always external — user data is never in the repo

**Lifecycle between tiers:**
```
Personal → Community → Core
  ↑ grows locally      ↑ proven useful to others      ↑ becomes infrastructure
  │                    │                               │
  └── app-gen creates  └── extract personal info,      └── extract to SDK,
      new apps             generalize config               becomes platform service
```

Apps naturally migrate upward: a personal experiment becomes a community app when generalized, and a community app becomes core infrastructure when the platform depends on it. The reverse also happens — core apps get retired when superseded.

---

## Why EmptyOS

### What Already Exists

Products in adjacent spaces:

| Product | Overlap | What's Different |
|---|---|---|
| Markdown vault + plugins | Vault as storage, note-centric | EmptyOS treats the vault as an OS filesystem, not a note-taking tool |
| All-in-one workspaces (Notion, Anytype) | Personal OS feel, app-like blocks | Closed platforms — EmptyOS is code you own, running locally |
| Self-hosted automation (Home Assistant, n8n) | Plugin architecture, event bus | Automation-only — no knowledge layer, no consciousness model |
| AI knowledge bases (Khoj, Quivr) | RAG search, LLM + vault | Search-only — no apps, no capabilities, no growth loop |
| AI coding agents (Aider, Cursor) | AI that modifies codebases | Tools for building software — EmptyOS IS the software that gets built |

### What's Unique

**1. The vault IS the OS filesystem.** Not "AI searches your notes." The vault is the actual storage layer. Apps read and write vault notes as their primary data format. Delete the OS, your data survives as readable markdown. No other system treats a human-readable folder as its real disk.

**2. Capabilities, not tools.** The 7-verb abstraction (think/read/write/search/speak/listen/draw) with provider fallback chains. Apps call `self.think()` and the system routes it. The human is always the final provider. This decouples every app from every tool.

**3. One life, one codebase.** 73 apps spanning career, health, finance, creativity, learning, meditation — unified under one event bus, one topology graph, one vault. The reactor ripples a single capture across journal, projects, tasks. The connections between apps ARE the architecture of a life.

**4. The consciousness model (唯识).** Not branding. The Six Verbs lifecycle (Absorb → Grow → Root → Connect → Emerge → Reflect) with the vault as ālaya-vijñāna drives real architectural decisions. The integrity audit scores the system against its own philosophy. No competitor has a self-awareness framework.

**5. Self-evolving via conversation.** The three runtime modes — especially conversation mode — mean the system grows by being understood. `CLAUDE.md` is DNA. Each AI session that reads it and extends the system makes the next session more capable. The growth compounds.

### The Meaning

EmptyOS is not a product for millions of users. It is the answer to: **what if you could code your entire life into a system that understands itself?**

Any single feature has a competitor. The uniqueness is integration density — 73 apps sharing 7 capabilities over a markdown vault with a Buddhist consciousness model, reactive event chains, and self-auditing agents. That specific combination exists nowhere else.

The risk is that it stays personal. The opportunity is that the **patterns** (capability abstraction, vault-as-filesystem, reactive population, conversation-as-runtime) could become a framework others build their own OS on. The system is empty — you fill it.

---

## Deployment Model

EmptyOS is **local-first by default, cloud-deployable by design.** Both run the same codebase — deployment is a config choice, not an architectural one.

### Three Network Modes

The `network.mode` config picks the trust level of the network EmptyOS is accessible on. Each mode has sane defaults; raw `host` / `auth_token` still work as overrides.

| Mode | Accessible from | Binds | Auth | When |
|---|---|---|---|---|
| **`local`** (default) | Only this machine | `127.0.0.1` | none | Single-machine personal use |
| **`private`** | Your own trusted network | `0.0.0.0` | none | Tailscale, WireGuard, LAN, VPN — the network layer is the gate |
| **`public`** | Public internet | `0.0.0.0` | **required** (bearer token) | VPS, Docker, Fly.io, Cloud Run |

EmptyOS refuses to start in `public` mode without an `auth_token` set. It warns (but allows) binding `0.0.0.0` in `private` mode without auth — that's intentional for Tailscale/LAN use where the network itself is trusted.

### Demo Mode (Orthogonal to Network Mode)

Demo mode is a separate `[demo]` config flag, not a network mode. It layers on top of any network mode to produce the demo UX:

- Shows a demo banner on the home screen
- Disables GPU-heavy capabilities (draw, speak, listen) — shows "install locally" hints
- Enables **BYOK** (Bring Your Own Key) in settings — users paste their own OpenAI/Anthropic key, session-scoped, never persisted
- Optionally resets the vault on restart (`demo.reset_on_restart = true`)

A public demo is `network.mode = "public"` + `demo.enabled = true`. You can also enable demo locally to test the demo UX.

### The Single-Tenant Invariant

**Each instance is single-tenant.** There is no multi-user EmptyOS. `public` mode means "your instance, accessible to you from anywhere" — not "many users share one instance." A hosted demo is still one instance with one vault, publicly visible. Scaling happens by running more instances, not by sharing one.

### Remote Access for Personal Use

You don't need to expose your local EmptyOS to the public internet just to use it from your phone. Two paths without code changes:

- **Private network** — set `network.mode = "private"`, join your devices to Tailscale/WireGuard/any mesh VPN, access `http://your-machine:9000` from anywhere on the tailnet
- **Publish app** — already deploys your own content (blog, project site) to GitHub Pages / Firebase without exposing EmptyOS itself

### What Cloud Never Touches

- **Your vault never syncs to the cloud.** The vault stays on the machine where EmptyOS runs. If you self-host, the vault lives on your VPS disk.
- **Cloud AI providers (OpenAI, Anthropic, etc.) receive prompts, not raw vault content.** If an app wants to send vault content to a cloud model, it must be explicit per-request.
- **No telemetry phones home.** EmptyOS never calls out to an Anthropic/OpenAI-of-EmptyOS server. There is no such server.

---

## Cloud Provider Consent

Every capability (think, draw, speak, etc.) can have local and cloud providers in its fallback chain. When the chain reaches a cloud provider, the system enforces a **consent gate** before any data leaves the machine.

### Three Consent Modes

```toml
[cloud]
consent = "ask"     # "ask" (default) | "always" | "never"
```

- **`"ask"`** — on the first cloud call per provider per session, prompt the user. The prompt shows which provider will be used and a summary of the data being sent. Approvals can be remembered for the session.
- **`"always"`** — trust the provider chain; never prompt. Use when you've explicitly configured cloud providers and don't want interruption.
- **`"never"`** — skip cloud providers entirely; fall through to the next local provider or human.

### How the Gate Works

1. A capability invocation walks the provider chain.
2. For each provider, the system checks `is_cloud` — auto-detected from the host URL. Localhost, 127.0.0.1, and private IPs (10.x, 172.16.x, 192.168.x) are local. Everything else is cloud.
3. Before calling a cloud provider, the consent policy decides: proceed, skip, or prompt.
4. In daemon mode, prompts surface as modals in the browser via WebSocket. In CLI mode, they prompt on stdin.
5. Once approved, the session cache lets subsequent calls to the same provider proceed silently.

### Provider Escalation Indicator

When a request uses a cloud provider, the UI shows a subtle badge indicating which provider handled it. This is informational — the consent gate handles approval; the badge shows what happened. Users always know when their data went to the cloud.

### Tie-in to the Consciousness Model

In the Yogācāra framing, the vault (ālaya) is the storehouse — local, sovereign, persistent. Cloud providers are **remote sense organs** (远感官): an eye in Seoul, an ear in Virginia. They perceive and return perceptions, but the storehouse is always where the user trusts it. The consent gate is the boundary that keeps ālaya inviolate.

---

## Philosophy

### Atomic Code, Like Atomic Notes

Obsidian's insight: the value of a note is not the note itself — it's the links between notes. `[[wikilinks]]` turn isolated text into a knowledge graph. Each note is a small, self-contained atom. The connections are what matter.

EmptyOS applies the same principle to code. Each app is an atom — a folder with `manifest.toml` and `app.py`. Small, self-contained, human-readable. The value is not in any single app. It's in how they connect:

- `capture` writes to vault → vault watcher fires `vault:changed` → `briefing` reacts
- `dashboard` calls `task`, `expense`, `journal`, `contacts`, `briefing` via `call_app()`
- `model-bench` uses `think_compare()` which calls all providers built by different plugins
- Events cascade across apps. Capabilities chain across providers.

The topology graph IS the knowledge graph — for code instead of notes.

And both share the same hard drive. The vault holds knowledge atoms (markdown) and the output of code atoms (apps). They interweave: a journal entry is both a note Obsidian can render and data the journal app can parse. A task is both a checkbox in markdown and a structured object the task app returns.

### Everything Can Be Generated

Two layers of generation:

**Runtime**: `eos new-app "description"` — the system knows its own capabilities and generates simple apps on the fly.

**Development**: A coding agent (Claude Code, Cursor, any AI IDE) reads `CLAUDE.md` + the full codebase and generates complex apps with full architectural understanding. This is how 33 apps were built in one session.

`CLAUDE.md` is not just documentation — it is the system's **DNA**. Its quality directly determines how well a coding agent can extend EmptyOS. Every architectural decision, every capability, every manifest format documented in `CLAUDE.md` becomes context for the next generation cycle:

```
Codebase + CLAUDE.md → Coding Agent → New apps → Updated CLAUDE.md → Better generation
```

The system grows by being well-documented enough that any coding agent can extend it.

The 55 Home Portal apps are the first garden. The soil grows anything.

### Everything Is Reusable

Nothing is single-purpose. An engine built for cable-rating is available to sheath-voltage. A media pipeline built for podcast serves talkbuddy. A frontend runtime built for dashboard works for fiction-engine. Every service, library, engine, and connector is a shared resource that any app can declare as a dependency.

If you build something for one app that another app could use, extract it into the platform. The platform is the accumulation of everything reusable.

### Everything Is Connected

Apps are not isolated silos. They share capabilities, services, engines. The event bus connects them — when one app writes, another can react. When a file changes in the vault, any app can listen. The topology graph is not just a visualization — it IS the living architecture, showing how every component depends on and feeds into every other.

The system is an organism, not a collection.

---

## Consciousness Model — 唯识架构

EmptyOS borrows its deepest structure from Yogācāra (唯识学), the Buddhist theory of consciousness. This is not metaphor — it is a design framework that determines where logic lives, how data flows, and what the system is evolving toward.

### Eight Consciousnesses — The Architecture

```
┌─────────────────────────────────────────────────────────┐
│  第八识 · 阿赖耶识 (Ālaya) — The Vault                  │
│                                                         │
│  The storehouse of all seeds. Does not judge, does not  │
│  process — only stores. Every frontmatter field, every  │
│  paragraph, every wikilink is a seed (種子/bīja).       │
│  After system reset, everything rebuilds from here.     │
│  The vault is external, swappable, human-readable —     │
│  like consciousness persisting across lifetimes.        │
│                                                         │
│  Implementation: vault (markdown), VaultIndex (memory)  │
├─────────────────────────────────────────────────────────┤
│  第七識 · 末那識 (Manas) — Self-Awareness Layer          │
│                                                         │
│  The consciousness that constantly asks: who am I?      │
│  what am I missing? am I healthy? It grasps at "self"   │
│  — the system's relentless drive to maintain identity    │
│  and completeness.                                      │
│                                                         │
│  Implementation: integrity audit (9 dimensions),        │
│  Growth Agent (daily cron), health watchdog,             │
│  scan_uncovered() (vault gap detection),                 │
│  unmet dependency tracking                              │
├─────────────────────────────────────────────────────────┤
│  第六識 · 意識 (Mano-vijñāna) — The Thinking Layer       │
│                                                         │
│  The only layer that reasons, compares, and decides.    │
│  Receives raw data from the five senses, makes meaning. │
│  All judgment lives here — never in the sense layer,    │
│  never in the storehouse.                               │
│                                                         │
│  Implementation: think capability, LLM routing,         │
│  domain-based dispatch (text/code/reason),              │
│  think_compare(), staff agents                          │
├─────────────────────────────────────────────────────────┤
│  前五識 · Five Senses — The Capability Layer             │
│                                                         │
│  Pure perception and action, no judgment.               │
│  read() reads a file — does not interpret it.           │
│  search() finds matches — does not rank meaning.        │
│  write() writes bytes — does not evaluate content.      │
│                                                         │
│  眼 (eye)  = read     seeing files                      │
│  耳 (ear)  = listen   hearing audio                     │
│  舌 (tongue)= speak   producing speech                  │
│  身 (body) = write    acting on the world               │
│  鼻 (nose) = search   sensing/probing the environment   │
│  draw = 意識 directing the senses to create             │
│                                                         │
│  Implementation: 7 capabilities with provider chains    │
└─────────────────────────────────────────────────────────┘
```

**Design rule derived from this model:** The five sense capabilities must never contain judgment logic. `read()` returns bytes, `search()` returns matches, `write()` persists data. All interpretation, ranking, and decision-making belongs in `think()`. If you find analysis logic in a read or search provider, it is in the wrong layer.

### The Core Cycle: Seed → Manifestation → Perfuming

唯识学's most important mechanism is a three-phase cycle that maps exactly to EmptyOS data flow:

```
  種子 (bīja)              現行 (pravṛtti)           熏習 (vāsanā)
  seeds dormant           activated into            actions perfume
  in the vault            behavior                  back into vault
       │                       │                         │
       │   app reads vault     │    user acts / system   │
       └──────────────────────►│    reacts / LLM thinks  │
                               └────────────────────────►│
                                                         │
       ┌─────────────────────────────────────────────────┘
       │   new seeds stored: journal entry, task update,
       │   frontmatter change, new note created
       ▼
  種子 (new seeds in vault, ready to manifest next time)
```

| Phase | 唯识 | EmptyOS | Example |
|---|---|---|---|
| **Seed** | 種子 (bīja) | Vault note sitting on disk | `50_Journal/2026-04-11.md` with mood data |
| **Manifestation** | 現行 (pravṛtti) | App reads seed, produces UI/insight | Dashboard reads journal → LLM generates narrative |
| **Perfuming** | 熏習 (vāsanā) | Action writes new seeds back to vault | Capture → reactor → journal entry → new seed |

This cycle never stops. Every user action perfumes the vault with new seeds. Every app activation manifests dormant seeds into visible behavior. The vault grows richer with each cycle — not because someone organizes it, but because the system's metabolism continuously deposits meaning.

**Reactive vault population IS perfuming.** When a user logs a healing mood, the event bus ripples that action into the daily journal, the healing trend, the integrity score. One action deposits seeds in multiple locations. The system's event chains are its perfuming pathways.

### Six Verbs — The Lifecycle

The system's evolution follows six movements, each mapped to a Buddhist concept:

| Verb | 中文 | Buddhist Concept | Mechanism |
|---|---|---|---|
| **Absorb** | 吸收 | 熏習 · new seeds enter the storehouse | HP absorption, vault writes, knowledge import |
| **Grow** | 生長 | 種子現行 · potential becomes actual | New apps, new capabilities, layer by layer |
| **Root** | 扎根 | 轉識成智 · coarse awareness becomes refined wisdom | Refactor, merge, extract to SDK/platform |
| **Connect** | 連接 | 因緣和合 · dependent origination | EventBus, cross-app calls, topology edges |
| **Emerge** | 涌現 | 末那識 · self-awareness of gaps | Vault emergence, Growth Agent, gap detection |
| **Reflect** | 反省 | 末那識 · immune self-examination | Integrity audit, health check, self-repair |

**Ripple is the runtime face of Connect** — what happens when an event activates a chain of connections. The Six Verbs describe the system's structural lifecycle; ripple is the dynamic that makes one user action (a capture, a git commit, a journal entry) propagate through the reactor into related notes, dashboards, and downstream apps. When [GETTING-STARTED.md](GETTING-STARTED.md#the-system-is-alive) introduces "Ripple" as a signature motion, that's Connect in motion.

These form a metabolic cycle:

```
Absorb → Grow → Connect → Emerge (discover new needs)
                              ↓
              Reflect ← Root (consolidate)
                 ↓
              Absorb (next cycle)
```

The system is never "done." Like a living organism, it metabolizes continuously — absorbing new input, growing new organs, connecting them, discovering what is still missing, consolidating what works, checking its own health, then absorbing again.

### Four Wisdoms — The Evolution Target

唯识学's ultimate goal is 轉識成智 — transforming the eight consciousnesses into four wisdoms. This maps to EmptyOS's maturity trajectory:

| Transformation | From → To | EmptyOS Stage | What Changes |
|---|---|---|---|
| 前五識 → **成所作智** (Accomplishing Wisdom) | Senses → skillful action | Capabilities become self-adaptive | Provider fallback, domain routing, graceful enhancement — capabilities that find the right tool automatically |
| 第六識 → **妙觀察智** (Observing Wisdom) | Thinking → subtle insight | Think becomes context-aware | Vault context in every prompt, RAG, staff agents with specialized knowledge — not just answering, but understanding |
| 第七識 → **平等性智** (Equality Wisdom) | Self-grasping → unbiased self-knowledge | Reflection becomes genuine | Integrity audit evolves from scoring to true architectural understanding — knowing where to grow, not just what's broken |
| 第八識 → **大圓鏡智** (Mirror Wisdom) | Storehouse → perfect reflection | Vault becomes a complete mirror | The vault faithfully reflects the user's full cognition, relationships, goals, and memory — adding no bias, missing nothing, distorting nothing |

**大圓鏡智 (Great Mirror Wisdom)** is the terminal state. The vault is no longer just a hard drive — it is a mirror that reflects the user's entire life with perfect fidelity. The system does not impose structure the user didn't intend. It does not lose what the user valued. It does not add noise. It simply reflects.

This is the deepest design constraint: **every feature must move the vault closer to being a faithful mirror, never further away.**

---

## Self-Awareness System — 13 Integrity Dimensions

The system continuously audits itself across 13 dimensions. The integrity app (`apps/personal/integrity/`) runs these checks on demand — fast filesystem reads, no LLM calls.

### Dimensions

| # | Dimension | What It Checks | Score |
|---|-----------|---------------|-------|
| P1 | **Generatable** | app-gen, auto_ui, templates exist | 0-10 |
| P2 | **Reusable** | SDK modules, shared CSS classes, JS helpers | 0-10 |
| P3 | **Connected** | Orphan apps, unheard events, connection density | 0-10 |
| P4 | **Atomic** | Monolith detection (>1200L), avg app size | 0-10 |
| P5 | **Self-Testing** | Human fallback provider, health plugin | 0-10 |
| P6 | **Expressive** | Custom UI percentage across apps | 0-10 |
| P7 | **Self-Documenting** | App description coverage in manifests | 0-10 |
| P8 | **Vault External** | Hardcoded PARA paths vs vault_config() | 0-10 |
| P9 | **Reactive Vault** | Apps that ripple events to vault notes | 0-10 |
| P10 | **Six Verbs** | Metabolic cycle health (see below) | 0-10 |
| P11 | **Security** | Hardcoded secrets, eval/exec, SQL injection, shell injection | 0-10 |
| P12 | **Privacy** | Personal data patterns (.eos-personal) in git-tracked code | 0-10 |
| P13 | **Scale** | Codebase vital signs (LOC, apps, endpoints) — always 10 | 10 |

### Consumers

| Consumer | How It Uses Integrity Data | Cadence |
|----------|---------------------------|---------|
| **Growth Agent** (staff) | Observes full audit, creates tasks for violations | Daily 6am |
| **Root Agent** (staff) | Observes P3/P4/P10, suggests decomposition/pruning | Weekly Sunday |
| **Connect Agent** (staff) | Observes P3, prunes dead events | Weekly Wednesday |
| **Topology API** | `/api/topology/improvements` includes verb health + violations | On demand |
| **CLI** | `eos integrity` prints scorecard | On demand |
| **Reactor** | `integrity:audit_completed` event → journal ripple for weak verbs | On audit |

### Access

```bash
# Full audit (13 dimensions)
curl http://localhost:9000/integrity/api/audit

# Six verbs only
curl http://localhost:9000/integrity/api/verbs

# CLI
eos integrity
```

---

## Mechanism Layers — What Drives Each Verb

Each of the six lifecycle verbs is backed by multiple mechanism layers. The integrity P10 check scores this:

```
              skill   app     agent      scheduled   events    api
             ──────  ──────  ─────────  ──────────  ────────  ──────
  Absorb      eos-    capture  inbox-     */30 cron  capture:  /api/
              grow    reader   processor             saved     add
                      plugin-
                      gen

  Grow        eos-    app-gen  growth-    daily 6am  app-gen:  /api/
              grow    plugin-  agent                 created   generate
                      gen

  Root        eos-    integ-   root-      weekly     integ:    /api/
              grow    rity     agent      Sun 8am    audit_    topology/
                                                    completed improvements

  Connect     connect reactor  connect-   weekly     staff:    /api/
              skill            agent      Wed 9am    shift_    topology
                                                    completed

  Emerge      eos-    vault-   growth-    daily 6am  integ:    /api/
              grow    analyti- agent                 audit_    topology/
                      cs                             completed improvements

  Reflect     eos-    integ-   weekly-    multiple   health:   /api/
              opera-  rity     reviewer   crons      heartbeat audit
              tor     health   project-              health:   /api/
                      plugin   auditor               problem:* health
```

### Mechanism Types

| Layer | What It Is | How It Triggers | Where It Lives |
|-------|-----------|-----------------|----------------|
| **Skill** | Claude Code instruction for human-invoked actions | User says "/connect", "/grow", etc. | `.claude/skills/` |
| **App** | Runtime service with API endpoints | HTTP requests, `call_app()` | `apps/`, `plugins/` |
| **Agent** | Autonomous AI with OBSERVE→DECIDE→ACT pipeline | APScheduler cron trigger | `apps/personal/staff/` |
| **Scheduled** | Periodic execution (cron expression) | Time-based (APScheduler) | Agent configs, `@scheduled` |
| **Events** | Reactive wiring (emit → listen → act) | EventBus pub/sub | `apps/reactor/`, `@on_event` |
| **API** | Programmatic access for other components | HTTP GET/POST | FastAPI `@web_route` |

### Staff Agents (Autonomous)

| Agent | Verb | Cron | Observes | Actions |
|-------|------|------|----------|---------|
| Growth Agent 🌱 | Grow + Emerge | `0 6 * * *` (daily 6am) | integrity, app-analytics, reflect | task, system-log, note |
| Root Agent 🌿 | Root | `0 8 * * 0` (Sunday 8am) | integrity | task, system-log |
| Connect Agent 🕸️ | Connect | `0 9 * * 3` (Wednesday 9am) | integrity, reactor | task, system-log |
| Weekly Reviewer 📊 | Reflect | `0 20 * * 5` (Friday 8pm) | english, nutrition, journal, task, focus, healing | system-log, review |
| Project Auditor 📋 | Reflect | `0 18 * * 5` (Friday 6pm) | projects | system-log |
| Task Coordinator ✅ | Reflect | `0 8,20 * * *` (daily 8am/8pm) | task, briefing | task, system-log |
| Finance Watchdog 💰 | Reflect | `0 8 1,15 * *` (1st/15th 8am) | expense, tracker | system-log, task |
| Mood Analyst 🌿 | Reflect | `0 22 * * 0` (Sunday 10pm) | healing, journal, english | system-log, healing |
| Schedule Sync 📅 | Reflect | `0 7,10,14,18 * * *` (4x daily) | briefing | system-log |

All agents follow the same pipeline: `OBSERVE (call_app) → DECIDE (per-agent LLM) → ACT (call_app) → REPORT (trace JSON)`. Anti-concurrent protection prevents overlapping runs. Traces are saved for audit in `data/apps/staff/traces/`.

---

## Design Principles

1. **Empty = Infinite** — minimal core, everything else grows from it
2. **Everything can be generated** — apps, UIs, configs, pipelines — the platform knows enough to generate them
3. **Everything is reusable** — nothing built for one app stays locked to one app
4. **Everything is connected** — event bus, shared services, topology graph
5. **Everything is an app** — no excluded services, no second-class citizens
6. **Self-testing, self-fixing** — health checks run continuously, failures fall back automatically
7. **UI grows from structure** — every API endpoint implies a UI; every manifest implies a page; the home screen is a dashboard, not an app list
8. **Human-first** — every capability has a human fallback
9. **Tool-agnostic** — not locked to any LLM, note system, or search engine. User-facing text (UIs, prompts, error messages) must not mention specific third-party products. Enforced by `scripts/check-branding.py` + `.eos-branding` patterns
10. **Portable** — one config file adapts to any machine
11. **Vault-native** — the vault IS the user-visible storage
12. **Apps declare, platform provides** — manifest says what's needed, runtime delivers
13. **Events over imports** — apps communicate via bus, not coupling
14. **Apps own their settings** — each app declares settings in its manifest; the settings page collects them at runtime

---

## UI Philosophy

### The UI Is Not an App List

**Wrong**: Home screen = grid of 57 app cards → click → API documentation
**Right**: Home screen = dashboard with live data → quick actions → flow into app UIs

The UI has three layers:

```
┌─────────────────────────────────────────────┐
│  Hub (home screen)                           │
│  Health score, what-now, weather, tasks,     │
│  quick actions, countdowns                   │
├─────────────────────────────────────────────┤
│  App UIs (per-app interactive pages)         │
│  Expense: log + history + charts             │
│  Speaking: start session + live conversation │
│  Journal: write + browse + heatmap           │
├─────────────────────────────────────────────┤
│  Shared Frontend (theme, nav, components)    │
│  Theme CSS, page shell, toast, streaming,    │
│  forms, cards, charts, modals                │
└─────────────────────────────────────────────┘
```

### UI Grows From Structure

The UI is not designed separately — it emerges from what the system already knows:

| System Structure | → UI That Grows From It |
|---|---|
| `POST /api/expense/log` takes `{text}` | → Text input + submit button |
| `GET /api/briefing/health-score` returns 5 dimensions | → Ring chart on hub dashboard |
| `[provides.events] emits = ["task:completed"]` | → Real-time counter on any screen |
| `[requires] connectors = ["voice_api"]` | → Microphone button wherever voice is relevant |
| Auto-clustering: Voice & English cluster | → "English Practice" screen combining speaking + shadowing + voice-review |
| Event flow: healing ← mood events | → Wellness screen: mood + nutrition + meditation together |

### UI Is Emergent, Not Designed

The UI is never manually laid out. It **emerges** from three things the system already knows:

1. **Graph clusters** → define what screens exist
2. **App manifests** → define what widgets each app contributes
3. **API endpoints** → define what each widget can do

No hardcoded screens. No manual widget placement. The system has enough information to generate the entire UI from its own structure.

```
Graph clusters → Screens
App endpoints  → Widgets  
Endpoint types → Widget types (form, list, chart, value, stream)
Hub app (most connected) → Home screen
```

**How screens emerge:**

The auto-clustering algorithm already groups apps by connection strength. Each cluster IS a screen:

| Cluster (from graph) | Emergent Screen | Why they're together |
|---|---|---|
| 📊 Life (12 apps) | Life Dashboard | call_app edges + shared capabilities |
| 🎤 Voice & English (10) | English Practice | shared voice_api connector + event flows |
| 🧘 Wellness (5) | Wellness | semantic + shared healing events |
| 🧠 Knowledge (5) | Knowledge | shared think capability pattern |
| 🎨 Creative (4) | Creative Studio | shared comfyui connector |
| ⚙️ System (11) | System (admin only) | infrastructure apps |

When a new app is added, it joins a cluster via its manifest edges → it appears on the right screen automatically. When enough apps form a new cluster, a new screen emerges.

**How widgets emerge:**

Each app's API endpoints imply widget types:

| Endpoint Pattern | → Emergent Widget Type |
|---|---|
| `GET /api/stats` returning `{count, total}` | Value card |
| `GET /api/entries` returning `[{...}]` | List/table |
| `GET /api/heatmap` returning `{date: count}` | Heatmap grid |
| `POST /api/log` taking `{text}` | Quick-input form |
| `POST /api/generate` (long-running) | Submit + progress |
| `GET /api/health-score` returning dimensions | Ring/radar chart |
| Event stream via WebSocket | Live counter/feed |

**How the home screen emerges:**

The hub is the most-connected node in the graph. Its widgets are the highest-centrality endpoints across all apps. The home screen IS the graph's center of gravity.

### Three UI Layers

```
┌──────────────────────────────────────────┐
│  Screens (intent-based, multi-app)       │
│  Hub, English Practice, Life Tracker,    │
│  Career Prep, Creative Studio            │
├──────────────────────────────────────────┤
│  Widgets (reusable, from any app)        │
│  Health ring, heatmap, quick-log form,   │
│  streak counter, voice recorder          │
├──────────────────────────────────────────┤
│  Shared Frontend (theme, components)     │
│  theme.css, eos.js, forms, charts,       │
│  toast, streaming, real-time updates     │
└──────────────────────────────────────────┘
```

**Screens** compose **widgets**. Widgets call **app APIs**. The graph determines which widgets appear on which screens.

### Widget = Atomic UI

A widget is the UI atom — like an app is the code atom:

```javascript
// A widget declares what API it needs and renders the result
{
    id: "health-score",
    api: "/hub/api/health-score",
    render: (data) => ringChart(data.score, data.dimensions),
    refresh: 60000  // ms
}

{
    id: "quick-expense",
    api: "/expense/api/log",
    method: "POST",
    render: () => textInput({placeholder: "15 lunch coffee", onSubmit: post}),
}
```

Widgets are composable. The hub screen is just a collection of widgets. The English Practice screen is a different collection. Same widgets can appear on multiple screens.

### Home Screen = Hub Dashboard

The home page (`/`) is NOT an app launcher. It IS the hub — showing:
1. **Health Score** ring (from hub app)
2. **What Now** — one actionable suggestion (from hub)
3. **Quick Actions** — top 4-6 widgets: log expense, write journal, start speaking
4. **Today's Activity** — from event bus
5. **Weather + Countdowns** — ambient context

The app list is accessible via navigation or Ctrl+K command palette, not the home screen.

### Navigation

```
[Hub] [Briefing] [English] [Life] [Career] [Creative] ... [All ▾] [⌘K]
```

- Top nav: intent-based screens (from graph clusters)
- "All" dropdown: full app list, clustered
- Ctrl+K: command palette — jump to any app or screen
- Screens auto-generated from graph clusters

### Mobile-First

All UI must work on iPhone (the primary consumption device):
- `viewport-fit=cover` + `env(safe-area-inset-*)` padding
- Touch targets ≥ 44px
- Collapsible sections for dense content
- Hold-to-talk for voice features

---

## Continuous Behaviors

Beyond serving requests, EmptyOS maintains these behaviors:

- **Health checks** — periodic verification of services, connectors, capabilities, and app routes. Failures trigger provider failover, service restart, or app reload. The system narrows but never collapses.
- **Context awareness** — a service that synthesizes recent events, time, and activity into queryable state. Any app can ask "what's going on?"
- **Chain reactions** — events trigger further events across apps. File arrives in inbox → classified → routed → linked.
- **Scheduling** — apps contribute cron tasks that form daily/weekly patterns. Morning briefing, evening review, weekly planning.
- **Pattern detection** — watchers on the event stream trigger automatic responses. Errors accumulate → alert. Task overdue → notify.
- **Proactive notifications** — the system initiates communication. Does not wait to be asked.
- **Auto-discovery** — drop a folder with a manifest. The system finds it, loads it, connects it.

### Testability

Everything goes through capabilities and the service registry:
- Swap any provider for a test mock
- Inject test events into the event bus
- Verify app behavior by checking emitted events
- Test apps in isolation — they only know about capabilities, not implementations

### `eos health`

```bash
eos health              # Full system check
eos health --watch      # Continuous monitoring
eos health --fix        # Auto-repair what can be fixed
```
