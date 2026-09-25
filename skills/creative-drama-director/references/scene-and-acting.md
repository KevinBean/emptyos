# Scene and acting — a two-hander this pipeline can actually render

Companion to `creative-mv-art-director/references/montage-and-mv-editing.md`,
which covers cutting. This covers what happens *inside* a scene: dramatic
shape, staging, and performance, for two people in one room.

Written 2026-09-20 after the first 短劇 capability test produced eight
technically clean shots with no dramatic shape and no acting in them. Every
rule here is stated so it can be written into a prompt, a planner field, or a
review gate. Where a rule exists only because of a model limitation, it says so.

**Reference lines** (Kevin's choice, 2026-09-20): stillness and duration ·
two-hander conversation · restraint and withheld gesture · TV two-handers.
Deliberately **not** other 短劇 — the work should not be derivative of the
genre.

---

## 0. The hard constraint, first

Everything below is filtered through what the renderer can do. Designing
around this is not a compromise; it selects a real film language.

| Can execute | Cannot execute |
|---|---|
| A locked-off frame | A camera move (a crane prompt rotated the whole scene) |
| A close-up or medium single | A wide two-shot carrying dialogue (83 px faces failed lip-sync) |
| Small in-frame motion: breath, blink, gaze shift, a hand | Complex blocking, walking through space, two people interacting physically |
| One action per shot | An action with a reversal inside it ("lifts, then slides") |
| A held frame with almost nothing moving | A shot over ~8 s with large motion (the motion band collapses to `minimal`) |
| Speech **or** body motion | Both at once — v2v flattens body motion to about a third |

The style that falls out of this list — static frames, singles, stillness
broken by one small event — is precisely the style of the four reference
lines. That is why they were chosen.

---

## 1. A scene is a change, not an exchange

The test scene failed here before a single frame was rendered. Two people
spoke, information passed, nothing changed.

**Beat · turn · button.**

- **Beat** — the smallest unit: one character's attempt at one thing. A scene
  is three to six beats, not one.
- **Turn** — the moment the scene stops going the way it was going. Someone
  gets something they did not expect, or gives something they did not intend.
  A scene without a turn is exposition with faces.
- **Button** — the last image or line, which lands the change. Usually
  visual, usually silent, and usually *after* the last line rather than on it.

**The test:** name what is different at the end. If the only answer is "the
audience knows more", the scene has no turn. In our store scene the honest
answer was "nothing" — he confesses, she replies kindly, the bus of the plot
never arrives.

**Where the turn lives in a two-hander.** Almost always in the *listener*.
The speaker delivers; the scene turns when the other person's position moves.
That is why coverage must protect the reaction (§3) and why the reaction shot
is an acting problem, not a filler problem (§5).

**Cheap turns that work in one location**, all renderable here:

| Turn | Shape |
|---|---|
| A confession lands differently than intended | He means it as an apology; she hears it as a request |
| A withheld fact surfaces | She knew already |
| A gesture reverses a refusal | She says no and hands him the thing anyway |
| Status flips | The person asking becomes the person being asked |
| A silence is taken as an answer | The button is the silence, not a line |

---

## 2. Stillness and duration — what earns a held frame

From the Ozu / Kaurismäki / Akerman / Tsai line. What they share is that
**duration is the performance**: the shot holds past the point where a
conventional cut would come, and the holding is what produces feeling.

Three usable ideas:

**a. The silent insert is a structural unit, not filler.** Ozu cuts to a
corridor, a kettle, a rooftop — images with no people, placed *between*
dramatic beats. They do three jobs: release pressure, mark elapsed time, and
let the previous beat land. This is the single most valuable import for us,
because a people-free held frame is what this renderer does best.

Rules: an insert belongs to the scene's space (the same store, not a stock
image); it carries one small motion with a stated physical cause (rain on
glass, steam, a flickering tube — never an object moving by itself); and it
is placed *after* a beat, not before, so it reads as consequence.

**b. Action performed at real length is drama.** Akerman's domestic work makes
an ordinary task the event by refusing to compress it. For us: one small
action, one shot, uninterrupted, at real speed. Do not cut inside an action —
which is also a hard technical rule (§6).

**c. Deadpan is a register, not an absence.** Kaurismäki frames people
frontally, flat and still, with almost no expressive movement, and the comedy
and pathos come from the gap between what is happening and how little the face
does about it. This is worth naming because it converts the renderer's
weakness — faces that do very little — into a deliberate style, provided the
*writing* supplies the pressure the face is withholding.

