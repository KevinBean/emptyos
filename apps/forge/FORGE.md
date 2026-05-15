# FORGE.md — the growth charter

> **Forge — a publishing platform for cross-platform apps, evolved through conversation, owned by the user.**

This file is to Forge what `CLAUDE.md` is to EmptyOS. Read it before adding a Target, a Skill, or anything that lives inside Forge. The charter exists because Forge is meant to evolve indefinitely without acquiring framework debt — and the only way that survives time is if every contributor (human or agent) shares the same anti-abstraction discipline.

## What Forge is

Apps are described in conversation with an AI coding tool. Forge scaffolds, builds, signs, and ships them. Each native stack is a **Target**. There is no framework underneath — every Target speaks its native build tooling directly. Coherence comes from convention, not from shared code.

## What Forge is NOT

- ❌ A cross-platform abstraction over native build tools (Gradle ≠ xcodebuild ≠ cargo; flattening them costs more than it saves)
- ❌ A framework with a `BaseTarget` class to subclass — the `Target` Protocol in `targets/base.py` is the contract, classes implement it directly
- ❌ A code generator for the app's actual content (that's the LLM's job in conversation; Forge ships scaffolds + builds, not app logic)
- ❌ An IDE replacement (let users keep Cursor / Claude Code / vim)
- ❌ A central app-store registry (separate product if it ever exists)
- ❌ A plugin system separate from EmptyOS's (reuse it if needed)

## The atom: a Target

A Target is exactly four things on disk:

```
apps/forge/targets/<target-id>.py         # Python class implementing the Target Protocol
apps/forge/templates/<target-id>/         # The minimal seed code (Kotlin / Swift / Rust / Python)
.claude/skills/forge-new-<target-id>/     # The Claude Code skill that scaffolds + grows it
.claude/skills/forge-release-<target-id>/ # The Claude Code skill that cuts releases (when verb diverges from default)
```

Registered in `targets/__init__.py` `TARGETS = {...}` — data-driven dispatch. No base class to extend. No lifecycle hooks to register. To add a new Target, write a sibling module satisfying the Protocol and add one line to the registry.

A Target owns its entire stack: which native CLI it drives, which AI CLI scaffolds for it, which build/sign/release tooling it speaks. **A Target's complexity belongs to that Target, never to Forge.**

## How AI coding tools plug in

One pattern. Skills wrap AI CLIs behind named verbs. Targets call AI CLIs through the `agent-runtime` service.

