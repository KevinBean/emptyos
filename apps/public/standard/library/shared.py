"""Library — pure constants + citation-format helpers shared across modules.

Extracted so `metadata.py`, `storage.py`, `highlights.py`, and `bibtex.py`
can import these directly without cycling through the spine `.app` module —
the `shared.py` exception documented in
`.claude/rules/multi-module-apps.md` §6 (pure data + pure functions only, no
``self``, no kernel access, no I/O).

Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import re

from emptyos.sdk.utils import slugify

PAPER_TAG = "paper"
ATTACHMENTS_DIR = "attachments"

# Only the DEFAULT — every read/write resolves the live folder through
# `LibraryApp.papers_dir()` (vault map + settings override, Dev Rule 8).
# Kept here beside ATTACHMENTS_DIR because both halves of a paper's storage
# (the note and its PDF) are composed from the same folder.
DEFAULT_PAPERS_DIR = "30_Resources/Library"

# 10.<4-9 digits>/<suffix>, trimmed of trailing punctuation a sentence/PDF
# often leaves attached to a DOI (a period, a closing paren/bracket/quote).
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
_TRAILING_PUNCT = ".,;:)]}\"'"

_STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "for", "and", "to", "with",
    "using", "toward", "towards", "via", "from", "into", "under", "over",
}


def find_doi(text: str) -> str:
    """Best-effort DOI extraction from free text (a PDF's first page, a
    pasted URL). Returns "" when none is found — never raises."""
    m = _DOI_RE.search(text or "")
    if not m:
        return ""
    return m.group(0).rstrip(_TRAILING_PUNCT)


def normalize_str_list(value) -> list[str]:
    """Coerce a loose value (list, comma-string, or None) to a list[str] —
    used for both `authors` and `topics`, the two list-shaped fields a
    caller might hand over as a comma-separated string."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def citekey_for(authors, year: str, title: str, *, taken: set[str] | None = None) -> str:
    """Zotero-style citekey: firstauthorsurname + year + firstsignificantword,
    lowercased, ASCII-sanitized via `slugify`. Appends a/b/c… on collision
    against `taken` (the caller's existing citekey set)."""
    names = normalize_str_list(authors)
    surname = ""
    if names:
        parts = names[0].replace(",", " ").split()
        if parts:
            surname = parts[-1]
    surname_slug = slugify(surname, max_len=24, fallback="anon")

    year_digits = "".join(c for c in str(year or "") if c.isdigit())[:4] or "nd"

    words = [w for w in re.findall(r"[A-Za-z0-9]+", title or "") if w.lower() not in _STOPWORDS]
    title_word = slugify(words[0], max_len=20, fallback="untitled") if words else "untitled"

    base = f"{surname_slug}{year_digits}{title_word}".lower() or "paper"
    if not taken or base not in taken:
        return base
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = base + suffix
        if candidate not in taken:
            return candidate
    return f"{base}{len(taken)}"


def escape_bibtex(s: str) -> str:
    return (s or "").replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def to_bibtex_entry(paper: dict) -> str:
    """Render one paper's stored frontmatter fields as a `@article`/`@misc`
    BibTeX block. Hand-rolled — no `bibtexparser`/`pybtex` dependency."""
    citekey = paper.get("citekey") or (paper.get("file") or "paper.md").removesuffix(".md")
    entry_type = "article" if paper.get("journal") else "misc"
    authors = normalize_str_list(paper.get("authors"))

    fields: list[tuple[str, str]] = []
    if authors:
        fields.append(("author", " and ".join(authors)))
    if paper.get("title"):
        fields.append(("title", str(paper["title"])))
    if paper.get("year"):
        fields.append(("year", str(paper["year"])))
    if paper.get("journal"):
        fields.append(("journal", str(paper["journal"])))
    if paper.get("doi"):
        fields.append(("doi", str(paper["doi"])))
    if paper.get("url"):
        fields.append(("url", str(paper["url"])))

    lines = [f"@{entry_type}{{{citekey},"]
    lines += [f"  {k} = {{{escape_bibtex(v)}}}," for k, v in fields]
    lines.append("}")
    return "\n".join(lines)


def to_csl_json(paper: dict) -> dict:
    """Render one paper's stored fields as a CSL-JSON item — the more
    interchange-friendly format most reference tools (Zotero included)
    accept for import, and trivial to add alongside BibTeX."""
    authors = normalize_str_list(paper.get("authors"))
    csl_authors = []
    for a in authors:
        parts = a.split()
        if len(parts) >= 2:
            csl_authors.append({"given": " ".join(parts[:-1]), "family": parts[-1]})
        elif parts:
            csl_authors.append({"family": parts[0]})

    out: dict = {
        "id": paper.get("citekey") or "paper",
        "type": "article-journal" if paper.get("journal") else "webpage",
        "title": paper.get("title", ""),
        "author": csl_authors,
    }
    year = str(paper.get("year") or "")[:4]
    if year.isdigit():
        out["issued"] = {"date-parts": [[int(year)]]}
    if paper.get("journal"):
        out["container-title"] = paper["journal"]
    if paper.get("doi"):
        out["DOI"] = paper["doi"]
    if paper.get("url"):
        out["URL"] = paper["url"]
    if paper.get("abstract"):
        out["abstract"] = paper["abstract"]
    return out
