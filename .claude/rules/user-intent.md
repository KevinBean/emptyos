---
paths:
  - "apps/**/manifest.toml"
  - "apps/_example/**"
---

# User Intent Rule — `user_intent` on the manifest

Every app's `manifest.toml` MAY carry a `user_intent` list under `[app]` —
phrases a real user would say when they want what this app does. This is
the **"Feeling Lucky" data layer**: the system can take a one-sentence
user goal ("I want to track my spending"), match it against every app's
`user_intent`, and route to the right app without the user having to know
which one of 80+ apps maps to their need.

The recommender that consumes this field is not built yet. **Build the
data first, ship the consumer when there are 10+ seeded apps and the
match quality is testable.** Per CLAUDE.md rule 9 — specific first; the
SDK extraction (the recommender) waits for the second consumer or for
the data to be valuable on its own.

## Shape

```toml
[app]
id = "expense"
name = "Expense"
description = "Expense tracking — add, summarize, analyze spending"
user_intent = [
    "I want to track my spending",
    "记账",
    "where did my money go this month",
    "compare this month vs last month",
]
```

- A flat list of strings. No nesting, no schema, no priority.
- Mix languages freely. The user's environment is bilingual (English + 中文);
  phrases in either language match the same way.
- Phrases are the **user's voice**, not the developer's. "VaultLibrary-
  backed expense tracking with frontmatter" belongs in `description`;
  `user_intent` is what a human would actually say out loud.
- 3–8 phrases is the sweet spot. One phrase is too narrow; more than
  ten is usually overlapping copy.

## How `description` and `user_intent` differ

| Field | Audience | Voice | Example |
|---|---|---|---|
| `description` | Developer / store catalog | terse, technical | "Daily journaling — entries, milestones, mood tracking" |
| `user_intent` | Recommender / future "Feeling Lucky" | user, plain language | "I want to write about my day", "今天发生了什么", "track my mood" |

Both fields can coexist. They serve different surfaces; don't fold them
together.

## What to write

Cover **the natural one-sentence ways a user would describe the goal**,
not the surface area:

- Bad: `"add row"`, `"see chart"`, `"export csv"` — those are UI moves,
  not goals.
- Good: `"I want to track my spending"`, `"where did my money go this
  month"`, `"compare this month vs last month"` — those are goals the
  user brings; the app's UI is how the system answers them.

Avoid:

- **Brand names / third-party services** — same rule as `description`;
  generic terms only ("markdown vault", not "Obsidian vault").
- **Imperative verbs alone** — `"track"` matches everything. `"track my
  spending"` is specific enough to route.
- **EmptyOS jargon** — phrases like "VaultLibrary", "BaseApp", "the
  reactor" never appear in user intent.

## When to skip

Some apps don't need this field today:

- **Aggregators / chrome** (hub, settings, store, system, topology) —
  the user never says "I want a topology graph"; they end up there from
  somewhere else.
- **Single-shot tools the user never opens directly** (focus timer,
  capture composer) — they're triggered by hotkey / FAB, not by name.
- **Generators / output surfaces** (publish, music-studio, podcast) —
  the user *says* "I want to make a podcast"; if those apps are the
  natural answer, fill in. If they're internal-only outputs, skip.

When in doubt: would the user ever phrase a need that lands them here?
If yes, write phrases. If no, leave the field off.

## Bilingual phrasing

For apps the user will use in both languages, include both. Don't translate
mechanically — write the way a native speaker would phrase it in each
language. "I want to journal my day" + "今天发生了什么" cover different
mental approaches to the same app, not the same approach in two
languages.

## Graduation: the recommender

When this convention has been seeded on ≥10 apps, the next consumer is
a `recommender` surface (likely a small skill + a `/recommend` input
mode on the existing `EOS_UI.searchBar`). Shape will be:

1. Read every app's `user_intent` at boot — cache `{app_id → phrases}`.
2. On a user query, ask the LLM "which 1-3 apps best match this need?"
   passing the cached map (small payload — phrases are short).
3. Return ranked candidates with actions: **open**, **install from Store**
   (if not enabled), **pin to hub**.
4. Auto-configure (writing to `emptyos.toml`) is deferred — uses
   `.claude/rules/proposed-action.md` diff-preview when it lands.
5. "No app matches" routes the query to `apps/extension/dev/app-builder/` — never
   tries to compose new logic itself.

Don't build the recommender before the data is rich enough to be
worth searching.

**Deterministic fallback already exists** — `voice-assistant`'s generic
`aura.open` verb resolves a spoken app name to an app by fuzzy matching
id / alias / name / description (`apps/public/standard/voice-assistant/generic_verbs.py`
`_resolve_app_query`). It is app-internal today (first + only consumer). When
the recommender is built it is the **second** consumer of that resolution
logic — extract the pure `(manifests, query) → (app_id, manifest)` matcher into
`emptyos/sdk/` then (per CLAUDE.md rule 9), rather than re-rolling it. The
recommender adds the LLM `user_intent`-phrase ranking layer *on top* of that
deterministic name match, not instead of it.

## Cross-references

- `apps/_example/manifest.toml` — reference manifest; add `user_intent`
  here once the convention is on 5+ real apps and we're sure of the
  shape.
- `.claude/rules/voice-intents.md` — sibling concept at the verb layer
  (LLM emits `[INTENT:app.verb(...)]` for actions). `user_intent` is at
  the *goal* layer (which app to open); voice intents are at the *action*
  layer (what to do once you're there). Both compose: a recommender
  could hand off to the matched app's voice intents for the actual move.
- `.claude/rules/hub-panels.md` — the eventual "pin to hub" action
  registers a panel contribution dynamically (one design path) or just
  flips an install/enable flag (simpler path; pick when implementing).
- `apps/extension/dev/app-builder/` — the "no existing app fits, build a new one"
  branch handed off here, not absorbed into the recommender.
