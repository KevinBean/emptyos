# Artifact Element-Edit Rule — highlight-to-edit on generated HTML

An app that generates a standalone HTML artifact (designer's web page, viz's
SVG/slide/diagram) can let the user **click one element and regenerate only
that element** — keeping the rest of the document byte-for-byte. This is
LangChain Open Canvas's "highlight-to-edit" UX, mapped onto EmptyOS primitives.
It is a scoped tweak, **additive to** (never a replacement for) the app's
whole-file Iterate path.

**Shared core:** `emptyos/sdk/html_element_edit.py` (`propose_element_edit`,
pure). **Addressing primitive:** `emptyos/sdk/html_anchors.py`
(`inject_anchors` / `extract_element` / `merge_inline_style`). **Gate:**
`emptyos/sdk/sandbox.py` (`SandboxedWrite`). **Shim:**
`emptyos/web/static/eos-edit-shim.js`. **Consumers:** `apps/public/standard/designer`
(first), `apps/public/standard/viz` (second).

**Markdown variant (third consumer — `apps/public/standard/publish`).** When the
artifact on disk is **markdown** (a publish article), not HTML, the HTML shown in
the preview is a lossy one-way render, so a clicked anchor cannot resolve to an
HTML span — it must resolve to a **markdown source block**. That path uses sibling
SDK cores instead of the HTML ones: `emptyos/sdk/markdown_blocks.py`
(`split_markdown_blocks` + `stamp_markdown_blocks` — fingerprint-verified
top-level anchor zip that disables on any splitter↔render desync) and
`emptyos/sdk/markdown_block_edit.py` (`propose_markdown_block_edit` — instruction-
only, **no knob**; rewrites the one markdown block and splices the `.md`).
`SandboxedWrite` still gates the diff/staleness on the `.md`. Editable kinds are a
safety allow-set (paragraph/heading/list/blockquote/callout/image); code fences,
tables, TOC, and footnote/ref defs are excluded — the markdown analogue of the
canvas exclusion below. Wiring lives in `apps/public/standard/publish/editing.py`
behind the same dark flag `feature.element-edit.enabled`. See the "core does not
port unchanged" note: only the addressing + block-rewrite cores are markdown-
specific; the shim, `SandboxedWrite` gate, and propose/preview/confirm UX are
shared.

## Why it sits on existing primitives — and adds NO capability

Element-edit composes three things EmptyOS already has; it is app UX, not a
kernel verb. **Do not add an `edit` capability.**

1. **Positional addressing** — `html_anchors.inject_anchors` stamps
   `data-eos-el="eN"` on editable elements at persist time; `extract_element`
   resolves a clicked anchor to the exact source char-span (mirrors the
   browser's implicit tag stack, so auto-closed `</p>`/`</li>` don't desync).
2. **`think`** — a single-element rewrite is a bounded task; the app passes a
   `think_fn(system, user) -> str` closure into the pure core. No
   `min_ability` (weak models handle one element well —
   `.claude/rules/model-ability.md`).
3. **Propose/preview/confirm + staleness** — `SandboxedWrite` captures the
   proposed full document, renders a unified diff, and refuses to apply if the
   file changed since capture (`.claude/rules/proposed-action.md`). The diff is
   small because only one element changed, and the gate stays because a
   free-form HTML edit is **never autopilot-eligible** — the diff IS the value
   (`.claude/rules/autopilot-grants.md`).

## The shared unit vs what stays app-side

`propose_element_edit(html, el_id, *, instruction|knob, think_fn, style_hint,
anchor_attr) -> ElementEdit` is the **only** shared piece: anchor → extract
span → produce a replacement (LLM rewrite OR deterministic style knob) →
validate (same outer tag, anchor preserved, not a whole document) → splice →
return the full new document. It is pure (no `self`, no I/O), so it unit-tests
without a daemon (`tests/test_unit_html_element_edit.py`).

Everything app-specific **stays in the app's `editing.py`**: the `think_fn`
closure, the `scene.html` vs `page.html` read path, `SandboxedWrite`
capture/apply, the `record.md` bump, the `<app>:updated` emit, and the
`?edit=1` shim injection. Do **not** extract a shared persist/SandboxedWrite
orchestrator — designer and viz genuinely differ (file name, `embeds`/`style`
vs `shape` frontmatter); forcing that into the SDK is over-abstraction.

