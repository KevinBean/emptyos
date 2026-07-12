---
name: eos-article-diagrams
description: Author conceptual diagrams for Published-site articles to the house standard — hand-written SVG in the GitHub-dark style, rasterized to a shipped 2x PNG, referenced with takeaway-bearing alt text. Use when the user says "add diagrams/graphs to this article", "diagram this post", "make a figure for X", or when drafting a post whose argument walks a structure (stack, loop, classification, before/after). NOT for data charts from real datasets (use the dataviz skill directly), app-UI screenshots (eos-screenshot), or generated cover art (publish app generate-cover).
---

# EmptyOS Article Diagrams

Diagram *authoring* is judgment work — what to diagram, what to say, how to lay
it out — so it lives here in conversation mode, not in an app `think()` call
(intricate SVG needs a strong model; see `.Codex/rules/model-ability.md`).
The *mechanical* half is deterministic and lives in the tooling: SVG → 2× PNG
via `emptyos/sdk/svg_raster.py`, exposed as `scripts/rasterize_svg.py`, the
publish endpoints (`GET /publish/api/diagrams`,
`POST /publish/api/diagrams/rasterize`), and a self-healing hook that
re-rasterizes stale pairs on every site build.

Canonical standard: **vault `AGENTS.md` § "Article diagram standard"**.
Reference implementations: `{vault}/30_Resources/Published/images/harness-*.svg`
and `operable-*.svg`.

## Procedure

### 1. Choose what earns a diagram

A structure the prose *walks through* earns one: a stack, a loop, a
classification, a convergence, a before/after. A list of examples does not.
2–4 per article, not one per section. Each diagram must make a single
argument the reader could quote — if you can't write that argument as one
sentence, it's not a diagram yet.

### 2. Author the SVG (the source of truth)

Write it by hand into `{vault}/30_Resources/Published/images/` as
`<post-shortname>-<concept>.svg` (kebab-case; one shared prefix per article so
its diagrams sort together).

House tokens (GitHub-dark):

| Element | Value |
|---|---|
| Canvas | `#0d1117`, `viewBox` width 860, height to fit |
| Cards | `#161b22` (alt `#1b2030`), `rx="10-12"`, stroke-width 1.7–2 |
| Hairlines/dividers | `#30363d` |
| Text | `#e6edf3` primary, `#8b949e` muted |
| Font | `Inter, Segoe UI, system-ui, sans-serif`; monospace for code strings |
| Type scale | title 22/700 · subtitle 13 muted · box titles 14.5–16/700 · sublabels 11.5 muted · footer caption 12 muted |

Color by job, fixed categorical order (strokes + heading text, never fills):
blue `#58a6ff` → teal `#39d3bb` → purple `#b39dff` → orange `#f0883e`.
Status: teal = good/auto, orange = warning/gated, red `#f85149` = danger/never
(reserved — never a fourth series color). Identity is never color-alone:
every colored element carries a text label. This palette already passes CVD
separation and ≥3:1 contrast on `#0d1117`; if you add a NEW hue, validate it
first with the dataviz skill's `scripts/validate_palette.js --mode dark`
(load the dataviz skill for the procedure).

Layout habits from the reference set: title + one-line muted subtitle at top
center; a muted footer caption carrying the takeaway; arrows via `<marker>`
defs; `&` escaped as `&amp;`.

### 3. Rasterize to the shipped 2× PNG

```bash
python scripts/rasterize_svg.py "{vault}/30_Resources/Published/images/<name>.svg"
# or sweep everything stale under the images dir:
python scripts/rasterize_svg.py --stale "{vault}/30_Resources/Published/images"
```

(Equivalent when the daemon is up: `POST /publish/api/diagrams/rasterize`
`{"site_id": "default"}`. Builds also self-heal stale pairs automatically, so
a hand-edited SVG can't ship with an outdated PNG.)

### 4. Render and eyeball

Read the PNG back (the Read tool renders it) and check for label collisions,
dead space, overflow. Trim the SVG and re-rasterize; don't ship the first
render unseen.

### 5. Reference the PNG in the post

```markdown
![<the diagram's full takeaway, written as prose>|637](<name>.png)
```

- Reference the **PNG** by bare filename (the builder resolves it
  recursively); `|637` is the display width.
- The alt text is the text fallback for RSS and screen readers — write what
  the diagram *says*, not what it looks like. It replaces any parallel ASCII
  version in the body: delete the ASCII block the diagram supersedes.

### 6. Verify

`GET /publish/api/diagrams?site_id=default` → the new pair listed, not stale.
If the post is `publish: true`, rebuild (`POST /publish/api/build`) and check
the built page; drafts need no build.

## Rules

- **SVG never ships as the reference** — share surfaces (LinkedIn, email)
  don't embed it. The SVG stays beside the PNG as the editable source; edit
  the SVG, never the PNG.
- **Never `sed` on vault `.md`** — use the Edit tool or Python (vault
  AGENTS.md; encoding-corruption history).
- Real-data charts (benchmarks, time series) are the dataviz skill's domain —
  follow its full procedure and validator, then ship through the same
  SVG→PNG pairing here.
