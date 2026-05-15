# CLAUDE.md — EmptyOS

> **EmptyOS — a mind companion. Think and create with you, not for you.**

## What Is This

EmptyOS is an AI-powered operating system — a **mind companion** that thinks and creates alongside the user. **An OS is just a human doing things** — reading, writing, thinking, searching. Tools are optional accelerators. A markdown vault serves as the **hard drive** — mounted externally, swappable, human-readable. Kernel state lives in SQLite/JSON.

**North star: with you, not for you.** Features that augment the user's judgment (surface, suggest, assist, generate-for-review) are the default. Features that replace judgment (autopilot, silent auto-decisions) need strong justification. This is the constraint that separates EmptyOS from autonomous-agent framings.

Three runtime modes: **daemon** (port 9000, apps + events + agents), **CLI** (one-shot commands), **conversation** (AI coding tool loads codebase as context, evolves the system). Conversation mode is the primary growth mechanism. See `docs/DESIGN.md` for architecture, philosophy, and the consciousness model.

Non-Claude-Code AI tools: see `AGENTS.md`.

## Quick Start (fresh clone)

```bash
cp emptyos.toml.example emptyos.toml   # then set notes.path = "D:/YourVault"
pip install -e .
restart.bat                            # Windows; or: python -m emptyos start
# → http://localhost:9000
```

In a Claude Code session: `/eos-session-resume` to pick up the last session, `/eos-session-wrapup` to close one out.

## Principles

