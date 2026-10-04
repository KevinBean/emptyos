"""Unit tests for emptyos.sdk.prose_lint — pure module, no daemon needed.

Run: python -m pytest tests/test_sdk_prose_lint.py -v
"""

from __future__ import annotations

import pytest

from emptyos.sdk.prose_lint import lint_prose, strip_markdown


def _rules(report: dict) -> set[str]:
    return {f["rule"] for f in report["findings"]}


# ── healthy calibration — the voice guide's own example sentences must pass ──

HEALTHY = """
The model sets a ceiling on what's possible; the harness decides how much of
that ceiling you actually get. The uncomfortable version is that my first
instinct was to publish the table that flattered EmptyOS. Easy to do, and wrong.
A correct note sitting next to drifted code is worse than no note at all,
because it lies confidently. The lesson is the same one power-systems work
drilled into me: control your variables, measure something you can't argue
with, and be most suspicious of the result you like best.
"""


def test_healthy_prose_lints_clean():
    report = lint_prose(HEALTHY, source="calibration")
    assert report["findings"] == []
    assert report["metrics"]["words"] > 50


# ── banned patterns ──────────────────────────────────────────────────────────

def test_banned_hype_detected():
    report = lint_prose("This revolutionary tool will supercharge your workflow.")
    rules = _rules(report)
    assert "banned-hype" in rules
    assert all(f["severity"] == "high" for f in report["findings"]
               if f["rule"].startswith("banned-"))


def test_banned_opener_detected():
    report = lint_prose("I'm excited to announce a new thing today.")
    assert "banned-opener" in _rules(report)


# Real sentences from the published telegram-pocket-console post, which the old
# bare-stem `unlock` rule flagged 4x as "hype vocabulary". The post is about a
# session lock; every use is literal. Known-healthy calibration (audits.md).
LITERAL_UNLOCK = """
Nothing is written until I unlock the session and tap Apply.
Once that window expires, the first inbound message only triggers the password
prompt. On success, the password message is deleted and the session unlocks for
another window. Voice notes cannot unlock the bridge. That is intentional.
Once unlocked, voice notes take the same path as text.
"""


def test_literal_unlock_not_flagged():
    report = lint_prose(LITERAL_UNLOCK, source="telegram-pocket-console")
    assert "banned-hype" not in _rules(report)


@pytest.mark.parametrize("text", [
    "This will unlock your potential as a builder.",
    "The integration unlocks new possibilities for growth.",
    "A single change unlocked untapped value across the funnel.",
    "Automation unlocks the power of your data.",
])
def test_hype_unlock_still_flagged(text: str):
    report = lint_prose(text)
    assert "banned-hype" in _rules(report)


# ── structural AI-tells ──────────────────────────────────────────────────────

def test_not_x_its_y_over_threshold_flagged():
    text = " ".join(
        ["The tool isn't a toy. It's a workbench."] * 4
        + ["Some ordinary filler sentence to add words."] * 5
    )
    report = lint_prose(text)
    assert "not-x-its-y" in _rules(report)


def test_not_x_its_y_single_use_allowed():
    text = (
        "The tool isn't a toy. It's a workbench. "
        + "Plain sentences follow and nothing else repeats the pattern. " * 6
    )
    report = lint_prose(text)
    assert "not-x-its-y" not in _rules(report)


def test_pivot_matches_through_closing_quote():
    text = " ".join(
        ["The shape is not 'AI instead of UI.' It's the UI as an entry point."] * 4
    )
    report = lint_prose(text)
    assert report["metrics"]["not_x_its_y"] >= 4


def test_anaphora_triple_flagged():
    text = (
        "Not by guessing pixels. Not by pretending to click. "
        "Not by holding a backend token. The rest is ordinary prose."
    )
    report = lint_prose(text)
    assert "anaphora-triple" in _rules(report)


def test_thats_the_openers_thresholded():
    text = (
        "That's the shift that matters. Something else happens next. "
        "That's the part I'd defend. Another plain sentence sits here. "
        "That's the third one, which crosses the ceiling."
    )
    report = lint_prose(text)
    assert "thats-the-opener" in _rules(report)


def test_thats_the_openers_two_allowed():
    text = (
        "That's the axis I care about. Ordinary sentence follows. "
        "That's the pattern worth keeping. Nothing else repeats it."
    )
    report = lint_prose(text)
    assert "thats-the-opener" not in _rules(report)


def test_ai_filler_detected():
    report = lint_prose("It's worth noting that this works. Moreover, it scales.")
    assert "ai-filler" in _rules(report)


# ── markdown stripping ───────────────────────────────────────────────────────

def test_frontmatter_code_and_images_ignored():
    doc = (
        "---\n"
        "title: revolutionary journey\n"
        "---\n\n"
        "Plain honest prose here.\n\n"
        "```python\n"
        "x = 'supercharge'  # revolutionary\n"
        "```\n\n"
        "![a revolutionary game-changing caption](pic.png)\n\n"
        "More plain prose after the image, measured and calm.\n"
    )
    report = lint_prose(doc)
    assert report["findings"] == []


def test_strip_markdown_preserves_line_numbers():
    doc = "---\ntitle: t\n---\n\n# Heading\n\nBody line one.\n"
    lines = strip_markdown(doc)
    texts = {t for _, t in lines}
    assert "Heading" in texts
    assert "Body line one." in texts
    body_line = next(n for n, t in lines if t == "Body line one.")
    assert body_line == 7  # 1-based line in the original document


