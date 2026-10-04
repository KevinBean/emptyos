"""Snapshot-back — render a live co-doc's current text as a vault-ready md note.

The live doc is ephemeral (Lane 1, no vault). A snapshot is the **one-way bridge**
to the durable vault: this service produces the markdown; the OWNER's daemon is
what actually writes it into the vault (Lane 1 has no vault by design). Authorship
is marked ``author: both`` (the authorship-boundary rule) — a live doc is
co-authored by everyone who edited it, never silently attributed to one hand.

Pure functions, no I/O — unit-testable without the service.
"""

from __future__ import annotations

import re


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "co-doc"


def snapshot_markdown(
    doc: dict, text: str, *, contributors: list[str] | None = None
) -> str:
    """Build a vault note from a co-doc's metadata + its current plain text.

    Block-style frontmatter (the vault convention — never inline arrays), and
    ``author: both`` so a reader can tell six months later it was co-authored.
    """
    title = (doc.get("title") or "").strip() or "Untitled co-doc"
    lines = [
        "---",
        f"title: {title}",
        "author: both",
        "source: codoc",
        f"codoc_id: {doc['id']}",
        f"codoc_owner: {doc['owner_id']}",
    ]
    if contributors:
        lines.append("contributors:")
        lines.extend(f"  - {c}" for c in contributors)
    if doc.get("created"):
        lines.append(f"created: {doc['created']}")
    if doc.get("updated"):
        lines.append(f"updated: {doc['updated']}")
    lines += ["---", "", text.strip(), ""]
    return "\n".join(lines)
