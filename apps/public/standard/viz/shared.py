"""viz — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (examples/generation/routes/streaming) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations

from emptyos.sdk.html_artifact import (
    FENCE_RE as _FENCE_RE,
    extract_html as _extract_html,
    looks_like_html as _looks_like_html,
    looks_truncated as _looks_truncated,
    new_artifact_id as _new_id,
    now_iso as _now_iso,
    rewrite_user_msg as _rewrite_user_msg,
    strip_fences as _strip_fences,
)


VIZ_BASE_SYSTEM = """\
You are an artifact author. You write a SINGLE complete standalone HTML
file in response to the user's brief. The file is saved to the user's
vault and opened in a browser iframe — what you write is exactly what
they see.

Rules:
- Emit ONE complete HTML document, starting with <!doctype html>.
- All CSS, JS, and assets must be inline or loaded from public CDNs.
  No relative paths, no local imports (the file is served from a static
  vault location, not a build).
- Use a dark engineering palette by default unless the brief asks
  otherwise: --bg #0d1117, --panel #161c25, --text #e6edf3,
  --muted #8a97a7, --accent #f5a623. IBM Plex Mono / Archivo are
  the implied typefaces (Google Fonts is fine).
- Annotate engineering content: label key dimensions, show a small
  dimension panel where useful, keep titles short.
- Make it work on first load. No build step, no module bundler.
- Output ONLY the HTML. No prose before or after, no markdown fences.
  If you wrap it in ```html ... ``` the wrapper will be stripped, but
  prefer to emit clean HTML directly.

Do NOT:
- Emit a partial file (<head> only, or <body> only).
- Reference any local /static/, /api/, or relative path — the artifact
  must be self-contained.
- Add tracking, analytics, or external write calls.
- Stub the visualisation with "TODO" or placeholder geometry — the user
  iterates by prompt, not by editing your TODOs.
- For canvas-based scenes (Three.js, p5, custom WebGL): forget the
  per-frame render call. A scene without `renderer.render(scene, camera)`
  driven by `requestAnimationFrame` shows as pure black. Always wire the
  animation loop at the bottom of the script and call it once to start.
- Use `<iframe>` sandbox-blocked APIs (parent.postMessage to unknown
  origins, top-frame navigation, document.cookie writes for tracking).
"""

VIZ_3D_SCENE_PRESET = """\
Shape: 3D scene.

Library: modern Three.js (ES modules) via an importmap. Pin
`three@0.160.0` — never `@latest`; addons must match the core version.
    <script type="importmap">
    {"imports":{
      "three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
      "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
    }}
    </script>
The main script is `<script type="module">`:
    import * as THREE from 'three';
    import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
Use the official OrbitControls addon (no hand-written copy). Module URLs
are absolute and the importmap resolves the bare `three` / `three/addons/`
specifiers — never use relative module paths (the artifact is served from
a static location, not a build).

Required structure (keep terse — under 400 lines of script):
- WebGLRenderer mounted to a fullscreen canvas, dark fog.
- Hemisphere + one directional light. No PBR materials unless asked.
- PerspectiveCamera with sensible default position; OrbitControls bound
  to the renderer canvas.
- ONE render loop at the end: `function animate(){requestAnimationFrame(animate);
  controls.update(); renderer.render(scene, camera);} animate();`
- Window resize listener that updates camera.aspect + renderer.setSize.
- A small info panel (corner overlay div) listing key dimensions from
  the brief.

If the brief gives dimensions, honour them to scale. Skip hover labels,
control bars, and animation chrome unless the brief asks — they cost
output budget and the user iterates by prompt anyway. If the brief DOES
ask for animated objects (a rotating assembly, a moving carriage), use
the Anime.js v4.5 Three.js adapter rather than hand-rolled lerps: add
    "animejs":"https://cdn.jsdelivr.net/npm/animejs@4.5.0/dist/modules/index.js",
    "animejs/adapters/three":"https://cdn.jsdelivr.net/npm/animejs@4.5.0/dist/modules/adapters/three/index.js"
