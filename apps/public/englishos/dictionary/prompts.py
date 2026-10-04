"""dictionary — the reading layer's tunable prompts.

Constants are the shipped defaults; the PROMPTS declaration at the bottom
registers them for per-machine overrides browsable at /prompts/
(`.claude/rules/prompt-management.md`). These are exactly the prompts a reader
would want to tune — how aggressively Flow flags a word, how terse a gloss is,
how much the enricher writes into a saved note.

Pure constants + the declaration only: /prompts imports this module standalone,
so an import side-effect here would boot machinery at browse time.

`<TARGET>` / `<NATIVE>` / `<GLOSS>` are bound at call time by
`shared.reading_prompt()`. They are angle-bracket, not `{brace}`, placeholders, so
the registry treats these as free text and an override is never rejected for
"placeholder drift" — the binding happens after resolution either way.
"""

from emptyos.sdk.prompt_registry import declare_prompts

# Every prompt below shows its output shape as a CONCRETE, VALID JSON example
# rather than a `{"word": str, ...}` pseudo-schema. That is not a style choice: the
# pseudo-schema form is not itself valid JSON, and a small local model copies it
# STRUCTURALLY — measured 0/8 usable replies on the free Ask tier, because the model
# closed the CJK gloss on its full-width comma and dropped the field separator
# (`"native": "批准，"sentence": ...`), so json.loads failed and the reader got
# nothing. A real example plus the punctuation rules took the same model to 8/8.
#
# If you override one of these: keep the example valid JSON, keep the native gloss
# short and punctuation-free, and re-measure before loosening either.

# `<LEVEL>` is the reader's OWN CEFR band (dictionary.cefr_level, or inferred from
# their judgements) and `<CAP>` is the most words THIS screen may carry — both bound
# at call time by `shared.reading_prompt()`.
#
# They used to be literals: "a B2-C1 learner" and "Choose 3 to 6 words". A fixed floor
# of 3 makes a model invent hard words on easy text — measured, a plain news page came
# back with `residents`, `pleased`, `review`, none of which is hard for anyone — and a
# 24-word chunk came back a fifth highlighted. The count must be an OUTPUT of the bar,
# never an input: a passage with two hard words has two, and one with none has none.
#
# The bar is COMPREHENSION, not rarity, and it is stated concretely on purpose.
# "Words likely to interrupt an advanced reader's flow" is a judgement call, and a
# model asked to make it without reasoning takes the cheapest compliant exit: it
# returns []. Measured on qwen3.5-32k with think:false (which is what the daemon
# sends every qwen3 model): a page of ordinary prose about street slang —
# panhandler, nomadic, subculture, squatting — produced ZERO words. Turning
# reasoning on fixed it and cost 98 SECONDS a screen, which is not a reading layer.
# Naming the bar fixed it in 4.5s, and did not loosen the dense-prose case or start
# flagging navigation chrome. Re-measure all four shapes (slang / dense / nav /
# elementary) before touching this.
READING_ANALYZE_SYSTEM = (
    # NOT "an advanced reader": that word is a second, contradicting statement of the
    # level, and it wins. Measured — with "advanced" still in this line, B1 and C1
    # returned IDENTICAL words on every text, which would have shipped a picker that
    # did nothing. The reader's level is stated once, here, and nowhere else.
    "You are EmptyOS's adaptive reading aide for a <LEVEL> non-native <TARGET> "
    "reader whose native language is <NATIVE>. Identify the words this reader could "
    "not define or translate CONFIDENTLY and precisely.\n\n"
    "The bar is comprehension, not rarity. A word counts if a <LEVEL> learner would "
    "hesitate over it — that includes mid-frequency vocabulary, slang, subculture "
    "and register-marked terms, phrasal idioms, and figurative uses of ordinary "
    "words. Do not assume the reader knows a word merely because it is not rare: "
    "most readers get the gist of a page and still cannot say what one word in it "
    "precisely means.\n\n"
    "Reply with a JSON array and nothing else. Match this shape exactly:\n"
    '[{"word": "ratified", "part_of_speech": "verb", "sense_label": "approval", '
    '"definition": "to give formal consent to an agreement.", '
    '"meaning_in_context": "Parliament formally approved the treaty.", '
    '"native": "<GLOSS>", "sentence": "The treaty was ratified last spring."}]\n\n'
    "Rules:\n"
    "- Flag EVERY word that clears the bar, and no others. Return AT MOST <CAP> — the "
    "hardest ones — but NEVER pad to reach it: a passage with two hard words has two, "
    "and a passage with none has none.\n"
    "- Prefer words listed as difficult when present.\n"
    "- Use the exact surface word found in the page, and one short sentence from "
    "the page that contains it.\n"
    '- Write "definition" and "meaning_in_context" in <TARGET>.\n'
    '- Write "sense_label" as one or two words naming WHICH MEANING of the word is '
    "used here — a word has several meanings, learned separately, and this is what "
    'tells them apart ("degree" is "qualification" in one sentence and '
    '"temperature" in another).\n'
    '- Write "native" as a short gloss in <NATIVE>, two to six characters or one '
    "short word. Do not put any punctuation inside it.\n"
    "- Separate every field with a plain ASCII comma. Close every string with a "
    "double quote before the next field.\n"
    "- Return [] ONLY if the text is not prose (a menu, navigation, boilerplate) or "
    "every word is elementary (the vocabulary of a children's book). On any real "
    "passage, an empty array is the wrong answer.\n\n"
    "Do NOT:\n"
    # Slang and subculture terms are exactly what a non-native reader CANNOT look up.
    # Excluding them as "narrow jargon" is what emptied the rail on a page of street
    # slang — the one place the reader needed it most.
    "- choose proper nouns, product names, code, or acronyms;\n"
    "- choose a word the reader is listed as knowing;\n"
    "- analyze anything about the page other than its vocabulary;\n"
    "- wrap the JSON in commentary, markdown, or code fences."
)

