"""DeleteFunction tool — remove a function or class definition by name.

Permission: `ask`. Mutating; deletes a contiguous span of source lines.

Why this exists: the bench surfaced that `delete-with-callers` thrashes
even strong models (gpt-4.1-mini hits 13–15 tool calls per run, often
ending in `NameError`). The model has to construct a multi-line `old_string`
matching the exact def block and pass it to Edit. Even with stage-2/3
fuzzy fallbacks the failure mode is real: every multi-line `old_string`
is a fresh chance to typo a special character. AST-based deletion is the
right primitive — name in, span out, no `old_string` to corrupt.

Scope:
- Top-level `def`, `async def`, and `class` definitions in `.py` files.
- Decorators preceding the def are removed too (the decorators are part
  of the def's footprint).
- Nested defs (methods inside a class) are NOT supported in v1 — they'd
  need a dotted path like `MyClass.method`. Add later if needed.
- The tool only handles Python because it leans on `ast`. Other languages
  go through Edit + fuzzy fallback as before.
"""

from __future__ import annotations

import ast
from pathlib import Path

from emptyos.sdk.agent_tools.base import Tool, ToolResult, resolve_path, unified_diff


MAX_BYTES = 5_000_000


def _find_def_span(source: str, name: str) -> tuple[int, int, str] | None:
    """Find the line span of a top-level def/async-def/class with `name`.

    Returns (start_line, end_line, kind) — both 1-indexed, end_line is the
    last line OF the def (inclusive). `kind` is "def"/"async def"/"class".
    Returns None if not found.

    Decorators are included in the start_line: a `@decorator` line above
    `def foo` is considered part of `foo`'s span.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    target_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in tree.body:
        if not isinstance(node, target_types):
            continue
        if node.name != name:
            continue
        kind = (
            "async def" if isinstance(node, ast.AsyncFunctionDef)
            else "class" if isinstance(node, ast.ClassDef)
            else "def"
        )
        # Include decorators in the span. ast assigns each decorator its
        # own lineno; the lowest among them is where the def really starts.
        start = node.lineno
        for dec in node.decorator_list:
            if hasattr(dec, "lineno") and dec.lineno < start:
                start = dec.lineno
        end = node.end_lineno or node.lineno
        return (start, end, kind)
    return None


def _find_all_def_lines(source: str, name: str) -> list[int]:
    """Return ALL top-level def/class linenos with `name` (for ambiguity reporting)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                out.append(node.lineno)
    return out


class DeleteFunctionTool(Tool):
    name = "DeleteFunction"
    description = (
        "Remove a top-level function or class definition (and its decorators) "
        "from a Python file by name. Use this instead of Edit when you need to "
        "delete a whole def/class — no `old_string` reconstruction, no "
        "whitespace gotchas. Does NOT touch call sites in OTHER files; for "
        "those, use Edit or Grep+Edit. Python files only."
    )
    permission = "ask"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to a .py file"},
            "name": {"type": "string", "description": "Top-level function or class name to remove"},
        },
        "required": ["path", "name"],
    }

    def permission_summary(self, input: dict) -> str:
        path = input.get("path", "")
        name = input.get("name", "")
        return f"DeleteFunction: {path}\n    delete `{name}` (top-level def/class)"

    async def run(self, app, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        name = kwargs.get("name", "")
        if not path:
            return ToolResult(ok=False, content="error: path is required")
        if not name:
            return ToolResult(ok=False, content="error: name is required")
        if not isinstance(name, str) or not name.isidentifier():
            return ToolResult(
                ok=False,
                content=f"error: name must be a valid Python identifier (got {name!r})",
            )

        p = resolve_path(app, path)
        if not p.exists():
            return ToolResult(ok=False, content=f"error: file not found: {path}")
        if not p.is_file():
            return ToolResult(ok=False, content=f"error: not a file: {path}")
        if p.suffix != ".py":
            return ToolResult(
                ok=False,
                content=f"error: DeleteFunction only handles .py files (got {p.suffix})",
            )

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

        # Verify the file parses before we touch it — we don't want to
        # silently no-op on a syntax-broken file and pretend success.
        try:
            ast.parse(text)
        except SyntaxError as e:
            return ToolResult(
                ok=False,
                content=f"error: file does not parse, refusing to edit: {e}",
            )

        all_matches = _find_all_def_lines(text, name)
        if not all_matches:
            return ToolResult(
                ok=False,
                content=(
                    f"error: no top-level function or class named {name!r} found in {path}. "
                    "Note: nested defs (e.g. methods inside a class) are not supported — "
                    "use Edit for those."
                ),
            )
        if len(all_matches) > 1:
            return ToolResult(
                ok=False,
                content=(
                    f"error: {len(all_matches)} top-level definitions named {name!r} "
                    f"in {path} (lines {all_matches}). Refusing to delete ambiguously — "
                    "use Edit to target one specifically."
                ),
            )

        span = _find_def_span(text, name)
        if span is None:
            # Should be unreachable given the all_matches check, but defensive
            return ToolResult(ok=False, content=f"error: could not locate {name!r} span")
        start_line, end_line, kind = span

        # Splice out lines [start_line .. end_line] inclusive.
        # Convert 1-indexed lines to 0-indexed list slice.
        lines = text.splitlines(keepends=True)
        before = lines[:start_line - 1]
        after = lines[end_line:]
        # Trim a trailing blank line just before the deleted span IF the
        # span is followed by another def — keeps PEP-8 spacing tidy and
        # avoids leaving a double-blank-line where one used to be a
        # before-def separator.
        # Conservative: only trim ONE trailing blank line, and only if the
        # next surviving line is also def-ish (def/class/async/@decorator).
        if before and after:
            last_before = before[-1].strip()
            first_after = after[0].lstrip() if after[0].strip() else ""
            looks_like_def = first_after.startswith(("def ", "async def ", "class ", "@"))
            if last_before == "" and looks_like_def and len(before) >= 2 and before[-2].strip() == "":
                # Two consecutive blanks before the span and a def follows —
                # drop one to restore PEP-8 single blank between defs.
                before = before[:-1]

        updated = "".join(before + after)

        try:
            p.write_bytes(updated.encode("utf-8"))
        except Exception as e:
            return ToolResult(ok=False, content=f"error: {e}")

        deleted_lines = end_line - start_line + 1
        delta = len(updated) - len(text)
        diff = unified_diff(text, updated, path)
        # In-context reminder: bench showed strong models use DeleteFunction
        # then declare done without cleaning callers. The tool *description*
        # already says "does NOT touch call sites" but the model only reads
        # that at registration; embedding the reminder in the success message
        # surfaces it exactly when the model is deciding what to do next.
        summary = (
            f"Deleted {kind} {name} from {path}: "
            f"{deleted_lines} lines removed (lines {start_line}-{end_line}), "
            f"{'+' if delta >= 0 else ''}{delta} bytes.\n"
            f"REMINDER: callers of `{name}` in OTHER files are now broken. "
            f"You must Grep for `{name}` across the project and Edit each "
            f"call site (e.g. replace with `pass` or remove the import) "
            f"BEFORE declaring this task complete."
        )
        return ToolResult(
            ok=True,
            content=summary,
            display={
                "path": path,
                "name": name,
                "kind": kind,
                "start_line": start_line,
                "end_line": end_line,
                "lines_removed": deleted_lines,
                "bytes_delta": delta,
                "diff": diff,
            },
        )
