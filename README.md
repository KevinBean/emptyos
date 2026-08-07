# EmptyOS

An AI-native, local-first operating system for building mind companions.

- ✨ Your AI workspace — local, private, yours
- 🧠 Augments your thinking, never replaces it
- ⚡ `pip install -e .` → running in 60 seconds
- 🔧 Swap any model, any provider, per-capability

**Licensed under [AGPL-3.0](LICENSE).** Self-host is free forever. Commercial licenses available for proprietary distribution or hosted services — contact commercial@binbian.net.

EmptyOS is the **platform**. Polished products built on it ship under separate brands with their own commercial terms — see [Distributions](#distributions) below.

[Live demo](https://demo.binbian.net/?token=bLN9bRP-wMfBiaqsfI4OHuu3tGHtGsaeGsLk-8v71N8) · [Project site](https://eos.binbian.net) · [Blog](https://binbian.net)

## Who this is for

- You want to **build** your own AI workspace, not buy someone else's.
- You want your data in plain markdown, on your hardware.
- You want capability-level control — which model, local vs cloud, with explicit consent.
- You're comfortable with Python / FastAPI / `pip install -e .`.

If you want a polished product instead, look at [distributions built on EmptyOS](#distributions).

## What is EmptyOS

EmptyOS is a **mind companion platform** — it remembers, thinks, and creates alongside you. The philosophy: augment the user's judgment, don't replace it. Every state-changing action surfaced to the user is reviewable by default ("with you, not for you").

An OS is just a human doing things — reading, writing, thinking, searching. Tools are optional accelerators. EmptyOS starts with **you** as every capability provider: when the system needs to "think", it asks you. Add an LLM and it thinks with you. Add a filesystem watcher and it reads with you. The system scales from fully manual to fully automated — your choice, capability by capability.

A markdown vault serves as the **hard drive** — external, swappable, human-readable. Kernel state lives in SQLite/JSON. Apps are atoms: a manifest + a Python file. The value is in the connections between them.

### What you get

- 🧠 **A markdown vault is the hard drive** — your data stays human-readable, portable, and usable even without the system.
- ⚙️ **57 built-in apps + a plugin system** — tasks, projects, journal, search, AI assistant, publishing, and creative workflows in one place.
- 🔁 **An event bus + scheduler drive automation** — capture → organize → review → self-fix closes the loop.
- 🤖 **Any model, any provider** — Ollama / OpenAI / Claude / any OpenAI-compatible API, routed per task.
- 🔒 **Cloud calls pass a consent gate** — by default your raw vault never leaves the machine.
- 🛠️ **Conversation mode** drops an AI coding tool straight into the system's context — it keeps generating new apps, abstractions, and workflows.

For people who want to **own** a long-term AI workspace, not rent an agent. (Canonical pitch + bilingual copy: [`brand/MESSAGING.md`](brand/MESSAGING.md).)

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

That's it. Open `http://127.0.0.1:9000` and you have a working OS with task management, note taking, search, and a capture inbox — no LLM required.

> Use `127.0.0.1`, not `localhost`. In local mode the daemon binds IPv4 loopback only; some browsers resolve `localhost` to IPv6 (`::1`) first and show an offline page instead of the app.

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

### Browser Companion (optional)

A Chrome extension that turns any page into input for your daemon: a chat side
panel, right-click capture of a page / selection / link, and "Ask Aura about
this page" — which works on logged-in or paywalled pages a fetch can't reach.

It ships in this repo rather than the Chrome Web Store, so you load it unpacked:

1. Open `chrome://extensions` and turn on **Developer mode**
2. **Load unpacked** → select `tools/chrome-extension/` in this clone
3. Open its **Options** and set the daemon URL (`http://127.0.0.1:9000`), plus
   the `auth_token` from your `emptyos.toml` if the daemon isn't in local mode

Everything it does runs against *your* daemon — there is no third-party server,
and it asks for no site permissions until you share a tab with it. It also asks
the daemon which apps you actually have and only offers the actions something
can answer, so it adapts to your install instead of showing dead menu items.

See [the extension README](tools/chrome-extension/README.md) for the full
feature list and which optional apps back which surfaces.

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
                        │  Config  EventBus  16 Capabilities│
                        │  AppLoader  PluginLoader         │
                        │  ServiceRegistry  Providers      │
                        └─────────────────────────────────┘
                        Vault (external) ← mounted via emptyos.toml
```

### 16 Capabilities

Each capability is a human verb with a chain of providers tried in order. **Human is always the final fallback** — without any tools configured, the system asks you; add providers to automate what you want automated.

| Capability | What | Providers (chain) |
|---|---|---|
| **think** | Reason, summarize, generate text | ollama → openai → claude-cli → human |
| **read** | Read a document into text | filesystem, markitdown (office/doc), human |
| **write** | Write content to the vault | filesystem, human |
| **search** | Find content in the vault | grep, human |
| **speak** | Text to speech | edge-tts → openai-tts → kokoro → xtts |
| **listen** | Speech to text | openai-whisper → whisper (local) |
| **pronounce** | Phoneme-level pronunciation scoring | wav2vec2 (local) |
| **draw** | Generate images | comfyui |
| **animate** | Image/text → video clip | comfyui-ltx; cloud (Runway/Luma/Kling) |
| **model** | Prompt → articulated 3D model + URDF | robot-modeller (CadQuery) |
| **artifact** | Prompt → standalone HTML (3D, charts, diagrams, decks) | viz |
| **see** | Capture an image from a camera | webcam → human |
| **browse** | Drive a headless browser | playwright (Chromium) |
| **send** | Outbound message to a third party | email-smtp → human |
| **footage** | Find a royalty-free stock clip | pexels → pixabay → human |
| **translate** | Translate strings (English-source → ~200 langs) | nllb-200 (local) → llm-translate |

Cloud providers are opt-in and pass a consent gate; the vault never syncs to the cloud. The capability roster is generated — see [docs/APPS.md](docs/APPS.md) and the live `/system` capability inspector.

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

### Tiers & packages

Apps are organized into named tiers in [`release.toml`](release.toml). `core` and `standard` are the platform tiers. Everything outside those two is a packaged distribution/profile for a concrete audience or operating context. The public OSS release ships `standard` (which `extends` `core`).

| Tier family | Purpose |
|---|---|
| **core** | Minimum OS: vault, task, search, settings, store, hub |
| **standard** | General-purpose EmptyOS: productivity, AI, creative, publishing, and automation surfaces |
| **application distributions** | EmptyOS Plus, EnglishOS, Plekto/Coding Agent, Engineer |
| **operational bundles** | Labs, demo, and developer tooling packages used to stage, showcase, or build EmptyOS |

The full, always-current breakdown — resolved app/plugin/skill counts per tier, platforms, and release targets — is **generated** in [docs/TIERS.md](docs/TIERS.md). Build your own by adding a `[tiers.<name>]` block; the packaging script handles arbitrary tiers.

To see what's live in your build: `eos app list`. To check a tier: `python scripts/package-release.py <tier> --check`.

## Distributions

EmptyOS is the platform. Curated, branded distributions ship on top of it for specific audiences — separate brands, separate pricing, separate marketing. Each one is just another `[tiers.<name>]` block in `release.toml` plus its own packaging + landing page.

| Distribution | Audience | Status |
|---|---|---|
| **EmptyOS Plus** | Paid / premium users — research + intelligence surfaces held out of the free public release | Private distribution |
| **EnglishOS** | English learners — academy, dictionary, SRS, radio/improv practice | Private / hosted learner distribution |
| **[Plekto](https://plekto.dev)** | Coding-agent users — persistent memory, project context, and multi-agent rooms | In development |
| **Engineer** | Engineering users/builders — calculators, conformance, design packages, app-builder loop | Private distribution family |

If you want to build your own distribution, the `[tiers.*]` mechanism is the entry point — pick the apps you want, add a `private = true` flag if you don't want it in the public OSS release, and `package-release.py` produces a self-contained directory ready to ship.

Commercial licenses (for proprietary forks, hosted services, or closed distributions that don't want to comply with AGPL's network-copyleft clause) are available — contact commercial@binbian.net.

## Plugins

**33 plugins** extend capabilities or add external services. **Enhancer** plugins inject a provider into a capability at startup (graceful fallback if absent); **service** plugins expose named services apps call via `self.require(...)`. A sample:

| Plugin | Kind | What |
|---|---|---|
| **health** | service | Heartbeat, capability probes, GPU monitoring (essential) |
| **ollama** | enhancer · think | Local LLM |
| **comfyui** | enhancer · draw | GPU image/video/music generation |
| **voice-api** | enhancer · speak+listen | Kokoro + XTTS + Whisper |
| **edge-tts** | enhancer · speak | Free in-process TTS |
| **markitdown** | enhancer · read | Office/doc → markdown (local, isolated venv) |
| **translate** | enhancer · translate | NLLB-200 local MT |
| **footage** | enhancer · footage | Pexels/Pixabay stock-clip lookup |
| **email-smtp** | enhancer · send | Outbound email |
| **cadquery** | service | Articulated 3D compile + URDF (isolated venv) |
| **playwright** | enhancer · browse | Headless Chromium |
| **sandbox-pool** | service | Throwaway daemons on :9002+ for safe testing |
| **telegram** · **blender** · **applio** · **tailscale** · … | service | Bot, 3D, voice-conversion, markdown-vault viewer, tailnet, + more |

Browse `plugins/` for the full set. Plugins are toggled per-user via `/store`.

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
eos health     # check capabilities, connectors, and apps; surface recovery hints
```

### Self-auditing
Fourteen integrity dimensions (P1–P14) score the system's own architecture — generatability, atomicity, reusability, connectivity, security, privacy, scale, wellbeing balance, and more. `eos integrity` prints the scorecard. Staff agents observe audit results on a schedule and create repair tasks for what they find. A scope-gated static suite (`scripts/preflight.py`) also guards docs, KB, accessibility, and contrast.

### Self-evolving
Conversation mode is the primary growth mechanism: point any AI coding tool (Claude Code, Cursor, Aider) at the codebase, describe what you want, and the system writes new apps, extracts shared patterns into the SDK, and wires new events — all coherent with `CLAUDE.md`. Skills like `/eos-sdk-extract` and `/eos-design-system-audit` automate the boring extraction and migration work.

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

In a Claude Code session, run the `eos-new-app` skill (or `eos-new-plugin`) — it scaffolds the manifest, `app.py`, `pages/`, and a test skeleton. Or create manually: `apps/<track>/<id>/manifest.toml` + `app.py`. See [App Development Guide](docs/APP-DEVELOPMENT.md).

Personal apps go in `apps/personal/` (gitignored, never shipped).

## Project Structure

```
emptyos/
├── emptyos/                  # Platform: kernel, SDK, capabilities, web server
├── apps/
│   ├── public/{core,standard,labs}/   # OSS, shipped
│   ├── extension/<group>/             # tracked, never public (engineering, dev, …)
│   └── personal/                      # your apps (gitignored)
├── engines/                  # Pure compute libs (engineering, etc.)
├── plugins/                  # Service + enhancer plugins
├── .claude/
│   ├── rules/                # ~45 pattern rules (conversation-mode context)
│   └── skills/               # Claude Code agent skills
├── docs/                     # Architecture, specs, guides (see docs/README.md)
├── scripts/                  # Release tooling, doc generators, validators
├── data/                     # Runtime state (gitignored)
├── release.toml              # Tier / package definitions
└── emptyos.toml              # Your machine config (gitignored)
```

## Documentation

Start at the **[documentation index](docs/README.md)** — it maps every doc to what you're trying to do (use / build / deploy / understand). Highlights:

- [Getting Started](docs/GETTING-STARTED.md) — Install, configure, first boot
- [App Development](docs/APP-DEVELOPMENT.md) — Build your own app
- [Architecture & Design](docs/DESIGN.md) — Philosophy, principles, consciousness model
- [App Catalog](docs/APPS.md) — All apps with capabilities *(generated)*
- [Tiers & Packages](docs/TIERS.md) — What each bundle contains *(generated)*
- [Doc System](docs/DOC-SYSTEM.md) — How the docs document themselves (authored vs generated)

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

[AGPL-3.0-or-later](LICENSE). Free for self-hosting, contribution, and personal use.

If you want to ship a hosted service built on EmptyOS, or a proprietary fork without AGPL's network-copyleft obligations, a commercial license is available — contact **commercial@binbian.net**.

Third-party dependencies and their licenses are inventoried in [LICENSES.md](LICENSES.md); run `python scripts/check-licenses.py` to audit.
