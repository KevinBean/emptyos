"""soundcheck — hand-authored content for the connected-speech dimension.

Read by the offline bank generator only; the runtime never imports it. Owns the
one dimension that cannot be derived from a dictionary.

## Why this file exists at all

cmudict is a *citation-form* dictionary: it records how a word is pronounced
when said alone and carefully. Connected speech is what happens when words stop
being said alone — the /t/ in "water" taps, "want to" collapses to one word, and
a final consonant slides onto the next vowel. None of that is in the corpus, and
generating it from one would be inventing data and calling it derived. So this
dimension is hand-written, and says so: every item here carries ``src: "hand"``.

## The constraint that shaped the item design

A text-to-speech voice reads carefully. Ask it for "want to" and it says *want
to*, so a drill built on "listen for the reduction" would be testing audio the
learner will never hear. The way round it is to make the **reduced spelling the
stimulus**: TTS given "wanna" produces /ˈwɑnə/, which is exactly the sound a
learner has to decode in the wild, and the question becomes what that sound
*means*. That is the real skill, and it happens to be the one TTS can support.

Verified 2026-08-15 that edge-tts renders these as distinct audio rather than
falling back to a spelling pronunciation.

## Accuracy notes

The flap pairs are the ones that genuinely merge in General American. Deliberately
excluded: *writer/rider*, because Canadian raising keeps them apart for a large
share of American speakers, and *putting/pudding*, because "putting" is two
different words. An item that is only sometimes true teaches a learner to distrust
the app.
"""

from __future__ import annotations

# ── Reductions ────────────────────────────────────────────────────
# (spoken form, what it means, two wrong readings, note)
#
# The spoken form is what the voice says; the learner picks what it means.
# Distractors are wrong *readings of the same sound*, not random phrases — a
# distractor nobody would ever hear turns the round into a reading test.

REDUCTIONS: list[tuple] = [
    ("gonna", "going to", "gone to", "go on to",
     "The most common reduction in spoken English. Never written in formal prose."),
    ("wanna", "want to", "wanted to", "won't",
     "Two words collapse to two syllables and the /t/ disappears entirely."),
    ("gotta", "got to", "go to", "get a",
     "Usually means 'have to' — 'I gotta go' is 'I have got to go'."),
    ("hafta", "have to", "had to", "half to",
     "The /v/ turns voiceless before /t/, so 'have to' sounds like 'haf to'."),
    ("hasta", "has to", "had to", "haste",
     "Same devoicing as 'hafta', one person further along."),
    ("oughta", "ought to", "out of", "auto",
     "'You oughta know' — the /t/ of 'ought' taps into the next syllable."),
    ("lemme", "let me", "let him", "lemon",
     "The /t/ assimilates completely into the /m/."),
    ("gimme", "give me", "give him", "gym",
     "Same assimilation as 'lemme', with /v/ vanishing."),
    ("kinda", "kind of", "kind to", "kinder",
     "'Of' reduces to a bare schwa, which is where most of these come from."),
    ("sorta", "sort of", "sort to", "sorter",
     "Same shape as 'kinda' — 'of' is almost never said in full."),
    ("outta", "out of", "out to", "otter",
     "'Get outta here' — two words, one and a half syllables."),
    ("lotta", "lot of", "lot to", "later",
     "'A lotta people' — the /f/ of 'of' disappears before a consonant."),
    ("coulda", "could have", "could of", "cooler",
     "The reason people write 'could of' — it is what 'could have' sounds like."),
    ("woulda", "would have", "would of", "wooden",
     "'Have' reduces to a schwa after a modal. Never 'would of' in writing."),
    ("shoulda", "should have", "should of", "shoulder",
     "Same as 'coulda' and 'woulda'. All three reduce 'have' to nothing."),
    ("musta", "must have", "must of", "mustard",
     "'He musta left' — the whole verb 'have' becomes one unstressed vowel."),
    ("dunno", "don't know", "did not know", "dono",
     "The /t/ of 'don't' and the /n/ of 'know' merge into one nasal."),
    ("gotcha", "got you", "got your", "gouge",
     "/t/ + /j/ becomes /tʃ/ — the same change that makes 'nature' from 'nat-'."),
    ("betcha", "bet you", "better", "beach",
     "Same /t/ + /j/ merge. 'I betcha' is two words, not one."),
    ("whatcha", "what are you", "what you", "watch",
     "Three words. This is why fast questions are hard to catch."),
    ("didja", "did you", "did she", "ditch",
     "/d/ + /j/ becomes /dʒ/, the voiced twin of the 'gotcha' change."),
    ("wouldja", "would you", "would she", "wooden",
     "Same voiced merge — every 'you' after a /d/ does this."),
    ("cuppa", "cup of", "cup to", "copper",
     "'Of' again, this time swallowed entirely into the next word."),
    ("c'mon", "come on", "common", "come in",
     "The vowel of 'come' disappears, leaving one syllable and a half."),
    ("'em", "them", "him", "am",
     "The /ð/ drops. 'Tell 'em' is far more common in speech than 'tell them'."),
    ("ya", "you", "yeah", "your",
     "'You' unstressed is a schwa — 'see ya', 'thank ya'."),
]

