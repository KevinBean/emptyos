# Voice Intents Rule — Apps Contribute Verbs to Aura

A **voice intent** is a verb an app exposes for Aura (the voice assistant) to invoke from natural speech. Intents are how apps gain voice agency without Aura hardcoding knowledge of any app. Same shape as `[[contributes.hub.panel]]`, different slot.

**Reference implementations:** `apps/task/` (`task.add`, `task.list_today`), `apps/journal/` (`journal.add_entry`).

## Principles

1. **Intents are data, not hooks.** A row in `manifest.toml` declares the verb; a method on the app implements it. Aura discovers via `call_contributions("voice-assistant", "intent")` at startup. No imports, no registry, no Aura code change per new intent.
2. **The verb is namespaced `<app>.<verb>`.** Prevents collisions, makes the LLM's emitted token self-explanatory.
3. **Scope is enforced, not all-tools-at-once.** Aura only injects intents into the prompt when they're (a) marked `always = true`, (b) belong to the active companion's app, or (c) belong to one of the last 2 invoked apps. This is the load-bearing decision that keeps the LLM from drowning in tools.
4. **Fail soft.** A missing handler, bad args, or thrown exception emits a single `error` event and the chat continues. Mirrors hub.panel.
5. **Cards are optional, and reuse existing renderers.** If a return shape includes `card`, it must use a renderer Aura already knows. New renderers are added centrally to Aura, not hand-rolled per app.
6. **Voice intents wrap, they don't replace.** The `voice_*` method on the app should call the existing public method (`add_task`, `add_entry`, …) and add only a short `say` summary — never duplicate logic.

## When NOT to add an intent

- Anything that's just **chat or summary** — Aura can already answer those by looking at the context contribution. Adding an intent for "tell me about X" is overhead, not capability.
- Anything **destructive without explicit user confirmation** — voice is lossy; "delete project foo" can be misheard. If you must, the handler should return `{say: "Are you sure?", confirm_token: "..."}` and require a follow-up confirmation intent (V2 — not built yet).
- Anything where the **LLM doesn't actually need to choose** — if you want a button, build a button. Intents are for the cases where natural language is the right interface.

## Convention

### Manifest

```toml
[[contributes.voice-assistant.intent]]
verb = "task.add"                       # <app>.<verb>
method = "voice_add_task"               # async method on the app
example = "add a task to call mom"      # one-line phrase the LLM sees
args = { text = "string" }              # arg schema (names + JSON types)
always = false                          # if true, always in scope
description = "Capture a quick task"    # optional
card = "task-list"                      # optional renderer name
```

Arg types: `string`, `number`, `boolean`. Optional args end with `?` (e.g. `{ text = "string", mood = "string?" }`). The schema is for the prompt + light validation, not strict typing.

V1 limit: args must be **flat** — the inline parser stops at the first `}`, so nested objects break parsing. If a verb really needs structured args, JSON-encode them into a string arg and decode in the handler.

### Handler shape

```python
async def voice_add_task(self, text: str) -> dict:
    new_id = await self.add_task(text)               # reuse existing method
    return {"say": f"Added: {text}"}                 # speak this
    # Or with a card:
    # return {"say": "Here's today.", "card": {"renderer": "task-list", "data": [...]}}
```

Always `async`. Always returns a `dict` with at least `say`. `say` is what gets spoken — keep it under one sentence. No markdown, no URLs (TTS reads them literally — see `AURA_SYSTEM`).

### LLM emission

The model emits `[INTENT:app.verb({"arg":"value"})]` inline in its reply. Aura strips the token before TTS, parses + dispatches, and feeds any returned `say` back into the same TTS pipeline. The model writes natural prose around the token; the user hears continuous speech.

## Scope rules (canonical)

When building the per-request prompt, Aura includes intents matching ANY of:
- `always: true`
- `_app_id == active companion's _app_id`
- `_app_id in self._recent_apps` (deque max-len 2)

Cap at 12 intents. If more match, prefer `always` first, then companion-app, then recent. Truncated intents simply don't appear in the prompt — the LLM can't invoke what it can't see.

## Card renderers (V1)

Aura's frontend (`apps/voice-assistant/pages/index.html`) maps three renderer names today. Aura ships its own renderers (not `EOS_UI`) because it's a deliberate visual island — full-screen dark glassy aesthetic, not the standard app surface. Data shapes are stable; styling stays Aura-native:

| Renderer      | Data shape                                                                |
|---------------|---------------------------------------------------------------------------|
| `stat-tile`   | `{label, value}` or list of same                                          |
| `entity-card` | `{title, subtitle?, fields?: [{label, value}]}`                           |
| `task-list`   | `[{text, done?, tag?, tone?}]` — `tone` is `overdue` / `today` for color  |

The full event from the backend is `{type:"card", intent, renderer, data, title?}`. `title` becomes a small uppercase header above the card body.

To add a new renderer: add a function to the `CARD_RENDERERS` map in `index.html`, document the data shape here, then any app can return it. Never hand-roll HTML in app handlers — stay in the data-shape contract.

## Post-intent narration (pull-side slot)

Apps know the consequence of their intents — Aura doesn't. The `[[contributes.voice-assistant.narration]]` slot lets an app append a short follow-up sentence after one of its intents fires, so the user hears confirmation that something actually changed.

```toml
[[contributes.voice-assistant.narration]]
intent = "task.add"            # exact verb, OR "task.*" for any verb in the namespace
method = "narrate_after_add"   # async method on the app
```

Method shape:

```python
async def narrate_after_add(self, *, args: dict, result: dict) -> str | None:
    open_tasks, _ = await self._idx.get()
    return f"That's {len(open_tasks)} open total."   # or None to skip
```

- Runs **after** the intent handler, never in place of it. The handler still returns its own `say`; narration is appended (single space separator).
- Multiple narrators per intent are allowed; their outputs concatenate in registration order.
- **Fail-soft** — exceptions are swallowed per narrator and the chat continues. Mirror of `call_contributions`.
- Keep narration short (one sentence). It's spoken straight into TTS.
- Don't use narration to do *new* work — call cheap state reads only. The intent handler already did the work; this is just the consequence summary.

## Debug

`GET /voice-assistant/debug/intents` returns:
- `registry` — full list of contributed intents across all apps
- `scoped` — what would be in scope right now for the active companion (helpful when "why didn't my intent fire")
- `narrators` — registered post-intent narrators (intent, app, method)

## Graduation paths

- **First reused validator / formatter** → extract to `BaseApp.voice_result(say, card_renderer=None, **data)` in `emptyos/sdk/base_app.py`.
- **Confirmation flow** → add `{confirm: True, token: "..."}` to the return shape; Aura re-prompts the user, second voice turn carries the token.
- **Cross-provider native tool-use** → if Ollama/OpenAI/Claude all expose stable function-calling, swap the `[INTENT:...]` parser for native tool-calls. The contribution slot stays the same; only the wire format changes.
