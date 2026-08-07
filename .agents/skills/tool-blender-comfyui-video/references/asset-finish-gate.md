# Asset Finish Gate

Use this gate before calling any Blender/ComfyUI hybrid shot final. It exists
because a technically valid simulation can still look like a blockout.

## Core rule

An imported, rigged, animated, licensed, or high-vertex-count asset is not
automatically a finished visual asset. Pixel evidence at delivery resolution
is the authority.

Control proxies are allowed during layout and simulation, but never in a
publishable render. This includes:

- reed curves used as vegetation;
- diamond or plane meshes used as falling leaves;
- stick figures, mannequins, base bodies, and test rigs;
- debug emitters, collision helpers, camera guides, and rig shapes;
- untextured cylinders, cubes, or planes that have no authored story purpose.

Note the last clause — **"that have no authored story purpose"** — and read it as
governing the whole list.

### Pixel evidence says what is there. The treatment says whether it belongs.

"Pixel evidence is the authority" settles *existence*, never *legitimacy*. A
deliberately featureless silhouette and an unfinished mannequin are
pixel-identical; a bare plane can be a proxy or a water surface; an untextured
form can be a placeholder or the point. Nothing in the frame distinguishes them.
Only the intent document does.

**So before naming anything a proxy, open `song-treatment.md` and
`art-direction.md` and check whether it is authorised.** Treatments carry a
literal-anchor allowance (typically two) and a motif grammar giving each motif an
opening / transformed / final state — an object matching an authorised anchor in a
declared state is finished work, however minimal it looks.

This is not hypothetical. On 2026-07-31 an audit of Log Out v6 called its human
silhouette an "untextured mannequin", quoted three prohibitions, blocked the
release, and was wrong: `song-treatment.md` authorises "a human silhouette at the
edge of the nocturnal world" by name, and the film resolves the motif exactly as
written. Three forbidding documents were read; the permitting one was not. The
prohibition list above is half of a contract, and reading it alone manufactures
defects out of intended features.

## Required asset record

For every promoted asset, record:

- source page and license;
- local source file and SHA-256;
- intended shot role;
- mesh/object count and approximate vertex count;
- material and texture status;
- whether it is a proxy, control asset, or final render asset;
- the frames and resolution at which it passed pixel review.

These fields establish provenance. They do not establish visual quality.

## Pixel review protocol

1. Render first, middle, and last frames at preview resolution.
2. Review the full frame, then the four quadrants.
3. Name foreign intrusions literally: "thin white rod at upper left,"
   "diamond-shaped falling plane," "unclothed mannequin," and so on.
4. Render at least one representative frame at final delivery resolution.
   Small foliage details can collapse into rods or cards in contact sheets.
5. Review the final-resolution frame without relying on object names,
   manifests, vertex counts, or provenance.
6. Review motion after stills pass. Check edge-on leaf cards, T/A poses,
   clipping, interpenetration, sliding feet, texture swimming, and fade logic.
7. Persist the review verdict and exact evidence. A generic "no debug
   geometry" statement is not sufficient.

## The sampling unit is the shot, never the film

Sample first, middle, and last of **every shot in the edit list** — not every N
seconds of the assembled master. A review that cannot say which shot each decoded
frame belongs to has reviewed part of the film and reported on all of it.

Log Out v6 is the measured case: `final-review-v6.json` decoded fourteen frames —
1.5, then 16.5 through 23.5, then 28.5 onward — skipping the whole 2–16 s window,
which is **three of eight shots**. The verdict covers 5/8 and does not say so.

Be honest about what this buys. Nothing bad shipped in those three shots; an audit
that later looked at them mistook an authored motif for a proxy and had to retract.
So the rule buys **coverage, not correctness** — it guarantees a human looked at
every shot, and guarantees nothing about the judgement they then make. Do not sell
it as defect-catching; a reviewer who looks at an unfamiliar shot without reading
the treatment can still get it exactly wrong (see
`art-direction-from-technical-master.md` on judging by intent, and read the
treatment before calling anything a proxy).

Checkable without a human: read the `edit` array from the production manifest, read
`decoded_review_frames` from the review, and assert every shot interval contains at
least one. Cheap, deterministic, names the unreviewed shots.

## Resolution trap

Foliage that reads organically at 1920x1080 may collapse into sticks and
diamonds when a contact sheet shrinks each frame to 640x360. Conversely, a
high-resolution source asset may still look primitive in the final shot.

When thumbnail and delivery-resolution verdicts disagree:

- do not accept the asset from metadata;
- do not reject it from the thumbnail alone;
- render and inspect the same frame at delivery resolution;
- require the reviewer to identify the exact screen location and visible
  shape of any failure.

## Character promotion gate

A base body plus a procedural clothing shell remains a base character if the
pixels read as a mannequin. Promote a character only when the render shows:

- authored clothing layers and materials;
- a natural non-T/A pose with readable weight distribution;
- correct body scale and contact with the environment;
- a coherent silhouette at the shot's actual framing;
- no visible rig controls or broken deformation;
- motion that serves the scene rather than demonstrating the rig.

Prefer a complete production character asset over building final clothing
from primitive shells under delivery pressure.

## Foliage promotion gate

Real-time leaf cards are a normal production technique, but they must read as
foliage in the delivered pixels. For falling leaves:

- use an authored leaf or frond silhouette with visible secondary structure;
- keep the broad face readable to camera at key poses;
- avoid unrestricted tumbling that presents only the thin edge;
- add subtle thickness or two-sided shading where necessary;
- separate overlapping silhouettes in screen space;
- reject any frame where the result reads as a rod, diamond, or grey card.

## Camera-motion gate

A contact sheet cannot reliably distinguish authored camera travel from
high-frequency shaking. When shaking is suspected:

- sample the evaluated camera world matrix on every frame;
- measure translation and angular deltas;
- flag rapid direction reversals and isolated acceleration spikes;
- require zero unexplained high-frequency direction reversals;
- keep the pixel review, because smooth camera matrices do not rule out
  unstable generated overlays or texture swimming.

## Final verdict language

Use separate verdicts:

- `technical_pass`: render, timing, streams, and file integrity are valid;
- `asset_finish_pass`: no proxy or unfinished asset is visible;
- `art_pass`: composition, motion, lighting, and story intention are
  publishable.

The deliverable is final only when all three are true.
