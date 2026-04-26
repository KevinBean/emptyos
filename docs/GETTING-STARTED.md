# Getting Started with EmptyOS

> **A mind companion. Think and create *with* you, not *for* you.**

EmptyOS is a mind companion — a system that remembers, thinks, and creates alongside you. This guide gets it running on your machine in under five minutes.

## Requirements

- Python 3.11+
- pip
- A terminal (bash, zsh, PowerShell)
- Optional: a markdown vault (any folder of `.md` files)

## Installation

### From Source

```bash
git clone https://github.com/KevinBean/emptyos.git
cd emptyos
pip install -e .
```

This installs the `eos` CLI globally.

### Verify

```bash
eos
```

You should see a system status summary showing capabilities and loaded apps.

## First-Time Setup

Run the interactive setup:

```bash
eos init
```

This walks you through:
1. **OS name** — give your instance a name
2. **Notes path** — point to your markdown vault (or leave empty)
3. **Port** — web dashboard port (default: 9000)
4. **LLM providers** — optional, configure Ollama/OpenAI/Claude
5. **Plugins** — optional, configure external services
6. **Startup programs** — optional, programs to launch on boot

The result is `emptyos.toml` — your machine-specific config. This file is gitignored and never shared.

### Minimal Config (No AI)

If you skip all optional steps, your `emptyos.toml` looks like:

```toml
[os]
name = "My EmptyOS"
data_dir = "./data"
log_level = "INFO"

[notes]
path = ""
watch = false

[network]
mode = "local"       # local | private | public (see Deployment Options below)
port = 9000
auth_token = ""       # required for "public" mode

[demo]
enabled = false       # true → demo banner, GPU off, BYOK

[cloud]
consent = "ask"       # prompt before cloud AI providers are used

[scheduler]
enabled = true
timezone = "UTC"

[apps]
path = "./apps"
```

This gives you a fully working OS — capture, notes, tasks, search, and all system apps. Every capability falls back to **human** mode: when the system needs to "think", it asks you via the CLI.

## Boot the System

```bash
eos start
```

Open `http://localhost:9000` in your browser. You'll see the home screen with app launcher, system stats, and recent events. A **Take the tour** banner appears on first run — it's a 2-minute walk through capture, task, journal, and the system inspector. Skip it any time and bring it back via `EOS.tour.start()` from the browser console.

Stuck on a feature that doesn't seem to work? Visit `http://localhost:9000/system` — the **Capability Inspector** lists every capability (think / listen / speak / draw / …), shows which provider is active, and tells you exactly what to do when one is missing (set an env var, install a plugin, start a local service).

### CLI Mode (No Daemon)

Every command also works without the daemon running:

```bash
eos capture "remember to buy milk"
eos task list
eos search "project status"
```

When the daemon is running, CLI commands proxy through HTTP (shared kernel state). Without it, they create a temporary local kernel.

## Connect a Vault

A vault is any folder of markdown files — notes, journals, projects, whatever you already have. EmptyOS treats it as a read/write hard drive.

Edit `emptyos.toml`:

```toml
[notes]
path = "D:/My Notes"    # absolute path to your vault
watch = true             # live-reload on file changes
```

Restart the daemon. EmptyOS indexes all `.md` files in memory (~800ms for 3000+ files) and updates incrementally when files change.

### Vault Structure

