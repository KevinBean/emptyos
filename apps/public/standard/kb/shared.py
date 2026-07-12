"""kb — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (indexes/notes/graph/docs/flipbook)
can import these directly. The spine `app.py` re-exports for backwards
compatibility with downstream imports.

Pure functions only. No `self`, no kernel access, no I/O.
"""

from __future__ import annotations

import re
from pathlib import Path


KINDS = ("concept", "formula", "reference", "clause", "case", "lesson", "pattern", "doc", "moc")
DEFAULT_DOCS_DIR = "30_Resources/EmptyOS/kb/docs"
DEFAULT_NOTES_DIR = "30_Resources/EmptyOS/kb/notes"
# Legacy flipbook-asset locations searched as fallbacks; kept here so the
# canonical list lives next to the rest of KB's path constants.
LEGACY_FLIPBOOK_DIRS = (
    "30_Resources/EmptyOS/kb/flipbook",
    "30_Resources/Explore",
)

# Flipbook storage default + seed symbol library. The generation *engine* moved
# to emptyos/sdk/flipbook.py; this domain data (kb's vault folder + EE seed SVGs)
# stays app-side. Override the folder via emptyos.toml [apps.kb] flipbook_dir.
DEFAULT_FOLDER = "30_Resources/EmptyOS/kb/flipbook"

DEMO_SYMBOLS: dict[str, str] = {
    # Pad-mount distribution transformer — cylindrical tank, three bushings, base
    "transformer": (
        "<svg viewBox=\"0 0 200 240\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Pad-mount distribution transformer with three bushings</desc>"
        "<line x1=\"50\" y1=\"10\" x2=\"50\" y2=\"50\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        "<line x1=\"100\" y1=\"10\" x2=\"100\" y2=\"50\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        "<line x1=\"150\" y1=\"10\" x2=\"150\" y2=\"50\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        "<circle cx=\"50\" cy=\"10\" r=\"8\" fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<circle cx=\"100\" cy=\"10\" r=\"8\" fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<circle cx=\"150\" cy=\"10\" r=\"8\" fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<rect x=\"20\" y=\"50\" width=\"160\" height=\"160\" rx=\"6\" fill=\"#8a7456\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<line x1=\"30\" y1=\"70\" x2=\"30\" y2=\"190\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"170\" y1=\"70\" x2=\"170\" y2=\"190\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"60\" y1=\"110\" x2=\"140\" y2=\"110\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"60\" y1=\"140\" x2=\"140\" y2=\"140\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"60\" y1=\"170\" x2=\"140\" y2=\"170\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<rect x=\"10\" y=\"210\" width=\"180\" height=\"20\" fill=\"#6f5d3f\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "</svg>"
    ),
    # Lattice transmission tower — H-frame
    "transmission-tower": (
        "<svg viewBox=\"0 0 180 280\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Lattice steel transmission tower with two crossarms</desc>"
        "<polygon points=\"30,260 60,40 120,40 150,260\" fill=\"none\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        "<line x1=\"30\" y1=\"260\" x2=\"150\" y2=\"40\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"150\" y1=\"260\" x2=\"30\" y2=\"40\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"45\" y1=\"150\" x2=\"135\" y2=\"150\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"55\" y1=\"100\" x2=\"125\" y2=\"100\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<line x1=\"38\" y1=\"200\" x2=\"142\" y2=\"200\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<rect x=\"10\" y=\"60\" width=\"160\" height=\"6\" fill=\"#6f5d3f\" stroke=\"#2c2722\"/>"
        "<rect x=\"30\" y=\"30\" width=\"120\" height=\"6\" fill=\"#6f5d3f\" stroke=\"#2c2722\"/>"
        "<circle cx=\"15\" cy=\"63\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "<circle cx=\"90\" cy=\"63\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "<circle cx=\"165\" cy=\"63\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "<circle cx=\"35\" cy=\"33\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "<circle cx=\"145\" cy=\"33\" r=\"4\" fill=\"#cdb88c\" stroke=\"#2c2722\"/>"
        "</svg>"
    ),
    # XLPE underground cable — concentric layers cross-section
    "cable-cross-section": (
        "<svg viewBox=\"0 0 200 200\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>XLPE MV/HV cable cross-section: conductor, semicon, XLPE, screen, sheath, jacket</desc>"
        "<circle cx=\"100\" cy=\"100\" r=\"95\" fill=\"#2c2722\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"82\" fill=\"#cdb88c\" opacity=\"0.6\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"70\" fill=\"#8a7456\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"56\" fill=\"#fdfaf2\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"44\" fill=\"#6f5d3f\"/>"
        "<circle cx=\"100\" cy=\"100\" r=\"30\" fill=\"#b08968\"/>"
        "<g fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"0.5\">"
        "<circle cx=\"100\" cy=\"80\" r=\"4\"/><circle cx=\"110\" cy=\"86\" r=\"4\"/>"
        "<circle cx=\"114\" cy=\"96\" r=\"4\"/><circle cx=\"110\" cy=\"108\" r=\"4\"/>"
        "<circle cx=\"100\" cy=\"114\" r=\"4\"/><circle cx=\"90\" cy=\"108\" r=\"4\"/>"
        "<circle cx=\"86\" cy=\"96\" r=\"4\"/><circle cx=\"90\" cy=\"86\" r=\"4\"/>"
        "<circle cx=\"100\" cy=\"96\" r=\"4\"/>"
        "</g></svg>"
    ),
    # Solar PV panel — grid of cells
    "solar-panel": (
        "<svg viewBox=\"0 0 220 140\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Photovoltaic solar panel with cell grid</desc>"
        "<rect x=\"10\" y=\"10\" width=\"200\" height=\"120\" fill=\"#2c2722\" stroke=\"#2c2722\" stroke-width=\"3\"/>"
        + "".join(
            f"<rect x='{15 + (col * 32)}' y='{15 + (row * 28)}' "
            f"width='30' height='26' fill='#3a4f6b' stroke='#1a1a1a' stroke-width='0.5'/>"
            for row in range(4) for col in range(6)
        )
        + "</svg>"
    ),
    # Wind turbine — tower + 3 blades
    "wind-turbine": (
        "<svg viewBox=\"0 0 200 280\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Horizontal-axis wind turbine — tower, nacelle, three blades</desc>"
        "<polygon points=\"95,260 105,260 102,80 98,80\" fill=\"#cdb88c\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<rect x=\"85\" y=\"70\" width=\"30\" height=\"20\" rx=\"4\" fill=\"#8a7456\" stroke=\"#2c2722\" stroke-width=\"2\"/>"
        "<circle cx=\"100\" cy=\"80\" r=\"6\" fill=\"#2c2722\"/>"
        "<path d=\"M 100 80 L 100 10 L 95 14 Z\" fill=\"#fdfaf2\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<path d=\"M 100 80 L 165 115 L 160 120 Z\" fill=\"#fdfaf2\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "<path d=\"M 100 80 L 35 115 L 40 120 Z\" fill=\"#fdfaf2\" stroke=\"#2c2722\" stroke-width=\"1\"/>"
        "</svg>"
    ),
    # Lightning bolt — high-voltage / fault icon
    "lightning-bolt": (
        "<svg viewBox=\"0 0 100 160\" xmlns=\"http://www.w3.org/2000/svg\">"
        "<desc>Lightning bolt — high voltage / fault indicator</desc>"
        "<polygon points=\"55,5 15,90 45,90 30,155 85,55 55,55 75,5\" "
        "fill=\"#d4a73c\" stroke=\"#2c2722\" stroke-width=\"2\" stroke-linejoin=\"round\"/>"
        "</svg>"
    ),
}