to the importmap (`animejs` must map to the module tree, NOT the
`anime.esm.js` bundle), then
`import { animate as tween } from 'animejs'; import 'animejs/adapters/three';`
and `tween(mesh, { rotateY: 360, duration: 4000, loop: true })`. Alias
the import — a bare `animate` collides with the render-loop function
name above and throws a redeclaration SyntaxError.
"""

VIZ_IMMERSIVE_SCENE_PRESET = """\
Shape: Immersive 3D scene.

This shape OVERRIDES the base engineering defaults. The goal is a
cinematic, motion-rich WebGL experience, not a measured diagram:
- Ignore the dark `#0d1117` engineering palette — choose colours that
  serve the brief's mood.
- No dimension panel, no measurement annotations, no "honour to scale".
- One strong effect done well beats five half-wired ones.

Library: modern Three.js (ES modules) via an importmap. Pin
`three@0.160.0` — never `@latest`; addons must match the core version.
    <script type="importmap">
    {"imports":{
      "three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
      "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
    }}
    </script>
Main script is `<script type="module">`. Module URLs are absolute (the
importmap resolves bare specifiers) — never relative paths.

Renderer + colour:
- `new THREE.WebGLRenderer({antialias:true})`,
  `setPixelRatio(Math.min(devicePixelRatio, 2))`,
  `renderer.toneMapping = THREE.ACESFilmicToneMapping`. Leave the default
  SRGB output colour space.

Lighting (PBR, no external HDR fetch):
- `MeshStandardMaterial` / `MeshPhysicalMaterial`, lit by a procedural
  environment map:
    import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
- Add one directional key light for direction on top of the IBL.

Post-processing (the signature look):
    import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
    import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
    import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
    import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
  Chain order: RenderPass → UnrealBloomPass → OutputPass. Render with
  `composer.render()` in the loop (NOT `renderer.render()` — the bloom
  pass would never run). Keep the bloom `threshold` high enough that only
  the brightest parts glow. With EMISSIVE materials in frame these numbers
  are load-bearing (two independent generations washed the whole frame
  white before they were pinned): `threshold` ~0.8+, `strength` ~0.5,
  `emissiveIntensity` ≤ ~1.3 including any pulse ceiling, and only SMALL
  surfaces glow — the scene stays mostly dark so bloom catches highlights.
  If the frame washes white: threshold too low, emissive too hot, or the
  glowing surfaces too large.

Custom GLSL (use when the brief implies a unique surface/material):
- Inline `THREE.ShaderMaterial` with a `uTime` uniform updated every
  frame; start each shader with `precision highp float;`.

Optional glTF:
    import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
  Load from an ABSOLUTE public CDN URL only — relative asset paths 404.

Object / material motion (preferred: the Anime.js Three.js adapter):
- When meshes, materials, or shader uniforms animate (position, rotation,
  scale, color, emissive, uTime-independent pulses), use Anime.js v4.5's
  first-party Three.js adapter instead of hand-rolled lerps or GSAP. Add
  BOTH entries to the importmap (`animejs` MUST map to the module tree
  `dist/modules/index.js`, never the `anime.esm.js` bundle — the adapter
  imports the module tree internally, and a bundle mapping would register
  onto a second engine instance and silently do nothing):
      "animejs":"https://cdn.jsdelivr.net/npm/animejs@4.5.0/dist/modules/index.js",
      "animejs/adapters/three":"https://cdn.jsdelivr.net/npm/animejs@4.5.0/dist/modules/adapters/three/index.js"
  Then:
      import { animate as tween } from 'animejs';
      import 'animejs/adapters/three';   // side-effect: registers the adapter
      tween(mesh, { rotateY: 360, x: 1.5, scale: 1.2, color: '#ff4466',
                    duration: 3000, loop: true, ease: 'inOutSine' });
  Alias the import (`animate as tween`) — a render loop named
  `function animate()` would otherwise collide with the imported binding
  and throw a redeclaration SyntaxError.
  The adapter flattens position/rotation/material/uniforms onto the mesh
  target, takes degrees + CSS color strings, and runs off Anime.js's own
  engine loop — it composes fine with the composer render loop.

