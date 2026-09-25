# Montage and Music-Video Editing

Read this reference when planning a storyboard, revising a living master, or
diagnosing a cut that feels like unrelated lyric illustrations. It is an
editorial decision guide, not a demand to use every technique.

## The governing idea

A shot has three meanings:

1. what it shows by itself;
2. what the adjacent shot makes the viewer infer;
3. what its position in the whole song makes it remember or anticipate.

Montage therefore includes both immediate collision and long-distance
recurrence. Do not reduce it to transitions, rapid cutting, or matching every
beat. A straight cut between well-related shots is stronger than a dissolve
between unrelated images.

## Montage families

Choose the smallest useful family for a passage. These can coexist within one
MV.

### Continuity and constructive montage

Build one readable event from partial views. Preserve screen direction,
eyeline, spatial landmarks, action phase, light, wardrobe, and prop state when
the viewer should understand continuous space or time.

Useful relations:

- action begins in one scale and finishes in another;
- a look is followed by the thing seen;
- an object exits one frame and enters the next with compatible direction;
- a wide establishes geography, details elaborate it, and a return shows the
  changed state.

Continuity does not require returning to one master shot. A sequence may
migrate through adjacent spaces if a visible bridge carries the viewer.

### Metric montage

Shot duration follows a designed temporal ratio. Use it briefly to create a
pulse, acceleration, insistence, or countdown. It becomes mechanical when
applied to an entire song without regard to image content.

### Rhythmic montage

The cut responds to motion inside the shot as well as musical time. Let a turn,
pedestrian crossing, falling object, lighting change, or camera move reach its
readable phase before cutting. A cut can land on a beat, anticipate it, or
resolve just after it.

### Tonal montage

Join shots by emotional temperature: light, colour, texture, scale, gaze,
pressure, or stillness. The images may show different objects but should
intensify one feeling.

### Overtonal montage

Combine metric, rhythmic, and tonal pressure. Use it for a section climax,
where shorter duration, larger movement, brighter contrast, and repeated forms
work together. Do not sustain maximum pressure for the whole MV.

### Intellectual or associative montage

Juxtapose two images so their collision creates an idea absent from either
alone. Record that intended third meaning explicitly. If the inference cannot
be stated, the pairing is probably decorative.

### Parallel and counterpoint montage

Alternate simultaneous strands, or deliberately oppose music and image.
Counterpoint is purposeful disagreement, not random mismatch: cheerful music
may accompany an emotionally withheld action, but the contrast must reveal a
specific subtext.

### Distance montage

An image can answer another image much later. Recurring motifs, gestures,
compositions, or sounds create a whole-film field without forcing every scene
to loop back. A return must transform, recontextualize, or complete the earlier
state. Mere repetition is asset reuse, not montage.

## Three musical time scales

Plan edit rhythm at three scales before choosing exact cuts.

### Macro: whole-song form

Map intro, verse, pre-chorus, chorus, bridge, final chorus, and outro. Decide
where visual grammar changes: location family, subject scale, colour pressure,
camera behaviour, motif state, and density. Section contrast matters more than
constant activity.

### Meso: phrase and lyric thought

Treat a sung phrase or complete thought as the default semantic unit. A lyric
line may contain several shots when one action needs articulation, and one
shot may span multiple lines when holding it creates meaning. Do not force
one-line/one-shot unless the current song contract asks for it.

### Micro: beat, onset, and gesture

Use beats, downbeats, drum fills, pickups, note attacks, held notes, and rests
as candidate edit cues. Also mark visual impacts, gaze changes, landings, and
state changes. A good cut aligns the chosen musical cue with the chosen visual
phase; it need not cut on every available marker.

## Elastic synchronization

For every important cut or action accent, declare one relationship:

- `sync`: cut/action lands with the cue;
- `anticipate`: picture changes just before the cue and makes it arrive harder;
- `afterbeat`: picture resolves just after the cue and feels relaxed or heavy;
- `sustain`: hold across expected beats to create focus or tension;
- `counterpoint`: picture follows another rhythm for a stated dramatic reason.

Use section boundaries and lyrical accents as stronger structural evidence
than indiscriminate beat markers. Repeated perfect synchronization can flatten
groove; variation creates phrasing.

