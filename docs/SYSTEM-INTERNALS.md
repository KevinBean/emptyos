# EmptyOS System Reference

> Kernel services, event system, staff agents, settings schema, plugins, capabilities.
> Source of truth: manifests + kernel code + plugin manifests.

---

## 1. Event System

62 event types emitted across 58 apps. 35 listened to. 29 unheard (emitted but no listener).

### Event Table

| Event | Emitter(s) | Listener(s) | Notes |
|-------|-----------|-------------|-------|
| `expense:added` | expense | hub, reactor | |
| `task:completed` | task | hub, reactor | |
| `task:added` | task | hub, reactor | |
| `journal:entry` | journal | hub, reactor | |
| `journal:created` | journal | hub, reactor | |
| `healing:mood-logged` | healing | hub, reactor | |
| `nutrition:logged` | nutrition | hub, reactor | |
| `focus:completed` | focus | hub, reactor | |
| `contacts:logged` | contacts | hub, reactor | |
| `speaking:session_ended` | speaking | english, hub, reactor | |
| `speaking:session_started` | speaking | — | Unheard |
| `voice-review:analyzed` | voice-review | english | |
| `speak-sharper:analyzed` | voice-review | english, hub, reactor | Refinement analysis |
| `speak-sharper:pattern_detected` | voice-review | — | Unheard |
| `shadowing:attempt` | shadowing | english | |
| `shadowing:perfect` | shadowing | english, reactor | |
| `reader:highlight_added` | reader | english, hub, reactor | |
| `reader:review_completed` | reader | english, hub, reactor | |
| `dictionary:word_saved` | dictionary | english, hub, reactor | |
| `dictionary:word_reviewed` | dictionary | english, reactor | |
| `english:level_up` | english | reactor | |
| `interview:session_started` | interview-studio | — | Unheard |
| `interview:session_ended` | interview-studio | english, hub, reactor | |
| `lesson:generated` | lessons | english, hub, reactor | |
| `meditation:completed` | meditation | hub, reactor | |
| `projects:task_toggled` | projects | hub, reactor | |
| `projects:status_changed` | projects | reactor | |
| `projects:refreshed` | projects | — | Unheard |
| `projects:task_added` | projects | — | Unheard |
| `projects:created` | projects | — | Unheard |
| `briefing:generated` | briefing | reactor | |
| `hub:refreshed` | hub | — | Unheard |
| `capture:saved` | capture | reactor | |
| `note:created` | note | reactor | |
| `note:updated` | note | — | Unheard |
| `items:added` | items | reactor | |
| `items:updated` | items | reactor | |
| `staff:run_started` | staff | — | Unheard |
| `staff:run_completed` | staff | hub, reactor | |
| `compose:generated` | compose | reactor | |
| `studio:generated` | studio | reactor | |
| `podcast:generated` | podcast | reactor | |
| `reactor:action` | reactor | — | Unheard |
| `app-gen:created` | app-gen | — | Unheard |
| `plugin-gen:created` | plugin-gen | — | Unheard |
| `divination:cast` | divination | — | Unheard |
| `lyrics:created` | lyrics | — | Unheard |
| `music:generated` | — | — | Declared but unused |
| `dashboard:generated` | dashboard | — | Unheard |
| `review:completed` | review | — | Unheard |
| `search:query` | search | — | Unheard |
| `interview-briefing:generated` | interview-briefing | — | Unheard |
| `billing:logged` | billing | — | Unheard |
| `assistant:message` | assistant | — | Unheard |
| `gpts:chat` | gpts | — | Unheard |
| `model-bench:completed` | model-bench | — | Unheard |
| `quotes:shown` | quotes | — | Unheard |
| `tts:generated` | tts | — | Unheard |
| `mv:generated` | mv-creator | — | Unheard |
| `comfyui:workflow_completed` | comfyui-app | — | Unheard |
| `tmpl:used` | tmpl | — | Unheard |
| `run:completed` | run | — | Unheard |
| `git:saved` | git | — | Unheard |
| `health:problem:detected` | — | reactor | System event (no app emitter) |
| `vault:changed` | — | reactor | System event (kernel vault watcher) |

### Event Flow

```
                    ┌─────────────────────────────┐
                    │     reactor (34 listeners)    │
                    │  Event chain reactions engine  │
                    └──────────┬──────────────────┘
                               │ listens to all
┌──────────┐  ┌──────────┐  ┌─┴──────────┐  ┌──────────┐
│ expense   │  │ task     │  │ hub (19)    │  │ english  │
│ journal   │  │ healing  │  │ Life dash   │  │ (11 src) │
│ nutrition │  │ contacts │  │ aggregator  │  │ Learning │
│ focus     │→ │ speaking │→ │             │← │ hub      │
│ ...       │  │ ...      │  │             │  │          │
└──────────┘  └──────────┘  └─────────────┘  └──────────┘
    emit          emit         listen+emit       listen
```

