"""Edit tool — exact (then line-fuzzy) string replacement in an existing file.

Permission: always `ask`. Edits are targeted mutations; the model provides
an `old_string` that must occur exactly once in the file (or set
`replace_all` to replace every occurrence). The uniqueness check protects
against over-broad edits that silently rewrite the wrong spot.

No "read-before-edit" gate is enforced in the tool itself — the agent loop
doesn't track read state. The uniqueness requirement achieves the same
effect: you can't pass a specific-enough `old_string` without having seen
the file.

Two-stage matching:
  1. Exact byte match (the safe default).
  2. Line-aware fuzzy fallback — same lines after stripping trailing
     whitespace + normalizing line endings. Leading whitespace is kept
     exact (Python indent is meaningful). The fallback fires only when
     the exact match found ZERO occurrences and the line-aware match
     finds exactly ONE span. The replacement substitutes the actual
     bytes of that span with the model's `new_string` verbatim — we do
     not normalize the file as a side effect.

When even the fuzzy match misses, the error embeds the file's closest
line(s) so the model can correct its `old_string` without an extra Read.
"""

from __future__ import annotations

import difflib

from emptyos.sdk.agent_tools.base import Tool, ToolResult, resolve_path, unified_diff

MAX_BYTES = 5_000_000


def _normalize_line(line: str) -> str:
    """Strip trailing whitespace + line-ending characters.

    Preserves leading whitespace because Python indentation is semantic.
    """
    # rstrip handles \r, \n, spaces, tabs in one pass — exactly what we want
    # for "ignore trailing whitespace AND line-ending differences".
    return line.rstrip()


def _split_lf_keepends(s: str) -> list[str]:
    """`splitlines(keepends=True)` but ONLY `\\n` is a separator.

    Why not just `splitlines()`: Python's splitlines also splits on `\\v`,
    `\\f`, `\\x1c..\\x1e`, `\\x85`, `\\u2028`, `\\u2029`. When a model
    mistokenizes em-dash → vertical-tab in `old_string`, splitlines()
    splits the string at the wrong place — line counts don't match the
    file and stage-3 similarity matching can't even see the lines align.
    LF-only splitting is what every editor on every platform actually does
    when computing line numbers.
    """
    if not s:
        return []
    out: list[str] = []
    start = 0
    for i, c in enumerate(s):
        if c == "\n":
            out.append(s[start : i + 1])
            start = i + 1
    if start < len(s):
        out.append(s[start:])
    return out


def _split_lf(s: str) -> list[str]:
    """LF-only counterpart of `splitlines()` (no line endings retained).

    Trailing `\\r` is stripped per line so CRLF files line up with LF
    `old_string` content.
    """
    if not s:
        return []
    parts = s.split("\n")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return [p.rstrip("\r") for p in parts]


def _leading_ws(line: str) -> str:
    """Return the leading whitespace prefix of `line` (spaces + tabs)."""
    i = 0
    while i < len(line) and line[i] in " \t":
        i += 1
    return line[:i]


def _line_fuzzy_spans(file_text: str, old_string: str) -> list[tuple[int, int]]:
    """Find every (start, end) char span in file_text whose lines match
    old_string's lines after `_normalize_line`.

    Empty `old_string` returns []. Single-line old_string is matched
    against single lines of the file (so a partial-line snippet won't
    fuzzy-match — fuzzy mode is line-granular).

    Returns char offsets into the original file_text so the caller can
    splice without re-flowing the file.
    """
    if not old_string:
        return []

    file_lines = _split_lf_keepends(file_text)
    if not file_lines:
        return []

    old_lines = [_normalize_line(l) for l in _split_lf(old_string)]
    n = len(old_lines)
    if n == 0 or n > len(file_lines):
        return []

    # Pre-normalize file lines once.
    file_norm = [_normalize_line(l) for l in file_lines]

    spans: list[tuple[int, int]] = []
    # Cumulative char offsets — `offsets[i]` = char offset where file_lines[i] starts
    offsets = [0]
    for line in file_lines:
        offsets.append(offsets[-1] + len(line))

    for i in range(len(file_lines) - n + 1):
        if file_norm[i : i + n] == old_lines:
            spans.append((offsets[i], offsets[i + n]))

    return spans


SIMILARITY_THRESHOLD = 0.85  # per-line ratio for stage-3 fuzzy match


