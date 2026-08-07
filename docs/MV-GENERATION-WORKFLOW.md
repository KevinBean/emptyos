# MV Generation Workflow

This is the canonical production workflow for AI music videos in EmptyOS.
Music Studio is the execution engine. Claude Code's `creative-mv-generator`
skill must drive this workflow rather than maintain a parallel renderer.

## Source-audio contract

An MV run is synchronized to the existing, user-selected song. Music Studio
probes that audio for duration, lyrics, beats, and section boundaries, then
muxes the same source into the reviewed master. The separate Compose surface
may generate new ACE-Step music, but it is not an MV stage and must never
silently replace, remix, extend, or regenerate the source song.

## Production state machine

```text
source analysis
  -> song treatment
  -> art direction
  -> reference design
  -> character reference card + scene/look reference cards
  -> art/identity review and approval
  -> plan
  -> creative-basis validation
  -> plan-review            (pre-GPU: plan vs art-direction)
  -> prompt-check
  -> stills
  -> art-review
  -> still-review
  -> rough-cut (full timeline: reviewed still placeholders + approved clips)
  -> motion-proof
  -> manual: PAUSED for human approval
     auto: continue only after technical proof passes
  -> motion-audition (only for scenes that need recovery)
  -> clips
  -> assemble
  -> final review
  -> persist edit-source-quality.json
```

### Doc stage → code stage

The numbered stages below are the *contract*; twelve of them are `Stage(...)`
entries in `_MV_STAGES` (`apps/personal/music-studio/visual.py`) and four are
human/skill-side work that happens before or between runs. Keep this table
accurate when either side changes — without it a reader cannot get from
"stage 7" to a function.

| doc stage | `Stage()` id | where it runs |
|---|---|---|
| 0. Whole-song treatment and art direction | — | art-director skill, before the run |
| 1. Reference design | — | `POST /api/visual/reference*`, before the run |
| 2. Plan | — | `POST /api/visual/plan`; its scenes are the run's input |
| 2a. Creative-basis validation | `creative-basis` | pipeline |
| 2a2. Reference-pack validation | `reference-pack` | pipeline |
| 2b. Plan review (pre-GPU art gate) | `plan-review` | pipeline |
| 3. Prompt check | `prompt-check` | pipeline |
| 4. Stills | `stills` | pipeline (GPU-scoped) |
| 5. Art-direction review | `art-review` | pipeline (GPU-scoped) |
| 6. Technical still review | `still-review` | pipeline (GPU-scoped) |
| 7. Full-length rough cut | `rough-cut` | pipeline |
| 8. Motion proof | `motion-proof` | pipeline |
| 9. Approval | — | gate *between* stages, not a stage |
| 10. Motion audition (recovery only) | `motion-audition` | pipeline, skipped unless a scene needs it |
| 11. Full clips | `clips` | pipeline (GPU-scoped) |
| 12. Assemble and review | `assemble` | pipeline |

### Which stages actually stop a run

Six stages can fail a run; the rest transform or produce. Audited statically
2026-07-28 — the set is minimal, with no two gates checking the same class of
failure, and they are ordered cheap-first so a bad plan never reaches GPU time.

| stage | stops the run? | catches |
|---|---|---|
| `creative-basis` | yes | a plan missing its treatment-derived fields |
| `reference-pack` | yes (dark) | a broken or ungoverned production input (anchor, hashes, verdicts) |
| `plan-review` | yes (dark) | a plan that **contradicts its own art direction**, before any GPU spend |
| `prompt-check` | **no — repairs** | bad motion prompts are rewritten, not rejected |
| `art-review` | yes | a still that contradicts the art direction |
| `still-review` | yes | technical still defects (face, hands, artifacts) |
| `rough-cut` | no — produces | the full-length placeholder timeline |
| `motion-proof` | **only at 0/N** | wrong motion, *before* full fan-out |
| `motion-audition` | recovery only | runs only for scenes that already failed |
| `clips` | yes | per-clip quality + vision gates |
| `assemble` | yes | the final master |

Note `motion-proof` **escalates rather than terminates on a partial pass**
(changed 2026-08-01). When some representatives animate and some do not, the
stage persists `motion-proof-partial.json` (which scenes passed, which failed,
the clips that were built) and raises `PipelineReviewRequired`, so the run is
`paused` and resumable rather than `error`. Only a proof where *nothing*
animated raises. Two choices are legal at that pause — approve the partial
proof, or regenerate it; a bare resume is not offered because it would
re-render the same representatives and fail identically.

This matters because a partial proof is the most informative event in a run:
it names exactly which scenes cannot be animated, having already paid the GPU
to find out. Before the change that information was discarded, three of the
four video runs on disk died here (1/3 and 2/3 passed), the operator could not
reach the manual-approval path because it is only offered on a *paused* run,
and `motion-audition` — the one recovery stage — sits downstream and was
therefore unreachable by the failure it exists to recover from. It now reads
the failed scenes out of that artifact, so a scene is auditioned without
anyone having had to predict which scene would fail.

Approving a partial proof does **not** relax the fan-out boundary:
`motion_proof_fanout_authorized` still requires an explicit human approval or a
per-run `auto` authorization plus a *passed* proof, and `passed` stays False
for a partial. A partial proof can never quietly become a full render.

Note `prompt-check` is a **repair** stage despite the name: `ensure_video_prompts`
validates and rewrites. Do not add a rejection path there — a repairable prompt
should be repaired, and an unrepairable one surfaces at `motion-proof`.

Every `video` run persists `motion_proof_approval_mode`: `manual` (default) or
`auto`. Manual pauses for explicit human approval. Auto is an explicit
per-run user authorization to continue only after the same technical proof
passes; a failed proof still stops the run. Slideshow, cards, assets, and other
non-I2V modes do not use this gate.

Music Studio is a `Pipeline` consumer, so its run surfaces are governed by the
**run-transparency contract** in `.claude/rules/staged-pipeline.md`
("The UI contract: progress + settings + choices") — one status table per run
carrying persisted progress, the run's *persisted* settings, and only
state-legal choices. That rule is the single source; it is not restated here,
because it applies to every Pipeline consumer and not just to MV.

The one MV-specific obligation on top of it: the sticky render-progress card
must link to that exact run.

## Importing an externally-generated still

Some shots cannot be produced locally — FLUX will not hold partial-body human
framing (`.claude/rules/dev-gotchas.md`), so "only her forearms on the rail" or
"seen entirely from the back" comes back with the figure completed and a face in
shot. Those are sourced from a model that does posture and imported. This is the
same import path a director uses to replace any still.

**The binding procedure below is mandatory, and it exists because the failure it
prevents is silent.** An external batch is many near-identical images of one
character in one location. Identifying them by position — "the second image in
the chat", "the newest download" — binds the wrong file to a scene, and nothing
downstream can tell: the gate dutifully reviews whatever it was handed and its
complaint reads as a *content* failure. Measured 2026-08-01: selecting by DOM
index returned the same image three times because the page renders each one at
several sizes, and the byte counts were identical, which is the only reason it
was caught.