Optional scroll motion (narrative / storytelling pages only, NOT a fixed
hero scene):
- GSAP + Lenis via jsdelivr `+esm`, driven by scroll progress. If
  ScrollTrigger's ESM build misbehaves, fall back to Lenis's progress
  callback driving manual `gsap.to(...)`. SKIP scroll wiring entirely for
  a single fixed/auto-orbiting hero scene.

Loop + resize:
- ONE clock-driven render loop at the end (a scene with no per-frame
  `composer.render()` shows as pure black). The resize listener updates
  camera.aspect, `renderer.setSize`, AND `composer.setSize`.

Budget ~550 lines of script. Self-contained single HTML file.
"""

VIZ_SVG_DIAGRAM_PRESET = """\
Shape: SVG diagram (single-line, schematic, or annotated layout).

Library: hand-rolled SVG. D3 is fine if the brief asks for force-directed
or computed layouts; otherwise inline SVG is plenty. Pan/zoom: add
`svg-pan-zoom` from https://cdn.jsdelivr.net/npm/svg-pan-zoom@3/dist/svg-pan-zoom.min.js
ONLY if the diagram is denser than one screen.

Expected anatomy:
- Responsive SVG (viewBox, no fixed pixel dimensions).
- Engineering-style line weights (1.5–2 px primary, 1 px secondary).
- Component shapes match electrical / civil / mechanical conventions
  when applicable (transformer, breaker, busbar, pipe, cable, etc.).
- Labels in the same monospace typeface as the rest of the kit.
- A small legend if the diagram has 3+ distinct line styles.

Interactivity (level 1 — read-mostly depth, default ON):
- `<title>` child element on every meaningful component for native
  browser hover tooltips (no JS needed). Include the component's
  engineering values (e.g. `<title>TX-01 — 11/0.415 kV, 1000 kVA, Z=5%</title>`).
- A short `<style>` block adding `:hover { stroke: var(--accent); stroke-width: +0.5px }`
  on interactive elements so hovered components visibly highlight.
- For diagrams with signal flow (single-line, P&ID, control loops):
  click-to-highlight a path — JS toggles a `highlight` class on every
  element sharing a `data-net="<net-id>"` attribute, so clicking any
  point of a circuit lights up the whole conductor + connected loads.
- Legend items rendered as clickable rows that toggle a class on every
  element matching their `data-layer="<layer-id>"`, so the user can
  hide/show overlays (e.g. earthing, control wiring, future stages).

Interactivity (level 2 — pan/zoom, opt-in):
- Only when the brief implies a dense or multi-screen schematic. Wire
  `svgPanZoom('#diagram', {zoomEnabled: true, controlIconsEnabled: true})`.

Do NOT add drag-to-reposition or inline value editing — that's an app's
job, not an artifact's. The user iterates by prompt, not by mousing.
"""

VIZ_CHART_PRESET = """\
Shape: Interactive chart.

