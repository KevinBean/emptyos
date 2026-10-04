"""Standard atomization — turn a reference note's verbatim archive into a plan
of layered atomic ``clause`` notes (one per level-2 §X.Y section).

A standard stored as a flat ``.txt`` archive is a blob; EmptyOS wants it as
atomic, connectable notes (one ``clause`` note per section) that the reference
note composes into the readable document. This module is the *pure* planner:
given the archive text + the reference's metadata + the clause numbers that
already exist, it returns a list of :class:`ClauseNoteSpec` (rel path,
frontmatter, body). The caller writes them — a standalone runner writes files
directly (no daemon), the kb app writes via ``vault_create_note`` (the reusable
``atomize_standard`` feature). No I/O, no kernel, no ``self``.

Reuses :mod:`emptyos.sdk.doc_slice` (``parse_contents`` + ``slice_clause_text``).
Skips sections whose clause number already exists, so hand-curated single-clause
notes (with their ``## Changes vs Rev 0.2``) are never overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from emptyos.sdk.doc_slice import parse_contents, slice_clause_text
from emptyos.sdk.utils import slugify_unicode


def _slugify(s: str) -> str:
    # Unicode-preserving: clause titles are frequently non-English, and the
    # ASCII slugify() would collapse them all onto "untitled".
    return slugify_unicode(s, fallback="untitled")


def _norm_clause(s) -> str | None:
    if s is None:
        return None
    raw = str(s).replace("§", "").strip().lower()
    raw = re.sub(r"\s+", "", raw).replace("‑", "-").replace("–", "-").replace("—", "-")
    return raw or None


def _format_section_body(no: str, text: str) -> str:
    """Verbatim section text → readable markdown.

    Drops the section's own heading line (the note's ``#`` title already shows
    it) and promotes verbatim subsection-heading lines ("1.7.1. General") to
    ``## §1.7.1 General`` so they anchor the on-this-page rail. Other lines stay
    verbatim (paragraphs). Mirrors the hand-curated clause notes' shape.
    """
    sub_re = re.compile(r"^(" + re.escape(no) + r"\.\d+(?:\.\d+)*)\.?\s+(\S.*)$")
    head_re = re.compile(r"^" + re.escape(no) + r"\.?\s+\S")
    out: list[str] = []
    for i, ln in enumerate(text.split("\n")):
        t = ln.strip()
        if i == 0 and head_re.match(t):
            continue  # the section's own heading — the note ``#`` title owns it
        if re.search(r"\.{2,}\s*\d+\s*$", t):  # dot-leader TOC noise
            out.append(ln)
            continue
        m = sub_re.match(t)
        if m and len(m.group(2)) < 80:
            out.append(f"## §{m.group(1)} {m.group(2).strip()}")
        else:
            out.append(ln)
    return "\n".join(out).strip()


def short_label(reference_title: str) -> str:
    """Compact standard label for note titles.

    'Transgrid Cable Design and Installation Manual (CDIM) — Rev 1.0' → 'Transgrid CDIM'
    (first word + a parenthesised acronym). Falls back to the pre-dash title.
    """
    t = (reference_title or "").strip()
    m = re.search(r"\(([A-Z0-9][A-Z0-9\-]{1,})\)", t)
    if m and t.split():
        return (t.split()[0] + " " + m.group(1)).strip()
    return re.split(r"\s+[—–-]\s+", t)[0].strip() or t


def standard_name_of(reference_title: str) -> str:
    """The standard's full name (reference title minus a trailing edition suffix)."""
    return re.split(r"\s+[—–-]\s+Rev\b", reference_title or "", flags=re.IGNORECASE)[0].strip() \
        or (reference_title or "").strip()


@dataclass
class ClauseNoteSpec:
    clause: str
    title: str
    slug: str
    rel: str
    frontmatter: dict
    body: str


def plan_atomization(
    archive_text: str,
    *,
    reference_slug: str,
    reference_title: str,
    standard_id: str,
    edition: str | None,
    domain: str = "",
    topic: str = "",
    source_file: str = "",
    existing_clauses=None,
    extra_frontmatter: dict | None = None,
    sources_dir: str = "30_Resources/EmptyOS/kb/sources",
    today: str = "",
    sections: list[dict] | None = None,
) -> list[ClauseNoteSpec]:
    """Plan one ``clause`` note per level-2 §X.Y section of the archive.

    Args:
      archive_text:     the cleaned verbatim standard text.
      reference_slug:   the parent reference note's slug (sets ``parent`` + slug prefix).
      reference_title:  the reference note's title (for the section titles + ``standard``).
      standard_id:      stable id shared by reference + clauses (``D2020/03048``).
      edition:          revision axis (``Rev 1.0``).
      existing_clauses: clause numbers already present for this standard+edition — skipped.
      extra_frontmatter: inherited fields to copy onto each clause (e.g. ``voltage_levels``).
      today:            ISO date for ``created``/``updated`` (caller supplies; this stays pure).
      sections:         the section index to plan from. Default: the archive's own
                        contents table (``parse_contents``). A caller holding a
                        document with no contents table passes
                        ``doc_slice.parse_sections(text)`` (heading fallback).

    Returns specs in document order; the caller persists them.
    """
    existing = {_norm_clause(c) for c in (existing_clauses or []) if c}
    short = short_label(reference_title)
    std_name = standard_name_of(reference_title)
    ed = (edition or "").strip()
    extra = {k: v for k, v in (extra_frontmatter or {}).items() if v}
    specs: list[ClauseNoteSpec] = []
    seen: set[str] = set()
    for s in (sections if sections is not None else parse_contents(archive_text)):
        if s.get("level") != 2:
            continue
        no = str(s.get("no") or "").strip()
        if not no or _norm_clause(no) in existing:
            continue
        body_text = _format_section_body(no, slice_clause_text(archive_text, no).strip())
        if not body_text:
            continue
        ctitle = str(s.get("title") or "").strip()
        title = f"{short} {ed} §{no} — {ctitle}".strip().rstrip("—").strip()
        slug = f"{reference_slug}-{no.replace('.', '-')}-{_slugify(ctitle)}".strip("-")
        base, i = slug, 2
        while slug in seen:
            slug = f"{base}-{i}"
            i += 1
        seen.add(slug)
        fm: dict = {
            "tags": ["kb"],
            "kind": "clause",
            "title": title,
            "standard": std_name,
            "standard_id": standard_id,
            "edition": ed or None,
            "clause": no,
            "clause_title": ctitle,
            "chapter": str(s.get("chapter") or ""),
        }
        fm.update(extra)
        if domain:
            fm["domain"] = domain
        if topic:
            fm["topic"] = topic
        if source_file:
            fm["source_file"] = source_file
        fm["parent"] = f"[[{reference_slug}]]"
        fm["related"] = [reference_slug]
        if today:
            fm["created"] = today
            fm["updated"] = today
        body = f"# §{no} — {ctitle}\n\n{body_text}\n"
        specs.append(ClauseNoteSpec(
            clause=no, title=title, slug=slug,
            rel=f"{sources_dir}/{slug}.md", frontmatter=fm, body=body,
        ))
    return specs