| Layer | Owns |
|---|---|
| **AI CLI** (claude-cli, google's android-cli, opencode, codex) | The actual generation work |
| **Target's `scaffold` / `release` verbs** | When to invoke which CLI, with what args, what to check after |
| **`agent-runtime` plugin** | One-shot subprocess driver for any registered CLI (already exists) |
| **Skill** (`forge-new-<id>`) | User-facing entry — the conversation prompt that drives Forge for this Target |

Flow: User says "ship to Android" in chat → Skill prompts Claude to call `forge.api_scaffold(target_id="native-android", ...)` → `NativeAndroidTarget.scaffold()` uses `runtime.text_cli_run("android-cli", ...)` → signed APK appears.

**Nothing in Forge core knows what Kotlin or Gradle look like.** That knowledge lives in the Android Target's `scaffold/build/release` implementation + the Android CLI itself. Forge core knows about Targets, verbs, and Skills.

## The five rules

1. **Each Target is shippable in isolation.** Deleting any Target leaves the rest building cleanly. No cross-Target imports.
2. **No Target knows about another Target.** Cross-platform coherence comes from the Protocol shape, not from coupling.
3. **The AI CLI is the heavyweight; the Target is the thin wrapper.** A Target's `.py` file over ~500 lines is a smell — extract complexity into a better Skill prompt or a better template snippet, not into Forge.
4. **Conversation-mode hackable.** Every Target template must be small enough that a Claude Code session can grok it cold and propose a change. Black-box generated code that no one reads is the failure mode.
5. **Skills are versioned; Targets are stable.** A Target's behaviour shouldn't break when a Skill prompt evolves. Pin if needed; default loose.

## Extract-on-second-consumer (the EmptyOS rule, ported)

| Pattern | Stays where | Extracts to |
|---|---|---|
| Release verb (bump + tag + push) | First Target that needs it | `targets/_shared/release.py` when 2nd Target shares the shape verbatim |
| Signing recipe | First Target | Shared helper when reused unchanged |
| Skill prompt fragment | One Skill | `.claude/skills/_shared/forge/` when ≥2 Skills use it |
| Template snippet | One template | Cross-template snippet only when ≥3 templates use the same chunk |

No abstraction lands without two real consumers. No abstraction *stays* without three. Yes, this means writing similar release scripts in two Targets first. That's the **correct** amount of duplication for the system to stay healthy.

## What grows / what doesn't

**Grows naturally:**
- New Targets land as full atoms (class + template + skill). One PR, one folder set.
- Skills evolve as the AI CLIs underneath evolve. Add args, refine prompts, add post-checks. No coordinated release.
- Templates absorb battle-scars over time. The "what should a minimal Kotlin shell look like" evolves through actual use.
- The `Target` Protocol gains methods only when ≥2 Targets demand the same new verb. New verbs added to the Protocol must come with default implementations so existing Targets don't break.

**Doesn't grow:**
- A `BaseTarget` class. Doesn't exist; doesn't get created.
- A "Forge SDK" until two Targets pull on it.
- A cross-platform abstraction over native build tools.
- A target lifecycle hook system.
- A unified plugin/extension model separate from EmptyOS.

## The discipline that prevents framework debt

**If you find yourself writing code that lives in Forge but doesn't belong to a specific Target, stop.** That code is the framework starting. Move it back into the Target where it actually belongs, or into a Skill if it's a verb.

If you find yourself writing helpers in `apps/forge/app.py` that any Target could call, **stop and check rule 1.** Either the helper belongs to one Target (move it there), or to two (extract per the table above), or to none (it's premature).

If a Target is starting to look like a generic wrapper that "supports many languages," **stop.** Make it a sibling Target per language. Three small Targets > one configurable Target.

If a Skill is starting to drive multiple Targets, **stop.** Make it three sibling Skills with a shared prompt fragment. Skills are verbs; verbs are per-Target.

## Tech anchors

- **Target Protocol**: `apps/forge/targets/base.py` — read before implementing.
- **Existing Targets**: `tauri.py` (web+native via Rust), `cli.py` (standalone Python binary via PyInstaller). Two categorically different stacks, same Protocol. The Protocol's job is to *be that thin*.
- **agent-runtime plugin**: `plugins/agent-runtime/plugin.py` — how Targets invoke AI CLIs. Read `.claude/rules/multi-cli-participants.md` before adding a new CLI.
- **Skills directory**: `.claude/skills/forge-*` — naming convention is `forge-<verb>-<target>` or `forge-<verb>` for cross-Target verbs.
- **Coming-soon stubs**: `COMING_SOON` list in `targets/__init__.py` — placeholders shown in UI; promote to `TARGETS` when implementing.

## When to read this file

- Before adding a new Target
- Before writing a new Forge Skill
- Before adding any method to the `Target` Protocol
- Before extracting anything to a shared helper
- When tempted to "make Forge a real framework"

## When to update this file

When a rule above gets in your way and you decide the rule is wrong — change it here first, in a commit by itself, with the reasoning. Never just bypass a rule in code. The charter is the artifact; if it's wrong, fix the artifact and the change cascades.

If a new pattern emerges that ≥2 Targets share and the charter doesn't cover, document it here. If a pattern stops being useful, remove it.

## The promise this charter makes

**Forge will grow indefinitely without acquiring framework debt** — because there's no central thing to keep coherent. Coherence lives in the Protocol shape, the four-on-disk atom, the extract-on-2nd-consumer rule, and the five rules above. Every Target is small. Every Skill is small. Every template is small. No piece carries the weight of "supporting all platforms generically" — each piece carries only its own platform.

This is the EmptyOS philosophy ported to publishing. Apps are atoms; Targets are atoms; the value is in the connections.
