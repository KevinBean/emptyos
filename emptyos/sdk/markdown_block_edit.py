"""markdown_block_edit — scoped single-block edit of a markdown note.

The markdown-sourced sibling of ``emptyos.sdk.html_element_edit``. Given a note
body and the ``eN`` anchor of one editable block (from ``markdown_blocks``),
produce a *replacement for that one markdown block* (an LLM rewrite), validate
it, splice it back into the body, and return the full new body. The caller owns
persistence, staleness, events, and the propose/preview/confirm gate
(``emptyos.sdk.sandbox.SandboxedWrite`` on the ``.md`` file).

**Instruction-only — there is no ``knob`` path.** The HTML sibling has a
deterministic inline-style knob; inline style has no markdown analogue, so this
core takes a natural-language instruction only. Do not port the knob.

Pure functions only — no ``self``, no kernel access, no I/O. The LLM call is
injected as ``think_fn(system, user) -> str`` so this module stays kernel-free
and unit-testable without a daemon (tests/test_unit_markdown_block_edit.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from emptyos.sdk.markdown_blocks import EDITABLE_BLOCK_KINDS, split_markdown_blocks

ANCHOR_RE = re.compile(r"^e\d+$")
_FRONTMATTER_LINE = re.compile(r"^---\s*$", re.MULTILINE)
# A whole-string fence with ANY info string (```md / ```markdown / ``` …); the
# closing marker must match the opener. html_artifact.strip_fences is html-only
# (it leaves a ```md language tag as content), so markdown needs its own.
_WRAP_FENCE = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*\n(.*?)\n?\1\s*$", re.DOTALL)


def strip_md_fences(text: str) -> str:
    """Strip one whole-string ```lang … ``` fence (any language tag), else return
    the trimmed text. Editable blocks are never code fences, so this can't eat a
    legitimate replacement."""
    t = (text or "").strip()
    m = _WRAP_FENCE.match(t)
    return m.group(2).strip() if m else t


MARKDOWN_BLOCK_EDIT_SYSTEM = """\
You are editing ONE markdown block inside a longer note. You are given that
block's current markdown and a change request. Return ONLY the replacement
markdown for that single block.

RULES
- Return the block and nothing else: no other paragraphs, no surrounding note,
  no prose about what you changed, no code fences wrapping your answer.
- Keep the SAME block TYPE: a paragraph stays a paragraph; a `##` heading keeps
  its `##`; a list stays a list; a blockquote stays `>`-prefixed.
- Return markdown, NOT HTML.
- Never emit a line that is just `---`, and never write or touch frontmatter.
- Preserve [[wikilinks]], ![[embeds]], markdown links, and footnote refs exactly
  unless the change is specifically about them.
- No em-dashes (the long dash) anywhere: use a period, comma, colon, or spaced
  hyphen.
"""


@dataclass
class BlockEdit:
    """Result of a scoped markdown-block edit proposal.

    ``ok`` True → ``new_body`` is the full note body with the one block replaced;
    the caller reassembles ``frontmatter + new_body`` and stages it (SandboxedWrite).
    ``ok`` False → ``error`` is a human reason; ``fallback == "iterate"`` means
    the block couldn't be resolved / isn't editable and the caller should offer
    its whole-file rewrite path instead of splicing a wrong span.
    """

    ok: bool
    new_body: str = ""
    block_id: str = ""
    error: str = ""
    fallback: str = ""


def validate_block_replacement(repl: str, orig_kind: str) -> str:
    """Return "" if ``repl`` is a safe single-block replacement of ``orig_kind``,
    else a human reason."""
    if not repl.strip():
        return "empty replacement"
    if _FRONTMATTER_LINE.search(repl):
        return "replacement contains a frontmatter delimiter"
    blocks = split_markdown_blocks(repl.strip())
    if len(blocks) != 1:
        return f"replacement is {len(blocks)} blocks, expected one"
    if blocks[0].kind != orig_kind:
        return f"replacement changed the block type ({orig_kind} → {blocks[0].kind})"
    return ""


def _build_user_msg(block_md: str, kind: str, instruction: str, voice_hint: str) -> str:
    voice_line = f"\n{voice_hint}" if voice_hint else ""
    return (
        f"CHANGE REQUEST:\n{instruction.strip()}\n\n"
        f"CURRENT BLOCK (a markdown {kind}; return its replacement, same type):\n"
        f"{block_md}{voice_line}"
    )


async def propose_markdown_block_edit(
    note_body: str,
    block_id: str,
    *,
    instruction: str = "",
    think_fn: Callable[[str, str], Awaitable[str]],
    voice_hint: str = "",
) -> BlockEdit:
    """Resolve ``block_id`` in ``note_body``, rewrite that one markdown block via
    ``think_fn``, validate, splice, and return the full new body.

    Never raises — every failure is a ``BlockEdit(ok=False, ...)``. A block that
    can't be resolved or isn't editable returns ``fallback="iterate"`` so the
    caller can offer the whole-file rewrite path.
    """
    block_id = (block_id or "").strip()
    if not block_id or not ANCHOR_RE.match(block_id):
        return BlockEdit(ok=False, error="bad block anchor")
    if not instruction.strip():
        return BlockEdit(ok=False, block_id=block_id, error="instruction is required")

    n = int(block_id[1:])
    target = next((b for b in split_markdown_blocks(note_body) if b.idx == n), None)
    if target is None or target.kind not in EDITABLE_BLOCK_KINDS:
        return BlockEdit(ok=False, block_id=block_id, fallback="iterate",
                         error="could not resolve that block")

    block_md = note_body[target.start:target.end]
    user = _build_user_msg(block_md, target.kind, instruction, voice_hint)
    raw = await think_fn(MARKDOWN_BLOCK_EDIT_SYSTEM, user)
    repl = strip_md_fences(raw if isinstance(raw, str) else str(raw))

    reason = validate_block_replacement(repl, target.kind)
    if reason:
        return BlockEdit(ok=False, block_id=block_id, error=reason)

    new_body = note_body[:target.start] + repl + note_body[target.end:]
    return BlockEdit(ok=True, new_body=new_body, block_id=block_id)
