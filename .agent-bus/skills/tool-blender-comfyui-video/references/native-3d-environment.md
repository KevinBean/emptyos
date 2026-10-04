# Native 3D Environment From an AI Reference

Use this mode when the camera, parallax, occlusion, or character route requires
real spatial authority. The AI image may establish palette, material family,
architectural rhythm, and layout intent. It must not supply the final full-frame
background pixels.

## Define "real 3D" before lookdev

A set qualifies as native 3D only when:

- building masses have world-space width, height, and depth;
- corners, reveals, lintels, sills, curbs, thresholds, and façade layers are
  geometry rather than painted depth;
- foreground props and vegetation occupy deliberate world positions and can
  correctly occlude or be occluded by the character;
- a small camera translation produces coherent parallax without revealing the
  edge of a plate or projection;
- the final render loads no full-frame environment plate.

A shallow façade can still be valid set-extension geometry if the approved
camera never reveals its missing side. Name that limitation explicitly; do not
call it a complete building.

## Build in passes

1. Work in a consistent scale, normally one Blender unit per metre. Block out
   the route, raised sidewalk, curb, building masses, and camera with matte
   materials. Validate horizon, human scale, foot line, and path clearance.
2. Replace the blockout with modular architectural assemblies. Build openings
   from a recess back, jambs, lintel, sill or threshold, glazing, mullions, and
   interior depth. Do not place a dark rectangle on a flat wall and call it a
   window.
3. Make repeated structural units as repeated geometry or instances: glass
   blocks, tiles, paving modules, louvers, and frames. Use bevels sized in world
   units so edges catch light at the delivered resolution.
4. Add only story-supporting props after the route works. Keep doors, walls,
   racks, planters, and other collision-like objects outside the character's
   path unless contact is authored.
5. Build materials and lights only after the geometry reads in clay renders.

## Material discipline

- Use Principled BSDF for stone, tile, metal, glass, wood, and cloth unless a
  named visual requirement needs a custom shader.
- Use actual geometry for silhouette, thickness, seams, recesses, and other
  structural depth. Use bump or normal variation only for micro-detail.
- Prefer Object or UV coordinates for procedural surface scale. Generated
  coordinates normalize to each object's bounding box and can stretch the same
  noise differently across a long lintel and a square pier.
- For glass, set transmission, IOR, roughness, and thickness deliberately. A
  pale transparent cube without a recess or interior behind it reads as plastic.
- Use Metallic and Roughness together for stainless steel. A grey diffuse
  material does not become metal because the object is cylindrical.
- Give cloth a curved or simulated surface and real thickness. A beveled box
  remains a board even when its material is named "fabric."

## Integrate a 2D character card

- Preserve the source image aspect ratio; size the plane from the decoded pixel
  dimensions rather than eyeballing X and Z independently.
- Keep the card camera-facing, but animate its world-space root. Ground the
  decoded foot line to a real surface and verify it in every pose family.
- Use a contact shadow and real occluders. The shadow cannot rescue a sprite
  whose route passes through a wall, planter, or rack.
- Put interactive labels or props in world space with an explicit depth order.
  Do not animate them only in screen coordinates once the camera moves.
- Continuity owns direction: match the outgoing motion vector and walk/run
  phase from the previous cut before choosing a convenient camera composition.

## Do not equate stylization with outlines

A 3D-to-2D look is usually established first by proportion, simplified
geometry, palette, controlled roughness, soft lighting, colour management, and
motion cadence. Freestyle or Grease Pencil line art is optional. Render a
matched A/B and keep contours only when they are visibly stable at delivery
resolution and improve the film's visual language. Merely enabling Freestyle
is not evidence that an animated 2D style has been achieved.

## Proof before production

Before building a full shot, render one bounded proof containing the hardest
architectural unit and material family. Persist:

- the `.blend` file and build script;
- a clean render plus any stylized variant;
- render engine, Blender version, resolution, object/material counts, and
  confirmation that no environment plate was loaded;
- first/middle/last frames of a short camera or character-motion test.

Reject the proof when it reads as an unfinished blockout, when materials hide
missing geometry, when procedural texture scale changes per object, or when the
2D card appears pasted on rather than grounded in the set.
