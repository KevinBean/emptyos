# Agent Context Bus — workspace config sync + internal-think context loading

The Agent Context Bus has two consumer paths that **must stay separate** in
your head. Confusing them is how you end up either (a) burning 12k tokens
auto-injecting CLAUDE.md into every `self.think()` call or (b) building a
parallel context system that drifts from what external CLIs see.

## The two paths

| Consumer | What it reads | How it reads |
|---|---|---|
| **External agents** — Claude Code CLI, codex, gemini, cursor | The reassembled boot files at workspace root (`CLAUDE.md`, `GEMINI.md`, `AGENTS.md`) + native `.claude/rules/*.md` + native `.claude/skills/*/SKILL.md` | Automatic — these CLIs boot into `cwd` and load the files themselves. `eos bus ripple` keeps them in sync with the canonical store. |
| **Internal think** — any `self.think()` from an app, an `eos staff` cron agent, an `eos rooms` agent participant (NOT CLI participant) | The **canonical store** at `.agent-bus/sections/`, `.agent-bus/rules/`, `.agent-bus/skills/` | Explicit — call `BaseApp.bus_context(...)` or `BaseApp.bus_assemble(...)`. |

CLI participants in rooms (`@claude` in a chat) inherit Path 1 automatically
because the `agent-runtime` plugin spawns claude-cli with `cwd=workspace`.
Non-CLI agent participants (regular `self.think`) need Path 2.

## When to use `bus_context()`

Use it in any `self.think()` call where the agent needs the same
architectural awareness an external coding agent would have. Examples:

```python
# A staff agent that drafts code reviews — needs the testing + multi-module rules.
ctx = self.bus_context(
    rules=["testing", "multi-module-apps", "dev-gotchas"],
)
review = await self.think(diff, system=ctx + "\n\n" + REVIEW_PERSONA)

# A vault-side staff agent — read the *vault's* bus, not EOS's.
ctx = self.bus_context(
    rules=["vault-operator"],
    workspace=self.vault_root,
)

# An eos rooms agent participant that should know EOS architecture.
ctx = self.bus_context(
    sections=["claude-architecture", "claude-development-rules"],
    rules=["app-ui-patterns"],
)
```

**Selective by design.** Don't pull everything. The bus is a menu, not a
buffet — load only the slices the agent will actually use this turn.

## When NOT to use it

- **Pure UI/CRUD think calls** — summarizing a few rows of data doesn't need
  architectural rules. The vanilla `self.think(prompt, system=persona)` is fine.
- **Public-facing creative work** — Aura voice intents, podcast generation,
  song-lyric drafting. Bus rules are internal developer doctrine, not
  end-user persona.
- **Bulk per-item loops** — if you'd call `bus_context()` inside a for-loop
  over 100 items, hoist it outside the loop. The content is stable per turn.
- **Inside `kb_explain` / `kb.api_get_note`** — that's the KB system, a
  different store with different semantics. Don't blur them.

## Scanning before drilling — `bus_index()` / `bus_menu()`

`bus_context()` requires you to *already know* which rules/sections/skills you
want. When you don't — when the agent itself should pick — use the L0 menu
first. `bus_index()` returns `[{kind, name, abstract}]` for everything in the
bus (a cheap, token-light listing); `bus_menu()` renders the same as a markdown
list for prompt injection. The canonical loop:

```python
# 1. scan — inject the one-liner menu of everything available
menu = self.bus_menu()                       # "- rule:testing — Testing Rule\n- ..."
picks = await self.select(                    # or a structured think() that returns names
    f"Which rules apply to this task?\n{menu}\nTask: {task}",
    choices=[e["name"] for e in self.bus_index(sections=False, skills=False)],
)
# 2. drill — load only the chosen bodies in full
ctx = self.bus_context(rules=picks)
answer = await self.think(task, system=ctx + "\n\n" + PERSONA)
```

This is the **L0/L2 split**: the menu is the abstract (read 84 one-liners
cheaply), `bus_context()` is the full detail (load the 3 you need). It reads the
same `.agent-bus/` store, so the menu and the drill never disagree.

Each entry's abstract comes from, in priority order: an `abstract:` frontmatter
field on the rule/section file, then a skill's `description:`, then the file's
first heading line. **You don't need to author anything** — every rule's H1 is
already a usable one-liner. Add an explicit `abstract:` frontmatter line only
when a rule's H1 is too long or wrong as a summary (compatible with the `paths:`
frontmatter pilot; remember `eos bus ripple` to sync the override into
`.agent-bus/`). Don't pre-build a `[think.context]` auto-scan — same rule-9
restraint as below.

## When to use `bus_assemble()`

Only when you genuinely need the whole boot file's worth of context — e.g.
an app that wants to feed CLAUDE.md to a model for "given this whole
architecture spec, generate X." Most of the time `bus_context()` with a
targeted list is the right answer; reach for `bus_assemble()` deliberately,
not by default.

## Don't auto-inject the bus into `self.think`

Tempting to add a manifest field like `[think.context] bus_rules = [...]`
that auto-prepends every call. **Resist until 3+ consumers want the same
shape** (CLAUDE.md rule 9). Today the explicit per-call composition is
honest about what the model sees; auto-magic hides it and silently inflates
token use.

When the graduation does happen, the right shape is:

```toml
# Future — when 3+ apps share this
[think.context]
bus_rules = ["vault-operator"]
bus_sections = ["claude-architecture"]
```

The think capability would prepend these to every `self.think()` call from
this app. Per CLAUDE.md rule 9, don't build it before the duplication is
real.

## Cross-references

- `scripts/agent_bus.py` and `eos bus {import,ripple,status,transpile}`
  are the same code — `emptyos.sdk.agent_bus`. Both edit the same
  `.agent-bus/` store.
- `BaseApp.bus_context()` / `BaseApp.bus_index()` / `BaseApp.bus_menu()` /
  `BaseApp.bus_assemble()` — `emptyos/sdk/base_app.py`. The index/menu pair
  (`list_bus_entries` + `_extract_abstract` in `emptyos/sdk/agent_bus.py`) is
  the L0 scan layer; `bus_context` is the L2 drill.
- `.claude/rules/multi-module-apps.md`, `.claude/rules/testing.md`,
  `.claude/rules/vault-operator.md` — common things to load into a
  staff agent's `bus_context`.
- `.claude/rules/path-scoped-rules.md` — `rules_for_paths` /
  `rules_menu_for_paths` (same module): deterministic path→rule matching
  over `paths:` frontmatter, for prompt builders that know which files a
  change touches (fix-agent first consumer).
- `.claude/skills/preflight/SKILL.md` — the preflight skill calls
  `eos bus ripple --dry-run` to surface drift between canonical store
  and native files.
