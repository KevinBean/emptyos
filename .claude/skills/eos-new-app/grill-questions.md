# eos-new-app — the Phase 1 question bank

**Read this file when running Step 1 Phase 1.** Ask one question at a time and
wait for the answer. Each question's "Why" line must be sent *with* the question —
it teaches the user what the answer drives, so they can give a better one.

Act as an ICT Business Analyst, not a templater. The job is to extract
requirements the user hasn't stated yet — users, jobs-to-be-done, acceptance
criteria, non-functionals, out-of-scope — *before* nailing down implementation
details.

---

## Phase 1A — Problem & Users (BA front-half)

Anchor *what* and *who* before *how*.

1. **Problem statement.** In one sentence: what user pain does this remove, or what user goal does this unlock? *Why:* Forces a real "why" before we name a verb. If you can't say it in a sentence, the app isn't ready.
2. **Primary user(s).** Who's the user (you alone / family / team / public demo visitor / another app)? *Why:* Drives `apps/` vs `apps/personal/`, demo-mode visibility, privacy posture, and whether the UI needs onboarding.
3. **User stories.** Give 3-5 in the form: *As a <user>, I want to <action> so that <outcome>*. *Why:* Each story becomes one acceptance test in Step 6 and one row in the scaffold checklist. Fewer than 3 = the app is a feature, not an app; more than 5 = it's two apps.
4. **Acceptance criteria.** For the top story, what's the Given/When/Then? *Why:* This is the test we'll write first. If you can't state it, the story isn't sharp enough.
5. **Out-of-scope.** What is this app deliberately NOT going to do? *Why:* Saves a future rewrite. Examples: "no sharing across users", "no AI generation in v1", "no mobile-specific layout". Out-of-scope items go into the spec note and the `next` section of Step 10.
6. **Non-functional requirements.** Any constraints on: latency (<200ms? async ok?), offline behaviour (works without daemon? without internet?), privacy (any cloud calls allowed?), accessibility (keyboard-only? screen-reader?)? *Why:* These shape capability choice + cloud-consent posture + whether `[provides.export]` matters. (CLAUDE.md Rules 18-20.)

## Phase 1B — Solution shape (BA back-half)

Now the technical-decoding questions, informed by Phase 0.5 prior art.

7. **Verb.** What single verb does this app give the user? *Why:* Apps are atoms — one verb each. If you need more than 5 words, it's two apps. (CLAUDE.md Principle 4.)
8. **Data shape.** Where does the data live — markdown vault notes / `data/<id>/*.json` / derived from another app / stateless? *Why:* Drives vault_map entry, frontmatter convention, which `self.read/write/vault_*` calls you need, and whether boards can render it (`.claude/rules/boards-as-view-layer.md`). If Phase 0.5 surfaced `VaultLibrary` or another reusable, default toward consuming it.
9. **Surfaces.** Beyond its own page, which surfaces does it appear on — hub panel / voice intent / boards view / tour step / addons slot? *Why:* Each surface is a manifest contribution slot. Picking them now means scaffolded; retrofitting means a follow-up PR. See `.claude/rules/hub-panels.md`, `voice-intents.md`, `tour-steps.md`, `addons.md`.
10. **Events.** What events does it emit on user actions? *Why:* Events are how the reactor + other apps see your work; no emits = invisible.
11. **Capabilities.** Which of {read, write, think, search, speak, listen, draw, animate, see} does it require? *Why:* Manifest declares them — kernel validates on load; missing capability = boot fail. Cross-check Phase 1A.6 non-functionals: if "no cloud" was a constraint, prefer providers tagged `is_cloud=False`.
12. **Sub-patterns.** Any of these patterns to adopt now: room-review-gate / multi-CLI participants / slash command palette / standalone export? *Why:* Each is documented in `.claude/rules/` — adopting at scaffold is hours; retrofitting is days.
13. **Privacy / branding.** Anything in the app that touches third-party brand names, personal data patterns, or should be hidden from `demo.enabled`? *Why:* Rule 13 + 14 (no personal data, no third-party brand in user-facing text) + `[app] private = true` for demo hiding. Cheaper to flag now than during release-public.py audit.
14. **Name/id.** Now: kebab-case id and display name? *Why:* Last, because the verb + data answers may have already shifted the right name.

---

## Phase 0.5 — Explore agent prompt template

> Quick prior-art scan for a new EmptyOS app. Working title: `<title>`. Verb: `<verb-from-Phase-0 or "TBD">`. Find:
> 1. Existing apps under `apps/` and `apps/personal/` with overlapping verbs/data — list id + one-line summary.
> 2. SDK helpers in `emptyos/sdk/` that would be relevant — list path + one-line of what it does.
> 3. `.claude/rules/` files likely to apply — list filename + why.
> 4. Plugins in `plugins/` that already provide the underlying capability.
> Keep total report under 250 words. Fail soft — if nothing is found, say so in one line.

---

## Phase 3 — Spec note sections, in order

Write to `{vault}/30_Resources/EmptyOS/grill/new-app-<id>-<ts>.md` with frontmatter
`tags: [grill-spec]`, `recipe: new-app`.

1. **Problem & users** — problem statement + primary users (from Phase 1A.1-2)
2. **User stories** — bulleted list, each in As/I-want/so-that form (Phase 1A.3)
3. **Acceptance criteria** — Given/When/Then per top story, minimum one (Phase 1A.4); these become Step 6 test names
4. **Out-of-scope** — explicit non-goals (Phase 1A.5)
5. **Non-functional requirements** — latency / offline / privacy / accessibility (Phase 1A.6)
6. **Solution shape** — verb, data shape, surfaces, events, capabilities, sub-patterns, name/id (Phase 1B)
7. **Prior art consumed** — apps/SDK/rules from Phase 0.5 the new app builds on (so the next grill knows this app already covered ground X)
8. **Scaffold checklist** — bullet list of files to create with manifest fields filled, one bullet per artifact (manifest.toml, app.py, pages/index.html if any, tests/test_sys_<id>.py, release.toml line, vault-map entry)

The spec survives if the session crashes mid-scaffold, and downstream
`/eos-new-app spec=<path>` invocations can re-use it. It also doubles as a PR
description and a future debugging reference ("why does this app exist?").
