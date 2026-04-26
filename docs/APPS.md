# EmptyOS Apps

> 70 apps, 1812 API routes, 69 custom pages, 144 event types.
> All first-class citizens. Same manifest, same lifecycle, same loader.

---

## Part 1: Core (12 apps — daily use, deep backends)

| App | Routes | Page | Capabilities | Connectors | Events (emit/listen) | Settings | Status |
|-----|--------|------|-------------|------------|---------------------|----------|--------|
| **expense** | 20 | custom | think | — | 1/0 | 3 | 95% |
| **briefing** | 20 | custom | read, search, think, speak | voice_api | 1/0 | 2 | 83% |
| **journal** | 11 | custom | read, write, search, think | — | 2/0 | 1 | 100% |
| **task** | 7 | custom | read, write, think | — | 2/0 | 2 | 100% |
| **english** | 13 | custom | read, search, think | — | 1/11 | 1 | 186% |
| **hub** | 7 | custom | think, read, search, speak | voice_api | 1/19 | 0 | 117% |
| **contacts** | 15 | custom | read, write, search, think | — | 1/0 | 2 | 79% |
| **healing** | 13 | custom | read, write, think | — | 1/0 | 2 | 87% |
| **nutrition** | 25 | custom | think | — | 1/0 | 4 | 81% |
| **focus** | 10 | custom | think | — | 1/0 | 4 | 143% |
| **dictionary** | 15 | custom | think, read, write, search | — | 2/0 | 2 | 94% |
| **projects** | 9 | custom | read, write, search, think | — | 5/0 | 1 | 112% |

**Total**: 165 routes, 12 custom pages, 19 emitted event types.
See `APP-SPECS.md` for detailed per-app specs.

---

## Part 2: Voice & English (8 apps)

| App | Routes | Page | Capabilities | Connectors | Events | Status |
|-----|--------|------|-------------|------------|--------|--------|
| **speaking** | 11 | custom | think, read, search, speak, listen | voice_api | 2/0 | New (no HP) |
| **shadowing** | 6 | custom | think, read, speak, listen | voice_api | 2/0 | New (no HP) |
| **voice-review** | 11 | custom | think, read, speak, listen | voice_api | 3/0 | 100% |
| **reader** | 24 | custom | read, write, search, think | — | 2/0 | 58% |
| **tts** | 2 | custom | speak | voice_api | 1/0 | 50% |
| **lessons** | 6 | custom | think, read, write, speak | voice_api | 1/0 | New |
| **interview-studio** | 13 | custom | read, write, search, think | — | 2/0 | 93% |
| **interview-briefing** | 7 | custom | read, search, think | — | 1/0 | 78% |

**Total**: 80 routes, 8 custom pages. Voice apps share `voice_api` connector.

---

## Part 3: Creative (7 apps)

| App | Routes | Page | Capabilities | Connectors | Events | Status |
|-----|--------|------|-------------|------------|--------|--------|
| **compose** | 7 | custom | think | comfyui | 1/0 | 54% |
| **studio** | 2 | custom | think | comfyui | 1/0 | 29% |
| **podcast** | 7 | custom | think, read, write, speak | voice_api | 1/0 | 33% |
| **mv-creator** | 6 | custom | think, read | comfyui | 1/0 | 38% |
| **lyrics** | 1 | custom | think, write | — | 1/0 | 14% |
| **music** | 1 | auto | read | — | 0/0 | 25% |

**Total**: 24 routes. Creative apps share `comfyui` connector. comfyui-app retired → studio (workflows tab).

---

## Part 4: Wellness (5 apps)

| App | Routes | Page | Capabilities | Connectors | Events | Status |
|-----|--------|------|-------------|------------|--------|--------|
| **meditation** | 2 | custom | read, write | — | 1/0 | 100% |
| **divination** | 2 | custom | think, write | — | 1/0 | 100% |
| **quotes** | 1 | custom | think | — | 1/0 | 100% |
| **tracker** | 7 | custom | read, write, search, think | — | 0/0 | 100% |
| **review** | 2 | custom | read, think | — | 1/0 | 67% |

**Total**: 14 routes.

---

## Part 5: Knowledge (6 apps)

