# Technique matrix

Use the smallest hybrid that gives Blender the control the shot requires.

## ComfyUI assets inside Blender

| Asset role | Blender use | Best for | Avoid when | Required proof |
|---|---|---|---|---|
| Sketch/control proxy | Camera, composition, occupancy, pose, and object-placement reference | Fast previsualization before modeling or rigging | The generated proxy is being mistaken for editable 3D or final lookdev | Sketch–blockout–final alignment board |
| Still plate | VSE strip, compositor input, or shadeless camera-facing plane | Locked backgrounds, approved concept art, distant environments | The camera must reveal hidden geometry | Color-match baseline; inspect edges and scale |
| Projected still | Camera or UV projection onto simple geometry | Architecture, set extension, controlled parallax | Large camera orbit or severe occlusion change | Occlusion and projection stretch test |
| Explicit depth | Depth mesh, displacement, fog split, or depth-aware composite | 2.5D push-ins from flat character/architecture stills | Water waves, transparent surfaces, hair, or depth that the estimator cannot infer | Compare against luminance displacement and inspect silhouettes |
| Mask | Material mix, object isolation, atmosphere region, relighting | Localized smoke, mist, reflections, leaves, skin-safe enhancement | The mask flickers or changes topology | Frame-by-frame edge and temporal-stability test |
| Still texture | UV or triplanar material input | Props, screens, signs, wardrobe, ground detail | Texture must wrap unseen surfaces correctly | Texture scale, seams, and color-space test |
| Video plate | VSE/compositor layer | Already accepted full-frame generated action | Precise camera, identity, or geometry must remain authoritative | Full duration drift, identity, freeze, and corruption review |
| Animated material | Color, roughness, normal-like microvariation, emission, or displacement input | Water shimmer, reflections, screens, subtle organic surface life | The video contains conspicuous semantic objects or global drift | Low-weight A/B and channel-isolation proof |
| Residual layer | Difference or masked change over the original | Local atmosphere and controlled motion extraction | Source and generated video do not register spatially | Edge registration and seam proof |

## Blender outputs into ComfyUI

| Blender pass | ComfyUI role | Keep Blender authoritative for |
|---|---|---|
| Beauty/blockout | Appearance reference or VACE base | Camera, edit timing, coarse composition |
| Depth | Geometry/camera control | Depth sign, scale, near/far interpretation |
| Normal | Surface orientation guidance | Actual mesh and silhouette |
| Object/material mask | Regional generation or replacement | Occupancy and layer boundaries |
| Motion vector/flow | Temporal guidance where supported | Final action timing and camera path |
| Character render | Surface enhancement or controlled stylization | Rig, pose, gesture, identity silhouette |

## Ownership rules

Choose one owner for every property:

| Property | Default owner | Reason |
|---|---|---|
| Camera path | Blender | A text-described move is invented, not executed |
| Character pose/action | Blender rig | Free I2V may escalate, reverse, or replace the gesture |
| Water-wave geometry | Blender simulation/procedural mesh | Monocular depth often reads water as a flat plane |
| Smoke volume silhouette | Blender or isolated generated layer | Keep the scene and atmosphere independently reviewable |
| Fine material variation | ComfyUI | Synthesis is useful when masked and low-weight |
| Identity and occupancy | Approved still + Blender scene contract | Neither may drift during enhancement |
| Final composite and grade | Blender | One controlled color and timing boundary |

## Selection heuristics

- Use a sketch-derived gray blockout to settle framing, perspective, subject
  scale, and pose before detailed modeling. Use featureless mannequins and
  simple geometry; rebuild the accepted result as native Blender camera,
  geometry, and rig data.
- Use explicit depth mesh for a still when camera translation is small and
  silhouette layers remain credible.
- Use geometry/procedural waves for water, then optionally borrow generated
  video as subtle roughness or reflection variation.
- Use Blender physics for leaves, cloth, particles, and ripples when collision,
  direction, or timing matters.
- Use isolated generated atmosphere only when it evolves locally and cannot
  move the background geometry.
- Use Blender rig/action for turns, arm lowering, walking, and other directed
  body changes. Use ComfyUI only for bounded surface/appearance refinement.
- Use a full-frame generated clip only after it independently passes every
  ordinary Music Studio clip gate.

## Sandwich loop

1. Approve the still and art direction.
2. Build camera, proxy geometry, rig, physics, and lights in Blender.
3. Render beauty plus only the control passes the chosen ComfyUI workflow
   actually consumes.
4. Generate one bounded enhancement with recorded workflow, seed, and hashes.
5. Recombine in Blender at the lowest strength that creates a visible gain.
6. Render matched baseline/hybrid A/B clips and review both gates.

Stop the loop when the new round no longer improves a named artistic property.