def _line_similar_spans(
    file_text: str,
    old_string: str,
    min_ratio: float = SIMILARITY_THRESHOLD,
) -> list[tuple[int, int]]:
    """Find char spans in file_text where each line of old_string matches
    a corresponding file line with similarity ratio >= min_ratio.

    Stage-3 fallback: catches character-level corruption (e.g. a model
    that mistokenizes em-dash → vertical-tab while copying from Read).
    Stricter than stage-2 — requires both leading-whitespace AND trailing-
    whitespace equality, with the line content fuzzy via SequenceMatcher.

    The 0.85 default rejects anything more than a few-character drift
    while accepting common transcoding errors (em-dash, smart quotes,
    soft hyphens, accented vowels). Same offset-based output shape as
    `_line_fuzzy_spans` so the caller can splice without re-flowing.
    """
    if not old_string:
        return []

    file_lines = _split_lf_keepends(file_text)
    if not file_lines:
        return []

    old_lines = _split_lf(old_string)
    n = len(old_lines)
    if n == 0 or n > len(file_lines):
        return []

    # Pre-strip file lines (drop \r\n) for content comparison.
    file_content = [l.rstrip("\r\n") for l in file_lines]

    # Cumulative offsets so we can map [i, i+n) → (char_start, char_end)
    offsets = [0]
    for line in file_lines:
        offsets.append(offsets[-1] + len(line))

    def _line_pair_ok(file_line: str, old_line: str) -> bool:
        # Empty lines must both be empty (avoid ratio=1.0 noise on '' vs '')
        if not file_line and not old_line:
            return True
        if not file_line or not old_line:
            return False
        # Leading whitespace MUST match exactly. Python indentation is
        # semantic; SequenceMatcher would happily call `  return 1` and
        # `    return 1` similar enough at the byte level, but accepting
        # that match would silently break the file's indent level on
        # splice. The fuzzy stages only forgive whitespace AT the line
        # END or the line ENDING — never the indent level.
        if _leading_ws(file_line) != _leading_ws(old_line):
            return False
        # Cheap reject: length diff > 25% of the shorter side → almost
        # certainly below 0.85 ratio. Avoids running SequenceMatcher on
        # obviously dissimilar pairs.
        a, b = len(file_line), len(old_line)
        if min(a, b) > 0 and abs(a - b) / min(a, b) > 0.5:
            return False
        return difflib.SequenceMatcher(None, file_line, old_line).ratio() >= min_ratio

    spans: list[tuple[int, int]] = []
    for i in range(len(file_lines) - n + 1):
        if all(_line_pair_ok(file_content[i + j], old_lines[j]) for j in range(n)):
            spans.append((offsets[i], offsets[i + n]))
    return spans


def _splice_preserve_eol(text: str, start: int, end: int, new: str) -> str:
    """Replace text[start:end] with `new`, preserving the matched span's
    trailing line ending if `new` lacks one.

    The fuzzy match's span includes the line terminator of the last matched
    line (because `splitlines(keepends=True)` is used). If we splice
    verbatim and the model's `new_string` doesn't end with a newline, we
    silently delete the line break that joined the matched block to the
    following line. Worse: if the original used CRLF and the model used LF,
    we'd flip just that one line ending. Both are surprises.

    Rule: if the matched span ends with `\\r\\n` (or `\\n`) and `new` does
    not, append the same terminator to `new` before splicing.
    """
    span = text[start:end]
    if span.endswith("\r\n") and not new.endswith(("\r\n", "\n")):
        new = new + "\r\n"
    elif span.endswith("\n") and not new.endswith("\n"):
        new = new + "\n"
    return text[:start] + new + text[end:]


def _closest_lines_hint(file_text: str, old_string: str, max_lines: int = 5) -> str:
    """Return up to `max_lines` lines from file_text most similar to the
    first non-empty line of old_string, with line numbers — to help the
    model retry without an extra Read.
    """
    first_old = ""
    for line in old_string.splitlines():
        if line.strip():
            first_old = line.strip()
            break
    if not first_old:
        return ""

    file_lines = file_text.splitlines()
    scored: list[tuple[float, int, str]] = []
    for idx, line in enumerate(file_lines, start=1):
        if not line.strip():
            continue
        ratio = difflib.SequenceMatcher(None, first_old, line.strip()).ratio()
        if ratio >= 0.6:
            scored.append((ratio, idx, line))

    if not scored:
        return ""

    scored.sort(key=lambda t: (-t[0], t[1]))
    top = scored[:max_lines]
    rendered = "\n".join(f"  {idx:>5}: {line}" for _, idx, line in top)
    return f"\nClosest lines in the file:\n{rendered}"