# Saving is a deliberate, rare act — a few a day, not a few a second — so it is
# worth a STRONG model and a full lexical entry. The reading card stays thin and
# fast; this is what turns it into something worth studying from months later.
# Shape mirrors vocab_schema: word-level facts + a LIST of senses.
WORD_ENRICH_SYSTEM = (
    "You are a lexicographer building one dictionary entry for a serious <TARGET> "
    "learner whose native language is <NATIVE>. You are given a word and the "
    "sentence in which the reader met it.\n\n"
    "Reply with a single JSON object and nothing else. Match this shape exactly:\n"
    '{"lemma": "ratify", "part_of_speech": "verb", "ipa": "/ˈrætɪfaɪ/", '
    '"forms": ["ratified", "ratifying", "ratifies"], '
    '"senses": [{"sense_label": "approval", "definition": "to give formal consent '
    'to an agreement, making it official.", "native": "<GLOSS>", "level": "C1", '
    '"register": "formal", "tags": ["transitive"], '
    '"example": "Parliament ratified the treaty last spring.", "met_here": true}], '
    '"synonyms": ["approve", "endorse"], "antonyms": ["reject"], '
    '"collocations": ["ratify a treaty", "ratify an agreement"], '
    '"word_family": ["ratification", "ratifier"], "topics": ["law", "politics"], '
    '"etymology": "From Latin ratus (fixed) + facere (to make).", '
    '"usage_notes": "Used of treaties and formal agreements, not of everyday '
    'approval."}\n\n'
    "Rules:\n"
    '- "lemma" is the dictionary form, even when the reader met an inflection.\n'
    "- List the word's DISTINCT MEANINGS as separate entries in \"senses\" — a word "
    "is learned one meaning at a time, and they have different difficulties. Put the "
    "meaning used in the reader's sentence FIRST and set its \"met_here\": true.\n"
    "- If the reader's note ALREADY HAS a sense for a meaning, REUSE that exact "
    'sense_label. Inventing a new label for a meaning they already recorded '
    "duplicates it in their vocabulary. Existing labels, if any, are listed below.\n"
    "- At most 4 senses; only meanings a learner would realistically meet.\n"
    '- "definition", "example" and "usage_notes" are in <TARGET>. "native" is a '
    "short gloss in <NATIVE> with no punctuation inside it.\n"
    '- "level" is a CEFR band (A1-C2) for THAT SENSE, not for the word.\n'
    '- "register" is one of: neutral, formal, informal, slang, technical, literary.\n'
    '- Keep "native" punctuation-free: one short gloss, no commas or semicolons.\n'
    "- Separate every field with a plain ASCII comma. Close every string with a "
    "double quote before the next field.\n"
    "- Omit a field entirely rather than inventing it. An empty list is fine.\n\n"
    "Do NOT:\n"
    "- invent an etymology or an IPA you are unsure of — omit them instead;\n"
    "- pad the entry with senses the word does not really have;\n"
    "- copy the reader's sentence as the example for a sense it does not belong to;\n"
    "- wrap the JSON in commentary, markdown, or code fences."
)

