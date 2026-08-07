---
name: tool-flow-stills
description: Generate, review and repair still images in Google Flow (Nano Banana 2) by browser automation — free unlimited image generation, batch prompting, per-image editing that preserves what you tell it to preserve, version-history revert, and asset renaming as the shot-mapping mechanism. Use when the user wants stills for an MV, article, slideshow or deck made in Flow, or says "use Flow", "use Nano Banana", "generate the stills", or points at a labs.google/fx/tools/flow project. NOT for local GPU generation (use tool-comfyui-image), NOT for the EmptyOS Music Studio staged pipeline (use creative-mv-generator), NOT for defining a film's visual language (use creative-mv-art-director first).
---

# Flow Stills Skill

Operating manual for Google Flow's image side, learned the expensive way on
无所住 (2026-08-07). Flow does **both** stills and video in one project, so
assets never leave the workspace between stages.

## The economics that shape everything

| | cost |
|---|---|
| **Nano Banana 2 image generation** | **0 credits — free, all tiers** |
| Image editing (same surface) | free |
| Omni Flash video, 10 s | 15 credits |
| Veo 3.1 Quality video | 100 credits |

**Because stills are free, review-and-regenerate is the cheap half of the
pipeline.** Do not try to nail a perfect first pass — generate, look, and
regenerate the misses. Four rounds cost nothing.

## Hard-won mechanics

### Free generations skip the approval gate
With *Confirm before generating: Always* set, credit-spending generations
require an Approve click. **Free image generations bypass it entirely** — so a
long unattended stills run needs no settings change and no supervision. Video
does.

### ⚠ Confirmed on video: the once-per-session approval fails SILENTLY

Measured on Love Hate v6, 2026-08-08. A second video request in the same session
produced the agent's *"I will generate…"* text and then **nothing** — no approval widget,
no generation, no credits spent, and **no error**. It is indistinguishable from a request
still thinking.

**A page reload is NOT sufficient** — this cost four wasted attempts. Reloading does
produce a differently-named session, so it *looks* like a reset, but that session still
never draws the widget. The agent replies *"I will generate…"* and then stops, every
time.

**The only reliable reset is the explicit control: ☰ (top-left of the session panel) →
"Create a new session".** That gives an "Untitled session", and the widget renders on
its first request. So the working loop for a multi-clip run is:

> ☰ → Create a new session → request → **Approve** → wait → verify in `Videos`

**Budget one explicit new session per credit-spending generation.** Approving with
"Approve" (not *"Approve, do not ask again"*) keeps the gate for the next one.

One more trap on top: **the session panel does not re-open by itself once closed.** If
you dismiss it with ✕, later replies — including approvals — render into a panel you
cannot see, which is indistinguishable from nothing happening. Re-open it with the
expand control beside the create box.

Because the failure is silent, **verify against the `Videos` list, not against the
chat.** Counting clips is the only reliable signal that a generation actually happened —
and it is also how you confirm no credits were spent when it did not.

### The approval widget renders only ONCE per conversation session
A credit-spending request later in the same session states its plan and then
silently waits on a control Flow never draws. The agent will insist it is
"waiting for your permission approval in the interface" when there is nothing
to click. **Fix: start a new session per credit-spending generation** (☰ →
Create a new session). This cost ~15 minutes before it was diagnosed.

### A single conversation collapses composition
Flow's agent volunteers *"I'll use the first generated image as a style
reference to maintain consistency"*. It propagates **composition**, not just
palette, and the collapse grows with series length — 13 of 18 frames became the
same photograph (eave over rooftops, misty mountains, wet ledge).

**Fix, and it works decisively:**
1. **Fresh session** for each batch of 3–5.
2. **Name what must not recur**: "no dark eave across the top, no misty mountain
   backdrop, no white-walled village receding into fog, no wet stone ledge along
   the bottom — those have been overused."
3. **Force shot scale explicitly** — macro / overhead / sky-only / enclosed.
   Without this every frame converges on the establishing composition.

### Batch 3–5 per message
The agent handles multiple numbered descriptions in one message and generates
them in parallel (~50 s). Beyond ~5 the per-image direction gets diluted.

### ⚠ A newline in the project chat SENDS the message — compose as ONE line

Found on Love Hate v6, 2026-08-07. A multi-paragraph prompt **sends at the first
newline**: the opening paragraph goes off as a complete message and the rest sits
in the input box, unsent. The agent then works from a preamble containing none of
the actual shot descriptions.