class EditTool(Tool):
    name = "Edit"
    description = (
        "Replace `old_string` with `new_string` in a file. By default `old_string` "
        "must occur exactly once — pass a longer unique snippet with surrounding "
        "context if needed. Set `replace_all` to replace every occurrence "
        "(useful for renames). File must exist. Use Write for full rewrites. "
        "If exact match fails, a line-aware fallback ignores trailing whitespace "
        "and line-ending differences (leading whitespace is kept exact)."
    )
    permission = "ask"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "old_string": {"type": "string", "description": "Exact text to replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false)",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }

    def permission_summary(self, input: dict) -> str:
        path = input.get("path", "")
        old = input.get("old_string", "") or ""
        new = input.get("new_string", "") or ""
        replace_all = bool(input.get("replace_all"))
        snippet_old = (old[:40] + "…") if len(old) > 40 else old
        snippet_new = (new[:40] + "…") if len(new) > 40 else new
        mode = " (replace_all)" if replace_all else ""
        return f"Edit: {path}{mode}\n    - {snippet_old!r}\n    + {snippet_new!r}"

    async def run(self, app, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        old = kwargs.get("old_string")
        new = kwargs.get("new_string")
        replace_all = bool(kwargs.get("replace_all"))

        if not path:
            return ToolResult(ok=False, content="error: path is required")
        if old is None or not isinstance(old, str):
            return ToolResult(ok=False, content="error: old_string is required (string)")
        if new is None or not isinstance(new, str):
            return ToolResult(ok=False, content="error: new_string is required (string)")
        if old == new:
            return ToolResult(ok=False, content="error: old_string and new_string are identical")
        if old == "":
            return ToolResult(
                ok=False,
                content="error: old_string cannot be empty (use Write to create a file)",
            )

        p = resolve_path(app, path)
        if not p.exists():
            return ToolResult(ok=False, content=f"error: file not found: {path}")
        if not p.is_file():
            return ToolResult(ok=False, content=f"error: not a file: {path}")

        try:
            raw = p.read_bytes()
        except Exception as e:
            return ToolResult(ok=False, content=f"error: {e}")

        if len(raw) > MAX_BYTES:
            return ToolResult(
                ok=False,
                content=f"error: file too large ({len(raw)} bytes, max {MAX_BYTES})",
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"error: not a UTF-8 text file: {path}")

        # ── Stage 1: exact match ──────────────────────────────────────
        count = text.count(old)
        if count > 1 and not replace_all:
            return ToolResult(
                ok=False,
                content=(
                    f"error: old_string occurs {count} times in {path}. "
                    "Extend it with more surrounding context to make it unique, "
                    "or set replace_all=true."
                ),
            )

        match_mode = "exact"
        if count >= 1:
            if replace_all:
                updated = text.replace(old, new)
                replacements = count
            else:
                updated = text.replace(old, new, 1)
                replacements = 1
        else:
            # ── Stage 2: line-aware fuzzy fallback ────────────────────
            spans = _line_fuzzy_spans(text, old)
            if len(spans) > 1 and not replace_all:
                return ToolResult(
                    ok=False,
                    content=(
                        f"error: old_string not found exactly, but matches {len(spans)} "
                        f"line-blocks in {path} after ignoring trailing whitespace. "
                        "Extend it with more surrounding context to make it unique, "
                        "or set replace_all=true."
                    ),
                )
            if not spans:
                # ── Stage 3: per-line similarity fallback ────────────
                # Catches char-level corruption (em-dash → control char,
                # smart quotes, etc.) where stage-2's exact-after-rstrip
                # check would still miss.
                sim_spans = _line_similar_spans(text, old)
                if len(sim_spans) > 1 and not replace_all:
                    return ToolResult(
                        ok=False,
                        content=(
                            f"error: old_string not found exactly, but {len(sim_spans)} "
                            f"line-blocks in {path} are >85% similar. "
                            "Extend it with more surrounding context to disambiguate, "
                            "or set replace_all=true."
                        ),
                    )
                if not sim_spans:
                    hint = _closest_lines_hint(text, old)
                    return ToolResult(
                        ok=False,
                        content=(
                            f"error: old_string not found in {path}. "
                            "Read the file first and copy the exact text (including whitespace). "
                            "Note: leading indentation must match exactly; trailing whitespace "
                            "and line endings are ignored." + hint
                        ),
                    )
                spans = sim_spans
                match_mode = "line-similar"
            else:
                match_mode = "line-fuzzy"

            if replace_all:
                # Splice in reverse so earlier offsets stay valid.
                updated = text
                for start, end in reversed(spans):
                    updated = _splice_preserve_eol(updated, start, end, new)
                replacements = len(spans)
            else:
                start, end = spans[0]
                updated = _splice_preserve_eol(text, start, end, new)
                replacements = 1

        try:
            p.write_bytes(updated.encode("utf-8"))
        except Exception as e:
            return ToolResult(ok=False, content=f"error: {e}")

        delta = len(updated) - len(text)
        mode_suffix = "" if match_mode == "exact" else f" (match: {match_mode})"
        summary = (
            f"Edited {path}: {replacements} replacement(s), "
            f"{'+' if delta >= 0 else ''}{delta} bytes{mode_suffix}"
        )
        diff = unified_diff(text, updated, path)
        return ToolResult(
            ok=True,
            content=summary,
            display={
                "path": str(p),  # absolute — /revert finds the file by this
                "action": "edit",
                "replacements": replacements,
                "bytes_delta": delta,
                "replace_all": replace_all,
                "match_mode": match_mode,
                "previous_content": text,  # raw pre-edit bytes, for /revert
                "diff": diff,
            },
        )
