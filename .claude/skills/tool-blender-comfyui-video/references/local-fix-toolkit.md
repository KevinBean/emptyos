# Local fix toolkit — repairing generated MV shots without regenerating

Case study: 〈換班〉(2026-09-15). Kevin's nine notes on a finished 1080p cut were
fixed with **10 Flow credits for one shot and zero for the other eight**, after
his instruction 「優先用blender解決」. Scripts live with that song
(`songs/2026-01-14__换班/mv-flow-20260914/post/`); this page records the
techniques, the numbers that mattered, and the failures that shaped them.
It is a case record, not a universal recipe.

## Decide the route first

| Defect in a generated shot | Local route | Why not regenerate |
|---|---|---|
| Blank glowing monitor / phone | Blender UI texture → `screen` layer (multiply) | Veo/Flow screens come back blank almost every time |
| Blank prop surface (record sleeve, poster) | Blender artwork → `replace` layer, shaded by the plate | the model redraws the prop, not the print |
| Handwriting that must follow a slanted surface | perspective quad measured along the surface edges; layout rotation 0 | a rotated layout on an axis-aligned quad reads as floating text |
| Sliding doors ghost / misalign mid-slide (`换班:video:S12:1`) | 0-credit "doors open" still (`换班:still-edit:O3-I-doors-open:1`) + drawn panels sliding in the wall's perspective (2.5D) | video models cannot keep rigid frames straight through a slide |
| Exterior contradicts interior (grilles) | 0-credit edit of the still (`换班:still-edit:R1-no-grille:1`) + 2.5D push-in, sky drift | one still edit is free; a video re-roll is 10 credits and may add new errors |
| Two shots will not join (2:22 door push) | give time back: shorten the arrival to one bar and insert a one-bar empty cutaway | continuity that fails twice is a structure problem, not a seed |
| Third failed generation of the same shot | salvage the clean segment, motion-compensated slow-down | the three-tries rule forbids a fourth attempt |
| Whole cut looks uneven | measured per-shot grade baked into 3D LUTs | — |

## Compositing layers (`composite_note_text.py`)

One script, several layers per shot, applied in order on every source frame
at output scale (720p source → 1920×1080 before compositing, so text and UI are
native 1080p).

- **ink** — alpha texture multiplied in the ink colour: `out = plate × (1 − a·w·opacity·(1 − ink))`.
  Paper grain, compression noise and light changes stay under the writing.
- **screen** — RGB texture multiplied onto the glowing screen, so glare and
  reflections survive. `fade_luma`: strength × (screen L / 95th-percentile L)^2,
  so the UI **goes out with the screen** (S10 monitor switching off, S20 phone
  dimming). Without it the content stays lit on a dark screen.
- **replace** — RGB print laid on a matte surface, shaded by `plate L / surface
  median L` clamped to 0.55–1.25.
