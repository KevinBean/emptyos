---
name: eos-communication-define-your-reader
description: Before writing or editing any external-facing text — a blog post, README, doc, landing page, announcement — define the reader precisely, then calibrate every sentence to them. Produces a sharp reader persona (who they are, what they know and don't, what they came for, what makes them bounce vs. stay), handles dual/mixed audiences (e.g. a curious non-programmer AND an expert to impress), and applies the craft rules that serve both without dumbing down. Use when the user says "who is this for", "define the reader/audience", "make this accessible", "will a non-technical reader get this", "this is hard to understand without background", "impress experts too", "calibrate this for X", or when a piece assumes knowledge its actual readers won't have. NOT for the mechanics of a specific medium (use eos-devlog-publish / eos-article-diagrams / eos-image-edit); this is the audience-calibration layer that runs first.
---

# Define Your Reader

The first move before writing or editing anything someone else will read. Most
"this is hard to understand" problems are not writing problems — they are
**undefined-reader** problems: the text was calibrated to whoever wrote it, who
already knows everything. Define the reader first, then every later decision
(what to explain, what to cut, what to lead with) has a test to pass.

Worked reference: the "Chat App as Pocket Console" post — an architecture essay
retargeted to a dual audience without losing its depth
(`feedback-emptyos-blog-dual-audience` memory).

## Step 1 — write the persona (out loud, specific)

Not "general readers." One named persona with five facts:

- **Who they are** — role, why they're reading *this*.
- **What they bring** — the concepts/literacy you can assume.
- **What they do NOT bring** — the two or three things that will lose them. Be concrete: which *words*, which *insider nouns*.
- **What they came for** — the question in their head; usually the *experience/stakes*, not the *mechanism*.
- **What makes them bounce vs. stay** — the exact moments they close the tab, and the ones that pull them in.

If you can't name what loses them, you haven't defined them yet.

## Step 2 — if there are two audiences, name the dual mandate

Common case: a **curious non-expert** who must be able to *follow* every
sentence, plus an **expert** you want to *respect* the work. Don't average them
into mush — serve both in the same sentence (Step 3).

## Step 3 — the calibration rules (serve both, dumb down neither)

1. **Keep the precise term, add a plain handhold** (progressive disclosure).
   `plain framing the non-expert reads (precise term the expert reads)`:
   "only your phone can reach it (a chat allowlist)", "it locks itself on a
   timer (a session TTL)". **Never delete the precise noun to simplify — wrap
   it.** Replacing a term with vague hand-waving loses the expert *and* teaches
   the non-expert nothing. Cut only true filler, never signal.
2. **Lead with the concrete; defer the abstraction.** Show the thing working
   (a scene, a screenshot, a feature) before the architecture/diagram/theory.
   Never let the reader's first visual be the hardest one.
3. **Gloss every insider proper noun on first mention** in one plain sentence —
   internal names, jargon, abbreviations. The reader has never heard of them.
4. **Cut what doesn't serve the reader** — an addendum whose point the
   conclusion already makes, a section that's there for completeness not for
   them. Tightness is a courtesy.

## Step 4 — the per-sentence test

Read each sentence as the persona: *could they follow it, and (if an expert is
in the room) would they respect it?* If it needs background the persona lacks to
even parse, translate it or make it **skippable** — an optional aside the
non-expert glides past without losing the thread, while the expert gets the
detail.

## When NOT to over-apply

- **Internal-only text** (code comments, dev notes, a devlog for yourself) — the reader IS an insider; glossing everything is noise.
- **A single homogeneous expert audience** — a deep-dive for practitioners doesn't need non-expert handholds on every line; define *that* reader and write to them.
- Don't let accessibility become **timidity**: keep the strong claim, the real term, the opinion. Define-your-reader sharpens the writing; it doesn't sand it flat.

## Cross-references
- `feedback-emptyos-blog-dual-audience` (memory) — the EmptyOS-blog instance of this doctrine.
- `eos-devlog-publish`, `eos-article-diagrams`, `eos-image-edit`, `eos-screenshot` — the *medium* skills this runs before.
- `life-communication-playbook` / `life-communication-written` — the interpersonal-message cousins (audience = one person, not a readership) (user-global skill — lives in `~/.claude/skills`, not in the repo).
