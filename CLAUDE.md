# CLAUDE.md — EmptyOS

> **EmptyOS — a mind companion. Think and create with you, not for you.**

## What Is This


EmptyOS is an AI-powered operating system — a **mind companion** that thinks and creates alongside the user. **An OS is just a human doing things** — reading, writing, thinking, searching. Tools are optional accelerators. A markdown vault serves as the **hard drive** — mounted externally, swappable, human-readable. Kernel state lives in SQLite/JSON.

**North star: the human owns judgment; the system owns reversible execution.** "With you, not for you" is a promise about *judgment* — the user owns the direction, the taste, and every irreversible or outbound action — not a tax on *execution*. So the default splits by reversibility, not by whether the action changes state:

- **Reversible / internal actions auto-run** (task/capture/journal/kb-tag/reminders/frontmatter, and any verb with a recorded inverse). The safety net is **audit + one-click undo after**, not approval before.
- **Irreversible / external / billing actions stay human-gated** — `publish.deploy`, outbound email/message/social to third parties, cloud calls that spend money, bulk-destructive ops without a verified undo. These are never auto-eligible regardless of any grant.

The line between the two is the verb-registry eligibility class (`stable` → auto, `gated`/`never` → gate). A **grant** lets a *gated* verb auto-run in a scope; a **hold** pauses auto for a *stable* verb. Both are per-actor + per-verb + per-scope, never a global mode; the system never widens that policy on its own, and its audit surface must be one the user actually reads. See `.claude/rules/autopilot-grants.md` and `.claude/rules/verb-registry.md`.

Three runtime modes: **daemon** (port 9000, apps + events + agents), **CLI** (one-shot commands), **conversation** (AI coding tool loads codebase as context, evolves the system). Conversation mode is the primary growth mechanism. See `docs/DESIGN.md` for architecture, philosophy, and the consciousness model. Non-Claude-Code AI tools: see `AGENTS.md`.

## Quick Start (fresh clone)


```bash
cp emptyos.example.toml emptyos.toml   # then set notes.path = "/path/to/your/vault"
pip install -e .
restart.bat                            # Windows; or: python -m emptyos start
# → http://127.0.0.1:9000  (use 127.0.0.1, not localhost — local mode binds
#                           IPv4 loopback only; some browsers try ::1 first)
```

In a Claude Code session: `/eos-session-resume` to pick up the last session, `/eos-session-wrapup` to close one out. `/preflight` sanity-checks git + daemons before any work. Run `/env-check` if shell behavior seems off. See `.claude/rules/environment.md`.

## Working Agreements


- **Scope discipline — produce ONLY the artifact that was asked for.** No extra findings reports, matrix entries, renames, summary docs, or "while I was in there" side-artifacts. If an extra artifact looks genuinely valuable, ask in **one line** first and wait. This is not a licence to under-deliver: finish the *whole* requested scope (a feature request still implies the full loop, § EmptyOS Workflow); it forbids widening, not completing.
- **Facts carry a source or a stop.** Any claim about a person, a company, or repo/vault state you did not read **this session** gets an inline tag: `[verified: <path or URL>]` or `[unverified — guess]`. Never assert a roster, team structure, headcount, or a named person's role from memory. **An unverified claim is a question, not a premise** — do not reason on top of it, do not let it shape a recommendation; surface it and ask. A confident wrong claim is worse than an admitted gap.

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
│  apps/public/{core,standard,englishos}/ — OSS, shipped │
│  apps/extension/<group>/  — tracked, never public    │
│  apps/personal/  — user apps, own nested repo, local │
├──────────────────────────────────────────────────────┤
│  Platform Runtime                                    │
│  Services: vault watcher, scheduler, real-time,      │
│            compute workers (GPU job queue)           │
│  Libraries: frontend (theme.css + eos.js)            │
│  Connectors: ollama, comfyui, voice-api, obsidian    │
├──────────────────────────────────────────────────────┤
│  Kernel                                              │
│  Config, 16 Capabilities, EventBus, ServiceRegistry, │
│  AppLoader, PluginLoader, WorkerPool, Providers      │
└──────────────────────────────────────────────────────┘
Vault (external) ← mounted via emptyos.toml: notes.path = "/path/to/your/vault"
```

### 16 Capabilities

Provider chains, first to last. Caveats (cloud routing, why `send` is CLI-only, unconsumed `footage`, artifact shapes) → `.claude/rules/capabilities-detail.md`.

| Capability | Providers |
|---|---|
| **think** | ollama, openai, claude-cli, human (domains: text, code, reason) |
| **read** / **write** | filesystem, human |
| **search** | grep, human (domains: `code` → semble BM25+embeddings; `files` → per-OS filename index, opt-in) |
| **speak** | edge-tts (**cloud**, `trust = "service"`) → openai-tts (cloud) → kokoro → xtts (via voice-api :8602); Chinese lines route to edge-tts unless a provider is pinned — an off-machine send |
| **listen** | openai-whisper (cloud) → whisper (local, via voice-api :8602) |
| **pronounce** | wav2vec2 phoneme scoring via the `pronounce` plugin; local-first, no human fallback |
| **draw** | comfyui |
| **animate** | comfyui-ltx (image-to-video via user-supplied workflow JSON); cloud providers plug in here |
| **model** | robot-modeller app (LLM → CadQuery → URDF via the `cadquery` plugin) |
| **artifact** | viz app (LLM → standalone HTML in one shot; 11 shapes) |
| **see** | webcam (OpenCV, local) → human (upload a file) |
| **browse** | playwright (headless Chromium) — no human fallback |
| **send** | email-smtp → human. Outbound to third parties is consent-gated + leak-scanned; general entry point is `eos send` (a CLI on purpose — no HTTP route) |
| **footage** | pexels → pixabay → human; dark until an API key is set; built but unconsumed |
| **translate** | NLLB-200 (local) → `llm-translate`; English is the single authored language (`emptyos/sdk/i18n.py`) |

### Plugins

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

Every KB note carries `tag: kb` and one of ten `kind`s. The vocabulary is closed and enforced in two places — `KINDS` in `apps/public/standard/kb/shared.py` and `VALID_KINDS` in `scripts/audit_kb_app_alignment.py` — pinned against each other by `tests/test_unit_kb_alignment_symbols.py`; add a kind to both or neither.

| Kind | Shape |
|---|---|
| `concept` | Explanatory standalone |
| `formula` | Implementable spec — `verified_against:` anchors to a case, `implemented_in:` to a code path |
| `reference` | Whole external source — landing page aggregating its clauses via `standard_id:` |
| `clause` | Verbatim text of one section of a reference — frontmatter `standard`, `edition`, `clause` |
| `case` | Worked example with published numbers |
| `lesson` | What people get WRONG (`## The lesson`, `## The misconception`) |
| `guide` | A procedure for carrying out a task — how to DO it; substantive body, composes/navigates nothing |
| `pattern` | Reusable scaffolding (engineering anatomy + fenced code) for viz few-shot injection or copying |
| `doc` | Composition outline via `paragraphs_json` — `noteRefs: [slug \| slug#section]` resolved at view time |
| `moc` | Map of content / navigation hub |

