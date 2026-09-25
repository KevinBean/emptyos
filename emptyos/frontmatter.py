"""The frontmatter block — where it ends, how it parses, how it is rewritten.

Top-level **on purpose**, for the same reason as :mod:`emptyos.nethost`: both
the SDK (`emptyos.sdk.utils`, and 184 call sites through it) and the runtime
(`emptyos.runtime.vault_index`, the read path behind `vault_query`) need this,
and neither may import the other's package at module level. Importing anything
under ``emptyos/sdk/`` runs ``emptyos/sdk/__init__.py``, which pulls in
``base_app``; a runtime module reaching into the SDK for a string helper is a
cycle waiting for someone to hoist one function-local import.

**This module exists because there used to be two of it.** ``parse_frontmatter``
and ``vault_index._parse_fm`` were near-verbatim copies, and they drifted — in
three separate ways, each found only after the previous one was fixed:

1. An empty ``key:`` closed as ``[]`` mid-block and ``""`` at the end, so the
   *position* of a key decided its type. Downstream, ``str([])`` is the truthy
   string ``"[]"``, which is how a bid scenario reached the state an engineer
   signs carrying no engineer.
2. One copy learned that ``key: ""`` is a string and never told the other, so
   the same note read as a list or a string depending on which door it came in.
3. One copy learned to find the block's end line-anchored and never told the
   other, so ``title: A---B`` ended the block mid-value in the SDK copy —
   dropping every key after it on read, and on *write* splicing inside the
   value to produce a malformed block carrying a duplicate key.

Each fix was correct and each left the copies further apart. The lesson is not
"be more careful": it is that two implementations of one contract will drift,
so there is now one. The three block-boundary helpers below all call
:func:`fm_end`, so the parser, the body splitter and the field writer cannot
disagree about where the block ends.

Deliberately not a YAML parser. It handles flat scalars, inline arrays and
block lists — the shape EmptyOS notes actually use (see
`.claude/rules/dev-gotchas.md` on flat-only frontmatter). Nested structures
need a real YAML writer; ``set_frontmatter_field`` says so.

Stdlib only. Keep it that way — the whole point is that anything may import it.
"""

from __future__ import annotations

import re

__all__ = [
    "fm_end",
    "parse_frontmatter",
    "strip_frontmatter",
    "set_frontmatter_field",
]


