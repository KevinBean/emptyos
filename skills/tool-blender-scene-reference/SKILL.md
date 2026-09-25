---
name: tool-blender-scene-reference
description: Build a Blender blockout of a set (room, cameras, a posable mannequin, lathed props, textures cut from an approved image) and use its renders as the control reference for photographic stills in Google Flow, iterating blockout → Flow → feed the best result's materials back until every angle comes out consistent, then staging video start (and end) frames from the same set. Use when an MV, short drama or series needs the SAME place seen from several angles, when Flow keeps inventing a different room/pose/prop per shot, or the user says "use Blender as the reference", "build the set in Blender", "make the scene consistent across angles". NOT for rendering final video in Blender (use tool-blender-comfyui-video), NOT for engineering models (tool-blender-cable-routing), NOT for Flow's own mechanics in depth (tool-flow-stills).
---

# Blender Scene Reference

<!-- skill-refs: ignore — scripts/*.py are this skill's own bundled files, relative to the skill folder -->

Blender owns **where things are** (geometry, cameras, pose, which object is
which). Flow owns **what it looks like as a photograph**. A render of the
blockout goes into Flow as the reference for everything except the character,
and the character comes from a Flow Character. Learned on one MV set over five
rounds (2026-09-20/21); every rule below cost at least one failed round.

## When it pays

- Several shots of one place: master, reverse, over-the-shoulder, down-room,
  empty seat, exterior. Without a blockout Flow invents a new room per angle.
- A gesture or pose that must match across angles.
- Not for a single hero still — one Flow prompt is cheaper.

## The one rule everything follows from

**Flow copies the blockout literally.** A cone lamp comes back a cone lamp, a
round mirror a round mirror, a handleless cylinder a handleless cup, a white
ball on blocks a white statue. And the more credible the blockout looks, the
more literally it is copied — once the room was textured, Flow stopped filling
in crude props and reproduced them. So **every object in frame must already be
the shape it should be**; the blockout is not a sketch Flow will interpret.

## Build order

### 1. Floor plan from the approved image, not by eye

If a master still already exists, solve it as a pinhole camera before placing
anything: pick a known length (table width), read vanishing lines, and derive
the focal length in pixels and the camera position. By-eye plans get whole
walls wrong — the first plan here put the window on a side wall of a corridor
when the master showed it as the wall facing camera. One room definition in
one script; every camera looks at the same room by construction.

### 2. Shapes

- Boxes are fine for furniture Flow will re-render (banquettes, tables).
  Give upholstery its channels/buttons as geometry — a flat slab came back as
  chesterfield leather.
- Anything with a recognisable form needs that form: pendant (half-sphere
  shade, not a cone), mirror (rectangle with a thick frame, or Flow draws it
  round), props (`scripts/props.py` lathes cup+handle, saucer, plate, tumbler,
  bud vase, shaker from (radius, height) profiles).
- Plants: seeded small ellipsoids along a hanging rod read as a trailing
  plant; a single green blob read as nothing and was dropped.

### 3. People: the mannequin

`scripts/mannequin.py` builds a jointed figure by forward kinematics from a
named pose of **angles** (azimuth, elevation per limb, body frame). One pose
in world space renders from every camera, so the gesture is identical in every
shot. Rules:

- **Dark and fully matte** (albedo ~0.05, roughness 1, specular 0). A pale
  figure came back as a white sculpture. Under a close light it will still
  read grey — that is lighting, and the prompt handles it.
- Name every part with one prefix (`prx_`) so per-camera hide rules remove it
  for empty-seat and exterior shots.
- Pin the head where the approved image has it (solve, then tune the spine
  lean) — check with a 50/50 overlay of render and master.
- Prompt it as a PLACEHOLDER and describe the pose in words (see template).

Result measured: with boxes each variant invented its own posture; with the
mannequin all three variants of the master held the exact gesture, and
identity scores rose.

### 4. Materials: pixels from the approved image

Flat colour gets a material invented per angle (leather style, the
photograph in the frame, the street outside all differed shot to shot). Cut
crops from the approved master and put them on the geometry:

- **Tiled surfaces** (plaster, leather, wood, floor): world-space box
  projection, REPEAT (MIRROR bookmatches grain into flame veneer), and
  **flatten** the crop (divide by a heavy blur) or every tile edge shows the
  source's lighting gradient as a checkerboard. Add large-scale noise breakup.
- **One-off images** (a framed picture, a mirror, the view out of a window):
  pin to world coordinates with a planar mapping. Pin the view out of the
  window where the master's camera sees it (pinhole), or the tree lands behind
  a post and Flow drops it.
- **The street card** must not light the room (`visible_diffuse = False`),
  must be defocused (sharp = printed mural), and **must not be clamped** past
  the crop's top — the smeared top row read as vertical blinds from a higher
  camera. Pad the crop upward with a sky gradient instead.
- **Give each object class its own material.** Window metal, picture-frame
  wood, a rusted panel and furniture legs sharing one "dark frame" material
  come back from Flow as one finish — the window frames and the picture frame
  rendered identical. Distinguish them in the blockout (colour AND roughness),
  and read each class's finish off the master.
- Base Color values are **linear**: 0.14 looked "dark red" and rendered
  salmon. Use AgX and an exposure a little under neutral.
- Glass: EEVEE transmission rendered a tumbler as a solid blue cylinder, and
  Flow copied it. Use a near-white Principled with low alpha and
  `surface_render_method = "BLENDED"`.

## The iteration loop

```
blockout vN ──render all cameras──▶ Flow ×3 per angle ──▶ score
     ▲                                                     │
     └── fix 1-2 parts, taking materials from the best ◀───┘
         image of the round when the master lacks them
```

1. Render every camera; export JPGs for upload.
2. Flow: one prompt per angle, three free variants (direct mode, not Agent).
3. Score each angle (below) and write the round into a scorecard file.
4. Pick **one or two** parts to fix. Take a missing material from the best
   Flow result of the round (e.g. a floor texture nobody had defined) —
   `FED_BACK` crops in `make_textures.py`-style scripts. **Only for surfaces
   the master does not show.** Before feeding anything back, crop the same
   surface from the master: if the master shows it, the master wins, however
   good the Flow version looks. A fed-back crop carries that variant's
   inventions with it — a window post fed back from a Flow image brought its
   invented rust streaks, and the next round put rust on every window in the
   room (the master's post was plain dark metal; its rust was only on the
   mirror rim and one panel).
5. Re-render, and re-test **only the angles the change touches**. An
   unchanged angle rerun adds no information.

**Exit gate:** every angle passes in the same round. **Stop rule:** an item
still failing after two rounds of *model* fixes is out of Blender's control —
it moves to a per-image Flow edit and stops being modelled.

## A room is judged by what the cameras see, not by its plan

Check the set in a **plan view** (top-down ortho render, cameras marked) as well
as through the cameras. A room with half its floor bare reads small however
big it is; a feature the shot list needs (the bar a barista works at, the door
he leaves by) must face the camera that shoots it. A door in a side wall is
seen edge-on and reads as one more window — put it where its camera sees it
head-on. Keep what an approved image pins (here the booth corner and its
pinhole solve) untouched while the rest of the room grows.

## States: one room, many moments

A film rarely stays at one time of day, and repetition devices need one
change per pass (water lower, a lamp warmer, chairs up, a cup cleared). Build
them as **named states applied to the one room by object name** after the
build — `--state=<name>` renders `blockout-<state>-<camera>.png` for that
state's cameras. Name every light when you create it, or a state cannot find
it. Read the film's light progression from the treatment first: a set frozen
at one moment will be used for shots set at another.

- **Dark states need a control exposure.** A true-night blockout is too dark
  for Flow to read: 2 of 3 night variants redrew the camera. Render the
  control image brighter (`ctrl_ev` per state) and let the prompt carry the
  darkness — but darken the sky itself too, so the window still reads darker
  than the room at control exposure, or the night comes back as blue hour.
- **Lit things must stay lit where the shot needs them.** A wall whose lamps
  were switched off for "night" rendered as a black void and came back as
  more street; keep a few warm pools on.

## One film, one world — check every location against every window

A set's window view is itself a location. On 說得太急 the café's windows showed
a modern city street (tree, blocks of flats, lit shopfronts) while the film's
second location, a street corner taken from the album cover, was an old-town
ochre alley. The scene bible excused every exterior except the one looking
*into* the café from needing geometry, so nobody compared the two until the
first corner clip came back and the director asked whether it was "just some
random street". Two towns in one three-minute film.

Before any exterior still, lay the window-view card of every interior next to
every exterior location and answer one question: **could a person walk from
one to the other?** If not, it is an inconsistency, and it gets resolved in
Blender (re-cut the street card, or build the exterior so both share a street)
— not by prompting each shot harder. The exemption "only S1-D needs geometry"
was about *shape*; the *world* still has to be shared.

## Staging a start frame for video

A still that works as a photograph can fail as the first frame of a clip. The
start frame is Veo's prompt; the words only steer. Measured on the 說得太急
proof clips (Veo 3.1 Fast, 2026-09-22):

| Start frame | What Veo did | Stage it instead as |
|---|---|---|
| He is already far away at the door (S24 "push the door") | Invented a **second man** walking in from the foreground to have someone to animate | Him near the camera at the action's *start*, or an empty room he enters |
| A long shadow painted on the pavement, nobody in frame (S25 "the shadow arrives first") | Kept the shadow fixed; the man walked in with a *different* shadow | Cast the shadow from a mannequin placed just **outside the camera frustum**, lit by the scene lamp, so it belongs to someone |
| Leaning in, hand half raised (S19 "only his shoulders breathe") | Continued the lean into a full bow after ~4 s | A settled pose with nowhere to go; or render the **same frame as start and end** and use both slots |

The Blender move for all three: render the start **and** the end of the action
from the same camera (the mannequin moved between them), and give Flow both
frames. What a still *implies is about to happen* is what the clip does.

Two refinements measured on 說得太急 (2026-09-23):

- Put the only subject at the **true action origin** in the start frame and
  leave the destination empty. This produced one continuous man walking to a
  far door; leaving a man at the destination made Veo invent another mover.
- Encode travel direction in the proxy's torso, head and leading foot, not
  only in prompt prose. If three free still attempts keep reversing the
  direction, stop: use the verified part of the action plus an empty aftermath
  frame. Rewording the same spatial contradiction is not progress.

## Making a surface beautiful, not just filled

"Too empty" and "not beautiful" are different notes. A row of small objects,
evenly spaced, fills a wall and reads as a catalogue from across a room. What
worked: **layers at mixed scales** (a large mirror, a clustered hang of small
frames, a piece of furniture with objects on it, a tall plant) in the set's
own material language, and **shaped light** — wall lamps throwing pools that
graze the plaster, a glowing table lamp — with deep shadow between. Check the
lights actually emit: a spot inside its own solid shade shadows itself, and
leaf-balls read as topiary or marbles (use long, flat, rotated leaves).

## Scoring — and reading variance as a signal

Per angle, over the three variants:

| Check | Pass |
|---|---|
| Layout | every landmark where the blockout puts it, ≥2/3 |
| Look | fixed-set items match the master, ≥2/3 |
| Invented | no extra window/door/text/person/sculpture |
| Identity | ArcFace ≥ 0.36 vs the canon face (0.30–0.36: look at it) — calibrated on one film's user-confirmed samples (0.366–0.519, 說得太急 scene bible §1, 2026-09-20); re-calibrate per character |

Two readings turn a score into a fix:

- **All three fail the same way → the blockout causes it.** A shared failure
  is never a seed problem.
- **The three disagree about an object → the blockout left it ambiguous.**
  Wood / terrazzo / concrete floor across variants; white / dark / white window
  frame; globe / none / ceiling pendant. Each disagreement is an object Blender
  must pin down — that is the next round's target list.

### Diagnose with a raycast before fixing

Guessing which object makes a pale strip wastes a round. Cast a ray through
the pixel and read the object's name (`scripts/probe.py`: build the scene,
create the cameras, `scene.ray_cast` per (u, v)). Two traps it caught:

- A near-black window post rendered pale — not albedo (still pale at 0.08) but
  a **reflection** of the street card; matte + low specular fixed it, and Flow
  had been reading the pale strip as white paint.
- `ray_cast` ignores `hide_render`; set `hide_viewport` on the per-camera
  hidden objects first or you probe the stand-in you meant to hide.

And check the **prompt** before the model: describing a plaster corner as "the
corner of the street wall" produced a window there twice.

## Flow submission (details: tool-flow-stills)

- Upload by clipboard paste into the composer (set the clipboard to the image
  immediately before pasting; another process can overwrite it).
- Agent pill **off** — Agent mode rewrites the prompt and drops the geometry
  clauses. Three variants.
- Add the character through the picker's Characters filter, by element
  reference, not coordinates — the grid reflows, and a stray click either adds
  a random asset as an ingredient or opens an image's edit box (where typed
  text would edit that image). **Zoom the composer and confirm exactly the
  intended attachments before every send.**
- Refer to attachments by description ("the attached rough 3D render", "the
  attached character reference"), never by order.
- Downloads: 1K original size; file names come from Flow's titles and can be
  anything (`image.png`) — verify each file by opening it, not by name.

### Prompt template

```text
Photographic 16:9 still of <place, time>. The attached rough 3D render is the
reference for EVERYTHING except the man: keep its camera position, lens and
framing, and every <object list> in the same position and scale - <each
landmark, where it is, what it is made of>. Take each surface's colour,
pattern and position from the render, but render every surface as the REAL
physical material, not as the render's flat 3D surfaces: <real materials> -
a photograph of this room, not a copy of the render's shading. Keep the
render's <grade>. Do not add or remove anything. The dark jointed mannequin
<where> is a PLACEHOLDER showing exactly where the man sits and how he is
posed: <pose in words>; it is NOT an object. Replace it with the real man from
the attached character reference in exactly that position and pose. There is
no mannequin, doll, statue, sculpture, sphere, ball, plinth or block anywhere.
The character reference is his identity only - do not copy its composition,
background or lighting. He is the only person in frame, lips closed, calm.
No text or lettering anywhere.
```

Empty shots drop the character sentences and say "There is no person anywhere
in this photograph." Name materials you have pinned ("pale terrazzo floor",
"dark metal window frames") so prompt and blockout agree.

## Failure catalogue

| Symptom in Flow | Cause in the blockout | Fix |
|---|---|---|
| Invented a window / different wall | shape or prompt left it open | model it; name it in the prompt |
| Round mirror, cone lamp, handleless cup | crude primitive copied literally | model the real shape |
| White statue where the man should be | pale stand-in | dark matte mannequin + placeholder clause |
| Pose differs per variant | box stand-in carries no posture | mannequin, pose as angles |
| Material differs per variant | surface left flat colour | texture from master or best Flow image |
| Vertical blinds at the window head | clamped texture smear | pad the crop, don't clamp |
| White-painted frame | specular reflection on a dark post | matte, low specular (probe first) |
| Solid blue glass | EEVEE transmission | alpha-blended clear glass |
| Tree missing / wrong street | street card not pinned to the master's view | pinhole-pin the crop |
| Exterior location reads as another town | window-view card never compared with the exteriors | one shared street (see "One film, one world") |
| Clip adds a second person | start frame leaves the subject nothing to do | stage the start of the action; start + end frames |
| Shadow in the still never moves | shadow painted, not cast | cast it from an off-frame mannequin |

## Files and records

- One script builds the room (`booth.py`-style: constants, `build()`,
  `CAMERAS`, `HIDE`, `--render --only=<cams>`); run headless:
  `blender --background --python booth.py -- --render`.
- `scripts/mannequin.py`, `scripts/props.py` — import from the set script.
- A texture script cutting crops (`CROPS` from the master, `FED_BACK` from a
  round's best image, `FLATTEN`, `DEFOCUS`, `SKY_PAD`).
- A scorecard markdown per set: exit gate, per-angle checklists, one table per
  round, and the fixes carried into the next round.
- Record every Flow attempt in the MV library when the project uses one
  (`scripts/mv_library.py record-attempt`); codes must come from its
  failure-codes vocabulary.
- Identity: the face harness (`harness/measure_identity.py` in the project's
  lip-sync test folder, run as `--ref canon=<face crop> --expect canon <files>`)
  takes `--json <output path>` — passing an image path there **overwrites the
  image**. Run without it and read stdout.
- `scripts/probe.py` re-imports the set script by path, so the set script must
  keep its build/render behind `if __name__ == "__main__":` — otherwise the
  scene is built twice and probe reports `.001` duplicates.

## Blender gotchas

- A local variable named like a module-level function (`clear`) shadows it for
  the whole function body → `UnboundLocalError` at the earlier call.
- Hide rules that match by prefix hide everything sharing it (`street` hid
  `street_solid`) — give stand-ins unique prefixes.
- Inline patches to the set script: assert each `old` string occurs exactly
  once before replacing, or a no-op patch "succeeds".
