# eos-new-app — automated mode (app-builder)

The spine (`SKILL.md`) is the **human-driven** path: you walk the grill yourself
and write the spec + scaffold by hand. There's a second path for the *same
conventions*, executed by claude-cli via `apps/extension/dev/app-builder/`.

Read this file when the user asks about the autonomous loop, or when deciding
whether to hand a settled spec to `app-builder` instead of scaffolding by hand.

---

## The loop

```
spec note in vault                ← human writes (or persona drafts in apps/personal/staff/)
        │
        ▼ POST /app-builder/api/run {spec_path}
claude-cli scaffolds in .claude/worktrees/app-builder/
        │ (reads CLAUDE.md + eos-new-app SKILL.md + scaffold-templates.md
        │  as part of its system prompt; emits scaffold per the skill's steps)
        ▼ status: ready
        │
   [Merge gate]                   ← human reviews diff, clicks Merge
   [Restart daemon]               ← human runs restart.bat
   [Install via Store]            ← human flips toggle at /store
   [Verify gate]                  ← endpoint smoke vs. acceptance criteria
```

The autonomous loop reads the SAME conventions documented in the skill's Steps
3-8 — `_BUILDER_SYSTEM_PROMPT` (`apps/extension/dev/app-builder/prompts.py`) tells
claude-cli to follow `SKILL.md` **and** `scaffold-templates.md` verbatim. So when
you edit conventions in either file (a manifest field, a test pattern, a
`[contributes.*]` shape), both paths pick them up immediately.

> **Maintainer note.** The templates live in `scaffold-templates.md`, not in the
> spine. If you move or rename that file, update the paths named in
> `_BUILDER_SYSTEM_PROMPT` — otherwise the loop scaffolds without its templates.

---

## Spec note shape

The grill output written by the spine at Step 1 (the spec note saved to
`30_Resources/EmptyOS/grill/new-app-<id>-<ts>.md`) is exactly what `app-builder`
consumes. Required frontmatter:

```yaml
---
tags: [grill, app-spec]
app_id: notes-counter          # lowercase, digits, hyphens; matches dir name
app_name: Notes Counter         # display name
verb: count notes by tag        # the one verb this app owns
capabilities: [search]          # think/read/write/search/draw/etc.
surfaces: [auto-ui]             # auto-ui / hub-panel / voice-intent / custom-page / etc.
data_shape: vault-query         # vault-frontmatter / data-json / vault-query
---
```

Required body sections:

```markdown
## Why
One paragraph: what's the problem, who's the user, what does this app refuse to do.

## Acceptance criteria
- GET /notes-counter/api/count?tag=x returns {"tag": "x", "count": int}
- GET /notes-counter/ renders an auto-UI showing tag counts
```

The `## Acceptance criteria` bullets are parsed by `app-builder`'s verify endpoint
— every `METHOD /path` pattern becomes a smoke-test call after merge. Anything not
in this format gets ignored at verify time (but claude-cli still reads it for
design intent).

---

## When to use which path

| Use the skill (manual) when | Use `app-builder` (loop) when |
|---|---|
| You're working out a new app's shape and want grill conversation | The shape is settled; you have a spec note ready to scaffold |
| First 2-3 apps you build in a new style | 4th+ when the pattern is settled |
| The spec is unclear and would benefit from iteration in chat | The spec is verbose, repetitive, or copy-pasted from a previous app |
| You want to write code yourself | You want claude-cli to do the scaffold so you review the diff |

There's no enforcement either way — they produce the same shape of app, write to
the same paths, follow the same conventions. The loop is a productivity
accelerator once the human cost of the grill outweighs its value.

Contract for the autonomous loop: `docs/app-builder.md`. The spec-drafter cron
(autonomous spec generation from a project entering `status: spec-ready`) lives in
`apps/personal/staff/` and is per-user.
