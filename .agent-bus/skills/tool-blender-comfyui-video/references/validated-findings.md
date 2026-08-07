# Validated findings

These findings came from bounded A/B experiments completed on 2026-07-31.
Treat them as reusable evidence, not universal laws. Re-test when the model,
workflow, shot class, or renderer changes materially.

## Conditional proof: generated video as subtle water material motion

- Keep Blender's ripple geometry authoritative.
- Use generated water video primarily for micro-roughness/reflection variation.
- A very low color contribution can add life; a strong mix produced visible
  pink blobs and was rejected.
- Local residual motion can be substantial while coherent global displacement
  remains low. This is surface motion, not camera shake.
- Full-resolution final-film review later found texture swimming even after the
  already-restrained hybrid was composited at 20%, then temporally averaged
  over nine frames and reduced to 8%. Both variants were rejected for delivery.

General conclusion: generated video may be useful as a bounded material signal,
but a low blend weight does not prove surface attachment. Promote it only when
full-resolution temporal review is better than the physical baseline.

## Accepted: physical water relighting and slower camera

- The final film retained Blender's procedural ripple mesh and removed the
  generated water layer.
- Softer neutral-cool and copper area lights replaced saturated cyan/red rakes.
- Camera travel was reduced by roughly half while preserving all 99 authored
  frames.
- The revised shot passed the art gate for water cohesion, highlight retention,
  camera weight, and fit with the restrained nocturnal grade.

General conclusion: when generated complexity fights surface physics, improve
lighting, material response, and camera weight before adding another model.

## Accepted: sketch-to-blockout as a control proxy

- A three-panel sketch–gray-blockout–final board made composition, camera,
  occupancy, and pose intent reviewable without exposing debug geometry in the
  film.
- The gray proxy used the actual production scene in Blender Workbench mode.
- The generated/derived sketch remained a reference image; native Blender
  geometry, camera, and a licensed 19-bone character rig remained authoritative.

General conclusion: sketch-to-blockout is a previsualization and control
technique, not an image-to-editable-3D conversion. Its value is reducing
ambiguity before expensive lookdev and animation.

## Accepted: explicit depth for restrained 2.5D parallax

- An explicit monocular depth map produced controlled camera-dolly parallax
  from a flat still.
- The matched luminance-displacement baseline was not equivalent depth.
- Small travel preserved the image; larger translation would expose missing
  geometry and silhouette gaps.

General conclusion: explicit depth is useful for restrained 2.5D shots of
architecture, machinery, and characters when silhouette limitations are
acknowledged.

## Rejected: monocular water depth as ripple geometry

- The estimator interpreted the water surface largely as a plane.
- It did not reconstruct the actual wave/ripple height field.

General conclusion: depth estimation recovers scene distance, not arbitrary
surface microgeometry. Keep water-wave shape in Blender.

## Rejected: unconstrained full-frame atmosphere video

- The test produced negligible independent motion and a freeze event.

General conclusion: a model response and valid file do not prove animation.
Prefer an isolated atmosphere layer or Blender procedural volume and require
measured local evolution.

## Rejected: free I2V for a directed character action

- The requested turn and arm-lowering action escalated into a jump.

General conclusion: use a Blender rig for directed body-state change. Let
ComfyUI refine bounded appearance only after pose and timing are fixed.

## Accepted: ComfyUI plate plus Blender-authored calm-scene motion

- ComfyUI supplied reviewed appearance plates; Blender owned water, grasses,
  bamboo, hair/cloth edges, smoke geometry, camera, and final compositing.
- A 1024x576 review plate was the lowest safe FLUX size in the tested local
  workflow. The 832x480 request repeatedly produced valid-size images with
  only the top latent rows rendered and a large neutral fill below.
- A deterministic edge-connected constant-colour detector caught the failure
  before semantic review.
- Blender 5 dynamic mesh renders required `mesh.update_gpu_tag()` plus an
  object data update after per-frame vertex changes. CPU-side mesh updates
  alone produced partially refreshed frames.
- Transparent leaf assets had to be reviewed over the final background. Alpha
  rectangles, colour mismatch, leaf scale, and ballistic trajectories were
  artistic hard failures even when the container and animation were valid.
- A separate borderless Blender curve layer produced readable incense smoke
  without asking I2V to reinterpret the whole frame.
- The first smoke geometry pass moved continuously but used a guessed horizontal
  anchor, so it detached from the painted smoke and failed the art gate. The
  accepted revision derived both deformation mask and curve root from the
  incense-bowl centre, then reviewed first/middle/final composites.

General conclusion: use ComfyUI for appearance and Blender for deterministic
motion/physics. Validate actual pixels and ordinary playback, not requested
dimensions, prompt intent, or file metadata.

## Accepted: broad-or-coherent-local temporal QA

- A full-frame changed-pixel floor incorrectly rejected a calm scene whose
  smoke occupied a tiny area.
- A fixed 6x8 spatial grid separated coherent sparse motion from compression
  speckles: require both local changed area and local mean absolute difference.
- Sequential pair testing exposed long frozen stretches that sparse sampling
  missed.
- Exact director approval settled only ambiguous motion-floor cases; literal
  freeze, corruption, shake, seam, and identity failures remained objective.

General conclusion: the motion detector must implement the authored scene
contract. Use global evidence for water/foliage fields and coherent local
evidence for smoke, hair, insects, or a single plant.

## Accepted: exact-byte source lock before assembly

- The final source manifest bound every scene to one absolute path and SHA-256.
- Assembly rehashed all 20 files and failed closed on any mismatch.
- Repaired scenes replaced only their own entries; approved scenes remained
  byte-identical.
- Full-duration clip QA completed before combination. Assembly QA checked
  repeated boundary frames, reported timestamps, audio/video duration, and
  uniform edge fill without reopening accepted source decisions.

General conclusion: never discover, regenerate, or substitute a scene during
combination. Stage ownership plus exact hashes prevents stale-source reuse and
the late re-litigation of already reviewed clips.

## Implementation lessons

- Clear Blender factory objects explicitly in generated scenes.
- Use emission and neutral exposure for a source-color-preserving image plate.
- Save the `.blend`, the scene-construction script, workflow JSON, seed, and
  hashes; an MP4 alone is not a reproducible proof.
- Compare baseline and hybrid with identical timing and camera.
- Judge global motion from separated background regions, not one whole-frame
  average dominated by moving water or fabric.
- Preserve strong and failed variants. A rejected over-strength mix establishes
  the upper bound that makes the accepted restrained mix meaningful.
- Re-open a conditionally accepted proof at final delivery resolution. A
  technique can pass bounded technical QA and still fail the film's art gate.