**What earns the hold:** something is being withheld, or something is about to
change, or the previous beat needs to land. Holding on nothing is not stillness
— it is dead air, and the measurement will show it as a flat motion trace.

---

## 3. Coverage when every dialogue shot must be a single

The pipeline forbids the normal two-shot for dialogue. That constraint is
close to Ozu's practice anyway: frontal singles, cut to alternate, with the
space established once and thereafter trusted.

**The minimum set for a two-hander:**

| Shot | Job | Notes for this pipeline |
|---|---|---|
| Orientation | Establish who is where, once | Wide is fine **because it is silent** |
| Single A | His line, his face | CU; face ≥ ~120 px in the rendered frame |
| Single B | Her line, her face | Mirrored screen direction |
| Reaction (either) | Where the turn lands | Held, silent, cut *before* the other stops speaking |
| Insert | Release and elapsed time | People-free, same space, one caused motion |
| Button | The change, stated visually | Usually silent, usually last |

**Overlap the sound, not the picture.** The one real cutting tool available is
the L-cut: cut the picture to the listener while the speaker's line continues.
It does two things at once — it puts the turn on the listener's face and it
hides the fact that we cannot render two performances in one frame.

**Do not cut on the line's last syllable.** Cut before it, so the tail plays
over the reaction; or after a beat of silence, so the silence belongs to the
listener. Cutting exactly on the end is the flattest option and it is what an
onset-driven editor does by default.

---

## 4. Screen direction is a contract with the space, not with the image

Already established in the Spark production pack and worth restating as the
general rule: **reverse angles are decided by a space map written once, not by
what looks good in each individual image.**

Write the map before the shots: camera on the customer side of the counter;
陈默 screen-left, looking screen-right; 周岚 screen-right, looking
screen-left; door screen-left; the street beyond it. Every prompt then quotes
its own side and eyeline from the map.

Two failure modes this prevents, both seen in the earlier bus-stop project:

- **Eyeline ping-pong** — both characters look the same way, so they read as
  standing side by side rather than facing each other.
- **Silent jump** — an insert or a re-staged shot flips the geography and the
  audience relocates the room without noticing why it feels wrong.

The insert must obey the map too: a cutaway shot from the far side of the
counter crosses the line as surely as a reverse of a face does.

---

## 5. Acting, when the face can only do a little

The model gives us: breath, blink, gaze shift, a small head turn, a hand
entering frame, the mouth driven by audio. That is a narrow instrument — but
it is roughly the instrument the restraint line uses on purpose.

**a. Stillness is the default; motion is an event.** Every movement should be
the *only* movement in its shot, and should be readable as a decision: she
looks away, he stops looking at the phone. If two things move, neither reads.
Name the stillness explicitly in the prompt, not just the motion — "everything
else in the frame stays still" is doing real work.

**b. One gesture, recurring, diminishing.** The strongest acting device
available to us, and already practised in 說得太急, where a half-raised hand
recurs across three shots and shrinks each time. The gesture carries the arc
that the face cannot. Design it at the treatment stage: what it is, where it
appears, and how it changes — full, reduced, suppressed.

**c. Subtext is a withheld action.** Write what the character *does not* do.
"His hand starts toward the cup and stops" is renderable, legible, and is the
whole Wong Kar-wai / Lee Chang-dong register in one line. Compare to "he looks
wistful", which the model will answer with a generic expression.

**d. The reaction shot is acted by the cut.** A listening face that merely
holds still is not a performance; what makes it read is *when* we arrive and
*when* we leave. Arrive before the line ends, leave after a beat of silence.
Within the shot, ask for one micro-event only — a blink at the wrong moment, a
gaze drop, a swallow — never an expression change described as an emotion.

**e. Say what the face does, not what it feels.** The vocabulary that survives
the model: eyes down, eyes to him, blink, a breath in, jaw set, chin lifts,
shoulders drop, lips stay closed. The vocabulary that fails: sad, moved,
conflicted, wistful, a flicker of recognition.

**f. Keep the mouth shut between lines.** `closed-lip-performance` has 39
passes behind it. Never write sigh, exhale, smile, grin, laugh — each one
returns teeth, and teeth return a different face.

**g. Do not let the actor turn to camera.** `subject_turns_to_camera` is a
recorded failure code; in a two-hander it also destroys the eyeline contract.

**h. Age and register.** The recorded performance rules from the MV work apply
unchanged: no childlike bouncing or hopping, no held teeth grin, grounded
weight, feet no wider than hips.

---

## 6. Action beats — the known weak point