It fails quietly, like the others in this file — the message looks sent, the agent
answers confidently, and it is answering a prompt you never meant to ask.

**Write every Flow chat prompt as a single line.** Separate items inline —
`IMAGE 1 — … IMAGE 2 — … IMAGE 3 — …` — never with line breaks. This applies to
the batch-edit template below too.

Recovery: click ■ to cancel the response, click the ✕ on the input box to clear the
leftover text, then retype as one line. Nothing is charged (images are free) and no
asset is created by the truncated request.

## 🔑 THE BIG ONE: the project chat is an AGENT — batch, don't hand-edit

**Read this before touching anything else.** Flow's right-hand project chat can
apply the *same edit to many existing images in one message*. It processed
**11 images in a single request**, correctly, in about a minute — after I had
spent hours opening frames one at a time.

Ask it plainly:

> I need a colour correction applied to MULTIPLE existing images in this
> project — please edit the existing images, do not generate new ones. Take
> every image whose name starts with a number and a middle dot (01 through 18)
> and apply this same correction to each: **⟨the edit⟩**. Keep each image's
> exact composition, framing, subject, brightness and contrast unchanged. Can
> you apply this across all of them in one go? If you can only do a few at a
> time, do as many as you can and tell me which ones you completed.

Why this phrasing matters:
- **"edit the existing images, do not generate new ones"** — without it the
  agent may generate fresh frames instead of editing.
- **Naming the selection by a filename pattern** (`NN ·`) is what lets it
  address a set. This is the payoff for renaming keepers — the names become a
  *queryable selector*, not just a label.
- **"tell me which ones you completed"** — you get an explicit manifest back
  instead of having to audit the grid. **But see the next section: the manifest is
  a claim, not evidence.**

### ⚠⚠ The completion manifest can be FABRICATED — always verify by search

Measured on Love Hate v6, 2026-08-07. Asked to rename 19 existing images, the
project chat replied with a per-item bulleted list of all 19 new names and the
sentence *"All items were successfully matched and updated. No files were left
unrenamed."*

**It had renamed exactly one of the 19.** Every other asset still carried its Flow
auto-caption ("Two men walking down street", "Hand resting on shoulder blade").

Renaming **is** an advertised capability — Flow's own onboarding tooltip lists
*"rename assets"* among what the agent can do. So this is not a missing feature. The
agent can do it, did one, and reported nineteen. Capability is not the issue;
**execution reporting is.**

This is the sharpest form of the proxy-signal trap in this file, because the
manifest is the thing recommended above as the fix for proxy signals. It is still
worth asking for — it tells you what the agent *believes* it did — but it is not
evidence.

**Open an asset and read its title bar** — that is the check that distinguishes a
rename from an auto-caption. A `·` search is a useful first pass but it did not
render reliably on a later attempt, so do not depend on it alone.

### Renaming by hand — the fast path

The **detail view title is editable inline**, which is much quicker than the grid's
`⋮ → Rename`:

1. **← / → arrow keys step through every asset deterministically.** Use these, not
   the filmstrip — the filmstrip reorders as you rename and coordinate-clicking
   lands on already-done frames.
