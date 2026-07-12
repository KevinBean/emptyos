# EmptyOS Chrome Extension

A small unpacked Chrome extension that captures pages, selections, and page
context into EmptyOS without leaving the browser.

## What it does

- **Toolbar popup** (`Ctrl+Shift+E`) — capture the current tab with optional note + tag.
- **Right-click menu** — "Capture page / selection / link to EmptyOS".
- **Ask Aura about this page** — sends the current page's text content to the
  assistant app and opens the resulting session. Works on logged-in / paywalled
  pages that `WebFetch` can't reach.

All requests go to your local EmptyOS daemon — nothing leaves your machine
unless your daemon is on a Tailnet / public host.

## Install (unpacked)

1. Open `chrome://extensions`
2. Toggle **Developer mode** on (top right)
3. Click **Load unpacked**
4. Pick this folder: `D:/emptyos/tools/chrome-extension/`
5. Pin the extension to the toolbar (puzzle icon → pin)

## Configure

Open the extension's options page (right-click icon → **Options**):

- **Daemon URL** — `http://localhost:9000` for local mode, or your
  Tailscale / public host.
- **Auth token** — paste `[network] auth_token` from `emptyos.toml`. Required
  when `network.mode` is `private` or `public`; leave blank for `local`.

Hit **Test connection** to verify.

## Endpoints used

| Action | Endpoint |
|---|---|
| Capture | `POST /quick-action/api/add` body `{text, tag}` |
| Ask Aura | `POST /assistant/api/chat` body `{message}` |
| Health check | `GET /api/health` |

## Icon

The manifest references `icon.png`; drop a 128×128 png in this folder. Chrome
will use a placeholder if it's missing.

## V0.3 features

- **WebSocket streaming** — replies render token-by-token via `/assistant/ws/<sid>`.
- **Slash commands** — type `/` for the palette. Built-in: `/capture`, `/task`, `/journal`, `/find`, `/new`, `/help`.
- **`[DO:]` review-gate cards** — when the model emits `[DO:app.method({...})]` tokens, they render as Apply/Reject cards inline. Apply hits `POST /assistant/api/dispatch`. A small deny-list (`repo.*`, `rooms.write_note`, `publish.deploy`) routes those verbs back to `/rooms/` so their diff/impact previews aren't bypassed.
- **Provenance chip** per assistant turn (🆓/🔒/☁ + provider name).
- **Model pill** in header — click to switch the provider used for `assistant` (writes `think.app.assistant` setting).
- **Session dropdown** — pick a past session to resume, or use **New**.

## V0.4 features (Phase 2 — Browser-as-input)

- **Site-hint chip** above the input — when you're on a LinkedIn/Seek job or a YouTube video, a one-click button appears:
  - **Capture as job** → scrapes company, role, salary, location, JD text from DOM → `POST /personal/jobs/api/applications/add`. Lands as a real application note in your vault.
  - **Digest this video** → `POST /video-digest/api/queue`.
- **"Already captured" badge** — `✓` on the toolbar icon when the current tab's URL is already in your inbox. Polls `/quick-action/api/has?url=...` on tab updates.
- **Right-click "Propose selection as KB clause"** — grabs your selection + its paragraph context + page URL → `POST /assistant/api/propose-kb-note` (wraps `BaseApp.propose_kb_note`). Review/apply in `/rooms/` pending dashboard.
- **Right-click "Capture job posting"** appears on LinkedIn/Seek; **"Digest this video"** appears on YouTube.

## V0.8 — Evaluate job vs CV (in-page score card)

- **Right-click "Evaluate selection as a job (vs my CV)"** (any page, on a selection) or **"Evaluate this job posting (vs my CV)"** (LinkedIn/Seek pages — auto-scrapes the full JD) → `POST /jobs/api/discover/evaluate` `{text}` → scores the JD against your Master CV (weighted-HR scoring when `[apps.jobs] feature.weighted-hr-scoring.enabled`, else legacy). Renders an in-page **score card**: `match %` badge (green ≥70 / amber ≥45 / red), verdict, priority, and top gaps. Needs a Master CV set in Settings (`user.cv_path`) — otherwise the card says so.
- Shares the generic in-page card renderer (`renderResultCard`) with the dictionary lookup — the reusable mechanism is documented in `.claude/rules/browser-extension-bridge.md`.
- **Fix:** `capture-job` now posts to the correct `/jobs/...` prefix (was `/personal/jobs/...`, a silent 404).

## V0.7 — In-page dictionary card + reading context

- **Right-click any selection** → "Look up '<word>' in EmptyOS dictionary". Calls `/dictionary/api/lookup` (uses the cached vault copy if you've looked it up before, otherwise hits the think provider), then auto-saves via `/dictionary/api/save`. Multi-word selections are reduced to the first word.
- **In-page result card** (划词即查) — the definition, phonetic, part of speech, example and Chinese gloss render in a floating card next to the selection (auto-dismiss on click-outside / 12s), instead of only a toolbar badge flash. Badge still flashes `OK` / `ERR` as a fallback.
- **Reading-context capture** (抓词现场·一键回跳) — the word's saved vault note now records the **sentence** it appeared in and a **[Source](url)** back-link (a `## Context` section + `source:` frontmatter), via new `source_url` / `sentence` fields on `/dictionary/api/save`. `extractSelectionSentence` in `content.js` narrows the enclosing block to the one sentence containing the selection.

## V0.5 features (Phase 3 — Voice in panel)

- **Voice mode toggle** — click 🔈 in the header to enable. Becomes 🔊 + accent-colored.
- **Push-to-talk mic** (🎤 next to the input) — uses Web Speech API (`webkitSpeechRecognition`). Click → speak → recognition fills the input in real time. On final result, auto-sends. Click again or press Esc to abort.
- **TTS playback** — after each assistant reply, the body is sent to `POST /assistant/api/tts`, the resulting audio is fetched (with auth) as a blob, then played in the panel. Strips markdown/URLs before speaking; long replies are trimmed to 800 chars.
- **Half-duplex** — mic suspends while TTS plays back (Web Speech has no AEC). Auto-reopens when audio ends so you can keep talking without clicking.
- **Esc** anywhere in the panel interrupts TTS, or stops mic if listening.
- **New audio route** — `GET /assistant/api/audio/{filename}` was missing; landed in `apps/assistant/voice.py`. Without it, TTS responses 404'd.

First-time mic use prompts Chrome's microphone permission dialog. Web Speech API uses Google's STT server — audio leaves your machine. For a privacy-strict mode, run audio through the local voice-api service instead (not built in this cut).

## Roadmap

- Local STT (audio upload → `POST /assistant/api/stt`) for privacy mode.
- Voice intents (`[INTENT:app.verb(...)]` parsing alongside `[DO:]`).
- Real model picker modal (replace the current `window.prompt`).
- Site-hint scrapers maintenance (LinkedIn/Seek redesigns).