1. **Verify each image by eye against its scene description.** Not by index, not
   by filename, not by generation order. Open it and confirm it depicts *that*
   scene's subject, crop and light.
2. **Record a reference list** — scene number, file, **sha256**, and one line of
   what the image actually shows. Distinct hashes across the set is the cheap
   check that no image was bound twice.
3. **Import** through `POST /api/visual/render/resume` with `redo_stills: true`
   and `still_replacements: [{scene, path}]`.
4. **Assert the binding after import.** Every imported still is renamed
   `scene-NN-external-<sha12>.png`, so the storyboard's `still_path` carries the
   hash. Re-hash each file the gate judged and compare it to the reference list
   before believing any verdict. A content complaint about a scene whose still
   is the wrong file is not a content complaint.

An imported still is marked **director-supplied**: art review still judges it,
but the repair loop will not regenerate over it through the character adapter —
repair-over-import is what made 那道彩虹 unfinishable, because every judged
import passed while every scene sent to repair came back with the framing the
import existed to escape.

## Pre-production reference pack

Create and approve a song-specific reference pack before bulk storyboard
generation. This is a production input, not a repair added after identity or
style drift appears.

- `character.md` defines the protagonist in words.
- `character-reference.png` is the canonical identity image consumed by
  character-bearing still generation. Prefer one clear three-quarter portrait
  with readable face, hairline, age, and core wardrobe. A multi-view contact
  sheet may be kept for human review, but do not feed a small tiled sheet to a
  face adapter when a clean canonical portrait is available.
- `scene-references/` contains two to four approved look cards spanning the
  project's recurring environments, palette, light, material language, and
  subject scale. Environment cards should omit the protagonist unless they
  were generated from the approved character reference.
- `reference-pack.json` records each approved asset's source, prompt, provider,
  approval mode, and SHA-256. Changing an approved reference invalidates only
  downstream character/style-dependent stills and clips.

Candidate cards may be generated locally with FLUX or created through
ChatGPT's image-generation subscription and imported. EmptyOS cannot silently
spend or impersonate a ChatGPT subscription: subscription generation is an
agent/user action whose resulting file is imported into the same reference
pack. Automated cloud generation from Music Studio instead uses the separately
configured, consent-gated OpenAI image API. Regardless of source, approval
freezes ordinary image files and hashes; the downstream Music Studio workflow
is identical.

Manual mode pauses on the reference pack. Auto mode may let the independent
art/technical reviewers approve it only when the user explicitly selected auto
for that project. Reject face corruption, inconsistent identity across views,
extra/fused fingers, malformed limbs, wrong cultural direction, incoherent
palette, and scene cards that contradict the song's art direction. Do not begin
bulk still generation while a required reference remains missing or rejected.

## Stage contract

0. **Whole-song treatment and art direction**
   - Interpret the complete song before writing scenes. Read musical structure,
     section energy, and the emotional arc first; then use the complete lyrics
     as evidence of theme, voice, subtext, and turning points.
   - Persist `song-treatment.md` with the song's essence, emotional/musical
     acts, subtext, visual thesis, motif grammar, literalism guardrails, and
     storyboard mandate. This is not a scene list.
   - Derive `art-direction.md` from that treatment before storyboard planning.
     The old order—authoring art direction from already-written scenes or
     already-generated stills—can only rationalize the result after the fact
     and is not canonical.
   - Lyrics remain authoritative for timing and emotional meaning, but not for
     literal objects. Prefer metaphor, emotional echo, narrative progression,
     or purposeful counterpoint. Reserve literal illustration for a small
     number of signature anchors.

1. **Reference design**
   - Derive the character and visual-language contracts from the current song.
   - Generate several low-cost candidates with the selected provider, review
     them, promote only approved assets, and persist the reference manifest.
   - Character-bearing generation uses the canonical identity image through a
     configured identity-control workflow such as IP-Adapter. If that workflow
     fails, stop; never fall back to an unanchored random face.
   - `no_character` images must not receive a face reference.

2. **Plan**
   - Consume `song-treatment.md` and `art-direction.md` before solving any
     lyric window. Preserve the song structure, timings, protagonist, and
     visual treatment.
   - Design one film-level journey rather than a sequence of independent lyric
     illustrations. Every scene declares:
     - `narrative_function`: what changes in the whole-film journey;
     - `lyric_relationship`: `metaphor`, `emotional_echo`,
       `narrative_progression`, `counterpoint`, `literal_anchor`, or
       `instrumental`;
     - `visual_motif`: the recurring motif introduced or transformed, or an
       intentional empty reset.
   - Every scene must transform a motif, alter emotional pressure, advance a
     narrative state, or provide intentional contrast. A new location alone is
     not progression.
   - Persist each lyric-aware slot as exact `start_seconds` / `end_seconds`.
     These are edit-timeline anchors, not relative duration weights. Rendering
     may snap them to the nearest native frame but must not proportionally
     rescale them after still approval.
   - Every scene has two separate fields:
     - `description`: still-image composition, appearance, palette, and lighting.
     - `video_prompt`: temporal action only.
   - Every video scene also declares director intent:
     - `motion_pace`: `slow_motion`, `slow`, `natural`, or `fast`.
     - `motion_amplitude`: `micro`, `small`, `medium`, or `large`.
     - `camera_motion`: `locked`, `handheld_subtle`, `handheld_strong`, or
       `impact_shake`.
     - `motion_floor`: `subtle` when intentional motion is local or stillness
       is expressive; `visible` when the action must read across an ordinary
       playback-sized frame.
     These are editable art-direction choices, not quality scores. Audio energy
     and prompt wording may suggest defaults, but they never override an
     explicit director selection.
     Motion amplitude and motion coverage are different: a fast leaf or small
     gesture may travel substantially while occupying little of a wide frame.
     Post-render review judges the named action against `motion_floor`; it does
     not translate amplitude into an unrelated whole-frame pixel quota.
   - Mark a bounded one-off event with `directed_state_change: true`. When its
     slot exceeds one native generation, persist chronological
     `video_prompt_segments`: initiate once, continue the consequence without
     resetting/reversing, then settle.
   - A video prompt begins with the safe `Locked-off camera.` planning prefix
     and names at least two observable motions: one subject action and one
     environmental change. Runtime replaces the prefix only when
     `camera_motion` explicitly requests an intentional handheld/impact style.
   - Do not ask I2V to pan, zoom, dolly, crane, orbit, or invent off-frame space.

2a. **Creative-basis validation**
   - New plans carry `creative_basis_version`. Before prompt repair or GPU
     spend, persist `creative-basis.json` with hashes of the exact
     `song-treatment.md` and `art-direction.md` used by the run.
   - Reject a new plan missing its narrative function, lyric relationship, or
     motif field. Legacy plans without a creative-basis version remain
     resumable and are identified explicitly as legacy rather than silently
     pretending they passed this gate.