2. The auto-caption in the title bar identifies the frame ("Two men sharing
   cigarette"), so one zoom on the title tells you which shot you are on. Where two
   shots could share a caption, look at the image before typing.
3. **Triple-click the title** to select it, then type the new name **with a trailing
   newline** — the newline commits. That is 4 actions per asset.

19 assets took roughly 80 actions this way.

Keep the authoritative shot→image mapping in a file on disk regardless. Flow names
are a convenience for the video stage, not the record.

Per-image editing (below) is now only for **one-off repairs** — a single frame's
composition, a continuity fix, a wrong subject. Never use it for anything
applied across the set.

Cost is unchanged: image work is free, so a batch re-run costs nothing.

## The editing surface (per-image repair)

Click an image → detail view with *"What do you want to change?"*.

**It honours "keep X exactly as it is."** Both successful edits on 无所住
preserved their stated invariants pixel-for-pixel and changed only what was
named:

> *"Keep the composition, the low camera height, the palette and the flower
> exactly as they are. Change only the setting: replace the European deciduous
> trees and tarmac with a wet stone lane and an out-of-focus Huizhou wall."*

**Narrow, explicitly-scoped edits are safe. Blanket style edits are not.**

### ⚠ A scoped edit can silently change the ASPECT RATIO

Found on Love Hate v6, 2026-08-08. An edit that only re-dressed a room's contents
returned the frame as **4:3 instead of 16:9**. Nothing in the prompt mentioned
framing, and the change is easy to miss because the detail view fits any ratio
neatly into its container.

It matters because assembly needs every clip at one ratio — a single 4:3 still
becomes a pillarboxed or cropped shot in the cut.

**Check the ratio after every edit**, and if it drifts, a follow-up edit naming
"wide 16:9 widescreen, extend the scene left and right" restores it while keeping
the content.

### The corollary: an edit changes ONLY what you name

The same session asked for a bed to be cleaned up and named the mattress, walls and
floor. The mattress, walls and floor came back clean — and the **pillows**, which
were never named, came back exactly as stained as before.

This is the same property that makes scoped edits trustworthy, seen from the other
side. **Enumerate every element that must change**, and expect anything unnamed to
survive untouched.

### ⚠ A near-black gradient is a LOADING PLACEHOLDER, not a failed render

**This wasted two edits and produced two wrong conclusions.** A colour/tone edit
renders progressively; mid-render the frame shows as a dark featureless
gradient that looks exactly like a destroyed image. Judging it there leads you
to revert a perfectly good result.

**Wait for the render to settle, and check the history panel before reverting**
— the finished version appears there even when the main canvas still shows the
placeholder.

Grade-type edits do work on this surface:

| Edit | Result |
|---|---|
| *"Shift the neutrals warm — stone as warm grey not blue-grey, tile as umber not blue-black, fog as ivory not blue-white."* | Worked. Only temperature moved. |
| *"Far too desaturated and near-monochrome — bring real colour back: living green moss, real slate and umber in tile, natural brown timber. Natural and filmic, not vivid."* | Worked. Real colour returned with composition and light intact. |

Both were briefly "black gradients" before finishing.

### 🚨 After a manual REVERT, a later edit does not take selection

Measured on Love Hate v6, 2026-08-08, and it is probably the real mechanism behind the
"I2V bound a stale version" observation below.

Five stills were edited in one pass. Checking them afterwards, **two carried the wrong
selected version** — the canvas and the asset's current state showed an *older* entry
while the newest render sat unselected further down the history panel.

The two were exactly the two where I had **manually reverted before editing**. The
sequence that breaks it:

1. click an older version in the history panel to revert → that version becomes selected
2. run a new edit → it renders and appends to the bottom of the history
3. **selection stays on the reverted-to version**, not the new one

Nothing announces this. The edit clearly succeeded, the new thumbnail is right there,
and the asset silently remains on the older frame. Anything downstream — a later edit,
an export, an image-to-video generation — takes the *selected* version, not the newest.

**So the check before animating is not "did my edit render" but "is my edit the selected
version".** Open each asset, look at which history entry carries the selection border,
and click the newest if it does not. On an asset you never reverted, the newest is
selected normally — the defect is specific to the revert-then-edit sequence.

**⚠ But fixing the selection does NOT fix the video binding.** Measured immediately
after, on the same asset: with the newest version correctly selected, and with the
agent stating in its own approval message *"I'll use the most recent, dimmer version"*,
the rendered clip came back from the **original, brightest** version anyway. Selection
is a separate defect from binding, and correcting one does not correct the other.
**Flattening (below) is mandatory, not a fallback.**

### 🚨 Image-to-video may bind a STALE VERSION of an edited still

Measured on Love Hate v6, 2026-08-08. A still that had been edited four times was
used as the start frame for an I2V clip, with the prompt explicitly asking for *"its
most recent version"*. **The clip rendered from an earlier version** — carrying two
defects that had already been fixed.

The consequence is severe and silent: every repair made to a still can be discarded
at the video stage, and nothing announces it. Only comparing the clip's first frame
against the intended still reveals it.

**Re-measured 2026-08-08 and CONFIRMED, with both obvious safeguards in place.** The
asset's newest version was correctly selected, and the agent's own approval message
said *"I'll use the most recent, dimmer version"*. The clip still rendered from the
**original**, several versions back. Neither the selection state nor the agent's stated
intent governs what I2V actually binds.

**Mitigation — flatten before animating. This is mandatory, not optional.** Tested and
**confirmed working** on Love Hate v6: the clip from a flattened source binds the correct
frame. There is no prompt wording that substitutes — that was tried and it failed.

The procedure, about two minutes per shot:

1. Open the asset and confirm the intended version is **selected**.
2. Download → **1K "Original size"**. Not 2K/4K — an upscale changes the pixels and adds
   a variable you did not ask for.
3. **Verify the file before re-uploading.** Mean luma via PIL is enough to tell a dim
   grade from a bright one; it costs seconds and catches a wrong download.
4. Re-upload through the page's file input (`find` the `type=file` element, then
   `file_upload` — never click the button, which opens a native picker you cannot
   drive). Flow files it under a new **Uploads** section.
