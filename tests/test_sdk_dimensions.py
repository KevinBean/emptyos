"""Unit tests for the wellbeing-dimension keyword scanner.

Pure (no daemon). Guards the activity-vocabulary that the journal off-system
activity logger relies on: an un-logged jumper class read as Physical=0, so the
scanner must recognise common off-keyboard activity words and infer the right
dimension. See apps/public/standard/journal/app.py::_log_activity.
"""

import pytest

from emptyos.sdk import dimensions as D


def _infer(activity: str) -> str:
    """Mirror _log_activity's silent inference: top scan_text hit, else physical."""
    counts = D.scan_text(activity)
    return max(counts, key=counts.get) if any(counts.values()) else "physical"


@pytest.mark.parametrize(
    "word",
    ["jumper", "jumping", "dance", "dancing", "pilates", "barre",
     "aerobics", "trampoline", "boxing", "crossfit", "hiit"],
)
def test_new_physical_activity_words_score_physical(word):
    assert D.scan_text(word).get("physical", 0) >= 1, f"{word!r} not seen as physical"
    assert word in D.TAG_ALIASES["physical"]


def test_jumper_class_prose_counts():
    # The exact gap: a written "jumper class" must register Physical signal.
    assert D.scan_text("went to a jumper class this morning")["physical"] == 1


@pytest.mark.parametrize(
    "activity,expected",
    [
        ("jumper class", "physical"),
        ("coffee with Warwick", "social"),
        ("read a chapter", "intellectual"),
        ("meditation session", "spiritual"),
        ("random unknown thing", "physical"),  # fallback for the off-keyboard logger
    ],
)
def test_activity_logger_inference(activity, expected):
    assert _infer(activity) == expected


def test_explicit_tag_extracts():
    # The logger stamps #<dim>; extract must resolve it for the wheel (+2 weight).
    assert D.extract("🏃 jumper class #physical") == ["physical"]
