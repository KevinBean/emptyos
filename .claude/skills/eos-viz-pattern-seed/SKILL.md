---
name: eos-viz-pattern-seed
description: Seed a viz KB kind:pattern note the disciplined way — build the scaffold as a real demo file, verify it in a headless browser (console errors + motion frame-diff + screenshots), THEN distill it into a KB note for few-shot injection. Use when a viz generation quality problem recurs ("bloom blown out", "model ignores X guidance", "shape Y keeps coming out wrong") and the fix is a curated example, or when the user says "seed a pattern note", "add a viz pattern", "make this a few-shot example". NOT for engineering-KB notes from a source PDF (use vault-source-digest) or non-viz patterns (cad patterns follow their own anatomy).
---

# EOS Viz Pattern Seed

Turn a generation-quality lesson into a KB `kind: pattern` note that viz
few-shot-injects via `examples=["<slug>"]`. The load-bearing rule: **the note's
code must be a verified artifact, never untested prose-code** — a pattern note
teaches every future generation, so a bug in it compounds.

Precedents (copy their shape): `viz/demo/immersive-scene.html` →
`viz-immersive-bloom-shader` (2026-06-22); `viz/demo/animated-scene.html` →
`viz-animejs-three-adapter` (2026-07-03).

## Prerequisites (read first)

- **Required — the verify harness**: `pip install playwright && playwright install chromium`.
  `eyeball_scene.py` drives a headless Chromium against a local `file://` URL; there is no
  offline substitute for Step 2, which is mandatory. `Pillow` is optional — without it the
  frame-diff degrades to `{"error": ...}` and you lose the motion proof, so install it when
  the pattern claims animation.
- **No daemon needed for Steps 1-3.** The scaffold + verify + distil loop is pure local files.
- **Daemon at `http://127.0.0.1:9000` only for the optional Step-4 pickup check**
  (`GET /viz/api/examples`, `GET /kb/api/notes/<slug>`) and the Step-6 preset edit, which
  needs a restart to take effect. Never `localhost` from Python — use `127.0.0.1` (the daemon
  binds IPv4 only). Private mode needs `Authorization: Bearer <token>` from
  `emptyos.toml [network] auth_token`.
- **Never restart `:9000` yourself** — ask the user, or lease a sandbox member
  (`.claude/rules/sandbox-driven-testing.md`).

## Steps

### 1. Build the scaffold as a real file

Write the minimal scene demonstrating the discipline at
`apps/public/standard/viz/demo/<name>.html`:

- Standalone single file, CDN modules only, versions **pinned** (never
  `@latest`; three addons must match the core version).
- Encode the traps as comments *at the point they bite* (importmap pins,
  threshold numbers, alias collisions) — the comments travel into the note.
- Keep it minimal: one strong demonstration of the discipline, not a showcase.

### 2. Verify before distilling (mandatory)

```bash
python .claude/skills/eos-viz-pattern-seed/eyeball_scene.py <path-to-scene.html> <out-prefix>
```

Requires the `playwright` pip package (+ Pillow for the frame-diff). Gate on:

- `n_errors == 0` — any console/page error disqualifies the scaffold.
- `motion.changed_px_channels` large + a real bbox when the pattern claims
  animation (frame-diff across 4s proves motion without eyeballing a video).
- **Read the screenshots** (`<prefix>-t0/t2/t4.png`) — the harness can't judge
  craft (blown bloom, unreadable layout); you can. A washed-white frame or
  shapeless geometry fails the pass even with zero errors.

Iterate the demo until clean. Never skip to step 3 on unverified code.

### 3. Distill into the KB note

Write `{vault}/30_Resources/EmptyOS/kb/notes/<slug>.md` (slug convention:
`viz-<discipline>`, e.g. `viz-animejs-three-adapter`):

```yaml
---
tags:
  - kb            # block-style, never inline arrays
kind: pattern
title: "Pattern: <what it scaffolds>"
created: <today>
updated: <today>
domain: viz
topic: <shape>    # immersive-scene / anim-explainer / ...
source: "viz demo/<name>.html (reproducible source of this note's code)"
author: ai
related:
  - <sibling pattern slugs>
---
```

Body = two sections:

1. **`## Engineering anatomy`** — the traps and the *concrete numbers*
   (evidence from this repo: prose warnings in presets did NOT stop bloom
   blowouts; pinned numbers did). Name each failure mode and its symptom so a
   model can self-diagnose ("if the frame washes white → threshold too low").
2. **`## Full reference scene`** — the demo file verbatim in a fenced block
   whose language is accepted for the target shape (check `_SHAPE_LANGS` in
   `apps/public/standard/viz/examples.py` — `html` is accepted everywhere).
   State the verification date + what was proven.

### 4. Verify pickup

- `GET /viz/api/examples` lists the slug (KB app path; falls back to a vault
  glob only under `30_Resources/KB/**/patterns/`).
- `GET /kb/api/notes/<slug>` returns the body with the fence intact — this is
  exactly what `resolve_pattern_examples` injects, and it fails soft, so a
  broken note silently injects nothing.

### 5. Prove it steers (optional but honest)

Generate once with `examples=["<slug>"]` on the shape the note targets and
compare against a bare generation. Expect partial adoption: models take the
*numbers and structure* readily but may keep their own idioms (e.g. strong
models kept hand-rolled rAF for organic motion — defensible; the bloom
discipline is what landed). Record what stuck in the note or the borrow log if
it changes the note's claims.

### 6. Consider the preset

If the lesson is short and numeric (thresholds, caps), also inline it into the
shape's preset in `apps/public/standard/viz/shared.py` — presets reach every
generation; pattern notes only reach opted-in ones. Python change → daemon
restart note per `.claude/rules/daemon-handling.md`.

## Anti-patterns

- **Prose-only pattern notes.** No fenced code accepted by the shape's langs =
  `resolve_pattern_examples` injects nothing. The note must carry code.
- **Distilling unverified code** — the whole point of step 2.
- **Inline `tags: [kb]`** — block-style only (CLAUDE.md § Development Gotchas).
- **A pattern per artifact.** Notes encode *disciplines* (bloom restraint,
  adapter usage, motion craft), not one-off scenes. If it doesn't generalize,
  it's a demo, not a pattern.
