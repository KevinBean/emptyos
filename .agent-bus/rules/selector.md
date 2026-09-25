---
paths:
  - "apps/**"
  - "emptyos/sdk/base_app.py"
---
# Selector Rule — `BaseApp.select()` for LLM-backed routing

When an app needs the model to **choose one branch from a closed set** —
classification, sub-handler routing, "refund vs escalate vs reply", "which
KB bucket" — use `BaseApp.select(prompt, choices, *, system=, default=,
min_ability=)`. It forces a single key out of `choices` (a list of keys or
a `{key: description}` map), validates the answer against that set, and
**never raises and never invents a key** — on a bad/unparseable response it
returns `default` (or the first key). The matching ladder is exact → bare →
case-insensitive → **echoed menu line**: the menu renders as `- <key>: <desc>`
and models routinely answer `{"choice": "clause: verbatim text of ..."}`, so a
`<key>:` prefix resolves to that key (longest key first, since a key may itself
contain a colon — `tag:cable` from `scope_menu`). Measured 2026-08-05 at ~11% of
dict-form calls on both qwen3.5-32k and gpt-5.4-mini, every one naming the RIGHT
key; without recovery those correct answers silently became the caller's
`default`. Pass `default=` — every call site does, and it is what keeps an
unusable reply off position 0. This is the inverse of the
`[DO:app.verb]` / `[INTENT:...]` token pattern: those *parse a verb out of
free-text generation*; `select()` *forces a choice* so the caller drives
deterministic control flow on the returned key. Use `select()` for routing;
keep `[DO:]`/`[INTENT:]` for agent/voice action emission and `self.think()`
for content generation — don't reach for `select()` to generate prose, and
don't hand-roll a `think()` + `parse_llm_json` + "did it pick a real option"
loop when this helper already does it. Pass `system=` for routing
persona/rules (rule 12) and `min_ability=` to gate weak models off a nuanced
decision (`.claude/rules/model-ability.md`). Inspiration: the named
`Selector` primitive in OpenRath (repo-review, 2026-06-06) — we already had
three ad-hoc routers (voice intents, `[DO:]` gating, `gate_mode`); this names
the forced-choice shape once for app-internal logic.
