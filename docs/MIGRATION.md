# Home Portal -> EmptyOS Migration Map

## Core Principle

**Everything is an app.** There are no "external" or "excluded" services. TalkBuddy, Fiction Engine, Cable Rating, ComfyUI — they're all EmptyOS apps. Some just need richer infrastructure than others.

## The Stack

```
┌──────────────────────────────────────────────────────────┐
│  Apps (ALL first-class, no tiers)                         │
│                                                          │
│  capture  note  task  briefing  dashboard  studio        │
│  fiction-engine  talkbuddy  cable-rating  digital-twin   │
│  compose  podcast  applio  isla-friends  ai-agent  ...   │
├──────────────────────────────────────────────────────────┤
│  Platform Runtime                                        │
│                                                          │
│  Services:    real-time, compute workers, scheduler,     │
│               vault watcher, notifications               │
│  Libraries:   frontend runtime, media pipeline           │
│  Engines:     engineering, physics, geospatial, game     │
│  Connectors:  ollama, comfyui, voice-api, telegram, ...  │
├──────────────────────────────────────────────────────────┤
│  Kernel                                                  │
│  Config, Capabilities (think/read/write/search),         │
│  EventBus, ServiceRegistry, AppLoader, PluginLoader,     │
│  Providers (human, filesystem, grep, openai-compat)      │
└──────────────────────────────────────────────────────────┘
```

---

## Infrastructure Layers (L1)

