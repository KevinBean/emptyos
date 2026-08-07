# From Technical Master to Art Master

Use this pass when a complete Blender video is technically sound but still
reads as a capability reel, a sequence of unrelated effects, or a repeated
hero-object showcase.

## Name the two masters

- **Technical master:** playable, stable, correctly encoded, complete assets,
  no proxy geometry, no shake/freeze/black corruption.
- **Art master:** every retained shot changes the emotional state, motifs
  evolve irreversibly, visual hierarchy follows the music, and the final frame
  resolves the film's thesis.

A technical pass never implies an art pass.

## Review the whole film as a state system

Extract one representative frame per shot into a contact sheet.  For each
shot, record:

- emotional function;
- motif state before and after;
- camera state;
- subject action;
- environmental action;
- color/contrast state;
- disposition: `keep`, `re-edit`, `rerender`, or `remove`.

Reject a shot that is individually attractive but repeats the previous shot's
state or introduces a different visual world without narrative justification.

## Rebuild hierarchy before adding effects

1. Divide the song into three to five emotional acts.
2. Assign one visual state change to each act.
3. Give each shot one function inside that change.
4. Remove demonstrations that do not serve the arc.
5. Regrade retained shots by act.
6. Rerender only the shot whose geometry, performance, or authored lighting
   truly blocks the new edit.

This preserves expensive technical work while changing the film's meaning.

## Seed a late motif early

If a human, object, or symbol first appears near the end, it often reads as a
new asset instead of a payoff.  Use a restrained bookend:

- show it briefly in the opening state;
- let the middle world continue without it;
- return to the same composition at the end;
- complete the motif's transformation or absence there.

The repeated composition must change state; literal clip duplication without
transformation is not a bookend.

## Complex models still need a performance gate

Mesh quality cannot rescue a default or misdriven pose.  For a rigged asset:

1. inspect the active action, constraints, and IK/FK mode;
2. determine which controls actually drive the deform bones;
3. render a small pose audition at delivery camera and lighting;
4. reject cruciform, T-pose, A-pose, mannequin, or balance-defying reads;
5. approve first/middle/final performance frames before the full render.

When full-influence IK constraints override FK controls, edit the IK hands and
pole targets rather than changing inactive FK animation curves.

Do not treat a successful current-frame `pose.matrix` edit as persisted
animation. Changing the frame can discard that evaluated override. Map the
control's local axes, write an animatable transform such as `PoseBone.location`,
keyframe it at the shot's required phases, then re-review a full-size decoded
frame after the animation renderer has advanced the timeline.

## Use silhouette as art direction, not asset degradation

A detailed character can be deliberately reduced to silhouette while
preserving authored anatomy, clothing folds, hair, and edge complexity.
Prefer one dedicated shader with a controlled visibility animation over
trying to tint many nested lookdev materials.  Review:

- opening legibility at ordinary playback size;
- natural pose and occlusion;
- separation from the background through restrained rim or low emission;
- complete disappearance at the intended final frame.

## Grade as narrative structure

Use act-level grading, not one global look:

- initiation: cold, fragile, low-density;
- embodiment: restrained warmth and dimensionality;
- bloom: richest but still controlled;
- pivot: flattened contrast, reduced saturation, near-stillness;
- withdrawal: progressive desaturation, dimming, and fade to absence.

Do not use grading to hide broken geometry, identity, or motion.

## Final evidence

Persist:

- shot disposition table;
- exact source paths and hashes;
- changed Blender scene and build script;
- first/middle/final review frames for rerendered shots;
- master probe, duration, frame count, audio presence, freeze/black report;
- final-film contact sheet;
- explicit technical and art verdicts.


## Semantic asset gate: never ship a technically valid placeholder

A complex asset is not automatically a designed character or a meaningful prop.
Before a visible asset enters the final edit, approve its narrative identity,
scale, screen occupancy, styling, and performance together. A rig that deforms
correctly can still read as a random doll. If character identity, wardrobe, and
performance are not controlled, crop, occlude, defocus, replace, or remove the
character. Do not expose isolated body, hair, or clothing meshes: detached parts
usually read as debris or broken geometry.

Review assets by what the audience will call them, not by their renamed Blender
collection. An object authored as `power_box_01` remains a power box even if the
shot list calls it a threshold cabinet. If that object has no story function,
remove the shot rather than inventing meaning after rendering.

## Organic motion needs morphology, local deformation, and a medium

A believable falling leaf requires all three layers:

1. species-readable outline, venation, thickness, and material variation;
2. local flex or torsion across the blade, not only whole-object rotation;
3. a continuous aerodynamic path with drift, changing angular velocity, and a
   credible exit or landing.

A leaf floating above an unreadable surface looks suspended in empty space.
Establish the supporting medium through grazing light, reflections, contact
shadow, parallax, or an explicit waterline before asking the audience to read a
landing.

Water droplets in free fall are approximately spherical to mildly oblate; the
familiar pointed teardrop is usually a graphic symbol, not a physical silhouette.
For a poetic impact shot, keeping the source off-screen and showing a surface
dimple plus one damped wavefront is often more convincing than rendering the
droplet itself.

## Calm is authored motion, not duplicated frames

Near-still shots still need a living temporal signal: low-amplitude water
micro-motion, moving reflections, particulate drift, light breathing, or fine
temporal grain. Keep it below the subject hierarchy and never substitute camera
shake for life. A freeze detector is a review trigger, not the art verdict:
inspect decoded playback, document intentional low-motion intervals, and reject
true held frames unless the hold is explicitly part of the edit.