# Citation parser — matches free-text strings like:
#   "IEC 60287-1-1:2023 §5.1.3 (description)"
#   "CIGRE TB 880 §4.6.4"
#   "IEC 60228 (description)"   ← standard-only, clause=None
# Unmatched strings fall through to plain-text passthrough.
_CITATION_RE = re.compile(
    r"""^\s*
    (?P<standard>
        IEC\s*\d+(?:[-‑–]\d+)*           # IEC 60287, IEC 60287-1-1
        | CIGRE\s+TB\s+\d+                # CIGRE TB 880
        | TB\s+\d+                        # TB 880 (CIGRE-omitted shorthand)
        | AS/?NZS\s+\d+(?:\.\d+)*         # AS/NZS 3008.1.1
        | AS\s+\d+(?:\.\d+)*              # AS 2067, AS 1824.1 (plain Australian Standard)
        | IEEE\s+Std\s+\d+(?:[-‑–]\d+)*   # IEEE Std 1547-2018, IEEE Std 1185-2019
        | IEEE\s+\d+                      # IEEE 80, IEEE 998
        | ENA\s+EREC\s+[A-Z]\d+(?:\.\d+)* # ENA EREC C55
        | EREC\s+[A-Z]\d+(?:\.\d+)*       # EREC C55 (ENA-omitted shorthand)
    )
    (?:\s*[:(\s]\s*(?P<edition>\d{4})\)?)?  # :2023, (2023), or " 2023"
    (?:[\s,]+§\s*(?P<clause>             # § followed by a clause number
        \d+(?:\.\d+)*(?:[a-z])?           #   5.1.3, 4.6.4.1a
        (?:\s*[\-–]\s*\d+(?:\.\d+)*)?    #   5.1.4–5.1.5 (range)
    ))?
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", str(s).strip().lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s or "untitled"


def _slug_of(path: str) -> str:
    return Path(path).stem


def _related_targets(props: dict) -> set[str]:
    """Slugs referenced in frontmatter `related` (accepts string or [[slug]] forms)."""
    out: set[str] = set()
    for v in props.get("related") or []:
        s = str(v).strip().strip("[]").strip()
        if s:
            out.add(s)
    return out


def _note_has_visual(props: dict) -> bool:
    """Does this kb note carry flipbook visual data?

    After Wave 3 consolidation, "has a flipbook" is computed from the
    presence of visual frontmatter rather than from a separate `flipbook`
    tag. A note has a visual when any of:
      - ``svg_callouts``  (JSON-encoded list of per-callout anchors for SVG mode)
      - ``image_callouts`` (same shape for image mode)
      - ``image_url``     (PNG asset URL for image mode)
    is non-empty in frontmatter. ``svg`` content itself lives in a
    sidecar asset file, so its presence is implied by ``svg_callouts``.
    """
    if not isinstance(props, dict):
        return False
    return bool(
        props.get("svg_callouts")
        or props.get("image_callouts")
        or props.get("image_url")
    )


def _norm_standard(s: str) -> str:
    """Normalize standard name: uppercase, normalize hyphens, single space between letters/digits."""
    s = (s or "").upper().strip()
    s = s.replace("‑", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    # "IEC60287" → "IEC 60287"
    s = re.sub(r"([A-Z])(\d)", r"\1 \2", s)
    # Collapse repeated spaces after the insertion
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_clause(s: str | None) -> str | None:
    """Normalize clause: strip §, collapse whitespace. '§ 5.1.3' → '5.1.3'."""
    if s is None:
        return None
    raw = str(s).replace("§", "").replace("¶", "").strip().lower()
    raw = re.sub(r"\s+", "", raw)
    # Normalize en-dash / em-dash / non-breaking hyphen → hyphen in ranges
    raw = raw.replace("‑", "-").replace("–", "-").replace("—", "-")
    return raw or None


def _clause_sort_key(clause: str | None) -> tuple:
    """Sort key for clauses: '5.1.10' > '5.1.2' (numeric, not lexical); 'None' first."""
    if not clause:
        return ()
    out = []
    for part in str(clause).split("."):
        m = re.match(r"^(\d+)([a-z]?)$", part, re.IGNORECASE)
        if m:
            out.append((int(m.group(1)), m.group(2).lower()))
        else:
            # Non-numeric segment (e.g. range '4-5' or letters) — sort as 0 with raw tail
            out.append((0, part.lower()))
    return tuple(out)


def _norm_edition(edition: str | None) -> str:
    """Normalize an edition/revision string for equality keying. '' for None."""
    return str(edition or "").strip().lower()


def _edition_sort_key(edition: str | None) -> tuple:
    """Version-ordering key for editions/revisions.

    Pulls the first dotted-number sequence out of strings like ``"Rev 1.0"``,
    ``"Rev 0.2"``, ``"2023"``, ``"Ed 2.0"`` → ``(1,0)``, ``(0,2)``, ``(2023,)``,
    ``(2,0)`` so they order as *versions* (``1.0`` > ``0.2`` > ``0.1``), not
    lexically. ``None``/unparseable → ``()`` which sorts lowest (so it lands
    last in a newest-first / ``reverse=True`` sort).

    Replaces the prior ``int(edition)`` / ``.isdigit()`` / 4-digit-year logic,
    which silently tied vendor revisions like ``Rev 1.0`` vs ``Rev 0.2``.
    Pure — no ``self``, no I/O.
    """
    if not edition:
        return ()
    m = re.search(r"\d+(?:\.\d+)*", str(edition))
    if not m:
        return ()
    return tuple(int(p) for p in m.group(0).split("."))


def _parse_citation(text: str) -> tuple[str, str | None, str | None] | None:
    """Parse a free-text citation string into (standard, edition, clause).

    Returns None when no standard pattern matches.
    For range citations like '§5.1.4–5.1.5', returns the lower bound only.
    """
    if not text:
        return None
    m = _CITATION_RE.match(text)
    if not m:
        return None
    standard = _norm_standard(m.group("standard") or "")
    edition = m.group("edition")
    edition = str(edition) if edition else None
    clause = m.group("clause")
    if clause:
        # Range like '5.1.4-5.1.5' → take lower bound
        clause = re.split(r"\s*[\-–—]\s*", clause, maxsplit=1)[0]
    clause = _norm_clause(clause)
    if not standard:
        return None
    return (standard, edition, clause)


def resolve_supersession(edges: dict, known_slugs) -> dict:
    """Resolve a flat supersession edge set into clean lookup maps.

    Temporal-supersession primitive (Supermemory's "newer fact wins", applied to
    KB `clause`/`reference` notes). A note declares `superseded_by: <slug>`; this
    turns the raw edge set into three maps the KB surfaces consume.

    Args:
      edges:       ``{slug -> superseded_by_slug}`` — one entry per note that
                   declares ``superseded_by``. Self-edges, edges to unknown
                   slugs, and cyclic edges are all tolerated as *input*.
      known_slugs: every KB slug that actually exists. Edges whose source or
                   target is unknown are dropped (a dangling pointer surfaces
                   nothing rather than a broken link).

    Returns a dict with:
      ``forward``  ``{slug -> immediate_successor_slug}`` — cleaned 1-hop edges.
                   The detail-view banner uses this: it shows the successor the
                   author actually declared, not a chain-followed endpoint.
      ``reverse``  ``{successor_slug -> [predecessor_slug, ...]}`` — derived,
                   each predecessor list sorted.
      ``terminal`` ``{slug -> terminal_current_slug}`` — chain-followed endpoint
                   (A→B→C gives A→C), cycle-safe. Returned for a future
                   jump-to-current consumer; not used by the v1 banner.

    Pure — no ``self``, no I/O. "Is X current?" is ``X not in forward``.
    """
    known = set(known_slugs)
    # Clean: drop edges whose source or target is unknown, and self-edges.
    forward: dict[str, str] = {}
    for src, tgt in (edges or {}).items():
        tgt = str(tgt or "").strip().strip("[]").strip()
        if not src or not tgt:
            continue
        if src not in known or tgt not in known or src == tgt:
            continue
        forward[src] = tgt

    reverse: dict[str, list[str]] = {}
    for src, tgt in forward.items():
        reverse.setdefault(tgt, []).append(src)
    for tgt in reverse:
        reverse[tgt].sort()

    def _terminal(start: str) -> str:
        seen = {start}
        cur = start
        while cur in forward:
            nxt = forward[cur]
            if nxt in seen:  # cycle — stop here, never loop
                return cur
            seen.add(nxt)
            cur = nxt
        return cur

    terminal = {src: _terminal(src) for src in forward}
    return {"forward": forward, "reverse": reverse, "terminal": terminal}


def resolve_document_cascade(references, clauses) -> dict:
    """Derive clause-level supersession from document(reference)-level edges.

    A ``kind: reference`` note that declares ``superseded_by`` means the whole
    *document revision* is superseded. Every ``kind: clause`` note sharing that
    document's ``(standard_id, edition)`` then inherits the supersession,
    pointing at the **successor reference** — i.e. "this clause is from a
    superseded revision; the current document is X". This avoids hand-wiring
    ``superseded_by`` on every clause when a whole standard is re-issued.

    Explicit always wins: a clause that declares its own ``superseded_by`` is
    left out of the derived map (the caller keeps the explicit edge).

    Args:
      references: iterable of ``{slug, standard_id, edition, superseded_by}``
                  for every ``kind: reference`` note.
      clauses:    iterable of ``{slug, standard_id, edition, superseded_by}``
                  for every ``kind: clause`` note.

    Returns ``{clause_slug -> successor_reference_slug}`` — derived edges only.
    Pure — no ``self``, no I/O. Keyed case-insensitively on
    ``(standard_id, edition)``; both reference and clause notes carry that pair.
    """
    def _key(sid, ed) -> tuple:
        return (str(sid or "").strip().lower(), _norm_edition(ed))

    # Map each superseded document version → its successor reference slug.
    superseded_doc: dict[tuple, str] = {}
    for r in references or []:
        tgt = str(r.get("superseded_by") or "").strip().strip("[]").strip()
        sid = r.get("standard_id")
        if not tgt or not sid:
            continue
        superseded_doc[_key(sid, r.get("edition"))] = tgt

    out: dict[str, str] = {}
    for c in clauses or []:
        slug = c.get("slug")
        if not slug:
            continue
        # Explicit per-clause edge wins — leave it to resolve_supersession.
        if str(c.get("superseded_by") or "").strip():
            continue
        succ = superseded_doc.get(_key(c.get("standard_id"), c.get("edition")))
        if succ and succ != slug:
            out[slug] = succ
    return out
