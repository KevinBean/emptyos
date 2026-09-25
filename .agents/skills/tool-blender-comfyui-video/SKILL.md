---
name: tool-blender-comfyui-video
description: Integrate ComfyUI-generated images, depth maps, masks, and videos into Blender music-video shots as plates, textures, projections, 2.5D geometry, or controlled surface enhancement. Use when exploring or producing Blender × ComfyUI hybrid video, improving realism while preserving authored camera, physics, and rig motion, diagnosing static, shake, drift, deformation, or false depth, or deciding which system should own geometry, appearance, motion, and final compositing.
---

# Blender × ComfyUI Video

Build hybrid shots in which Blender supplies spatial and temporal authority and
ComfyUI supplies selected appearance detail. Treat every experiment as a
bounded, reproducible proof that must pass both technical and artistic review.

Read `D:\emptyos\docs\MV-GENERATION-WORKFLOW.md` before acting on an EmptyOS
music video. Use `creative-mv-art-director` for the film's visual thesis and
`creative-mv-generator` for the canonical production run. Use
`tool-comfyui-image` for image-generation mechanics. Do not create a second
production pipeline.

## Establish authority

- Treat the persisted Music Studio run as the production source of truth.
- Let Blender own geometry, camera, rigged performance, physics, lighting
  layout, render passes, and the final composite.
- Let ComfyUI own bounded appearance synthesis: textures, material
  microvariation, background detail, depth estimation for flat sources, and
  localized atmosphere or surface motion.
- Never let unconstrained I2V own a precise camera move, a required body
  action, or a physically specific deformation.
- Keep diagnostic outputs outside the production run until the director
  approves them and their provenance is imported into the run.

## Run the bounded proof

1. Inspect the active run, current `song-treatment.md`, `art-direction.md`,
   scene contract, approved still, ComfyUI health, and existing Blender
   artifacts. Preserve accepted work.
2. State one visual hypothesis and one failure hypothesis. Select a
   representative two-to-five-second shot rather than a full film.
3. Assign ownership for geometry, appearance, motion, camera, and composite.
   Read [technique-matrix.md](references/technique-matrix.md) before selecting
   the asset role.
4. When composition, camera, or pose is uncertain, create a control proxy
   before lookdev:
   - treat the input sketch only as a blueprint for framing, perspective,
     occupancy, pose, and object placement;
   - reconstruct a matte-gray blockout with simple geometry, featureless
     articulated mannequins, a perspective grid, soft shadows, and ambient
     occlusion;
   - forbid the proxy from inheriting the sketch's line style, texture, or
     color;
   - treat an AI-generated blockout image as reference only, never as an
     editable Blender scene;
   - rebuild and verify the geometry, camera, and rig natively in Blender.
5. Generate only the required ComfyUI source. Save the workflow JSON, prompt,
   negative prompt, model, seed, input hashes, output hash, dimensions, frame
   rate, and generation date.
6. Import the source into Blender:
   - delete or deliberately retain factory objects; never leave the default
     cube, camera, or light by accident;
   - use emission/shadeless treatment for a plate whose source color must be
     preserved;
   - set movie or image-sequence textures to refresh cyclically only when
     looping is intentional;
   - use non-color data for depth, masks, normals, and roughness;
   - use explicit depth for displacement or a depth mesh; do not silently
     substitute image luminance;
   - fit camera-facing plates against the evaluated render aspect and camera
     sensor fit. For a landscape orthographic camera, do not assume
     `ortho_scale` is the visible image height: fit the plate width (or use
     `plate_height * render_aspect`) and verify a zero-motion render against
     the source. Reject any unexplained crop of a story anchor;
   - keep AI-video contribution low, masked, and channel-specific when it is
     serving as material motion rather than a visible plate.
7. Render matched baseline and hybrid clips with identical camera, timing,
   resolution, frame rate, color management, and encoding.
8. Run `scripts/check_hybrid_video.py` on both clips. Treat its result as
   container/freeze/black evidence only, never as artistic approval.
9. Review first, middle, and final frames; the high-detail full frame; and
   magnified quadrants. Compare local residual motion with coherent global
   displacement before calling motion "shake." Inspect temporal texture
   attachment: detail must move with the receiving surface rather than swim,
   crawl, pop, or flicker independently.
10. Record the disposition as `accepted`, `rejected`, or `inconclusive`.
    Preserve rejected outputs and name the failure signature. Read
    [qa-and-failure-signatures.md](references/qa-and-failure-signatures.md).