2a2. **Reference-pack validation**
   - Pure file I/O, no model call — the cheapest stage in the pipeline.
   - It exists because the reference pack decided more about a film's look than
     any other artifact (its identity image is conditioned into every
     character-bearing still) while being the *least* governed thing in the
     system: built by hand, outside any stage, with `reference-pack.json` read
     by no code at all.
   - Checks, when a manifest is present: every declared asset exists, its
     sha256 still matches (a same-path replacement is the exact failure the
     art-repair path already guards against), and every asset carries a review
     verdict. An anchor on disk that the manifest does not declare is a hard
     failure — that is an ungoverned input feeding the identity adapter.
   - Checks, always: **an identity anchor against a plan where no scene can
     show a face** (every scene `no_character` or `face_visible: false`) is a
     hard failure — it can only condition a face into shots whose contract
     forbids one.
   - A manifest may declare `"protagonist": false` — the state the schema
     could not previously express, so "this film has no protagonist" lived only
     as prose in `character.md` that no code read. Declaring it and shipping an
     anchor is a hard failure.
   - **No manifest is legacy, not failure.** Every song predating this gate has
     a pack and no manifest; failing them would break the catalogue to enforce
     a convention they were authored before. Legacy packs still get the
     plan-consistency checks, which are the ones that catch real art errors.
   - Calibrated on five real songs: 0 errors, 1 advisory warning, and one true
     positive (a manifest still declaring three assets that had been retired).
   - Known gap it reports but does not fail on: the generation path gates the
     identity anchor on `no_character` alone and **never consults
     `face_visible`**, so a body-trace shot still receives the anchor. The
     third occupancy state is honoured when *judging* a still and ignored when
     *conditioning* one. Fixing that changes clip fingerprints, so it is left
     to a session with no render in flight.
   - Dark behind `[apps.music-studio] feature.reference-pack-gate.enabled`.

2b. **Plan review — the pre-GPU art gate**
   - One text-only model call comparing `art-direction.md` with the scene plan.
     It answers exactly one question: does any scene *contradict* the contract?
   - This exists because the contract and the plan are authored separately, so
     a plan can silently violate a rule its own art direction states in
     writing. Before this stage the first content gate was `art-review`, which
     runs on generated stills — so a non-compliant plan was only caught after
     it had been paid for in images, and a still that faithfully renders a
     non-compliant scene can pass the still gates on its own terms.
   - Measured on `Porch-Light-Low` (2026-07-28): fed the original storyboard
     against a contract reading *"No face appears anywhere, at any scale"*, it
     flagged exactly the two face scenes with the clause they broke; fed the
     corrected storyboard it passed clean.
   - **Fails open** on a model or parse error. Unlike the still gates there is
     no artifact here whose correctness is in doubt, so an unavailable reviewer
     must not block a render the way a genuine contract violation does.
   - The reviewer sees a deliberately narrow projection of each scene
     (`plan_review_payload`) — composition and occupancy intent only, never
     seeds, checkpoints or edit permissions. `face_visible` is omitted entirely
     on `no_character` scenes: the field is undefined when nobody is in shot,
     and emitting its default told the reviewer a face was visible in an empty
     porch still (six false violations on a clean 3-scene plan).
   - Violations are written to the cross-song art ledger with the contract
     clause each one broke.
   - Dark behind `[apps.music-studio] feature.plan-review.enabled`.

3. **Prompt check**
   - Validate all motion prompts before generating stills.
   - Repair missing, legacy, weak, or static prompts in one batched model call.
   - Persist the repaired scenes. The storyboard, proof, resumed full render,
     and final history all use the same repaired prompts.

4. **Stills**
   - Use one character contract, one style contract, and a reproducible seed
     ladder for the whole MV.
   - Load the approved `character-reference.png` once as the project's identity
     anchor. Record its path and SHA-256 in every character-bearing generation
     result so a resumed run can audit exactly which face was conditioned.
   - A scene may opt out of the protagonist with `no_character: true`.
   - Save the storyboard so later stages can resume without regenerating images.
   - Character scenes add generation-side negatives for extra/fused/missing
     fingers, malformed hands, duplicate faces/people, and extra/missing limbs.
     `no_character` scenes forbid people and animals at the still stage too.

5. **Art-direction review**
   - Every song owns an `art-direction.md`. If none exists, Music Studio
     authors one from that song's lyrics, scenes, character, and style
     contracts; it never applies one fixed aesthetic to every MV.
   - An independent vision pass scores composition, emotional fit, cultural
     fit, style consistency, and subject scale against that song-specific
     contract. A close-up may pass one MV and fail another.
   - Clear mismatches regenerate only that scene with a concise positive art
     correction. Persist `art-review.json` and the reviewed storyboard.
     Reviewer outages and unresolved failures stop before technical QA or I2V.
   - Treat each generated or imported still as a versioned source revision,
     never as a stable pathname. An art-repair candidate records the SHA-256
     of both its own bytes and the source still from which it was derived,
     plus a contract fingerprint covering the scene description, still
     negative prompt, character occupancy, song art direction, character,
     character-reference bytes, style contract, style, and preset. Automatic
     recovery and explicit director selection require all three fingerprints
     to match the current run. A same-path file replacement or a hashless
     legacy candidate is re-reviewed; it cannot inherit an old selection.
   - When this gate stops, the run preview exposes every preserved attempt as
     a per-scene contact sheet with verdict, repair note, prompt, provider,
     preset, seed, and local cost. The original is comparison-only. The
     director may select only a repair whose current bytes still match its
     versioned record; resume rechecks the source-still and scene-contract
     fingerprints before the selection can bypass another model verdict.
   - A targeted still rewind clears art selections and replacement markers for
     the affected scenes before generation. Fresh source revisions never
     recover candidates derived from the previous revision, even when their
     scene number and attempt number match.

6. **Technical still review**
   - Vision-review every reference still before spending on I2V. Inspect visible
     hands, fingers, limbs, faces, subject count, and gross structural errors.
   - Compare the reviewed still with its temporal contract before I2V. A
     concrete object required to move must already exist in the still, while a
     directed one-off event must not already be visibly underway or completed
     in the starting frame. Regenerate only that scene when the still lacks a
     motion prerequisite or pre-bakes the action state.
   - Count fingers only when the hand is clear enough to judge. Occlusion,
     cropping, perspective, or a fist is not evidence of missing fingers.
   - Derive the gate from observed facts, not the reviewer's verdict label. For
     example, `visible_people: 0` and `visible_animals: 0` cannot simultaneously
     support `unexpected_character`; discard that contradictory label.
   - Clearly visible extra/fused/missing fingers, malformed hands, duplicated or
     disconnected limbs, duplicate faces/people, severe facial corruption, and
     a character in a `no_character` scene are hard failures.
   - Regenerate only failed scenes with a new seed, a compact positive repair
     hint, and scene-specific negatives; review the replacement again. Preserve
     rejected originals and persist the verdict trail in `still-review.json`.
   - For `no_character` scenes, strip character-bearing words and clauses from
     the positive style treatment before image generation; negative prompts
     cannot reliably cancel a song-wide positive instruction about a person.
     Directors may add a per-scene `still_negative_prompt` for composition
     exclusions such as a path, corridor, or vanishing point. Keep this
     scene-specific instead of imposing one composition on every MV.
   - A human art-direction rejection (for example, the protagonist reads as
     culturally wrong for the song) updates the shared character contract and
     rewinds to still generation. Regenerate only the affected character
     scenes, reuse approved no-character stills, then run still review across
     the rebuilt storyboard before any I2V resumes.
   - The resume API treats edits to `description`, `still_negative_prompt`, or
     `no_character` as still-affecting. If stills already exist, it
     automatically merges those scene numbers into a targeted still rewind;
     callers do not need to remember `redo_stills`. Motion-only edits keep the
     approved stills and invalidate only their downstream motion fingerprints.
  - If the vision reviewer is unavailable or a defect remains uncertain after
     the bounded repair attempt, fail the resumable stage rather than silently
     sending the image to I2V.

