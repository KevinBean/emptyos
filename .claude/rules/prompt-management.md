# Prompt Management — code defaults, discoverable registry, per-machine overrides

EmptyOS has three kinds of prompt content, each with its own home. Don't blur
them — the 2026-07-04 design session explicitly rejected migrating prompt
constants into a data store (it breaks prompt↔parser locality, git-diff
review, and greppability) in favor of a thin **override layer**.

| Kind | Home | Editable how |
|---|---|---|
| **Pipeline prompts** (classification, extraction, generation stages — coupled to the parser next to them) | UPPERCASE constants in code (CLAUDE.md rule 12), optionally registered via `declare_prompts()` | Code change; per-machine override via `data/prompts/overrides.json` when registered |
| **Personas** (rooms agents, staff agents, voice companions) | Their existing data stores (`data/apps/rooms/agents/`, `data/apps/staff/agents.json`, per-app `system_prompt_method`) | Their own app UIs. **Never migrate these into the override layer** — they're already prompt-as-data |
| **Few-shot content / shape-priors** | KB `kind: pattern` notes via `resolve_pattern_examples` | Edit the vault note |

**Core:** `emptyos/sdk/prompt_registry.py` (pure, unit-tested in
`tests/test_sdk_prompt_registry.py`). **Surface:** `apps/extension/dev/prompts/`
(`/prompts/` — browse, edit, sweep; personas listed read-only).
**Store:** `data/prompts/overrides.json` (per-machine, gitignored).
**Reference adopters:** `apps/public/standard/{agent,publish,voice-assistant}/prompts.py`
· the English suite, adopted 2026-08-08 — `english/prompts.py` (extracted from a
14-line **inline f-string at the `self.think` call site**, the rule-12 violation
this section exists to prevent), `voice-review/prompts.py` (one inline, one class
attribute), `speaking/speaking_data.py` (declares in place — a prompts module
need not be named `prompts.py`), `improv/prompts.py` (good constants that simply
never called `declare_prompts`)
· `apps/extension/english-learning/dictionary/prompts.py` (the reading layer — its
header shows the shape worth copying: an override that reintroduces a
`{"k": str}` pseudo-schema instead of a valid JSON example silently broke the free
local tier, 0/8 vs 8/8, so the *why* travels with the prompt).

## Adopting an app (3 moves)

```python
# prompts.py — constants stay exactly as authored (rule 12 unchanged)
from emptyos.sdk.prompt_registry import declare_prompts

CLASSIFY_SYSTEM = """You are a task classifier. ..."""

PROMPTS = declare_prompts("myapp", classify_system=CLASSIFY_SYSTEM)
```

```toml
# manifest.toml — lets /prompts import the module without booting the app
[provides.prompts]
module = "prompts"
```

```python
# call sites — switch the constant reference to the resolving accessor
result = await self.think(text, system=PROMPTS.classify_system)
```

Kwarg names in `declare_prompts` are the lowercase of the constants they
mirror (`classify_system` ↔ `CLASSIFY_SYSTEM`). The prompts module must stay
**pure constants + the declaration** — the `/prompts` app imports it
standalone (`eos_apps.<id>.prompts`) for apps that aren't loaded, so an import
side-effect there would boot machinery at browse time.

## Resolution contract (fail-open, byte-identical when unused)

`PROMPTS.name` resolves at access time: override from
`data/prompts/overrides.json` if present and valid, else the constant. Every
failure mode falls back to the default — missing file, corrupt JSON,
unregistered key, **placeholder drift** (a stored override whose
`{placeholders}` no longer match the current default's is ignored, because the
caller's `.format()` would KeyError). An override can degrade to "no effect";
it can never break a call site. With no overrides file the resolved value IS
the constant — which is why the SDK layer ships without a dark flag.

Placeholder validation is identifier-based: only `{name}` fields that are
valid Python identifiers count, so JSON examples in prompts (`{"reply": ...}`)
and doubled braces (`{{`) don't make a prompt a "template". Non-template
prompts accept free-text overrides.

## Prefix-cache discipline (load-bearing)

`.claude/rules/prompt-prefix-cache.md` rule 2 still applies: a multi-turn loop
resolves the prompt **once before the loop** and reuses the string — never
`PROMPTS.x` inside the iteration. Same rule when a resolved value feeds both a
cache key and the call (`agent/orient.py` binds `classify_system` to a local
for exactly this reason). One-shot calls may access the attribute inline.

Composition order: suffixes compose **after** resolution
(`PROMPTS.review_prompt_header + voice`), so an override still gets its
voice-guide / foot appended.

## When NOT to declare a prompt

- **Internal glue strings** — a 5-word instruction inside a helper isn't a
  tunable prompt; declaring it is registry noise.
- **Personas** — they're already data (see table). The `/prompts` Personas tab
  lists them read-only; editing stays in their owning apps.
- **Prompts built dynamically per call** (`_build_cli_system`, companion
  context assembly) — there's no stable default to register. Register the
  stable *fragments* if user-tunable, not the assembled result.
- **Don't bulk-migrate.** ~190 constants remain undeclared; adopt an app when
  you touch it and a user would plausibly want to tune its wording (editorial
  voice, personas, user-facing content). A JSON-extraction prompt nobody
  should touch can stay unregistered forever.

## Drift is loud

`GET /prompts/api/sweep` (the Sweep button) reports **orphans** (override keys
no longer registered), **stale** overrides (placeholder drift — stored but
falling back), and a corrupt store file. The UI badges stale overrides. When
you rename a declared constant or change its placeholders, run the sweep —
the user's override doesn't break anything, but it silently stops applying
until they update it.

## Write path

All override writes go through `prompt_registry.set_override` (atomic
tmp+`os.replace`, placeholder-validated) via the `/prompts` app. Hand-editing
`data/prompts/overrides.json` bypasses validation and merely degrades to
fall-back on mismatch. Events: `prompts:override_set` / `prompts:override_cleared`.

## Deferred (build on demand)

Override versioning/history, A/B testing, per-domain overrides,
think-provenance "override active" chip, test-preview endpoint, `eos prompt`
CLI (agent-cli envelope; add when an agent consumer wants a validated write
path from the shell), repo-wide AST coverage scanner.