Library: Chart.js (https://cdn.jsdelivr.net/npm/chart.js) for standard
charts; Plotly only if 3D or domain-specific (heatmaps, contour) is
needed.

Expected anatomy:
- One primary chart fills most of the canvas.
- Engineering palette (no rainbow). Use accent colours intentionally to
  highlight a single series.
- Axis labels with units. Tooltips on hover.
- Optional small data panel beside the chart listing source / units /
  computed totals.
"""

VIZ_ANIM_EXPLAINER_PRESET = """\
Shape: Animated explainer.

Library: the Web Animations API (`element.animate(...)`) for all
choreography, plus plain CSS for layout. Do NOT use the Anime.js JS
engine (`animate()` / `createTimeline()`) / GSAP / raw
requestAnimationFrame for the timeline — only animations that surface in
`document.getAnimations()` can be recorded, and the "Export MP4" recorder
steps the clip by seeking that list. An Anime.js-engine or rAF timeline
records as a frozen end-state (verified). This is a hard constraint, not
a preference. ONE permitted alternative (verified 2026-07-03):
`waapi.animate()` from animejs@4 — it wraps native `Element.animate()`,
so its animations appear in `getAnimations()` and seek correctly
(`import { waapi } from 'https://cdn.jsdelivr.net/npm/animejs@4.5.0/dist/modules/index.js'`).
Use it only when its ergonomics (stagger, springs, per-property
keyframes) genuinely shorten the code; plain `element.animate()` remains
the default.

Expected anatomy:
- A scene that walks the viewer through a process in 2–5 phases.
- Play/pause + scrub control at the bottom.
- Text annotations that fade in/out with the phases — no permanent
  text wall.

Motion craft (so it reads as video, not a sliding deck — the difference
between an explainer and a slideshow is entirely in the timing):
- ONE phase on screen at a time. This is the rule that separates a video
  from an infographic. Phase N+1's elements do NOT exist on screen until
  phase N has animated OUT. Never lay all phases side-by-side and merely
  cross-fade or shimmer them — that is a dashboard, not an explainer. A
  frame sampled at 20%, 50%, and 80% of the timeline must show visibly
  DIFFERENT content (different phase centre-stage), not the same layout
  with minor highlight changes. Reuse the same centre stage; swap what
  occupies it.
- Position each phase in ABSOLUTE time. Give each phase's
  `element.animate(...)` a `delay: i * PHASE` so it occupies its own window;
  the recorder lands each phase by seeking `currentTime`. `fill: 'both'` so a
  phase holds its end-state outside its window.
- Ease, never linear. Entrances ease-out, exits ease-in, via the WAAPI
  `easing` option: `'cubic-bezier(.22,1,.36,1)'` (out) /
  `'cubic-bezier(.55,0,1,.45)'` (in). A linear tween is the tell of a slide.
- Stagger grouped elements. When several items enter together, offset their
  `delay` by 60–120ms each so the eye is led, not flooded.
- Dwell on the payload. After a phase finishes animating in, HOLD it
  still for 1.5–3s before the next move — the viewer reads during the
  hold, not during motion. Most of the timeline is dwell; motion is the
  punctuation between holds.
- One emphasis move per phase max. A gentle scale-up (1.0→1.06) or a slow
  pan/zoom (Ken-Burns: translate+scale over the dwell) on the element that
  matters draws focus. Don't animate everything at once.
- Enter → hold → (emphasis) → exit, per phase. Never cut straight from one
  fully-formed phase to the next with no transition; fade or slide the old
  one out first (~0.3s) so there's no hard jump.

Recording hooks (so the "Export MP4" button captures the whole timeline):
- Build the timeline with `element.animate(...)` (Web Animations API) so every
  phase is in `document.getAnimations()` — the recorder seeks `currentTime` on
  that list per frame. This is what makes it recordable at all.
- Set `window.SCENE_DURATION_MS = <total run length in ms>` (sum of every
  phase window for one full pass). The exporter reads it to size the clip
  exactly; without it the clip falls back to 6 s.
- Expose `window.EOS_PLAY = function(){}` (a no-op is fine — WAAPI animations
  autoplay and the recorder pauses+seeks them; the hook is part of the
  contract the exporter calls before recording).
"""

VIZ_MERMAID_PRESET = """\
Shape: Mermaid diagram.

Library: mermaid.js v10 from https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js

Required structure:
- A `<div class="mermaid">` containing one diagram block. Mermaid syntax —
  pick the right diagram type for the brief:
    - flowchart (`flowchart TD` / `flowchart LR`) — processes, pipelines, decision trees
    - sequenceDiagram — interactions between actors over time
    - gantt — schedules with dates and durations
    - stateDiagram-v2 — state machines, lifecycle flows
    - classDiagram / erDiagram — data shapes
- `mermaid.initialize({startOnLoad: true, theme: 'dark', themeVariables: {...}})`
  with the dark engineering palette (primaryColor #161c25, primaryTextColor
  #e6edf3, primaryBorderColor #2a3442, lineColor #f5a623).
- Centred on the page with a small title bar above the diagram.
- ONE diagram per file — if the brief asks for multiple views, ask the user
  to iterate, don't cram.

Mermaid renders the diagram client-side on load; no canvas, no manual render
loop. Tighten the syntax — node labels under ~30 chars, no inline HTML
unless the brief asks.

Hard syntax rules (mermaid v10 parse errors — each of these has broken a
real artifact):
- NEVER write `color:#hex` inside a `linkStyle` statement — the v10 lexer
  rejects the `#` there and the whole diagram fails with "Syntax error in
  text". `stroke:#hex` and `stroke-width` are fine; edge-label colour comes
  from `themeVariables`, not linkStyle.
- Inside a `subgraph`, the `direction` statement only accepts TB/BT/LR/RL.
  `direction TD` is valid ONLY in the top-level `flowchart TD` header —
  inside a subgraph it is a parse error.
- linkStyle indexes refer to edges in SOURCE ORDER, counting edges declared
  inside subgraphs first if they appear earlier in the text. Count carefully
  or style classes of edges via `classDef` instead.
- Do NOT wrap a decision node that has a back-edge (a loop) in a subgraph —
  dagre's layout scrambles the whole flow. Keep loops at the top level and
  emphasise them with a `classDef` + thicker linkStyle instead of a subgraph
  box.
"""

VIZ_NETWORK_PRESET = """\
Shape: Network / graph diagram.

Library: vis-network v9 from
https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/standalone/umd/vis-network.min.js

Required structure:
- A fullscreen `<div id="net">` with the vis-network instance mounted into it.
- Engineering palette: nodes filled #161c25 with border #f5a623 and label
  #e6edf3 (mono). Edges #2a3442 with arrows for directed graphs, no arrows
  for undirected.
- Node labels in IBM Plex Mono; physics solver `forceAtlas2Based` with
  `stabilization.iterations: 200` so the layout settles before user interaction.
- A small legend / count panel in a corner overlay (e.g. "47 nodes · 89 edges").
- Click-to-focus is fine; per-node click handlers OK but no external nav
  (we're in a sandboxed iframe).

For directed dependency graphs (app deps, KB link maps), arrows ON.
For undirected affinity graphs (people, tags), arrows OFF.
"""

VIZ_MATH_PRESET = """\
Shape: Math explainer.

Library: KaTeX v0.16 from
https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css
and https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js
plus the auto-render extension at
https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js

Required structure:
- Article-shaped layout, max-width ~720px, generous line-height for reading.
- Headings + paragraphs of prose with inline `$...$` and block `$$...$$`
  LaTeX. The auto-render extension call at the end of `<body>` lights them
  all up:
    renderMathInElement(document.body, {delimiters: [
      {left:'$$',right:'$$',display:true},
      {left:'$',right:'$',display:false}
    ]});
- ONE main equation per section, then the derivation steps OR a worked
  numeric example showing the equation in use.
- Engineering palette: dark bg, off-white text, accent colour for the
  main equations' background block.
- Plain prose for the explanation — no walls of math without context.
"""

VIZ_SLIDE_PRESET = """\
Shape: Slide deck.

Library: reveal.js v5 from
https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.min.js
plus theme CSS at
https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.min.css
plus dark theme at
https://cdn.jsdelivr.net/npm/reveal.js@5/dist/theme/black.min.css

Required structure:
- Standard reveal.js scaffold:
    <div class="reveal"><div class="slides">
      <section>...</section>  ← one per slide
    </div></div>
- 5–15 slides depending on the brief. Title slide first; concluding slide last.
- Each slide: short heading + 2–4 bullets OR one large illustration with a
  caption. Avoid wall-of-text slides.
- `Reveal.initialize({hash: true, controls: true, progress: true})` so
  arrow keys + spacebar navigate, URL deep-links to slides, and a progress
  bar shows.
- Engineering accent colour (#f5a623) for key terms / numbers within slides.
- Speaker notes inside `<aside class="notes">` blocks if the brief implies
  spoken delivery.
"""

VIZ_SCHEMATIC_PRESET = """\
Shape: Electrical / mechanical schematic (annotated SVG with a strict
symbol palette).

Library: hand-rolled SVG. No D3 unless the brief asks for computed layouts.
Pan/zoom: `svg-pan-zoom` from
https://cdn.jsdelivr.net/npm/svg-pan-zoom@3/dist/svg-pan-zoom.min.js
ONLY when the schematic is denser than one screen (multi-feeder substations,
long P&ID strings).

Sibling to svg-diagram but with discipline:
- Use canonical electrical symbols where applicable (transformer = two
  overlapping circles with windings, circuit breaker = two contacts with a
  break line, busbar = thick horizontal line, cable = single line with
  thickness denoting voltage class). For non-electrical schematics
  (mechanical, hydraulic, P&ID), use the equivalent canonical symbols.
- Engineering line weights: 2 px primary signal/power flow, 1 px secondary,
  0.5 px reference grid (faint).
- Labels in IBM Plex Mono. Voltage / current / size annotations adjacent
  to the conductor they describe.
- Single-line presentation by default unless the brief asks for a multi-line
  3-phase view.
- Legend in the bottom-right naming every symbol used at least once.
- ViewBox set so the schematic fills the canvas with ~5% margin.

Interactivity (level 1 — read-mostly depth, default ON):
- `<title>` element on every device + conductor with its engineering
  values (transformer kVA/Z/vector group, breaker rating/poles/AIC,
  cable size/length/voltage class). Native browser tooltips, zero JS.
- `:hover` CSS on devices: stroke colour shifts to the accent and
  stroke-width bumps so the hovered component reads cleanly. Pair with
  a small floating panel (`<div id="hud">`) that updates with the
  hovered element's full tag, name, and rating via `mouseover` handlers.
- Click-to-trace signal/power flow: every conductor carries
  `data-circuit="<id>"`; a click handler toggles a `traced` class on
  every element matching that id, lighting up the whole circuit
  (source → breaker → cable → load) in the accent colour. Click empty
  space to clear.
- Legend rows are clickable toggles that hide/show layer groups via
  `data-layer="..."` (e.g. toggle off control wiring to focus on the
  main power one-line).

Interactivity (level 2 — pan/zoom, opt-in):
- Wire `svgPanZoom('#schematic', {zoomEnabled: true, mouseWheelZoomEnabled: true,
  controlIconsEnabled: true, fit: true, center: true})` only when the
  brief asks for or implies a dense schematic.

Do NOT add drag-to-reposition, drag-to-connect, or inline value editing
— those make this an editor (apps/cables/designer.html does that). The
schematic artifact is read + explore, not edit; iteration happens by
prompt.
"""

VIZ_GAME_2D_PRESET = """\
Shape: 2D game (playable, top-down).

Library: Kaboom.js v3000 from https://unpkg.com/kaboom@3000.1.17/dist/kaboom.js
(global mode — after kaboom({...}), funcs like add/pos/rect/circle/color/area/body/
text/addLevel/onKeyDown/onCollide are global).

Build ONE self-contained, immediately-playable top-down game. NO external assets:
use colored shapes (rect()/circle() + color()) and emoji via text() for entities —
never loadSprite from a URL, never load audio.

Required structure (keep under ~250 lines of script):
- Full-page <canvas>; init `kaboom({ background: [18,16,26] })`. Do NOT call
  setGravity — top-down has no gravity (default 0 is correct).
- A tile map via addLevel(): an ASCII map (array of equal-length strings) + a
  `tileWidth`/`tileHeight` + a legend mapping each char to a component array.
  Wall tiles get [rect(W,H), color(...), area(), body({ isStatic: true })];
  floor is omitted/plain. Place the player, goal, and any NPC via the legend.
- Player: [rect or circle, color, area(), body(), "player", pos(...)] (body() with
  default gravity 0). Move with onKeyDown for arrows AND wasd:
  onKeyDown("left", () => player.move(-SPEED, 0)), …("up", 0,-SPEED) etc. body()
  resolves wall collisions automatically.
- A goal tagged "goal": onCollide("player","goal", () => { /* win */ }) shows a
  centered win overlay (add([ text("You win!"), pos(center()), anchor("center"), … ]))
  and stops movement. Optional NPC tagged "npc": on collide, show a line of dialogue
  as a text overlay.
- A small HUD: a fixed() corner text() with the objective + "Arrows / WASD to move".

Honour the brief's theme — setting, the character's look (emoji/colour), the goal,
hazards. If the brief is a language/learning premise, put the target words on signs
or NPC dialogue.

Do NOT: load external sprite/audio assets; use body() gravity; build menus or
multiple levels unless asked; exceed the line budget. Iteration happens by prompt.
The player clicks the game once to give the canvas keyboard focus.
"""

PRESETS = {
    "3d-scene": VIZ_3D_SCENE_PRESET,
    "immersive-scene": VIZ_IMMERSIVE_SCENE_PRESET,
    "svg-diagram": VIZ_SVG_DIAGRAM_PRESET,
    "schematic": VIZ_SCHEMATIC_PRESET,
    "network-graph": VIZ_NETWORK_PRESET,
    "chart": VIZ_CHART_PRESET,
    "anim-explainer": VIZ_ANIM_EXPLAINER_PRESET,
    "slide-deck": VIZ_SLIDE_PRESET,
    "math-explainer": VIZ_MATH_PRESET,
    "mermaid": VIZ_MERMAID_PRESET,
    "game-2d": VIZ_GAME_2D_PRESET,
}

# Per-shape minimum model ability (weak | standard | strong). Bounded, structured
# shapes (diagrams, charts, equations) come out clean on a weak model; intricate
# generative ones (3D scenes, frame-by-frame animation) need a strong model.
# Drives the shape-picker gate on weak deployments + routes think() to a
# sufficiently-able provider. See .claude/rules/model-ability.md.
SHAPE_META = {
    # `max_tokens`: per-shape output budget for the STREAMING think path (which
    # is timeout-safe — chunks keep the socket alive, unlike the one-shot 60s
    # response timeout that pins the non-stream path at the 8192 default). The
    # intricate generative shapes (3D, animation, games, decks) routinely exceed
    # ~24 KB, so they get a bigger budget; bounded shapes stay at the default.
    # See _shape_max_tokens + generation.py `_think_html_stream`.
    "mermaid":        {"label": "Flowchart (Mermaid)",  "min_ability": "weak"},
    "chart":          {"label": "Chart",                "min_ability": "weak"},
    "math-explainer": {"label": "Equation / math",      "min_ability": "weak"},
    "svg-diagram":    {"label": "SVG diagram",          "min_ability": "weak"},
    "schematic":      {"label": "Schematic / one-line", "min_ability": "standard"},
    "network-graph":  {"label": "Network graph",        "min_ability": "standard"},
    "slide-deck":     {"label": "Slide deck",           "min_ability": "standard", "max_tokens": 12288},
    "3d-scene":       {"label": "3D scene",             "min_ability": "strong", "max_tokens": 16384},
    "immersive-scene":{"label": "Immersive 3D scene",   "min_ability": "strong", "max_tokens": 16384},
    "anim-explainer": {"label": "Animated explainer",   "min_ability": "strong", "max_tokens": 16384},
    "game-2d":        {"label": "2D game (Kaboom.js)",  "min_ability": "strong", "max_tokens": 16384},
}

_DEFAULT_MAX_TOKENS = 8192


def _shape_min_ability(shape: str) -> str | None:
    return (SHAPE_META.get(shape) or {}).get("min_ability")


def _shape_max_tokens(shape: str) -> int:
    """Streaming-path output budget for a shape (default 8192)."""
    return int((SHAPE_META.get(shape) or {}).get("max_tokens") or _DEFAULT_MAX_TOKENS)


# Shapes whose output is addressable DOM / SVG — the Open-Canvas-style
# element-edit loop (click an element → regenerate only it) applies. The
# canvas-rendered shapes (3d-scene Three.js, chart Chart.js, network-graph
# vis-network, game-2d Kaboom.js) draw to a <canvas> with no per-element DOM nodes to highlight,
# so element-edit is NOT offered for them — their full-file / agent iterate
# path stays. anim-explainer is excluded too: its value is the timeline, not
# per-element edits. The `extract_element` guard does not catch a <canvas>, so
# this set is the only protection — stay conservative.
# See .claude/rules/artifact-element-edit.md.
SHAPE_SUPPORTS_ELEMENT_EDIT = frozenset({
    "svg-diagram", "schematic", "math-explainer", "mermaid", "slide-deck",
})


# Shapes whose animation can be recorded to MP4 (the html-video borrow —
# emptyos.sdk.media.html_record). The recorder seeks the Web Animations API
# (document.getAnimations() — CSS @keyframes surface there), so it captures
# DECLARATIVE CSS-keyframe pages faithfully. It does NOT step <canvas> scenes
# driven by requestAnimationFrame (3d-scene Three.js, chart Chart.js,
# network-graph vis-network — none appear in getAnimations()) nor imperative
# setTimeout timelines. So v1 gates export to anim-explainer only; a real-time
# / CDP-screencast path for canvas scenes is a separate future borrow.
# See .claude/rules/... (html-record) + project_html_video_borrow_verdict.
SHAPE_SUPPORTS_RECORD = frozenset({
    "anim-explainer",
})


# System prompt for the agent-driven iterate path. Per CLAUDE.md Rule 12,
# this is a module-top template; `{shape}` + `{prior_prompt}` are substituted
# at call time inside `_iterate_stream_events`. Note the explicit Do-NOTs —
# the agent has Edit access and will rewrite the whole file if not told
# otherwise (which blows the output budget and loses surgical precision).
VIZ_ITERATE_SYSTEM = """\
You are editing scene.html in the current working directory.

This is a viz artifact of shape `{shape}` (single-file HTML).
The user's original brief was:
  {prior_prompt}

The user now wants a CHANGE. Your job:
  1. Read scene.html to understand its current state.
  2. Make MINIMAL surgical edits via the Edit tool to apply the change.
  3. Do NOT rewrite the whole file. Preserve everything unrelated to the change.
  4. The file must remain a valid single-page HTML document with all CDN imports intact.
  5. Do NOT add prose explanations — every emitted character costs output budget. Just edit.
  6. When done, stop. Do not summarise.
"""

# HTML extraction + validation helpers now live in the SDK (CLAUDE.md rule 9 —
# designer became the second consumer). Aliased to the legacy private names so
# the generation/routes/streaming helper modules import them unchanged.
# `_extract_html` is the salvage that also trims a prose preamble / fence — viz
# adopts it here (it's a no-op on already-clean output).