7. **Full-length rough cut**
   - As soon as every still passes review, build a complete, playable master
     from the production lyric-first EDL. This is the first version of the
     film, not a disconnected slideshow export.
   - Each authored scene owns one replaceable source slot. Before motion
     exists, the slot points directly at its reviewed high-resolution still;
     the frame-native assembler holds it for the exact delivery frame count.
     Do not generate Ken Burns movement and do not import old or loose video.
   - A video can replace a still slot only when the current production clip
     builder returned it after all scene QA and checkpoint checks. Motion-proof
     candidates, failed clips, legacy low-resolution assets, and files without
     current-run approval provenance never enter automatically.
   - Persist `rough-cut.json`, a source manifest, the frame-native EDL, edit
     statistics, and a versioned `rough-cut-rNNN.mp4`. The manifest must state
     which scenes remain placeholders and which contain approved motion.
   - When full clip fan-out is partial, refresh the same timeline once with all
     clips that did pass, then report the missing scenes honestly. A retry
     reuses their checkpoints and creates the next rough-cut revision. The
     initial full cut remains playable even if the refresh itself fails.
   - When every scene passes and final assembly succeeds, point the same
     rough-cut lineage at the final master: zero placeholders, all scenes
     approved. Do not create a second pipeline for this progression.

8. **Motion proof**
   - Select three representative scenes: the longest character shot, the
     longest insert/no-character shot, and the hardest remaining shot.
   - Render one native I2V generation for each using the exact production clip
     builder—same upload, prompt, model preset, frame grid, review, retry,
     normalization, and concat path used by the full MV. If any selected scene
     is longer than one native generation, render one representative at its
     real scene duration so the proof also exercises last-frame hand-off and
     multi-generation continuity. By default, prefer a `no_character` insert
     for this long-chain proof so chain mechanics and face/identity stability
     are diagnosed independently. Keep the character representative short to
     test face and identity preservation. A run may explicitly select another
     chain scene when the director needs a particular hand-off tested.
   - Derive a motion budget from each generation's actual frame count and native
     fps, the complete scene duration, and that scene's declared pace/amplitude.
     A short native segment shows only its proportional share of a longer
     scene's action. Later chained segments decay again so motion cannot
     escalate across last-frame hand-offs.
   - Compare sampled video frames with both the approved still and the scene
     contract. Reject not only character/anatomy drift but also clear
     environment geometry changes, invented objects, and semantic scene-content
     drift. Allow the requested natural evolution of smoke, flame, water,
     fabric, foliage, and reflections; the gate targets conspicuous unrequested
     transformations, not ordinary motion.
   - Compile the validated scene into concise Wan-native conditioning:
     camera phrase + visible action + environment action + one short
     pace/amplitude phrase. Do not send numeric thresholds, duration
     calculations, or the QA contract to the diffusion model.
   - Use a per-scene `video_negative_prompt` when the director must exclude a
     specific unprompted transformation or object (for example a large flame
     in a dying-ember shot). Keep the exclusion local to that scene and retain
     the shared anatomy, occupancy, pace, and camera negatives.
   - Concatenate them as `motion-proof.mp4`. Pause in `manual` mode; in `auto`
     mode continue only when all proof clips passed the technical gates.
   - If a selected scene already has a complete run-scoped director-approved
     production chain, review that exact chain at the scene's real delivery
     duration. Do not compress a full 10-12 second candidate into the ordinary
     five-second proof window: that changes its authored speed and tests an
     artificial retime rather than the approved source. Prefix-only approvals
     remain ordinary proof inputs because they do not cover the whole scene.

9. **Approval**
   - Play the proof in Music Studio.
   - `manual` mode requires explicit approval. A generic resume is rejected.
   - `auto` mode must have been explicitly selected for that run before the
     proof; it is not inferred by an agent. It authorizes fan-out only when the
     proof artifact records `passed: true`.
   - If the user switches a failed run to `auto`, persist the new per-run mode.
     A successful resumed proof continues to full fan-out instead of retaining
     the previous manual pause.
   - If rejected, edit the motion prompts and make another proof. Do not hide a
     failed animation with Ken Burns or approve it merely because the frame
     technically changes.
   - If the complete measured retry ladder still cannot make the intended
     action visible, treat the reference composition as the fault. Revise that
     scene's description, regenerate only its still, and run the replacement
     through art/anatomy review before proving motion again.

10. **Motion audition (recovery only)**
   - Do not add audition cost to every healthy scene. A scene enters this stage
     when a prior production attempt failed or the director explicitly asks to
     compare motion/model candidates.
   - Before queueing ComfyUI, look for an exact passed full-production
     checkpoint for that scene. It may skip the short audition only when its
     recorded seed reconstructs the same reference-image hash, prompt, motion
     profile, delivery duration, mode, and model preset fingerprint. A complete
     production review is stronger evidence than a two-second recovery screen:
     reuse its seed and clip instead of allowing an inconclusive short-static
     result to invalidate it. Any changed fingerprint still requires audition
     or regeneration.
   - Render two to four short candidates through Music Studio, never a scratch
     side pipeline. Record the exact model workflow, prompt, negative prompt,
     seed, frame geometry, motion metrics, corruption metrics, failure
     signature, candidate path, and the submitted `*.workflow-api.json`
     snapshot in `motion-audition.json`. API submissions do not populate the
     browser's current ComfyUI graph, so this sidecar is the durable audit of
     the exact substituted node payload.
   - Stay inside a spatial resolution already validated for the active model.
     Wan 2.2 at 512x288 produced broad colour bands and frame tearing in live
     testing, so its audition path keeps the validated 1024x576 geometry and
     saves compute by shortening temporal length.
   - Use the persisted `motion_audition_tier`, never a one-off frame override.
     The default `screen` tier is about 1.4 seconds for fast motion, 2 seconds
     otherwise, and 3 seconds for `micro`; it rejects bad seeds, immediate
     action misunderstanding, and gross corruption. Use `extended` only after
     a static or pace-specific failure makes the short screen inconclusive:
     about 2 seconds fast, 3 seconds natural/slow, and 4 seconds `micro`.
     Neither tier approves long-tail stability.
   - Reject full-height/full-width colour bands, frame tearing, block-shaped
     foreign image fragments, and widespread unprompted chroma corruption.
     These artifacts may contain plenty of pixel motion and can fool ordinary
     freeze/flow checks, so compare sampled edge structure with the approved
     reference still.
   - Promote the best technically valid seed, not the candidate's approval.
     Resolution/length changes alter diffusion behavior, so the production
     render must independently pass the complete container, motion, identity,
     speed, corruption, and seam gates.
   - Slow actions, later identity drift, frozen tails, completed-event
     repetition, and chained hand-offs cannot be proven by a short audition.
     They remain production/final-review responsibilities.

