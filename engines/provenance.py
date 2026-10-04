"""Row-level provenance for transcribed reference tables — the shared half.

A reference-table JSON (``engines/<pkg>/data/<name>.json``) carries a
``sources`` registry keyed by id, and every row that states a published value
names one of those ids in its ``provenance`` field. This module is the part
that does not depend on what the table is about: loading with shape checks,
resolving a row to its source record, and producing the one-line statement a
report or picker prints. One engine's soil-property reference table is the
first consumer; a second engine's page-cited standard tables are the other it
was extracted for (plan kb-fact-integrity, T2).

Stdlib only — engines never import ``emptyos.sdk``.

Source record::

    "iec60949-t3": {
        "citation": "IEC 60949:1988 Table III (p. 19) — X, Y for the ...",
        "tier": "cited",               # cited | referenced | practice | disputed
        "held": "10_Projects/.../IEC 60949-1988.pdf",   # vault-relative path
        "page": 19,                    # printed page; optional pdf_page when they differ
        "checked": "2026-09-30",
        "checked_method": "page-images",   # page-images | fulltext — REQUIRED when cited
        "note": "..."                  # optional; travels with the sentence when disputed
    }

Tiers, unchanged from the soil table: ``cited`` = the primary was opened and
the value transcribed with its page recorded; ``referenced`` = derived here
from a cited source, not tabulated in it; ``practice`` = a nominal with no
single primary traced; ``disputed`` = a specific primary was named and the
attribution is doubted.

``checked_method`` is the field the 2026-09-30 IEC 60949 incident showed to be
load-bearing: four notes and an engine default were "checked" against a text
extraction whose equations and tables were garbled. A ``cited`` row read off
extracted text is not the same claim as one read off the page image, so a
cited source must say which it was, and a reader may treat ``fulltext`` as
"transcribed, not yet verified against the print".
"""

from __future__ import annotations

import json
from pathlib import Path

TIERS = ("cited", "referenced", "practice", "disputed")
CHECKED_METHODS = ("page-images", "fulltext")


def validate_sources(table: dict) -> list[str]:
    """Problems with a table's ``sources`` registry, as human-readable strings.

    Empty list means the registry is well-formed. Checked: ``sources`` is a
    dict; every record has a ``citation``; ``tier`` is one of ``TIERS``; a
    ``cited`` record carries ``checked_method`` from ``CHECKED_METHODS``; a
    ``checked_method`` that is present is from the vocabulary whatever the tier.
    """
    problems: list[str] = []
    sources = table.get("sources")
    if not isinstance(sources, dict) or not sources:
        return ["table has no `sources` registry"]
    for sid, rec in sources.items():
        if not isinstance(rec, dict):
            problems.append(f"source {sid!r} is not a record")
            continue
        if not str(rec.get("citation", "")).strip():
            problems.append(f"source {sid!r} has no citation")
        tier = rec.get("tier")
        if tier not in TIERS:
            problems.append(f"source {sid!r} has tier {tier!r}; expected one of {TIERS}")
        method = rec.get("checked_method")
        if method is not None and method not in CHECKED_METHODS:
            problems.append(
                f"source {sid!r} has checked_method {method!r}; expected one of {CHECKED_METHODS}"
            )
        if tier == "cited" and method is None:
            problems.append(
                f"source {sid!r} is cited but does not say how it was checked "
                f"(checked_method: {' | '.join(CHECKED_METHODS)})"
            )
    return problems


def load_table_file(path: str | Path) -> dict:
    """Read a reference-table JSON and refuse a malformed ``sources`` registry.

    Raises ``ValueError`` naming every problem, so a typo in a data file fails
    at the first lookup with the row spelled out rather than reaching a report
    as an anonymous number.
    """
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        table = json.load(f)
    problems = validate_sources(table)
    if problems:
        raise ValueError(f"{p.name}: " + "; ".join(problems))
    return table


def source_record(table: dict, source_id: str) -> dict | None:
    """The source record for an id, or ``None``."""
    return (table.get("sources") or {}).get(source_id)


def row_provenance(table: dict, entry: dict | None) -> dict | None:
    """Resolve a row (any dict carrying ``provenance``) to its source record."""
    if not isinstance(entry, dict):
        return None
    return source_record(table, str(entry.get("provenance", "")))


def provenance_sentence_for(record: dict | None) -> str:
    """One line stating where a value came from: ``<tier> - <citation>``.

    The tier leads so a reader sees it before the citation. A ``disputed``
    record's note IS the disclosure, so it travels; other notes are generic
    advice and stay off the line. Returns "" for ``None`` so a caller can skip
    the clause entirely rather than print an empty tier. Always one line — it
    is interpolated into a markdown bullet.
    """
    if not record:
        return ""
    tier = str(record.get("tier", "")).strip() or "unknown"
    body = " ".join(str(record.get("citation", "")).split())
    note = " ".join(str(record.get("note", "")).split())
    if tier == "disputed" and note:
        body = (body + " " + note).strip()
    return f"{tier} - {body}".strip(" -")


def rows_missing_provenance(table: dict, groups: tuple[str, ...] | list[str]) -> list[str]:
    """``group.key`` for every row in the named groups without a ``provenance``."""
    out: list[str] = []
    for group in groups:
        for key, entry in (table.get(group) or {}).items():
            if not isinstance(entry, dict) or "provenance" not in entry:
                out.append(f"{group}.{key}")
    return out


def undeclared_sources(table: dict, groups: tuple[str, ...] | list[str]) -> list[str]:
    """``group.key -> id`` for every row naming a source id not in ``sources``."""
    declared = set(table.get("sources") or {})
    out: list[str] = []
    for group in groups:
        for key, entry in (table.get(group) or {}).items():
            if isinstance(entry, dict) and "provenance" in entry:
                sid = str(entry["provenance"])
                if sid not in declared:
                    out.append(f"{group}.{key} -> {sid}")
    return out