Free-text `references:` strings auto-resolve to `clause` notes (citation parser in `apps/public/standard/kb/shared.py`: closed regex families for IEC/CIGRE/AS/IEEE/EREC, plus every *registered* `standard_id`). Reverse lookups: `/kb/api/references`, `/kb/api/implementations/<path>`, `/kb/api/notes/<slug>/section/<name>`. `BaseApp.kb_explain(slug)` pulls a KB body for tooltips. Clauses live flat under `30_Resources/EmptyOS/kb/sources/<slug>.md`; docs under `kb/docs/<slug>.md`. **A client document registered for one project** (`POST /kb/api/sources/ingest`, `apps/public/standard/kb/sources.py`) keeps the `reference`/`clause` shape but is tagged `kb-source`, not `kb`, carries `project:`, and lives under `10_Projects/<id>/docs/sources/` — the global KB, health, boards and every `vault_query(tags=["kb"])` consumer never see it; `resolve_reference(reference, project=)` searches it first.

### Project standard

Every `10_Projects/` entry is a **directory**, never a flat `.md` — `{project-id}/{project-id}.md` plus `docs/`, `assets/`, `log/` created **lazily on first write** (the vault-structure scanner prunes empty dirs). Defined as `PROJECT_STRUCTURE` in `apps/public/standard/projects/app.py`. `POST /api/projects/{id}/upgrade` converts flat files; `GET /api/structure` reports compliance.

## Task ↔ Project Data Flow


Projects is the **write endpoint**, task app is the **read-only aggregator**. `task.add(text)` routes to the inbox project by default, or a specific project via `project=`; capture `#dev` goes to `emptyos-development` (tag → project routing: `_TAG_PROJECT` in `apps/public/core/quick-action/app.py`). The task app scans the whole vault for `- [ ]` lines and shows `[Project Name]` badges for tasks in `10_Projects/`.

## Project Structure

Loaders scan the whole `apps/` track tree (any depth) via `emptyos/sdk/app_layout.py` (`iter_app_dirs`) — all apps equal at runtime; **app ids are independent of folder location**. A fresh public `git clone` gives `public/core` + `public/standard` + `public/englishos` (`PUBLIC_TIERS`, `emptyos/sdk/release_tiers.py`; the release filter drops `labs/`, `extension/` + `personal/`). Marketplace installs land in a category folder via the Store. See `docs/DESIGN.md` "Core vs Personal" + `.claude/rules/store.md`.

## Apps


Live inventory is authoritative — `eos app list`, or browse `apps/`. Every app is self-documenting: `eos app info <id>`. Don't maintain an app catalog here — it drifts.

**Four grouping layers, not substitutes:** `release.toml` **tiers** (what ships), `suites.toml` **suites** (9 descriptive product chapters; surface apps compose, atom apps own data), `store_category` (launcher sections), and the `workspaces` app's per-machine **spaces**. They drift independently — **when you add an app, add it to the tier, the suite, and (for engineering) the Space.** Contract, checkers, and the load-bearing `private` / `delivery` fields → `.claude/rules/app-grouping.md`.

