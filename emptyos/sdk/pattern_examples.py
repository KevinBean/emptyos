"""Few-shot pattern injection from KB `kind: pattern` notes.

Resolves named pattern notes (via the `kb` app, with a vault-glob fallback),
pulls their fenced code blocks filtered by language, and assembles a
"Reference …" block to prepend/append to a generation system prompt. The
curated shape-prior: hand-authored anatomy that nudges a model toward valid,
plausible output before generation — the complement to the shape-validation
gate that catches what slips through.

Shared by any app that wants curated few-shot scaffolding:
- viz — 3D-scene / SVG / chart / mermaid web code (lang per shape)
- cad — eos-cad/1 feature-tree JSON (`langs={"json"}`)
- robot-modeller — articulated SDK Python (`langs={"python"}`)

Takes a `BaseApp` for `call_app` (kb lookup) + `vault_root` (glob fallback).
No kernel import — pure orchestration over the app's own capabilities.
"""

from __future__ import annotations

import re

# A pattern note's fenced code blocks. Tolerant of an empty language tag.
_FENCE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_-]*)\s*\n(.*?)```", re.DOTALL)

_DEFAULT_INTRO = (
    "Treat the following as canonical scaffolding — match the construction "
    "style and conventions unless the brief explicitly diverges. Do NOT copy "
    "verbatim; adapt to the brief's dimensions and intent."
)


async def _resolve_note(app, name: str) -> tuple[str, str] | None:
    """Resolve a pattern name to (title, body). KB app first, vault glob second."""
    slug = (name or "").strip()
    if not slug:
        return None
    try:
        note = await app.call_app("kb", "get_note", slug=slug)
    except Exception:
        note = None
    if isinstance(note, dict) and not note.get("error") and note.get("body"):
        props = note.get("properties") or {}
        return (str(props.get("title") or slug), str(note["body"]))
    # Fallback for environments where the KB app isn't installed / doesn't index
    # this tree. Mirrors the legacy viz glob path.
    try:
        for cand in app.vault_root.glob(f"30_Resources/KB/**/patterns/{slug}.md"):
            text = cand.read_text(encoding="utf-8")
            body = text.split("---", 2)[-1].lstrip("\n") if text.startswith("---") else text
            return (slug, body)
    except Exception:
        pass
    return None


def _extract_blocks(body: str, langs) -> list[str]:
    """Fenced blocks whose language is in `langs` (or all, if `langs` is falsy)."""
    out: list[str] = []
    accepted = set(langs) if langs else None
    for m in _FENCE_BLOCK_RE.finditer(body):
        lang = (m.group(1) or "").strip().lower()
        if accepted is not None and lang and lang not in accepted:
            continue
        code = m.group(2).strip()
        if code:
            out.append(f"```{lang or 'text'}\n{code}\n```")
    return out


async def resolve_pattern_examples_detail(
    app,
    names,
    *,
    langs=None,
    intro: str | None = None,
    heading: str = "Reference patterns",
) -> tuple[str, list[str]]:
    """`resolve_pattern_examples`, plus the slugs that actually landed.

    Returns ``(block, resolved_slugs)``. A requested name is in
    ``resolved_slugs`` only when it resolved to a note AND that note yielded
    at least one fence in an accepted language — i.e. only when its code is
    genuinely in the system prompt.

    That distinction is the whole point of this variant: a caller recording
    provenance ("this artifact was built from pattern X") must record what the
    model was actually shown, not what the request asked for. A name that
    404s, or whose fences are all in the wrong language for this shape, is
    silently skipped during assembly — writing it down anyway would reify a
    request as a fact.
    """
    names = [n for n in (names or []) if n]
    if not names:
        return "", []
    chunks: list[str] = []
    resolved: list[str] = []
    for name in names:
        res = await _resolve_note(app, name)
        if not res:
            continue
        title, body = res
        blocks = _extract_blocks(body, langs)
        if not blocks:
            continue
        chunks.append(f"## Example: {title}\n\n" + "\n\n".join(blocks))
        resolved.append(name)
    if not chunks:
        return "", []
    block = f"\n\n# {heading}\n\n{intro or _DEFAULT_INTRO}\n\n" + "\n\n".join(chunks)
    return block, resolved


async def resolve_pattern_examples(
    app,
    names,
    *,
    langs=None,
    intro: str | None = None,
    heading: str = "Reference patterns",
) -> str:
    """Build a markdown few-shot block for the named pattern notes, or "".

    Args:
        app: a BaseApp (provides `call_app` + `vault_root`).
        names: iterable of pattern-note slugs.
        langs: accepted fence languages (e.g. {"json"}, {"python"}). Falsy = all.
        intro: preamble line under the heading; `_DEFAULT_INTRO` if None.
        heading: the section title (rendered as `# <heading>`).

    Returns "" when nothing resolves — callers append unconditionally.
    Use `resolve_pattern_examples_detail` when you also need to record which
    patterns landed.
    """
    block, _resolved = await resolve_pattern_examples_detail(
        app, names, langs=langs, intro=intro, heading=heading
    )
    return block
