"""Shared module-level helpers for reader app and its mixins."""

from __future__ import annotations

import re
import unicodedata


DEFAULT_BOOKS_DIR = "30_Resources/Books"
DEFAULT_PRODUCTIONS_DIR = "30_Resources/EmptyOS/reader/productions"
DEFAULT_NOTES_DIR = "30_Resources/EmptyOS/reader/notes"


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text) or "book"


def _split_paragraphs(body: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    return [p for p in paras if not re.match(r"^#{1,6}\s", p) or len(p) > 80]