5. **Name it distinctly** — `NN FLAT · <desc> · video source · <timing>` — so it can
   never be confused with the multi-version original.
6. Animate the FLAT asset by name.

**Always** compare frame zero of each clip against its source still before accepting
the clip, whether or not you flattened. Do this on the **first** clip of a run, before
committing credits to the rest: one wasted generation is the cheapest possible way to
discover this.

### Revert works
Version history is in the right-hand panel; clicking an earlier version
restores it. Always check the result of an edit — and revert immediately if it
degraded, before stacking another edit on top.

## ⚠ Rename by the IMAGE, never by the auto-caption

Measured on Love Hate v6, 2026-08-08. A shot was regenerated because the first
attempt broke its brief — an "empty street with nobody casting the shadows" came back
with two men walking in it. Both versions received near-identical auto-captions
("Empty street at night"). During the rename pass **the reject was named as the
keeper**, purely on its caption, and the correct version was left unnamed.

The result is the worst possible state: a wrong asset wearing the right name, which
every downstream step then trusts.

**Open the image and look at it before typing a name.** The caption describes the
*subject*, not the *outcome*, so a failed generation and its fix are usually
indistinguishable by caption alone. This matters most exactly where you regenerated
— i.e. where you already know one version is wrong.

Label rejects explicitly (`zz REJECT - <why>`) rather than leaving them unnamed;
"unnamed" is indistinguishable from "not yet processed".

### ⚠ A re-staged shot leaves its predecessor holding the slot name

Measured on Love Hate v6, 2026-08-08 — the sharper form of the trap above, and the
one that actually bit. Six shots were regenerated for blocking variety and renamed
into their slots. **Four of the frames they replaced were already named into those
same slots** from the previous round: `10 · a night doorway`, `09 · sharing a
cigarette`, `05 · the kiss`, `13 · the two falling onto a bed`.

So renaming the keeper does not resolve the ambiguity — it *creates* a duplicate.
Two assets now answer to shot 09, and the search filter returns both.

**Renaming a regenerated shot is a two-step operation.** Name the new keeper, then
immediately demote the frame it replaced. Doing only the first half is worse than
doing neither, because the collection now looks tidy.

The predecessors are easy to miss because they sit far away in the grid — they were
made in an earlier session, so they are separated from their replacements by every
asset generated since. Do not scan for them by scrolling; go to the shot list, and
for each slot you re-staged, open **every** asset that could plausibly be it.

### ⚠ A re-NUMBERING orphans names too — and the orphan proves the slot is TAKEN

Same session, worse variant. A revision that moves a shot between slots (rev3 gave
shot 04 to a new scene, shot 06 to another) leaves the *previous* occupants wearing
numbers that no longer describe them. Two frames were still named `04 · the two
dancing close` and `06 · two empty glasses on a wet bar` long after both slots had
been reassigned.

These are invisible to every cheap check, because the image is good and the name is
well-formed. Only opening the asset and reading **title against image** finds them.

**The trap: an orphaned name means its slot was taken, not that the slot is free.**
Seeing the dancefloor two-shot stranded on `04 ·`, I renamed it into `03 ·` — which
was already correctly held by a different frame. The fix created a duplicate. Those
two situations feel identical and are opposite.

So: **before moving a name into a slot, confirm the slot is empty.** Check the shot
list, then check the asset that should already be there.

The same renumbering also orphans **prompts**. A video prompt for shot 06 described
condensation on a drinking glass, which read as a random index error — until the
orphaned asset turned up. Under the old numbering, shot 06 *was* the glasses. One
cause, two symptoms, found hours apart. After a renumbering, re-read the prompts as
well as the names.

Two labels, and the difference is real:

| prefix | meaning |
|---|---|
| `zz REJECT (<slot>) - <why>` | the frame failed its own brief — the model ignored an instruction, or it duplicates blocking the film already has |
| `zz ALT (<slot>) - <what>` | the frame is good; the choice between it and the keeper is open and belongs to the director |

`zz` sorts both to the end of any listing while keeping them available. Never delete
— a wrong reference is a hazard, an unused asset is just inventory.

