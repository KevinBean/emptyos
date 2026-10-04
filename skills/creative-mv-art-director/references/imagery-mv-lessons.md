# Imagery-led MV: art-direction lessons

Read this when designing a medium-budget MV in which environment, objects and
camera carry the song instead of a protagonist's continuous performance.

Source: 不可說 / The Unsaid (2026-09-13 to 09-14, Google Flow, 31 shots over
173.44 s). Each rule below came from a review turn by Kevin that either
recurred or cost a regeneration. They are production judgements, not a style
preset; the next song still starts from its own lyric.

## 1. Check the lyric's argument before approving a beautiful world

The first pass built an attractive lakeside house and started animating it.
Kevin asked whether the scene design actually matched the lyric's meaning, and
the honest answer was "only partly". Approving a palette and a location is not
approving a narrative.

For each lyric group, write down:

- what the viewer sees first;
- what the next shot changes in that understanding;
- which relationship the cut embodies (reflection vs. the thing itself, a trace
  vs. its source, the pointing finger vs. the moon).

When a song is about misplaced attention, a mood board cannot answer that
question. Only a shot-to-shot relationship can.

## 2. Decide early whether a person is the subject — usually not

Character references were built and revised three times before Kevin said the
person was not the subject of this MV. The wardrobe "looked like a ghost"
(`不可说:reference:character-reference-v1:1`), the head was too large
(`不可说:still:U003-start-v2:1`), and the whole turnaround was redone.

Settle the subject hierarchy in the treatment before spending on character
references. When a person only supplies scale, keep them distant or
back-facing in two or three shots. Do not let sunk reference work increase
their screen time.

## 3. Design the camera before locking scenes to it

Fixed frames in which only water and curtains move read as a slideshow. Kevin's
instruction was to design the shots first rather than be constrained by the
scenes.

Write shot size, camera move, in-frame motion and cut rhythm per section, then
derive which references each move needs. On this film the chorus widened space
and movement, the second verse moved to tactile close-ups, and the outro slowed
to stillness.

## 4. Lived-in space, with emptiness kept in the composition

The first scene references were "too empty" — a zen showroom rather than a
place someone lives. Furnish the master scene (desk, lamp, cup, books, a coat,
plants) and keep negative space in the framing, not in the set dressing. Where
the lyric says the room empties, make that an arc across shots rather than the
starting state.

## 5. Time of day moves in one direction

Once the film turns to dawn, never cut back to night. Plan the transition lyric
(here 「天已經放晴」) and regenerate every later shot that still carries night
light. Grading could not convert those shots convincingly.

## 6. Avoid left–right ping-pong

Adjacent lateral moves in opposite directions read as an unmotivated reverse
shot. Keep adjacent motion vectors compatible. Change direction through a
locked-off shot, a vertical move, a push, or a detail insert.

## 7. Every insert needs a spatial reason

An empty alley at 0:43 made no sense to the viewer (`不可说:video:U008:1`). The replacement stayed in
the established courtyard and used one shared cause: a single gust carries
flowers → leaf shadows on the threshold → the doorway. Judge a repair by
watching the whole phrase with its neighbours, not the replaced seconds alone.

## 8. Text in the world must be complete and beautiful

Generated pages carry fake glyphs. Clean them from the still, then composite the
song's real lyrics onto the surface (Blender, planar-tracked). Kevin's
sequence of requests was: write the whole lyric, the page is too empty, it must
be beautiful. The result was a large Kai-style hand in phrase groups with
breathing room, consistent between the two-page spread and the close-up.

Surface text is a scene element and never replaces readable subtitles.

## 9. Delivery chrome belongs in the treatment

Decide these up front so they do not arrive as late requests:

- a title card over the opening image (fade in and out);
- at least one second of hold after the music ends;
- a small, full-length copyright watermark (`© <year> <artist>`) kept clear of
  the lyrics.

## 10. From 換班 (2026-09-14–15): what the director should catch in the treatment

- **Plan-B imagery with a small human presence works** when every person shot
  is calm, closed-lip and grounded; the environment still carries the song.
- **Interiors and exteriors of the same building must agree** — if the view
  from her window shows plain glass, the exterior cannot show security grilles.
  Decide this in the scene bible, not after the cut. (Fixed after the cut with a
  free still edit, `换班:still-edit:R1-no-grille:1`.)
- **Screens and prints come back blank.** Any monitor, phone or record sleeve
  that is in shot needs its content designed (non-legible UI, textless cover
  art) and planned as a post layer.
- **Text on a slanted surface is written along that surface**, in the
  perspective of the object, never as a flat rotated label.
- **Props that matter appear in the start frame of every shot they belong to**;
  a later shot cannot introduce them. A light prop the prompt says will move
  may leave and return mid-clip (`换班:video:S30:1`).
- **Light states need a cause on screen** (lights off one bar before a dark
  office), and a hanging object moves only when something moves it.
- **A transition that will not connect needs time, not force**: one bar of an
  empty establishing shot between arrival and action.


## Related

- `montage-and-mv-editing.md` — relationship-first editing vocabulary.
- `creative-mv-generator` → `references/imagery-mv-production-lessons.md` —
  budget, generation loop, ledger and delivery lessons from the same film.
- `tool-flow-stills` → *Image-to-video (Veo 3.1 Fast)* — what the start frame
  invites the model to animate.
- MV library `{vault}/10_Projects/YouTube-Music-Channel/library/` — the IDs in
  backticks above are its generation records; `patterns/` holds prompt
  patterns with pass/fail counts.
