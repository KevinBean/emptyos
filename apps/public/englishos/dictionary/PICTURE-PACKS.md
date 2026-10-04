# Picture packs — intent

> Absorbed into `dictionary` on 2026-08-19 from the standalone `picture-dict`
> app (retired to `apps/_retired/`). The decisions below predate the merge and
> survive it — they are why the shipped pack is not written to the vault, and
> why the photos are real rather than generated. The app-shaped framing ("this
> app") now means *the picture surfaces of the dictionary*.

## What it is for

Learning the **English name of a thing you can recognise by sight**. The first
pack is Animals (130 entries). The whole machine — photo fetch, gallery, quiz,
scheduling, pronunciation — is category-agnostic; a Food or Tools pack is a new
membership file plus one line in `packs/meta.json`, not a new app.

## One object, many packs

An **object** is stored once in `packs/objects.jsonl` and carries identity only
— slug, name, 中文, pinyin, emoji, wiki title, hint. A **pack** is a membership
file (`packs/<id>.pack.json`) listing which objects it contains and how it
groups them. `meta.json` is the registry joining the two.

```
packs/
  meta.json            registry: id, title, emoji, membership filename
  objects.jsonl        one line per object. No category. No pack.
  animals.pack.json    groups -> [slug]
```

`chicken` is one body, one progress row, one cached photo and one `pic:` card —
appearing in Animals as `farm` and in Food as `meat`. Learning it once counts
everywhere, which is what the storage layer already assumed: progress and the
image index are both keyed by slug globally.

**`category` is a property of the membership, not of the object.** It is
empirically pack-scoped, it is the quiz's distractor pool (which must match the
pack the learner opened), and its labels belong to the pack that names them.
Before the 2026-08-20 split it lived in the object row, where there was only
room for one — so the second pack to claim a slug lost it, silently.

`by_category` keys are qualified (`animals:sea`). Generic group ids collide
across themed packs by construction; `picture_catalog.resolve_category` accepts
the bare form while it is unambiguous, and refuses rather than guesses when it
is not.

**Adding a pack:** add its objects to `objects.jsonl` (reusing any slug that
already exists — do not duplicate it), write `packs/<id>.pack.json`, and add one
row to `meta.json`. Then fetch and run `/eos-picture-pack-review` — a clean
`ok=N failed=0` proves the bytes downloaded, not that the photos show the right
thing.

That is why it is not called `animals`. Renaming an app id later is expensive
(web prefix, tier membership, store state, tests, bookmarks), and the generality
was free at authoring time.

**When the article's lead image is wrong**, set `"image_hint": "none"` on the
object. The photo fetch skips it, it is not counted as a failure, and the card
keeps its emoji. Everyday objects need this often: the scene packs (2026-09-22)
carry 39 such rows, whose lead images included a 1622 bond for *banknote*, soy
powder for *flour* and a door knocker for *door handle*. A wrong photo teaches
the wrong word; the emoji tile is the designed fallback. Such cards are named
in Browse, Review and Speak, but once enough photos exist in a scope the Quiz
draws only photographed cards, so they are not quizzed there.

## The four surfaces, and why each exists

