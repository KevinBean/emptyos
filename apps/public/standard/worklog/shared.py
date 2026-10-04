"""worklog — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (importing/reads/reporting/search/surfaces) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations

import re
from datetime import date

# ── Engineers Australia Stage 2 competency standard (approved 2012) ──────────
# Sixteen elements across four areas. Verbatim element names, transcribed in
# 10_Projects/cpeng/docs/competency-evidence-inventory.md from the approved PDF.
#
# Why this lives in worklog: elements 11 and 13 are the two that can fail a
# Chartered application, and NEITHER can be back-filled from a CV — judgement
# is a decision you made on a particular day, and local engineering knowledge
# accrues only from Australian practice. They have to be caught the day they
# happen, which is what a work log is. The tag makes logging and evidencing the
# same action instead of two.
COMPETENCY_AREAS: dict[str, tuple[int, ...]] = {
    "Personal commitment": (1, 2, 3),
    "Obligation to community": (4, 5, 6, 7),
    "Value in the workplace": (8, 9, 10, 11),
    "Technical proficiency": (12, 13, 14, 15, 16),
}

# The element names and the focus map below are GENERATED from the vault note
# `10_Projects/cpeng/docs/competency-evidence-inventory.md`, which transcribes the
# approved standard and carries the live per-element Strength assessment. Edit the
# note, then run `python scripts/gen_competency_focus.py`; `--check` gates it in
# preflight. Names are stable but Strength moves as elements get evidenced, which
# is the drift worth catching — see the script's docstring.
#
# COMPETENCY_AREAS above is deliberately NOT generated: the note tables 8-16
# together for readability and states the real 11/12 split in prose, so parsing
# unit membership from its headings would yield 3/4/9/0.
# --- generated: EA competency table (scripts/gen_competency_focus.py) ---
# Generated from the vault note; edit there, then re-run the script.
#   10_Projects/cpeng/docs/competency-evidence-inventory.md
COMPETENCIES: dict[int, str] = {
    1: "Deal with ethical issues",
    2: "Practise competently",
    3: "Responsibility for engineering activities",
    4: "Develop safe and sustainable solutions",
    5: "Engage with the relevant community and stakeholders",
    6: "Identify, assess and manage risks",
    7: "Meet legal and regulatory requirements",
    8: "Communication",
    9: "Performance",
    10: "Taking action",
    11: "Judgement",
    12: "Advanced engineering knowledge",
    13: "Local engineering knowledge",
    14: "Problem analysis",
    15: "Creativity and innovation",
    16: "Evaluation",
}

# Strength -> focus. "Strong" is absent by design: only elements that still
# need hunting appear here, so an empty map means nothing is outstanding.
COMPETENCY_FOCUS: dict[int, str] = {
    1: "thin",
    5: "thin",
    7: "thin",
    11: "gap",
    13: "gap",
}
# --- /generated: EA competency table ---

# `#c11` written inline in an item's text. A hashtag deliberately: it rides
# inside the item text, so it round-trips through the markdown parser, the
# portable JSON, the import merge (which matches on item text) and the
# standalone browser bundle with zero changes to any of them.
COMPETENCY_TAG_RE = re.compile(r"(?<![\w#])#c(\d{1,2})\b")


def parse_competencies(text: str) -> list[int]:
    """Element numbers tagged in *text*, deduped, ascending. Out-of-range ignored."""
    seen: set[int] = set()
    for m in COMPETENCY_TAG_RE.finditer(text or ""):
        n = int(m.group(1))
        if n in COMPETENCIES:
            seen.add(n)
    return sorted(seen)


def strip_competency_tags(text: str) -> str:
    """*text* with the `#cN` tags removed — for display and for LLM input."""
    return re.sub(r"\s{2,}", " ", COMPETENCY_TAG_RE.sub("", text or "")).strip()


def _parse_date(date_s: str) -> date:
    if date_s:
        try:
            return date.fromisoformat(date_s)
        except ValueError:
            pass
    return date.today()


def _unfence(text: str) -> str:
    """Unwrap a reply the model wrapped entirely in a ``` fence.

    Deliberately NOT ``strip_markdown``: that deletes fenced spans outright, so
    a fully-fenced reply would come back as an empty draft. The prompt already
    forbids markdown; this only repairs the one failure mode that would
    otherwise look like the model returned nothing.
    """
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    body = s[3:]
    end = body.rfind("```")
    if end == -1:
        return s
    body = body[:end]
    # Drop the language tag on the opening fence, if any.
    first_nl = body.find("\n")
    if first_nl != -1 and " " not in body[:first_nl].strip():
        body = body[first_nl + 1:]
    return body.strip()