11. Promote only a director-approved technique into the current Music Studio
    run, with exact hashes and the same ordinary motion-proof/final-review
    gates as every other source.

## Enforce production stage boundaries

- Finish still/composition review before motion generation.
- Finish full-duration clip review before assembly. Test every sequential
  frame pair, not only sampled thumbnails.
- Classify each clip as hard pass, hard fail, or ask. Ask only when the result
  is genuinely artistic or motion-floor ambiguity, and offer bounded choices
  plus free-form feedback.
- For calm shots, accept either broad motion or coherent sparse local motion
  in a fixed spatial cell. Require both local changed area and local MAD so
  compression speckles cannot launder a literal still into a pass.
- Do not treat smoke pasted over a static plate as general motion. Name the
  authored moving subjects in the scene contract and verify those subjects at
  ordinary playback size.
- Prevent a technically moving edit from becoming a slideshow. Before final
  assembly, name the song's performance or action spine and measure the share
  of the full timeline carried by full-frame video rather than still-image
  camera drift. Use a project-specific floor (60% is a useful starting point
  for performance-led music videos) and review the slow sections by intent.
  A repeated clip counts only when the music or lyric itself repeats and the
  recurrence changes or deepens meaning; looping unrelated motion to satisfy
  the percentage is a hard fail.
- For locally generated lip-sync, discard guide audio in assembly, preserve
  the canonical song track, and measure any stable visual latency with a
  hand-placed mouth ROI. Correct a stable offset at the source in-point; do
  not time-stretch the performance unless drift over time is separately
  demonstrated. Always read the open/closed extreme-frame sheet so a bad ROI
  or proxy cannot masquerade as a sync result.
- Give every generated overlay or geometry effect an attachment contract:
  source anchor, direction, scale, depth/layer, colour, and tolerance. Derive
  coordinates from the approved plate or mask rather than guessing them.
  Inspect overlay-only plus first/middle/final composites; motion evidence
  cannot waive a detached root, doubled contour, sprite rectangle, or wrong
  colour integration.
- Add a composition-retention gate before temporal QA: compare source and
  rendered first frame at full-frame scale, checking that every named anchor
  remains visible and has the approved occupancy. A technically continuous
  effect is a hard fail when its emitter, subject, or narrative support has
  been cropped out by the Blender camera.
- After clip review, write one complete scene-to-file manifest with absolute
  paths and SHA-256. Make assembly fail closed if any selected byte changes.
  Never discover or replace a scene source during combination.
- Preserve exact director locks. A later aggregate preflight may report them,
  but must not silently reopen or regenerate them.

Use a two-profile resolution contract:

- default review/debug: the lowest model-validated safe resolution, normally
  `1024x576` for a 16:9 FLUX plate in the validated local workflow;
- delivery: an explicit higher-resolution profile selected after review.

Do not infer a safe size from aspect ratio alone. Render a resolution matrix
for each model/workflow and hard-fail a large edge-connected constant-colour
fill. A valid PNG header and requested dimensions do not prove that all latent
rows decoded.

For Blender 5 frame-handler mesh deformation, call `mesh.update()`,
`mesh.update_gpu_tag()`, and `object.update_tag(refresh={"DATA"})` after
changing vertex coordinates. Without the GPU tag, saved animation frames can
show only a small updated region even though Blender's CPU mesh data changed.

## Use the sandwich loop

Prefer this exchange:

```text
ComfyUI concept/still
  -> Blender blockout, camera, rig, simulation, masks/depth/normals
  -> ComfyUI controlled appearance enhancement
  -> Blender compositing, grading, edit, and delivery
```

Use a simpler one-way path when it is sufficient. Do not add an AI round-trip
unless it improves a named visual property without weakening identity,
geometry, action, or temporal stability.

## Enforce two approval gates

Require both gates.

**Technical gate**

- playable container, expected dimensions, native frame rate, and frame count;
- no corrupt, black, duplicated, or frozen sections unless explicitly
  authored and reviewed;
- no unrequested coherent whole-frame drift or shake;
- visible independent local motion where the scene requires it;
- stable identity, occupancy, geometry, and action;
- recorded asset, workflow, and render provenance.

**Art gate**

- the change strengthens the current song's visual thesis and emotional arc;
- material motion reads as part of the world, not as an effect pasted on top;
- realism gains survive ordinary playback, not only a difference image;
- added motion does not compete with the lyric, gesture, or edit;
- the hybrid is better than the baseline, not merely more complex.