| Tab | Answers |
|---|---|
| Browse | "What is this called?" — the reference surface, and the only one that works with zero setup. |
| Quiz | "Do I actually know it?" — recognition under pressure, photo → name. |
| Review | "Will I still know it next month?" — spaced repetition over the ones you missed or starred. |
| Speak | "Can I say it so someone understands?" — recognition is not production. |
| Speak → ⚡ Speed round | "Does the word come *fast*?" — ten pictures from the pack being browsed, each shown only once the microphone is recording, so the silence before the first sound (`speech_onset`, measured from the audio) is the reaction time. Onset is relative to the recording's own noise floor, so a noisy room does not read as "instant"; a hesitation sound counts as the onset, and the photo is loaded before the microphone opens. Right within `picture-dict.fast_seconds` reviews a card only if it is due; slow or wrong adds it to Review if it is not there yet; nothing is ever graded down. |
| Speak → 🎭 Improv scene | "Can I produce it in a conversation?" — opens Improv with the browsed pack (or the talked-about object's pack) preselected as its word source (`?source=dictionary:pack:<id>`); Improv's own picker also offers every pack and "Words due for review today". The dictionary is one `[[contributes.improv.wordsource]]` contributor (`improv_sources` / `improv_words` / `improv_result`): only the source key travels in the link and the names come back from the dictionary, so link text never reaches a model prompt. The partner sets up moments that need each object without naming it (and any name it slips is blanked before it is shown or spoken). At the end Improv reports used + missed ids back through `improv_result`: missed words are added to Review (a due one simply stays due), each word said is counted under its progress entry's `scene` (`said`, `last`, `last_source`) and shown on the card as "Said in a conversation N×", and a card **due today** that was said is graded `good` — saying it in the scene was its review ("due" is the Review tab's rule, so an enrolled card not yet reviewed counts too). A card not yet due is not graded early. |
| Speak → 🗣 Talk about it | "Can I keep going?" — 30–60 s of free speech about one picture, with prompt questions and words from the same pack to try. Fluency is `emptyos.sdk.audio.score_fluency`, the scorer Speaking Practice uses (pace, fillers, immediate repeats, long pauses). Pace and pauses come from the recording itself — the span from the first spoken sound to the last, and the gaps inside it; pauses background noise hides are shown as "not measured", and a recording that cannot be measured at all is not scored. Also reports whether the object's name and which of the pack's other words were used. Practice only — it never touches the review schedule. |

## Decisions worth not re-litigating

**Real photographs, never generated images.** The entire point is identifying a
real animal. Generative models produce anatomically wrong animals often enough
that a generated "heron" actively teaches the wrong thing. Accuracy beats style
here, so photos come from Wikimedia.

**Nothing is written to the vault unless you star it.** 130 shipped reference
entries are not user content, and flooding `30_Resources/Learning/Dictionary`
with words you already know would make the real vocabulary store less useful.
The star routes through `dictionary.save_word`, so `dictionary` stays the single
durable vocabulary store and this app never becomes a second one.

**Photo credit is captured at fetch time, not derived later.** The images are
CC BY-SA / CC0 and attribution is a licence condition, not a nicety. An offline
app cannot re-derive the artist, so artist / licence / source page are stored
with the file the moment it is downloaded, and rendered on the card.

**No `think` calls, so no model pill.** Quiz distractors are sampled
deterministically from the answer's own category — a leopard's decoys are other
big cats. That is better than an LLM would do for this task, costs nothing, and
works offline. Per `.claude/rules/model-pill.md` the pill is for apps whose value
*is* model output; this app has none, and the absence is deliberate rather than
an oversight.

**A weak spoken attempt never grades a card down.** A bad microphone is not a
failure of recall. Above the pass mark it grades the card `good`; below it, the
attempt is recorded and the schedule is left alone.

**The emoji tile is a designed state, not a broken one.** Before any photo is
downloaded — and permanently, for anything Wikimedia has no free image of, and
on a public deployment where the fetch gate is closed — every card shows its
emoji. The app is fully usable in that state.

## Measured facts that shaped the code

* The API batches **50 titles per call**, so metadata for 130 animals is 6
  requests, not 130.
* Only three thumbnail widths are actually served: **330 / 500 / 960**. The API
  reports the width you *asked* for while returning a different bucket, and a
  hand-built URL at an unlisted width is rejected outright. Never construct a
  thumbnail URL. Disk for 130 animals: ~5 MB / ~10 MB / ~31 MB.
* `Crane`, `Seal` and `Mole` are disambiguation pages with no image. Every pack
  entry therefore carries an explicit `wiki` title, and all 130 were verified
  against the live API to resolve to a free-licensed lead image.

## Deliberately not built

* **Bulk SRS enrolment.** Cards enter review on a quiz miss, a star, or an
  explicit "add to review". Seeding 130 cards on install produces a queue nobody
  chose and therefore nobody clears.
* **An LLM "fun fact" generator.** It would add a model dependency, a cost, and a
  hallucination surface to an app whose content is otherwise verified.
* **Editing the pack from the UI.** The pack is shipped reference data. Correcting
  a photo is `Wrong photo?`; correcting a name is a pack edit in git.
