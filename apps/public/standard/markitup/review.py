"""markitup — the review pass: an anchor menu and page text in, comments out.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
one ``think`` call that writes a shot's comments, its untrusted-content
fencing, and the join back onto measured geometry.

The model never supplies a coordinate. It picks an ``el`` from the menu that
``capture.py`` measured, and ``shared.attach_points`` turns that into a pin.
That is the whole reason the pass is split this way: a comment whose element the
model invented is caught by ``normalize_comments`` and simply loses its pin,
rather than being drawn confidently in the wrong place.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: rubrics (leaf), shared (leaf), prompts (leaf).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk.utils import parse_llm_json
from emptyos.sdk.web_search import source_fencer

from .prompts import PROMPTS
from .rubrics import get_rubric, normalize_comments, normalize_tag, raw_element_ids, rubric_menu
from .shared import anchor_menu, attach_points, attach_refs, is_document_shot, source_line

if TYPE_CHECKING:
    from .app import MarkitupApp  # noqa: F401 — for type hints only


# ─── Bind to MarkitupApp class as ────────────────────────────────────
#   review_system  = _review.review_system
#   review_shot    = _review.review_shot
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def review_system(self, rubric_id: str) -> str:
    """The system prompt for one rubric, resolved once per run.

    Deliberately separate from :func:`review_shot` so a caller reviewing N shots
    resolves it **once before the loop**. Re-reading it per iteration would vary
    the prompt prefix between calls and forfeit provider-side prefix caching for
    every shot after the first (`.claude/rules/prompt-prefix-cache.md` rule 2).
    """
    key = get_rubric(rubric_id)["prompt"]
    base = getattr(PROMPTS, key)
    # The clause naming the fence markers must travel with the fence itself, or
    # the markers are decoration; source_fencer keeps the pair in one place.
    return source_fencer(self).system(base)


async def review_shot(
    self,
    *,
    shot: dict,
    anchors: list[dict],
    origin: dict,
    rubric_id: str,
    system: str,
    cap: int = 12,
    start_n: int = 1,
) -> dict:
    """Draft comments for one captured view.

    Returns ``{"comments": [...], "stats": {...}}``. The stats are not
    debugging leftovers: ``named`` vs ``unresolved`` is the only thing that
    separates "the model chose not to anchor this observation" from "the model
    invented element ids", and once the comments are stored those two look
    identical. A run that anchors nothing should be able to say which it was.

    Never raises on a bad model reply — an unparseable answer yields no comments
    for this shot, which the caller reports, rather than failing a run that has
    already paid for several other shots.
    """
    fencer = source_fencer(self)
    menu = anchor_menu(anchors)
    page_text = (shot.get("text") or "").strip()

    parts = [
        f"VIEW: {shot.get('title') or shot.get('url') or 'untitled'}",
        # A document shot names its source ref, not the loopback render URL;
        # a web shot its route. (Empty only for a shot with neither.)
        source_line(shot),
    ]
    parts = [p for p in parts if p]
    if shot.get("focus"):
        parts.append(f"WHAT TO ATTEND TO: {shot['focus']}")
    if shot.get("selector"):
        # A hint, not a boundary. The shot is the whole page and every menu id
        # is pinnable, so naming the region must not read as "ignore the rest" —
        # that would trade the old bug (comments that could not pin) for a new
        # one (a whole-page shot reviewed as if it were a crop).
        parts.append(
            f"The view is nominally about the `{shot['selector']}` region, but "
            "you are seeing the whole page and may comment anywhere on it."
        )
    parts.append(f"\nTAGS you may use (use the id, lowercase):\n{rubric_menu(rubric_id)}")
    # The menu is page-derived text (innerText / alt / placeholder / aria-label
    # / name), so on a client's site it is attacker-controlled and must be
    # fenced exactly like PAGE TEXT below. 300 anchors x 80 chars is ~24k
    # characters of untrusted text reaching a prompt that carries our authority.
    parts.append(
        "\nANCHOR MENU — copy an `el` id verbatim, or use \"\" for a point about "
        "the view as a whole:\n"
        + (fencer.wrap(menu, label=shot.get("url", ""))
           if menu else "(no addressable elements were found)")
    )
    if page_text:
        # Fetched page content is data, never instruction. It goes in the user
        # message, fenced — a system prompt would give it the app's authority.
        parts.append(
            "\nPAGE TEXT:\n"
            + fencer.wrap(page_text, label=shot.get("url", ""))
        )

    raw = await self.think(
        "\n".join(parts),
        system=system,
        domain=self.setting_or_config("markitup.think_domain", "text"),
        temperature=0.4,
    )
    text = raw if isinstance(raw, str) else str(raw)
    parsed = parse_llm_json(text, fallback=[])

    valid_els = {a.get("el") for a in (anchors or []) if isinstance(a, dict) and a.get("el")}
    comments = normalize_comments(
        parsed, rubric_id=rubric_id, valid_els=valid_els, cap=cap, start_n=start_n
    )
    rects = {
        a["el"]: a.get("rect")
        for a in (anchors or [])
        if isinstance(a, dict) and a.get("el")
    }
    attach_points(comments, rects, origin)
    # Refs only for a rendered document: `leading_id` is a generic
    # hyphen-number matcher, and on a site review a heading like "COVID-19
    # response" would otherwise export a fabricated requirement id.
    if is_document_shot(shot):
        attach_refs(comments, anchors)
    for c in comments:
        c["shot_id"] = shot.get("id", "")

    named = [e for e in raw_element_ids(parsed) if e]
    unresolved = sorted({e for e in named if e not in valid_els})
    # Tags the model used that this rubric does not know. A dropped comment
    # looks identical whether it was tagless prose or a real finding filed
    # under another rubric's word — and the second is a prompt defect worth
    # naming (measured: two anchored findings lost to the site example's tags).
    rows = parsed.get("items") if isinstance(parsed, dict) else parsed
    rejected_tags = sorted({
        str(r.get("tag")).strip()[:24]
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and r.get("tag")
        and normalize_tag(rubric_id, str(r.get("tag"))) is None
    })
    return {
        "comments": comments,
        "stats": {
            "shot": shot.get("id", ""),
            "menu_anchors": len(valid_els),
            "kept": len(comments),
            "named_an_element": len(named),
            "unresolved_ids": unresolved[:6],
            "rejected_tags": rejected_tags[:6],
            "anchored": sum(1 for c in comments if c.get("anchor") == "dom"),
            "reply_chars": len(text),
        },
    }
