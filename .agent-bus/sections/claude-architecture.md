

```
┌──────────────────────────────────────────────────────┐
│  Apps (ALL first-class, no runtime tiers)             │
│  apps/public/{core,standard,labs}/   — OSS, shipped  │
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