To scaffold a new app, invoke the `eos-new-app` skill (or `eos-new-plugin`). **Engineering calculators** follow `docs/ENGINEERING-APP-WORKFLOW.md` with `docs/TRUST-LOOP.md` as the assurance contract — every obligation carries a receipt naming *what runs it* → `.claude/rules/engineering-workflow.md`. For **any feature**, the general loop (strategy → brainstorm → plan → work → review → compound-learning) is `docs/ENGINEERING-WORK-LOOP.md`; there is no missing orchestration layer to build (`docs/OPEN-SOURCE-BORROWING-PLAN.md` for borrowed-framework verdicts).

### Store (per-user install gate)

`/store` is the per-user install/enable gate for apps + plugins + skills. State at `data/store/installed-{apps,plugins}.json`; loader.enabled_ids = installed - disabled ∪ essentials (`ESSENTIAL_APPS = {store, settings, hub}`; `ESSENTIAL_PLUGINS = {health}`). Demo mode bypasses the gate. The Marketplace installs third-party apps and plugins through one propose→preview→confirm pipeline; plugins run code at boot so a static `skill_scan` is mandatory on every plugin install. Full contract: `.claude/rules/store.md`.

## App Development Pattern


An app = backend (`app.py`) + API (`@web_route`) + UI (`pages/`). **UI is not optional** — every POST implies a form, every GET list implies a table, and **every value a user would change without editing a file implies a `[provides.settings]` entry** read back with `setting_or_config` (`scripts/check_settings_dead_toggle.py` gates the read path). `app_config()` alone is for operator/machine facts — paths, hostnames, secrets, dark flags, addon templates; the exceptions are listed once in `.claude/rules/app-ui-patterns.md`. Kernel keys pass the same test; their home is `SYSTEM_SETTINGS` in the settings app. See `docs/DESIGN.md` "UI Philosophy" and `docs/APP-DEVELOPMENT.md`.

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
optional_apps = ["weather"]  # soft integrations; absence is tolerated

[provides.cli]
commands = ["myapp"]

[provides.web]
prefix = "/myapp"

