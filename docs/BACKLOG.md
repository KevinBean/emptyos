# EmptyOS Development Backlog

Prioritized list of remaining work. Updated as items are completed.

## High Priority (next session)

- [ ] **Speaking: test local-fast Whisper path end-to-end** — the misleading "local-fast" tile (which actually used Chrome's Web Speech API → Google) was split into two honestly-labelled modes: `local-fast` (MediaRecorder → voice-api Whisper :8602 → Ollama stream → Kokoro TTS, fully on-device) and `browser-stt` (Chrome SR → Google, kept for the zero-setup path). Provenance chips on every pipeline tile show whether each step is 🔒 local or ☁ cloud. Still-to-verify: real-device round trip of the new `local-fast` flow (mic → upload → `/api/stt?provider=whisper` → `/api/session/converse-stream`), and sane error when voice-api is down (should surface "is voice-api running on :8602?"). Files: `apps/personal/speaking/app.py` (PIPELINE_MODES, `api_stt` provider pin, `api_converse_stream`), `apps/personal/speaking/pages/index.html` (pipe tiles + chips), `apps/personal/speaking/pages/speaking.js` (`lfStart`, `lfStartRecWhisper`, `lfOnRecStopped`), `apps/personal/speaking/pages/speaking.css` (pipe-chip styles). Next: add browser-side VAD to auto-stop after ~1.5s of silence so the UX matches turn-based expectations without requiring the user to tap twice.
- [ ] **Streaming LLM in more apps** — 50 apps use self.think() but only GPTs streams. Add streaming to: briefing AI summary, voice-review refine, hub AI ask, assistant, compose, podcast script generation. Pattern: `StreamingResponse` + `think_stream()` + NDJSON
- [ ] **Test pages visually** — open each of the 59 pages in browser, try real tasks, fix rendering bugs. We keep finding issues (scrollToBottom, wrong IDs) only when actually using
- [ ] **Telegram two-way** — currently push-only. Add: receive messages, parse commands (/expense 25 lunch, /task list, /mood 7), route to apps

## Medium Priority

- [ ] **Remaining 8 migration apps** — cable-rating (needs engineering engine), sheath-voltage, digital-twin (physics engine), fiction-engine (port from 7700), writing-engine (port from 7800), talkbuddy (plugin to 8600), isla-friends (game engine), geodemo
- [ ] **Apple Health sync** — iPhone Shortcuts → iCloud → Obsidian Sync → EmptyOS reads. Or Health Auto Export app → JSON
- [ ] **Wire unheard events** — 14/99 events still unheard. Most are edge cases but some (vault:edited, settings:changed) should trigger reactions
- [ ] **Obsidian CLI enrichment** — backlinks in search results, tag cloud in vault-analytics, orphan detection
- [ ] **Staff agent scheduling** — actually register agents with APScheduler so they auto-run on cron
- [ ] **Error boundaries** — most pages show nothing when an API fails. Add graceful fallbacks

## Low Priority / Polish

- [ ] **Undo/redo** — destructive actions (delete task, delete expense) have no undo
- [ ] **Data validation** — forms don't validate before submitting
- [ ] **Accessibility** — no ARIA labels, no keyboard navigation beyond shortcuts
- [ ] **PWA manifest** — add service worker + manifest.json for installable app
- [ ] **Dark/light auto** — respect OS prefers-color-scheme
- [ ] **Export/backup** — download all app data as JSON zip
- [ ] **Per-app docs** — fill docs/apps/ with deep dives for each app
- [ ] **More GPT agents** — domain-specific: immigration advisor, investment advisor, cooking assistant
- [ ] **Shared component extraction** — pull repeated patterns into eos-components properly
- [ ] **Performance** — lazy-load app pages, cache API responses, optimize vault scans

## Completed (this session)

- [x] Streaming LLM (GPTs chat)
- [x] Telegram plugin + auto-notifications (7 event types)
- [x] Mobile responsive (global CSS rules)
- [x] 8 GPT agents seeded
- [x] 59 pages static-analyzed + ID-fixed
- [x] 42 apps declare settings (71 total)
- [x] Docs rewritten as EmptyOS-native
- [x] Keyboard shortcuts (Ctrl+K, go-to nav)
- [x] Obsidian integration (URI + note viewer/editor)
- [x] App drawer (⋯ button)
- [x] Nav configurable via settings