- **Occlusion** for every layer: LAB distance from the surface's per-frame median
  colour inside the quad (`mask_de` ramp). Hands, steam, hair and glints get no
  layer. **Masks and shading always read the original frame**, never the
  composite so far — otherwise a later layer cannot cover an earlier one (the
  front sleeve refused to paint over the back sleeve's artwork).
- **Blur**: per frame, match the plate's Laplacian variance around the quad
  (`blur: auto`) plus `base_sigma`. A defocused background monitor needed a
  fixed `base_sigma: 6`; auto-matching only measures relative sharpness inside
  a crop that is already soft.
- Wide `mask_de` (60–90) for a print replacing a white record edge seen through
  a sleeve hole: at 14–32 the white arc survived as a visible outline.

## Tracking (`track_note_ecc.py`, `derive_track.py`)

- ECC affine against the **init template**, warm-started from the previous
  frame, so drift cannot accumulate. Template and input crops must be the same
  size or `findTransformECC` errors on every frame.
- **Exclude what changes from the template**: a screen that switches off (S10,
  S20) collapsed correlation to 0.3 until the screen interior was masked out,
  then held above 0.85.
- **Rotated or overlapping targets: track an axis-aligned proxy box**, then carry
  the per-frame affine (proxy at init → proxy at frame) to the real quads with
  `derive_track.py`. The tracker's corner-ordering helper scrambles a rotated
  phone's corners; two sleeves need two quads from one track.
- **Measure the quad on the tracker's init frame.** S31's first quads were
  measured on another frame of a slowly moving overhead shot and the covers
  landed ~20 px off; re-measuring on frame 107 (the in-point) fixed it.
- Grid crops (2× upscale, labelled 20/40 px lines) are the reliable way to read
  corner coordinates by eye.

## Blender textures (`build_screen_textures.py`)

- Headless Blender 5, Cycles 16 samples, one emission plane per element,
  orthographic camera, **Standard** view transform, pixel-space authoring
  (top-left origin, px/100 units).
- Every "text line" is a rounded grey bar (Bevel modifier, `affect=VERTICES`),
  so nothing is readable even before blur — no glyph checks needed downstream.
- Rounded rectangles on a coplanar z collide (z-fighting shows as squiggles in a
  highlighted row); keep overlays on a distinct z.
- `material.use_nodes = True` still works in 5.0 for emission materials; a
  factory-empty scene needs no world for an emission-only render.

## 2.5D plates from a still (`plate_motion.py`)

- **Push-in**: 16:9 window of height `ph/z` moving from the frame centre toward a
  target with a 60/40 linear/smoothstep ease; zoom 1.00→1.06–1.14 over 3.5–6 s.
- **Sky drift without dragging the building**: sky mask by Canny edge map +
  flood fill from the top corners; close 9 px; small enclosed non-sky specks are
  birds/dust → inpaint them out; **inpaint a 41 px band under the rooftops** so
  the drifted sky layer has sky, not wall, to pull in. Drift 8–14 px total.
- **Lit window breathing**: brightness × (1 + 3% slow sine + 1.8% faster sine)
  only where L > 120 inside the window rect.
- **Cloth sway**: `cv2.remap` sideways, amplitude growing toward the hem
  (`hang^1.2`), two sines; weight from a light/low-saturation mask. Pass
  `float32` maps — a float64 `d · weight` product raises in `remap`.
- **Sliding doors**: base is the doors-open edit (ECC shows it aligned to the
  closed master within 0.1 px). Panels are drawn (bronze frame, 16% tinted glass,
  faint reflection band) in the wall's own perspective lines, eased open over
  ~1.7 s, clipped at the wall and behind the plant; rain streaks drawn only
  inside the glass area.
- Fine grain (σ ≈ 2.2) so a still sits with the Veo clips around it.

## Salvaging a failed generation

- Dense review first: full frames every 12, face crops ×3 every 4 frames. S08's
  third attempt (`换班:video:S08:3` in the MV library) was clean only for 0–1.75 s (lips part at frame 44, the push
  then accelerates and slides past her).
- `setpts=3.3*(PTS-STARTPTS),minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1`
  turned 42 source frames into 136 (5.67 s). Face crops every 6 frames showed no
  warping on a slow wide push; frame-diff p95 1.07. Expect a floaty feel — it
  suits a contemplative beat, not a performance beat.

## Grade as per-shot LUTs (`grade/build_grade_luts.py`)

- Measure the **ungraded** cut: per shot, three frames, CIELAB mean L/a/b,
  chroma, L p5/p95.
- Group by location (office, street, home, facade night, rooftop, dawn); pull
  a/b **70%** toward the group target (cap 10), chroma × sqrt(target/measured)
  clamped 0.78–1.12; lower lifted-grey black points (p5 > 15 → 0.6 × p5).
- Shared look: toe lift (+3 L at black), highlight roll-off above L 80, +8% mid
  contrast, cool shadows / warm highlights split tone, chroma × 0.92.
- Bake 33³ `.cube` per shot (R fastest); ffmpeg `lut3d=file=S08.cube:interp=tetrahedral`
  inside the cut's per-slot chain, so the grade costs no extra encode. Run the cut
  with the LUT folder as working directory so the filter path needs no escaping.
- **The first pass at 55% was numerically right and invisible side by side.**
  Always review before/after pairs at the same timestamp before calling a grade
  done.

## Verification that caught real defects

- Composite review sheets every 12 frames per shot (sleeve offset, record-hole
  outline, content on an off screen).
- Full-cut frame count (4,689 = 195.36 s × 24), full decode, audio packet hash
  identical across textless / subtitled / clean outputs, audio lag 0 vs source.
- Spot frames at every changed timestamp on the graded master before burning
  subtitles.