[provides.events]
emits = ["myapp:done", "myapp:progress"]
internal = ["myapp:progress"]   # subset of emits — no cross-app listener by design
```

Declare every literal `self.call_app("id", ...)` target: load-time dependencies in `apps`, graceful-fallback integrations in `optional_apps`. Declare every event the code emits — the topology graph builds edges from manifests, so an undeclared emit is invisible to `/api/topology`. **`internal` modifies `emits`, it is not an alternative**: a name listed only under `internal` produces no node and no edge. `scripts/check_event_wiring.py` reports drift.

### App Sub-Patterns

Each has a rule file with full examples; read it before building. Most load automatically when you touch `apps/**`.

| Pattern | When | Reference |
|---|---|---|
| Settings panel | `[provides.settings]` → ⚙ button + `EOS_UI.settingsPanel()`; read back with `setting_or_config` | `app-ui-patterns.md` |
| Hash-route detail view | `showDetail(id)` → `EOS_UI.hashRoute()` | `app-ui-patterns.md` |
| 4D timeline panel | entity detail with past + future → `[provides.timeline]` | `app-ui-patterns.md` |
| Addons | user-extensible URL slots | `addons.md` |
| Hub panels | glanceable signal on `/hub/` | `hub-panels.md` |
| Voice intents | verb callable from Aura | `voice-intents.md`, `verb-registry.md` |
| Field-suggest | ✨ vault-grounded suggestions on a form input; propose-not-autofill | `field-suggest.md` |
| Boards as view layer | render data another app owns | `boards-as-view-layer.md` |
| App report | calculation sheet / study record as markdown → `[provides.report]` (not data exports) | `app-reports.md` |
| Standalone export | run without the daemon → `[provides.export]` | `app-conventions-for-export.md` |
| Tour steps | appear in the product tour | `tour-steps.md` |
| Private to repo | never in a public/demo deployment → `[app] private = true` | `demo-mode.md` |
| Multi-CLI participants | spawn claude-cli/codex/gemini per @-mention | `multi-cli-participants.md` |
| Slash command palette | 4+ keyboard-first verbs in one input | `slash-command-palette.md` |
| Room review gate | `[DO:app.method(...)]` tokens needing Apply/Reject | `room-review-gate.md`, `proposed-action.md` |
| Multi-module decomposition | `app.py` past ~1200L → helper modules + bindings | `multi-module-apps.md` |

(All paths under `.claude/rules/`.) Debug surfaces: `/hub/debug/panels`, `/voice-assistant/debug/intents`.

## Web & CLI Access


```bash
# Outbound — prints the plan (recipient, each attachment + size) then asks y/N.
# --dry-run previews without sending; --json refuses without --yes.
eos send you@example.com -s "Subject" --body-file note.txt -a dist/thing.zip
```

CLI detects running daemon → proxies via HTTP (shared kernel). No daemon → falls back to local kernel.

## Development Rules


1. **Apps use capabilities, never direct tools** — `self.read()` not `open()`
2. **Human is always the final fallback** — in interactive mode
3. **All apps are equal** — no runtime tiers, same manifest/lifecycle (`release.toml` tiers are for release bundling only)
4. **Apps declare, platform provides** — manifest dependencies validated on load
5. **Events over imports** — apps communicate via bus, not coupling
6. **Self-documenting** — improve `eos app info`, don't write READMEs
7. **Auto-UI is the default; `pages/index.html` overrides** — new CRUD apps boot with a working UI from `emptyos/web/auto_ui.py`. Write a custom page only when the surface isn't a list or auto-UI's affordances aren't enough
8. **Vault-map driven** — `{vault}/30_Resources/EmptyOS/_vault-map.toml` declares where each app's data lives. Apps read via `self.vault_config("key")`, never hardcode. `self.vault_write()` → `{vault}/30_Resources/EmptyOS/{app}/`. `self.search()` reads the whole vault.
9. **Extract shared, then reuse** — build specific first in one app, extract to `sdk/` when a second app needs it
10. **Everything self-testable** — if you can't test it from `localhost:9000` or CLI, it's not done
11. **Reactive vault population** — user actions emit events (e.g. `git:saved`, `capture:saved`); `apps/public/standard/reactor/` chains them into journal entries + related notes
12. **Prompts are first-class artifacts** — named `UPPERCASE` module constants; always `system=` for persona/rules; every content prompt says what NOT to do; temperature parsing 0.1–0.3, analysis 0.3–0.5, creative 0.6–0.8; no thin prompts for user-facing content. User-tunable prompts register via `declare_prompts()` + `[provides.prompts]` (`.claude/rules/prompt-management.md`)
13. **No personal data in committed code** — no personal paths, names, coordinates, API keys in git-tracked files. Machine config goes in `emptyos.toml` (gitignored); machine-specific files under `.claude/` must be gitignored. One gate, `scripts/check-personal.py` (the pre-commit hook): personal shapes in `.eos-personal`, credential shapes in `SECRET_PATTERNS` — **never add a key regex to `.eos-personal`** (self-allowlisted). Git-tracked no longer implies published (`engines/personal/`, `tests/personal/` are pruned from snapshots). Detail → `.claude/rules/personal-data-gate.md`
14. **No third-party branding in user-facing text** — App UIs, prompts, error messages must not mention Obsidian/Suno/Kindle/etc. Use generic terms ("markdown vault", "source URL", "Open external"). Plugin code integrating a specific service may. `.eos-branding` + `scripts/check-branding.py` enforce.
15. **Community apps, personal config** — `apps/` are generic; machine-specific customization lives in `emptyos.toml` `[apps.<id>]`, read via `self.app_config(key, default)` — operator/machine values only; anything the user tunes is a declared setting (§ App Development Pattern). VaultLibrary accepts `extra_fields` from config. `apps/personal/` is for apps whose **logic** is personal. Shipped **defaults** (seed content, templates, fallback hostnames) must read as generic product content — the author's own narrative or infrastructure there is misfiled config.
16. **Wellbeing wheel as design lens** — the 8 dimensions (physical, social, intellectual, emotional, spiritual, environmental, financial, occupational) are a rubric applied in reasoning, **never a feature added to UIs**. Silently prefer suggestions that feed thin dimensions over already-dominant ones (typically occupational/intellectual); resist dimension pickers, tag prompts, or wheel displays. Canonical list: `emptyos/sdk/dimensions.py`; passive metadata in manifests (`dimensions = [...]`).
17. **No localhost assumptions in app logic** — never hardcode `localhost`, `127.0.0.1`, or ports; host/port come from `[network]` config (`network.mode` = `local` / `private` / `public`).
18. **Cloud consent is mandatory** — a provider is `is_cloud=True` when it declares `trust` other than `"owned"`, or (undeclared) when its host is not localhost/private-IP; it must pass the consent gate in `Capability.execute()`. **Declare `trust` for any compute not on hardware you own** — a rented Tailscale GPU looks local otherwise (`.claude/rules/rented-compute.md`). Never add cloud-specific code paths in apps.
19. **No vault data to cloud by default** — cloud providers receive prompts, not raw vault content, unless explicit per-request or via an opt-in config flag. Never embed large vault excerpts in system prompts that hit cloud.
20. **Docker-bootable** — must work from `docker run -v /vault:/vault -v ./emptyos.toml:/app/emptyos.toml emptyos`. No hardcoded absolute paths, no host-OS assumptions, no Windows-only code in runtime paths.

## Vault Data Layer


Vault is the source of truth for app data. **VaultIndex** (`emptyos/runtime/vault_index.py`) indexes all vault markdown in memory on startup (~800ms for 3000+ files), updates incrementally via `vault:changed`.

A note has three layers: **frontmatter** (structured, indexed, queryable), **`##` sections** (semi-structured, names indexed — e.g. `## Timeline` with `- 2026-04-02 — interview` bullets), and **prose** (unstructured, LLM-summarizable).

| Method | Layer | Purpose |
|---|---|---|
| `vault_query(tags, **props)` | 1 | Find notes by tags + frontmatter properties |
| `vault_get_properties(path)` / `vault_update(path, props)` | 1 | Read / mutate frontmatter |
| `vault_sections(path)` | 2 | List `##` section names |
| `vault_read_section` / `vault_append_section` / `vault_set_section` | 2+3 | Read / append / replace a `##` section (preserves frontmatter) |
| `vault_read_body(path)` | 3 | Everything after frontmatter |
| `vault_create_note(path, fm, body)` | all | Create new note |

**Soft schema.** Tags in frontmatter identify note types (`job-application`, `person`, `daily`, `song`); folder never determines type. No hard schema — notes stay hand-editable. App-managed note types may use `VaultModel` (`emptyos/sdk/vault_model.py`): coerces YAML strings at the write boundary, round-trips unknown fields, maps legacy keys, validates on write, fails soft on read (`docs/SOFT-SCHEMA.md`). Two access patterns coexist — **VaultIndex** (target) and **vault_config + file I/O** (legacy); migrate an app when touched, and only when its notes have queryable frontmatter — never silently return empty data.

Markdown profile: `docs/EOS-MARKDOWN-PROFILE.md`. Vault operations and connection state: `.claude/rules/vault-operator.md`.

## Career / Job Search Workflow


**Read the tracker before researching anything.** Before researching an employer, recruiter, or role — and before drafting any reply about one — read the job tracker in the vault first. Live threads, prior contact, and already-established facts live there. `check_career_verdict` is the pre-check for company research — run it by hand; nothing enforces it.

**Roster and org claims need a citation.** Who works at a company, who reports to whom, and what a named person's role is each need a real source (tracker entry, a page you actually read, a prior note). Tag it or ask; never smooth an unsourced roster claim into confident prose.

Drafting the outbound message follows § Writing & Tone; the hard geographic and strategic gates live in the `life-job-evaluator` / `life-strategic-advisor` skills.

## Deployment


Five lanes, documented in `docs/DEPLOYMENT.md`: **1 Service** (HTTP container, no vault — `services/<name>/` + `scripts/deploy-service.sh`), **2 Daemon** (full EmptyOS, vault mounted — `docker-compose.yml` + `scripts/redeploy-demo.sh`), **3 Static site** (`eos publish deploy`), **4 Bundled product** (future, `profiles/<name>/profile.toml`), **5 Multi-tenant SaaS** (future). Decision rule: **service has no vault, daemon has a vault** — bridge through a daemon rather than giving a service vault access.

**Demo vs public vs private:** `network.mode = "public"`, `demo.enabled`, `[trust] web`, and `.eos-personal` + `[app] private = true` are four orthogonal knobs — `.claude/rules/demo-mode.md`.

**Auth model:** the daemon is single-user by design — `auth_token` (machine) + `password` (human), a network gate not an identity system. Multi-user collaboration is the separate `commons` service; a multi-tenant product is daemon-per-user + the control-plane + the commons. 1 vault = 1 user; never grow a users table inside the daemon (`docs/AUTH.md`).

**Operator vs user posture:** `[trust] web = "operator" | "user"` (a boot-fixed deployment fact, never a per-request role) decides whether the browser user is the machine's operator. In `user` posture (public demo, hosted learner) operator-only routes (host files, config, network, plugins, code install, generic dispatch) are refused and `read`/`write` are confined to the vault. A new core route must be classified in `emptyos/posture.py` `OPERATOR_ROUTES` or the reviewed user set (gated by `scripts/check_route_posture.py`); an app route marks itself `@web_route(..., operator=True)`. Full contract: `docs/AUTH.md` § Operator vs user + `.claude/rules/operator-posture.md`.

## Conversation stack


5 backends × 8 frontends. Four families — **TOOL LOOP** (`agent`: `/agent/`, `/code/`, `eos chat`), **CONVERSATION** (`rooms` with the `[DO:]` review gate, `assistant` for vault-aware Q&A), **VOICE** (`voice-assistant`/Aura + phone PWA), **CRON** (`staff`: scheduled + on-demand agents, HITL approvals). Don't merge across families — audited 2026-05-16, each has unique value. Full layout: `docs/CONVERSATION-STACK.md`.

## Tech Stack


Python 3.12+, FastAPI, Typer, Rich, aiohttp, SQLite, APScheduler, watchfiles.

## Dev Log


| Layer | Where | When |
|---|---|---|
| **Breadcrumbs** (reactor) | Daily journal note (`50_Journal/`) | Automatic — `git:saved` ripples |
| **Session summaries** (`/devlog`) | `10_Projects/emptyos/log/YYYY-MM-DD.md` | End of session |
| **Raw history** | `git log` | Every commit |

At session end for meaningful changes, invoke `/eos-session-wrapup` (`.claude/rules/docs-sync.md`).

## Working Style


- **Long output goes to a file, not the chat.** Audits, reports, generated content, and full file dumps are written to a file (an HTML report, a vault note, a `docs/` file) and referenced by path — never printed inline. Blown output caps silently lose whole sessions. Analysis, tradeoffs, and reasoning still belong in the reply.
- **Front-load naming + keybinding decisions.** Before renaming anything or assigning a shortcut, list the proposals, check conflicts (existing names, `GET /api/shortcuts`, `.claude/rules/shared-frontend.md`), and wait for approval. See `[[feedback_front_load_naming_keybinding]]`.
- **Prose answers are structured for a terminal, not a page.** Paragraphs ~3 sentences, each section opens with a `##` header or bold lead-in, multi-part answers broken into named sections. Reading happens in WezTerm; an unbroken wall of prose has no landmarks. Formatting rule, not a brevity rule.
- **Use the full markdown range, and don't lean on bold alone to carry structure.** Headings, tables, fenced code with a language tag, blockquotes, nested lists. **Bold is nearly indistinguishable from body text in this terminal theme** — load-bearing distinctions get a heading, table row, or list item.

## Writing & Tone


Applies to anything the user will send or publish — recruiter replies, outreach, internal work email, blog posts.

- **Lead with capability, not caveats.** Never open with "I don't have X", "I haven't done Y", or apologetic / gate-keeping framing. State what he *can* do, then qualify inside the message if genuinely needed.
- **Assume his claimed experience is true, and verify before pushing back.** If a claim looks like an overclaim, read the vault/project records first (the OHL EMF/earthing claim is the standing example — the pushback was wrong). Challenge only *after* checking, and cite what you checked.
- Voice, scenario plays, and channel/tone selection live in the user-global `life-communication-written` skill; blog gate checks run before publishing.

## Git / Version Control


- **POSIX only in the Bash tool.** It is Git Bash, not PowerShell — never use PowerShell here-strings (`@'…'@`) when committing there.
- **Never put a backtick in a double-quoted commit message** — Git Bash runs it as command substitution and silently drops text. Use a **single-quoted heredoc** (`git commit -F- <<'MSG'` … `MSG`); an unquoted `<<MSG` still expands. Hook-enforced by `scripts/guard_git_safety.py`, which also blocks `git add -A` / `git add .` / `git commit -am`.
- **Check for parallel-session work before staging.** Run `git status --short`, stage/commit **only** your task's files, never commit another session's uncommitted changes, and verify `HEAD` is yours afterwards. Full rule: `.claude/rules/environment.md` § Parallel-session staging.

## EmptyOS Workflow


A feature request implies the **whole loop by default** — don't stop at "code written" and wait to be told to continue. Scope down only when the user explicitly narrows it or a step can't apply (no UI → skip the walk). The loop is **build → conform → walk → simplify → adversarial review → commit → live-verify**:

1. **Build** the feature (app / plugin / engine / SDK).
2. **Conform** — write system tests (`tests/test_sys_<app>.py`) and run the relevant slice (`.claude/rules/testing.md`), then **mutation-verify** each new test with `eos-mutation-verify`: break what it names, watch it go red. A test that has never been red pins nothing.
3. **UI walk** — exercise the real surface in a browser with screenshots when the UI changed (`/eos-ui-walk`).
4. **Simplify** — `/eos-simplify` against EmptyOS conventions; apply the flagged fixes.
5. **Adversarial review** — `/eos-adversarial-review`: hostile reviewers hunt six defect classes (listed in the skill); it runs after simplify so reviewers spend attention on substance. A finding that says "this broken version still passes" becomes a new test plus a mutation row, re-run until red. Every finding ends fixed or waived **with a written reason**, recorded with `python scripts/review_receipt.py write` — that is what opens the commit gate (`scripts/guard_adversarial_review.py`).
6. **Commit** with a scoped message (§ Git / Version Control).
7. **Live-verify** against the daemon — never restart `:9000` yourself; lease a sandbox member (`.claude/rules/sandbox-driven-testing.md`), or ask the user to restart.

Full loop: `docs/ENGINEERING-WORK-LOOP.md`; autonomous form: `.claude/rules/test-fix-verify-loop.md`.

## Autonomous Loops


A "continue the loop" instruction means: **work until the current tranche is verified, tested, and committed, then STOP and report.** A tranche is what the user named (or one coherent unit of work) — not the whole backlog.

- **An explicit stop wins over everything.** A hook, a queued item, or a backlog count never overrides it; if a hook keeps re-firing after a stop, say so and halt.
- **A remaining-item count is never a termination condition.** Loops terminate on a *stated* budget (N items, a time box, or an executable check).
- **Report at the stop**: what landed, what was skipped and why, and the exact state a follower needs to resume.

## Repo Evaluation


Evaluating an external repo/tool for borrowing: **first check for a closed verdict** — `python scripts/check_borrow_verdict.py <name>` (exit 1 = a verdict exists in `docs/OPEN-SOURCE-BORROWING-PLAN.md` / `docs/DEFERRED-WORK.md`; read it and stop). Then **ground every claim in the real source**, never the README or memory. Record the build / borrow / build-nothing decision and why; a deferral also gets a `docs/DEFERRED-WORK.md` row with its trigger. The disciplined form is `/eos-repo-extract`.

**Any conclusion drawn across many sources** — insights, audits, "state/trajectory of X", "why does Y recur" — follows `.claude/rules/deep-research.md`: baseline → first-pass → gap-pick → deep-read → refute → grade evidence.

## Testing


Tests (pytest + Playwright + `node --test` for browser JS) cover apps, UI components, user stories, accessibility, visual baselines. When to run what: `.claude/rules/testing.md`; how to write tests: `.claude/rules/test-authoring.md`. Fixtures in `tests/conftest.py`, assertions in `tests/helpers.py`.

```bash
python -m pytest tests/ --ignore=tests/personal -v    # CI / release-safe
python -m pytest tests/test_sys_<app>.py -v           # single app (after UI change)
python -m pytest tests/ -k "not test_ui" -v           # API-only fast path
python -m pytest -m "dogfood and not llm" -v          # dogfood — "is it usable?"
```

- **Always `python -m pytest`, never bare `pytest`** — the binary may resolve to a different Python and silently lose the playwright plugin.
- Daemon-backed tests need `:9000`. Test data uses `TEST_PREFIX = "PLAYWRIGHT-TEST-"`, cleaned by a session autouse fixture. Release/CI runs pass `--timeout=60 --reruns 2`.
- Four layers, four questions — **system** (`test_sys_<app>.py`: does each endpoint work?), **user story** (`test_user_stories.py`: one deep per-app flow), **journey** (`test_journeys.py`: cross-app event chains), **dogfood** (`test_dogfood*.py`: usable for a week?; LLM steps marked `@pytest.mark.llm`). Don't duplicate across layers: dogfood is narrative + ordered + state-threading and earns its keep only when it spans ≥2 apps or catches aggregation bugs endpoint tests miss — below that bar, `test_user_stories.py` is the home.
- A tracked test must never import a gitignored `apps/personal/` module at module scope — CI's bare `--collect-only` aborts the whole run.
- Test-fix-verify loop (dogfood-agent → fix-agent → sandbox `:9001` → verifier): `.claude/rules/test-fix-verify-loop.md`.

## External Service Launch Pattern


Ollama, ComfyUI, voice-api and Blender launch from embedded runtimes: **always** set `cwd` to the service dir, **never** `start /min` or `cmd /c start` (both open windows). Full pattern: `.claude/rules/external-service-launch.md`.

## Development Gotchas


The architecturally load-bearing ones. Generic quirks: `.claude/rules/dev-gotchas.md`.

- **Vault read-modify-write races**: any `read → mutate → write` yields the loop between awaits, and the reactor subscribes ~30 events, so a POST and a handler can clobber each other. Serialize a **vault note** with `async with self.note_lock(path)` (kernel-wide, keyed by vault-relative path — excludes across apps); `self.write_lock(key)` is per-app-instance, for everything else. Journal's `_daily_lock()` is the reference; `vault_set_body` and quick-action/expense/learn/people take `note_lock`, and `VaultIndex` logs a `race candidate` warning when a write lands under another task's lock. Keep emits **outside** the lock so handlers can recurse through `call_app` without deadlocking.
- **Vault frontmatter tags must be block-style**: `tags:\n  - a\n  - b`, not `tags: [a, b]`. One parser, `emptyos/frontmatter.py`: `key:` and `key: ""` read `""`, `key: []` reads `[]` — `str([])` is the truthy `"[]"`, which is how an unfilled field used to pass `if not value`.
- **Normalize loose field shapes at the write boundary**: when callers pass a string where `dict | None` is typed, coerce once in the write function with a small `_coerce_<field>()` helper, not at every read site.
- **The daemon process is user-owned**: never run `restart.bat`/`stop.bat`, `python -m emptyos start`, `taskkill` python, or delete `data/*.db*` against `:9000` / `:9001`. Probe via `curl`, read `data/daemon.err.log`, surface diagnoses; verify Python changes on a leased sandbox member. `.claude/rules/daemon-handling.md`.

## Shared Frontend


Visual + interaction DNA: `docs/FRONTEND-DESIGN-LANGUAGE.md` (read before touching a page). Shared bundles, geo stack and shortcuts: `.claude/rules/shared-frontend.md` (loads on `apps/**/pages/**`). Audits: `/eos-design-system-audit`.

**Never hand-roll a primitive:**
- **`EOS_UI.statusBadge(label, status, map?)`** for every status/priority chip — never `'eos-badge-status-' + statusVariant(...)` (double-prefixes).
- **`EOS_UI.jsArg(value)`** for any value in a hand-written inline `onclick=` — **never bare `JSON.stringify`**, whose double quotes close the attribute and leave a dead handler (gated by `scripts/check_onclick_args.py`). `EOS_UI.entityCard({onClick})` is the opposite sink and wants a bare `JSON.stringify` — never copy a handler between them.
- **Status is never conveyed by colour alone** — pair it with a word (`.claude/rules/list-card-density.md`).
- Vault paths are always clickable via `EOS.noteActions(path)`.

## Session Housekeeping


**Assume another session is running.** Before writing to any shared file — devlog, ledger, `docs/`, `_next/` briefs, `MEMORY.md` — re-read it immediately before the write and **append** rather than overwrite. Stage and commit in one chained command; never `git add -A`.

**Close ledger entries by exact id, never by fuzzy or substring match** — a substring close once shut three unrelated entries. Verify the count you closed matches the count you intended.

## Session Continuation


```bash
python -m emptyos          # System status
python -m emptyos health   # Full health check
```

`/eos-session-resume` reads the per-track brief index at `{vault}/10_Projects/emptyos/log/_next/_index.md` (written by the previous `/eos-session-wrapup`). **Bounded work** (≥3 ordered tasks) runs off a plan file in `{vault}/10_Projects/emptyos/log/_plans/` — resume claims one task by setting its row to `active` with a `YYYY-MM-DD #sid8` session cell, wrapup closes it; plans carry the *task*, `_next/` briefs carry the *story* (`.claude/rules/session-plans.md`). For recent work, use `git log` and `10_Projects/emptyos/log/`.

**Reading this file means you're in conversation mode** — the system's most powerful runtime. You can create apps, extract patterns, wire events, and make architectural decisions coherent with the consciousness model.

## Key Files


- `docs/README.md` — documentation index; `docs/DOC-SYSTEM.md` — how the docs are structured
- `docs/DESIGN.md` — architecture, philosophy, consciousness model
- `docs/APPS.md`, `docs/TIERS.md`, `docs/SKILLS.md` — generated catalogs (`scripts/generate_{apps,tiers,skills}_doc.py`)
- `docs/APP-DEVELOPMENT.md`, `docs/FRONTEND-DESIGN-LANGUAGE.md`, `docs/GETTING-STARTED.md`
- `docs/DEFERRED-WORK.md` — deferred features with build triggers; grep it before building something substantive
- `docs/AGENT-FRAMEWORK.md` — a new autonomous agent is *config, not code*; register every loop in `emptyos/sdk/loops.py`
- `AGENTS.md` — non-Claude-Code AI self-config; `apps/public/standard/forge/FORGE.md` — Forge growth charter
- `emptyos.toml` — machine config (gitignored); `restart.bat` — kill python, check external services, boot EmptyOS
- `emptyos/kernel/__init__.py` — kernel boot; `emptyos/sdk/base_app.py` — BaseApp; `emptyos/web/server.py` — FastAPI server + auto-UI + topology
- `emptyos/runtime/vault_index.py` — in-memory vault index; `emptyos/runtime/vault_map.py` — app path discovery + auto-heal
- `emptyos/sdk/vault_library.py` — vault-backed collections; `emptyos/sdk/utils.py` — `parse_llm_json`, `streak_from_dates`, … (re-exports frontmatter helpers — don't redefine them); `emptyos/sdk/srs.py` — FSRS-4.5
- `emptyos/sdk/loops.py` — feedback-loop registry (`eos loops list|show|stages`)
- `emptyos/capabilities/providers/claude_cli.py`, `openai_compat.py` — think providers
- **Top-level stdlib-only modules** — **the kernel never imports `emptyos.sdk` at module level** (it pulls in `base_app`), so helpers both need live here: `nethost.py` (canonicalise hosts before loopback checks), `frontmatter.py` (the one frontmatter parser), `basepath.py` (`resolve_under_base`), `fieldspec.py` (calculator-declaration checker), `composite_score.py` (weighted scoring; three silent-wrong-answer edges), `headless.py` (child processes never open a console window), `speechlang.py` (zh/ja/en for TTS routing), `plan_table.py` (the one session-plan table parser), `gitpaths.py` (what git ignores / tracks, for anything that publishes an app or skill list), `source_inspect.py` (read a module-level literal by AST, no import). Why each exists → `.claude/rules/top-level-modules.md`.

### Rules loaded on demand

`.claude/rules/` holds one file per topic. Files with `paths:` frontmatter load only when you touch matching files (`.claude/rules/path-scoped-rules.md`). **Read these by hand when the task is about them but you haven't touched a matching path:** `proposed-action` (propose/preview/confirm), `autopilot-grants`, `verb-registry`, `authorship-boundary`, `proactive-comms`, `sandbox-driven-testing` + `sandbox-usage` (leasing `:9002+`), `vault-operator`, `media-gotchas` (any MV/podcast/ComfyUI work — vault paths don't trigger it), `web-tool-operation` (driving Flow/ChatGPT/claude.ai), `three-natures-lens`, `self-audit-loops`, `agent-bus` (any `self.think()` needing architecture context), `demo-mode` (`[app] private = true`), `gate-driven-fix-loop`, `parallel-shard-runs`, `dev-cli-dispatch`.