def fm_end(content: str) -> int:
    """Offset of the closing ``---`` LINE of a frontmatter block, or -1.

    Line-anchored on purpose. A bare ``content.find("---", 3)`` matches ``---``
    anywhere — including inside a quoted value (``title: A---B``) — and silently
    truncates the block, dropping every key after it. Every frontmatter split
    goes through here so the parser, the body splitter and the field writer can
    never disagree about where the block ends.
    """
    nl = content.find("\n", 3)
    if nl < 0:
        return -1
    pos = nl + 1
    while pos < len(content):
        nxt = content.find("\n", pos)
        line = content[pos:] if nxt < 0 else content[pos:nxt]
        if line.strip() == "---":
            return pos
        if nxt < 0:
            return -1
        pos = nxt + 1
    return -1


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from markdown content.

    Extracts key-value pairs from the ``---`` delimited block at the top of a
    markdown file. Values are stripped of surrounding quotes. Handles simple
    YAML lists (``- item`` lines following a key with no inline value).

    **One syntax, one type** — the rule the two copies kept breaking:

    ==========================  ===============  ==================================
    written                     reads as         because
    ==========================  ===============  ==================================
    ``key: value``              ``"value"``      a scalar
    ``key:``                    ``""``           no value at all is an empty scalar
    ``key: ""``                 ``""``           an author writing empty on purpose
    ``key: [a, b]``             ``["a", "b"]``   inline array
    ``key: []``                 ``[]``           inline array, empty
    ``key:`` + ``  - a`` lines  ``["a"]``        block list
    ==========================  ===============  ==================================

    The two empty forms are the interesting rows. ``key:`` and ``key: ""`` are
    empty *scalars* and read ``""`` wherever they sit in the block — position
    used to decide it, which meant the same line parsed two ways. ``key: []``
    is an empty *list*, because the same syntax with contents yields a list and
    a parser that returned a string for the empty case would be reproducing, in
    one syntax, exactly the type-instability the rest of this module exists to
    remove. Both are falsy, which is what ``if not value`` guards rely on.
    """
    if not content.startswith("---"):
        return {}
    end = fm_end(content)
    if end < 0:
        return {}
    fm: dict = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for line in content[3:end].strip().split("\n"):
        stripped = line.strip()
        # YAML list item (indented "- value")
        if stripped.startswith("- ") and current_key is not None and current_list is not None:
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue
        # Flush any pending list. An empty one means the key had no inline
        # value and no `- ` children — an empty scalar, not an empty list.
        if current_key is not None and current_list is not None:
            fm[current_key] = current_list if current_list else ""
            current_key = None
            current_list = None
        if ":" in line and not stripped.startswith("-"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # Track whether the raw value was wrapped in YAML string quotes
            # ("..." or '...'). A quote-wrapped value is ALWAYS a string —
            # never an inline YAML array — even if its inner content starts
            # with [. This prevents JSON-encoded strings like
            # `svg_callouts: "[{...}, {...}]"` from being mis-split on
            # commas into a list of garbage fragments.
            was_quoted = False
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                # YAML double-quoted strings support \" escapes; YAML single-
                # quoted strings don't, but JSON-encoded values always land
                # in double quotes from json.dumps. Unescape \" → " inside
                # double-quoted values so consumers see the real string.
                quote_char = val[0]
                val = val[1:-1]
                if quote_char == '"':
                    val = val.replace('\\"', '"').replace("\\\\", "\\")
                was_quoted = True
            if val:
                # Inline YAML array: [a, b, c] — only when the raw value
                # was NOT quote-wrapped. A quoted value like "[...]" is a
                # string whose content happens to start with [.
                if not was_quoted and val.startswith("[") and val.endswith("]"):
                    fm[key] = [_unquote(v) for v in val[1:-1].split(",") if v.strip()]
                else:
                    fm[key] = val
            elif was_quoted:
                # `key: ""` — an author writing an empty string on purpose.
                # Only a bare `key:` can open a block list, so settle it here
                # rather than leaving it pending; otherwise a following `- x`
                # line would be swallowed into a list the author never opened.
                fm[key] = ""
            else:
                # Could be the start of a block list — decided by what follows.
                current_key = key
                current_list = []
    # Flush final list
    if current_key is not None and current_list is not None:
        fm[current_key] = current_list if current_list else ""
    return fm


def strip_frontmatter(content: str) -> str:
    """Return markdown content with the YAML frontmatter block removed."""
    if content.startswith("---"):
        end = fm_end(content)
        if end > 0:
            return content[end + 3 :]
    return content


def set_frontmatter_field(content: str, key: str, raw_value: str) -> str:
    """Insert or replace ``key: <raw_value>`` in the frontmatter block.

    Pure string transform. *raw_value* is written verbatim after ``key: ``;
    the caller owns YAML encoding (quoting strings, ``[a, b]`` for lists,
    escaping newlines). If no ``---`` block exists, one is created at the top.

    Use for: simple single-line scalar/array fields. Not for: nested YAML,
    block-style list values (``key:\\n  - a``) — use a real YAML writer there.

    This is the site where getting the block boundary wrong *corrupts* rather
    than misreads: a splice point inside a value rewrites the note into a
    malformed block carrying a duplicate of the key it just set.
    """
    line = f"{key}: {raw_value}"
    if content.startswith("---"):
        end = fm_end(content)
        if end > 0:
            fm_block = content[3:end]
            pattern = re.compile(rf"(?m)^{re.escape(key)}\s*:.*$")
            if pattern.search(fm_block):
                fm_block = pattern.sub(lambda _m: line, fm_block, count=1)
            else:
                fm_block = fm_block.rstrip() + "\n" + line + "\n"
            return "---" + fm_block + content[end:]
    return f"---\n{line}\n---\n{content}"
