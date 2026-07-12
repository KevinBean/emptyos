"""doc_slice — pure verbatim-clause slicer over a stored full-text artifact.

Given the full text of a standard (either the page-marked canonical
``_fulltext/<SLUG>.md`` layout — ``<!-- Page N of M -->`` markers — or a legacy
numbered ``.txt`` extract) and a clause number, return the verbatim span for
that clause: from its numbered heading down to the next heading at the same or
a higher level (so sub-clauses stay included). Handles ranges like ``7.4-7.20``.

Used by the KB revision-diff engine to diff the *original* text of a clause
across two revisions. Pure — no ``self``, no kernel, no I/O — so it unit-tests
without a daemon (see ``tests/test_unit_doc_slice.py``).

Heading detection is heuristic but conservative:
  - A heading line starts (after optional ``#``/``§``/whitespace) with the
    clause number, an optional trailing dot, whitespace, then title text.
  - Table-of-contents rows (``1.7 Ratings .......... 17``) are excluded by their
    trailing dotted-leader + page-number tail, so the *body* heading is found.
"""

from __future__ import annotations

import re

# A table-of-contents row: a run of dot leaders followed by a page number at EOL.
_LEADER_RE = re.compile(r"\.{2,}\s*\d+\s*$")
# Leading chrome allowed before a heading number: markdown hashes, a §, whitespace.
_HEAD_PREFIX = r"^\s*(?:#{1,6}\s*)?§?\s*"
# A table-of-contents entry: "1.7. Ratings ............ 17" — number, title, dot
# leader, page. The title is non-greedy up to the dot leader (titles never carry
# a 2+-dot run themselves), so it works whether or not a space precedes the dots.
_CONTENTS_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(.+?)\s*\.{2,}\s*(\d+)\s*$")


def _norm_clause_no(clause_no: str) -> str:
    """Strip §/¶ and surrounding whitespace from a clause number."""
    return re.sub(r"\s+", "", str(clause_no or "").replace("§", "").replace("¶", "").strip())


def _num_key(num: str) -> tuple:
    """Numeric sort key for a dotted clause number ('1.10' -> (1, 10))."""
    out: list[int] = []
    for part in str(num).split("."):
        m = re.match(r"(\d+)", part)
        out.append(int(m.group(1)) if m else 0)
    return tuple(out)


def _depth(num: str) -> int:
    return len([p for p in str(num).split(".") if p])


def _heading_num(line: str) -> str | None:
    """Extract a leading clause number from a *body* heading line (not a TOC row).

    A bare integer must carry a trailing dot ("7." → chapter heading), so a
    sentence like "7 days …" is not mistaken for a section.
    """
    if _LEADER_RE.search(line):
        return None
    m = re.match(_HEAD_PREFIX + r"(\d+(?:\.\d+)*)(\.?)\s+\S", line)
    if not m:
        return None
    num, dot = m.group(1), m.group(2)
    if "." not in num and not dot:  # "7 days" — bare integer, no dot → not a heading
        return None
    return num


def _is_heading_for(line: str, num: str) -> bool:
    """True if `line` is the body heading for clause `num` (exact, not a child).

    For a bare-integer (chapter) number the trailing dot is required ("7." not
    "7 days"); a dotted number keeps the dot optional.
    """
    if _LEADER_RE.search(line):
        return False
    tail = r"\.\s+\S" if "." not in num else r"\.?\s+\S"
    return re.match(_HEAD_PREFIX + re.escape(num) + tail, line) is not None


def _parse_range(clause_no: str) -> tuple[str, str]:
    """Return (lo, hi) for a clause number; equal for a single clause.

    Accepts '7.4-7.20', '7.4–7.20' (en/em dash), or a single '1.7'.
    """
    raw = _norm_clause_no(clause_no)
    m = re.match(r"^(\d+(?:\.\d+)*)[\-–—](\d+(?:\.\d+)*)$", raw)
    if m:
        return m.group(1), m.group(2)
    return raw, raw


def slice_clause_text(fulltext: str, clause_no: str) -> str:
    """Return the verbatim text of `clause_no` from `fulltext`.

    Span = the clause's body heading → the next heading whose depth is <= the
    clause's depth (children stay included). For a range 'a-b', span = a's
    heading → the next depth-<=depth(a) heading numbered strictly above b.
    Returns '' when the heading can't be located.
    """
    if not fulltext or not clause_no:
        return ""
    lo, hi = _parse_range(clause_no)
    if not lo:
        return ""
    lines = fulltext.splitlines()

    # Locate the start: the body heading for `lo`.
    start = next((i for i, ln in enumerate(lines) if _is_heading_for(ln, lo)), None)
    if start is None:
        return ""

    lo_depth = _depth(lo)
    hi_key = _num_key(hi)
    # Locate the end: first subsequent heading at depth <= lo_depth whose number
    # is strictly greater than `hi` (i.e. a sibling/parent past the range).
    end = len(lines)
    for j in range(start + 1, len(lines)):
        hn = _heading_num(lines[j])
        if hn is None:
            continue
        if _depth(hn) <= lo_depth and _num_key(hn) > hi_key:
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def parse_contents(fulltext: str) -> list[dict]:
    """Parse a document's own table-of-contents into a section index.

    Reads the dot-leader rows ("1.7. Ratings ......... 17") — the standard's
    authoritative section list — and returns, in document order::

        [{"no": "1.7", "title": "Ratings", "page": 17, "level": 2, "chapter": "1"}, ...]

    `level` is the dotted depth (1 = chapter, 2 = section, 3 = sub-section).
    Body headings (no trailing dot-leader+page) are ignored, so list items or
    sentences that merely start with a number can't masquerade as sections.
    Duplicate section numbers keep their first occurrence. Pure — no I/O.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for line in (fulltext or "").splitlines():
        m = _CONTENTS_RE.match(line)
        if not m:
            continue
        no = m.group(1)
        if no in seen:
            continue
        seen.add(no)
        try:
            page = int(m.group(3))
        except ValueError:
            page = 0
        out.append({
            "no": no,
            "title": m.group(2).strip(),
            "page": page,
            "level": len([p for p in no.split(".") if p]),
            "chapter": no.split(".")[0],
        })
    return out
