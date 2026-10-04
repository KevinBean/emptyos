# EmptyOS Chrome Extension

A small Chrome companion that keeps browser presentation and extraction here,
while EmptyOS's local daemon owns reasoning, vault access, agents, and policy.

## V0.11 — Browser Session

Browser Session is an explicit, one-hour connection between selected Chrome
tabs and the EmptyOS daemon. Open the side panel, share one or more tabs, then
choose **Arm**. The status changes to **Connected** when the daemon can issue
typed commands and receive structured results while the panel is closed.

The session supports tab listing and navigation, bounded text snapshots,
screenshots, clicks, fills, key presses, selects, scrolling, waits, and the
existing EmptyOS card/badge/toast surfaces. Use **Extend** to restart the
one-hour timer or **Disarm** to revoke the whole session. If it is not armed,
tab sharing keeps its previous one-message behavior.

The first daemon-led workflow in the panel is **Evaluate shared jobs**. It asks
the daemon to read each armed tab, then sends those snapshots through the
existing job evaluator. Aura also gets a read-only Browser tool for questions
about armed tabs. Full interaction remains available to the Agent app's Browse
tool with `target="user-chrome"`; Playwright remains the default for every
existing caller.

Browser Session is dark by default. Enable it in `emptyos.toml`:

```toml
[apps.agent]
feature.browser-session.enabled = true
```

An older daemon leaves the rest of the extension working and reports that a
daemon update is required when Browser Session is armed.

## What it does

- **Side panel** (`Ctrl+Shift+E`) — chat, capture, tab sharing, reading controls,
  voice, jobs, and Browser Session.
- **Right-click menu** — "Capture page / selection / link to EmptyOS".
- **Ask Aura about this page** — sends the current page's text content to the
  assistant app and opens the resulting session. Works on logged-in / paywalled
  pages that `WebFetch` can't reach.
- **Reading layer** — `Off`, local-on-demand `Ask`, or proactive paid-model
  `Flow`, with cache-first explanations, adaptive hard-word highlights, and an
  in-page margin card. Pause it per site from the side panel.

All extension requests first go to your configured EmptyOS daemon. `Off` sends
nothing. `Ask` checks saved paid answers, then uses a local model only when you
double-click a word. `Flow` sends a capped visible-text excerpt to the paid
provider you select, after EmptyOS shows its cloud-consent gate. Raw page text
is not kept; derived paid explanations are cached locally.

## Optional features (they depend on your daemon's apps)

Chat, capture, tab sharing, Browser Session, and the slash commands work on any
EmptyOS daemon. Three surfaces are backed by apps not every daemon has — notably
the public release, which ships neither `dictionary` nor `jobs`:

| Needs this app | You get |
|---|---|
| `dictionary` | "Look up '…' in EmptyOS dictionary", and the whole Reading layer (`Off`/`Ask`/`Flow`, the rail, per-site pauses) |
| `jobs` | "Evaluate selection / this job posting (vs my CV)", "Capture job posting", the **Capture as job** chip, **Evaluate shared jobs** |
| `video-digest` | "Digest this video" and the YouTube chip |

The extension asks the daemon which apps it serves (`GET /api/apps`) and registers
only the menu items and panel sections that something can answer, so you never see
a verb that can only fail. When it cannot ask — daemon down, no token yet — it
shows everything and individual actions report the error as before. The check
re-runs when you save the options page and when Chrome restarts, so pointing the
extension at a different daemon adapts the menu.

## Install (unpacked)

1. Open `chrome://extensions`
2. Toggle **Developer mode** on (top right)
3. Click **Load unpacked**
4. Pick this folder: `<your-emptyos-clone>/tools/chrome-extension/`
5. Pin the extension to the toolbar (puzzle icon → pin)

## Configure

Open the extension's options page (right-click icon → **Options**):

- **Daemon URL** — `http://localhost:9000` for local mode, or your
  Tailscale / public host.
- **Auth token** — paste `[network] auth_token` from `emptyos.toml`. Required
  when `network.mode` is `private` or `public`; leave blank for `local`.
- **Remember on this device** — off keeps the bearer token in Chrome session
  storage. On stores it locally in extension-only storage. The token is never
  placed in sync storage.

Hit **Test connection** to verify.

## Endpoints used

| Action | Endpoint |
|---|---|
| Capture | `POST /quick-action/api/add` body `{text, tag}` |
| Ask Aura | `POST /assistant/api/chat` body `{message}` |
| Armed-tab snapshots for panel workflows | `POST /assistant/api/browser-session/snapshots` |
| Health check | `GET /api/health` |
| Which apps this daemon serves (feature gating) | `GET /api/apps` |
| Reading status | `GET /dictionary/api/reading/status` |
| Proactive analysis | `POST /dictionary/api/reading/analyze` |
| On-demand local lookup | `POST /dictionary/api/reading/lookup` |
| Reading feedback | `POST /dictionary/api/reading/feedback` |

Browser Session uses the authenticated daemon `/ws` connection rather than a
second transport.

## Permissions and data flow

The installed extension always has access only to the configured loopback
daemon, plus Chrome's `activeTab`, context-menu, storage, scripting, side-panel,
and tab APIs. A site origin is requested when you share a tab. The reading layer
is dynamically registered only on origins you approved; **Flow everywhere** is
a separate, explicit all-sites request.