## Relationship-first scene planning

For each planned shot, store editorial intent in the living master or an
editorial sidecar. Do not send unsupported fields to a production API.

- `sequence_group`: the local scene, geography, action, or idea this shot
  belongs to;
- `entry_relation`: how it follows the previous shot (`same_action`,
  `eyeline`, `screen_direction`, `graphic_match`, `motion_match`,
  `spatial_bridge`, `associative_collision`, `counterpoint`,
  `distance_return`, or `intentional_reset`);
- `entry_inheritance`: the exact element inherited from the previous shot;
- `exit_payload`: the action, object, direction, shape, emotion, or question
  made available to the next shot;
- `montage_function`: `continuity`, `metric`, `rhythmic`, `tonal`,
  `overtonal`, `intellectual`, `parallel`, `counterpoint`, or `distance`;
- `audio_cue`: the real timestamp and musical/lyrical event used;
- `sync_phase`: `sync`, `anticipate`, `afterbeat`, `sustain`, or
  `counterpoint`;
- `third_meaning`: the inference produced by the pairing; may be `none` only
  for transparent continuity;
- `return_policy`: `none`, `local_insert_return`, or `distance_recurrence`;
- `motif_state`: what changed since the motif last appeared.

An intentional reset is legal at a section break, climax, interruption, or
emotional rupture. Name what the discontinuity expresses. "New lyric" by
itself is not a reason.

## Shot state and cut-point ledger

For a character/action video, annotate every selected shot before assembly.
During planning, record **intended** entry/exit states to request useful
coverage; after generation or import, replace assumptions with **observed**
states. A prompt, filename, source first-frame still, or successful render is
not evidence of the selected action. This applies equally to a new MV and an
existing-footage recut. Keep the ledger in the existing living-master/EDL or
an attached editorial sidecar; it is not a new production API or approval stage.

Use this compact record, extending it only when the action needs more detail:

| Field | What to record |
|---|---|
| `shot_id`, `subject_id`, `sequence_group` | Stable shot and subject identities; separate subjects when their paths differ. Use `none` for a subject-free insert. |
| `source_range`, `timeline_range` | File/hash, native fps, selected integer `[in, out)` source frames and delivery frames. After trimming, the end-state evidence is frame `out - 1`, not the original clip's last frame. |
| `screen_travel` | Left, right, toward camera, away, diagonal, stationary, turning, or unknown; split a shot into phases if it changes. Record observed direction and confidence, not just an arrow guessed from the pose. |
| `facing`, `gaze`, `camera_motion` | Separate where the body faces, where the eyes look, and how the camera moves from actual travel. A tracked runner can stay centered while advancing; a backward glance is not a route reversal. |
| `entry_state`, `exit_state` | At the selected boundaries: frame index/image, screen position and scale, pose/action phase, movement vector, grounded/airborne state and supporting foot when visible, gaze, held objects, wardrobe/label side and relevant environment state. Mark occluded details unknown. |
| `usable_handles`, `reject_ranges` | Clean lead-in/tail and excluded source intervals with observed reasons: suspended start, incomplete landing, awkward preparation, deformation, idle tail, etc. An airborne phase is valid when intended and connected to takeoff/landing. |
| `motion_profile` | Walk/run/jump/pause, perceived pace at delivery speed, source-to-delivery speed ratio, and any ramp, optical interpolation or authored hold. Distinguish slow generation from editor-added slow motion. |
| `transforms` | Crop, scale, mirror and other edits that affect apparent direction or framing. Recheck the rendered result and regenerate direction/state annotations after these transforms. |
| `cut_relation`, `emotional_intent` | Same action, spatial/eyeline bridge, neutral detail, or deliberate reset; state whether the neighboring shots should sustain momentum or interrupt it, and why. |
| `evidence`, `review_status` | Boundary frames plus short moving entry/exit windows at delivery speed; identify what was actually inspected and what remains uncertain. Sampled stills do not prove normal-speed gait or a seamless cut. |

For a non-character shot, record the dominant moving object or camera motion
and `none` for irrelevant body/foot fields. Do not invent bodily detail to
complete the record. A useful ledger distinguishes unknown from stationary.

## Action-montage seam review