Reject the technique when either gate fails. A technically successful render
may still be artistically worse.

## Persist the experiment

Keep one evidence directory per proof:

```text
proof/
  inputs/
  comfyui/
    workflow-api.json
  blender/
    scene.blend
    build-scene.py
  baseline.mp4
  hybrid.mp4
  comparison.mp4
  manifest.json
  qa.json
  review.json
```

Store absolute paths only in run-local manifests. Keep this reusable skill free
of personal paths and secrets. Record hashes rather than trusting filenames.
For a hybrid whose approved composition differs from its source still, also
hash-bind a decoded frame from the approved video as the QA reference. Comparing
the hybrid against the obsolete still can misclassify legitimate architecture
or grading changes as colour bands or tearing.

Use `blender_depth` for an explicit per-scene depth input in the current Music
Studio experiment contract. Preserve the luminance fallback only for deliberate
diagnosis; label it as luminance, not depth.

## Route detailed questions

- For two-pin cloth drape, soft folds, attached patterns, and matte animation
  shading, read [soft-cloth-animation-example.md](references/soft-cloth-animation-example.md).
  It records a rendered case and its failures, not universal cloth settings
  or final artistic approval.

- For runnable paper-peel and action-blocking examples, read
  [animation-examples.md](references/animation-examples.md); build editable
  scenes with `scripts/build_animation_examples.py`. The samples are teaching
  mechanisms with documented limitations, not finished character animation.

- For authored animation, paper peel, lyric timing, surface attachment, and
  reproducible rendering, read `docs/BLENDER-ANIMATION-GUIDE.md` from the
  repository root. It separates implemented case-study methods from proposed
  improvements and does not impose the case study's style or frame timings.

- For real lyrics written onto a generated page or other locked-off surface
  (clean plate, homography-mapped Blender text, masked composite over a
  motion clip), read [lyric-surface-composite.md](references/lyric-surface-composite.md).
  It records the 不可說 build and its limits, not a typography preset.

- Read [local-fix-toolkit.md](references/local-fix-toolkit.md) when a finished
  generated shot has a local defect — blank screens or props, slanted surface
  text, ghosting doors, an exterior that contradicts the interior, a join that
  will not connect, a third failed regeneration, or an uneven grade. It records
  the 〈換班〉 multi-layer compositor, proxy tracking, 2.5D plates, salvage
  slow-down and per-shot LUT grade, with the failures that shaped them.

- Read [technique-matrix.md](references/technique-matrix.md) to choose between
  plates, projections, depth geometry, material video, masks, control passes,
  and the sandwich loop.
- Read
  [qa-and-failure-signatures.md](references/qa-and-failure-signatures.md) when
  diagnosing static output, shake, drift, false depth, color blobs, duplicated
  objects, or character-action failure.
- Read [validated-findings.md](references/validated-findings.md) before
  repeating an experiment already resolved by the 2026-07-31 case study.
- Read
  [director-approval-qa.md](references/director-approval-qa.md) when a quiet,
  procedural, or 2.5D shot conflicts with an automated minimum-motion or
  reference-corruption gate.
- Read
  [art-direction-from-technical-master.md](references/art-direction-from-technical-master.md)
  when a complete, stable film still reads as a capability reel, repeats its
  hero object, introduces a late asset without setup, or needs a director's
  cut rather than more effects.
- Read [asset-finish-gate.md](references/asset-finish-gate.md) before calling a
  shot final, whenever a rig, imported asset, or simulated element might still
  be a control proxy — a technically valid simulation can still render as a
  blockout, and pixel evidence at delivery resolution is the authority.
- Read [native-3d-environment.md](references/native-3d-environment.md) when an
  AI environment reference must be rebuilt as real Blender architecture, or
  when a 2D character card must inhabit a camera-moving 3D set.

After editing EmptyOS runtime Python, do not restart ports `9000` or `9001`.
Tell the user that `restart.bat` is required for their running daemon to load
the change, or use the repository's sanctioned sandbox verification path.

## Reuse an illustrated performance with local cloth

Read [illustrated-performance-compositing.md](references/illustrated-performance-compositing.md)
when preserving an existing actor take while repairing scene continuity with
Blender cloth, mattes, camera projection or contact shadows. It records the
Spark G look, dark-clothing matte failure, attachment framing and distinction
between selected acting, held poses and reused native cloth motion.
