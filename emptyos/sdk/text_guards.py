"""Text-shape guards for write boundaries.

Pure functions — no kernel, no I/O. Safe to import from any app.
"""

from __future__ import annotations


def assert_single_line(text: str, *, label: str = "text") -> None:
    """Reject text containing newlines.

    Use at write boundaries that interpolate ``text`` into a markdown list
    item or similar single-line context, where a stray newline would
    silently corrupt surrounding structure (split a task list, fatten a
    journal section, etc.).

    Raises ValueError on newline. Empty/whitespace-only is the caller's
    concern — guard it separately if needed.
    """
    if "\n" in text or "\r" in text:
        raise ValueError(f"{label} must be a single plain line (no newlines)")
