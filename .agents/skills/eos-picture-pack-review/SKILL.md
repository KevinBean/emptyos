---
name: eos-picture-pack-review
description: Review a fetched image pack by eye before shipping it — build contact sheets of every downloaded photo, classify the defects that automated fetching cannot see. Use when a bulk image fetch has just finished for a picture-dictionary pack, a card deck, an illustrated KB set, or any corpus where the image IS the content. NOT for verifying that a fetch *succeeded* (a status endpoint answers that), and NOT for judging one generated artifact (use eos-page-design-review).
---

# Review a Picture Pack

A fetch that reports `ok=130 failed=0` has proved it downloaded 130 files. It has
proved nothing about whether those files show the right animal, one animal, or a
photograph at all. Every defect this skill exists to catch is **invisible to the
fetcher and green in every test** — the bytes are valid, the licence is present,
the route serves them.

Built from the Animals pack (2026-08), where a clean 130/130 fetch
still contained 18 defects, one of which made a quiz question unanswerable.

## Why by eye, and why all at once

There is no reliable programmatic detector for "this is a grid of six species
rather than one animal". A vision model could be asked, but it costs a call per
image and its judgment is exactly as reviewable as your own — so the honest
method is to look, and the only affordable way to look at 130 images is to put
them on one page.

**Do not review by scrolling the app.** The gallery shows ~20 tiles per screen at
a size where a montage reads as a photo. Two of the worst defects in the Animals
pack were invisible in the running app and obvious on a contact sheet.

## The loop

### 1. Fetch everything first

Run the pack's own prefetch to completion. Reviewing a half-fetched pack means
reviewing twice. Record the honest numbers (`ok`, `failed`, elapsed) — they are
the denominator for everything below.

### 2. Detect duplicates programmatically

This one *is* mechanical, so do it before looking at anything:

```python
by_url = collections.defaultdict(list)
for slug, rec in index.items():
    if rec.get("status") == "ok":
        by_url[rec["source_url"].split("/")[-1].split("?")[0]].append(slug)
dups = {u: s for u, s in by_url.items() if len(s) > 1}
```

Two entries sharing one photograph is the **most severe** defect class, because a
photo-to-name question becomes genuinely unanswerable rather than merely ugly —
and the two entries are usually near-synonyms (`chicken`/`rooster`), so a quiz
will happily offer both as options for the same picture.

### 3. Build contact sheets

Roughly 45 per sheet at ~190px cells, each labelled with its slug. More than that
and the cells get too small to judge; fewer and you lose the whole-pack view.
Write them to the scratchpad, then read them.

```python
sheet = Image.new("RGB", (COLS * CELL, ROWS * (CELL + LABEL)), (245, 243, 239))
# paste thumbnail, draw slug underneath
```

### 4. Classify what you see

Five classes, in descending severity. Name the class for each hit — it determines
the fix.

| Class | Looks like | Why it matters |
|---|---|---|
| **Duplicate** | two entries, one photo | the question has two correct answers |
| **Montage** | a grid of 4–6 sub-images | a taxobox for a *genus*; teaches "these are all X" when you wanted one X |
| **Illustration** | engraving, painting, line art | the learner is memorising an artist's rendering, not the thing |
| **Wrong subject** | the named thing is absent, tiny, or incidental | a street scene where the animal is one of thirty objects |
| **Weak** | correct but dark, cluttered, occluded, or a close-up of one part | recognisable only if you already know the answer |

Fix the first four. **Leave "weak" alone unless it is genuinely unreadable** —
chasing aesthetic perfection across a whole pack is unbounded, and a slightly
dark but correct photo still teaches the word.

### 5. Find replacements — articles before file searches

Two sources, in this order:

1. **A more specific article title.** `Deer` is a genus page with a montage;
   `White-tailed deer` is a species page with one clear animal. This fixes most
   montages and costs one field change. The displayed name does not change — only
   where the photograph comes from.
2. **A direct file pin** (`image_hint`), when no article's lead image works —
   e.g. every chicken article shares one photo, so `rooster` needs a pinned file.
   Use a site search restricted to bitmaps.

Build a **candidate comparison sheet**: 2–4 candidates per defect, side by side,
labelled. Judging candidates against each other on one page is far faster and
more consistent than opening them one at a time.

### 6. Apply with an assertion per change

Write the fixes through a script that asserts the current value before replacing
it, so a re-run cannot silently double-apply or edit the wrong entry:

```python
assert by_slug[slug]["wiki"] == old, f"{slug} is {by_slug[slug]['wiki']!r}, expected {old!r}"
```

Record *why* next to each change. "montage of 6 deer species" is the sentence
that stops someone reverting it in six months.

### 7. Re-validate, refetch, re-review

- Re-run the pack validator — a replacement title must still resolve.
- **Confirm the daemon reloaded the pack before refetching.** A pack is read at
  `setup()`; refetching against a stale in-memory pack re-downloads the *old*
  sources and looks like success. Verify by reading one changed entry back
  (`item["wiki"] == "Harp seal"`) before spending the downloads.
- Refetch only the changed slugs, `force=True`.
- Re-run the duplicate check, then build one final sheet of just the fixes and
  look at it. Roughly one in ten replacements is itself a defect.

## Budget

For ~130 images: about 20 minutes of looking, plus fetch time. This is real work,
not optional polish — plan it in rather than discovering it after ship.

## When NOT to use this

- **Generated images**, where the fix is the prompt and the loop is the generator's
  own review gate.
- **User-supplied images** — they chose them.
- ~~**Packs under ~20 items**, where scrolling the real app is genuinely enough.~~
  **Withdrawn 2026-08-31** — measured wrong. Vegetables (20), kitchen (20) and
  home & furniture (15) each carried defects a contact sheet caught and the
  running app did not; home was 9 wrong in 15. Size does not predict defect
  rate. What does is the theme — see `.claude/skills/eos-picture-pack-author`
  § Choose a theme the source can serve.
- **When the image is decoration**, not the content. This skill is for corpora
  where getting the picture wrong means teaching the wrong thing.

## Cross-references

- `.claude/rules/audits.md` — the false-positive discipline; step 2 is the only
  part here that can be mechanised, which is why the rest stays a hand-review.
- `apps/public/englishos/dictionary/PICTURE-PACKS.md` — the worked example,
  including the measured API constraints that shape any Wikimedia-backed pack.
- `.claude/rules/proposed-action.md` — step 6's assert-before-replace is the same
  staleness instinct, applied to a batch edit.