### The new asset panel makes this much easier

Flow's updated layout shows each asset's **full generation prompt**, creation date,
model and aspect ratio beside the thumbnail. That is the reliable discriminator
between a reject and its fix — their prompts differ even when their captions don't.
Use it in preference to captions for any audit.

## ⚠ In a silhouette shot, character references do nothing — identity must be in the OUTLINE

Measured on Love Hate v6, 2026-08-08. A shot specified as *"two men kissing, seen in
full profile as near-black silhouettes"* came back with both figures carrying the
same head: same hair volume, same jaw, same neck, same collar. It read as **one man
mirrored**, not as the film's two characters — even though both character reference
images were attached and every other shot in the film held them perfectly.

The cause is structural, not a bad roll. Flow's Characters feature carries identity in
**face and colouring**, and a silhouette discards both. With the faces unlit there is
nothing left for the reference to act on, so the model defaults to a generic head —
twice.

**A silhouette has only four carriers of identity**, and a prompt must name them
explicitly or the shot cannot hold its cast:

1. **Hair shape and volume** — tall and messy vs cropped tight to the skull
2. **Head and jaw profile** — narrow vs heavy and square
3. **Shoulder width and height** — the build, not the face
4. **Neckline** — a raised collar vs a bare shoulder and vest strap

Fixing it is one scoped edit that names all four per figure, with left/right stated so
the model cannot swap them, plus *"their two outlines must be immediately
distinguishable from shape alone"* and *"both faces still unlit and featureless"* so
the fix doesn't quietly light the faces to solve the problem.

**Generalises to any backlit, underlit, obscured, distant or rear-view shot.** Wherever
the face is not readable, the character reference is inert and the differentiation has
to be written into the prompt as silhouette geometry.

## ⚠ Tone-consolidate the OUTLIERS only, and change only the property that is wrong

Measured on Love Hate v6, 2026-08-08, over three attempts — the first two both wrong,
in different ways.

**Attempt 1 — a blanket grade.** Asked for a tone pass across 19 stills, the obvious
move is one grade instruction applied to every frame: lift the blacks, halate the
highlights, ease the contrast, add heavy grain. It reads as a careful plan and it
**makes the images worse**: *"the new filter makes it low quality."*

The mechanism is real and worth knowing. **An image edit re-renders the entire
frame**, so on a frame that is already right:

- "add heavy visible grain" lands as noise **on top of** the grain already baked in at
  generation — the result reads as compression artefact, not emulsion;
- "lower contrast, lift the blacks" flattens an exposure that was already correct;
- the re-render softens fine detail slightly, every time.

**But do not over-generalise from that**, which is the mistake I made next: I
concluded tone work could not be done in Flow at all and should be deferred to ffmpeg.
Two frames that *did not need the pass* say nothing about the frames that do. On a
genuinely off-look frame the same class of edit works cleanly.

**Attempt 2 — the wrong property.** Given the real list, I read "too clean" as missing
surface texture and added grain, mottling and dust. Also wrong: *"clean means the tone
not the stains."* The frames were never short of texture — their **colour** was
freshly white-balanced and digital, and they were the **brightest** frames in a film
that is otherwise night.

**What worked**, applied only to the named frames:

> **Do NOT add dust, specks, scratches, marks or dirt of any kind**, and do not change
> composition, framing or subject. **(1) The light is too bright** — bring the exposure
> down noticeably; the brightest areas should read as pale grey, not near-white.
> **(2) The colour is too clean** — expired-stock tone: no pure white and no neutral
> grey anywhere, highlights slightly off, a faint sallow yellow-green in the shadows,
> colours faded and muddied rather than fresh.

Plus a per-frame clause protecting whatever that shot cannot afford to lose — the one
warm shot must stay warm, face shots must keep faces natural and readable, and every
frame keeps its existing dominant colour so the film's colour arc survives.

Three rules that fall out of it:

- **A note about the look names a symptom, not a mechanism.** "Too clean" can mean
  texture, colour or exposure. Ask which before choosing an instruction.
- **Test on a frame the director named**, not one of your own choosing. Both failed
  passes were tested on frames that were never on the list.
- **A frame that already looks right needs nothing.** Grade outliers only, and confirm
  the outliers are outliers first.

A whole-film grade — matching every shot to every other — still belongs at assembly in
ffmpeg (`eq` / `curves` / `colorchannelmixer`), where it is arithmetic on pixels rather
than a re-render. Per-frame Flow edits are for fixing *specific* frames that are wrong.