11. **Full clips**
   - Resume the same run, skipping completed prompt, still, and proof stages.
   - If clips were cancelled/partial or assembly failed, use the dedicated
     `redo_clips` rewind. Preserve the passed motion proof, discard only clips
     and downstream assembly state, and reuse every still-valid per-scene or
     per-segment checkpoint.
   - Generate at the configured model's native frame rate and legal frame grid.
     For the current Wan standard preset this is 24 fps at 1024x576 generation,
     normalized to 1280x720 delivery.
   - Chain native generations for long scenes rather than stretching a short
     clip into low-motion playback.
   - Treat a long scene as a sequence of action phases, not repeated copies of
     one prompt. Generic later segments advance the next visible phase without
     replaying a completed event. For long one-off actions, directors must
     provide `video_prompt_segments`, one prompt per native segment; pace, amplitude,
     occupancy, camera, and negative constraints still apply to every phase.
   - A chain is complete only when every planned native segment rendered.
     Never stretch an arbitrary partial chain across the scene slot. The sole
     recovery exception is a failed tail after at least two independently
     passed segments already cover enough delivery for the ordinary
     scene-aware retime gate to accept the ratio: no directed state change may
     be missing, and the concatenated prefix, retimed whole clip, and seams
     must all pass review. Do not add a second fixed coverage threshold that
     contradicts the retime policy. Measure each exact last-frame/first-frame
     hand-off before the joined clip can pass. A last-frame-conditioned
     continuation repeats the conditioning image as its decoded frame zero;
     canonical chain assembly drops exactly that one overlap frame and rebuilds
     PTS on the native frame clock. The seam gate then checks both spatial
     continuity and temporal continuity: reject an abrupt image jump, a
     one-frame velocity collapse, or a material pre/post optical-flow speed
     discontinuity. A small pixel delta alone does not prove a natural join.
   - Review each native segment before handing its last frame to the next.
     Container/motion checks are necessary but not sufficient: compare sampled
     generated frames with the approved reference still using the vision gate.
     Visible face deformation, identity drift, changed head structure,
     duplicate people/limbs, or a subject entering a `no_character` shot stops
     the chain immediately and never creates a reusable segment checkpoint.
     Count complete people and separately record partial human/animal anatomy
     in every sampled frame. A cropped hand, arm, leg, face/animal fragment, or
     silhouette still violates `no_character` even when the complete-person
     count is zero. Also record concrete foreign intrusions (rod, tool, falling
     object) relative to the approved still and scene contract. Discard a
     contradictory `unexpected_character` label only when both complete-person
     counts and partial-part observations are empty.
     For character scenes, compare video occupancy with the approved still:
     one visible person in both cannot support `unexpected_character`, while a
     count increase remains a hard failure.
   - Sample enough chronological frames to inspect the action, not only identity
     at the endpoints. Reject a completed action that resets/repeats only when
     the scene declares a bounded `directed_state_change`; continuous leaves,
     water, fog, fabric, or similar environmental motion may naturally recur.
     Reject an unrequested reversal, implausible stop-start motion,
     facial-expression drift, or gesture anatomy that deforms during movement.
   - Keep the profile-aware duration budget as structured runtime metadata and
     logging, not prompt prose. The model receives only concise natural-language
     motion direction; the system enforces numeric travel/speed/camera limits
     after rendering.
   - Stabilize sampled frames and measure residual local motion and optical-flow
     speed. Reject static output, whole-frame drift, excessive global
     instability, local deformation, and speed that contradicts the declared
     profile. A turn is not a failure merely because direction changes.
   - Duration-aware camera budgets are authoritative over legacy fixed-pixel
     drift heuristics. A slow cumulative displacement that stays inside the
     scene's declared global-shift and peak-speed envelope is not camera shake;
     a fixed threshold must not reject it first. This does not authorize drift
     beyond the structured budget.
   - A camera-drift rejection requires coherent displacement across several
     spatially separated background regions. Do not let a moving foreground
     figure or garment dominate one affine estimate and masquerade as camera
     movement. Sparse hair/fabric/water flow uses a perceptual speed floor;
     only fast motion over substantial frame area becomes a hard speed failure.
   - Preserve scene occupancy. A `no_character: true` shot receives a
     scene-specific negative prompt forbidding people, animals, faces, bodies,
     or silhouettes. Character shots forbid duplicate/new subjects. Human proof
     review treats any subject appearing from nowhere as a hard rejection.
   - Use the explicit `video_strategy: layered-atmosphere` branch when the
     approved composition requires a spatially locked background while cloud,
     fog, mist, smoke, or similar foreground atmosphere evolves independently.
     The locked reviewed plate may contain an already-approved character or
     distant human trace; the generated atmosphere layer itself remains a
     strict `no_character` source, and the final composite is re-reviewed
     against the original scene's occupancy and identity contract.
     Generate the sparse monochrome atmosphere reference on pure black with
     the procedural Blender source first, normalize it, and checkpoint its
     isolation contract before animation. Its structural provenance is the
     semantic proof: the scene graph contains only a black world and one
     noise-emission plane, so it cannot invent a landscape, person, object, or
     graphic ribbon. If Blender is unavailable or fails isolation, fall back to
     the image model and its independent semantic still review. Black-pixel
     coverage alone is insufficient for an image-model fallback: any hidden
     landscape, mountain, valley, horizon, architecture, ground, water,
     vegetation, person, animal, tool, text, or other scene content rejects the
     layer. Describe only cloud/fog/mist in the layer prompt; its spatial
     relationship to the finished background belongs to the compositor.
     Animate and vision-review the isolated layer separately, then composite
     it over the locked plate. Use screen blending on dark plates; on a bright
     plate, where screen blending would erase the mist's motion contrast, use
     the layer luminance as a softened alpha mask and tint the veil from the
     plate's own colour. The
     layer must curl, thin, dissipate, and change opacity; translating one
     static PNG is not valid motion. Sample the isolated layer across its full
     duration and require readable mist structure throughout, including the
     exact final frame: checking only the hand-off misses a clip that stays
     black for most of its duration and reappears at the end. Reject a segment
     that has fully dissipated to black, because a start-image-only
     continuation then has nothing left to animate. Fade the layer in at a
     continuation seam, measure the final
     composite again, and preserve any approved prefix by using its final frame
     as the locked plate. Do not repurpose depth-parallax for this case because
     it moves background geometry.
   - Persist a passed sidecar checkpoint per delivery clip. Reuse it after a
     daemon restart only when the reference-image hash, motion prompt, declared
     profile, scene duration, mode, and model preset all match. Any changed
     input invalidates only that scene and regenerates it.
     New optional strategies must preserve the historical default/native
     fingerprint: only an explicitly selected non-default strategy and its
     strategy-specific inputs may alter the key. Adding a default-valued field
     must never invalidate every accepted clip in an older resumed run.
   - Human proof rejection invalidates the rejected scene's delivery and
     segment checkpoints. The regenerate API may target scene numbers, so one
     bad face does not force unrelated proof shots to render again.
   - Long scenes also checkpoint each completed native segment with a content
     hash. A retry or restart reuses the verified prefix and resumes at the
     first missing segment. Segment checkpoints are cleared only after the
     complete joined scene passes review and its delivery checkpoint is saved.
     Layered-atmosphere attempts inherit the same retry ladder's effective pace
     and amplitude; a static retry must not silently render again at the
     authored amplitude after the ladder has stepped it upward. A disappearing
     or implausibly moving atmosphere layer steps amplitude downward. Animate
     the layer with the installed Blender procedural-atmosphere renderer first:
     separate irregular noise-driven wisps on black, monotonic non-looping 4D
     evolution scaled by the scene's declared pace and amplitude, and no
     dependency on image-model geometry. Run the same persistence, speed, and
     final-composite motion gates on Blender's output; use structural
     provenance instead of paying a cloud reviewer to rediscover what the
     renderer graph can contain. If Blender is unavailable or fails, try the
     AI I2V layer and its ordinary identity/semantic review. If that also
     fails, animate the already-reviewed mist
     plate with deterministic local displacement fields and local opacity
     evolution, then run the gates again. Neither recovery may pan, zoom, or
     translate the whole still; each must produce measured independent motion
     and remain visible for the full scene. Reject a reference before motion
     generation when it is a single dominant ribbon, regular sine/S-curve,
     calligraphic stroke, snake-like line, or repeated parallel-band pattern.
     Those shapes predictably become graphic loops rather than natural mist.
     The provider fallback must exist at both the reference and motion stages;
     it must not become the default while the deterministic renderer is
     healthy. Stop image-model reference generation after two semantic
     failures; a third expensive image from the same failing contract is not
     useful.
     Review a layered-atmosphere delivery with the localized
     `subtle` motion floor because most of the approved plate is intentionally
     locked. This is not a static-image waiver: persistent residual coverage,
     local flow, and a real local peak must still prove motion. A nearly
     invisible composite continues to fail.
   - Allow up to three total attempts with new seeds, compact corrective cues,
     and a remembered one-tier amplitude adjustment after each measured
     failure. Consecutive static outputs climb `small -> medium -> large` while
     keeping subject placement, camera intent, and the director's pace
     unchanged; distortion steps amplitude back down. Identity-only failures
     keep the current motion profile. Never restart the ladder at the authored
     amplitude, repeat a long policy prompt, or reinterpret a slow scene as a
     fast one.
   - Classify each failure before retrying. Two consecutive `static`,
     `semantic-drift`, or `frame-corruption` results indicate a conditioning or
     model limit; stop same-model retries. Preserve the lyric/narrative intent,
     then choose the first validated recovery that fits:
     1. re-stage the intent as a simpler model-friendly shot (cutaway,
        silhouette, occlusion cut, detail, or environmental reaction);
     2. for an essential directed state change, generate and independently
        review consistent start/mid/end keyframes, then render short spans only
        through a workflow proven to condition on both ends;
     3. route through a validated alternate model or control workflow.
     A start-image-only workflow, unrelated stills joined by crossfades, or an
     installed-but-untested node does not count as keyframe control. Camera
     shake and similar stochastic failures may still use the bounded seed
     ladder.
   - Minimum motion is an artistic floor, not an absolute demand for spectacle.
     A director-approved contemplative shot may persist
     `motion_floor: subtle`; this suppresses only the measured
     no-independent-motion rejection. It never suppresses whole-frame drift,
     shake, deformation, corruption, occupancy/identity, or seam gates. Save
     the exact approved candidate hash, workflow snapshot, and remaining gate
     obligations so an approval cannot silently transfer to a different clip.
   - Whole-frame freeze coverage is reconciled with stabilized local optical
     flow. The global `frozen` label is removed only when independent motion
     metrics pass the authored floor; black frames, corruption, identity
     drift, camera instability, and chain-seam failures remain hard failures.
   - Video mode never substitutes a slideshow when I2V fails.
   - Keep `delivery_duration` (the exact song/beat window) separate from
     `capture_duration` (the source coverage generated for editing). Budget a
     bounded 0.5-1.0 second or 10-15% tail handle when the shot/model risk
     justifies it. Prefer one continuous clean action window. Time-stretch is a
     valid fallback only after recording the ratio and validating the retimed
     result against authored pace, unique-frame rate, freeze/repetition,
     instantaneous motion, and action physics. Impacts, gait, lip sync, and
     one-off state changes use tighter limits than smoke, water, cloud, or
     foliage. Never stretch merely because surplus footage exists.
   - A corruption or unprompted object confined to a late handle may be
     trimmed. If it enters the required action window, salvage only when the
     first bad frame is located, a safety margin leaves at least 1.5 seconds
     and 60% clean, the retained prefix is snapped to the model's frame grid,
     the discarded frames are regenerated from the last clean frame or a
     bounded retime is explicitly judged suitable, and the trimmed prefix,
     new tail/retimed result, and seam all pass review. Record both retained
     and discarded frame ranges plus any retime ratio. Do not salvage a bad
     reference frame or a defect present from the start.
   - Full fan-out must produce one accepted clip for every planned scene.
     Refuse to concatenate or assemble a shortened MV when any scene is
     missing.