These sit between kernel and apps. They're NOT apps (users don't open them) and NOT plugins (they don't connect to external services). They're **shared runtimes** that multiple apps depend on — like libc, X11, or ALSA in Linux.

### 1. Frontend Runtime

**What**: Shared web UI framework — themes, components, navigation, responsive layout, iPhone notch handling.

**Why**: Every app with a web UI needs dark theme, mobile support, shared CSS. Without this, each app reinvents the wheel (Home Portal solved this with `theme.css` + `home-nav.js` + `page-shell.js`).

**Provides**:
- Theme system (dark/light, CSS variables)
- Page shell (header, nav, safe areas)
- Shared components (cards, tables, forms, modals)
- App launcher / home screen
- Command palette (Ctrl+K)

**Lives in**: `emptyos/web/frontend/` — served as shared static assets

**Apps that need it**: ALL apps with web UI

### 2. Real-time

**What**: WebSocket server + client library for live updates.

**Why**: Vault watcher pushes file changes, LLM streams tokens, apps push notifications — all need real-time bidirectional communication.

**Provides**:
- WebSocket endpoint (`/ws`)
- EventBus -> WebSocket bridge (server pushes events to browser)
- Client-side JS library for subscribing to event types
- SSE (Server-Sent Events) for simpler streaming

**Lives in**: `emptyos/web/realtime.py` + `emptyos/web/frontend/realtime.js`

**Apps that need it**: TalkBuddy, Digital Twin, AI Agent, dashboard, briefing, any app showing live data

### 3. Compute Workers

**What**: Background task queue for long-running jobs.

**Why**: Image generation takes 30s, audio processing takes minutes, engineering calculations are CPU-heavy. These can't block the web server.

**Provides**:
- Task queue (submit job -> get job ID -> poll/stream result)
- Worker pool management
- GPU resource coordination (one GPU job at a time)
- Job status tracking + events (`job:started`, `job:completed`)
- Web API: `POST /api/jobs`, `GET /api/jobs/{id}`

**Lives in**: `emptyos/kernel/workers.py`

**Apps that need it**: ComfyUI workflows, Applio voice conversion, Cable Rating calculations, podcast generation, MV creation

### 4. Media Pipeline

**What**: Audio/video/image processing chain.

**Why**: Multiple apps need audio recording, TTS, transcription, image manipulation, video assembly. These are complex pipelines with shared dependencies.

**Provides**:
- Audio: record -> transcribe -> analyze -> TTS
- Image: generate -> upscale -> convert -> serve
- Video: frames -> assemble -> encode
- Format conversion utilities
- Media file serving (streaming audio/video)

**Lives in**: `emptyos/media/` (audio.py, image.py, video.py)

**Apps that need it**: TalkBuddy, podcast, compose, applio, mv-creator, voice-review, tts

### 5. Domain Engines (optional, per-domain)

**What**: Pluggable computation libraries for specialized domains.

**Why**: Some apps need domain-specific computation that's too complex for inline code but too specialized to be a general capability.

**Provides** (examples):
- Engineering: cable rating (IEC 60287), sheath voltage, catenary sag
- Geospatial: coordinate transforms, distance calculations, spatial indexing
- Physics: thermal expansion, mechanical loading, electromagnetic fields
- Game: state machines, NPC AI, turn-based logic

**Lives in**: `engines/` folder (like apps/ and plugins/, auto-discovered)

**Apps that need it**: Cable Rating, Sheath Voltage, Digital Twin, GeoDemo, Isla Friends

---

## Classification: Every Home Portal Component

### Plugins (device drivers — connect to external hardware/APIs)

| Plugin | Connects To | Tags |
|---|---|---|
| `ollama` | Ollama LLM (localhost:11434) | llm, local |
| `comfyui` | ComfyUI GPU (localhost:8188) | gpu, image |
| `voice-api` | Voice API (localhost:8601) | audio, tts, stt |
| `telegram` | Telegram Bot API | messaging |
| `google-maps` | Google Maps API | geo |
| `acestep` | ACE-Step music generation | gpu, music |
| `weather` | Weather API | data |

### Capability Providers (make core verbs smarter)

| Provider | Capability | Effect |
|---|---|---|
| `obsidian_cli` | read, write, search | Uses Obsidian's live index |
| `semantic_search` | search | FAISS vector similarity |

### Kernel Services (system infrastructure)

| Service | What |
|---|---|
| Vault watcher | File change -> EventBus |
| Scheduler | Cron jobs (APScheduler) |
| Notifications | Push via telegram plugin |
| Analytics | Usage tracking, LLM billing |

### Apps — Every Single One

All apps are first-class. No tiers. They just declare what they need.

| App | Capabilities | Connectors | Services | Libraries | Engines | Status |
|---|---|---|---|---|---|---|
| capture | read, write | — | — | — | — | DONE |
| note | read, write, search | — | — | — | — | DONE |
| task | read, write, search, think | — | — | — | — | DONE |
| link | read, search | — | — | — | — | DONE |
| tmpl | write | — | — | — | — | DONE |
| run | — | — | — | — | — | DONE |
| git | — | — | — | — | — | DONE |
| briefing | read, think_stream | weather | real-time | frontend | — | TODO |
| journal | read, write | — | — | frontend | — | TODO |
| review | read, write, search, think_stream | — | — | frontend | — | TODO |
| dashboard | read, search, think_stream | — | real-time | frontend | — | TODO |
| search | search | — | — | frontend | — | TODO |
| contacts | read, write, search | — | — | frontend | — | TODO |
| media | read, write, search | — | — | frontend | — | TODO |
| projects | read, search | — | — | frontend | — | TODO |
| tracker | read, search | — | — | frontend | — | TODO |
| timeline | read, search | — | — | frontend | — | TODO |
| healing | read, write, think_stream | — | — | frontend | — | TODO |
| divination | read, write | — | — | frontend | — | TODO |
| meditation | read, write | — | — | frontend | — | TODO |
| expense | read, think | — | — | frontend | — | TODO |
| items | read, write, think | — | — | frontend | — | TODO |
| nutrition | think | — | — | frontend | — | TODO |
| focus | read, think | — | — | frontend | — | TODO |
| dictionary | think | — | — | frontend | — | TODO |
| english | read | — | — | frontend | — | TODO |
| places | read, write | google_maps | — | frontend | — | TODO |
| assistant | think_stream, read | — | real-time | frontend | — | TODO |
| gpts | think_stream | — | real-time | frontend | — | TODO |
| lyrics | think_stream, read | — | — | frontend | — | TODO |
| hub | think_stream, read, search | — | — | frontend | — | TODO |
| interview-studio | think, read | voice_api | — | frontend, media | — | TODO |
| interview-briefing | think, read | — | — | frontend | — | TODO |
| studio | think | comfyui | compute | frontend | — | TODO |
| compose | — | acestep, comfyui | compute | frontend, media | — | TODO |
| mv-creator | think_stream | comfyui | compute | frontend, media | — | TODO |
| podcast | — | voice_api | compute | frontend, media | — | TODO |
| tts | — | voice_api | — | frontend, media | — | TODO |
| voice-review | — | voice_api | real-time | frontend, media | — | TODO |
| speaking | — | voice_api | real-time | frontend, media | — | TODO |
| talkbuddy | think_stream | voice_api | real-time | frontend, media | — | TODO |
| fiction-engine | think_stream, read, write | — | real-time | frontend | — | TODO |
| writing-engine | think_stream, read, write | — | real-time | frontend | — | TODO |
| cable-rating | read, write | — | compute | frontend | engineering | TODO |
| sheath-voltage | read, write | — | compute | frontend | engineering | TODO |
| digital-twin | read | — | real-time, compute | frontend | physics | TODO |
| applio | — | — | compute | frontend, media | — | TODO |
| isla-friends | think_stream | voice_api | real-time, compute | frontend, media | game | TODO |
| geodemo | read | — | — | frontend | geospatial | TODO |
| comfyui-app | think | comfyui | compute | frontend | — | TODO |
| ai-agent | think_stream | — | real-time | frontend | — | TODO |
| settings | — | — | — | frontend | — | TODO |
| vault-analytics | search | — | — | frontend | — | TODO |
| app-analytics | — | — | — | frontend | — | TODO |
| staff | think, run | — | compute | frontend | — | TODO |
| system-log | — | — | real-time | frontend | — | TODO |
| billing | — | — | — | frontend | — | TODO |

---

## Build Sequence

### Phase 1: Remaining Soil (kernel)
- [ ] Vault watcher -> EventBus
- [ ] Scheduler (APScheduler)
- [ ] Notification service

### Phase 2: Frontend Runtime (L1 infra)
- [ ] Theme system (port from Home Portal)
- [ ] Page shell + navigation
- [ ] Shared components
- [ ] App launcher / home screen

### Phase 3: Real-time (L1 infra)
- [ ] WebSocket endpoint
- [ ] EventBus -> WebSocket bridge
- [ ] Client-side JS subscription library

### Phase 4: Apps that need capabilities + frontend only
- [ ] briefing, journal, review, dashboard, search
- [ ] contacts, media, projects, tracker, timeline
- [ ] expense, items, dictionary, healing, divination

### Phase 5: Compute workers + media pipeline
- [ ] Job queue + worker management
- [ ] GPU coordination
- [ ] Audio/image/video processing chains
- [ ] Build ComfyUI, Voice API, ACE-Step connectors

### Phase 6: Apps that need compute + media + connectors
- [ ] studio, compose, mv-creator, podcast, tts
- [ ] voice-review, speaking, interview-studio
- [ ] assistant, gpts, lyrics, hub

### Phase 7: Domain engines + apps that need them
- [ ] Engineering engine -> cable-rating, sheath-voltage
- [ ] Physics engine -> digital-twin
- [ ] Geospatial engine -> geodemo
- [ ] Game engine -> isla-friends

### Phase 8: Apps that need real-time + everything
- [ ] talkbuddy, fiction-engine, writing-engine
- [ ] applio, comfyui-app, ai-agent

### Phase 9: System apps
- [ ] settings, staff, system-log, billing
- [ ] vault-analytics, app-analytics
