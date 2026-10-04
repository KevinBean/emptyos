"""markitup — the rubric table, and validation of a review pass against it.

A *rubric* is the vocabulary one review speaks: an ordered tag set plus the id
of the system prompt that teaches a model to use it. Three ship today and
they are deliberately different shapes, which is why this is a table and not
a constant:

  * ``site``   — tags encode a *disposition* (KEEP / REFRAME / PROVE / FIX /
    OPTION). Suits reviewing someone else's site, where the useful output is
    "what would you do about this".
  * ``design`` — tags encode a *dimension* (focal / hierarchy / rhythm / …),
    lifted verbatim from ``.claude/skills/eos-page-design-review/SKILL.md`` so
    the skill and this app cannot drift apart. Suits reviewing our own pages,
    where the useful output is "which design property is wrong".
  * ``requirements`` — tags encode a *verdict on a clause* (UNCLEAR / WRONG /
    MISSING / CONFLICT / QUESTION / ACCEPT). Suits reviewing a rendered
    document, where a comment's pin sits on an id-leading row and the useful
    output is "what is wrong with SR-INP-06".

Pure data + pure helpers — no ``self``, no kernel access, no I/O, no prompt
text (that lives in ``prompts.py``; each rubric names its prompt by registry
key, so neither module imports the other — keeping the text there is CLAUDE.md
rule 12 and keeps both leaves).

``normalize_comments`` lives here rather than in ``shared.py`` because it is
rubric logic: it decides what counts as a usable comment *in this vocabulary*.
Putting it here leaves ``shared.py`` a true leaf with no sibling imports.

Unit-tested without a daemon (tests/test_unit_markitup.py).
"""

from __future__ import annotations


def _clean(v) -> str:
    return v.strip() if isinstance(v, str) else ""

# Every ``variant`` below must be a member of EOS_UI.STATUS_VARIANTS
# (eos-components.js) or the badge renders unstyled — see
# .claude/rules/shared-frontend.md on never hand-building the badge class.
# Colour is never the only signal: the tag label always renders beside the
# swatch, per .claude/rules/list-card-density.md.
#
# ``neutral`` is the one member that is NOT in STATUS_VARIANTS: it is what
# ``statusVariant`` returns for anything unrecognised, and ``.eos-badge-neutral``
# exists, so it is a legitimate deliberate choice rather than a typo. The test
# reads the real vocabulary out of eos-components.js and allows exactly this
# one addition — this list alone cannot police itself.
VALID_VARIANTS: frozenset[str] = frozenset({
    "active", "archived", "blocked", "completed", "draft",
    "fail", "idea", "pass", "published", "running", "shelved", "neutral",
})

RUBRICS: dict[str, dict] = {
    "site": {
        "label": "Site review",
        "prompt": "site_review_system",
        "hint": "Reviewing a site you did not build — disposition per observation.",
        "tags": [
            {"id": "keep", "label": "KEEP", "variant": "pass",
             "desc": "Works. Name it so a redesign does not throw it away."},
            {"id": "reframe", "label": "REFRAME", "variant": "idea",
             "desc": "The substance is right but the framing undersells or misdirects."},
            {"id": "prove", "label": "PROVE", "variant": "blocked",
             "desc": "A claim the reader is asked to take on faith. Name the evidence it needs."},
            {"id": "fix", "label": "FIX", "variant": "fail",
             "desc": "Concretely wrong or broken — a defect, not a preference."},
            {"id": "option", "label": "OPTION", "variant": "draft",
             "desc": "A defensible alternative. Explicitly not a mandate."},
        ],
    },
    "design": {
        "label": "Page design review",
        "prompt": "design_review_system",
        "hint": "Reviewing our own surface against the frontend design language.",
        "tags": [
            {"id": "focal", "label": "focal", "variant": "active",
             "desc": "What the eye lands on first, and whether that is the right thing."},
            {"id": "hierarchy", "label": "hierarchy", "variant": "running",
             "desc": "Whether relative importance is legible without reading."},
            {"id": "rhythm", "label": "rhythm", "variant": "idea",
             "desc": "Spacing cadence — repetition and interval down the page."},
            {"id": "weight", "label": "weight", "variant": "draft",
             "desc": "Type and colour weight carrying more or less than it should."},
            {"id": "density", "label": "density", "variant": "shelved",
             "desc": "Information per screen against what the task actually needs."},
            {"id": "affordance", "label": "affordance", "variant": "blocked",
             "desc": "Whether an interactive thing looks interactive, and reachable."},
            {"id": "restraint", "label": "restraint", "variant": "neutral",
             "desc": "Decoration that is not carrying meaning."},
        ],
    },
    # Reviewing a rendered requirements or specification document (a
    # `document` source). One disposition per statement, in the vocabulary a
    # requirements review actually uses; each anchor is a table row or heading
    # that opens with the requirement's id, so the comment carries the id.
    "requirements": {
        "label": "Requirements review",
        "prompt": "requirements_review_system",
        "hint": "Reviewing a requirements or specification document — one disposition per statement.",
        "tags": [
            {"id": "unclear", "label": "UNCLEAR", "variant": "draft",
             "desc": "Admits more than one reading, or uses a term the document never defines. Say which readings."},
            {"id": "wrong", "label": "WRONG", "variant": "fail",
             "desc": "States something false, or contradicts a source it cites. Name the source or the fact."},
            {"id": "missing", "label": "MISSING", "variant": "blocked",
             "desc": "A statement the document needs and does not have — a case, a limit, an error path, an owner."},
            {"id": "conflict", "label": "CONFLICT", "variant": "running",
             "desc": "Disagrees with another statement in the pack. Cite the other id."},
            {"id": "question", "label": "QUESTION", "variant": "idea",
             "desc": "Cannot be judged without an answer from the author or a stakeholder. Ask the question."},
            {"id": "accept", "label": "ACCEPT", "variant": "pass",
             "desc": "Clear, singular, verifiable and correct as far as the reviewer can tell. Worth recording so it is not re-litigated."},
        ],
    },
}

