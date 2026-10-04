---
name: eos-picture-pack-author
description: Author a picture-vocabulary pack from a theme — choose a theme the image source can actually serve, propose it, screen the resolved filenames BEFORE spending any downloads, repair the proposal in place (retitle / add / drop / merge), and only then apply. Use when the user says "new picture pack", "add a pack for <theme>", "extend the food pack", or asks which pack themes are worth building. NOT the by-eye pass on an already-fetched pack (use eos-picture-pack-review, which this skill hands off to), and NOT for generated images.
---

# Author a Picture Pack

The composer's verification proves a free lead image **exists**. It proves
nothing about whether that image shows the thing. Those two are uncorrelated,
and the gap is where every defect lives.

Measured across six packs in one session:

| Pack | Verified | Wrong subject | Outcome |
|---|---|---|---|
| vegetables | 21/21 | 3 | shipped |
| kitchen | 20/25 | 2 | shipped |
| tools | 32/32 | 6 | shipped |
| **home & furniture** | 15/18 | **9** | **rejected** |
| garden | 16/16 | 7 *(caught pre-fetch)* | shipped 13 |

Tools verified perfectly and had the second-worst pictures. Home verified well
and was unshippable. Do not read a clean fetch as a clean pack.

## 1. Choose a theme the source can serve

The bar is **not** "is it photographable". It is **does the image source hold
product photography of it**. Wikimedia Commons is a cultural-heritage archive:
it is rich where the subject is a specimen or a documented manufactured object,
and poor where the subject is ordinary domestic stuff nobody uploads.

| Good fit | Poor fit |
|---|---|
| specimens — animals, plants, food | domestic furniture |
| documented manufactured objects — tools, kitchenware, vehicles, instruments | interiors and fittings |
| natural phenomena — weather, landscape | anything mainly documented by museums |

The home pack failed on this and no amount of repair would have saved it:
searching `sofa`, `bed`, `wardrobe`, `blanket`, `door` returns Chippendale
drawings, Wellcome etchings, museum cabinets, HABS architectural surveys and
Chilkat ceremonial blankets. `bed` and `doorknob` returned nothing usable at all.

Reject the theme rather than grinding. A rejected theme costs one proposal; a
half-repaired pack costs an hour and still teaches the wrong picture.

## 2. Propose

```
POST /dictionary/api/picture/packs/propose  {"description": "..."}
```

Extending an existing pack needs `{"pack_id": "food", "extend": true}` — the
flag is required, because an auto-derived id collides (`_id_from("food in the
fridge")` is `food`) and inferring intent would write into a shipped pack.

Compose takes ~60s and exceeds the default tool timeout. Start it in one call,
poll in another.

## 3. Screen the filenames — before spending a download

**This is the load-bearing step and it is mechanical.** `_verify_candidates`
already records the resolved Commons filename on every row. Read it:

```python
SUSPECT = re.compile(r'museum|MET |BC|neolithic|medieval|antique|etching|'
                     r'drawing|scheme|patent|DPLA|\.svg|18\d\d|19\d\d', re.I)
```

Then read the survivors by eye too — the regex cannot see
`Peasant_in_the_vegetable_garden.JPG` (subject is the person) or
`Assorted_forks.jpg` (cutlery, for a garden fork).

On the garden pack this caught **7 of 16 with zero downloads**. The four earlier
packs paid full fetch cost and found the same class afterwards.

### The failure shapes, with real examples

| Shape | Why it happens | Seen as |
|---|---|---|
| **Species lead** | a plant article is about the species | `parsnip` → a herbarium specimen of SEEDS |
| **Generic-concept lead** | the article spans the tool's whole history | `hammer` → a museum WAR hammer; `chisel` → Neolithic, 4100 BC |
| **Object in a scene** | the article illustrates use, not the thing | `hoe` → a peasant in a garden; `baking tray` → a Navy baker |
| **Schematic** | the lead is a diagram | `drill` → `Drill_scheme.svg` |
| **Heritage documentation** | museums upload, owners don't | `bucket` → a Gotland museum farmstead |
| **Wrong instrument** | the title redirects | `protractor` → `Goniometer` |

`looks_like_a_drawing()` catches none of these: it keys on filename tells
(`koeh`, `plate_N`, `lithograph`) and a museum specimen photo is a real
photograph with an innocent name.

## 4. Repair in the proposal, not after

Everything here is free — nothing is written until apply.

| Verb | Body | Use for |
|---|---|---|
| retitle | `{slug, wiki}` | a more specific article. **Try first** — re-verifies on the spot |
| add | `{add: [row]}` | a replacement word. Needs no slug |
| drop | `{slug, drop: true}` | no usable photo exists, or the word was filler |
| merge | `{merge_group, into}` | clear a group under the four-item floor |

Order: retitle → drop → merge. Merging last means you merge what actually
survived.

A more specific article often fails too — `Claw hammer` leads with a crop of
**Dürer's Melencolia I**, `Garden fork` with a bar spade. Check the lead before
retitling; the article route is cheap but not reliable.

Drop a word rather than ship a wrong picture. No word beats a wrong word: a
goniometer captioned "protractor" teaches a false association, and the caption
does not undo it.

## 5. Apply, then hand off

```
POST .../proposal/{pid}/apply
```

Then run **`/eos-picture-pack-review`** for the by-eye pass. Its guidance that
packs under ~20 items don't need it is wrong — vegetables (20), kitchen (20) and
home (15) each carried defects.

## 6. Pin what the pre-screen missed

Post-apply, put a bare Commons filename in `image_hint` on the object, assert
the current value first, then force-refetch just those slugs. Record *why* next
to the change — "lead was a museum WAR hammer" is the sentence that stops
someone reverting it.

Search Commons with `intitle:` — a bare term drowns in seed catalogues and
architectural surveys.

## When NOT to use this

- The theme is in the poor-fit column. Reject it.
- Fewer than ~10 photographable candidates — the group floor is 4, and two
  groups of four is the smallest pack worth quizzing.
- Harvesting words from the user's own vocabulary. That is the inverse problem
  (words → pack, not theme → words), and it was measured at 10–12% yield —
  a one-time hand-authored candidates file through `scripts/install_pack.py`
  beats building anything.
- Generated images, where the fix is the prompt.

## Cross-references

- `.claude/skills/eos-picture-pack-review/SKILL.md` — the by-eye pass this
  hands off to; also the duplicate-detection step.
- `apps/public/englishos/dictionary/PICTURE-PACKS.md` — the measured
  Wikimedia API constraints behind the fetcher.
- `.claude/rules/audits.md` — a clean fetch is the "green because it checks
  nothing" shape; verification and correctness are different questions.