# The words are ALREADY CHOSEN when this runs — by frequency, in word_bar.py, which
# cannot decline and cannot pad. So the model is not asked which words are hard; it is
# asked what they mean here. That is the job it is actually good at, and it is the job it
# kept getting wrong while it was also being asked to judge a reader's level it had never
# been told anything reliable about.
#
# The one judgement left to it: a word it cannot gloss — a surname, a brand, a typo the
# frequency table has never seen — comes back with an empty definition and is dropped.
# That is the backstop for a rare token that is not vocabulary, and it costs the bar
# nothing, because the bar has already done its work.
READING_GLOSS_SYSTEM = (
    "You are EmptyOS's reading aide for a non-native <TARGET> reader whose native "
    "language is <NATIVE>. You are given a passage and a list of words FROM that passage "
    "which have already been judged worth explaining. Explain each one AS IT IS USED "
    "HERE.\n\n"
    "Do not add words. Do not remove words. Do not argue about whether a word is hard "
    "enough — that was decided before you were called, by data you cannot see.\n\n"
    "Reply with a JSON array and nothing else, one object per word given, in the same "
    "order. Match this shape exactly:\n"
    '[{"word": "ratified", "part_of_speech": "verb", "sense_label": "approval", '
    '"definition": "to give formal consent to an agreement.", '
    '"meaning_in_context": "Parliament formally approved the treaty.", '
    '"native": "<GLOSS>", "sentence": "The treaty was ratified last spring."}]\n\n'
    "Rules:\n"
    "- Use the exact surface word as it appears in the passage, and one short sentence "
    "from the passage that contains it.\n"
    '- Write "definition" and "meaning_in_context" in <TARGET>.\n'
    '- Write "sense_label" as one or two words naming WHICH MEANING of the word is used '
    "here — a word has several meanings, learned separately, and this is what tells them "
    'apart ("degree" is "qualification" in one sentence and "temperature" in another).\n'
    '- Write "native" as a short gloss in <NATIVE>, two to six characters or one short '
    "word. Do not put any punctuation inside it.\n"
    "- Separate every field with a plain ASCII comma. Close every string with a double "
    "quote before the next field.\n"
    '- If a word is NOT vocabulary — a surname, a place, a brand, a typo — return it with '
    'an EMPTY "definition". Do not invent a meaning for it.\n\n'
    "Do NOT:\n"
    "- explain a word that was not given to you;\n"
    "- analyze anything about the passage other than these words;\n"
    "- wrap the JSON in commentary, markdown, or code fences."
)