DEFAULT_RUBRIC = "site"


def rubric_ids() -> list[str]:
    """Every registered rubric id, in declaration order."""
    return list(RUBRICS)


def get_rubric(rubric_id: str | None) -> dict:
    """Resolve a rubric id to its record, falling back to the default.

    Fails soft on purpose: an unknown id from a stored review (a rubric that
    was renamed) must still render the review rather than 500 it.
    """
    return RUBRICS.get((rubric_id or "").strip().lower()) or RUBRICS[DEFAULT_RUBRIC]


def tag_ids(rubric_id: str | None) -> tuple[str, ...]:
    """The rubric's tag ids, in declaration order."""
    return tuple(t["id"] for t in get_rubric(rubric_id)["tags"])


def tag_meta(rubric_id: str | None, tag: str) -> dict | None:
    """One tag's record, or ``None`` when it is not in this rubric."""
    want = (tag or "").strip().lower()
    for t in get_rubric(rubric_id)["tags"]:
        if t["id"] == want:
            return t
    return None


def normalize_tag(rubric_id: str | None, raw: str) -> str | None:
    """Coerce a model-supplied tag to a canonical id, or ``None`` if unknown.

    Accepts the id or the display label, any case, with surrounding brackets or
    punctuation — models routinely answer ``"[FIX]"`` or ``"Fix:"`` where the
    menu said ``fix``. Returning ``None`` (rather than guessing a neighbour) is
    what lets the caller drop an uninterpretable comment instead of silently
    filing it under the wrong tag.
    """
    s = (raw or "").strip().strip("[](){}<>.:;,-— ").lower()
    if not s:
        return None
    for t in get_rubric(rubric_id)["tags"]:
        if s == t["id"] or s == t["label"].lower():
            return t["id"]
    return None


def rubric_menu(rubric_id: str | None) -> str:
    """The tag menu injected into a review prompt: ``- <id>: <desc>`` per line."""
    return "\n".join(
        f"- {t['id']}: {t['desc']}" for t in get_rubric(rubric_id)["tags"]
    )


# ── Validating a review pass against a rubric ────────────────────────

def raw_element_ids(raw) -> list[str]:
    """Every ``el`` the model named, before validation blanks the bad ones.

    Exists to keep two failure modes apart, which the stored review cannot:
    a comment with ``el: ""`` may mean the model *declined* to anchor (fine —
    some observations are about the whole view) or that it named an element
    that does not exist (a prompt problem). Both look identical afterwards.
    """
    rows = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    return [_clean(r.get("el")) for r in rows if isinstance(r, dict)]


def normalize_comments(
    raw,
    *,
    rubric_id: str,
    valid_els,
    cap: int = 12,
    start_n: int = 1,
) -> list[dict]:
    """Filter raw model output to well-formed comments and number them.

    ``raw`` may be a list or ``{"items": [...]}`` — both shapes models produce.
    A comment survives when it carries a tag this rubric knows and some prose.

    Unlike designer's ``normalize_annotations`` this does **not** dedupe by
    element: a review legitimately makes several points about one hero, and
    collapsing them to the first would silently discard paid-for output.

    An unrecognised ``el`` is blanked rather than dropping the whole comment — a
    page-level observation ("nothing here says what the company does") is a real
    review comment that simply has no single element to point at. It renders in
    the gutter with no pin until something places it.
    """
    rows = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    valid = set(valid_els or ())
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        tag = normalize_tag(rubric_id, _clean(r.get("tag")))
        if not tag:
            continue
        title = _clean(r.get("title"))
        body = _clean(r.get("body"))
        if not (title or body):
            continue
        el = _clean(r.get("el"))
        out.append({
            "n": start_n + len(out),
            "tag": tag,
            "el": el if el in valid else "",
            "title": title or body[:60],
            "body": body,
            "x": None,
            "y": None,
            "anchor": "none",
            "status": "open",
            "author": "ai",
        })
        if len(out) >= cap:
            break
    return out
