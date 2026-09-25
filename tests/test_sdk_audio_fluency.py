"""score_fluency — the shared oral-fluency scorer (Speaking Practice + dictionary).

Pins the one behaviour changed when it moved to the SDK: a repetition is an
IMMEDIATE repeat. The old rule counted any word pair seen twice anywhere and
charged a fluent description for ordinary phrases — measured on a clean
22-second description of a kettle, which it scored as 7 false starts.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.audio import (
    LONG_PAUSE_S,
    immediate_repetitions,
    score_fluency,
    speech_tokens,
)

KETTLE = ("This is a kettle. You find it in the kitchen, usually next to the sink and "
          "the toaster. It is made of metal and plastic. You fill it with water from the "
          "tap, then you switch it on, and after a few minutes the water boils. I use it "
          "every morning to make tea, and sometimes coffee. It is quite loud when the "
          "water boils. I pour the hot water into a mug. That is the kettle.")


def _reps(text):
    return immediate_repetitions(speech_tokens(text))


def test_ordinary_repeated_phrases_are_not_false_starts():
    assert _reps(KETTLE) == 0


@pytest.mark.parametrize("text,count", [
    ("the the cup", 1),                 # stutter
    ("I went I went home", 1),          # restart on a pair
    ("we we we go", 2),                 # a stutter held twice
    ("I went I went went", 2),          # restart, then a stutter after it
    ("I want to I want to go", 1),      # three-word restart
    ("a b a b a b", 2),                 # the pair restarted twice
    ("it is red and it is big", 0),     # same pair, not adjacent
    ("I know that that is true", 0),    # grammar, not a stutter
    ("she had had enough", 0),
    ("it was very very hot", 0),        # emphasis
    ("no no no", 0),
    ("", 0),
])
def test_immediate_repetitions(text, count):
    assert _reps(text) == count


def test_supplied_pauses_override_the_alignment():
    payload = {"word_alignment": [{"start": 0.0, "end": 0.4}, {"start": 3.0, "end": 3.5}]}
    from_alignment = score_fluency("a b c d e f", 4, payload)
    overridden = score_fluency("a b c d e f", 4, payload, pauses=[0.2])
    assert from_alignment["long_pause_count"] == 1
    assert overridden["long_pause_count"] == 0


def test_the_long_pause_line_is_inclusive():
    res = score_fluency("one two three four five", 3, pauses=[LONG_PAUSE_S, LONG_PAUSE_S - 0.01])
    assert res["long_pause_count"] == 1


def test_a_fluent_description_scores_high_and_a_hesitant_one_lower():
    fluent = score_fluency(KETTLE, 30)
    hesitant = score_fluency("um the the kettle um it is um I went I went", 30, pauses=[2.0, 1.5])
    assert fluent["score"] >= 4.5
    assert hesitant["score"] < fluent["score"]
    assert hesitant["repetitions_false_starts"] == 2


# ─── Words used / masked — one matcher for scoring and masking ───────

from emptyos.sdk.audio import mask_words, words_used  # noqa: E402


@pytest.mark.parametrize("said,name,hit", [
    ("I went to the cafe", "café", True),            # accents do not matter
    ("the CAFÉ was shut", "café", True),
    ("my e scooter", "e-scooter", True),              # hyphen = space
    ("the self-checkout", "self checkout", True),
    ("two rice cookers", "rice cooker", True),        # plural on the last word
    ("a knife block", "knife", True),
    ("tapes", "tap", False),
])
def test_words_used_matching(said, name, hit):
    assert bool(words_used(said, [name])) is hit


def test_a_longer_name_claims_its_words_first():
    assert words_used("pass the chef knife", ["knife", "chef knife"]) == ["chef knife"]
    assert words_used("the chef knife and a knife", ["knife", "chef knife"]) == ["knife", "chef knife"]


def test_masking_matches_exactly_what_scoring_matches():
    text = "Pass the chef knife, the knife, the Café, the cafe and my e scooter."
    names = ["knife", "chef knife", "café", "e-scooter"]
    assert mask_words(text, names) == "Pass the …, the …, the …, the … and my …."
    assert mask_words("Nothing here, just a tapestry.", ["tap"]) == "Nothing here, just a tapestry."