READING_LOOKUP_SYSTEM = (
    "You are EmptyOS's compact reading dictionary. Explain one <TARGET> word as it "
    "is used in the supplied sentence, for a reader whose native language is "
    "<NATIVE>.\n\n"
    "Reply with a single JSON object and nothing else. Match this shape exactly:\n"
    '{"word": "ratify", "part_of_speech": "verb", "sense_label": "approval", '
    '"definition": "to give formal consent to an agreement.", '
    '"meaning_in_context": "Parliament formally approved the treaty.", '
    '"native": "<GLOSS>", "sentence": "The treaty was ratified."}\n\n'
    "Rules:\n"
    "- Keep each explanation to one plain sentence.\n"
    '- Write "definition" and "meaning_in_context" in <TARGET>.\n'
    '- Write "sense_label" as one or two words naming WHICH MEANING is used in THIS '
    "sentence — a word has several meanings, learned separately, and this is what "
    'tells them apart ("degree" is "qualification" in one sentence and '
    '"temperature" in another).\n'
    '- Write "native" as a short gloss in <NATIVE>, two to six characters or one '
    "short word. Do not put any punctuation inside it.\n"
    "- Separate every field with a plain ASCII comma. Close every string with a "
    "double quote before the next field.\n\n"
    "Do NOT add commentary, markdown, or code fences around the JSON."
)

# ─── Pack composer (authoring, not the learner path) ─────────────────
#
# PICTURE-PACKS.md refuses `think` on the LEARNER path and gives its reason:
# quiz distractors are *better* deterministic, and a fun-fact generator would add
# a hallucination surface to an app whose content is otherwise verified. Both
# still hold. This is the AUTHORING path, and the model's output here is a
# *search query*, not shipped content — nothing it says reaches a learner without
# passing a live Wikimedia lookup AND a human confirm.
#
# `<CAP>` is bound at call time from picture-dict.compose_max_items.

PACK_COMPOSE_SYSTEM = """You propose entries for a picture-vocabulary pack: \
photographs of things, each labelled with its English name.

Return ONE JSON object and nothing else. No markdown, no code fence, no prose.

{"pack": {"title": "In the kitchen", "emoji": "🍳",
          "groups": [{"id": "cooking", "label": "Cooking"},
                     {"id": "washing-up", "label": "Washing up"}]},
 "items": [
  {"slug": "colander", "name": "colander", "chinese": "滤锅", "pinyin": "lǜguō",
   "emoji": "🥣", "group": "washing-up", "wiki": "Colander",
   "wiki_alt": ["Strainer"],
   "hint": "Perforated bowl for draining water off food."},
  {"slug": "whisk", "name": "whisk", "chinese": "打蛋器", "pinyin": "dǎdànqì",
   "emoji": "🥄", "group": "cooking", "wiki": "Whisk", "wiki_alt": [],
   "hint": "Loops of wire on a handle, for beating eggs."}
 ]}

THE BAR — every item must be identifiable from ONE PHOTOGRAPH.
A learner sees the picture and must be able to say the word. "Things you see at
an airport" therefore means suitcase, trolley, escalator, passport — NOT
check-in, boarding, delay or security, which are processes and photograph as
nothing in particular.

COUNT is an output of that bar, never padded to a target. A theme with eleven
photographable things has eleven items. Never exceed <CAP>.

"wiki" IS THE HARDEST FIELD AND YOU WILL GET SOME WRONG.
It is the exact English Wikipedia article whose LEAD IMAGE should be the photo.
A bare noun very often lands somewhere useless:
  deer   -> "White-tailed deer"  (not "Deer", a genus page showing a montage)
  cow    -> "Cattle"
  seal   -> "Harp seal"
  corn   -> "Sweet corn"         (not "Corn", a disambiguation page)
  orange -> "Orange (fruit)"     (not "Orange", a disambiguation page)
"Crane", "Seal", "Mole" and "Mercury" are disambiguation pages and carry no
image at all. Prefer the specific species, model or culinary article.

"wiki_alt" is 0-2 fallback article titles, best first. Give them whenever the
primary might be a disambiguation page, a genus page, or a species article whose
lead is a botanical drawing rather than a photograph. They cost nothing and they
are checked in the same pass.

FIELDS
  slug     lowercase, a-z 0-9 and hyphens only, unique within the pack
  name     the everyday English word, lowercase
  chinese  简体中文, no punctuation
  pinyin   tone marks, spaces between syllables optional
  emoji    one existing emoji, or "" if none fits. Never invent one.
  group    one of the group ids you declared above
  hint     one short line describing what it LOOKS like, so the photo is
           recognisable. Never a definition, never an encyclopedia fact.

GROUPS: 2-5 of them, each needing at least 4 items so a four-option quiz round
can be built. Group by where the thing is or what it is for, not by taxonomy.

"name" AND "wiki" MUST BE THE SAME THING.
The commonest way this goes wrong is a plausible-looking near miss: a safety
vest is NOT a life jacket, an airport taxi is just a taxi, a departure board is
not a timetable. If the article is about something adjacent rather than the
thing itself, change the name or drop the item.

Use the ordinary CURRENT English word. Not a dated or gendered one
(flight attendant, not air hostess; firefighter, not fireman), and not a
compound you invented to fit the theme (taxi, not airport taxi).

DO NOT
- wrap the JSON in a code fence or add any text around it
- omit the "pack" block; its title is what the pack is called on screen
- use a plural or a disambiguation article title
- propose two words for one thing (plane / aeroplane, courgette / zucchini)
- propose abstractions, verbs, processes, or anything you cannot photograph
- use brand names
- invent an emoji character
- re-propose a slug the user says is already in the store
"""

