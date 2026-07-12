

```
┌──────────────────────────────────────────────────────┐
│  Apps (ALL first-class, no runtime tiers)             │
│  apps/public/{core,standard,labs}/   — OSS, shipped  │
│  apps/extension/<group>/  — tracked, never public    │
│  apps/personal/  — user apps, gitignored, local      │
├──────────────────────────────────────────────────────┤
│  Platform Runtime                                    │
│  Services:    vault watcher, scheduler, real-time,   │
│               compute workers (GPU job queue)        │
│  Libraries:   frontend (theme.css + eos.js)          │
│  Connectors:  ollama, comfyui, voice-api, obsidian   │
├──────────────────────────────────────────────────────┤
│  Kernel                                              │
│  Config, 16 Capabilities, EventBus, ServiceRegistry, │
│  AppLoader, PluginLoader, WorkerPool, Providers      │
└──────────────────────────────────────────────────────┘
Vault (external) ← mounted via emptyos.toml: notes.path = "D:/YourVault"
```

### 16 Capabilities

| Capability | Providers |
|---|---|
| **think** | ollama, openai, claude-cli, human (domains: text, code, reason) |
| **read** | filesystem, human |
| **write** | filesystem, human |
| **search** | grep, human (domains: `code` → semble BM25+embeddings; `files` → per-OS filename index, opt-in: Everything/mdfind/fd) |
| **speak** | edge-tts (in-process) → openai-tts (cloud) → kokoro → xtts (via voice-api :8602) |
| **listen** | openai-whisper (cloud) → whisper (local, via voice-api :8602) |
| **pronounce** | wav2vec2 phoneme-level scoring via the `pronounce` plugin (local wav2vec2 service). Returns per-phone alignment + weak-phone roll-up. Local-first; no human fallback (a human can't score per-phone accuracy by ear). |
| **draw** | comfyui |
| **animate** | comfyui-ltx (image-to-video via user-supplied workflow JSON); cloud providers (Runway/Luma/Kling) plug in here |
| **model** | robot-modeller app (LLM → CadQuery → URDF via the `cadquery` plugin); cloud providers (Articraft API, MeshyAI) plug in here. Domains: `articulated` (joints) / `static_mesh` (single rigid body). |
| **artifact** | viz app (LLM → standalone HTML in one shot, judged by eye). Shapes: `3d-scene` (Three.js), `immersive-scene` (modern Three.js ESM — PBR + bloom + GLSL), `svg-diagram`, `schematic`, `network-graph` (vis-network), `chart` (Chart.js), `anim-explainer`, `slide-deck` (reveal.js), `math-explainer` (KaTeX), `mermaid`, `game-2d` (Kaboom.js — one-shot playable top-down game). Cloud providers ("render via claude.ai artifacts" backends) plug in here. |
| **see** | webcam (OpenCV, local) → human (upload a file) |
| **browse** | playwright (headless Chromium) — no human fallback; raises when no provider, app catches |
| **send** | email-smtp (outbound email) → human (interactive: deliver by hand). Outbound to third parties → consent gate + leak-scan apply; SMS/Resend/SES plug in here |
| **footage** | footage plugin: pexels → pixabay (keyword → downloaded royalty-free clip, content-addressed cache) → human (supply a file). Dark until an API key is set; cloud providers consent-gated. Consumed by staged-generation pipelines (MV / podcast slideshow). |
| **translate** | `translate` plugin: NLLB-200 (local, deterministic, ~200 langs via CTranslate2) → `llm-translate` (think-backed fallback, any language). No human fallback. English is the single authored language; everything else is derived + cached (`data/i18n/`). Powers the UI-chrome layer (`eos-i18n.js`) + `BaseApp.translate()`. See `emptyos/sdk/i18n.py`. |

### Plugins

Service plugins expose named services (`self.require("name")`); enhancer plugins inject providers into capabilities at startup with graceful fallback. Inventory + Obsidian-dependency clause: `.claude/rules/plugins.md`. Browse `plugins/` for source.