def test_finding_line_numbers_point_into_original():
    doc = "---\na: b\n---\n\nFine first line.\n\nThis revolutionary idea lands here.\n"
    report = lint_prose(doc)
    hits = [f for f in report["findings"] if f["rule"] == "banned-hype"]
    assert hits and hits[0]["line"] == 7


# ── metrics ──────────────────────────────────────────────────────────────────

def test_broken_image_embed_flagged():
    # The real incident: a `[DO:]` inside the caption truncated the embed.
    doc = "Intro line.\n\n![The card renders the [DO:] token diff|637](media/x.png)\n"
    report = lint_prose(doc)
    hits = [f for f in report["findings"] if f["rule"] == "broken-image-embed"]
    assert hits and hits[0]["severity"] == "high" and hits[0]["line"] == 3


def test_healthy_embed_and_wikilink_not_flagged():
    doc = (
        "![a clean caption with (parens) and `code`|637](x.png)\n\n"
        "![[obsidian-embed_MD5.png]]\n\n"
        "```\n![DO:] inside a code fence](y.png)\n```\n"
    )
    report = lint_prose(doc)
    assert "broken-image-embed" not in {f["rule"] for f in report["findings"]}


def test_em_dash_metric_counted():
    report = lint_prose("One thing — and another — plus a third thing to say.")
    assert report["metrics"]["em_dashes"] == 2


def test_empty_input_safe():
    report = lint_prose("")
    assert report["findings"] == []
    assert report["metrics"]["words"] == 0


# ── spoken register (spoken=True only) ──────────────────────────────────────
# Both directions are pinned per audits.md: a checker that has only ever been
# watched going green has proved nothing. Calibration sample is the vault's own
# interview phrase bank (Phrase Bank family G) — the standard these reproduce.

SPOKEN_HEALTHY = """
I'm a power engineer with nine years in HV cable systems, most recently
network modelling. Three things I'd want you to know: the rating work, the
earthing work, and the software. Good question. Let me give you a concrete
example rather than a general answer. I haven't worked with that directly.
The closest thing I've done is cable rating. I'd be guessing if I gave you a
number. What I can tell you is how I'd find out.
"""

# The register a real phone-screen brief shipped with, 2026-08-19.
SPOKEN_DEFECT = (
    "I am a power engineer, fourteen years. It is actually why I put this one "
    "in: it is the first posting I have seen that names HV. I am not a "
    "protection-and-control engineer; I would not claim secondary systems."
)


def _spoken_rules(report: dict) -> set[str]:
    return {f["rule"] for f in report["findings"]
            if f["rule"].startswith(("contraction", "spoken"))}


def test_spoken_healthy_is_quiet():
    report = lint_prose(SPOKEN_HEALTHY, spoken=True)
    assert _spoken_rules(report) == set()
    assert report["metrics"]["contraction_ratio"] >= 0.5


def test_spoken_contraction_free_flagged():
    report = lint_prose(SPOKEN_DEFECT, spoken=True)
    assert "contraction-rate" in _spoken_rules(report)
    assert report["metrics"]["contractions_used"] == 0


def test_spoken_semicolon_flagged():
    report = lint_prose(SPOKEN_DEFECT, spoken=True)
    assert "spoken-semicolon" in _spoken_rules(report)


def test_spoken_long_sentence_flagged():
    long_one = "I " + "really " * 30 + "do think so."
    report = lint_prose(long_one, spoken=True)
    assert "spoken-sentence-length" in _spoken_rules(report)


def test_spoken_short_sample_not_judged():
    """Below CONTRACTION_MIN_OPPS the sample is noise — must stay quiet."""
    report = lint_prose("It is fine.", spoken=True)
    assert "contraction-rate" not in _spoken_rules(report)


def test_curly_apostrophes_counted_as_contractions():
    """Authored prose uses U+2019; the rule must not read it as spelled-out."""
    report = lint_prose(
        "I’m here and I’d go and it’s fine and that’s all.",
        spoken=True,
    )
    assert report["metrics"]["contractions_used"] == 4
    assert "contraction-rate" not in _spoken_rules(report)


def test_overlapping_contractible_phrases_count_once():
    """"I have not" contains "I have" AND "have not" — one point, not two.

    Counting each pattern separately made the denominator depend on whether the
    text was already contracted: the same sentence scored 0/2 spoken out and
    1/1 contracted, so the ratio was not comparable between the two versions.
    """
    pad = " Padding one. Padding two. Padding three."
    spelled = lint_prose("I have not run it." + pad, spoken=True)["metrics"]
    contracted = lint_prose("I haven't run it." + pad, spoken=True)["metrics"]
    assert spelled["contraction_opportunities"] == contracted["contraction_opportunities"] == 1
    assert spelled["contraction_ratio"] == 0.0
    assert contracted["contraction_ratio"] == 1.0


def test_spoken_off_is_byte_identical():
    """The regression contract: spoken=False changes nothing at all."""
    assert lint_prose(SPOKEN_DEFECT) == lint_prose(SPOKEN_DEFECT, spoken=False)


def test_spoken_rules_do_not_leak_into_default():
    report = lint_prose(SPOKEN_DEFECT)
    assert _spoken_rules(report) == set()
    assert "contraction_ratio" not in report["metrics"]