EmptyOS works with any folder layout. If you use [PARA](https://fortelabs.com/blog/para/), it maps naturally:

```
My Notes/
├── 00_Inbox/          # captures, unsorted
├── 10_Projects/       # active projects
├── 20_Areas/          # ongoing responsibilities
├── 30_Resources/      # reference material
├── 40_Archive/        # completed projects
└── 50_Journal/        # daily notes
```

Apps discover vault content by **tags in frontmatter**, not by folder path:

```markdown
---
title: Weekly Review
tags: [journal, weekly]
date: 2026-04-12
---

This week I...
```

## Add AI (Think Capability)

Without an LLM, EmptyOS asks you whenever it needs to "think". To automate this, configure a provider.

### Option 1: Ollama (Local, Free)

Install [Ollama](https://ollama.ai), then:

```toml
[capabilities.think]
providers = ["ollama"]

[capabilities.think.ollama]
host = "http://localhost:11434"
model = "llama3.1"
```

### Option 2: OpenAI-Compatible API

Works with OpenAI, Together, Groq, LM Studio, or any OpenAI-compatible endpoint:

```toml
[capabilities.think]
providers = ["openai"]

[capabilities.think.openai]
host = "https://api.openai.com"
model = "gpt-5-mini"
api_key_env = "OPENAI_API_KEY"    # reads from environment variable
```

### Option 3: Claude CLI

If you have Claude Code installed:

```toml
[capabilities.think]
providers = ["claude-cli"]
```

### Multiple Providers (Fallback Chain)

```toml
[capabilities.think]
providers = ["ollama", "openai"]   # tries Ollama first, falls back to OpenAI
```

Human is always the implicit last fallback. If all providers fail, the system asks you.

### Cloud Provider Consent

When the provider chain reaches a cloud provider (OpenAI, Anthropic, any non-localhost API), EmptyOS asks for your consent before data leaves the machine:

```toml
[cloud]
consent = "ask"     # "ask" (default) | "always" | "never"
```

- **`"ask"`** — prompts you the first time each cloud provider is used per session. You approve or deny; approvals are remembered for the session.
- **`"always"`** — trust the chain, never prompt. Best when you've configured your providers intentionally.
- **`"never"`** — skip cloud providers entirely, always fall through to local or human.

The UI shows a badge indicating which provider handled each request, so you always know when your data went to the cloud.

See [DESIGN.md → Cloud Provider Consent](DESIGN.md#cloud-provider-consent) for the architecture.

Restart after config changes:

```bash
# Ctrl+C to stop, then:
eos start
```

### Verify AI Works

```bash
eos dict "ephemeral"    # dictionary lookup uses think capability
```

Or visit `localhost:9000/assistant/` for an AI chat interface.

## Add Plugins

Plugins extend capabilities with external services. Configure in `emptyos.toml`:

### Ollama Plugin (Enhanced Think)

```toml
[plugins.ollama]
host = "http://localhost:11434"
model = "llama3.1"
```

Adds model listing, generation endpoints, and GPU memory monitoring.

### ComfyUI Plugin (Draw Capability)

```toml
[plugins.comfyui]
host = "http://localhost:8188"
```

Enables AI image generation via the studio app.

### Voice API Plugin (Speak + Listen)

```toml
[plugins.voice-api]
host = "http://localhost:8601"
```

Enables text-to-speech and speech-to-text across voice apps.

Plugins auto-discover on startup. If a plugin's service is offline, the system degrades gracefully — no crashes, just falls back to the next provider.

## Health Check

```bash
eos health
```

Reports status of all capabilities, providers, plugins, services, and loaded apps. Use this to diagnose configuration issues.

Full health via the API:

```
http://localhost:9000/health/api/status?full=true
```

## System Topology

Visit `localhost:9000/topology` to see a live dependency graph of all apps, plugins, capabilities, and their connections. This IS the architecture — if it's not on the graph, it's not in the system.

## The System Is Alive

EmptyOS doesn't just serve requests — it watches itself, repairs itself, and grows new capabilities as you use it.

It does this through four signature motions — vocabulary unique to EmptyOS, worth knowing as you use the system:

| Verb | What it means | Where you see it |
|---|---|---|
| **Absorb** | New input enters the storehouse — vault notes, captures, web articles, voice memos | Capture app, reader, journal — any time something enters the vault |
| **Grow** | New apps and capabilities come into being from your conversations with the system | `eos app-gen`, conversation mode, the `/eos-*` skills under `.claude/skills/` |
| **Emerge** | Gaps and patterns become visible to the system itself | Topology graph, integrity audit, unmet manifest dependencies, the Growth Agent |
| **Ripple** | One action propagates through the event bus into related notes, dashboards, and reactions | Reactor — a single git commit becomes a journal breadcrumb, a project log entry, a dashboard update |

These four are the surface of a deeper six-verb lifecycle (Absorb → Grow → Root → Connect → Emerge → Reflect) that the system uses to score and evolve itself — see [DESIGN.md → Six Verbs](DESIGN.md#six-verbs--the-lifecycle).

### Self-healing

When a provider fails (Ollama crashes, OpenAI rate-limits, ComfyUI offline), the capability falls back to the next provider — ultimately asking you. The system narrows but never collapses.

```bash
eos health --fix        # auto-repair capabilities, services, and vault mappings
```

### Self-auditing

`eos integrity` scores the system across 13 dimensions — architecture health, connectivity, atomicity, reusability, security, privacy. Useful before shipping your own apps, or when something feels off.

```bash
eos integrity           # full 13-dimension scorecard
curl http://localhost:9000/integrity/api/audit   # JSON for tooling
```

### Self-evolving

Conversation mode is how EmptyOS grows over time. Point any AI coding tool (Claude Code, Cursor, Aider) at the repo — it reads `CLAUDE.md` as the contract, loads the codebase into context, and evolves the system coherently. Create new apps, extract shared patterns, wire new events — all from a conversation.

```bash
eos app-gen myapp "What it does"    # scaffold a new app from a prompt
```

Skills under `.claude/skills/` (`/eos-sdk-extract`, `/eos-ui-audit-and-consolidate`, `/eos-system-check-and-fix`) automate the boring parts of keeping the system coherent as it grows.

See [Architecture & Design → Self-Awareness System](DESIGN.md#self-awareness-system--13-integrity-dimensions) for how it works under the hood.

## Deployment Options

EmptyOS runs in three network modes, same codebase. Pick a mode in `emptyos.toml`:

```toml
[network]
mode = "local"    # local | private | public
```

### Local (Default)

`mode = "local"` — binds `127.0.0.1:9000`, no auth, only this machine. Run `eos start` and visit `http://localhost:9000`.

### Private (Tailscale / WireGuard / LAN / VPN)

`mode = "private"` — binds `0.0.0.0:9000`, **no auth**. Your network layer is the gate. Use this when:

- You run [Tailscale](https://tailscale.com) / [Netbird](https://netbird.io) / WireGuard / ZeroTier between your devices and want to access EmptyOS from your phone or laptop
- You're on a trusted LAN behind a firewall
- You want the simplicity of no-auth access from your own devices

Example with Tailscale: install on your local machine and your phone, set `mode = "private"`, `eos start`, then visit `http://your-machine-name:9000` from your phone — all over the private mesh VPN.

```toml
[network]
mode = "private"
port = 9000
```

EmptyOS will log a note at startup that it's binding all interfaces without auth — that's expected in this mode.

### Public (Docker / VPS / Cloud)

`mode = "public"` — binds `0.0.0.0:9000`, **auth token required**. For internet-exposed deployment:

```toml
[network]
mode = "public"
port = 9000
auth_token = "a-long-random-string"    # REQUIRED — EmptyOS refuses to start without this
```

All `/api/` requests require `Authorization: Bearer <token>`. Browser visits are redirected to a login page. EmptyOS refuses to start in `public` mode without a non-empty `auth_token` set.

Docker example:

```bash
docker run -d \
  -v /path/to/your/vault:/vault \
  -v ./emptyos.toml:/app/emptyos.toml \
  -p 9000:9000 \
  emptyos/emptyos:latest
```

### Demo Mode

Demo mode is a separate `[demo]` flag that layers on top of any network mode:

```toml
[demo]
enabled = true
reset_on_restart = false    # true = ephemeral vault
```

Adds a banner, disables GPU capabilities, and exposes BYOK (Bring Your Own Key) in settings so users can paste their own OpenAI/Anthropic key. A hosted demo is typically `mode = "public"` + `demo.enabled = true`.

### Live Demo

Want to see EmptyOS without installing? **[Try the demo →](https://demo.emptyos.dev)** *(placeholder)*

## What's Next

- **Explore apps** — visit `localhost:9000` and click through the app launcher
- **Build your own app** — see [App Development Guide](APP-DEVELOPMENT.md)
- **Customize** — add personal apps in `apps/personal/` (gitignored)
- **Read the design** — see [Architecture & Design](DESIGN.md) for the full philosophy