# ── Flapped /t/ and /d/ ───────────────────────────────────────────
# (word a, word b, do they merge in General American, note)
#
# Between two vowels where the second is unstressed, American English taps both
# /t/ and /d/ to the same sound. Some pairs therefore become genuine homophones,
# which is a fact learners are rarely told and often disbelieve.
#
# The "different" rows matter as much as the "same" ones: without them the answer
# is always yes and the shape stops measuring anything.

FLAP_PAIRS: list[tuple] = [
    ("latter", "ladder", True,
     "Both tap to the same sound between the vowels. Genuinely identical."),
    ("atom", "Adam", True,
     "A real homophone pair in American speech, and a surprise to most learners."),
    ("metal", "medal", True,
     "Olympic medal, sheet metal — in speech, the same word."),
    ("petal", "pedal", True,
     "Flower or bicycle: only context separates them."),
    ("bitter", "bidder", True,
     "The /t/ and /d/ both become the same quick tap."),
    ("seated", "seeded", True,
     "Both are /ˈsiːɾɪd/. A seeded player and a seated one sound alike."),
    ("betting", "bedding", True,
     "Same tap, same vowel — nothing left to tell them apart."),
    ("matter", "madder", True,
     "'What's the matter' and 'much madder' share their middle sound."),
    ("traitor", "trader", True,
     "One betrays, one buys. In speech they are the same."),
    ("coating", "coding", True,
     "A coat of paint or a line of software — identical when spoken."),
    ("hearty", "hardy", True,
     "Both taps land in the same place after the r-coloured vowel."),
    ("writing", "riding", True,
     "Often identical, though some speakers keep the vowels slightly apart."),
    # Not the same — the vowel differs, so the tap changes nothing.
    ("latter", "later", False,
     "Both tap, but the vowels differ: 'latter' is /æ/ and 'later' is /eɪ/."),
    ("bitter", "better", False,
     "The tap is the same; /ɪ/ against /ɛ/ is what keeps them apart."),
    ("atom", "autumn", False,
     "Same consonant tap, different first vowel — /æ/ against /ɔ/."),
    ("medal", "middle", False,
     "The tap does not help here: the vowels and the endings both differ."),
]

# ── Linking ───────────────────────────────────────────────────────
# (phrase, the sound that carries over, two wrong answers, note)
#
# A final consonant attaches to a following vowel, so the word boundary moves.
# This is knowledge rather than perception — a careful voice may not link at all
# — so these items work by eye as well as by ear.

LINKING: list[tuple] = [
    ("turn off", "n", "r", "f",
     "The /n/ carries over: it sounds like 'tur-noff'."),
    ("an apple", "n", "p", "l",
     "The /n/ of 'an' becomes the first sound of the next syllable."),
    ("far away", "r", "w", "y",
     "In a rhotic accent the /r/ links; without it a glide appears instead."),
    ("hold on", "d", "l", "n",
     "'Hol-don' — the /d/ moves across the boundary."),
    ("pick it up", "k", "t", "p",
     "Two links in three words: 'pi-ki-tup'."),
    ("give up", "v", "g", "p",
     "The /v/ attaches to 'up', which is why it is easy to miss."),
    ("come in", "m", "k", "n",
     "'Co-min' — one of the most common linked phrases in English."),
    ("this evening", "s", "th", "v",
     "The /s/ links, so the phrase has no audible gap at all."),
    ("look at", "k", "l", "t",
     "'Loo-kat'. Learners often hear a word they do not recognise."),
    ("made it", "d", "m", "t",
     "The /d/ taps AND links — two processes in one short phrase."),
    ("first of all", "t", "f", "v",
     "The /t/ carries into 'of', which itself reduces to a schwa."),
    ("keep it", "p", "k", "t",
     "'Kee-pit' — the boundary you see is not the boundary you hear."),
]