| App | Routes | Page | Capabilities | Connectors | Events | Status |
|-----|--------|------|-------------|------------|--------|--------|
| **search** | 3 | custom | read, search, think | — | 1/0 | 100% |
| **assistant** | 2 | custom | think | — | 1/0 | Streaming chat |
| **gpts** | 8 | custom | think, read, write | — | 1/0 | 80% |
| **media** | 1 | custom | read, write, search, think | — | 0/0 | 25% |
| **model-bench** | 3 | custom | think | — | 1/0 | 100% |
| **items** | 10 | custom | read, write, search, think | — | 2/0 | 77% |

**Total**: 27 routes.

---

## Part 6: System (11 apps)

| App | Routes | Page | Capabilities | Connectors | Events | Status |
|-----|--------|------|-------------|------------|--------|--------|
| **settings** | 6 | custom | — | — | 0/0 | 100% |
| **staff** | 8 | custom | think, read, write, search | — | 2/0 | 80% |
| **billing** | 1 | auto | — | — | 0/0 | Core |
| **app-analytics** | 17 | custom | think, search | link | 0/0 | Core (absorbed vault-analytics) |
| **app-gen** | 3 | auto | think, write | — | 1/0 | Core |
| **plugin-gen** | 3 | auto | think, write | — | 1/0 | Core |
| **reactor** | 1 | auto | think | — | 1/34 | Core |
| **system-log** | 2 | custom | read | — | 0/0 | 100% |
| **dashboard** | 5 | custom | read, write, search, think | — | 1/0 | 100% |
| **timeline** | 1 | custom | read, write | — | 0/0 | Core |

**Total**: 32 routes. Reactor listens to 34 events (most-connected listener).

---

## Part 7: Infrastructure (7 apps)

| App | Routes | Page | Capabilities | Events | Notes |
|-----|--------|------|-------------|--------|-------|
| **capture** | 1 | custom | read, write, think | 1/0 | Quick inbox append |
| **note** | 1 | auto | read, write | 2/0 | Note CRUD, fuzzy match |
| **link** | 1 | auto | read, search | 0/0 | Wikilinks, backlinks |
| **tmpl** | 1 | auto | read, write | 1/0 | Template-based creation |
| **run** | 1 | auto | — | 1/0 | Shell execution |
| **git** | 1 | auto | — | 1/0 | Version control |
| **hello-world** | 1 | custom | think | 1/0 | Canonical scaffold |

**Total**: 7 routes. These are platform utilities, not user-facing.

---

## Part 8: Other (2 apps)

| App | Routes | Page | Capabilities | Notes |
|-----|--------|------|-------------|-------|
| **places** | 6 | custom | read, write, search, think | Vault location notes + geocoding |
| **cable** | 6 | custom | — | Pulling tension + HDD footprint (2 modules) |

---

## Summary

| Category | Apps | Routes | Custom Pages |
|----------|------|--------|-------------|
| Core | 12 | 165 | 12 |
| Voice & English | 8 | 80 | 8 |
| Creative | 7 | 29 | 6 |
| Wellness | 5 | 14 | 5 |
| Knowledge | 6 | 27 | 6 |
| System | 11 | 32 | 4 |
| Infrastructure | 7 | 7 | 2 |
| Other | 2 | 9 | 2 |
| **Total** | **58** | **363** | **45** |

---

## Not Yet Built (7 remaining)

| App | What | Blocker |
|-----|------|---------|
| cable-rating | IEC 60287 cable current rating calculator | Needs engineering engine |
| sheath-voltage | Cable sheath voltage analysis (OpenDSS) | Needs engineering engine |
| digital-twin | Power line 3D simulation (Neara-style) | Needs physics engine |
| fiction-engine | Novel/story writing workbench | Port from external service (7700) |
| writing-engine | General writing workbench | Port from external service (7800) |
| talkbuddy | Conversational English practice | Plugin to external service (8600) |
| isla-friends | Virtual pet island with AI characters | Needs game engine |

---

## App Discovery

Apps are auto-discovered from `apps/` directories. Each app is a folder containing:

```
apps/{id}/
  manifest.toml    # Identity, dependencies, provides
  app.py           # BaseApp subclass with @web_route, @cli_command
  pages/           # Optional: custom HTML/JS/CSS frontend
    index.html
```

Drop a folder, restart, it appears. The manifest declares what it needs; the platform validates and provides.
