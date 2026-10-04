"""File-attachment text extraction — PDF, docx, txt, md.

Moved to ``emptyos/sdk/attachments.py`` when the agent became the second
consumer (CLAUDE.md rule 9); this module re-exports it so the assistant's
imports keep working. The SDK version also confines the path to the vault —
this module's original joined ``vault_root / rel_path`` unchecked, so a
``../`` path read outside it. Override the cap via ``[apps.assistant]
max_file_chars`` in emptyos.toml.
"""

from __future__ import annotations

from emptyos.sdk.attachments import (
    DEFAULT_MAX_CHARS,
    EXTRACTABLE_EXTENSIONS,
    ExtractedFile,
    extract_file,
    format_block,
    is_extractable_path,
)

__all__ = [
    "DEFAULT_MAX_CHARS", "EXTRACTABLE_EXTENSIONS", "ExtractedFile",
    "extract_file", "format_block", "is_extractable_path",
]
