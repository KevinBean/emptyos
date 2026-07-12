"""Diff reducer — keep files, hunks, and changed lines; thin the context.

Deterministic + query-independent. Preserves every ``diff --git`` / ``---`` /
``+++`` / ``@@`` header and every added/removed line. Unchanged context lines
(leading space) are kept only in a small window around each hunk header, so the
risky changes survive intact while bulk context shrinks. Hunks are NEVER
reordered.
"""

from __future__ import annotations

from ..budget import est_tokens
from ..blocks import ReducerResult

CONTEXT_KEEP = 1  # context lines to keep immediately after a hunk header


def _is_header(line: str) -> bool:
    return (
        line.startswith("diff --git")
        or line.startswith("--- ")
        or line.startswith("+++ ")
        or line.startswith("@@")
        or line.startswith("index ")
        or line.startswith("new file")
        or line.startswith("deleted file")
        or line.startswith("rename ")
    )


def reduce(text: str) -> ReducerResult:
    original_tokens = est_tokens(text)
    lines = text.splitlines()

    out: list[str] = []
    files = 0
    hunks = 0
    changed = 0
    context_budget = 0
    dropped_context = 0

    for line in lines:
        if line.startswith("diff --git"):
            files += 1
        if line.startswith("@@"):
            hunks += 1
            context_budget = CONTEXT_KEEP

        if _is_header(line):
            out.append(line)
            continue
        if line.startswith("+") or line.startswith("-"):
            out.append(line)
            changed += 1
            continue
        if line.startswith("\\"):  # "\ No newline at end of file"
            out.append(line)
            continue
        # Context line (leading space or blank): keep a small window post-hunk.
        if context_budget > 0:
            out.append(line)
            context_budget -= 1
        else:
            dropped_context += 1

    header = (
        f"[diff summary: {files} files, {hunks} hunks, {changed} changed lines, "
        f"{dropped_context} context lines thinned]"
    )
    summary = header + "\n" + "\n".join(out)
    return ReducerResult(
        text=summary,
        original_tokens=original_tokens,
        packed_tokens=est_tokens(summary),
        warnings=[],
    )