## ⚠ Motion in an empty frame needs a stated CAUSE, or it reads as a presence

Measured on Love Hate v6, 2026-08-08, on the first rendered clip. A shot of an empty
unmade bed was given the motion *"one fold of the sheet releases and settles into the
hollow"*. The clip is technically exactly that — and it is **uncanny**: a sheet moving
by itself, in an empty room, with nobody there. It reads as a poltergeist, not as
absence.

The cause is a prompt error, not a model error. The *"name two observable motions per
shot"* discipline is right, but on an **absence** shot it pushes you into inventing
motion that nothing in frame could have produced. A body could move that sheet — and
the whole point of the shot is that the body is gone.

**Every motion in a people-free frame must have a visible or stated physical cause.**
Auditing the rest of the film found this was a pattern, not a one-off — three of five
people-free shots had it:

| motion | verdict |
|---|---|
| a colour field drifting, grain crawling | ✓ the medium itself is the cause |
| fog re-forming on cold glass, a droplet running | ✓ condensation, gravity |
| litter turning over in the wind, a lamp flickering, moths circling | ✓ wind, electrics, insects |
| **a coat swaying on its hook** indoors | ✗ no draught named |
| **a sheet folding itself** | ✗ nothing touches it |
| **shadows wavering where nobody stands** | ✗ **worst** — the premise is that nothing casts them, so movement puts someone there |

Two ways to fix, and prefer the first:

1. **Name the cause** — *"a draught under the front door stirs the hem of the coat"*.
   The detail survives and becomes motivated.
2. **Change what moves** — for the bed, the light creeping as the sun rises and dust
   motes turning in the beam, with *"the bedding does not move at all"* stated
   explicitly. The shot still has two motions; both have a source.

For shadows specifically: **intensity may change, shape may not.** A flickering lamp
legitimately makes a shadow's edges harden and blur. A shadow that *shifts position*
has a body behind it.

## Review at full size, never from thumbnails

Two defects on 无所住 were **invisible in the grid and obvious full-screen**:

1. **Cultural fit** — a "roadside flower" frame rendered as an English country
   lane (bare deciduous trees, tarmac) in a film set in a Huizhou town. The only
   frame with no Chinese context at all.
2. **Continuity** — two consecutive shots of *the same flower* showed a white
   anemone and a daisy.

Open every frame individually before declaring a set done.

## Shot mapping lives on the assets

Flow has **Rename** in each image's ⋮ menu. Name keepers
`NN · description · motif/state · timing`, leave superseded versions with their
auto-names, then **search `·` to filter to exactly the keeper set**. This
survives sessions and puts the mapping where the video stage needs it.

## Cleanup

⋮ → **Move to trash** (recoverable; *Restore All* in the Trash view). Never
empty the trash.

**Verify by name before every removal.** The grid reflows after each deletion,
so coordinates go stale and blind clicking opens the wrong asset — one such
click opened a keeper's detail view instead of a menu. Read the label, then act.

## The failure mode that recurs — constant text overwriting variation

Four separate defects on 无所住 had one cause: **anything held constant across
shots silently overwrites anything meant to vary across shots.**

| Symptom | Constant that caused it |
|---|---|
| A kite appeared in a shot with no kite | a style contract listing per-shot literal anchors, appended verbatim to every prompt |
| Rain fell in all 18 shots, including the clearing sky and the final stillness | one shared material-behaviour string containing "rain visibly strikes surfaces" |
| 13 of 18 frames were the same photograph | one conversation + the agent's style reference |
| Every frame sat at one flat tone | an identical palette line in every prompt |

**Rule: a globally-appended string may contain only what is true of every
frame.** An object, a weather state, or a tonal key belongs in its own shot's
description. Any per-act arc — weather, tone, framing — must be written into
the per-shot prompt or it will not survive.

## Licensing

Nano Banana output carries Google's terms and a **SynthID watermark with no
opt-out**. Different position from local Apache-2.0 models. Check before
monetizing.

## Cross-references

- `creative-mv-art-director` — define the visual language first; this skill executes it
- `creative-mv-generator` — the EmptyOS Music Studio staged pipeline (local GPU)
- `tool-comfyui-image` — local FLUX generation, no watermark, commercial-safe presets
- `{vault}/10_Projects/YouTube-Music-Channel/songs/2026-02-23__无所住/` — the worked example: `art-direction.md` § Generation method, `stills-run-2026-08-07.md`
