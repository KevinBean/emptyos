# QA and failure signatures

Do not reduce video quality to one metric. Combine container evidence, spatial
motion evidence, frame inspection, and art-direction judgment.

## Minimum evidence

- Probe dimensions, codec, frame rate, frame count, and duration.
- Detect black and near-duplicate/frozen intervals.
- Inspect first, middle, last, and failure-adjacent frames.
- Inspect the full-resolution frame and magnified quadrants.
- Compare the hybrid against a matched baseline.
- Preserve the exact ComfyUI workflow, Blender scene/script, and hashes.

`scripts/check_hybrid_video.py` automates the first two items and optional SSIM
comparison. Its result never approves art, identity, or useful motion.

## Motion interpretation

Use two different questions:

1. Does the frame change locally after compensating for camera motion?
2. Do several spatially separated background regions move coherently?

High local residual motion with low global consensus usually means water,
foliage, fabric, smoke, or subject motion. High coherent displacement across
the frame indicates camera movement, drift, or shake. A whole-frame optical
flow average can confuse the two.

SSIM is a difference indicator, not a quality score. A lower SSIM can mean
useful detail, corruption, a color cast, or misregistration.

## Failure signatures

### Static output

Symptoms:

- only grain or compression shimmer changes;
- the requested object/action does not change state;
- freeze detection covers most of the clip;
- stabilized local residual motion stays negligible.

Response:

- verify that the video texture is advancing and auto-refresh is enabled;
- verify image-sequence frame mapping;
- separate intentional subtle motion from model failure;
- for I2V, revise the starting composition or route the action to Blender
  after two repeated static results.

### Camera shake or global drift

Symptoms:

- background corners and center move in the same direction;
- straight edges wobble coherently;
- the frame slowly pans/zooms despite a locked contract.

Response:

- measure several separated background regions;
- distinguish an authored Blender camera move from unrequested AI drift;
- reject unrequested drift rather than hiding it with stabilization;
- do not classify foreground water, cloth, or hair as camera movement.

### Local deformation

Symptoms:

- face, hands, limbs, architecture, or prop edges melt independently;
- motion is visible but the scene's identity or geometry changes.

Response:

- reduce or mask the generated contribution;
- use Blender geometry/rig motion and restrict ComfyUI to appearance;
- reject the clip when deformation remains visible at playback size.

### False depth

Symptoms:

- water behaves as one sloped slab;
- foreground/background signs are inverted;
- hair, transparent edges, or fine structures tear during a dolly;
- luminance is mistaken for distance.

Response:

- inspect the depth map directly;
- confirm the depth sign and near/far convention;
- use explicit depth rather than luminance;
- use Blender geometry for water waves and other surfaces whose shape the
  monocular estimator cannot recover.

### Color blobs or semantic contamination

Symptoms:

- pink/green patches appear in water or reflections;
- a generated material video contains objects that read as new scene content;
- strong chroma motion overwhelms the original grade.

Response:

- route the source into roughness, a mask, or a very low-weight color mix;
- remove semantic regions from the source;
- reject the enhancement if it reads as pasted-on content.

### Texture swimming or temporal detachment

Symptoms:

- reflections, foam, grain, or roughness crawl across a surface rather than
  following its deformation;
- highlights pop between frames despite stable geometry and lighting;
- temporal averaging makes the layer softer but does not attach it to the
  receiving mesh.

Response:

- review the material layer at full delivery resolution and ordinary playback
  speed, not only a difference image or low-resolution proof;
- reduce the contribution and isolate it to a physically appropriate channel;
- prefer UV/object-space procedural detail or motion-compensated generation;
- remove the generated layer from the final when swimming remains visible.

### Factory-object contamination

Symptoms:

- an unexplained gray cube or duplicate light/camera appears in the frame;
- center geometry is unrelated to the proof.

Response:

- clear the factory scene explicitly at construction;
- enumerate intended scene objects in the manifest;
- reopen the saved `.blend` and verify the render, not only the build script.

### Exposure or color-management mismatch

Symptoms:

- an imported plate becomes gray, dim, clipped, or over-saturated;
- baseline and hybrid differ before the hybrid layer is enabled.

Response:

- use an emission/shadeless plate when preserving source color;
- align view transform, look, exposure, alpha mode, and texture color space;
- render a zero-contribution hybrid as a control.

### Character-action escalation

Symptoms:

- “turn and lower arms” becomes a jump, walk, new gesture, or pose reset;
- body occupancy, limb count, or identity changes.

Response:

- author the action in a Blender rig;
- render control passes for bounded surface enhancement;
- do not retry free I2V as though a different seed were action control.

### Freeze, black frame, or corrupt tail

Symptoms:

- repeated tail frames, reference-frame flash, black interval, decode error,
  or mismatched timestamps.

Response:

- inspect exact frame ranges;
- preserve native frame rate and integer frame count;
- regenerate or trim only when the retained action and seam still satisfy the
  canonical MV recovery contract;
- never call the video final because the renderer merely returned a file.

## Art gate

Ask:

- Does the hybrid deepen the shot's emotion or only advertise a technique?
- Is the material motion physically and stylistically integrated?
- Does the eye still land on the intended subject?
- Does the motion pace respect the music and lyric?
- Is the improvement visible in normal playback?
- Would the baseline be the stronger edit?

Record concrete reasons for acceptance or rejection so the next experiment
does not repeat the same failure.