1. **Everything can be generated** — apps, UIs, configs, pipelines
2. **Everything is reusable** — extract shared work into the platform (`sdk/`)
3. **Everything is connected** — event bus over imports; topology graph IS the architecture
4. **Atomic code, like atomic notes** — apps are atoms (manifest + app.py); value is in connections
5. **Self-testing, self-fixing** — health checks + graceful fallback
6. **The system is expressive** — every app has a UI (custom or auto-generated)
7. **Self-documenting** — `eos app info <id>` generates docs from manifest + code; no separate dev docs
8. **Vault is external** — mounted, swappable, human-readable
9. **Reactive vault population** — `git:saved` → reactor → journal entry; one action ripples to related notes
10. **The system is alive** — Growth Agent + vault emergence + self-audit loop

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Apps (ALL first-class, no runtime tiers)             │
│  apps/           — core, git-tracked, shipped        │
│  apps/personal/  — user apps, gitignored, local      │
├──────────────────────────────────────────────────────┤
│  Platform Runtime                                    │
│  Services:    vault watcher, scheduler, real-time,   │
│               compute workers (GPU job queue)        │
│  Libraries:   frontend (theme.css + eos.js)          │
│  Connectors:  ollama, comfyui, voice-api, obsidian   │
├──────────────────────────────────────────────────────┤
│  Kernel                                              │
│  Config, 10 Capabilities, EventBus, ServiceRegistry, │
│  AppLoader, PluginLoader, WorkerPool, Providers      │
└──────────────────────────────────────────────────────┘
Vault (external) ← mounted via emptyos.toml: notes.path = "D:/YourVault"
```

### 10 Capabilities

| Capability | Providers |
|---|---|
| **think** | ollama, openai, claude-cli, human (domains: text, code, reason) |
| **read** | filesystem, human |
| **write** | filesystem, human |
| **search** | grep, human |
| **speak** | edge-tts (in-process) → openai-tts (cloud) → kokoro → xtts (via voice-api :8602) |
| **listen** | openai-whisper (cloud) → whisper (local, via voice-api :8602) |
| **draw** | comfyui |
| **animate** | comfyui-ltx (image-to-video via user-supplied workflow JSON); cloud providers (Runway/Luma/Kling) plug in here |
| **see** | webcam (OpenCV, local) → human (upload a file) |
| **browse** | playwright (headless Chromium) — no human fallback; raises when no provider, app catches |

### 20 Plugins

Service plugins expose named services (`self.require("name")`); enhancer plugins inject providers into capabilities at startup with graceful fallback. Inventory + Obsidian-dependency clause: `.claude/rules/plugins.md`. Browse `plugins/` for source.

## Storage & Vault

### Two domains

| Domain | What | Location |
|---|---|---|
| **Vault (user knowledge)** | Anything a human wrote, edits, or needs after reset — journal, contacts, jobs, expenses, items | External `notes.path` in emptyos.toml, markdown + frontmatter |
| **data/ (machine telemetry)** | Event history, syslog, billing counters, chat sessions, activity logs | `data/` (SQLite/JSON) |

**Rule:** Human-authored or recovery-critical → vault. High-frequency operational bookkeeping → `data/`.

### VaultLibrary standard

All vault-backed collections use `VaultLibrary` (SDK). Each item is a `.md` note with frontmatter + type tag (e.g. `tags: [song]`). Query by tag via `vault_query()`, not folder. Folder is default creation location only. See `emptyos/sdk/vault_library.py`.

### KB note kinds

Every KB note carries `tag: kb` and one of eight `kind`s — one corpus, role typing via kind (Notes + Blocks + Documents were unified 2026-05-15):

| Kind | Shape |
|---|---|
| `concept` | Explanatory standalone |
| `formula` | Implementable spec — `verified_against:` anchors to a case, `implemented_in:` to a code path |
| `reference` | Whole external source (IEC 60287, TB 880, Anders textbook…) — landing page that aggregates its clauses via the `standard_id:` field |
| `clause` | Verbatim text of one section/clause from a reference — frontmatter `standard`, `edition`, `clause` |
| `case` | Worked example with published numbers |
| `lesson` | Practical / hard-won knowledge |
| `doc` | Composition outline via `paragraphs_json` — each paragraph carries `noteRefs: [slug | slug#section]` resolved server-side at view time |
| `moc` | Map of content / navigation hub |

Free-text `references:` strings in any KB note auto-resolve to clickable `clause` notes via the citation parser in `apps/kb/app.py`. Reverse-lookup endpoints: `/kb/api/references` (citation index), `/kb/api/implementations/<path>` (which formulas implement a code path), `/kb/api/notes/<slug>/section/<name>` (slice a single section). `BaseApp.kb_explain(slug)` lets any app pull a KB note's body for tooltip / "?" surfaces. Clauses live flat under `30_Resources/EmptyOS/kb/sources/<slug>.md`; docs under `kb/docs/<slug>.md`.

### Project standard

Every `10_Projects/` entry is a **directory**, never a flat `.md`. Defined as `PROJECT_STRUCTURE` in `apps/projects/app.py`.

```
10_Projects/{project-id}/
├── {project-id}.md      # Main note (frontmatter + tasks + notes)
├── docs/                # Specs, meeting notes, research
├── assets/              # Images, PDFs, attachments
└── log/                 # Activity logs, changelogs
```

All creation paths enforce this. `POST /api/projects/{id}/upgrade` converts flat files. `GET /api/structure` reports compliance.

## Task ↔ Project Data Flow

Projects is the **write endpoint**, task app is the **read-only aggregator**.

```
Capture #dev   → projects.add_task_to_project("emptyos-development")
Capture other  → task.add() → projects.add_task_to_project("inbox")
Task app       → scans entire vault for - [ ] lines (cross-project view)
```

- `task.add(text)` routes to inbox by default, or specific project via `project=` kwarg
- Quick-action tag → project routing: `_TAG_PROJECT` in `apps/quick-action/app.py`
- Task UI shows `[Project Name]` badges for tasks in `10_Projects/`

## Project Structure

```
D:\emptyos\
├── emptyos/
│   ├── kernel/             # Config, EventBus, ServiceRegistry, loaders
│   ├── capabilities/       # 10 capabilities + providers
│   ├── sdk/                # BaseApp, BasePlugin, decorators, VaultLibrary, srs, utils
│   ├── cli/                # eos command (Typer) — daemon client mode
│   ├── web/                # FastAPI server + auto-UI + topology
│   │   └── static/         # theme.css, eos.js, eos-components.*, eos-keys.*
│   └── runtime/            # vault watcher, vault_index, scheduler, realtime, vault_map
├── apps/                   # Core apps (git-tracked)
├── apps/personal/          # User apps (gitignored)
├── plugins/                # 19 plugins (auto-discovered, loaded before apps)
├── engines/personal/       # User engines (gitignored)
├── data/                   # Runtime state
├── emptyos.toml            # Machine config (.gitignored)
└── restart.bat             # Kill + boot
```

Loaders scan both `apps/` and `apps/personal/` — all apps equal at runtime. Fresh `git clone` gives only core. See `docs/DESIGN.md` "Core vs Personal".

## Apps

Live inventory is authoritative — `eos app list`, or browse `apps/` + `apps/personal/`. Every app is self-documenting: `eos app info <id>` generates docs from manifest + code. Don't maintain an app catalog here — it drifts.

To scaffold a new app, invoke the `eos-new-app` skill (or `eos-new-plugin` for plugins). It generates manifest, `app.py`, `pages/`, and a `tests/test_sys_<id>.py` skeleton.

### Store (per-user install gate)

`/store` is the per-user install/enable gate for apps + plugins + skills (Obsidian-community-plugins-style). State at `data/store/installed-{apps,plugins}.json`; loader.enabled_ids = installed - disabled ∪ essentials. `ESSENTIAL_APPS = {store, settings, system, hub}`; `ESSENTIAL_PLUGINS = {health}`. Demo mode bypasses the gate. Full contract: `.claude/rules/store.md`.

## App Development Pattern

An app = backend (`app.py`) + API (`@web_route`) + UI (`pages/`). **UI is not optional** — every POST implies a form, every GET list implies a table. See `docs/DESIGN.md` "UI Philosophy" and `docs/APP-DEVELOPMENT.md`.

```python
class MyApp(BaseApp):
    data = await self.read("file.md")
    result = await self.think("Analyze this", domain="code")
    await self.write("output.md", result)

    tasks = await self.call_app("task", "list_tasks")
    await self.emit("myapp:done", {"key": "value"})

    async for chunk in self.think_stream("Summarize"):
        yield chunk
```

### Manifest

```toml
[app]
id = "myapp"
name = "My App"
version = "1.0.0"
description = "What it does"

[app.entry]
module = "app"
class = "MyAppClass"

[requires]
capabilities = ["read", "write", "think"]
apps = ["task", "journal"]

[provides.cli]
commands = ["myapp"]

[provides.web]
prefix = "/myapp"

[provides.events]
emits = ["myapp:done"]
```

### App Sub-Patterns

Each pattern below has a dedicated rule file with full examples + manifest snippets. Read the rule file before building.

| Pattern | When to use | Reference |
|---|---|---|
| **Settings panel** | App has `[provides.settings]` — mandatory ⚙ button + `EOS_UI.settingsPanel()` | `.claude/rules/app-ui-patterns.md` |
| **Hash-route detail view** | App has `showDetail(id)` — bookmarkable URLs + back button via `EOS_UI.hashRoute()` | `.claude/rules/app-ui-patterns.md` |
| **Addons** | User-extensible URL slots → `[[apps.<id>.<slot>_addons]]` config + `GET /<app>/api/<slot>/{ctx}` | `.claude/rules/addons.md` |
| **Hub panels** | Glanceable signal on `/hub/` → `[[contributes.hub.panel]]` + `panel_*` method (20 renderers, priority bands, lazy hydration) | `.claude/rules/hub-panels.md` |
| **Voice intents** | App verb callable from Aura → `[[contributes.voice-assistant.intent]]` + `voice_*` method (scope-narrowing, card renderers) | `.claude/rules/voice-intents.md` |
| **Boards as view layer** | Render data another app owns → `source.type = "app"` + expose `list_all`/`set_field` + `SETTABLE_FIELDS` whitelist; auto-instantiated as readonly system-database boards | `.claude/rules/boards-as-view-layer.md` |
| **Standalone export** | App should run without the daemon → `[provides.export]` + optional `export.py` hook (`export_state` / `stub_routes` / `client_overrides`); one-way snapshot, no sync | `.claude/rules/app-conventions-for-export.md` |
| **Tour steps** | App should appear in the product tour → `[[contributes.tour.step]]` with `route` + `spotlight` selector + optional `requires`; capability-missing steps auto-rewrite to `/system` | `.claude/rules/tour-steps.md` |
| **Private to repo, hidden from demo** | App must never appear in a public/demo deployment → `[app] private = true`; `app_loader` skips it when `demo.enabled`, `release-public.py` aborts if it shows up in a public snapshot | `.claude/rules/demo-mode.md` |
| **Multi-CLI participants** | App spawns external coding-agent CLIs (claude-cli, codex, gemini) per @-mention → `agent-runtime` plugin's `text_cli_run` for buffered text, `claude_cli_run` for stream-json + tool events; per-CLI config in `[plugins.agent-runtime.clis.<id>]` | `.claude/rules/multi-cli-participants.md` |
| **Slash command palette** | App's text input has 4+ verbs that need keyboard-first firing → single `SLASH_COMMANDS` array, mode-aware popup state, command-owned arg parsing | `.claude/rules/slash-command-palette.md` |
| **Room review gate** | CLI / agent emits `[DO:app.method({...})]` tokens that should require Apply/Reject before executing → `_gate_server_actions` parses + persists pending; per-action card renders in chat + activity drawer + global dashboard | `.claude/rules/room-review-gate.md` |
| **Multi-module app decomposition** | `app.py` crossed ~1200L (P4 Atomic threshold) → split into helper modules (module-level fns taking `self`, decorators preserved, re-bind in class body); 5 mandatory conventions (docstring template, binding banner, `TYPE_CHECKING` guard, no helper-to-helper imports, constants travel with consumer). Tooling: `scripts/decompose_app.py`. References: `apps/dogfood-agent/`, `apps/rooms/`, `apps/projects/` | `.claude/rules/multi-module-apps.md` |

Triggers: `eos app export <id>` for export bundles. Debug surfaces: `/hub/debug/panels`, `/voice-assistant/debug/intents`.

## Web & CLI Access

| URL | What |
|---|---|
| `http://localhost:9000/` | Home — app launcher, stats, events |
| `http://localhost:9000/topology` | Live dependency graph |
| `http://localhost:9000/system` | Capability Inspector — providers, status, recovery hints |
| `http://localhost:9000/docs` | FastAPI Swagger — all API routes |
| `http://localhost:9000/{app}/` | App UI (custom or auto-generated) |
| `ws://localhost:9000/ws` | WebSocket — live events |

```bash
eos                     # System status
eos start               # Boot daemon (port 9000)
eos health              # Full health check
eos app list            # All apps
eos app info <id>       # Self-documenting app details

# App commands (auto-routed to running daemon, or local kernel)
eos capture "idea"
eos task list
eos journal add "good day" good
eos search "cable rating"
```

CLI detects running daemon → proxies via HTTP (shared kernel). No daemon → falls back to local kernel.

## Development Rules

1. **Apps use capabilities, never direct tools** — `self.read()` not `open()`
2. **Human is always the final fallback** — in interactive mode
3. **All apps are equal** — no runtime tiers, same manifest/lifecycle (`release.toml` tiers are for release bundling only)
4. **Apps declare, platform provides** — manifest dependencies validated on load
5. **Events over imports** — apps communicate via bus, not coupling
6. **Self-documenting** — improve `eos app info`, don't write READMEs
7. **Auto-UI is the default; `pages/index.html` overrides** — new CRUD apps boot with a working UI from `emptyos/web/auto_ui.py` (shared bundle, list + add + delete, stats). Write a custom `pages/index.html` only when the surface isn't a list (deep-canvas, dashboards, single-shot generators) or auto-UI's affordances aren't enough
8. **Vault-map driven** — `{vault}/30_Resources/EmptyOS/_vault-map.toml` (auto-generated by `emptyos/runtime/vault_map.py` on first boot) declares where each app's data lives. Apps read via `self.vault_config("key")`, never hardcode. `self.vault_write()` → `{vault}/30_Resources/EmptyOS/{app}/`. `self.search()` reads the whole vault.
9. **Extract shared, then reuse** — build specific first in one app, extract to `sdk/` when a second app needs it
10. **Everything self-testable** — if you can't test it from `localhost:9000` or CLI, it's not done
11. **Reactive vault population** — user actions emit events (e.g. `git:saved`, `capture:saved`); `apps/reactor/` chains them into journal entries + related notes
12. **Prompts are first-class artifacts**
    - Named `UPPERCASE` constants at module top, never inline
    - Always `system=` kwarg for persona/rules; user message for the specific request
    - Every content prompt says what NOT to do (negative examples are highest-leverage)
    - Temperature: parsing 0.1–0.3, analysis 0.3–0.5, creative 0.6–0.8
    - No thin prompts — user-facing content needs host personas + quality standards + structure
13. **No personal data in committed code** — no personal paths, names, coordinates, API keys in git-tracked files. Use `emptyos.toml` (gitignored) for machine config. `.eos-personal` defines banned patterns. Files under `.claude/` with machine-specific data (e.g. `vault-connection.json` — already in `.gitignore`) must be gitignored.
14. **No third-party branding in user-facing text** — App UIs, prompts, error messages must not mention Obsidian/Suno/Kindle/etc. Use generic terms: "markdown vault", "source URL", "Open external". **Exception:** plugin code that integrates with a specific service may reference it. `.eos-branding` + `scripts/check-branding.py` enforce.
15. **Community apps, personal config** — `apps/` are generic; machine-specific customization lives in `emptyos.toml` `[apps.<id>]`, read via `self.app_config(key, default)`. VaultLibrary accepts `extra_fields` from config. Same codebase, different behavior per machine. `apps/personal/` is for apps whose **logic** is personal, not customized community apps.
16. **Wellbeing wheel as design lens** — the 8 dimensions (physical, social, intellectual, emotional, spiritual, environmental, financial, occupational) are a rubric applied in reasoning, **never a feature added to UIs**. When suggesting work, reviewing apps, or proposing features, silently score against wheel balance: prefer suggestions that feed thin dimensions over those that fatten already-dominant ones (typically occupational/intellectual). Resist adding dimension pickers, tag prompts, or wheel displays — the wheel shapes *what gets built and what gets refused*, not what the user sees. Canonical list + aliases live in `emptyos/sdk/dimensions.py`; read passive metadata from app manifests (`dimensions = [...]`) when available.
17. **No localhost assumptions in app logic** — apps use `self.*` capabilities, never hardcode `localhost`, `127.0.0.1`, or port numbers. Host/port come from `[network]` config, which is derived from `network.mode` (`local` / `private` / `public`). The same code must run on a user's laptop, a Tailscale-private machine, a VPS, or a demo container without edits.
18. **Cloud consent is mandatory** — any provider whose host is not localhost/127.0.0.1/private-IP is `is_cloud=True` and must pass through the consent gate in `Capability.execute()`. Never add cloud-specific code paths in apps — the provider chain handles routing, the consent gate handles approval. See `docs/DESIGN.md` "Cloud Provider Consent".
19. **No vault data to cloud by default** — cloud providers receive prompts, not raw vault content. If an app needs to send vault content to a cloud model, it must be explicit per-request or via an opt-in config flag. Never embed large vault excerpts in system prompts that hit cloud.
20. **Docker-bootable** — the system must work from `docker run -v /vault:/vault -v ./emptyos.toml:/app/emptyos.toml emptyos`. No hardcoded absolute paths, no assumptions about the host OS, no Windows-only code in runtime paths (Windows-only tooling like `restart.bat` is fine).

## Vault Data Layer

Vault is the source of truth for app data. **VaultIndex** (`emptyos/runtime/vault_index.py`) indexes all vault markdown in memory on startup (~800ms for 3000+ files), updates incrementally via `vault:changed`.

### Three Layers in a Vault Note

```
--- frontmatter ---          ← Layer 1: Structured (indexed, queryable)
company: Acme Corp
tags: [job-application]
---

## Timeline                  ← Layer 2: Semi-structured (section names indexed)
- 2026-04-02 — interview

## Notes                     ← Layer 3: Unstructured (human prose, LLM-summarizable)
Panel was friendly...
```

### BaseApp Vault API

| Method | Layer | Purpose |
|---|---|---|
| `vault_query(tags, **props)` | 1 | Find notes by tags + frontmatter properties |
| `vault_get_properties(path)` | 1 | Read all frontmatter fields |
| `vault_update(path, props)` | 1 | Mutate frontmatter fields |
| `vault_sections(path)` | 2 | List `##` section names |
| `vault_read_section(path, name)` | 2+3 | Read content of a `##` section |
| `vault_append_section(path, name, text)` | 2 | Append to a `##` section |
| `vault_read_body(path)` | 3 | Read everything after frontmatter |
| `vault_create_note(path, fm, body)` | all | Create new note |

### Convention (not enforced)

Tags in frontmatter identify note types (`job-application`, `person`, `daily`, `song`). Apps query by tag. Apps handle missing fields with `.get()` defaults. No schema enforcement — the vault notes ARE the schema.

Two access patterns coexist: **VaultIndex** (target — `vault_query`, `vault_update`) and **vault_config + file I/O** (legacy — `vault_config()` → `Path.glob()` → parse). Apps migrate when touched. Safe migration rule: only migrate an app when its notes have queryable frontmatter. Otherwise add tags first via a vault script — never silently return empty data.

For vault operations and connection state, see `.claude/rules/vault-operator.md`.

## Deployment

EmptyOS deployments fall into **5 lanes**, documented in `docs/DEPLOYMENT.md`:

| # | Lane | Discriminator | Mechanism |
|---|---|---|---|
| 1 | Service | HTTP container, **no vault** | `services/<name>/` + `scripts/deploy-service.sh` |
| 2 | Daemon (single-tenant) | Full EmptyOS, **vault mounted** | `docker-compose.yml` + `scripts/redeploy-demo.sh` |
| 3 | Static site | Pre-rendered, no runtime | `eos publish deploy` |
| 4 | Bundled product | Daemon preconfigured + branded (future) | `profiles/<name>/profile.toml` |
| 5 | Multi-tenant SaaS | Daemon serving many tenants (future) | TBD |

Decision rule: **service has no vault, daemon has a vault**. Don't add vault access to a Lane 1 service — bridge through a daemon instead.

Variants compose with lanes (don't multiply them): `worker` (no vhost on Lane 1), `edge` (constrained), `air-gapped`, `hybrid` (multiple lanes per product). Out of scope: serverless, native distribution (PyPI/desktop/mobile), federation/CRDT.

**Demo vs public vs private:** `network.mode = "public"`, `demo.enabled`, and `.eos-personal` + `[app] private = true` are three orthogonal knobs — see `.claude/rules/demo-mode.md` for the contract (what each does, when to use which, and how reset/seed-on-boot are wired).

**Auth model:** EmptyOS is single-user by design — `auth_token` (machine) + `password` (human) are the only two credentials, applied as a network gate (not an identity system). See `docs/AUTH.md` for the pin and the rationale for never growing a users table inside the daemon.

## Tech Stack

Python 3.12+, FastAPI, Typer, Rich, aiohttp, SQLite, APScheduler, watchfiles.

## Dev Log

Three layers, different purposes:

| Layer | Where | When |
|---|---|---|
| **Breadcrumbs** (reactor) | Daily journal note (`50_Journal/`) | Automatic — `git:saved` ripples |
| **Session summaries** (`/devlog`) | `10_Projects/emptyos/log/YYYY-MM-DD.md` | End of session — invoke `/devlog` |
| **Raw history** | `git log` | Every commit |

At session end for meaningful changes, invoke `/eos-session-wrapup`. See `.claude/rules/docs-sync.md` for triggers.

## Testing

Tests (pytest + Playwright) cover apps, UI components (modal/sidebar/chat), user stories, accessibility, visual baselines, edge cases. System suite is CI-safe; personal tests gitignored. See `tests/conftest.py` for fixtures and `tests/helpers.py` for shared assertions.

```bash
python -m pytest tests/ --ignore=tests/personal -v    # CI / release-safe
python -m pytest tests/test_sys_<app>.py -v           # single app (after UI change)
python -m pytest tests/ -k "not test_ui" -v           # API-only fast path
python -m pytest -m "dogfood and not llm" -v          # dogfood — "is it usable?"
```

Always invoke as `python -m pytest`, never bare `pytest` — the `pytest` binary may resolve to a different Python than the one running the daemon (common on Windows with multiple Python installs), causing pytest-playwright plugin discovery to fail silently (`fixture 'page' not found`).

Requires daemon at `localhost:9000`. One-time setup: `pip install playwright pytest-playwright pytest-timeout pytest-rerunfailures httpx && playwright install chromium`. Test data uses `TEST_PREFIX = "PLAYWRIGHT-TEST-"`, cleaned by session autouse fixture. Pass `--timeout=60 --reruns 2` on release / CI runs: `--timeout` kills hung tests, `--reruns 2` retries UI flakes that surface when the daemon is under heavy parallel load — 1 retry isn't always enough because the immediate retry runs while the daemon is still swamped; 2 retries gives the daemon a chance to catch up.

### Four layers, four questions

| Layer | Files | Answers |
|---|---|---|
| **System** | `test_sys_<app>.py` | Does each button/endpoint work? (smoke) |
| **User story** | `test_user_stories.py` | Does one deep per-app flow work end-to-end? |
| **Journey** | `test_journeys.py` | Do cross-app event chains ripple? |
| **Dogfood** | `test_dogfood.py` + `test_dogfood_<app>.py` | Could I use this for a week/month without noticing something broken? |

Don't conflate or duplicate across layers. Dogfood is narrative + ordered + state-threading; earns its keep when it spans ≥2 apps or catches aggregation bugs endpoint tests miss. Below that bar, `test_user_stories.py` is the right home. LLM-hitting steps use `@pytest.mark.llm` so `-m "dogfood and not llm"` stays fast and free. CI runs the non-LLM dogfood suite on every push. Full workflow: `.claude/rules/testing.md`.

### Test-fix-verify loop

EmptyOS tests/fixes/verifies itself by composing four roles via the event bus: **friction source** (today `dogfood-agent`) → **fix-driver** (`apps/fix-agent/`, worktree-per-fix, py_compile-gated merge) → **sandbox** (`:9001` via `plugins/dogfood-demo/`, restarted between merge + verify) → **verifier** (dogfood-agent re-runs scenario, auto-reverts on failure). Main daemon never restarted by the loop. Contract + safety invariants: `.claude/rules/test-fix-verify-loop.md`.

## External Service Launch Pattern

External services (Ollama, ComfyUI, voice-api, Blender) use embedded runtimes with relative paths. Two launch contexts, same rules:

**restart.bat** — `pushd` + `start /b` for headless background:
```batch
pushd D:\ComfyUI_windows_portable
start /b "" .\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build >nul 2>nul
popd
```

**Plugin `auto_start()`** — embedded python directly with `CREATE_NO_WINDOW`:
```python
subprocess.Popen([python_exe, "-s", main_py, "--flags"],
                 cwd=launcher_dir, creationflags=subprocess.CREATE_NO_WINDOW,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

Rules:
- **Never** `start /min` or `cmd /c start` (both open windows)
- **Always** set `cwd` to the service directory — launcher scripts use relative paths
- Config: `emptyos.toml` `[plugins.<id>]` `launcher = "path/to/run.bat"`
- Health check: poll service endpoint (e.g. `/system_stats`) until ready, 60s timeout

## Development Gotchas

The architecturally load-bearing ones are inline below. Generic Python/web/integration quirks (Windows paths, FLUX params, Claude CLI flags, staff-agent lock, etc.) live in `.claude/rules/dev-gotchas.md`.

- **Vault read-modify-write races**: any write path that does `read → mutate → write` on the same file yields the event loop between awaits. If a user-triggered POST and a reactor-driven event handler (the reactor subscribes ~30 events) both hit the same file at nearly the same instant, both read the pre-write content and the later write wipes the earlier writer's entry. Serialize with an `asyncio.Lock` keyed by the unit of isolation (date, entity id, path). Journal is the reference implementation — `apps/journal/app.py` `_daily_lock()` wraps `_add_entry` / `api_milestone` / `api_three_things`; emits stay **outside** the lock so handlers can recurse through `call_app("journal", …)` without deadlocking. Extract to `BaseApp` when a second app (likely capture or task) needs the same pattern.
- **Vault frontmatter tags must be block-style**: write `tags:\n  - a\n  - b`, not `tags: [a, b]`. The `VaultIndex` YAML parser (`emptyos/runtime/vault_index.py`) does handle inline arrays as a defense, but mixing styles across apps is a smell — stick to block style so notes render identically across every app and any future parser that doesn't handle inline arrays won't silently drop them from tag queries.
- **Normalize loose field shapes at the write boundary**: if an API param is typed `dict | None` but callers in the wild pass a plain string (source titles, tag lists, ids), coerce once in the write function, not at every read site. Pattern: a small `_coerce_<field>(x) -> dict` helper next to the write; downstream reads can still use it defensively for legacy rows. Example: `apps/personal/media/highlights.py` `_coerce_source()`.
- **Daemon process is user-owned, not Claude-owned**: from a Claude Code session, never run `restart.bat`/`stop.bat`, `python -m emptyos start`, `taskkill` python procs, or delete `data/*.db*` files. Daemons spawned from a Claude tool inherit its process group and die when the tool ends; killing python procs leaves SQLite WAL handles dangling and corrupts `data/syslog.db`. Probe via `curl`, read `data/daemon.err.log`, surface diagnoses — let the user act. Full rules + safe alternatives: `.claude/rules/daemon-handling.md`.

## Shared Frontend

Visual + interaction DNA: `docs/FRONTEND-DESIGN-LANGUAGE.md` (read before touching a page). Shared bundles + geo stack + keyboard shortcuts: `.claude/rules/shared-frontend.md`.

Quick pointers — `EOS_UI.*` for modal/form/cards/badges/FABs; `EOS_MAP` for Leaflet; `EOS.noteActions(path)` for clickable vault links; `apps/geocode/` + `apps/routing/` for geo; `Ctrl+K` opens the command palette; `g`+letter go-to nav. Audits run via `/eos-ui-audit-and-consolidate`.

## Session Continuation

```bash
python -m emptyos          # System status
python -m emptyos health   # Full health check
python -m emptyos start    # Boot daemon on port 9000 (or restart.bat on Windows)
```

In a Claude Code session, `/eos-session-resume` reads `{vault}/10_Projects/emptyos/log/_next.md` (written by the previous `/eos-session-wrapup`) and briefs you on where to pick up.

**Reading this file means you're in conversation mode** — the system's most powerful runtime. You have the full architecture in context. You can create apps, extract patterns, wire events, make architectural decisions coherent with the consciousness model. The daemon serves what exists; you evolve what's next.

For recent work, use `git log` and `10_Projects/emptyos/log/`. Don't maintain changelogs here.

## Key Files

- `docs/DESIGN.md` — architecture, philosophy, consciousness model, mechanism layers
- `docs/APP-DEVELOPMENT.md` — building apps
- `docs/FRONTEND-DESIGN-LANGUAGE.md` — visual + interaction DNA for every page
- `docs/GETTING-STARTED.md` — public onboarding
- `AGENTS.md` — non-Claude-Code AI self-config
- `apps/forge/FORGE.md` — Forge growth charter (read before adding a Target / Skill / Protocol method)
- `emptyos.toml` — machine config (gitignored)
- `emptyos/kernel/__init__.py` — kernel boot sequence
- `emptyos/sdk/base_app.py` — BaseApp with all capabilities
- `emptyos/sdk/vault_library.py` — vault-backed collection standard
- `emptyos/sdk/utils.py` — `parse_llm_json`, `streak_from_dates`, etc.
- `emptyos/sdk/srs.py` — SM-2 spaced repetition scheduler
- `emptyos/web/server.py` — FastAPI server + auto-UI + topology
- `emptyos/runtime/vault_index.py` — in-memory vault index
- `emptyos/runtime/vault_map.py` — app-specific path discovery + auto-heal
- `emptyos/capabilities/providers/claude_cli.py` — Claude Code provider
- `emptyos/capabilities/providers/openai_compat.py` — OpenAI/Ollama provider
- `.claude/rules/` — addons, app-conventions-for-export, app-ui-patterns, boards-as-view-layer, daemon-handling, demo-mode, dev-gotchas, docs-sync, geo, hub-panels, multi-cli-participants, multi-module-apps, plugins, proposed-action, room-review-gate, sandbox-driven-testing, sandbox-usage, shared-frontend, slash-command-palette, store, test-fix-verify-loop, testing, tour-steps, vault-operator, voice-intents
- `restart.bat` — kill python, check external services, boot EmptyOS
