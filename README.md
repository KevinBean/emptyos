# EmptyOS

> **A mind companion. Think and create *with* you, not *for* you.**

[Live demo](https://demo.binbian.net/?token=bLN9bRP-wMfBiaqsfI4OHuu3tGHtGsaeGsLk-8v71N8) · [Project site](https://eos.binbian.net) · [Blog](https://binbian.net)

An AI-powered operating system built on human capabilities. Empty by default, infinite by design.

## What Is This

EmptyOS is a **mind companion** — a system that remembers, thinks, and creates alongside you. Not autopilot, not a note dump: it augments your judgment, it doesn't replace it.

**Runs on your machine or your server.** Local-first by default — your vault stays on your disk, cloud AI is opt-in with explicit consent.

An OS is just a human doing things — reading, writing, thinking, searching. Tools are optional accelerators. EmptyOS starts with **you** as every capability provider: when the system needs to "think", it asks you. When it needs to "read", you read. Add an LLM and it thinks with you. Add a filesystem watcher and it reads with you. The system scales from fully manual to fully automated — your choice.

A markdown vault serves as the **hard drive** — external, swappable, human-readable. Kernel state lives in SQLite/JSON. Apps are atoms: a manifest + a Python file. The value is in the connections between them.

## Quick Start

```bash
# Clone
git clone https://github.com/KevinBean/emptyos.git
cd emptyos

# Install
pip install -e .

# Configure
eos init          # interactive setup — vault path, LLM providers, plugins

# Boot
eos start         # web UI at http://localhost:9000
```

That's it. Open `localhost:9000` and you have a working OS with task management, note taking, search, and a capture inbox — no LLM required.

### Try the Demo

**[Open the live demo →](https://demo.binbian.net/?token=bLN9bRP-wMfBiaqsfI4OHuu3tGHtGsaeGsLk-8v71N8)**

The link includes a one-click sign-in token (it's a public demo — don't put real data in it). If you'd rather paste it manually, the token is `bLN9bRP-wMfBiaqsfI4OHuu3tGHtGsaeGsLk-8v71N8`.

The demo runs on a small VPS with a tiny local LLM (`qwen2.5:1.5b`) — fast enough to prove the system works, not enough for impressive output. Visitors who want gpt-4-class quality paste their own OpenAI key in **Settings → Demo → BYOK** (the key stays in browser storage, never touches the server).

The demo vault is curated sample content (no personal data) and resets daily. Try:

- **Capture / Tasks / Journal** — basic vault writing flows
- **Aura voice-assistant** — talk to it via your browser's mic (Web Speech API → daemon → local LLM → reply via edge-tts)
- **Search** — vault search with optional AI summarization
- **Settings → Demo** — paste an OpenAI key for the full experience

Voice/camera works because the visitor's browser does the capture — the server has no mic or camera attached. See [docs/DEPLOYMENT.md § Public demo](docs/DEPLOYMENT.md) for the full deployment recipe (Hetzner CX22 + Caddy + Cloudflare, ~€4.50/month).

### Add AI

To enable AI-powered features (search summarization, writing assistance, smart routing), configure a think provider in `emptyos.toml`:

```toml
[capabilities.think]
providers = ["ollama"]    # try local LLM first, falls back to asking you

[capabilities.think.ollama]
host = "http://localhost:11434"
model = "llama3.1"
```

Works with Ollama, OpenAI-compatible APIs, or Claude CLI. See [Getting Started](docs/GETTING-STARTED.md) for full setup.

## Architecture

```
                        ┌─────────────────────────────────┐
                        │  Apps                            │
                        │  capture  task  search  journal  │
                        │  assistant  projects  publish    │
                        │  focus  quotes  billing  ...     │
                        ├─────────────────────────────────┤
                        │  Platform Runtime                │
                        │  vault watcher  scheduler        │
                        │  real-time (WebSocket)           │
                        │  compute workers (GPU queue)     │
                        ├─────────────────────────────────┤
                        │  Kernel                          │
                        │  Config  EventBus  7 Capabilities│
                        │  AppLoader  PluginLoader         │
                        │  ServiceRegistry  Providers      │
                        └─────────────────────────────────┘
                        Vault (external) ← mounted via emptyos.toml
```

### 7 Capabilities

| Capability | What | Providers |
|---|---|---|
| **think** | Answer questions, summarize, generate | ollama, openai, claude-cli, human |
| **read** | Read files from vault | filesystem, human |
| **write** | Write files to vault | filesystem, human |
| **search** | Find content in vault | grep, human |
| **speak** | Text to speech | voice-api, human |
| **listen** | Speech to text | voice-api, human |
| **draw** | Generate images/video | comfyui, human |

Every capability has **human** as the final fallback. Without any tools configured, the system asks you. Add providers to automate what you want automated.

### Apps

Apps declare what they need, the platform provides it:

```python
class MyApp(BaseApp):
    async def summarize(self):
        data = await self.read("notes/today.md")       # read capability
        summary = await self.think(f"Summarize: {data}") # think capability
        await self.write("summaries/today.md", summary)   # write capability
        await self.emit("myapp:summarized", {"date": "today"})
```

Apps communicate via the event bus, not imports. One action ripples to all related vault files.

### Two Distribution Tiers

| Tier | Apps | Plugins | What You Get |
|---|---|---|---|
| **Core** | 8 | 2 | Vault basics — capture, note, task, search, link, settings |
| **Standard** | 28 | 9 | Full productivity — journal, assistant, projects, publish, focus, AI tools |

Both tiers ship the full platform (kernel, SDK, capabilities, web server). The difference is which apps and plugins are included.

## Core Apps

| App | What It Does |
|---|---|
| **capture** | Quick inbox — append thoughts, route to projects |
| **note** | Note CRUD, fuzzy search, markdown viewer |
| **task** | Task management with decay tiers and focus scoring |
| **search** | Vault search with AI-powered RAG |
| **link** | Wikilinks, backlinks, orphan detection |
| **settings** | System configuration UI |
| **system-log** | Live event feed |
| **run** | Shell command execution |

## Standard Adds

| App | What It Does |
|---|---|
| **journal** | Daily entries, mood tracking, heatmap |
| **assistant** | AI chat with vault context, 35 slash commands |
| **projects** | Project portfolio, task routing, timeline |
| **publish** | Static site generator — blog or project site |
| **focus** | Pomodoro timer with task suggestions |
| **dictionary** | LLM word lookup and vocabulary building |
| **quotes** | Quote of the day from vault |
| **billing** | LLM cost tracking across providers |
| **app-analytics** | App usage + vault health analytics |
| **reactor** | Event chain reactions across apps |
| + 16 more | model-bench, music-studio, app-gen, plugin-gen, ... |

## Plugins

Plugins extend capabilities or add external services:

| Plugin | What | Required |
|---|---|---|
| **health** | Heartbeat, capability probes, GPU monitoring | Default |
| **notifications** | Push notifications (vault + Telegram) | Default |
| **ollama** | Local LLM via Ollama | Optional |
| **comfyui** | GPU image/video generation | Optional |
| **voice-api** | TTS + STT (F5-TTS, Whisper) | Optional |
| **telegram** | Two-way Telegram bot | Optional |
| **blender** | 3D modeling, headless rendering | Optional |
| **applio** | AI voice conversion | Optional |

## Vault — Your Hard Drive

The vault is any folder of markdown files. EmptyOS mounts it read/write and treats it as the source of truth for all user data. Notes use YAML frontmatter for structured data:

```markdown
---
title: My Project
status: active
tags: [project, work]
---

## Tasks
- [ ] Ship v1.0
- [x] Write README

## Notes
This project started because...
```

Apps query vault notes by tags and frontmatter properties. No database — the markdown files ARE the schema. Compatible with any markdown editor.

## The Living System

EmptyOS doesn't just run — it watches itself, repairs itself, and grows new capabilities as you use it. Four self-* behaviours:

### Self-healing
Every capability has a provider chain ending in **human**. When a provider fails — Ollama offline, OpenAI rate-limited, ComfyUI crashed — the capability falls back to the next one. The system narrows but never collapses.

```bash
eos health --fix     # auto-repair capabilities, services, vault mappings
```

### Self-auditing
Thirteen integrity dimensions score the system's own architecture — connectivity, atomicity, reusability, security, privacy, expressiveness. `eos integrity` prints the scorecard. Staff agents (Growth, Root, Connect) observe audit results on a schedule and create repair tasks for what they find — weekly pruning of dead events, daily task-generation for violations.

### Self-evolving
Conversation mode is the primary growth mechanism: point any AI coding tool (Claude Code, Cursor, Aider) at the codebase, describe what you want, and the system writes new apps, extracts shared patterns into the SDK, and wires new events — all coherent with `CLAUDE.md`. Skills like `/eos-sdk-extract` and `/eos-ui-audit-and-consolidate` automate the boring extraction and migration work.

### Self-documenting
Every app generates its own docs from its manifest and code — `eos app info <id>`. The topology graph at `localhost:9000/topology` is generated from live state, not hand-maintained. If it's not on the graph, it's not in the system.

See [Architecture & Design → Self-Awareness System](docs/DESIGN.md#self-awareness-system--13-integrity-dimensions) for the full model.

## CLI

```bash
eos                     # System status
eos start               # Boot daemon (port 9000)
eos health              # Full health check
eos app list            # All loaded apps
eos app info <id>       # Self-documenting app details
eos release check       # Scan for personal data leaks
eos release core        # Package core tier for distribution

# App commands (auto-routed)
eos capture "new idea"
eos task list
eos search "cable rating"
```

## Web UI

Every app gets a UI — custom-built or auto-generated. Four themes ship by default.

| URL | What |
|---|---|
| `localhost:9000/` | Home — app launcher, stats, events |
| `localhost:9000/topology` | Live dependency graph |
| `localhost:9000/{app}/` | App UI |
| `localhost:9000/docs` | API explorer (Swagger) |
| `ws://localhost:9000/ws` | Real-time event stream |

## Build Your Own App

```bash
eos app-gen myapp "What it does"   # scaffold
```

Or create manually: `apps/myapp/manifest.toml` + `apps/myapp/app.py`. See [App Development Guide](docs/APP-DEVELOPMENT.md).

Personal apps go in `apps/personal/` (gitignored, never shipped).

## Project Structure

```
emptyos/
├── emptyos/           # Platform: kernel, SDK, capabilities, web server
├── apps/              # Community apps (shipped)
├── apps/personal/     # Your apps (gitignored)
├── plugins/           # Service plugins
├── skills/            # Claude Code agent skills
├── docs/              # Architecture, specs, guides
├── scripts/           # Release tooling, validators
├── data/              # Runtime state (gitignored)
└── emptyos.toml       # Your machine config (gitignored)
```

## Documentation

- [Getting Started](docs/GETTING-STARTED.md) — Install, configure, first boot
- [App Development](docs/APP-DEVELOPMENT.md) — Build your own app
- [Architecture & Design](docs/DESIGN.md) — Philosophy, principles, consciousness model
- [App Inventory](docs/APPS.md) — All apps with capabilities and status

## Deployment

EmptyOS runs in three contexts, same codebase:

- **Local** (default) — `eos start` on your laptop. Your vault on your disk. No auth, localhost-only.
- **Self-hosted** — `docker run` on your VPS with auth enabled. Access your instance from anywhere via private URL or Tailscale.
- **Live demo** — public instance with a curated demo vault. Users can try EmptyOS without installing.

Cloud AI providers (OpenAI, Anthropic) are opt-in. When a capability uses a cloud provider, you see a consent prompt showing what data will be sent. Your vault never syncs to the cloud — it stays where you put it.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full Hetzner + Caddy walkthrough (private VPS and public demo), or [Getting Started → Deployment](docs/GETTING-STARTED.md#deployment-options) for the config-only summary.

## Contributing & Security

- [CONTRIBUTING.md](CONTRIBUTING.md) — how to file issues, open PRs, and ship apps
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — how we treat each other
- [SECURITY.md](SECURITY.md) — how to report vulnerabilities (please don't open public issues for security)

## Tech Stack

Python 3.12+, FastAPI, Typer, Rich, aiohttp, SQLite, APScheduler, watchfiles

## License

[MIT](LICENSE)