Page text and action results travel to your configured EmptyOS daemon. Page
content is treated as untrusted data and cannot approve actions or widen tab or
origin scope. Password, payment, and upload fields are blocked. Submissions,
sending, publishing, destructive controls, download links, and tab closure ask
for one-use confirmation in a closed-shadow-DOM overlay.

Screenshots use Chrome's `activeTab` grant. Invoke EmptyOS on the target tab
before requesting one; Chrome otherwise returns
`screenshot_permission_required`. Images are capped at 4 MB. Snapshot text is
capped at 12,000 characters and 250 interactive elements. Audit events record
the session, action, host, outcome, and confirmation status—not page bodies,
typed values, or screenshots.

Non-loopback daemon URLs must use HTTPS/WSS. Native Messaging, debugger access,
history, bookmarks, download management, uploads, arbitrary page JavaScript,
and password/payment filling are intentionally absent.

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
  - **Capture as job** → scrapes company, role, salary, location, JD text from DOM → `POST /jobs/api/applications/add`. Lands as a real application note in your vault.
  - **Digest this video** → `POST /video-digest/api/queue`.
- **"Already captured" badge** — `✓` on the toolbar icon when the current tab's URL is already in your inbox. Polls `/quick-action/api/has?url=...` on tab updates.
- **Right-click "Propose selection as KB clause"** — grabs your selection + its paragraph context + page URL → `POST /assistant/api/propose-kb-note` (wraps `BaseApp.propose_kb_note`). Review/apply in `/rooms/` pending dashboard.
- **Right-click "Capture job posting"** appears on LinkedIn/Seek; **"Digest this video"** appears on YouTube.

## V0.8 — Evaluate job vs CV (in-page score card)

- **Right-click "Evaluate selection as a job (vs my CV)"** (any page, on a selection) or **"Evaluate this job posting (vs my CV)"** (LinkedIn/Seek pages — auto-scrapes the full JD) → `POST /jobs/api/discover/evaluate` `{text}` → scores the JD against your Master CV (weighted-HR scoring when `[apps.jobs] feature.weighted-hr-scoring.enabled`, else legacy). Renders an in-page **score card**: `match %` badge (green ≥70 / amber ≥45 / red), verdict, priority, and top gaps. Needs a Master CV set in Settings (`user.cv_path`) — otherwise the card says so.
- Shares the generic in-page card renderer (`renderResultCard`) with the dictionary lookup — the reusable mechanism is documented in `.claude/rules/browser-extension-bridge.md`.
- **Fix:** `capture-job` now posts to the correct `/jobs/...` prefix (was `/personal/jobs/...`, a silent 404).

## V0.10 — Reading rail, sense-based vocabulary, one settings home

- **The rail.** In `Flow`, a docked panel lists every flagged word currently on
  screen, in reading order, re-listed as you scroll — no clicking. Scanning is
  **viewport-scoped**: a long article's tail used to be invisible to the layer,
  because only the document's first 8,000 characters were ever read. Each new
  region is analysed once, fingerprinted, and never paid for twice.
- **Three sources, cheapest and most-owned first:** the reader's own saved note
  (`yours`) → a cached answer (`cached`) → the model on demand (`new`). Their note
  outranks any model, and the chip says which one answered. Only `yours` means the
  word is in the dictionary — the cache chip used to read `saved`, which is a fact
  about our cost, not about the reader's vocabulary, and it left a Save button under
  a chip that claimed the word was already saved. `Save` is now offered only on a
  word that is not yet theirs.
- **Every verdict is a save.** `I know this` records the word as `known` (not
  drilled — it is already theirs); `Still hard` records it as `learning` and
  enrols it in review; `Save word` does the same. A judged word used to leave no
  trace in the vocabulary at all.
- **The saved note is polished by a strong model.** The card must be fast, so it
  is thin. A saved note is read months later, so on save the chain's strongest
  model produces a real lexical entry — lemma, IPA, inflections, **every distinct
  sense** with its own CEFR level and register, collocations, word family,
  etymology. Fails soft: a model hiccup never costs you the save.
- **Multilingual, and language-switch-safe.** Native and target language are
  settings. Glosses **accumulate per language** on the note (`gloss_zh`,
  `gloss_es`), so switching your native language never orphans your vocabulary.
- **Settings live in the daemon, not the browser.** The extension stores only
  host + token; every preference (mode, display, languages, providers, per-site
  pause) is owned by the dictionary app. Daemon unreachable → the layer stays
  dormant, the only safe default for something that would otherwise send page
  text somewhere.
- **Pronunciation** on the card and every rail row (TTS via `speak`; audio is
  fetched in the worker and handed over as a `data:` URL, because an `<audio src>`
  cannot carry an auth header and the page must never see the token).

## V0.9 — Adaptive reading layer (superseded by V0.10 above)

- **Three explicit modes:** `Off` is fully dormant; `Ask` runs only after a
  double-click; `Flow` proactively highlights likely hard words in visible page
  text. The toolbar badge shows `A` or `F` while a mode is active.
- **Page-safe presentation:** CSS Custom Highlights mark words without wrapping
  or rewriting the host page DOM. The explanation appears as a restrained
  margin, corner, or near-word card. Inputs, editors, code, navigation, links,
  and controls are excluded.
- **Bounded behavior:** at most 8,000 visible characters and six suggestions per
  analysis, a 20-second scan throttle for dynamic pages/captions, a 500-entry
  derived-response cache, and per-site pause. Browser-internal pages, closed
  shadow DOM, canvas/image text, and native desktop apps remain out of scope.

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
