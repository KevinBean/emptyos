"""worklog — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (importing/reads/reporting/search/surfaces) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations

from datetime import date


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