**Two kinds, one pipeline.** `instruction` (natural language) → the LLM rewrites
just that element via `ELEMENT_EDIT_SYSTEM`; the reply is fence-stripped and
validated. `knob` (color/background/font-size/padding/text-align) → a
deterministic single inline-style merge, **no LLM**, values constrained so a
knob can never inject arbitrary CSS.

## When NOT to use it — the canvas exclusion (load-bearing)

Element-edit requires **addressable DOM/SVG elements**. It does NOT apply to
canvas-rendered output, where there is nothing to highlight:

- viz `3d-scene` (Three.js → `<canvas>`), `chart` (Chart.js → `<canvas>`),
  `network-graph` (vis-network → `<canvas>`) — **excluded**.
- viz `anim-explainer` — excluded too (its value is the timeline, not
  per-element edits).
- viz DOM-structured shapes (`svg-diagram`, `schematic`, `math-explainer`,
  `mermaid`, `slide-deck`) — **supported**.

`extract_element` does NOT detect a `<canvas>`, so the per-shape allow-set is
the only protection — encode it as a frozenset in the app
(`viz/shared.py::SHAPE_SUPPORTS_ELEMENT_EDIT`) and gate both the anchor stamp
and the propose endpoint on it. When in doubt, exclude — a new shape is opt-in.

## Gating + the off-state guarantee

Two gates compose, both checked at the persist choke-point and the endpoints:

- **Dark flag** — `[apps.<id>] feature.element-edit.enabled` (default false, read
  via `app_config`; no `emptyos.toml`/manifest commit — per
  `project_feature_pipeline_flag_default_dark`).
- **Per-shape support** (apps with shapes) — the allow-set above.

Anchors are stamped **only when both hold**. When the flag is off, persisted
artifacts are **byte-identical** to the pre-feature output, and
`/api/edit/propose` returns `{ok:false,"element edit is disabled"}`. This is the
regression contract — verify it.

## Frontend contract

`GET /<app>/api/html/<id>?edit=1` injects
`<script src="/static/eos-edit-shim.js"></script>` (only when the flag is on).
The preview iframe is `sandbox="allow-scripts"` (opaque origin), so the shim
resolves the clicked element *inside* the frame and `postMessage`s the anchor
out; the parent validates by **`e.source === iframe.contentWindow` identity**,
not origin (an opaque frame's origin is the string `"null"`). The parent then
calls `/api/edit/propose` → renders the diff → Apply/Reject. Reference page JS:
`designer/pages/designer.js`, viz `pages/index.html` (`onEditMessage`,
`_propose`, `applyEdit`).

## Graduation paths

- **Per-artifact version history / time-travel** — both apps store
  `history:[{ts,prompt}]` (prompts only, no rendered snapshot), so an edit
  can't be *undone to a prior render*. The honest next borrow from Open Canvas:
  `outputs/<id>/versions/<n>/` ring + picker, tied to the `outputs/` authorship
  convention (`.claude/rules/authorship-boundary.md`) and the
  `[provides.timeline]` both apps already declare (a version list IS the
  artifact's `past`). Separate extraction with its own first-consumer; build
  when someone wants restore-to-version.
- **Reflections (passive style memory)** — Open Canvas distills style prefs from
  edit history. EmptyOS's substrate already exists (agent-context bus + KB
  `kind:pattern` few-shot via `resolve_pattern_examples`). Rejected for now:
  needs an edit-history corpus (doesn't exist until version history ships) and
  would duplicate the bus/KB. Revisit only if per-user style drift is felt.

## Cross-references

- `.claude/rules/proposed-action.md` — the propose/preview/confirm paradigm;
  diff-shaped edits like this are never autopilot-eligible.
- `.claude/rules/model-ability.md` — why no `min_ability` on a one-element rewrite.
- `.claude/rules/multi-module-apps.md` — the bound-helper pattern `editing.py` uses.
- `emptyos/sdk/html_anchors.py` — the addressing primitive (kept pure; the prompt
  lives in `html_element_edit.py`, not here — two unrelated consumers of anchors).
