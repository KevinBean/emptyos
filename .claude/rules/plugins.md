# Plugins — Inventory + Patterns

19 plugins. **Service plugins** expose named services — apps access via `self.require("name")`. **Enhancer plugins** inject providers into capabilities at startup (`priority=0`); capability falls back to next provider if plugin is absent/offline. No app code changes — "Graceful Enhancement" pattern. See `docs/DESIGN.md`.

| Plugin | Type | Purpose |
|---|---|---|
| health | service | Heartbeat, capability probes, GPU VRAM monitoring |
| agent-runtime | service | Ephemeral CLI subprocess driver (claude-cli/codex/gemini); one-shot per turn with stdout/stderr drain + tick callback + timeout kill |
| notifications | service | Vault + Telegram push |
| ollama | enhancer (think) | Local LLM |
| comfyui | enhancer (draw) | GPU image/video/music generation |
| openai-image | enhancer (draw + think, cloud-explicit domain) | gpt-image-1 + gpt-4o-mini vision; opt-in only |
| edge-tts | enhancer (speak) | Free in-process TTS via Microsoft Edge voices |
| voice-api | enhancer (speak+listen) | Kokoro + XTTS (TTS) + Whisper (STT), :8602 |
| webcam | enhancer (see) + service | Local camera capture via OpenCV |
| applio | service | AI voice conversion |
| obsidian | service (registers `viewer`) | URI scheme + cross-platform apps for viewing/editing notes |
| telegram | service | Two-way bot — commands + push notifications |
| blender | service | 3D modeling, cable routing, headless rendering |
| global-hotkey | service | Global OS keyboard shortcuts → EventBus |
| system-tray | service | Native system tray icon (Windows/macOS) |
| dogfood-demo | service | Spawns + supervises a parallel daemon on :9001 with `dogfood/emptyos.toml` (throwaway vault, human-only think) for screenshare/test scenarios |
| playwright | enhancer (browse) + service | Headless Chromium for app-driven UI automation (dogfood-agent UI-walk, fix-agent repro loop). Lazy-launches on first call; one browser per daemon, one context per `context_id`. Needs `pip install playwright && playwright install chromium`. |
| sandbox-pool | service | Supervises 2–3 throwaway EmptyOS daemons on `:9002+` that operators (Claude / `/loop` jobs / smoke harnesses) lease, restart between code edits, and release via `/sandbox/api/*`. Each member gets isolated `data/` + `vault/`; recursion guards prevent members from spawning their own pool. The plugin owns every PID it spawns — kills go through `POST /sandbox/api/lease/{id}/restart`, never raw `taskkill`. Paired with `apps/sandbox/` for HTTP + dashboard. See `.claude/rules/sandbox-usage.md`. |
| pronounce | service | Phoneme-level pronunciation scoring via a local wav2vec2 service (registers service `pronounce`) |

## Obsidian is a dependency, not a given

EmptyOS uses the external markdown vault as its "hard drive" (`notes.path`) and Obsidian as the **vault viewer** — cross-platform desktop/mobile apps + the `obsidian://` URI scheme that every `EOS.noteActions()` link uses. The `obsidian` plugin registers service `viewer` and owns the URI templates; swap the plugin (or override its `uri_templates` in config) to switch viewers. Sync is independent — the vault is plain files, so Syncthing/git/iCloud all work without Obsidian Sync. Search runs through EmptyOS's own grep provider against the vault files — no Obsidian CLI dependency.