Our one action shot inverted itself: asked to slide a can and an umbrella
across a counter, the hand lifted the can away instead.

Working rules until measurement says otherwise:

- **One verb per shot.** "Slides the can across the counter." Not "picks up
  and slides", which contains a reversal the model resolves in the wrong order.
- **Start the shot with the object already in contact.** The still should show
  the hand on the object, so the only thing left to render is the movement.
- **Name the destination, not the intention.** "The can moves to the far edge
  of the counter" beats "she offers him the coffee".
- **State what does not move.** The other hand, the umbrella, the till.
- **Never cut inside the action.** Duration is free here and cutting is not.
- **Expect to re-roll**, and treat a prop's continuity across the cut as a
  separate check — our umbrella changed shape between two shots.

---

## 7. What each reference line contributes

| Line | What it teaches us specifically | The rule it yields | The failure it prevents |
|---|---|---|---|
| **Stillness & duration** (Ozu, Kaurismäki, Akerman, Tsai) | Duration is performance; the people-free insert is structural; deadpan is a register | §2 — hold with intent, place inserts after beats, one caused motion | Dead air that looks like style; inserts used as filler |
| **Two-hander conversation** (Hong Sang-soo, Linklater, Kore-eda) | A scene of two people talking still needs a turn, and the turn lives in the listener | §1, §3 — beat/turn/button, protect the reaction, L-cut onto the listener | Exposition with faces |
| **Restraint & withheld gesture** (Wong Kar-wai, Lee Chang-dong) | Feeling is carried by what is withheld and by one recurring gesture | §5b, §5c — design the gesture arc; write the un-done action | "Looks wistful" prompts and generic expressions |
| **TV two-handers** (Better Call Saul, Mad Men) | Rigorous scene construction and coverage that makes a scene cuttable | §1, §3 — button the scene; shoot the minimum set every time | Scenes that cannot be cut because the coverage is missing |

---

## 8. Writing it into the pipeline

Port Music Studio's planner fields to drama and add a performance column, as
换班's shot design already does:

| Field | Drama meaning |
|---|---|
| `narrative_function` | This shot's job in the scene: setup / attempt / turn / reaction / button |
| `mood` | One or two words, and **varied across the scene** |
| `motion_amplitude` | micro / small / medium — micro is the default for dialogue |
| `motion_floor` | subtle / visible — a reaction shot is `subtle`; an action beat is `visible` |
| `camera_motion` | `locked` for everything until proven otherwise |
| `performance` | The one micro-event, in physical vocabulary (§5e) |
| `stillness` | What must not move |
| `entry_relation` | From the montage doc: `eyeline` / `screen_direction` / `graphic_match` / `spatial_bridge` |
| `gesture_state` | For the recurring gesture: full / reduced / suppressed |

Two gates worth adding to review, both already having codes: `facial_expression_drift`
for a face that changes expression when it should be holding, and
`action_not_followed` for §6.

---

## 9. What to measure on the reference scenes

When the reference files land, these are the numbers that turn this document
into targets — and the same script runs on our own output, so the comparison
is like for like:

1. **Shot-length distribution**, split by shot type (dialogue single, reaction,
   insert). Specifically: how long is a held insert allowed to be?
2. **Face height as a share of frame height** for dialogue versus reaction.
3. **Cut timing against speech onsets** — how far before the line ends does the
   cut to the listener happen?
4. **Reaction-shot duration** and how much of it is silent.
5. **Motion amplitude** (optical flow) inside a held shot — how still is
   "still" in a film we admire, versus in ours?
6. **Time-to-first-movement** in a held shot — how long does a face do nothing
   before the one event?

Open question the measurement should settle: our shots average 3–4 s because
that is what the audio needed. If the reference distribution says held shots
run 6–12 s, then our pacing is a rendering artefact rather than a choice.

---

## Cross-references

- `creative-mv-art-director/references/montage-and-mv-editing.md` — cutting,
  entry relations, shot-state ledger
- `creative-shortdrama` SKILL — the pipeline these rules are written for
- `{vault}/70_Media/references/scene-acting/` — reference scenes and their
  measurements
- `{vault}/10_Projects/YouTube-Music-Channel/library/patterns/` —
  `closed-lip-performance`, `adult-groove-on-beat`, `locked-off-camera`,
  `single-motion-nothing-else-moves`
- `{vault}/70_Media/Music/new-rock-album-2026-09/01-說得太急/mv-flow-20260920/scene-bible.md`
  — §3 表演約定, the diminishing gesture in practice