**Key listeners by event count:**
- `reactor`: 34 events (chain reaction engine)
- `hub`: 19 events (life dashboard aggregation)
- `english`: 11 events (learning hub from voice apps)

---

## 2. Staff Agents (17 agents)

Agents follow the OBSERVE -> DECIDE -> ACT pipeline. Each agent gathers data from source apps, passes to LLM for decision, then executes actions.

| ID | Name | Schedule | Enabled | Observe Sources | Actions |
|----|------|----------|---------|----------------|---------|
| inbox | Inbox Processor | `0 8 * * *` | yes | capture | capture, note |
| tasks | Task Coordinator | `0 9 * * *` | yes | task | task |
| schedule-sync | Schedule Sync | `0 7 * * *` | yes | briefing | briefing |
| reviewer | Weekly Reviewer | `0 20 * * 5` | yes | journal, task, healing, expense, english | review |
| project-auditor | Project Auditor | `0 18 * * 5` | yes | projects | projects |
| mood | Mood Analyst | `0 22 * * 0` | yes | healing | healing |
| finance | Finance Watchdog | `0 8 1,15 * *` | yes | expense | expense |
| habit-coach | Habit Coach | `0 21 * * *` | no | briefing, english | briefing |
| english-tutor | English Tutor | `0 19 * * *` | no | english, voice-review | english |
| reading-coach | Reading Coach | `0 20 * * *` | no | reader | reader |
| voice-reviewer | Voice Reviewer | `0 22 * * *` | no | voice-review | voice-review |
| job-scout | Job Scout | `0 9 * * 1,3,5` | no | tracker | tracker |
| relationship-keeper | Relationship Keeper | `0 10 * * 1` | no | contacts | contacts |
| news-curator | News Curator | `0 8 * * *` | no | system-log | system-log |
| music-manager | Music Manager | `0 11 * * 2,5` | no | music, lyrics | music |
| dashboard-keeper | Dashboard Keeper | `0 7 * * 1` | no | dashboard, tracker | dashboard |
| content-curator | Content Curator | `0 10 * * 1,4` | no | vault-analytics, link | vault-analytics |

**Execution model:**
```
Cron trigger → OBSERVE (gather data from observe_sources via call_app)
            → DECIDE (LLM with system_prompt + observed data)
            → ACT (call_app on action targets, max 5 actions)
            → REPORT (emit staff:run_completed)
```

**Agent persistence**: `data/apps/staff/agents.json` (config), activity log in memory (max 500 entries).

---

## 3. Settings Schema

36 setting keys across 18 apps. Each app declares settings in its `manifest.toml` under `[provides.settings].schema`. The settings app collects all schemas at runtime.

| App | Keys | Settings |
|-----|------|----------|
| expense | 3 | budget ($3000), default_category (Other), alert_threshold (80%) |
| nutrition | 4 | calories (2000), protein (80g), carbs (250g), fat (65g) |
| focus | 4 | daily_goal (4), work_min (25), break_min (5), long_break_min (15) |
| task | 2 | zombie_days (90), focus_top_n (3) |
| contacts | 2 | overdue_alert (on), birthday_alert_days (7) |
| healing | 2 | care_threshold (4), mood_reminder (on) |
| briefing | 2 | auto_weather (on), morning_time (10) |
| dictionary | 2 | srs_new_per_day (5), quiz_count (5) |
| speaking | 2 | default_tutor (emma), default_scenario (free) |
| shadowing | 2 | default_difficulty (intermediate), perfect_threshold (0.95) |
| tracker | 2 | visa_expiry, pr_submitted (from personal-defaults) |
| gpts | 2 | default_model (ollama), history_limit (20) |
| reader | 2 | daily_review_limit (8), default_ease (2.5) |
| english | 1 | target_hours (1.0) |
| journal | 1 | default_mood (good) |
| projects | 1 | stale_days (90) |
| voice-review | 1 | default_mode (natural) |
| staff | 1 | auto_run (on) |

**Setting types**: `number`, `select` (with options array), `toggle` (boolean), `text`.

**How it works**: Apps declare `[provides.settings].schema` in manifest -> settings app reads all manifests -> renders unified settings page -> values stored in `data/settings.json`.

---

## 4. Kernel Services

| Service | Module | Purpose | Status |
|---------|--------|---------|--------|
| **Vault Watcher** | `emptyos/runtime/vault_watcher.py` | Monitors vault file changes, emits `vault:changed` events | Live |
| **Scheduler** | `emptyos/runtime/scheduler.py` | APScheduler cron for staff agents | Live |
| **Real-time** | `emptyos/runtime/realtime.py` | WebSocket server, EventBus -> browser push | Live |
| **Compute Workers** | `emptyos/runtime/workers.py` | GPU job queue, background tasks | Live |
| **Settings** | `emptyos/runtime/settings.py` | Key-value store, manifest schema collection | Live |
| **EventBus** | `emptyos/kernel/events.py` | SQLite-backed event bus, emit/listen/history | Live |
| **ServiceRegistry** | `emptyos/kernel/services.py` | Service discovery, health status | Live |
| **AppLoader** | `emptyos/kernel/app_loader.py` | Manifest parsing, dependency ordering, lifecycle | Live |
| **PluginLoader** | `emptyos/kernel/plugin_loader.py` | Plugin discovery, loaded before apps | Live |
| **Config** | `emptyos/kernel/config.py` | `emptyos.toml` reader, machine-specific settings | Live |