12. **Assemble and review**
   - Treat per-clip review as the content authority. Assembly verifies that the
     selected source manifest is complete and that every current file still
     matches the exact reviewed SHA-256; it does not rerun a generic motion,
     pace, or aesthetic threshold over clips that already passed their
     scene-specific review. A byte or provenance mismatch rewinds to clip
     review instead of letting assembly regenerate, replace, or reinterpret
     the scene.
   - Normalize every accepted clip to one exact delivery geometry and native
     frame rate before concatenation.
   - Duration fitting outputs exactly `round(delivery_duration * native_fps)`
     frames at constant native fps. Prefer selecting from generated tail-handle
     coverage. A reviewed short-source recovery may use bounded
     motion-compensated interpolation, but plain `setpts` output with an
     arbitrary nominal frame rate is invalid. Re-run container, repetition,
     motion, and projected chain-seam gates on the fitted result.
   - Assemble from frame ranges (`start_frame`, exclusive `end_frame`) on the
     native 24 fps grid, ignoring inherited stretched source timestamps. The
     edit list itself must carry the lyric-aligned boundaries and every
     retained/discarded source range.
   - Concatenate, mux the song, burn approved subtitles, and save the run.
   - Review the final master for duration, audio, freeze/static sections,
     identity, scene continuity, and obvious generation artifacts.
   - Record final-cut feedback in Music Studio, not in an unversioned chat
     message. Each review session binds the exact master SHA-256 and production
     EDL SHA-256, and each issue binds a half-open destination-frame range to
     every overlapping EDL shot, authored scene, source index, source-frame
     range, and selected source hash. If either the master or EDL changes while
     the reviewer is watching, reject the submission and reload the cut.
   - Classify each issue by both symptom and requested action. Symptoms include
     frozen motion, speed mismatch, transition/continuity, visual anomaly,
     style drift, narrative mismatch, repetition, missing atmosphere, and
     subtitle/audio. Actions are deliberately fewer: regenerate the visual,
     regenerate motion, perform manual post-production, or retain a note.
     Free text alone is not an executable iteration contract.
   - Saving a review does not spend GPU. “Start revision” is a separate,
     explicit action that compiles the saved issues into the earliest safe
     existing rewind: visual replacements target `stills`; motion corrections
     target `motion-proof`; a mixed review starts at `stills` and reuses every
     unaffected approved still. The issue text is appended to the affected
     scene's still or motion direction, so downstream generation receives the
     human correction rather than merely recording it in a side log.
   - Persist immutable sessions in `final-review.json`. A session records its
     review decision, bound issues, derived iteration plan, and the job that
     started the next revision. Approving a master is an issue-free session.
     A review can start at most one iteration; the new master receives a new
     review session instead of overwriting history.