Review each outgoing **selected** action against the next incoming action,
including internal A/B/A cuts and shortened takes. Do not treat one source file
or one lyric slot as necessarily one shot. Recompute the ledger and timeline
markers after a trim, reorder, retime, crop or mirror.

1. **Establish the intended connection.** For continuous effort or travel,
   preserve compatible direction, action phase, apparent speed and spatial
   anchors. Compare actual endpoint poses and short motion windows. Matching
   arrows alone cannot excuse repeated takeoffs, missing landings, reversed
   prop states, scale jumps or a running-to-floating seam.
2. **Resolve a direction change deliberately.** Prefer a visible turn, a
   compatible angle or an insert with a relevant onward connection. A detail
   is not automatically neutral: feet, hands, paper and water can carry a strong
   vector too. A neutral insert can relax screen-axis expectations but cannot
   prove literal geography or repair a missing action phase.
3. **Allow emotional discontinuity.** Opposite directions are not forbidden.
   At a rupture, interruption or section change, a reset may be the better cut.
   State the intended effect and inspect it in the phrase and whole-song arc;
   do not rationalize every accidental reversal as emotion after assembly.
4. **Trim around readable action.** Exclude defective preparation, hovering
   openings and incomplete tails. Enter on an established clean phase or let
   the action begin visibly. Do not turn a legitimate jump into a universal
   grounded-first-frame rule. If trimming leaves a gap, reflow nearby coverage
   or use a motivated insert while keeping the selected soundtrack intact.
5. **Match perceived pace.** Inspect the action at its actual delivery speed
   before and after the cut. A long gentle foot-contact insert can interrupt
   an otherwise energetic jump even at native fps. Shorten or choose better
   coverage, or use a reviewed bounded retime; do not prescribe a universal
   speed multiplier or slow footage simply to fill a lyric duration. Keep
   intentional slow motion/holds when the musical and emotional phrase earns it.
6. **Treat mirroring as a conditional option.** Check handedness, asymmetric
   clothing, text, props, light and geography first. An isolated feet insert may
   tolerate a mirror when a face-and-sleeve shot cannot. Record it and inspect
   the final pixels; never silently relabel unchanged footage as the desired
   direction.
7. **Review moving seams, then the film.** Watch several surrounding actions
   in silence for spatial/action continuity, then with the original audio for
   pulse and emotional intent. Endpoint stills and contact sheets locate issues;
   they do not replace moving audiovisual review. If that review is unavailable,
   report its limit instead of claiming it passed. Container, black-frame and
   freeze checks remain separate technical evidence.

Keep a timecoded direction/state table and editor timeline markers as reusable
editing information. When the user requests a direction review, export a
separate annotated version with shot ID and direction; retain a clean version.
Both must use the same EDL, timing and audio. Do not burn diagnostic labels into
the delivery master or export a second full video on every routine edit.

Before delivery, check the ledger against the final render: annotations cover
every selected shot and internal cut, boundary evidence matches the final
ranges/transforms, and each discontinuity is repaired, intentional, or honestly
unresolved. Examples to challenge the review: a tracked leftward runner whose
screen position barely changes; an airborne opening without a takeoff; a
raised-arm preparation that can be trimmed; a slow foot insert after a fast
jump; and an opposite-direction cut that is justified by an emotional reset.

## Coverage for an editable AI-generated MV

Generation must provide editorial options, not merely one delivery-length clip
per lyric.

For each scene group, budget only the coverage the edit needs:

- one orientation shot when geography must be understood;
- a compatible wide/medium/detail set when an action needs articulation;
- inserts tied to actual props, reflections, materials, or gestures in the
  host scene;
- clean entry and exit phases around a directed action;
- a bounded tail handle when trimming to a musical cue is likely;
- consistent movement direction, light, wardrobe, object state, and motif
  state across connected shots;
- one deliberate climax image whose arrival is prepared rather than repeated.

Do not generate generic B-roll merely to increase quantity. Every insert needs
a host, an entry trigger, and a return or onward payload. Do not spend equal
coverage on every shot; difficult transitions and central actions deserve
more options than stable atmosphere.

## Storyboard and living-master passes

Run these passes separately so one success cannot hide another failure.

