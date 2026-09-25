# Illustrated performance and cloth compositing: Spark case

## Preserve the accepted look

The accepted G cloth retained the reference illustration's matte palette,
printed ornaments and broad hanging arcs through UV textures on animated
geometry. Painted highlights and shadows were retained with emission; this
was not dynamically relit physical fabric. The earlier soft-cloth example
remains a historical comparison, not the authority for the later G adoption.

Start a draping textile with a smooth rest surface and allowance between its
supports. Shape broad gravity/wind arcs before adding small folds. Denser mesh
alone did not fix naturalness: changing resolution also changes mass, stiffness
and collision behavior. Cloth must not read as glossy plastic or rigid paper;
inspect the rendered result, warm illumination, pattern attachment and implied
transmission, rather than relying on shader names.

## Reuse acting and environment independently

1. Keep the accepted scene's poles, ropes, cloth attachments, light direction
   and perspective as continuity authority. Use the character reference for
   identity only when its background contradicts that scene.
2. Extract a bounded useful section of the existing character video. In this
   case simple GrabCut lost dark trousers and hair; the already installed
   `u2net_human_seg` model retained them better. This is evidence for testing a
   dedicated person matte, not proof that it works for every illustration.
3. Inspect hair, hands, leg gaps, motion blur and every changing silhouette.
   Composite premultiplied color with the matte; do not mistake a rectangle or
   a soft background halo for a clean foreground. Keep original source bytes.
4. Establish a floor and occlusion contract. Small authored contact shadows can
   improve grounding but are compositing approximations, not reconstructed
   physical lighting. Check airborne feet and avoid attaching shadows to them.
5. With a moving Blender camera, project the character layer with that same
   camera transform. A fixed screen-space overlay will slide against the set.
   A held ready pose during a pullback is not generated character motion.
6. Match the reveal's final camera, cloth state and character pose to the next
   performance's first frame. Otherwise the character can pop into an empty
   location even when each shot looks fine on its own.

## Frame and review the actual shot

For a cloth close-up, verify both visible upper attachment points and the free
edge through the complete motion. A tighter camera in Spark initially clipped
an upper pin; moving the camera restored the attachment. Do not move approved
geometry merely to hide a framing mistake. A deliberate detail crop may omit
anchors, but must be described as such rather than claiming full-cloth coverage.

Reuse of native cloth frames at original speed is a new framing of existing
motion, not a fresh simulation. Record the source frame range and transformation.
Keep G's adopted source unchanged; author new shots in their own bounded scene.
Check first/middle/final composites and adjacent frames, then put the candidate
into its original musical phrase with context. Numerical endpoint agreement
supports spatial continuity only; it does not certify rhythm or artistic fit.

## Scope of the evidence

Spark used 24 fps local previews: a retained 1.5-second male composite, a
3.375-second cloth/camera reveal accepted into the rough cut, and a 2-second
reframed cloth candidate. The female 2.75-second selection was still awaiting
user review. These durations and dispositions belong to this case, not global
quality thresholds. Keep asset paths, hashes, seeds, timing, credit balances and
the latest adoption decisions in the project manifest, not in reusable skills.