### Boot Sequence

```
1. Config loads emptyos.toml
2. EventBus initializes (SQLite)
3. ServiceRegistry starts
4. PluginLoader discovers + loads plugins/
5. Capability providers registered (think, read, write, search, speak, listen, draw)
6. AppLoader discovers + dependency-orders + loads apps/
7. Runtime services start (vault watcher, scheduler, realtime, workers)
8. FastAPI server starts on port 9000
```

---

## 5. Plugins (6)

| Plugin | Service ID | Tags | Connects To |
|--------|-----------|------|-------------|
| **health** | `health` | system, monitoring | Internal (capability probes, app health) |
| **notifications** | `notifications` | system, messaging | Vault file + Telegram (when configured) |
| **ollama** | `ollama` | llm, local | localhost:11434 (local LLM inference) |
| **comfyui** | `comfyui` | gpu, image | localhost:8188 (image/video generation) |
| **voice-api** | `voice_api` | audio, tts, stt | localhost:8601 (F5-TTS + Whisper STT) |
| **applio** | `applio` | audio, voice-conversion | localhost:6969 (AI voice conversion) |

**Plugin lifecycle**: Plugins are discovered from `plugins/` directories, loaded BEFORE apps. Each registers a service that apps can declare as a connector dependency.

**Default plugins** (always loaded): health, notifications.

---

## 6. Capabilities (7)

| Capability | Providers | Apps Using | Domain Routing |
|-----------|-----------|------------|----------------|
| **think** | ollama, openai, claude-cli, human | 37 | text, code, reason |
| **read** | filesystem, human | 31 | — |
| **search** | grep, human | 19 | — |
| **write** | filesystem, human | 16 | — |
| **speak** | voice-api, human | 7 | — |
| **listen** | voice-api, human | 4 | — |
| **draw** | comfyui, human | 1 | — |

**Provider chain**: Each capability tries providers in order. Human is always the final fallback.

```
think:   ollama -> openai -> claude-cli -> human
read:    filesystem -> human
write:   filesystem -> human
search:  grep -> semantic -> human
speak:   voice-api -> human
listen:  voice-api -> human
draw:    comfyui -> human
```

**Connector usage** (declared in manifests):
- `voice_api`: 9 apps (speaking, shadowing, voice-review, tts, lessons, podcast, hub, briefing, compose)
- `comfyui`: 4 apps (compose, studio, mv-creator, comfyui-app)

**Service usage**:
- `workers`: 3 apps (compute-intensive tasks)

---

## 7. Port Assignment

| Port | Service | Purpose |
|------|---------|---------|
| **9000** | **EmptyOS** | Kernel + all apps + WebSocket |

### External Services (separate processes)

| Port | Service | Connection Method |
|------|---------|-------------------|
| 11434 | Ollama | `ollama` plugin -> `think` provider |
| 8188 | ComfyUI | `comfyui` plugin -> `draw` provider |
| 8601 | Voice API | `voice-api` plugin -> `speak`/`listen` providers |
| 6969 | Applio | `applio` plugin |
| 8600 | TalkBuddy | Not yet absorbed |
| 7700 | Fiction Engine | Not yet absorbed |
| 7800 | Writing Engine | Not yet absorbed |

---

## 8. Auto-Clustering

The topology graph determines how apps are organized on the home screen. Edge weights:

| Edge Type | Weight | Example |
|-----------|--------|---------|
| `call_app()` dependency | 5 | hub -> expense, briefing -> task |
| Event flow (emit -> listen) | 4 | speaking -> english |
| Shared connector | 3 | compose + studio (both comfyui) |
| Shared required app | 2 | focus + briefing (both need task) |
| Rare capability (speak/listen/draw) | 2 | speaking + shadowing (both speak) |
| Common capability (think/read/write/search) | 0.5 | Weak signal |

Clusters emerge from label propagation on this weighted graph. Each cluster becomes a screen in the UI. When a new app is added, it joins the nearest cluster via its manifest edges.

---

## 9. Topology Stats

| Metric | Value |
|--------|-------|
| Total apps | 58 |
| Total API routes | 361 |
| Custom pages | 45 |
| Event types emitted | 62 |
| Event types listened | 35 |
| Unheard events | 29 |
| Capabilities | 7 |
| Plugins | 6 |
| Settings keys | 36 |
| Staff agents | 17 (7 enabled) |