### Lyric-first edit contract

For a song with timed lyrics, assembly is an editorial stage rather than a
direct scene concat. Music Studio remains the sole production path: the
existing `clips` stage builds and persists a frame-native edit-decision list,
then the existing `assemble` stage muxes the approved song and subtitles. The
offline recut script is an acceptance/repair tool only.

Cut priority is:

1. protected authored scene boundaries;
2. real SRT lyric-line onsets that preserve the minimum shot;
3. musical section boundaries;
4. grouped detected-beat positions for lyricless intervals only.

Cut rate is **derived from the song, then scaled by the director**, because a
fixed second-count cannot fit two songs. Music Studio measures the phrase
floor — the longer of one bar and the median gap between sung lines — and
`song-treatment.md` declares a pace band that multiplies it:

| pace | multiplier | for |
|---|---|---|
| `held` | 1.5x | contemplative, ambient, sparse |
| `flowing` | 1.0x | narrative, mid-energy |
| `driving` | 0.75x | propulsive, dense |

Shots are then whole phrases per section (intro/outro 2, everything else 1),
and a chorus may be offered a half-phrase grid — but a sub-phrase shot only
survives where a motif genuinely transforms, because every boundary that finds
no legal motif return is merged back into the held shot it actually was.

On `梦幻泡影` the measured floor is 5.48s (its sung lines) against a 1.48s bar,
so `held` yields ~8.2s shots and `driving` ~4.1s. The same table on a 140 BPM
pop song with 2.1s lines yields ~3s chorus shots with no per-song tuning. The
fixed `LYRIC_FIRST_*` ladder (3.5s minimum; 8/6/4 beat groups) remains the
fallback when a song has no treatment.

Cutting this song at a charting-pop 3.5s chorus produced 49 pieces that chopped
sung lines and alternated between images with no shared visual language — the
reference class matters more than the number.

These values are a production default, not a universal aesthetic law. A
director may tune them per song, but the following invariants do not change:

- If a lyric-bearing scene has no safe onset after the minimum-shot gate, hold
  the shot. Never insert a decorative mid-line beat cut merely to add motion.
- Primary source windows always advance on the decoded-frame timeline. Never
  rewind or replay a completed action.
- `edit_motif_allowed` defaults false. Reusing an earlier source requires an
  explicit per-scene semantic decision that its exact content can recur under
  the later lyric; technical QA alone cannot authorize a motif.
- A return must share the host's `visual_motif` family **and** sit at or before
  it on `motif_state` (opening → transformed → final). Permission alone is not
  enough: authorizing every character-free scene by `no_character` produced 22
  returns of which one was coherent, including an intro pond-mist plate cutting
  into the song's release beat. No shared family, or no state-legal candidate,
  means no return — hold the primary.
- `edit_reframe_allowed` defaults false. When explicitly approved, alternate
  only a restrained centered 6% punch-in on an internal primary cut. It must
  not crop an essential face, hand, prop, edge composition, or text.
- Preserve native model fps and exact integer frame counts. Do not globally
  stretch or retime the master to manufacture lyric sync.
- Persist both `edit-decision-list.json` and `edit-stats.json` in the run and
  delivery folder. Frames are truth; seconds are derived display values.
- Editing cannot invent missing source coverage. New renders should normally
  capture about 1.10–1.15x on risky shots through bounded tail handles; a whole
  MV that needs more editorial alternatives should budget roughly 1.3–1.5x
  aggregate usable coverage rather than repeating defective or mismatched
  material.
- Metrics do not ship a master. The final visual review still rejects lyric
  mismatch, discontinuity, accidental repetition, frozen motion, bad crops,
  and generation artifacts.
- A visual rejection is source state, not a note attached only to one master.
  Final review must persist `edit-source-quality.json` through
  `PUT /api/visual/render/source-quality/{run_id}`. The preview API returns the
  same manifest so the UI, a resumed run, and an external editor see one fact.
- Every decision binds `scene`, exact full-file `sha256`, decoded `frames`,
  `source_kind`, and `disposition` (`approved`, `limited`, or `rejected`).
  A limited source declares one half-open approved frame range. Replacing bytes
  at the same pathname invalidates the decision; approval never transfers by
  filename.
- A complete manifest selects exactly one approved source per authored scene.
  Rejected sources cannot be primary shots or motif returns. A selected
  replacement is explicit (for example a deterministic move derived from a
  reviewed still), carries its own hash, and may separately deny motif reuse.
- Production assembly, evolving rough cuts, and the bounded offline recut all
  resolve this contract before building an EDL. The offline recut refuses to
  run without a complete manifest. No downstream tool may recover a rejected
  clip merely because `clip-NN.mp4` still exists.

The production branch is introduced behind
`[apps.music-studio] feature.lyric-edit.enabled` with a dark default. While it
is false, the legacy direct-concat path is unchanged. After one complete
production run passes EDL inspection and final visual review, the flag may be
enabled for normal use; the edit remains part of the existing staged pipeline,
not a second workflow.

## Operating paths

- **Music Studio UI/API:** normal production path and source of run state,
  proof playback, approval, resume, and final artifacts.
- **Claude Code skill:** plans, inspects, diagnoses, and drives the Music Studio
  run. It may use scratch scripts only for bounded diagnostics or repair, never
  as a second default production pipeline.
- **Codex skill:** follows this same stage and edit contract. Harness-specific
  wording may differ, but cut priority, approvals, persisted artifacts, and
  Music Studio run-state authority must not drift.
- **ComfyUI:** model executor behind Music Studio. A queued prompt is not proof
  of useful animation; only the post-render motion gate and human proof review
  establish that.