1. **Silent continuity pass** — Can the viewer understand where attention,
   objects, and action states travel without lyrics?
2. **Audio-only structure pass** — Mark phrases, section changes, pickups,
   rests, drum fills, accents, and energy curves from the actual recording.
3. **Audio-visual rhythm pass** — Does the cut pattern phrase with the song,
   including holds and off-beat choices, rather than merely count beats?
4. **Third-meaning pass** — At associative cuts, state what the juxtaposition
   adds. Remove pairs that add nothing.
5. **Distance-recurrence pass** — Do later motif returns transform earlier
   meaning? Remove empty callbacks and forced loops.
6. **Energy pass** — Compare cut density, shot scale, internal motion, and
   contrast across sections. Fast music does not require constant fast cutting,
   but it does require visible rhythmic life.
7. **Source-feasibility pass** — Does each cut have clean, forward-moving,
   approved source coverage? Editing cannot invent missing action phases.

## Common failures

- one unrelated illustration for every lyric line;
- forcing every insert to return to one anchor scene;
- changing location without a spatial, graphic, motion, tonal, or conceptual
  bridge;
- cutting on every beat and mistaking synchronization for rhythm;
- using dissolves, speed ramps, or camera shake to disguise weak shot
  relationships;
- reusing a motif without changing its state or meaning;
- matching shapes while contradicting screen direction or emotional logic;
- holding slow motion throughout an upbeat song;
- generating a completed action in the starting still, leaving the video model
  nothing to perform;
- stretching or replaying a finished action to fill a lyric window;
- approving each shot separately without reviewing the whole-song sequence.

## Sources and what they contribute

- BFI, *Where to begin with Sergei Eisenstein* — the five montage methods,
  shot collision, and new meaning:
  https://www.bfi.org.uk/features/where-begin-sergei-eisenstein
- BFI / Sight and Sound, *Artavazd Pelechian ... poetic montage* — distance
  montage, whole-film unity, waves, and transformed recurrence:
  https://www.bfi.org.uk/sight-and-sound/features/artavazd-pelechian-nature-seasons-poetic-montage
- Avid Media Composer First curriculum — shot order changes inferred meaning;
  suitability depends on the surrounding story:
  https://cdn-www.avid.com/-/media/avid/files/education-pdf/video-editing-music-production-curriculum/mcfirst-2021-sample-pp.pdf
- ISMIR 2021, *On the Audio-visual Synchronization of Music Videos* — editors
  respond to sections, rhythm, beats, and downbeats, while exact sync varies by
  desired atmosphere and off-beat cuts can create dynamics:
  https://archives.ismir.net/ismir2021/paper/000067.pdf
- HKU, *audeosynth: Music-Driven Video Montage* — visual activity, not cuts
  alone, participates in synchronization; music-to-image matching also spans
  pace, velocity, loudness/dynamism, timbre/colour, and melody/mood:
  https://hub.hku.hk/bitstream/10722/215520/1/Content.pdf
- Adobe Premiere, *Edit a music video* and *Bring your travel videos to life
  with music* — markers, speed changes, shot-scale variation, similar actions,
  and visual flow:
  https://helpx.adobe.com/premiere-pro/how-to/edit-music-video.html
  https://www.adobe.com/learn/premiere-pro/web/add-music-to-travel-videos
- Adobe, *How to make and edit a music video* — storyboards, shot lists, basic
  coverage, editorial alternatives, and a prepared climax shot:
  https://www.adobe.com/uk/creativecloud/video/discover/how-to-make-a-music-video.html
- Berklee Online, *Music Video Production* and *Music Video Editing with Final
  Cut Pro* — emotional translation, visual flow, musical structure, rough-edit
  pacing, continuity, and delivery:
  https://online.berklee.edu/courses/music-video-production
  https://online.berklee.edu/courses/music-video-editing-with-final-cut-pro
- Runway, *How to create longer videos and films* — storyboard individual
  generations, use character/environment plates, compare scenes side by side,
  and assemble a story-first rough cut before fine timing:
  https://help.runwayml.com/hc/en-us/articles/26871350018835-How-to-create-longer-videos-and-films

These sources provide techniques and evidence, not a universal style. The
current song treatment and the director's approved taste remain authoritative.
