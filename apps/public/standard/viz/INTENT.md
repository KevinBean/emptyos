# Intent — Viz

> Living design doc for `apps/viz/`. Edit as the app evolves.
> Birth certificate: `30_Resources/EmptyOS/grill/new-app-viz-2026-05-25.md`.
> Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why

EmptyOS has no equivalent of the claude.ai artifacts surface. Engineering
explainers — like the cable drum + ramp roller + trench-to-conduit
visualisation from 2026-05-21 — get produced outside EmptyOS and
re-imported by hand. Viz closes the loop: LLM writes a complete
standalone HTML file, vault stores it, iframe previews it, user
iterates by prompt.

Viz is the **deliberate counter** to `apps/personal/robot-modeller/`.

| `robot-modeller` (regulated) | `viz` (free) |
|---|---|
| Output must be topologically valid (URDF, joints, mesh tolerance) | Output must be visually correct |
| Correctness judged by compiler + shape check | Correctness judged by human eyes |
| Fix loop = LLM rewrites Python via SDK | Fix loop = LLM rewrites the whole HTML; human can also edit directly |
| Worth paying for: structural guarantees | Worth paying for: zero friction between intent and render |
| Articulated robots, mechanical CAD | Cable laying viz, single-line diagrams, charts, animated explainers |

Both apps serve adjacent verbs ("generate me a visual thing"), but they
register on *different* capabilities because the outputs aren't
comparable: a `.urdf` (kinematic body) and a `.html` (interactive artifact)
have nothing in common downstream. The split:

- `model` capability → robot-modeller → URDF + glTF (correctness by compiler).
- `artifact` capability → viz → standalone HTML (correctness by eye).

Viz is the local `artifact` provider; cloud providers (e.g. a future
"render via claude.ai artifacts" backend) would plug in alongside.

## Relationships

**Calls into** (`self.call_app(...)`):
- (none — viz is self-contained in v1)

**Emits:**
- `viz:created` — fires after `/api/generate` successfully writes a new artifact. Payload: `{id, shape}`. Reactor can use this to drop a journal breadcrumb when an artifact lands.
- `viz:updated` — fires after `/api/iterate` overwrites an existing artifact. Same payload shape.
- `viz:rendered` — fires when the iframe preview loads successfully on the client side. Payload: `{id}`. Used by the timeline drawer and future "first preview seen at" metrics.

**Listens for** (`@on_event`):
- (none)

**Capability provider registrations:**
- `artifact` — viz is the local provider (priority 0). Routes `self.artifact(prompt, shape="...", examples=[...])` calls through `VizApp.generate()`. See `_register_artifact_provider` in `apps/viz/app.py`.

**Two LLM paths, two routings — don't conflate:**
- **Generate** (`/api/generate`, `/api/generate-stream`) — single-shot `self.think_stream(...)` through the `think` capability. Provider chain respects `think.app.viz` settings override + the shared `EOS_UI.modelPill` chip in the toolbar.
- **Iterate** (`/api/iterate-stream`) — agent-driven via `agent-runtime.claude_cli_run` with Read+Edit tools scoped to the artifact's record dir **when agent-runtime is available** (surgical in-place edits, no output-budget risk). When the plugin is absent — cloud/demo deployments with no claude-cli — it **falls back** to `_iterate_via_think_stream`: a whole-file rewrite through the `think` capability (the configured provider / pill pin), streaming the same event shape. The frontend reads `started.agent` to label the two modes. Iterate is structurally an editor, not a generative call.

Never hard-error a user-facing feature on a missing agent-runtime: if a `think`-based path exists, fall back to it. The agent path is *preferred where present*, not *required*. If you want iterate to be CLI-configurable, make `_iterate_stream_events` read a `viz.iterate.cli` setting and dispatch to `text_cli_run` for non-claude CLIs.

## Shapes (presets)

Each shape is a system-prompt preset in `PRESETS` (`apps/viz/app.py`).
Adding a shape = one constant + one dict entry + one fence-language
whitelist row in `_SHAPE_LANGS` + one `<option>` in the UI dropdown.

- `3d-scene` — Three.js r128, fullscreen WebGL, OrbitControls, dimension HUD
- `svg-diagram` — hand-rolled SVG, engineering line weights, legend
- `schematic` — strict SVG with canonical electrical symbols + voltage labels
- `network-graph` — vis-network forceAtlas2, dark palette, count overlay
- `chart` — Chart.js (Plotly for 3D / heatmap), engineering accent
- `anim-explainer` — CSS keyframes + Anime.js for 3+ phase choreography
- `slide-deck` — reveal.js v5 dark theme, 5–15 slides, speaker notes
- `math-explainer` — KaTeX auto-render, prose + derivations + worked examples
- `mermaid` — mermaid.js v10 dark theme, one diagram per file

## Open questions

- **Iframe hot-reload mechanism.** v1 = manual reload + cache-buster on
  every iterate call. Upgrade to live reload (poll `record.updated`, or
  WebSocket via the `realtime` service) when it actually annoys.
- **Output folder lifecycle.** v1 = never auto-archived; user manages
  via the file panel or a future boards view.
- **Pre-generation cost preview.** Today the user types a brief and
  pays the LLM cost on submit. A "estimate tokens / cost first" mode
  may matter once cloud spend on this becomes a thing.

## Future

- Streaming generation — show the HTML being typed live in a read-only
  textarea while it streams, before the iframe renders the final file.
  Matches claude.ai's artifact UX. Costs one SSE endpoint.
- Optional headless-load sanity check (Playwright opens the HTML in
  the background, captures console errors, surfaces them so the user
  knows the artifact loads cleanly).
- Per-shape thumbnail capture on first preview (Playwright screenshot)
  for the record list and timeline drawer.
- Hub panel — "Recent artifacts" stat tile.
- Voice intent — `aura, sketch a 3D cable drum`.
- Consumer-app affordance — `EOS_UI.vizButton(prompt_template)` that
  any other app can drop into its detail pages to offer "explain this".
  Cables (`📊 View as scene`) and projects (`📊` Gantt) are the first
  two ad-hoc consumers; extract when a third arrives.
- Streaming generation — claude.ai-style live HTML typed into a read-only
  textarea while it streams, before the iframe renders the final file.
  Needs an SSE endpoint + a chunked-fence parser.