- **ComfyUI runtime preflight:** before any image, video, depth, or ACE-Step
  workflow is queued, compare the server-reported core version with the
  configured minimum and compare every reported companion package's
  `installed` and `required` versions. A reachable port with a core-only
  checkout, malformed package metadata, or mixed dependency stack is not a
  healthy executor. Refuse the render before spending GPU time. Servers too old
  to expose package telemetry remain compatible unless an explicit
  `minimum_version` is configured.

## Closing the loop — memory and friction

A 2026-07-28 audit scored this pipeline against the six loop stages in
`emptyos/sdk/loops.py` and found `gate: strong`, `revert: strong`,
`memory: absent`, `friction: absent`. It caught faults *within* a run and
forgot every one of them *between* runs, so each song started from zero and the
same art-direction mistake was free to recur across the catalogue. (The
`face_visible: false` lesson from 那道彩虹 survived only as a code comment and
therefore never reached the next author, who re-derived it in prose two songs
later.)

Three mechanisms close it. All are registered in `loops.py` as `mv-production`,
`mv-plan-review` and `mv-art-ledger`.

- **`emptyos/sdk/art_ledger.py` — cross-song memory.** One JSONL row per art
  verdict under `data/art_direction/`, each carrying the *contract clause* it
  violated, not just a code. A bare `subject_scale_mismatch` says a rule broke
  but not which one, so it cannot teach the next run anything.

  Three defects found 2026-07-31 meant it recorded rows without teaching
  anything, and all three are fixed — the lesson is that a *registered* loop
  stage is not an *earning* one, and only the data tells you which:

  1. **219 of 225 rows carried an empty `contract_clause`.** `PLAN_REVIEW`
     asked the model for the clause; `ART_REVIEW` — which writes 96% of the
     rows — had no such field in its output schema, and neither
     `log_art_verdict` call passed one. The field the whole mechanism is built
     around was never populated by its main producer.
  2. **Unit tests wrote the production ledger.** The root was a hardcoded
     `Path(__file__).parents[3]`, so any test driving the art gate appended to
     the real file: 66 of 225 rows carried the fixture song name `"song"`. That
     is not cosmetic — `recurring_failures` thresholds on the number of
     *distinct songs* a code has bitten, so fixtures were voting on what the
     next real song's reviewer gets told. The root now comes from the app's
     data dir, and returns `None` rather than guessing.
  3. **One song counted as two.** `梦幻泡影` and `2026-02-23__梦幻泡影` were
     distinct keys, so a single song could clear a bar meant to require two.
     `song_key()` strips the dated-folder prefix and case-folds — deliberately
     nothing more, so genuinely different songs stay distinct.
- **Recurring-failure seeding.** `recurring_failures_block()` feeds the *next*
  song's `art-review` the failure modes already rejected on **two or more
  different songs**. The threshold is on distinct songs, not raw count, on
  purpose: one song failing the same way eight times is one lesson about that
  song, and seeding it elsewhere would import a bias the new song never earned.
  The block is `""` until real evidence exists, so a fresh install's reviewer
  prompt stays byte-identical (and prefix-cacheable). It is resolved **once per
  run**, never per scene.
- **`POST /api/visual/art-reject` — the friction signal.** Automated gates only
  catch what they have a code for. A human rejecting work for a reason no gate
  names ("this is person-centric and the song is not") previously left no trace
  at all. This records it to the ledger, and for a *systemic* reason — one that
  implicates the contract or the pipeline rather than one unlucky image — also
  files a fix-prompt through `surface_friction()` into the same queue every
  other EmptyOS friction source uses. `GET /api/visual/art-ledger` reads it back.

## Invariants

- No full video fan-out before a passed motion proof plus either explicit
  manual approval or persisted per-run `auto` authorization.
- No render submitted to a reachable but dependency-mismatched ComfyUI runtime.
- No generated-audio substitution: the selected existing song remains the
  timing source and final-master soundtrack for the entire run.
- No I2V generation from a reference still that has not passed the anatomy,
  occupancy, and structure gate.
- No still-image prompt reused as temporal conditioning.
- No camera move described in a prompt. A text-described move is invented, not
  executed: measured twice, once turning a wet street into a rotated rooftop
  (2026-07-25) and once producing 49 frames of no movement at all (2026-07-28,
  same seed as a passing control). `video_prompt` opens with
  `Locked-off camera.`; motion is described *within* the frame.
  The exception is a scene that carries a `blockout` spec, where the move is
  rendered as Blender depth geometry and Wan 2.1 VACE supplies appearance —
  per-scene, so a song may mix locked-off and moving shots. Dark behind
  `[apps.music-studio] feature.geometry-camera.enabled`.

  The authoring path exists and is gated: a scene opts in by carrying
  `camera_move` (free text), and `POST /api/visual/blockout/author` renders a
  spec per requesting scene, gates the depth sequence, and retries three times
  with the gate's own complaint before returning the scene untouched. What is
  missing is narrower than "unreachable": **no stage or prompt emits
  `camera_move`** — it appears nowhere in `prompts.py` or `visual.py` — so the
  field is authored only by the art director or the operator, and a normal run
  that nobody hand-annotates is locked off throughout. This is the same
  authored-nowhere shape that left `visual_motif` unset; if a planner should
  emit it, the art-director skill's treatment format has to name it first.
- No silent substitution for a failed guided clip. A scene that asked for a
  camera move and could not get one is left unbuilt and logged, exactly as the
  video branch already refuses a Ken Burns rescue — a static shot delivered in
  place of a move is degradation the run would otherwise report as success.
- No arbitrary 25/30 fps conversion when the model generated at 24 fps.
- No inherited 22–30 fps nominal-rate drift from `setpts`: every accepted
  delivery clip and the master are constant native fps with integer frame
  counts, and every chained continuation drops its single conditioning-frame
  overlap before seam review.
- No motion speed/amplitude inferred from duration alone. Each scene declares
  director intent, then runtime combines it with scene/generation duration and
  chain position.
- No numeric QA thresholds or internal policy paragraphs in Wan conditioning.
  The prompt states artistic intent; deterministic gates enforce the contract.
- No accidental shake accepted as cinematic energy. Whole-frame motion is
  judged against the scene's explicit `camera_motion`.
- No retry that increases displacement after a distortion failure.
- No new person/animal/duplicate subject introduced during I2V.
- No silent Ken Burns fallback in video mode.
- No one-off preview script treated as the production path.
- No audition candidate treated as final approval; promotion always re-renders
  and re-reviews production length.
- No assembly from a partial scene fan-out.
- No assembly-stage content re-review of locked clips. Assembly checks exact
  byte identity, provenance, completeness, and edit mechanics; scene-specific
  motion and visual judgment remains owned by the clip stage.
- No mid-line rhythm cut inside a lyric-bearing scene when no safe lyric onset
  passes the minimum-shot gate.
- No unapproved motif reuse, action rewind, or alternate crop.
- One persisted run carries the same scenes and prompts from validation through
  final history.