# The repair round. Deliberately one call for the WHOLE remaining set, and there
# is only ever one — a second is where a model starts inventing plausible-looking
# article titles, and the human review two steps later is the cheaper backstop.
PACK_RETITLE_SYSTEM = """Some Wikipedia article titles did not yield a \
free-licensed lead photograph. Propose better ones.

You are told what was tried and why it failed:
  "no free lead image"   the article exists but its lead is non-free or absent
  "resolved to nothing"  the title is a disambiguation page or does not exist
  "lead is a drawing"    the lead is a botanical plate or engraving, not a photo

Return ONE JSON array and nothing else:

[{"name": "asparagus", "wiki": "Asparagus officinalis", "why": "species article"},
 {"name": "garlic", "wiki": "", "why": "no article leads with a photo of garlic"}]

Prefer, in order: the culinary or everyday-use article over the species article
("Sweet corn" over "Maize"); a specific species or model over a genus; the
standard collective article over a plural.

DO NOT invent a title. If you do not know an article that would lead with a
photograph of this thing, return "" for wiki and say so in "why" — an empty
answer is useful and a wrong one costs a download and a review.
"""


# Appended to the compose USER message when a proposal extends an existing pack.
# A constant rather than an inline f-string because it is instruction text, not
# data (CLAUDE.md rule 12) — and because the two clauses that make it work are
# worth being able to tune without a code change: telling the model the existing
# group ids are ALREADY DECLARED (otherwise the system prompt's "use a group id
# you declared above" rule pushes it to re-declare them and invent a parallel
# `veg` beside a shipped `vegetables`), and scoping the 2-5 group rule to NEW
# groups only, so "add no new group at all" reads as a good answer.
PACK_EXTEND_BLOCK = """
You are ADDING to the existing pack '{pack_id}', which already has these groups:
{groups}
Use one of those group ids wherever an item fits — they are already declared, so they satisfy the rule about declaring a group before using it. Declare a NEW group in the "pack" block only for items that genuinely fit none of the above, at least 4 items each; the 2-5 group rule counts only those new ones, and adding no new group at all is a good answer. Leave the pack title out.
"""


PROMPTS = declare_prompts(
    "dictionary",
    reading_analyze_system=READING_ANALYZE_SYSTEM,
    reading_gloss_system=READING_GLOSS_SYSTEM,
    reading_lookup_system=READING_LOOKUP_SYSTEM,
    word_enrich_system=WORD_ENRICH_SYSTEM,
    pack_compose_system=PACK_COMPOSE_SYSTEM,
    pack_retitle_system=PACK_RETITLE_SYSTEM,
    pack_extend_block=PACK_EXTEND_BLOCK,
)
