"""Search-result reducer — group grep/ripgrep output by file, keep matches.

Deterministic + query-independent (the matches are already determined by the
search). Groups ``path:lineno:content`` lines by file, keeps a sample of
matched lines per file, and NEVER silently drops an unmatched file — every
source file is named with its match count.
"""

from __future__ import annotations

import re

from ..budget import est_tokens
from ..blocks import ReducerResult

# ripgrep / grep -n style: "path:123:content" (also "path-123-content" context).
_MATCH = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):(?P<body>.*)$")
SAMPLE_PER_FILE = 4


def reduce(text: str) -> ReducerResult:
    original_tokens = est_tokens(text)
    lines = text.splitlines()

    # Preserve file order of first appearance.
    order: list[str] = []
    by_file: dict[str, list[str]] = {}
    unmatched: list[str] = []

    for ln in lines:
        m = _MATCH.match(ln)
        if not m:
            if ln.strip():
                unmatched.append(ln)
            continue
        path = m.group("path")
        if path not in by_file:
            by_file[path] = []
            order.append(path)
        by_file[path].append(f"{m.group('line')}: {m.group('body')}")

    if not by_file:
        # Not grep-shaped — return verbatim rather than mangle it.
        return ReducerResult(
            text=text,
            original_tokens=original_tokens,
            packed_tokens=original_tokens,
            warnings=["search_result: no path:line: matches found"],
        )

    total = sum(len(v) for v in by_file.values())
    out: list[str] = [f"[search summary: {total} matches across {len(by_file)} files]"]
    for path in order:
        hits = by_file[path]
        out.append(f"{path} ({len(hits)} matches)")
        for h in hits[:SAMPLE_PER_FILE]:
            out.append(f"  {h}")
        if len(hits) > SAMPLE_PER_FILE:
            out.append(f"  ... (+{len(hits) - SAMPLE_PER_FILE} more in this file)")
    if unmatched:
        out.append(f"[{len(unmatched)} non-match lines omitted]")

    summary = "\n".join(out)
    return ReducerResult(
        text=summary,
        original_tokens=original_tokens,
        packed_tokens=est_tokens(summary),
        warnings=[],
    )
